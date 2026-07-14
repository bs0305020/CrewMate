"""Execution-flow unit tests for the gap_event Lambda handler (담당자 B).

Under 담당자 A's decided design, gap_event is the **EventBridge consumer** of the
``GapEventDetected`` event published by ``company_request`` (which has ALREADY created the
GapEvent as ``DETECTED`` and published the event's ``detail``). So these tests:

- PRE-SEED a DETECTED GapEvent (as company_request would) + the affected crew + the workers
  into the in-memory ``FakeSharedDB`` (reached through the ``shared_gateway`` adapter via the
  ``install_shared`` fixture), then
- fire an **EventBridge event** (a dict with ``detail`` = {event_id, office_id, crew_id,
  request_id, gap_type, missing_worker_ids}) at ``gap_handler.handler``.

There is NO auth on the EventBridge path (an EventBridge invocation carries no Cognito
principal), so there are no FORBIDDEN cases. The one seam to agent_invoke —
``handler.invoke_agent`` — is monkeypatched so NO real Lambda / boto3 call happens; it
returns an API-Gateway PROXY dict built with the real ``responses.success`` / ``responses.error``
(the shape the production boto3 invoke would return). The handler returns a PLAIN status
dict (not an HTTP response), which these tests assert on directly.

Python 3.9: ``from __future__ import annotations`` keeps annotations lazy.
"""
from __future__ import annotations

from functions.gap_event import handler as gap_handler
from shared import responses

OFFICE_ID = "OFFICE001"
EVENT_ID = "GAP1"

# Recommendations surfaced by the stubbed agent_invoke success response.
_AGENT_RECS = [
    {
        "rank": 1,
        "member_ids": ["F1", "N1"],
        "total_cost": 310_000,
        "reason": "결원 직종 인원을 충족하는 팀 재구성",
        "considerations": ["잔여 팀원 유지", "예산 내"],
    }
]


# --------------------------------------------------------------------------- #
# Fake agent_invoke seam (no live Lambda / boto3) — returns PROXY responses     #
# --------------------------------------------------------------------------- #
def _success_response():
    """A well-formed agent_invoke PROXY success response for EMERGENCY (option-1 hand-off).

    Under option 1 agent_invoke creates NO Crew: it returns the validated recommendations
    (each with the FULL ``member_ids`` = fixed + new) and no ``crew_id``. gap_event reshapes
    these into ``{replacement_member_ids, total_cost, reason}`` and records them on the GapEvent.
    """
    return responses.success({"recommendations": _AGENT_RECS})


def _failure_response(code="AGENT_RETRY_FAILED", message="재시도 후에도 검증 실패"):
    """A well-formed agent_invoke PROXY error response."""
    return responses.error(code, message)


def _make_capturing_invoke(sink, response):
    """A fake ``invoke_agent`` that records each payload into ``sink`` and returns ``response``."""

    def _fake(payload):
        sink.append(payload)
        return response

    return _fake


def _explode_invoke(payload):
    """A fake ``invoke_agent`` that must never be reached (asserts a pre-invoke short-circuit)."""
    raise AssertionError("invoke_agent must not be called on this path")


# --------------------------------------------------------------------------- #
# EventBridge event / seeding builders                                         #
# --------------------------------------------------------------------------- #
def _eventbridge_event(*, event_id=EVENT_ID, crew_id="CREW1", request_id="REQ_E",
                       gap_type="NO_SHOW", missing_worker_ids=("F2",), office_id=OFFICE_ID):
    """An EventBridge ``GapEventDetected`` event mirroring company_request's Detail schema."""
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


def _seed_detected_gap(db, *, event_id=EVENT_ID, crew_id="CREW1", request_id="REQ_E",
                       office_id=OFFICE_ID, missing_worker_ids=("F2",), gap_type="NO_SHOW"):
    """Seed a RUNNING request, the affected crew (F1 stays, F2 departs), workers, and the
    DETECTED GapEvent — exactly the state ``company_request`` leaves behind before publishing."""
    db.add_work_request(
        request_id,
        status="RUNNING",  # during an emergency the original request may already be RUNNING
        office_id=office_id,
        required_workers=[{"trade": "FORMWORK", "count": 2}],
        budget=1_000_000,
        priority={"cost": "HIGH", "skill": "MEDIUM", "teamwork": "LOW"},
        site_name="현장 E",
        work_date="2025-01-02",
        start_time="07:00",
    )
    db.add_crew(
        crew_id,
        request_id=request_id,
        office_id=office_id,
        active_members=[
            {"worker_id": "F1", "trade": "FORMWORK", "desired_daily_wage": 150_000,
             "state": "RUNNING"},
            {"worker_id": "F2", "trade": "FORMWORK", "desired_daily_wage": 155_000,
             "state": "RUNNING"},
        ],
    )
    db.add_worker("F1", office_id=office_id, state="RUNNING", trade="FORMWORK",
                  desired_daily_wage=150_000, current_crew_id=crew_id,
                  skill_level=4, career_years=9)
    db.add_worker("F2", office_id=office_id, state="RUNNING", trade="FORMWORK",
                  desired_daily_wage=155_000, current_crew_id=crew_id,
                  skill_level=3, career_years=6)
    db.add_worker("N1", office_id=office_id, state="READY", trade="FORMWORK",
                  desired_daily_wage=160_000, current_crew_id=None,
                  skill_level=3, career_years=4)
    # company_request already created + persisted the GapEvent as DETECTED.
    db.add_gap_event(
        event_id,
        status="DETECTED",
        office_id=office_id,
        crew_id=crew_id,
        request_id=request_id,
        gap_type=gap_type,
        missing_worker_ids=list(missing_worker_ids),
    )


