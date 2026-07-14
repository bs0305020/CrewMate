"""Execution-flow unit tests for the agent_invoke Lambda handler (담당자 B, task 5.5).

These are EXAMPLE / UNIT tests (plain pytest - no Hypothesis, no ``property`` marker).
They exercise the control flow of ``backend/functions/agent_invoke/handler.py`` end-to-end
against the in-memory ``FakeSharedDB`` that 담당자 B's code now reaches through the
``shared_gateway`` adapter (installed by the ``install_shared`` fixture), with the Bedrock
``compose`` call replaced by a deterministic fake so no live model is invoked.

Trigger model (post Task A/B)
-----------------------------
- **DB**: the high-level DB functions are monkeypatched onto ``FakeSharedDB`` via the
  ``shared_gateway`` adapter (``install_shared``). The real ``shared`` package stays
  intact.
- **Triggers**: NORMAL is a ``ComposeRequested`` EventBridge event (``_normal_event``);
  EMERGENCY is gap_event's trusted internal invoke (``_internal_payload``). There is no API
  Gateway proxy path and NO in-handler auth gate — the OFFICE-only rule is enforced at the
  publisher / by IAM, so these tests carry no Cognito claims.
- **Responses**: the handler still returns the proxy envelope (``{statusCode, headers,
  body}``) — gap_event parses ``body`` on the EMERGENCY path; on NORMAL EventBridge ignores
  the return. ``_body`` decodes the ``{success, data|error}`` envelope.

Concerns covered (one clearly-named test per concern): routing + mode, no-principal internal
path, per-path state guard, NORMAL save split (EMERGENCY creates no Crew), EMERGENCY
terminal-transition ownership (gap_event's), and the freshest-snapshot validation.

Python 3.9: ``from __future__ import annotations`` keeps annotations lazy.
"""
from __future__ import annotations

import json
from collections import Counter

import pytest

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
from functions.agent_invoke.persistence import SaveContext
from functions.gap_event.emergency_payload import build_emergency_payload

OFFICE_ID = "OFFICE001"


def _body(resp):
    """Decode the ``{success, data|error}`` envelope from the proxy-shaped response.

    compose_flow / handler still return the proxy envelope (``{statusCode, headers, body}``)
    — it is what gap_event's internal invoke parses on the EMERGENCY path; on the NORMAL
    EventBridge path EventBridge just ignores the return value.
    """
    return json.loads(resp["body"])


# --------------------------------------------------------------------------- #
# Fake compose functions (no live Bedrock)                                     #
# --------------------------------------------------------------------------- #
def _valid_output_for(agent_input: AgentInput) -> AgentOutput:
    """Build a rule-compliant :class:`AgentOutput` derived from ``agent_input``.

    Produces ONE recommendation (rank 1) whose members exactly satisfy the request's
    required trade/headcount: every EMERGENCY ``fixed_members`` entry is kept, and the
    remaining per-trade shortage is filled from the candidate pool. ``total_cost`` is the
    sum of the retained fixed-member wages plus the picked candidates' wages, matching the
    freshest ``get_workers`` snapshot the validator uses (tests seed matching wages).
    """
    required: Counter = Counter()
    for tr in agent_input.request.required_workers:
        required[tr.trade] += tr.count

    fixed_ids = [f.worker_id for f in agent_input.fixed_members]
    remaining = Counter(required)
    for f in agent_input.fixed_members:
        if remaining.get(f.trade, 0) > 0:
            remaining[f.trade] -= 1

    by_trade: dict = {}
    for c in agent_input.candidates:
        by_trade.setdefault(c.trade, []).append(c)

    picked = []
    for trade, need in remaining.items():
        if need > 0:
            picked.extend(by_trade.get(trade, [])[:need])

    member_ids = list(fixed_ids) + [c.worker_id for c in picked]
    total_cost = sum(f.desired_daily_wage for f in agent_input.fixed_members) + sum(
        c.desired_daily_wage for c in picked
    )
    return AgentOutput(
        mode=agent_input.mode,
        request_id=agent_input.request.request_id,
        recommendations=[
            Recommendation(
                rank=1,
                member_ids=member_ids,
                total_cost=total_cost,
                reason="필요 직종 인원을 충족하는 팀 구성",
                considerations=["직종 인원 충족", "예산 내"],
            )
        ],
    )


def _fake_compose(agent_input, *, timeout_s=None, agent=None):
    """Stand-in for ``agent.crew_agent.compose`` returning a valid output (no Bedrock)."""
    return _valid_output_for(agent_input)


def _boom_compose(agent_input, *, timeout_s=None, agent=None):
    """Compose that must never be reached (asserts short-circuit before agent execution)."""
    raise AssertionError("compose must not be called on this path")


