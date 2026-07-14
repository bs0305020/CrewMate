"""Validator unit tests — the seven wrong-output kinds (task 3.10).

_Requirements: 7.2, 7.3, 7.4, 7.5, 7.6, 7.7, 7.8_

These are **example / unit tests** (plain pytest functions, no Hypothesis). They
complement the validator property tests (tasks 3.2–3.9, ``test_property_01..08``) with
small, hand-constructed, easy-to-read fixtures — one representative case per wrong-output
kind. Together they cover the PRD_B Day 2 completion criterion **"잘못된 출력 7종이 전부
검출됨"** (design.md → "Testing Strategy" → "단위 테스트가 다룰 대표 케이스").

Approach (design.md → "Testing Strategy" → "검증기 테스트 패턴")
--------------------------------------------------------------
Build one small, EXPLICIT, fully rule-compliant baseline (``_normal_baseline`` /
``_emergency_baseline``) and assert it validates ``True`` (a soundness / no-false-negative
anchor mirroring Property 8). Then derive seven focused cases, each applying **exactly one**
defect to that baseline and asserting (a) ``result.valid is False`` and (b) the SPECIFIC
check for that defect is among ``result.failed_checks()``:

  1. 미지 id (unknown member)            → ``member_exists``            (Req 7.2)
  2. 비READY (new member not READY)      → ``new_ready``               (Req 7.3)
  3. 중복 (duplicate worker_id)          → ``no_dup``                  (Req 7.4)
  4. 직종·인원 미충족 (headcount short)  → ``trade_headcount``         (Req 7.5)
  5. 비용 불일치 (wrong total_cost)      → ``total_cost``              (Req 7.6)
  6. 타배정 충돌 (RUNNING elsewhere)     → ``no_conflict_assignment``  (Req 7.7)
  7. fixed_members 훼손 (EMERGENCY)      → ``fixed_preserved``         (Req 7.8)

Sole-failure vs. membership (documented)
----------------------------------------
Where a defect can be expressed without disturbing the other six checks, the case asserts
the failing check is the *sole* failure (``== [CHECK]``) for a sharp, unambiguous mapping:
cases 2, 3, 4, 5 and 7 do this. Two defects are inherently coupled to a second check and so
assert membership (``in``) instead:

  * Case 1 (unknown id) — an id absent from candidates/fixed has no trade, wage, or state
    snapshot, so ``trade_headcount`` / ``total_cost`` / ``new_ready`` co-fail. The
    load-bearing guarantee is that ``member_exists`` fires.
  * Case 6 (conflicting assignment) — a worker RUNNING/RESERVED in another crew is, by
    definition, not READY, so ``new_ready`` co-fails. The failing set is therefore exactly
    the two snapshot-reading checks ``{new_ready, no_conflict_assignment}``.

Every baseline uses a single required trade (FORMWORK×2) and hand-picked wages so each
server-computed wage sum is trivial to verify by eye.
"""
from __future__ import annotations

from agent.schemas import (
    AgentOutput,
    Candidate,
    FixedMember,
    Recommendation,
    TradeRequirement,
)
from functions.agent_invoke.validator import (
    CHECK_FIXED_PRESERVED,
    CHECK_MEMBER_EXISTS,
    CHECK_NEW_READY,
    CHECK_NO_CONFLICT_ASSIGNMENT,
    CHECK_NO_DUP,
    CHECK_TOTAL_COST,
    CHECK_TRADE_HEADCOUNT,
    ValidationContext,
    WorkerStateSnapshot,
    validate_output,
)

# Explicit, hand-picked wages so every server-computed sum in this file is easy to verify.
_W1_WAGE = 150_000
_W2_WAGE = 180_000
_FIX_WAGE = 200_000

_CREW_CURRENT = "CREW-CURRENT"  # EMERGENCY re-composition target crew (exempt from conflict)
_CREW_OTHER = "CREW-OTHER"  # a genuinely different crew — the source of a conflict


def _formwork_candidate(worker_id: str, wage: int) -> Candidate:
    """A FORMWORK candidate with fixed, explicit attributes (READY-eligible)."""
    return Candidate(
        worker_id=worker_id,
        trade="FORMWORK",
        skill_level=3,
        desired_daily_wage=wage,
        certifications=[],
        career_years=5,
    )


def _ready(worker_id: str) -> WorkerStateSnapshot:
    """Freshest snapshot for a READY, unassigned candidate (no current crew)."""
    return WorkerStateSnapshot(worker_id=worker_id, state="READY", current_crew_id=None)


def _normal_baseline():
    """A minimal, fully rule-compliant NORMAL scenario.

    Request: 2 FORMWORK workers. Candidates W1/W2 are READY. The single recommendation
    picks both, with ``total_cost`` equal to the true wage sum. Returns ``(output, ctx)``.
    """
    required = [TradeRequirement(trade="FORMWORK", count=2)]
    w1 = _formwork_candidate("W1", _W1_WAGE)
    w2 = _formwork_candidate("W2", _W2_WAGE)
    rec = Recommendation(
        rank=1,
        member_ids=["W1", "W2"],
        total_cost=_W1_WAGE + _W2_WAGE,
        reason="형틀(FORMWORK) 2인 구성, 예산 내 편성",
        considerations=["직종 균형", "협업 이력"],
    )
    output = AgentOutput(mode="NORMAL", request_id="REQ-001", recommendations=[rec])
    ctx = ValidationContext.build(
        mode="NORMAL",
        candidates=[w1, w2],
        fixed_members=[],
        required_workers=required,
        worker_states={"W1": _ready("W1"), "W2": _ready("W2")},
        current_crew_id=None,
    )
    return output, ctx