def _transitions(db):
    """The recorded GapEvent transitions as ``(expected, target, ok)`` tuples, in order."""
    return [(t["expected"], t["target"], t["ok"]) for t in db.gap_status_transitions]


# =========================================================================== #
# 1. Happy path — DETECTED → RECOMPOSING → PROPOSED                            #
# =========================================================================== #
def test_emergency_happy_path_recomposes_to_proposed(install_shared, monkeypatch):
    """An EventBridge gap event drives DETECTED → RECOMPOSING → PROPOSED end-to-end."""
    db = install_shared.db
    _seed_detected_gap(db)

    invoked = []
    monkeypatch.setattr(
        gap_handler, "invoke_agent", _make_capturing_invoke(invoked, _success_response())
    )

    result = gap_handler.handler(_eventbridge_event(missing_worker_ids=["F2"]))

    # --- Plain status dict (NOT an HTTP proxy response): PROPOSED, EMERGENCY, option-1 data ---
    assert result["gap_status"] == "PROPOSED"
    assert result["mode"] == "EMERGENCY"
    assert result["event_id"] == EVENT_ID
    # Option-1 hand-off: no Crew is created; the GapEvent carries fixed_member_ids + the
    # reshaped recommendations ({replacement_member_ids, total_cost, reason}).
    assert "crew_id" not in result
    assert result["fixed_member_ids"] == ["F1"]  # active(F1,F2) minus departed(F2)
    assert result["recommendations"] == [
        {
            "replacement_member_ids": ["N1"],  # rec member_ids ["F1","N1"] minus fixed {F1}
            "total_cost": _AGENT_RECS[0]["total_cost"],
            "reason": _AGENT_RECS[0]["reason"],
        }
    ]
    # --- The recommendations + fixed_member_ids were written onto the GapEvent item ---
    assert db.gap_events[EVENT_ID]["fixed_member_ids"] == ["F1"]
    assert db.gap_events[EVENT_ID]["recommendations"] == result["recommendations"]

    # --- The GapEvent was NOT created here (company_request did) ---
    assert db.method_calls("save_gap_event") == []
    assert db.saved_gap_events == []

    # --- Affected crew was looked up (Req 10.2) ---
    assert db.method_calls("get_crew")
    assert db.method_calls("get_crew")[0]["crew_id"] == "CREW1"

    # --- Terminal transition sequence is EXACTLY DETECTED → RECOMPOSING → PROPOSED (Req 10.7) ---
    assert _transitions(db) == [
        ("DETECTED", "RECOMPOSING", True),
        ("RECOMPOSING", "PROPOSED", True),
    ]
    assert db.gap_events[EVENT_ID]["status"] == "PROPOSED"

    # --- agent_invoke called exactly once with the trusted-internal contract ---
    assert len(invoked) == 1
    payload = invoked[0]
    assert payload["internal_invoke"] is True
    assert payload["mode"] == "EMERGENCY"
    assert payload["event_id"] == EVENT_ID  # the pre-existing GapEvent gap_event locked
    assert payload["current_crew_id"] == "CREW1"
    assert "agent_input" in payload

    # --- fixed_members = active(F1,F2) minus departed(F2) == {F1} ---
    agent_input = payload["agent_input"]
    assert agent_input["mode"] == "EMERGENCY"
    fixed_ids = [f["worker_id"] for f in agent_input["fixed_members"]]
    assert fixed_ids == ["F1"]


# =========================================================================== #
# 2. Bare-detail ergonomic invoke (no EventBridge ``detail`` wrapper)          #
# =========================================================================== #
def test_bare_detail_event_is_accepted(install_shared, monkeypatch):
    """A bare detail dict (no ``detail`` wrapper) is treated as the detail (direct invoke)."""
    db = install_shared.db
    _seed_detected_gap(db)
    invoked = []
    monkeypatch.setattr(
        gap_handler, "invoke_agent", _make_capturing_invoke(invoked, _success_response())
    )

    bare = _eventbridge_event(missing_worker_ids=["F2"])["detail"]
    result = gap_handler.handler(bare)

    assert result["gap_status"] == "PROPOSED"
    assert len(invoked) == 1
    assert _transitions(db) == [
        ("DETECTED", "RECOMPOSING", True),
        ("RECOMPOSING", "PROPOSED", True),
    ]