# --------------------------------------------------------------------------- #
# Event / payload builders                                                     #
# --------------------------------------------------------------------------- #
def _normal_event(request_id="REQ1", *, office_id=OFFICE_ID):
    """An EventBridge ``ComposeRequested`` event (the NORMAL trigger, as A's publisher emits)."""
    detail = {"request_id": request_id}
    if office_id:
        detail["office_id"] = office_id
    return {
        "source": "crewmate.office",
        "detail-type": "ComposeRequested",
        "detail": detail,
    }


def _internal_payload(agent_input, *, event_id="GE1", office_id=OFFICE_ID,
                      current_crew_id="CREW1"):
    """gap_event's trusted internal invoke payload (a plain dict + marker, no claims).

    Deliberately carries NO authorizer claims: the internal path is IAM-trusted and never
    calls ``get_principal`` (Req 11.3), so a claimless payload flowing through to success is
    itself proof the OFFICE gate was skipped.
    """
    return {
        handler.INTERNAL_INVOKE_MARKER: True,
        "mode": "EMERGENCY",
        "event_id": event_id,
        "agent_input": agent_input.model_dump(),
        "office_id": office_id,
        "current_crew_id": current_crew_id,
    }


def _emergency_agent_input(request_id="REQ_E"):
    """A ready-to-consume EMERGENCY ``AgentInput`` (fixed F1 + candidate N1, FORMWORK:2)."""
    request = RequestSpec(
        request_id=request_id,
        required_workers=[TradeRequirement(trade="FORMWORK", count=2)],
        budget=1_000_000,
        priority=Priority(cost="HIGH", skill="MEDIUM", teamwork="LOW"),
        site="현장 E",
        work_date="2025-01-02",
        start_time="07:00",
    )
    fixed = [FixedMember(worker_id="F1", trade="FORMWORK", desired_daily_wage=150_000)]
    candidates = [
        Candidate(worker_id="N1", trade="FORMWORK", skill_level=3,
                  desired_daily_wage=160_000, career_years=4)
    ]
    return build_emergency_payload(request, fixed, candidates, [])


# --------------------------------------------------------------------------- #
# Seeding helpers                                                              #
# --------------------------------------------------------------------------- #
def _seed_normal(db, *, request_id="REQ1", status="REQUESTED", office_id=OFFICE_ID):
    """Seed a REQUESTED WorkRequest + two READY FORMWORK candidates for the NORMAL flow."""
    db.add_work_request(
        request_id,
        status=status,
        office_id=office_id,
        required_workers=[{"trade": "FORMWORK", "count": 2}],
        budget=1_000_000,
        priority={"cost": "HIGH", "skill": "MEDIUM", "teamwork": "LOW"},
        site_name="현장 A",
        work_date="2025-01-01",
        start_time="08:00",
    )
    db.add_worker("W1", office_id=office_id, state="READY", trade="FORMWORK",
                  desired_daily_wage=150_000, skill_level=3, career_years=5)
    db.add_worker("W2", office_id=office_id, state="READY", trade="FORMWORK",
                  desired_daily_wage=160_000, skill_level=4, career_years=8)


def _seed_internal_workers(db, *, crew_id="CREW1", office_id=OFFICE_ID):
    """Seed just the workers the internal-invoke validation snapshot needs (F1 fixed, N1 new)."""
    db.add_worker("F1", office_id=office_id, state="RUNNING", trade="FORMWORK",
                  desired_daily_wage=150_000, current_crew_id=crew_id,
                  skill_level=4, career_years=9)
    db.add_worker("N1", office_id=office_id, state="READY", trade="FORMWORK",
                  desired_daily_wage=160_000, current_crew_id=None,
                  skill_level=3, career_years=4)


def _call_index(db, method):
    """Index of the first recorded call to ``method`` in ``db.calls`` (-1 if absent)."""
    for i, c in enumerate(db.calls):
        if c.get("method") == method:
            return i
    return -1


# =========================================================================== #
# 1. Routing & mode setting                                                    #
# =========================================================================== #
def test_normal_route_runs_in_normal_mode_and_succeeds(install_shared, monkeypatch):
    """A ComposeRequested EventBridge event routes to NORMAL and produces a saved proposal."""
    db = install_shared.db
    _seed_normal(db)
    monkeypatch.setattr(handler, "compose", _fake_compose)

    resp = handler.handler(_normal_event("REQ1"))
    body = _body(resp)

    assert body["success"] is True
    assert body["data"]["mode"] == "NORMAL"
    assert body["data"]["request_id"] == "REQ1"
    assert body["data"]["crew_id"]  # a crew was persisted
    assert len(body["data"]["recommendations"]) == 1
    assert len(db.saved_crews) == 1


