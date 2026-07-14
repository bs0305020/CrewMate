"""Unit tests for the agent_invoke assembler (task 5.1).

Covers the two public functions of ``backend/functions/agent_invoke/assembler.py``:

- :func:`assemble_normal_input` - NORMAL AgentInput assembly: office+READY candidate
  scoping (Req 6.3), work-request → RequestSpec mapping (Req 2.1-2.4), collaboration
  pairs, trade-narrowed READY query, and reads-only (no writes/transitions).
- :func:`build_validation_context` - the "검증 직전 최신 스냅샷" contract: worker state /
  wage / trade come from the FRESH ``get_workers`` read (not the stale agent-input pool),
  so the resulting context makes the READY / total_cost / no-conflict checks depend on
  current truth (Req 7.3, 7.6, 7.7).

These are example/unit tests (task 5.1 has no Correctness Property of its own). They use
the shared ``db`` stub installed under ``shared.*`` via the ``install_shared``
fixture (conftest.py), matching the lazy-import consumption pattern of the module.
"""
from __future__ import annotations

import pytest

from agent.schemas import AgentInput, Candidate, FixedMember, TradeRequirement
from functions.agent_invoke.assembler import (
    assemble_normal_input,
    build_validation_context,
)
from functions.agent_invoke.validator import validate_output


# --------------------------------------------------------------------------- #
# assemble_normal_input                                                        #
# --------------------------------------------------------------------------- #
def test_assemble_normal_input_scopes_candidates_and_maps_request(install_shared):
    """Only office-matched READY workers become candidates; request maps faithfully."""
    db = install_shared.db
    db.add_work_request(
        "REQ1",
        status="REQUESTED",
        office_id="OFFICE001",
        required_workers=[{"trade": "FORMWORK", "count": 2}],
        budget=1_000_000,
        priority={"cost": "HIGH", "skill": "MEDIUM", "teamwork": "LOW"},
        site="현장 A",
        work_date="2025-01-01",
        start_time="08:00",
    )
    # In-scope: office match + READY.
    db.add_worker("W1", office_id="OFFICE001", state="READY", trade="FORMWORK",
                  desired_daily_wage=150_000, skill_level=3, career_years=5,
                  certifications=["안전교육"])
    db.add_worker("W2", office_id="OFFICE001", state="READY", trade="FORMWORK",
                  desired_daily_wage=160_000, skill_level=4, career_years=8)
    # Out of scope: not READY (RUNNING) and different office.
    db.add_worker("W3", office_id="OFFICE001", state="RUNNING", trade="FORMWORK")
    db.add_worker("W4", office_id="OFFICE002", state="READY", trade="FORMWORK")
    db.add_collaboration("W1", "W2", 3)

    result = assemble_normal_input("REQ1", "OFFICE001")

    assert isinstance(result, AgentInput)
    assert result.mode == "NORMAL"
    assert result.fixed_members == []  # NORMAL never has fixed members
    # Candidate scope: only the office-matched READY workers.
    assert {c.worker_id for c in result.candidates} == {"W1", "W2"}
    # RequestSpec mapping (Req 2.1-2.4).
    assert result.request.request_id == "REQ1"
    assert result.request.budget == 1_000_000
    assert result.request.priority.cost == "HIGH"
    assert result.request.priority.teamwork == "LOW"
    assert result.request.site == "현장 A"
    assert [(t.trade, t.count) for t in result.request.required_workers] == [("FORMWORK", 2)]
    # Candidate field mapping.
    w1 = next(c for c in result.candidates if c.worker_id == "W1")
    assert (w1.trade, w1.skill_level, w1.desired_daily_wage) == ("FORMWORK", 3, 150_000)
    assert w1.certifications == ["안전교육"]
    # Collaboration pairs assembled from candidates.
    assert [(p.worker_a, p.worker_b, p.count) for p in result.collaboration_pairs] == [
        ("W1", "W2", 3)
    ]


