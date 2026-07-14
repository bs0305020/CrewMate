"""Mocked end-to-end integration test — the capstone for 담당자 B (task 9.4).

This is the REQUIRED integration test that automatically verifies the code paths behind
demo scenarios 2 and 3 (요청→AI 편성→저장, C 노쇼→A+B+E 추천→저장). It wires the REAL modules
together and mocks ONLY the external boundaries — proving the pieces compose into the three
designed flows.

REAL (wired together, not stubbed):
- ``agent_invoke`` handler → assembler → validator → persistence orchestration.
- ``gap_event`` handler → gap_logic → ``build_emergency_payload`` → trusted internal invoke.
- ``shared.auth`` (real ``get_principal`` / ``require_role``, driven by claims) and
  ``shared.responses`` (handlers return proxy responses).
- The freshest-snapshot validation context built from the same in-memory DB.

Mocked / stubbed (the four external boundaries):
- ``shared/db``   — the in-memory ``FakeSharedDB`` reached through the ``shared_gateway``
  adapter (``install_shared``). Both Lambdas import the adapter, so they share ONE instance.
- ``EventBridge`` — NORMAL is triggered by a ``ComposeRequested`` event and EMERGENCY by a
  ``GapEventDetected`` event (built as plain dicts; no Cognito principal on either).
- ``Bedrock``     — the live ``compose`` call is monkeypatched to a deterministic fake.
- ``Lambda invoke`` — the ``gap_event → agent_invoke`` seam (``invoke_agent``) is
  monkeypatched to call ``agent_invoke``'s REAL handler directly with the internal payload;
  it returns the same API-Gateway PROXY response the production boto3 invoke would.

Python 3.9: ``from __future__ import annotations`` keeps annotations lazy.
"""
from __future__ import annotations

import json
from collections import Counter

from agent.schemas import AgentInput, AgentOutput, Recommendation
from functions.agent_invoke import handler as agent_invoke_handler
from functions.gap_event import handler as gap_handler

OFFICE_ID = "OFFICE001"


def _body(resp):
    """Decode the ``{success, data|error}`` envelope from an API-Gateway proxy response."""
    return json.loads(resp["body"])


# --------------------------------------------------------------------------- #
# Fake Bedrock compose (deterministic; no live model)                          #
# --------------------------------------------------------------------------- #
def _valid_output_for(agent_input: AgentInput) -> AgentOutput:
    """Build a rule-compliant :class:`AgentOutput` derived from ``agent_input``."""
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
    """Stand-in for ``agent.crew_agent.compose`` — returns a valid output (no Bedrock)."""
    return _valid_output_for(agent_input)


# --------------------------------------------------------------------------- #
# EventBridge event builders                                                   #
# --------------------------------------------------------------------------- #
def _normal_event(request_id, *, office_id=OFFICE_ID):
    """An EventBridge ``ComposeRequested`` event (the NORMAL trigger A's office_core emits)."""
    return {
        "source": "crewmate.office",
        "detail-type": "ComposeRequested",
        "detail": {"request_id": request_id, "office_id": office_id},
    }


def _gap_detected_eventbridge_event(event_id, crew_id, request_id, gap_type="NO_SHOW",
                                    missing_worker_ids=("C",), *, office_id=OFFICE_ID):
    """An EventBridge ``GapEventDetected`` event, as ``company_request`` would publish it.

    ``company_request`` creates the GapEvent (DETECTED) and publishes this event's ``detail``
    to EventBridge; gap_event is the target that consumes it. Mirrors
    ``company_request._publish_gap_event``'s Detail schema exactly.
    """
    return {
        "source": "crewmate.company",
        "detail-type": "GapEventDetected",
        "detail": {
            "event_id": event_id,
            "office_id": office_id,
            "crew_id": crew_id,
            "request_id": request_id,
            "gap_type": gap_type,
            "missing_worker_ids": list(missing_worker_ids),
        },
    }