def _emergency_baseline():
    """A minimal, fully rule-compliant EMERGENCY scenario with one fixed member.

    Request: 2 FORMWORK workers. FIX1 is the retained RUNNING fixed member (in the current
    crew); W1 fills the one remaining slot. W2 is a spare READY candidate used by the
    'replace' defect (case 7). The single recommendation is ``[FIX1, W1]`` with
    ``total_cost`` equal to the true wage sum. Returns ``(output, ctx)``.
    """
    required = [TradeRequirement(trade="FORMWORK", count=2)]
    fix1 = FixedMember(worker_id="FIX1", trade="FORMWORK", desired_daily_wage=_FIX_WAGE)
    w1 = _formwork_candidate("W1", _W1_WAGE)
    w2 = _formwork_candidate("W2", _W2_WAGE)  # spare candidate for the replace defect
    rec = Recommendation(
        rank=1,
        member_ids=["FIX1", "W1"],
        total_cost=_FIX_WAGE + _W1_WAGE,
        reason="기존 팀원(FIX1) 유지 + 결원 1인(W1) 보충",
        considerations=["fixed_members 유지"],
    )
    output = AgentOutput(mode="EMERGENCY", request_id="REQ-900", recommendations=[rec])
    ctx = ValidationContext.build(
        mode="EMERGENCY",
        candidates=[w1, w2],
        fixed_members=[fix1],
        required_workers=required,
        worker_states={
            # Fixed member keeps its RUNNING state in the current (re-composition) crew.
            "FIX1": WorkerStateSnapshot(
                worker_id="FIX1", state="RUNNING", current_crew_id=_CREW_CURRENT
            ),
            "W1": _ready("W1"),
            "W2": _ready("W2"),
        },
        current_crew_id=_CREW_CURRENT,
    )
    return output, ctx


# --------------------------------------------------------------------------- #
# Soundness anchors — the baselines must validate (no false negatives).        #
# --------------------------------------------------------------------------- #
def test_valid_baseline_normal_passes():
    """A fully compliant NORMAL output validates True with no failed checks."""
    output, ctx = _normal_baseline()
    result = validate_output(output, ctx)
    assert result.valid is True
    assert result.failed_checks() == []


def test_valid_baseline_emergency_passes():
    """A fully compliant EMERGENCY output (fixed member preserved) validates True."""
    output, ctx = _emergency_baseline()
    result = validate_output(output, ctx)
    assert result.valid is True
    assert result.failed_checks() == []


# --------------------------------------------------------------------------- #
# 1. 미지 id — an id absent from candidates AND fixed_members. (Req 7.2)         #
# --------------------------------------------------------------------------- #
def test_unknown_member_id_detected_by_member_exists():
    """Replacing a member with an unknown id is caught by ``member_exists``.

    ``GHOST`` exists in neither candidates nor fixed_members, so it also has no trade,
    wage, or state snapshot — ``trade_headcount`` / ``total_cost`` / ``new_ready`` co-fail
    as an unavoidable side effect. The load-bearing assertion is that ``member_exists``
    detected the provenance violation.
    """
    output, ctx = _normal_baseline()
    assert validate_output(output, ctx).valid is True  # baseline anchor

    defective = output.model_copy(deep=True)
    defective.recommendations[0] = output.recommendations[0].model_copy(
        update={"member_ids": ["W1", "GHOST"]}
    )

    result = validate_output(defective, ctx)
    assert result.valid is False
    assert CHECK_MEMBER_EXISTS in result.failed_checks()


# --------------------------------------------------------------------------- #
# 2. 비READY — a new member whose freshest snapshot is not READY. (Req 7.3)     #
# --------------------------------------------------------------------------- #
def test_new_member_not_ready_detected_by_new_ready():
    """A new member that is INACTIVE (not READY) is caught by ``new_ready``.

    INACTIVE is not an assignment state (RESERVED/RUNNING), so the conflict check stays
    satisfied and ``new_ready`` is the sole failure.
    """
    output, ctx = _normal_baseline()
    assert validate_output(output, ctx).valid is True  # baseline anchor

    states = dict(ctx.worker_states)
    states["W2"] = WorkerStateSnapshot(
        worker_id="W2", state="INACTIVE", current_crew_id=None
    )
    defective_ctx = ctx.model_copy(update={"worker_states": states})

    result = validate_output(output, defective_ctx)
    assert result.valid is False
    assert result.failed_checks() == [CHECK_NEW_READY]