def test_assemble_normal_input_narrows_ready_query_to_required_trades(install_shared):
    """The READY query is narrowed to the request's required trades."""
    db = install_shared.db
    db.add_work_request(
        "REQ2",
        office_id="OFFICE001",
        required_workers=[{"trade": "REBAR", "count": 1}, {"trade": "MASONRY", "count": 1}],
        budget=500_000,
        priority={"cost": "MEDIUM", "skill": "HIGH", "teamwork": "MEDIUM"},
        site="현장 B",
        work_date="2025-02-01",
        start_time="09:00",
    )
    db.add_worker("R1", office_id="OFFICE001", state="READY", trade="REBAR")

    assemble_normal_input("REQ2", "OFFICE001")

    ready_calls = db.method_calls("query_ready_workers")
    assert len(ready_calls) == 1
    assert ready_calls[0]["office_id"] == "OFFICE001"
    # Distinct required trades, first-appearance order.
    assert ready_calls[0]["trades"] == ["REBAR", "MASONRY"]


def test_assemble_normal_input_defaults_for_absent_optional_fields(install_shared):
    """A lean work-request record still assembles (documented fallbacks apply)."""
    db = install_shared.db
    # Only required_workers present; budget/priority/site/dates omitted.
    db.add_work_request(
        "REQ3",
        office_id="OFFICE001",
        required_workers=[{"trade": "GENERAL", "count": 1}],
    )

    result = assemble_normal_input("REQ3", "OFFICE001")

    assert result.request.budget > 0  # non-constraining default, satisfies gt=0
    assert result.request.priority.cost == "MEDIUM"  # neutral default
    assert result.request.priority.skill == "MEDIUM"
    assert result.request.priority.teamwork == "MEDIUM"
    assert result.request.site == ""
    assert result.candidates == []  # no workers seeded


def test_assemble_normal_input_reads_only_no_writes(install_shared):
    """Assembly must not save or transition anything (reads only, Req 6.4)."""
    db = install_shared.db
    db.add_work_request("REQ4", office_id="OFFICE001",
                        required_workers=[{"trade": "FORMWORK", "count": 1}], budget=1)
    db.add_worker("W1", office_id="OFFICE001", state="READY", trade="FORMWORK")

    assemble_normal_input("REQ4", "OFFICE001")

    assert db.saved_crews == []
    assert db.status_transitions == []
    assert db.gap_status_transitions == []


def test_assemble_normal_input_missing_request_raises(install_shared):
    """Defensive guard: a missing work request raises ValueError."""
    with pytest.raises(ValueError):
        assemble_normal_input("NOPE", "OFFICE001")


# --------------------------------------------------------------------------- #
# build_validation_context - freshest snapshot                                 #
# --------------------------------------------------------------------------- #
def test_build_validation_context_uses_fresh_snapshot_over_stale_pool(install_shared):
    """State + wage come from the FRESH get_workers read, overriding stale pool values."""
    db = install_shared.db
    # Fresh DB truth: READY, wage 175_000 (differs from the stale candidate below).
    db.add_worker("W1", state="READY", trade="FORMWORK",
                  desired_daily_wage=175_000, current_crew_id=None)
    # Stale agent-input pool: wage 100_000.
    stale_candidate = Candidate(worker_id="W1", trade="FORMWORK", skill_level=3,
                                desired_daily_wage=100_000, career_years=5)

    ctx = build_validation_context(
        ["W1", "W1"],  # duplicate across recommendations -> deduped into one read
        mode="NORMAL",
        candidates=[stale_candidate],
        fixed_members=[],
        required_workers=[TradeRequirement(trade="FORMWORK", count=1)],
    )

    # Fresh read happened, deduped to a single id.
    read_calls = db.method_calls("get_workers")
    assert len(read_calls) == 1
    assert read_calls[0]["worker_ids"] == ["W1"]
    # worker_states sourced entirely from the fresh snapshot.
    assert ctx.worker_states["W1"].state == "READY"
    # Fresh wage wins over the stale pool value (Property 5 depends on server truth).
    assert ctx.wage_by_worker["W1"] == 175_000