# =========================================================================== #
# 3. CREW_INVALID — missing affected crew (Req 10.11)                          #
# =========================================================================== #
def test_missing_crew_returns_crew_invalid_without_lock_or_invoke(install_shared, monkeypatch):
    """A missing affected crew → CREW_INVALID status; no lock, no invoke, GapEvent untouched."""
    db = install_shared.db  # NOTE: no crew/gap seeded → get_crew returns None
    monkeypatch.setattr(gap_handler, "invoke_agent", _explode_invoke)

    result = gap_handler.handler(_eventbridge_event(crew_id="MISSING", missing_worker_ids=["F2"]))

    assert result["gap_status"] == "CREW_INVALID"
    assert result["event_id"] == EVENT_ID
    assert result["crew_id"] == "MISSING"

    # No creation, no lock, no transition, no recomposition.
    assert db.method_calls("save_gap_event") == []
    assert db.gap_status_transitions == []
    assert db.saved_crews == []


# =========================================================================== #
# 4. CONFLICT — the DETECTED → RECOMPOSING lock fails                          #
# =========================================================================== #
def test_lock_failure_returns_conflict_without_invoking(install_shared, monkeypatch):
    """A failed DETECTED → RECOMPOSING lock short-circuits to CONFLICT before the invoke."""
    from functions.agent_invoke import shared_gateway  # the adapter the handler calls

    db = install_shared.db
    _seed_detected_gap(db)

    # Force the conditional lock to fail (as if the GapEvent were already recomposing),
    # recording the attempt so we can prove it was the DETECTED → RECOMPOSING lock.
    attempts = []

    def _failing_transition(event_id, expected, target):
        attempts.append((event_id, expected, target))
        return False

    monkeypatch.setattr(shared_gateway, "transition_gap_event_status", _failing_transition)
    monkeypatch.setattr(gap_handler, "invoke_agent", _explode_invoke)

    result = gap_handler.handler(_eventbridge_event(missing_worker_ids=["F2"]))

    assert result["gap_status"] == "CONFLICT"
    assert result["event_id"] == EVENT_ID
    # Exactly one transition attempt — the DETECTED → RECOMPOSING lock — and nothing after it.
    assert attempts == [(EVENT_ID, "DETECTED", "RECOMPOSING")]
    assert db.saved_crews == []


# =========================================================================== #
# 5. Recomposition failure → FAILED + manual guidance (Req 10.9, 10.8)         #
# =========================================================================== #
def test_recomposition_failure_transitions_to_failed_with_manual_guidance(
    install_shared, monkeypatch
):
    """agent_invoke failure → GapEvent RECOMPOSING → FAILED, FAILED status with guidance, no PROPOSED."""
    db = install_shared.db
    _seed_detected_gap(db)
    invoked = []
    monkeypatch.setattr(
        gap_handler, "invoke_agent", _make_capturing_invoke(invoked, _failure_response())
    )

    result = gap_handler.handler(_eventbridge_event(missing_worker_ids=["F2"]))

    # --- FAILED status carrying manual-composition guidance (Req 10.9) ---
    assert result["gap_status"] == "FAILED"
    assert result["error"]["code"] == "AGENT_RETRY_FAILED"
    assert gap_handler._MANUAL_GUIDANCE in result["error"]["message"]

    # --- agent_invoke was reached exactly once ---
    assert len(invoked) == 1

    # --- GapEvent moved RECOMPOSING → FAILED and NEVER reached PROPOSED (Req 10.9) ---
    assert _transitions(db) == [
        ("DETECTED", "RECOMPOSING", True),
        ("RECOMPOSING", "FAILED", True),
    ]
    assert db.gap_events[EVENT_ID]["status"] == "FAILED"
    assert all(t["target"] != "PROPOSED" for t in db.gap_status_transitions)

    # --- No worker-state change and no WorkRequest transition (Req 10.8) ---
    assert db.saved_crews == []            # gap_event never saves a crew
    assert db.status_transitions == []     # WorkRequest state machine untouched
    assert db.workers["F1"]["state"] == "RUNNING"   # remaining team stays RUNNING
    assert db.workers["F2"]["state"] == "RUNNING"   # departed worker not mutated here
    assert db.workers["N1"]["state"] == "READY"     # candidate not reserved/assigned


# =========================================================================== #
# 6. Scope — B stops at PROPOSED/FAILED, never APPROVED/FILLED                  #
# =========================================================================== #
def test_handler_never_transitions_to_approved_or_filled(install_shared, monkeypatch):
    """The handler owns only DETECTED → RECOMPOSING → PROPOSED (or FAILED); never APPROVED/FILLED."""
    db = install_shared.db
    _seed_detected_gap(db)
    monkeypatch.setattr(
        gap_handler, "invoke_agent", _make_capturing_invoke([], _success_response())
    )

    result = gap_handler.handler(_eventbridge_event(missing_worker_ids=["F2"]))

    assert result["gap_status"] == "PROPOSED"
    targets = {t["target"] for t in db.gap_status_transitions}
    expecteds = {t["expected"] for t in db.gap_status_transitions}
    assert "APPROVED" not in targets and "APPROVED" not in expecteds
    assert "FILLED" not in targets and "FILLED" not in expecteds
    # Only the two in-scope terminal states appear.
    assert targets == {"RECOMPOSING", "PROPOSED"}
