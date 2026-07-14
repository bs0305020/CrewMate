"""Retry / fallback / rollback unit tests for the agent_invoke compose flow (담당자 B, task 6.4).

These are EXAMPLE / UNIT tests (plain pytest — no Hypothesis, no ``property`` marker). They
pin the retry, Bedrock-fallback, and failure-cleanup orchestration wired in task 6.3
(``compose_flow_with_retry`` in ``backend/functions/agent_invoke/handler.py``) against the
in-memory shared stubs installed under ``shared.*`` by the ``install_shared`` fixture
(conftest.py), with the Bedrock ``compose`` call replaced by a scripted fake so no live model
is invoked.

Why drive ``compose_flow_with_retry`` directly
-----------------------------------------------
The retry / fallback / rollback logic 6.4 verifies lives in the wrapper, not in the single
attempt (``compose_flow``) nor in the handler's routing / auth / state-guard (already covered
by task 5.5's ``test_agent_invoke_flow.py``). Driving the wrapper with an injected
``compose_fn`` and asserting on the ``FakeSharedDB`` recorders (``saved_crews``,
``status_transitions`` for the WorkRequest, ``gap_status_transitions`` for the GapEvent) is
the most direct way to exercise exactly the behaviour task 6.3 added. On terminal failure the
wrapper raises ``handler._FlowError`` with ``code == "AGENT_RETRY_FAILED"``; on success it
returns a ``shared/response`` success dict.

Concerns covered (see tasks.md task 6.4)
----------------------------------------
1. **Retry exactly once.** A validation failure is retried exactly once — at most two compose
   attempts total. (Req 9.1)
2. **Retry recovers.** Invalid-then-valid succeeds on the second attempt, saves, and performs
   the NORMAL ``COMPOSING -> PROPOSED`` transition. (Req 9.1)
3. **NORMAL rollback (legitimate transition, owned by 6.4).** On retry exhaustion NORMAL rolls
   the WorkRequest back ``COMPOSING -> REQUESTED`` exactly once and returns
   ``AGENT_RETRY_FAILED``. This rollback is NOT the thing Property 9 (task 5.4) forbids — 5.4
   asserts "no save + no PROPOSED transition", while 6.4 owns this rollback assertion (the
   5.4/6.4 responsibility split). (Req 9.2)
4. **EMERGENCY never rolls back the WorkRequest** (it may be RUNNING). Per-path GapEvent
   failure-transition ownership (design "compose_flow의 실행 규칙", tasks 6.3 / 8.5):
     - **INTERNAL** (gap_event's trusted invoke): agent_invoke does NOT transition the
       GapEvent — gap_event owns ``RECOMPOSING -> FAILED``.
     - **EXTERNAL** (``agent-recompose``, agent_invoke holds the lock): agent_invoke DOES
       transition ``RECOMPOSING -> FAILED`` and attaches manual-composition guidance.
   (Req 9.2, 10.9)
5. **Fallback ON + Bedrock forced failure keeps the demo path** — the deterministic
   ``demo_fallback`` output validates and is saved (PRD_B Day 4 "Bedrock 강제 실패 시에도 데모
   경로 유지"). Fallback OFF + Bedrock failure maps to ``AGENT_RETRY_FAILED`` with no save and
   no retry (a down Bedrock is not worth retrying). (Req 9.4, 9.2)

_Requirements: 9.1, 9.2, 9.4_

Python 3.9: ``from __future__ import annotations`` keeps annotations lazy.
"""
from __future__ import annotations

import json

import pytest

from agent.crew_agent import BedrockUnavailable
from agent.schemas import (
    AgentInput,
    AgentOutput,
    Candidate,
    FixedMember,
    Priority,
    Recommendation,
    RequestSpec,
    TradeRequirement,
)
from functions.agent_invoke import handler
from functions.agent_invoke.fallback import demo_fallback
from functions.agent_invoke.persistence import SaveContext

OFFICE_ID = "OFFICE001"


def _body(resp):
    """Decode the ``{success, data|error}`` envelope from an API-Gateway proxy response."""
    return json.loads(resp["body"])


# --------------------------------------------------------------------------- #
# Scripted compose fake (no live Bedrock)                                      #
# --------------------------------------------------------------------------- #
class _ScriptedCompose:
    """A ``compose_fn`` stand-in returning / raising a scripted per-attempt sequence.

    Each positional ``outcome`` is used for the matching attempt (1st outcome -> 1st call,
    etc.); the final outcome repeats if there are more calls than outcomes. An ``Exception``
    outcome is raised (used for ``BedrockUnavailable``); an :class:`AgentOutput` is returned.
    ``calls`` records how many times it was invoked so a test can assert the retry budget was
    honoured (initial + at most one retry). The call signature mirrors ``compose``:
    ``compose_fn(agent_input, *, timeout_s)``.
    """

    def __init__(self, *outcomes):
        self._outcomes = list(outcomes)
        self.calls = 0

    def __call__(self, agent_input, *, timeout_s=None):
        self.calls += 1
        outcome = self._outcomes[min(self.calls - 1, len(self._outcomes) - 1)]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


