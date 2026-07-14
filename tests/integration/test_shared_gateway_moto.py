"""Moto-backed REAL single-table test for the ``shared_gateway`` adapter (checkpoint 2 capstone).

Unlike the other 담당자 B tests — which drive the flow against the in-memory ``FakeSharedDB``
through the adapter — this module proves the adapter + a full NORMAL compose flow work against
담당자 A's REAL ``shared.db`` on a moto-mocked DynamoDB single table. It is the
capstone that shows checkpoint-2 compatibility is REAL, not just stub-shaped.

What is REAL here
-----------------
- ``shared.db`` (real low-level single-table API) against a moto ``CrewMate-test``
  table (the teammate's ``table`` fixture from ``tests/conftest.py``).
- ``shared.schemas`` item builders + ``shared.state`` enums.
- The ``shared_gateway`` adapter and the agent_invoke handler → assembler → validator →
  persistence chain.
- ``shared.responses`` (the proxy envelope the handler returns). NORMAL is triggered
  by a ``ComposeRequested`` EventBridge event (no Cognito principal).

Only Bedrock is mocked (``compose`` monkeypatched to a deterministic fake).

Module-object caching note (from the task brief)
------------------------------------------------
The real ``db.py`` caches ``_resource`` / ``_table`` and reads ``TABLE_NAME`` at import. The
teammate ``table`` fixture resets ``shared.db`` (imported as top-level ``shared.db``), but the
adapter imports ``shared.db`` — a DISTINCT module object. The ``real_table`` fixture
below therefore rebinds the SAME module object the adapter uses to the moto table
(``TABLE_NAME`` + cache reset), so the adapter provably hits moto.
"""
from __future__ import annotations

import json
import os
from collections import Counter

import pytest

from agent.schemas import AgentInput, AgentOutput, Recommendation
from functions.agent_invoke import handler as agent_invoke_handler
from functions.agent_invoke import shared_gateway as sg
from shared import db as shared_db
from shared import schemas
from shared.state import CrewStatus, GapStatus, GapType, RequestStatus, WorkerState

OFFICE_ID = "OFFICE001"
COMPANY_ID = "COMPANY001"


# --------------------------------------------------------------------------- #
# Fixture: bind the ADAPTER's shared.db to the moto CrewMate-test table #
# --------------------------------------------------------------------------- #
@pytest.fixture
def real_table(table):
    """Point the adapter's ``shared.db`` at the moto ``CrewMate-test`` table.

    Depends on the teammate ``table`` fixture (creates the moto table inside ``mock_aws``).
    That fixture resets the top-level ``shared.db`` cache, but the adapter imports
    ``shared.db`` — a different module object — so here we rebind THAT object's
    ``TABLE_NAME`` (to the env value the ``_aws_env`` fixture set) and clear its lazy caches,
    guaranteeing the adapter's ``get_table()`` binds to the moto-mocked table. Restored on
    teardown.
    """
    prev_name = shared_db.TABLE_NAME
    shared_db.TABLE_NAME = os.environ["TABLE_NAME"]  # "CrewMate-test" (set by _aws_env)
    shared_db._resource = None
    shared_db._table = None
    try:
        yield table
    finally:
        shared_db.TABLE_NAME = prev_name
        shared_db._resource = None
        shared_db._table = None


# --------------------------------------------------------------------------- #
# Fake Bedrock compose (deterministic; no live model)                          #
# --------------------------------------------------------------------------- #
def _valid_output_for(agent_input: AgentInput) -> AgentOutput:
    """A rule-compliant output: fill each required trade's headcount from the candidate pool."""
    required: Counter = Counter()
    for tr in agent_input.request.required_workers:
        required[tr.trade] += tr.count
    by_trade: dict = {}
    for c in agent_input.candidates:
        by_trade.setdefault(c.trade, []).append(c)
    picked = []
    for trade, need in required.items():
        picked.extend(by_trade.get(trade, [])[:need])
    member_ids = [c.worker_id for c in picked]
    total_cost = sum(c.desired_daily_wage for c in picked)
    return AgentOutput(
        mode=agent_input.mode,
        request_id=agent_input.request.request_id,
        recommendations=[
            Recommendation(rank=1, member_ids=member_ids, total_cost=total_cost,
                           reason="필요 직종 인원 충족", considerations=["예산 내"]),
        ],
    )


def _fake_compose(agent_input, *, timeout_s=None, agent=None):
    return _valid_output_for(agent_input)