# --------------------------------------------------------------------------- #
# Seeding helpers                                                              #
# --------------------------------------------------------------------------- #
def _seed_running_crew_ABC(db, *, crew_id, request_id, office_id=OFFICE_ID):
    """Seed a RUNNING crew (A, B, C all RUNNING FORMWORK) + a READY replacement E."""
    db.add_work_request(
        request_id,
        status="RUNNING",  # during an emergency the original request may already be RUNNING
        office_id=office_id,
        required_workers=[{"trade": "FORMWORK", "count": 3}],
        budget=2_000_000,
        priority={"cost": "MEDIUM", "skill": "HIGH", "teamwork": "HIGH"},
        site_name="현장 E",
        work_date="2025-01-02",
        start_time="07:00",
    )
    db.add_crew(
        crew_id,
        request_id=request_id,
        office_id=office_id,
        active_members=[
            {"worker_id": "A", "trade": "FORMWORK", "desired_daily_wage": 150_000,
             "state": "RUNNING"},
            {"worker_id": "B", "trade": "FORMWORK", "desired_daily_wage": 155_000,
             "state": "RUNNING"},
            {"worker_id": "C", "trade": "FORMWORK", "desired_daily_wage": 160_000,
             "state": "RUNNING"},
        ],
    )
    db.add_worker("A", office_id=office_id, state="RUNNING", trade="FORMWORK",
                  desired_daily_wage=150_000, current_crew_id=crew_id,
                  skill_level=4, career_years=9)
    db.add_worker("B", office_id=office_id, state="RUNNING", trade="FORMWORK",
                  desired_daily_wage=155_000, current_crew_id=crew_id,
                  skill_level=4, career_years=7)
    db.add_worker("C", office_id=office_id, state="RUNNING", trade="FORMWORK",
                  desired_daily_wage=160_000, current_crew_id=crew_id,
                  skill_level=3, career_years=5)
    db.add_worker("E", office_id=office_id, state="READY", trade="FORMWORK",
                  desired_daily_wage=158_000, current_crew_id=None,
                  skill_level=4, career_years=6)


def _call_index(db, method):
    """Index of the first recorded call to ``method`` in ``db.calls`` (-1 if absent)."""
    for i, c in enumerate(db.calls):
        if c.get("method") == method:
            return i
    return -1