# --------------------------------------------------------------------------- #
# Input / output builders                                                      #
# --------------------------------------------------------------------------- #
def _normal_input(request_id="REQ1"):
    """A NORMAL ``AgentInput`` requiring FORMWORK:1 with a single READY candidate W1."""
    return AgentInput(
        mode="NORMAL",
        request=RequestSpec(
            request_id=request_id,
            required_workers=[TradeRequirement(trade="FORMWORK", count=1)],
            budget=1_000_000,
            priority=Priority(cost="HIGH", skill="MEDIUM", teamwork="LOW"),
            site="현장 A",
            work_date="2025-01-01",
            start_time="08:00",
        ),
        candidates=[
            Candidate(worker_id="W1", trade="FORMWORK", skill_level=3,
                      desired_daily_wage=150_000, career_years=5),
        ],
    )


def _emergency_input(request_id="REQ_E"):
    """An EMERGENCY ``AgentInput`` (fixed F1 + candidate N1, FORMWORK:2)."""
    return AgentInput(
        mode="EMERGENCY",
        request=RequestSpec(
            request_id=request_id,
            required_workers=[TradeRequirement(trade="FORMWORK", count=2)],
            budget=1_000_000,
            priority=Priority(cost="HIGH", skill="MEDIUM", teamwork="LOW"),
            site="현장 E",
            work_date="2025-01-02",
            start_time="07:00",
        ),
        fixed_members=[
            FixedMember(worker_id="F1", trade="FORMWORK", desired_daily_wage=150_000),
        ],
        candidates=[
            Candidate(worker_id="N1", trade="FORMWORK", skill_level=3,
                      desired_daily_wage=160_000, career_years=4),
        ],
    )


def _valid_normal_output(request_id="REQ1"):
    """A rule-compliant NORMAL output (member W1, total_cost matching the fresh W1 wage)."""
    return AgentOutput(
        mode="NORMAL",
        request_id=request_id,
        recommendations=[
            Recommendation(rank=1, member_ids=["W1"], total_cost=150_000,
                           reason="필요 직종 인원을 충족하는 팀 구성", considerations=["직종 인원 충족"]),
        ],
    )


def _invalid_output(request_id="REQ1", mode="NORMAL"):
    """An invalid output: an unknown member id (``GHOST``) fails the member_exists check.

    Works for either mode — ``member_exists`` rejects a worker_id absent from both the
    candidate pool and the fixed members, so this is discarded on every attempt.
    """
    return AgentOutput(
        mode=mode,
        request_id=request_id,
        recommendations=[
            Recommendation(rank=1, member_ids=["GHOST"], total_cost=1,
                           reason="무효 출력", considerations=["invalid"]),
        ],
    )


# --------------------------------------------------------------------------- #
# Seeding helpers                                                              #
# --------------------------------------------------------------------------- #
def _seed_normal_composing(db, *, request_id="REQ1", office_id=OFFICE_ID):
    """Seed a COMPOSING WorkRequest (lock already held) + the READY candidate W1.

    The request is COMPOSING because the entry state guard (``REQUESTED -> COMPOSING``) has
    already run by the time ``compose_flow_with_retry`` executes; seeding it here lets the
    terminal ``COMPOSING -> PROPOSED`` (success) or the ``COMPOSING -> REQUESTED`` rollback
    (failure) record as a successful conditional write.
    """
    db.add_work_request(request_id, status="COMPOSING", office_id=office_id)
    db.add_worker("W1", office_id=office_id, state="READY", trade="FORMWORK",
                  desired_daily_wage=150_000, current_crew_id=None,
                  skill_level=3, career_years=5)


def _seed_emergency(db, *, request_id="REQ_E", event_id="GE1", crew_id="CREW1",
                    office_id=OFFICE_ID):
    """Seed a RUNNING WorkRequest + a RECOMPOSING GapEvent for the EMERGENCY flows.

    The WorkRequest is RUNNING so a test can prove EMERGENCY never rewinds it. The GapEvent
    is RECOMPOSING (gap_event/agent_invoke already acquired the lock) so the EXTERNAL path's
    ``RECOMPOSING -> FAILED`` transition records as a successful conditional write.
    """
    db.add_work_request(request_id, status="RUNNING", office_id=office_id)
    db.add_gap_event(event_id, status="RECOMPOSING", crew_id=crew_id, office_id=office_id)