def _agent_compose_event(request_id, *, office_id=OFFICE_ID):
    """EventBridge ``ComposeRequested`` event (the NORMAL trigger consumed by agent_invoke)."""
    return {
        "source": "crewmate.office",
        "detail-type": "ComposeRequested",
        "detail": {"request_id": request_id, "office_id": office_id},
    }


def _body(resp):
    return json.loads(resp["body"])


def _seed_ready_worker(worker_id, *, wage, trade="FORMWORK", office_id=OFFICE_ID):
    """Build + persist a READY worker via the REAL schema/db helpers."""
    item = schemas.build_worker(
        user_id=f"user-{worker_id}",
        worker_id=worker_id,
        name="테스트",
        phone="010-0000-0000",
        office_id=office_id,
        trade=trade,
        skill_level=3,
        career_years=5,
        age=40,
        region="BUSAN_HAEUNDAE",
        desired_daily_wage=wage,
        state=WorkerState.READY,
    )
    shared_db.put_item(item)
    return item


def _seed_requested_request(request_id, *, office_id=OFFICE_ID):
    """Build + persist a REQUESTED WorkRequest (FORMWORK:2) via the REAL schema/db helpers."""
    item = schemas.build_request(
        company_id=COMPANY_ID,
        office_id=office_id,
        site_name="현장 모토",
        work_date="2025-01-01",
        start_time="08:00",
        location_text="부산 해운대구",
        required_workers=[{"trade": "FORMWORK", "count": 2}],
        budget=1_000_000,
        request_id=request_id,
        status=RequestStatus.REQUESTED,
    )
    shared_db.put_item(item)
    return item


# =========================================================================== #
# Full NORMAL flow against the REAL single table                               #
# =========================================================================== #
def test_normal_flow_writes_crew_and_transitions_request_on_real_table(real_table, monkeypatch):
    """NORMAL ComposeRequested end-to-end against moto: a Crew(PROPOSED, AGENT) is written and
    the WorkRequest moves REQUESTED→COMPOSING→PROPOSED — read back from the REAL table."""
    _seed_requested_request("REQ-M1")
    _seed_ready_worker("MW1", wage=150_000)
    _seed_ready_worker("MW2", wage=160_000)
    # Out-of-scope worker: different office, must not be recommended.
    _seed_ready_worker("MW-OTHER", wage=140_000, office_id="OFFICE999")

    monkeypatch.setattr(agent_invoke_handler, "compose", _fake_compose)

    resp = agent_invoke_handler.handler(_agent_compose_event("REQ-M1"))
    body = _body(resp)

    assert body["success"] is True, body
    assert body["data"]["mode"] == "NORMAL"
    crew_id = body["data"]["crew_id"]
    assert crew_id

    # --- Crew item written to the REAL table (status PROPOSED, source AGENT) ---
    crew_item = shared_db.get_item(shared_db.crew_pk(crew_id), "META")
    assert crew_item is not None
    assert crew_item["status"] == CrewStatus.PROPOSED
    assert crew_item["source"] == "AGENT"
    assert set(crew_item["member_ids"]) == {"MW1", "MW2"}  # office-scoped READY candidates
    assert crew_item["request_id"] == "REQ-M1"

    # --- WorkRequest transitioned to PROPOSED, read back from the REAL table (Req 8.2) ---
    req_item = shared_db.get_item(shared_db.request_pk("REQ-M1"), "META")
    assert req_item["status"] == RequestStatus.PROPOSED
    # And its GSI1SK was rewritten to the PROPOSED partition (the adapter's transition shape).
    assert req_item["GSI1SK"] == shared_db.request_gsi1sk(RequestStatus.PROPOSED, "REQ-M1")

    # --- office-scoped crew query (GSI1) finds exactly the one PROPOSED AGENT crew ---
    crews = shared_db.query_office_crews(OFFICE_ID, CrewStatus.PROPOSED)
    assert [c["crew_id"] for c in crews] == [crew_id]


# =========================================================================== #
# Direct adapter unit tests over the REAL single table                         #
# =========================================================================== #
def test_adapter_transition_request_status_true_on_match_false_on_stale(real_table):
    """transition_request_status: True on the expected state, False (no mutation) when stale."""
    _seed_requested_request("REQ-T1")

    # Correct expected state → conditional write succeeds and mutates.
    assert sg.transition_request_status("REQ-T1", RequestStatus.REQUESTED, RequestStatus.COMPOSING) is True
    assert shared_db.get_item(shared_db.request_pk("REQ-T1"), "META")["status"] == RequestStatus.COMPOSING

    # Stale expected state (still REQUESTED expected, but it's now COMPOSING) → conditional
    # check fails → False, and the item is not mutated (models STATE_CONFLICT).
    assert sg.transition_request_status("REQ-T1", RequestStatus.REQUESTED, RequestStatus.PROPOSED) is False
    assert shared_db.get_item(shared_db.request_pk("REQ-T1"), "META")["status"] == RequestStatus.COMPOSING