def test_build_validation_context_missing_worker_gets_no_snapshot(install_shared):
    """A recommended worker absent from get_workers gets no snapshot (fails closed)."""
    install_shared.db  # nothing seeded -> get_workers returns []
    candidate = Candidate(worker_id="GHOST", trade="FORMWORK", skill_level=1,
                          desired_daily_wage=120_000, career_years=0)

    ctx = build_validation_context(
        ["GHOST"],
        mode="NORMAL",
        candidates=[candidate],
        fixed_members=[],
        required_workers=[TradeRequirement(trade="FORMWORK", count=1)],
    )

    assert "GHOST" not in ctx.worker_states  # validator treats absent snapshot as MISSING
    # Provenance/fallback wage still available from the pool.
    assert ctx.wage_by_worker["GHOST"] == 120_000


def test_build_validation_context_emergency_passes_current_crew_and_fixed(install_shared):
    """EMERGENCY: current_crew_id passes through; fixed members are snapshotted fresh."""
    db = install_shared.db
    db.add_worker("FIX1", state="RUNNING", trade="FORMWORK",
                  desired_daily_wage=155_000, current_crew_id="CREW#1")
    db.add_worker("NEW1", state="READY", trade="REBAR",
                  desired_daily_wage=140_000, current_crew_id=None)
    fixed = [FixedMember(worker_id="FIX1", trade="FORMWORK", desired_daily_wage=155_000)]
    candidate = Candidate(worker_id="NEW1", trade="REBAR", skill_level=2,
                          desired_daily_wage=140_000, career_years=1)

    ctx = build_validation_context(
        ["FIX1", "NEW1"],
        mode="EMERGENCY",
        candidates=[candidate],
        fixed_members=fixed,
        required_workers=[TradeRequirement(trade="FORMWORK", count=1),
                          TradeRequirement(trade="REBAR", count=1)],
        current_crew_id="CREW#1",
    )

    assert ctx.mode == "EMERGENCY"
    assert ctx.current_crew_id == "CREW#1"
    # Fixed member is snapshotted from the fresh read (RUNNING in the target crew).
    assert ctx.worker_states["FIX1"].state == "RUNNING"
    assert ctx.worker_states["FIX1"].current_crew_id == "CREW#1"
    assert ctx.worker_states["NEW1"].state == "READY"


# --------------------------------------------------------------------------- #
# assemble -> build_validation_context -> validate_output (sanity path)         #
# --------------------------------------------------------------------------- #
def test_assembled_context_validates_a_compliant_output(install_shared):
    """End-to-end sanity: a compliant output validates against a freshly-built context."""
    from agent.schemas import AgentOutput, Recommendation

    db = install_shared.db
    db.add_work_request(
        "REQ9",
        office_id="OFFICE001",
        required_workers=[{"trade": "FORMWORK", "count": 2}],
        budget=1_000_000,
        priority={"cost": "HIGH", "skill": "MEDIUM", "teamwork": "MEDIUM"},
        site="현장 C",
        work_date="2025-03-01",
        start_time="07:30",
    )
    db.add_worker("W1", office_id="OFFICE001", state="READY", trade="FORMWORK",
                  desired_daily_wage=150_000, skill_level=3, career_years=5)
    db.add_worker("W2", office_id="OFFICE001", state="READY", trade="FORMWORK",
                  desired_daily_wage=160_000, skill_level=4, career_years=8)

    agent_input = assemble_normal_input("REQ9", "OFFICE001")

    # A rule-compliant recommendation: both READY FORMWORK candidates, correct total_cost.
    output = AgentOutput(
        mode="NORMAL",
        request_id="REQ9",
        recommendations=[
            Recommendation(
                rank=1,
                member_ids=["W1", "W2"],
                total_cost=310_000,  # 150_000 + 160_000 (server truth)
                reason="필요 직종 인원을 충족하는 팀 구성",
                considerations=["직종 인원 충족", "예산 내"],
            )
        ],
    )
    member_ids = [m for rec in output.recommendations for m in rec.member_ids]

    ctx = build_validation_context(
        member_ids,
        mode=agent_input.mode,
        candidates=agent_input.candidates,
        fixed_members=agent_input.fixed_members,
        required_workers=agent_input.request.required_workers,
    )
    result = validate_output(output, ctx)

    assert result.valid, result.failed_checks()