# =========================================================================== #
# 1. Retry exactly once (Req 9.1)                                              #
# =========================================================================== #
def test_retry_exactly_once_when_output_invalid_twice(install_shared):
    """Invalid output on both attempts -> exactly two compose calls, then AGENT_RETRY_FAILED.

    Confirms the retry budget is exactly one retry (initial + one), the invalid output is
    never saved, and no PROPOSED transition occurs.
    """
    db = install_shared.db
    _seed_normal_composing(db)
    agent_input = _normal_input()
    save_ctx = SaveContext(mode="NORMAL", request_id="REQ1", office_id=OFFICE_ID)
    scripted = _ScriptedCompose(_invalid_output(), _invalid_output())

    with pytest.raises(handler._FlowError) as excinfo:
        handler.compose_flow_with_retry(
            agent_input, save_ctx, path=handler._PATH_EXTERNAL,
            compose_fn=scripted, fallback_enabled=False,
        )

    assert excinfo.value.code == "AGENT_RETRY_FAILED"
    assert scripted.calls == 2  # initial attempt + exactly one retry (never a third)
    assert db.saved_crews == []  # an invalid output is never saved
    # No PROPOSED transition happened (the Property 9 boundary is preserved on the retry path).
    assert all(t["target"] != "PROPOSED" for t in db.status_transitions)


# =========================================================================== #
# 2. Retry recovers (Req 9.1)                                                  #
# =========================================================================== #
def test_retry_recovers_when_second_attempt_is_valid(install_shared):
    """Invalid-then-valid -> two compose calls, success, saved, NORMAL COMPOSING->PROPOSED."""
    db = install_shared.db
    _seed_normal_composing(db)
    agent_input = _normal_input()
    save_ctx = SaveContext(mode="NORMAL", request_id="REQ1", office_id=OFFICE_ID)
    scripted = _ScriptedCompose(_invalid_output(), _valid_normal_output())

    resp = handler.compose_flow_with_retry(
        agent_input, save_ctx, path=handler._PATH_EXTERNAL,
        compose_fn=scripted, fallback_enabled=False,
    )

    assert _body(resp)["success"] is True
    assert scripted.calls == 2  # recovered on the retry
    assert len(db.saved_crews) == 1
    assert db.saved_crews[0]["status"] == "PROPOSED"
    assert db.saved_crews[0]["source"] == "AGENT"
    # NORMAL performs the terminal COMPOSING -> PROPOSED transition on save.
    kinds = [(t["expected"], t["target"], t["ok"]) for t in db.status_transitions]
    assert ("COMPOSING", "PROPOSED", True) in kinds
    assert db.work_requests["REQ1"]["status"] == "PROPOSED"


# =========================================================================== #
# 3. NORMAL rollback — the legitimate transition 6.4 owns (Req 9.2)            #
# =========================================================================== #
def test_normal_rollback_composing_to_requested_exactly_once(install_shared):
    """NORMAL retry exhaustion rolls COMPOSING->REQUESTED exactly once + AGENT_RETRY_FAILED.

    This rollback is the legitimate state transition 6.4 verifies — distinct from Property 9
    (task 5.4), which only forbids a *save* or a *PROPOSED* transition on an invalid output.
    """
    db = install_shared.db
    _seed_normal_composing(db)
    agent_input = _normal_input()
    save_ctx = SaveContext(mode="NORMAL", request_id="REQ1", office_id=OFFICE_ID)
    scripted = _ScriptedCompose(_invalid_output(), _invalid_output())

    with pytest.raises(handler._FlowError) as excinfo:
        handler.compose_flow_with_retry(
            agent_input, save_ctx, path=handler._PATH_EXTERNAL,
            compose_fn=scripted, fallback_enabled=False,
        )

    assert excinfo.value.code == "AGENT_RETRY_FAILED"
    # Exactly one COMPOSING -> REQUESTED rollback, and it succeeded.
    rollbacks = [t for t in db.status_transitions
                 if t["expected"] == "COMPOSING" and t["target"] == "REQUESTED"]
    assert len(rollbacks) == 1
    assert rollbacks[0]["ok"] is True
    assert db.work_requests["REQ1"]["status"] == "REQUESTED"  # manual composition now possible
    # No PROPOSED transition and nothing saved (only the legitimate rollback happened).
    assert all(t["target"] != "PROPOSED" for t in db.status_transitions)
    assert db.saved_crews == []