def test_adapter_save_crew_writes_retrievable_item_and_returns_id(real_table):
    """save_crew builds a canonical Crew via build_crew, persists it, and returns its id."""
    crew_id = sg.save_crew(
        office_id=OFFICE_ID,
        request_id="REQ-C1",
        member_ids=["MW1", "MW2"],
        rationale="비용 우선 편성",
        estimated_cost=310_000,
        source="AGENT",
    )
    assert crew_id
    item = shared_db.get_item(shared_db.crew_pk(crew_id), "META")
    assert item is not None
    assert item["status"] == CrewStatus.PROPOSED  # adapter default
    assert item["source"] == "AGENT"
    assert item["member_ids"] == ["MW1", "MW2"]
    assert int(item["estimated_cost"]) == 310_000
    # Canonical Crew schema has no gap/rank field — linkage lives in the response only.
    assert "gap_event_id" not in item
    assert "rank" not in item


def test_adapter_save_gap_event_and_transition_on_real_table(real_table):
    """save_gap_event writes a DETECTED GapEvent, and transition_gap_event_status advances it."""
    event_id = sg.save_gap_event(
        office_id=OFFICE_ID,
        crew_id="CREW-G1",
        request_id="REQ-G1",
        gap_type=GapType.NO_SHOW,
        missing_worker_ids=["C"],
    )
    assert event_id
    item = shared_db.get_item(shared_db.gap_pk(event_id), "META")
    assert item is not None
    assert item["status"] == GapStatus.DETECTED  # adapter default
    assert item["gap_type"] == "NO_SHOW"
    assert item["missing_worker_ids"] == ["C"]

    # DETECTED → RECOMPOSING succeeds; a stale DETECTED expected then fails.
    assert sg.transition_gap_event_status(event_id, GapStatus.DETECTED, GapStatus.RECOMPOSING) is True
    assert sg.transition_gap_event_status(event_id, GapStatus.DETECTED, GapStatus.PROPOSED) is False
    assert shared_db.get_item(shared_db.gap_pk(event_id), "META")["status"] == GapStatus.RECOMPOSING


def test_adapter_record_gap_recommendations_on_real_table(real_table):
    """record_gap_recommendations: option-1 EMERGENCY hand-off writes fixed_member_ids +
    recommendations AND transitions RECOMPOSING→PROPOSED in one conditional write."""
    event_id = sg.save_gap_event(
        office_id=OFFICE_ID,
        crew_id="CREW-R1",
        request_id="REQ-R1",
        gap_type=GapType.NO_SHOW,
        missing_worker_ids=["C"],
    )
    # gap_event pre-locks DETECTED → RECOMPOSING before the agent runs.
    assert sg.transition_gap_event_status(event_id, GapStatus.DETECTED, GapStatus.RECOMPOSING) is True

    recommendations = [
        {"replacement_member_ids": ["E"], "total_cost": 158_000, "reason": "결원 보충"},
    ]
    # From RECOMPOSING: record the recommendations + transition to PROPOSED (succeeds once).
    assert sg.record_gap_recommendations(
        event_id,
        fixed_member_ids=["A", "B"],
        recommendations=recommendations,
        expected=GapStatus.RECOMPOSING,
        target=GapStatus.PROPOSED,
    ) is True

    item = shared_db.get_item(shared_db.gap_pk(event_id), "META")
    assert item["status"] == GapStatus.PROPOSED
    assert item["fixed_member_ids"] == ["A", "B"]
    assert item["recommendations"][0]["replacement_member_ids"] == ["E"]
    assert int(item["recommendations"][0]["total_cost"]) == 158_000
    assert item["GSI1SK"] == shared_db.gap_gsi1sk(GapStatus.PROPOSED, event_id)

    # A stale expected (still RECOMPOSING, but it's now PROPOSED) → conditional check fails,
    # returns False, and neither the status nor the recommendations are mutated.
    assert sg.record_gap_recommendations(
        event_id,
        fixed_member_ids=["X"],
        recommendations=[{"replacement_member_ids": ["Z"], "total_cost": 1, "reason": "stale"}],
        expected=GapStatus.RECOMPOSING,
        target=GapStatus.FAILED,
    ) is False
    item_after = shared_db.get_item(shared_db.gap_pk(event_id), "META")
    assert item_after["status"] == GapStatus.PROPOSED
    assert item_after["fixed_member_ids"] == ["A", "B"]