def test_internal_invoke_routes_to_emergency_mode(install_shared, monkeypatch):
    """A trusted internal invoke (plain dict + marker) routes to EMERGENCY mode."""
    db = install_shared.db
    _seed_internal_workers(db)
    monkeypatch.setattr(handler, "compose", _fake_compose)

    resp = handler.handler(_internal_payload(_emergency_agent_input()))
    body = _body(resp)

    assert body["success"] is True
    assert body["data"]["mode"] == "EMERGENCY"
    # Option-1 hand-off: EMERGENCY creates NO Crew; it returns recommendations for gap_event
    # to record on the GapEvent (no crew_id in the response).
    assert db.saved_crews == []
    assert "crew_id" not in body["data"]
    assert len(body["data"]["recommendations"]) == 1


# =========================================================================== #
# 2. Authorization - no in-handler gate (trust is publisher / IAM enforced)    #
# =========================================================================== #
def test_internal_invoke_needs_no_principal_and_proceeds(install_shared, monkeypatch):
    """The trusted internal invoke proceeds with NO Cognito principal (Req 11.3).

    Neither entry path applies an OFFICE gate any more (NORMAL is publisher-authorized, the
    internal invoke is IAM-authorized). The internal payload carries no claims, so flowing
    through to a successful EMERGENCY compose proves no principal is required on this path —
    a COMPANY-registered gap's recomposition is not blocked.
    """
    db = install_shared.db
    _seed_internal_workers(db)
    monkeypatch.setattr(handler, "compose", _fake_compose)

    resp = handler.handler(_internal_payload(_emergency_agent_input()))
    body = _body(resp)

    assert body["success"] is True  # not blocked for lack of a principal
    assert db.saved_crews == []  # option-1: EMERGENCY creates no Crew
    assert len(body["data"]["recommendations"]) == 1


# =========================================================================== #
# 3. State guard - per-path branching                                          #
# =========================================================================== #
def test_normal_conditional_write_failure_returns_state_conflict(install_shared, monkeypatch):
    """NORMAL: a failed REQUESTED->COMPOSING conditional write -> STATE_CONFLICT (no save)."""
    db = install_shared.db
    # Already COMPOSING => the REQUESTED->COMPOSING conditional transition fails.
    _seed_normal(db, status="COMPOSING")
    monkeypatch.setattr(handler, "compose", _boom_compose)

    resp = handler.handler(_normal_event("REQ1"))
    body = _body(resp)

    assert body["success"] is False
    assert body["error"]["code"] == "STATE_CONFLICT"
    # The single (failed) transition attempt was recorded; nothing was saved.
    assert len(db.status_transitions) == 1
    assert db.status_transitions[0]["ok"] is False
    assert db.saved_crews == []


def test_internal_invoke_accepts_recomposing_without_conflict(install_shared, monkeypatch):
    """Trusted internal invoke ACCEPTS an already-RECOMPOSING GapEvent and never conflicts."""
    db = install_shared.db
    _seed_internal_workers(db)
    db.add_gap_event("GE1", status="RECOMPOSING", crew_id="CREW1")
    monkeypatch.setattr(handler, "compose", _fake_compose)

    resp = handler.handler(_internal_payload(_emergency_agent_input()))

    assert _body(resp)["success"] is True
    assert db.saved_crews == []  # option-1: EMERGENCY creates no Crew
    # agent_invoke's internal path never transitions the GapEvent (gap_event owns it).
    assert db.gap_status_transitions == []
    assert db.gap_events["GE1"]["status"] == "RECOMPOSING"


# =========================================================================== #
# 4. Save split - NORMAL transitions WorkRequest, EMERGENCY does not           #
# =========================================================================== #
def test_normal_save_transitions_work_request_to_proposed(install_shared, monkeypatch):
    """NORMAL takes the save_normal_proposal path: Crew(PROPOSED) + WorkRequest ->PROPOSED."""
    db = install_shared.db
    _seed_normal(db)
    monkeypatch.setattr(handler, "compose", _fake_compose)

    resp = handler.handler(_normal_event("REQ1"))

    assert _body(resp)["success"] is True
    # Crew stored as a PROPOSED, AGENT-sourced proposal.
    assert len(db.saved_crews) == 1
    assert db.saved_crews[0]["status"] == "PROPOSED"
    assert db.saved_crews[0]["source"] == "AGENT"
    # Two WorkRequest transitions: the entry lock and the terminal COMPOSING->PROPOSED.
    kinds = [(t["expected"], t["target"], t["ok"]) for t in db.status_transitions]
    assert ("REQUESTED", "COMPOSING", True) in kinds
    assert ("COMPOSING", "PROPOSED", True) in kinds
    assert db.work_requests["REQ1"]["status"] == "PROPOSED"