# =========================================================================== #
# 4. EMERGENCY — no WorkRequest rollback; per-path GapEvent ownership          #
# =========================================================================== #
def test_emergency_internal_touches_neither_work_request_nor_gap_event(install_shared):
    """EMERGENCY INTERNAL invoke: on failure, no WorkRequest transition and NO GapEvent FAILED.

    The internal (gap_event trusted invoke) path leaves the GapEvent terminal transition to
    gap_event (task 8.5); agent_invoke must not transition it. EMERGENCY never touches the
    WorkRequest (it may be RUNNING).
    """
    db = install_shared.db
    _seed_emergency(db)
    agent_input = _emergency_input()
    save_ctx = SaveContext(mode="EMERGENCY", request_id="REQ_E", office_id=OFFICE_ID,
                           current_crew_id="CREW1", gap_event_id="GE1")
    scripted = _ScriptedCompose(
        _invalid_output("REQ_E", "EMERGENCY"), _invalid_output("REQ_E", "EMERGENCY")
    )

    with pytest.raises(handler._FlowError) as excinfo:
        handler.compose_flow_with_retry(
            agent_input, save_ctx, path=handler._PATH_INTERNAL, event_id="GE1",
            compose_fn=scripted, fallback_enabled=False,
        )

    assert excinfo.value.code == "AGENT_RETRY_FAILED"
    assert scripted.calls == 2
    assert db.saved_crews == []
    # EMERGENCY must not rewind/alter the WorkRequest state machine.
    assert db.status_transitions == []
    assert db.work_requests["REQ_E"]["status"] == "RUNNING"
    # INTERNAL path: gap_event owns RECOMPOSING->FAILED; agent_invoke leaves the GapEvent alone.
    assert db.gap_status_transitions == []
    assert db.gap_events["GE1"]["status"] == "RECOMPOSING"


# =========================================================================== #
# 5. Bedrock fallback on/off branch (Req 9.4 / 9.2)                            #
# =========================================================================== #
def test_fallback_on_bedrock_failure_keeps_demo_path_and_saves(install_shared):
    """Bedrock forced failure + fallback ON -> the demo path yields a valid, saved proposal.

    Directly corresponds to PRD_B Day 4 "Bedrock 강제 실패 시에도 데모 경로 유지": the
    deterministic ``demo_fallback`` output passes the same server-side validation and is saved,
    and the NORMAL COMPOSING->PROPOSED transition is performed. A down Bedrock is served by the
    fallback within the same attempt (not retried), so compose_fn is called exactly once.
    """
    db = install_shared.db
    _seed_normal_composing(db)
    agent_input = _normal_input()
    save_ctx = SaveContext(mode="NORMAL", request_id="REQ1", office_id=OFFICE_ID)
    scripted = _ScriptedCompose(BedrockUnavailable("forced bedrock failure"))

    resp = handler.compose_flow_with_retry(
        agent_input, save_ctx, path=handler._PATH_EXTERNAL,
        compose_fn=scripted, fallback_enabled=True,
    )
    body = _body(resp)

    assert body["success"] is True
    assert scripted.calls == 1  # a down Bedrock is served by the fallback, not retried
    assert len(db.saved_crews) == 1
    assert db.saved_crews[0]["status"] == "PROPOSED"
    kinds = [(t["expected"], t["target"], t["ok"]) for t in db.status_transitions]
    assert ("COMPOSING", "PROPOSED", True) in kinds
    # The saved proposal is the deterministic demo output (Bedrock raised, so nothing else
    # could have produced it) — proving the demo path was taken and validated.
    expected = demo_fallback(agent_input)
    assert body["data"]["recommendations"] == [r.model_dump() for r in expected.recommendations]
    top = min(expected.recommendations, key=lambda r: r.rank)
    assert db.saved_crews[0]["member_ids"] == top.member_ids
    # ``total_cost`` on the recommendation maps to the canonical Crew's ``estimated_cost``.
    assert db.saved_crews[0]["estimated_cost"] == top.total_cost


def test_fallback_off_bedrock_failure_returns_retry_failed_without_retry_or_save(install_shared):
    """Bedrock failure + fallback OFF -> AGENT_RETRY_FAILED, no save, compose_fn called once.

    A down Bedrock with the fallback disabled is not worth retrying (design Error Handling
    mapping), so it maps straight to AGENT_RETRY_FAILED after a single attempt.
    """
    db = install_shared.db
    _seed_normal_composing(db)
    agent_input = _normal_input()
    save_ctx = SaveContext(mode="NORMAL", request_id="REQ1", office_id=OFFICE_ID)
    scripted = _ScriptedCompose(BedrockUnavailable("forced bedrock failure"))

    with pytest.raises(handler._FlowError) as excinfo:
        handler.compose_flow_with_retry(
            agent_input, save_ctx, path=handler._PATH_EXTERNAL,
            compose_fn=scripted, fallback_enabled=False,
        )

    assert excinfo.value.code == "AGENT_RETRY_FAILED"
    assert scripted.calls == 1  # a down Bedrock (fallback OFF) is NOT retried
    assert db.saved_crews == []
    # No PROPOSED transition (only the NORMAL rollback, which is the legitimate cleanup).
    assert all(t["target"] != "PROPOSED" for t in db.status_transitions)