# =========================================================================== #
# Path 1 — NORMAL happy path (demo scenario 2: 요청 → AI 편성 → 저장)            #
# =========================================================================== #
def test_normal_end_to_end_request_to_saved_proposal(install_shared, monkeypatch):
    """NORMAL ComposeRequested end-to-end: event → assembly → compose → freshest snapshot →
    validate → Crew(PROPOSED) saved + WorkRequest COMPOSING→PROPOSED (demo scenario 2)."""
    db = install_shared.db

    db.add_work_request(
        "REQ-N1",
        status="REQUESTED",
        office_id=OFFICE_ID,
        required_workers=[{"trade": "FORMWORK", "count": 2}],
        budget=1_000_000,
        priority={"cost": "HIGH", "skill": "MEDIUM", "teamwork": "LOW"},
        site_name="현장 A",
        work_date="2025-01-01",
        start_time="08:00",
    )
    db.add_worker("W1", office_id=OFFICE_ID, state="READY", trade="FORMWORK",
                  desired_daily_wage=150_000, skill_level=3, career_years=5)
    db.add_worker("W2", office_id=OFFICE_ID, state="READY", trade="FORMWORK",
                  desired_daily_wage=160_000, skill_level=4, career_years=8)
    # Out-of-scope workers must never surface: a RUNNING worker and a different-office one.
    db.add_worker("W3", office_id=OFFICE_ID, state="RUNNING", trade="FORMWORK",
                  desired_daily_wage=140_000)
    db.add_worker("OTHER", office_id="OFFICE999", state="READY", trade="FORMWORK",
                  desired_daily_wage=140_000)

    monkeypatch.setattr(agent_invoke_handler, "compose", _fake_compose)

    resp = agent_invoke_handler.handler(_normal_event("REQ-N1"))
    body = _body(resp)

    # --- success response with recommendations (Req 6.2) ---
    assert body["success"] is True
    assert body["data"]["mode"] == "NORMAL"
    assert body["data"]["request_id"] == "REQ-N1"
    assert body["data"]["crew_id"]
    assert len(body["data"]["recommendations"]) == 1

    # --- Crew(status=PROPOSED, source=AGENT) saved with the recommended members (Req 8.1) ---
    assert len(db.saved_crews) == 1
    crew = db.saved_crews[0]
    assert crew["status"] == "PROPOSED"
    assert crew["source"] == "AGENT"
    assert set(crew["member_ids"]) == {"W1", "W2"}  # only office-scoped READY candidates
    assert "OTHER" not in crew["member_ids"] and "W3" not in crew["member_ids"]

    # --- WorkRequest REQUESTED→COMPOSING then COMPOSING→PROPOSED (Req 8.2) ---
    kinds = [(t["expected"], t["target"], t["ok"]) for t in db.status_transitions]
    assert kinds == [
        ("REQUESTED", "COMPOSING", True),
        ("COMPOSING", "PROPOSED", True),
    ]
    assert db.work_requests["REQ-N1"]["status"] == "PROPOSED"

    # --- freshest snapshot: get_workers read for the recommended members BEFORE save (Req 6.5) ---
    get_workers_calls = db.method_calls("get_workers")
    assert len(get_workers_calls) == 1
    assert set(get_workers_calls[0]["worker_ids"]) == {"W1", "W2"}
    assert 0 <= _call_index(db, "get_workers") < _call_index(db, "save_crew")