def test_internal_invoke_save_does_not_transition_work_request(install_shared, monkeypatch):
    """Internal EMERGENCY invoke saves no Crew and never transitions the WorkRequest."""
    db = install_shared.db
    _seed_internal_workers(db)
    monkeypatch.setattr(handler, "compose", _fake_compose)

    resp = handler.handler(_internal_payload(_emergency_agent_input()))

    assert _body(resp)["success"] is True
    assert db.saved_crews == []  # option-1: EMERGENCY creates no Crew
    assert db.status_transitions == []  # no WorkRequest transition on the internal path


# =========================================================================== #
# 5. EMERGENCY terminal-transition ownership (per path)                        #
# =========================================================================== #
def test_internal_invoke_does_not_transition_gap_event(install_shared, monkeypatch):
    """Trusted internal invoke: agent_invoke does NOT transition the GapEvent (gap_event owns it)."""
    db = install_shared.db
    _seed_internal_workers(db)
    db.add_gap_event("GE1", status="RECOMPOSING", crew_id="CREW1")
    monkeypatch.setattr(handler, "compose", _fake_compose)

    resp = handler.handler(_internal_payload(_emergency_agent_input()))

    assert _body(resp)["success"] is True
    assert db.saved_crews == []  # option-1: EMERGENCY creates no Crew
    # The terminal RECOMPOSING->PROPOSED transition is gap_event's responsibility, not ours.
    assert db.gap_status_transitions == []
    assert db.gap_events["GE1"]["status"] == "RECOMPOSING"


# =========================================================================== #
# 6. Freshest snapshot (검증 직전 최신 스냅샷)                                    #
# =========================================================================== #
def test_normal_reads_fresh_snapshot_before_validation_and_save(install_shared, monkeypatch):
    """get_workers is read for the recommended members and BEFORE the crew is saved."""
    db = install_shared.db
    _seed_normal(db)
    monkeypatch.setattr(handler, "compose", _fake_compose)

    resp = handler.handler(_normal_event("REQ1"))

    assert _body(resp)["success"] is True
    get_workers_calls = db.method_calls("get_workers")
    assert len(get_workers_calls) == 1
    assert get_workers_calls[0]["worker_ids"] == ["W1", "W2"]  # the recommended members
    # The fresh read happened before the crew was persisted (validation sits between them).
    assert 0 <= _call_index(db, "get_workers") < _call_index(db, "save_crew")


def test_compose_flow_validates_against_freshest_snapshot_over_stale_pool(install_shared):
    """The injected fresh snapshot (get_workers) - not the stale agent-input pool - drives validity."""
    db = install_shared.db
    db.add_worker("N1", office_id=OFFICE_ID, state="READY", trade="FORMWORK",
                  desired_daily_wage=175_000, current_crew_id=None,
                  skill_level=3, career_years=4)

    # Stale agent-input pool: wage 100_000 (differs from the fresh DB value).
    stale_candidate = Candidate(worker_id="N1", trade="FORMWORK", skill_level=3,
                                desired_daily_wage=100_000, career_years=4)
    agent_input = AgentInput(
        mode="NORMAL",
        request=RequestSpec(
            request_id="REQF",
            required_workers=[TradeRequirement(trade="FORMWORK", count=1)],
            budget=1_000_000,
            priority=Priority(cost="MEDIUM", skill="MEDIUM", teamwork="MEDIUM"),
            site="현장 F", work_date="2025-04-01", start_time="08:00",
        ),
        candidates=[stale_candidate],
    )
    save_ctx = SaveContext(mode="NORMAL", request_id="REQF", office_id=OFFICE_ID)

    # (a) total_cost matching the FRESH wage (175_000) validates and saves.
    def _fresh_valued(ai, *, timeout_s=None, agent=None):
        return AgentOutput(
            mode="NORMAL", request_id="REQF",
            recommendations=[Recommendation(rank=1, member_ids=["N1"], total_cost=175_000,
                                            reason="fresh", considerations=["ok"])],
        )

    resp = handler.compose_flow(agent_input, save_ctx, path=handler._PATH_EXTERNAL,
                                compose_fn=_fresh_valued)
    assert _body(resp)["success"] is True
    assert len(db.saved_crews) == 1
    # The fresh read was for the recommended member only.
    assert db.method_calls("get_workers")[0]["worker_ids"] == ["N1"]

    # (b) total_cost matching the STALE pool wage (100_000) fails validation - nothing saved.
    def _stale_valued(ai, *, timeout_s=None, agent=None):
        return AgentOutput(
            mode="NORMAL", request_id="REQF",
            recommendations=[Recommendation(rank=1, member_ids=["N1"], total_cost=100_000,
                                            reason="stale", considerations=["no"])],
        )

    with pytest.raises(handler._FlowError) as excinfo:
        handler.compose_flow(agent_input, save_ctx, path=handler._PATH_EXTERNAL,
                             compose_fn=_stale_valued)
    assert excinfo.value.code == "AGENT_OUTPUT_INVALID"
    assert len(db.saved_crews) == 1  # unchanged - the invalid output was NOT saved