# --------------------------------------------------------------------------- #
# 3. 중복 — the same worker_id twice in one recommendation. (Req 7.4)           #
# --------------------------------------------------------------------------- #
def test_duplicate_member_detected_by_no_dup():
    """Listing W1 twice is caught by ``no_dup``.

    Two FORMWORK slots are still filled (the duplicate tallies as two FORMWORK) and
    ``total_cost`` is recomputed for ``[W1, W1]``, so ``no_dup`` is the sole failure.
    """
    output, ctx = _normal_baseline()
    assert validate_output(output, ctx).valid is True  # baseline anchor

    defective = output.model_copy(deep=True)
    defective.recommendations[0] = output.recommendations[0].model_copy(
        update={"member_ids": ["W1", "W1"], "total_cost": _W1_WAGE + _W1_WAGE}
    )

    result = validate_output(defective, ctx)
    assert result.valid is False
    assert result.failed_checks() == [CHECK_NO_DUP]


# --------------------------------------------------------------------------- #
# 4. 직종·인원 미충족 — a required trade left one short. (Req 7.5)               #
# --------------------------------------------------------------------------- #
def test_trade_headcount_shortfall_detected_by_trade_headcount():
    """Only one FORMWORK worker where two are required is caught by ``trade_headcount``.

    ``total_cost`` is recomputed for the shortened crew so it stays consistent, leaving
    ``trade_headcount`` as the sole failure.
    """
    output, ctx = _normal_baseline()
    assert validate_output(output, ctx).valid is True  # baseline anchor

    defective = output.model_copy(deep=True)
    defective.recommendations[0] = output.recommendations[0].model_copy(
        update={"member_ids": ["W1"], "total_cost": _W1_WAGE}
    )

    result = validate_output(defective, ctx)
    assert result.valid is False
    assert result.failed_checks() == [CHECK_TRADE_HEADCOUNT]


# --------------------------------------------------------------------------- #
# 5. 비용 불일치 — total_cost != server-computed wage sum. (Req 7.6)            #
# --------------------------------------------------------------------------- #
def test_total_cost_mismatch_detected_by_total_cost():
    """A total_cost off the true wage sum is caught by ``total_cost``.

    ``member_ids`` are untouched (every member still has a known wage), so ``total_cost``
    is the sole failure.
    """
    output, ctx = _normal_baseline()
    assert validate_output(output, ctx).valid is True  # baseline anchor

    wrong_cost = _W1_WAGE + _W2_WAGE + 50_000  # 50,000 more than the true sum
    defective = output.model_copy(deep=True)
    defective.recommendations[0] = output.recommendations[0].model_copy(
        update={"total_cost": wrong_cost}
    )

    result = validate_output(defective, ctx)
    assert result.valid is False
    assert result.failed_checks() == [CHECK_TOTAL_COST]


# --------------------------------------------------------------------------- #
# 6. 타배정 충돌 — a new member RUNNING/RESERVED in another crew. (Req 7.7)      #
# --------------------------------------------------------------------------- #
def test_conflicting_assignment_detected_by_no_conflict():
    """A new member RUNNING in a DIFFERENT crew is caught by ``no_conflict_assignment``.

    A conflicting worker is RUNNING/RESERVED, hence not READY, so ``new_ready`` co-fails by
    construction. These are exactly the two snapshot-reading checks; the failing set is
    ``{new_ready, no_conflict_assignment}`` and the other five checks stay undisturbed.
    """
    output, ctx = _normal_baseline()
    assert validate_output(output, ctx).valid is True  # baseline anchor

    states = dict(ctx.worker_states)
    states["W2"] = WorkerStateSnapshot(
        worker_id="W2", state="RUNNING", current_crew_id=_CREW_OTHER
    )
    defective_ctx = ctx.model_copy(update={"worker_states": states})

    result = validate_output(output, defective_ctx)
    assert result.valid is False
    assert CHECK_NO_CONFLICT_ASSIGNMENT in result.failed_checks()
    assert set(result.failed_checks()) == {CHECK_NEW_READY, CHECK_NO_CONFLICT_ASSIGNMENT}


# --------------------------------------------------------------------------- #
# 7. fixed_members 훼손 — EMERGENCY recommendation drops/replaces a fixed       #
#    member. (Req 7.8)                                                          #
# --------------------------------------------------------------------------- #
def test_fixed_member_replaced_detected_by_fixed_preserved():
    """Replacing fixed member FIX1 with spare candidate W2 is caught by ``fixed_preserved``.

    Two FORMWORK slots are still filled by READY workers and ``total_cost`` is recomputed
    for ``[W2, W1]``, so every other check still passes and ``fixed_preserved`` is the sole
    failure — proving EMERGENCY fixed-member preservation is enforced.
    """
    output, ctx = _emergency_baseline()
    assert validate_output(output, ctx).valid is True  # baseline anchor

    defective = output.model_copy(deep=True)
    defective.recommendations[0] = output.recommendations[0].model_copy(
        update={"member_ids": ["W2", "W1"], "total_cost": _W2_WAGE + _W1_WAGE}
    )

    result = validate_output(defective, ctx)
    assert result.valid is False
    assert result.failed_checks() == [CHECK_FIXED_PRESERVED]