# =========================================================================== #
# Path 2 — EMERGENCY trusted internal invoke (demo scenario 3: C 노쇼 → A+B+E)  #
# =========================================================================== #
def test_emergency_internal_invoke_end_to_end_no_show_recompose(install_shared, monkeypatch):
    """gap_event → trusted internal invoke → agent_invoke, end-to-end (demo scenario 3).

    A COMPANY-registered NO_SHOW gap on a RUNNING crew (C departs) drives
    ``DETECTED→RECOMPOSING→PROPOSED``: gap_event computes fixed_members (A, B) and the
    shortage, locks the GapEvent, and synchronously invokes agent_invoke's REAL handler
    against the SAME FakeSharedDB. **gap_event** owns the terminal ``RECOMPOSING→PROPOSED``.
    """
    db = install_shared.db
    _seed_running_crew_ABC(db, crew_id="CREW-E1", request_id="REQ-E1")
    # company_request has ALREADY created the GapEvent as DETECTED and published the
    # EventBridge event; seed that DETECTED gap so gap_event only recomposes it.
    event_id = "GE-E1"
    db.add_gap_event(event_id, status="DETECTED", crew_id="CREW-E1", request_id="REQ-E1",
                     missing_worker_ids=["C"], gap_type="NO_SHOW", office_id=OFFICE_ID)

    monkeypatch.setattr(agent_invoke_handler, "compose", _fake_compose)

    # Wire the gap_event → agent_invoke internal invoke: replace the boto3/Lambda seam with a
    # DIRECT call to agent_invoke's REAL handler (which returns a PROXY response), capturing
    # the payload so we can assert the trusted-internal contract.
    invoked_payloads = []

    def _internal_invoke(payload):
        invoked_payloads.append(payload)
        return agent_invoke_handler.handler(payload)

    monkeypatch.setattr(gap_handler, "invoke_agent", _internal_invoke)

    result = gap_handler.handler(
        _gap_detected_eventbridge_event(event_id, "CREW-E1", "REQ-E1", "NO_SHOW", ["C"])
    )

    # --- gap_event status dict (EventBridge Lambda → plain dict): EMERGENCY, ended at PROPOSED ---
    assert result["mode"] == "EMERGENCY"
    assert result["gap_status"] == "PROPOSED"
    assert result["event_id"] == event_id

    # --- gap_event does NOT create the GapEvent (company_request did) ---
    assert db.method_calls("save_gap_event") == []
    assert db.saved_gap_events == []

    # --- the Lambda-invoke seam actually fired, with the trusted-internal contract ---
    assert len(invoked_payloads) == 1
    payload = invoked_payloads[0]
    assert payload["internal_invoke"] is True
    assert payload["mode"] == "EMERGENCY"
    assert payload["event_id"] == event_id
    assert payload["current_crew_id"] == "CREW-E1"
    # fixed_members = active(A,B,C) − departed(C) = {A, B}, carried in the EMERGENCY payload.
    fixed_ids = sorted(f["worker_id"] for f in payload["agent_input"]["fixed_members"])
    assert fixed_ids == ["A", "B"]

    # --- Option-1 hand-off: NO Crew is created; the recommendations are recorded on the
    #     GapEvent (fixed_member_ids + {replacement_member_ids, total_cost, reason}) ---
    assert db.saved_crews == []
    # fixed members = active(A,B,C) − departed(C) = A, B (retained, kept RUNNING).
    assert db.gap_events[event_id]["fixed_member_ids"] == ["A", "B"]
    recs = db.gap_events[event_id]["recommendations"]
    assert len(recs) == 1
    # Only the NEW hire E is a replacement; A/B are retained (not "replacements"), C excluded.
    assert recs[0]["replacement_member_ids"] == ["E"]
    assert "C" not in recs[0]["replacement_member_ids"]
    # The handler surfaced the same option-1 data in its status dict.
    assert result["fixed_member_ids"] == ["A", "B"]
    assert result["recommendations"] == recs
    # EMERGENCY must NOT touch the WorkRequest state machine (it is RUNNING).
    assert db.status_transitions == []
    assert db.work_requests["REQ-E1"]["status"] == "RUNNING"

    # --- terminal-transition ownership: gap_event owns RECOMPOSING→PROPOSED (Req 10.7) ---
    gap_transitions = [(t["expected"], t["target"], t["ok"]) for t in db.gap_status_transitions]
    assert gap_transitions == [
        ("DETECTED", "RECOMPOSING", True),   # gap_event acquired the lock
        ("RECOMPOSING", "PROPOSED", True),   # gap_event owns the terminal transition
    ]
    assert db.gap_events[event_id]["status"] == "PROPOSED"
    # PROPOSED-only scope: never APPROVED/FILLED (담당자 A's approval API).
    _targets = {t["target"] for t in db.gap_status_transitions}
    _expecteds = {t["expected"] for t in db.gap_status_transitions}
    assert "APPROVED" not in _targets and "FILLED" not in _targets
    assert "APPROVED" not in _expecteds and "FILLED" not in _expecteds

    # --- freshest snapshot fed the validator (get_workers for the recommended members) ---
    get_workers_calls = db.method_calls("get_workers")
    assert len(get_workers_calls) == 1
    assert set(get_workers_calls[0]["worker_ids"]) == {"A", "B", "E"}

    # --- no worker-state change anywhere (assignment/approval is 담당자 A's) ---
    assert db.workers["A"]["state"] == "RUNNING"
    assert db.workers["B"]["state"] == "RUNNING"
    assert db.workers["C"]["state"] == "RUNNING"  # departed worker not mutated here
    assert db.workers["E"]["state"] == "READY"    # replacement not reserved/assigned


# NOTE: The former "Path 3 — EMERGENCY external/direct agent-recompose" end-to-end test was
# removed with the external ``agent-recompose`` route (decision 5). EMERGENCY is now driven
# exclusively by the gap_event EventBridge → trusted internal invoke path exercised in Path 2.
