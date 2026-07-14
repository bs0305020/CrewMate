"""Server-side code validator for Crew Composition Agent output (담당자 B).

The Agent (LLM) is *never trusted*: its output is accepted for storage only after it
passes all seven code checks defined here. This module implements those checks as a
**pure function** (:func:`validate_output`) plus the Pydantic context/result models it
operates on. All state the checks need (worker state snapshots, trade/wage maps, the
re-composition target crew id) is injected via :class:`ValidationContext`; the validator
performs **no I/O and no DB calls**. The freshest-snapshot assembly that produces the
context is the handler/assembler's job (task 5.1), keeping this module deterministic and
suitable for property-based testing.

Design references
-----------------
- ``design.md`` → "Data Models" → "검증 컨텍스트 & 결과" (model shapes + check names).
- ``design.md`` → "Correctness Properties" 1~8.
- ``requirements.md`` → Requirement 7 (Agent 출력 코드 검증) and 1.4 / 1.5 / 1.6 / 1.8.

The seven checks (names MUST match design.md)
--------------------------------------------
1. ``member_exists``          - every member_id exists in candidates OR fixed_members.
                                (Property 1 / Req 7.2, 1.6)
2. ``new_ready``              - every NEW (non-fixed) recommended worker is READY in the
                                injected snapshot. (Property 2 / Req 7.3, 1.8)
3. ``no_dup``                 - no duplicate worker_id within a single recommendation.
                                (Property 3 / Req 7.4)
4. ``trade_headcount``        - 1..3 recommendations AND each recommendation's members
                                (fixed + new) exactly satisfy the FULL required trade
                                headcount - no shortfall, no excess.
                                (Property 4 / Req 7.5, 1.4)
5. ``total_cost``             - total_cost equals the server-computed sum of the
                                recommended workers' desired_daily_wage.
                                (Property 5 / Req 7.6)
6. ``no_conflict_assignment`` - no NEW recommended worker is in another RUNNING/RESERVED
                                assignment, except the current re-composition target crew
                                and except EMERGENCY fixed_members. (Property 6 / Req 7.7)
7. ``fixed_preserved``        - in EMERGENCY, every recommendation includes ALL
                                fixed_members unchanged. (Property 7 / Req 7.8, 1.5, 1.3)

Soundness (Property 8)
----------------------
A fully rule-compliant output paired with a correctly assembled context is accepted
(``valid=True``). This validator is *not* degenerate: it collects a per-check result and
sets ``valid`` only from ``all(check.passed)``, so a compliant output passes every check.

Context construction contract
-----------------------------
- ``worker_states`` MUST contain a snapshot for every *newly recommended* (non-fixed)
  worker_id. ``new_ready`` fails closed when a new member's snapshot is missing (it cannot
  confirm READY), so an incomplete context would (correctly) be rejected.
- ``required_workers`` is the **FULL** required trade/headcount the recommendation must
  satisfy. In EMERGENCY the recommendation is ``fixed_members + new members`` and their
  combined trades must equal this full requirement (remaining + filled = full).
- ``trade_by_worker`` / ``wage_by_worker`` cover BOTH candidates and fixed_members.

Python 3.9 note
---------------
``from __future__ import annotations`` keeps annotations lazy so the design's builtin
generics (``dict[str, ...]`` / ``list[...]``, PEP 585) resolve on the local Python 3.9
runtime. The design's PEP 604 unions (``str | None``) are written here as
``Optional[str]`` because Pydantic v2 on Python 3.9 cannot evaluate the ``X | None``
form at model-build time (it would require the ``eval_type_backport`` package). This
mirrors the pattern already used in ``agent/schemas.py``. Models are declared in
dependency order.
"""
from __future__ import annotations

from collections import Counter
from typing import Literal, Optional

from pydantic import BaseModel

from agent.schemas import AgentOutput, Candidate, FixedMember, TradeRequirement

__all__ = [
    "WorkerStateSnapshot",
    "ValidationContext",
    "CheckResult",
    "ValidationResult",
    "validate_output",
    "CHECK_MEMBER_EXISTS",
    "CHECK_NEW_READY",
    "CHECK_NO_DUP",
    "CHECK_TRADE_HEADCOUNT",
    "CHECK_TOTAL_COST",
    "CHECK_NO_CONFLICT_ASSIGNMENT",
    "CHECK_FIXED_PRESERVED",
    "CHECK_NAMES",
]

# Worker-state literals used by the checks. Kept local (not imported from 담당자 A's
# shared/state) so the validator stays pure and dependency-light; values mirror the
# shared state contract (READY / RESERVED / RUNNING).
_READY = "READY"
_RESERVED = "RESERVED"
_RUNNING = "RUNNING"
_ASSIGNED_STATES = (_RESERVED, _RUNNING)

# Canonical check names - MUST match design.md "검증 컨텍스트 & 결과".
CHECK_MEMBER_EXISTS = "member_exists"
CHECK_NEW_READY = "new_ready"
CHECK_NO_DUP = "no_dup"
CHECK_TRADE_HEADCOUNT = "trade_headcount"
CHECK_TOTAL_COST = "total_cost"
CHECK_NO_CONFLICT_ASSIGNMENT = "no_conflict_assignment"
CHECK_FIXED_PRESERVED = "fixed_preserved"

CHECK_NAMES = (
    CHECK_MEMBER_EXISTS,
    CHECK_NEW_READY,
    CHECK_NO_DUP,
    CHECK_TRADE_HEADCOUNT,
    CHECK_TOTAL_COST,
    CHECK_NO_CONFLICT_ASSIGNMENT,
    CHECK_FIXED_PRESERVED,
)


# --------------------------------------------------------------------------- #
# Context & result models (design.md → "검증 컨텍스트 & 결과")                   #
# --------------------------------------------------------------------------- #
class WorkerStateSnapshot(BaseModel):
    """A freshest-available snapshot of one recommended worker's assignment state.

    ``state`` is the worker's status (READY/RESERVED/RUNNING/...). ``current_crew_id`` is
    the crew the worker is currently attached to (if any); it lets ``no_conflict_assignment``
    exempt the re-composition target crew.
    """

    worker_id: str
    state: str
    current_crew_id: Optional[str] = None


class ValidationContext(BaseModel):
    """Pure inputs the seven checks operate on - assembled by the caller before validation.

    See the module docstring's "Context construction contract" for the invariants callers
    must honour (snapshot coverage, FULL required_workers, combined trade/wage maps).
    """

    mode: Literal["NORMAL", "EMERGENCY"]
    candidates: list[Candidate] = []
    fixed_members: list[FixedMember] = []
    required_workers: list[TradeRequirement] = []
    worker_states: dict[str, WorkerStateSnapshot] = {}
    current_crew_id: Optional[str] = None  # EMERGENCY 재편성 대상 crew (check 6 예외)
    trade_by_worker: dict[str, str] = {}  # worker_id → trade (candidates + fixed 통합)
    wage_by_worker: dict[str, int] = {}  # worker_id → desired_daily_wage

    @classmethod
    def build(
        cls,
        *,
        mode: str,
        candidates: list[Candidate],
        fixed_members: list[FixedMember],
        required_workers: list[TradeRequirement],
        worker_states: dict[str, WorkerStateSnapshot],
        current_crew_id: Optional[str] = None,
    ) -> "ValidationContext":
        """Assemble a context, deriving ``trade_by_worker`` / ``wage_by_worker`` from the
        candidate and fixed-member pools (pure convenience for callers and tests).

        ``worker_states`` is supplied by the caller (task 5.1 sources it from the freshest
        DB snapshot). Fixed members override candidates on ``worker_id`` collisions.
        """
        trade_by_worker: dict[str, str] = {}
        wage_by_worker: dict[str, int] = {}
        for c in candidates:
            trade_by_worker[c.worker_id] = c.trade
            wage_by_worker[c.worker_id] = c.desired_daily_wage
        for f in fixed_members:
            trade_by_worker[f.worker_id] = f.trade
            wage_by_worker[f.worker_id] = f.desired_daily_wage
        return cls(
            mode=mode,
            candidates=list(candidates),
            fixed_members=list(fixed_members),
            required_workers=list(required_workers),
            worker_states=dict(worker_states),
            current_crew_id=current_crew_id,
            trade_by_worker=trade_by_worker,
            wage_by_worker=wage_by_worker,
        )


class CheckResult(BaseModel):
    """Outcome of a single named check (kept for every check, pass or fail)."""

    check: str
    passed: bool
    detail: str = ""


class ValidationResult(BaseModel):
    """Aggregate result. ``valid`` is true only when every check passed."""

    valid: bool
    checks: list[CheckResult]

    def failed_checks(self) -> list[str]:
        """Names of the checks that failed (handy for observability logging)."""
        return [c.check for c in self.checks if not c.passed]


# --------------------------------------------------------------------------- #
# Individual checks - each returns a CheckResult (never raises).               #
# --------------------------------------------------------------------------- #
def _truncate(items: list, limit: int = 5) -> str:
    """Render a short, bounded preview of offending items for a detail message."""
    shown = items[:limit]
    suffix = "" if len(items) <= limit else f" (+{len(items) - limit} more)"
    return ", ".join(str(i) for i in shown) + suffix


def _check_member_exists(output: AgentOutput, ctx: ValidationContext) -> CheckResult:
    """Property 1 / Req 7.2, 1.6: every member_id must come from candidates or fixed."""
    known = {c.worker_id for c in ctx.candidates} | {f.worker_id for f in ctx.fixed_members}
    unknown = [
        f"rank {rec.rank}:{mid}"
        for rec in output.recommendations
        for mid in rec.member_ids
        if mid not in known
    ]
    passed = not unknown
    detail = "" if passed else f"member_id(s) absent from candidates/fixed_members: {_truncate(unknown)}"
    return CheckResult(check=CHECK_MEMBER_EXISTS, passed=passed, detail=detail)


def _check_new_ready(output: AgentOutput, ctx: ValidationContext) -> CheckResult:
    """Property 2 / Req 7.3, 1.8: every NEW (non-fixed) member must be READY.

    Fails closed when a new member's snapshot is missing (cannot confirm READY).
    """
    fixed_ids = {f.worker_id for f in ctx.fixed_members}
    offenders: list[str] = []
    for rec in output.recommendations:
        for mid in rec.member_ids:
            if mid in fixed_ids:
                continue  # fixed members keep their RUNNING state - not "new"
            snap = ctx.worker_states.get(mid)
            state = snap.state if snap is not None else "MISSING"
            if state != _READY:
                offenders.append(f"rank {rec.rank}:{mid}={state}")
    passed = not offenders
    detail = "" if passed else f"new member(s) not READY: {_truncate(offenders)}"
    return CheckResult(check=CHECK_NEW_READY, passed=passed, detail=detail)


def _check_no_dup(output: AgentOutput, ctx: ValidationContext) -> CheckResult:
    """Property 3 / Req 7.4: no duplicate worker_id within one recommendation."""
    offenders: list[str] = []
    for rec in output.recommendations:
        seen: set[str] = set()
        dups: set[str] = set()
        for mid in rec.member_ids:
            if mid in seen:
                dups.add(mid)
            seen.add(mid)
        if dups:
            offenders.append(f"rank {rec.rank}:{sorted(dups)}")
    passed = not offenders
    detail = "" if passed else f"duplicate member_id(s): {_truncate(offenders)}"
    return CheckResult(check=CHECK_NO_DUP, passed=passed, detail=detail)


def _check_trade_headcount(output: AgentOutput, ctx: ValidationContext) -> CheckResult:
    """Property 4 / Req 7.5, 1.4: 1..3 recommendations AND exact per-trade headcount.

    Each recommendation's members (fixed + new) are tallied by trade and must exactly
    equal the FULL requirement - shortfall, excess, or an unknown trade all fail.
    """
    required: Counter = Counter()
    for tr in ctx.required_workers:
        required[tr.trade] += tr.count

    problems: list[str] = []
    n = len(output.recommendations)
    if not 1 <= n <= 3:
        problems.append(f"recommendation count {n} not in 1..3")

    for rec in output.recommendations:
        actual: Counter = Counter()
        for mid in rec.member_ids:
            actual[ctx.trade_by_worker.get(mid)] += 1  # None key if trade unknown
        if dict(actual) != dict(required):
            problems.append(f"rank {rec.rank}: {dict(actual)} != required {dict(required)}")

    passed = not problems
    detail = "" if passed else f"trade/headcount mismatch: {_truncate(problems)}"
    return CheckResult(check=CHECK_TRADE_HEADCOUNT, passed=passed, detail=detail)


def _check_total_cost(output: AgentOutput, ctx: ValidationContext) -> CheckResult:
    """Property 5 / Req 7.6: total_cost equals the server-computed wage sum."""
    problems: list[str] = []
    for rec in output.recommendations:
        missing = [m for m in rec.member_ids if m not in ctx.wage_by_worker]
        if missing:
            problems.append(f"rank {rec.rank}: no wage for {_truncate(missing)}")
            continue
        server_sum = sum(ctx.wage_by_worker[m] for m in rec.member_ids)
        if rec.total_cost != server_sum:
            problems.append(f"rank {rec.rank}: total_cost {rec.total_cost} != {server_sum}")
    passed = not problems
    detail = "" if passed else f"total_cost mismatch: {_truncate(problems)}"
    return CheckResult(check=CHECK_TOTAL_COST, passed=passed, detail=detail)


def _check_no_conflict_assignment(output: AgentOutput, ctx: ValidationContext) -> CheckResult:
    """Property 6 / Req 7.7: no NEW member is RUNNING/RESERVED in another crew.

    The current re-composition target crew (``ctx.current_crew_id``) is exempt, and
    EMERGENCY ``fixed_members`` (who stay RUNNING in the current crew) are exempt.
    """
    fixed_ids = {f.worker_id for f in ctx.fixed_members}
    offenders: list[str] = []
    for rec in output.recommendations:
        for mid in rec.member_ids:
            if mid in fixed_ids:
                continue  # fixed members are expected to be RUNNING in the current crew
            snap = ctx.worker_states.get(mid)
            if snap is None:
                continue  # cannot prove a conflict; new_ready already covers missing/non-READY
            if snap.state in _ASSIGNED_STATES and snap.current_crew_id != ctx.current_crew_id:
                offenders.append(f"rank {rec.rank}:{mid}={snap.state}@{snap.current_crew_id}")
    passed = not offenders
    detail = "" if passed else f"conflicting RUNNING/RESERVED assignment(s): {_truncate(offenders)}"
    return CheckResult(check=CHECK_NO_CONFLICT_ASSIGNMENT, passed=passed, detail=detail)


def _check_fixed_preserved(output: AgentOutput, ctx: ValidationContext) -> CheckResult:
    """Property 7 / Req 7.8, 1.5, 1.3: EMERGENCY keeps every fixed member in every rec."""
    if ctx.mode != "EMERGENCY":
        return CheckResult(
            check=CHECK_FIXED_PRESERVED,
            passed=True,
            detail="not applicable (mode != EMERGENCY)",
        )
    fixed_ids = {f.worker_id for f in ctx.fixed_members}
    offenders: list[str] = []
    for rec in output.recommendations:
        missing = fixed_ids - set(rec.member_ids)
        if missing:
            offenders.append(f"rank {rec.rank}: missing {sorted(missing)}")
    passed = not offenders
    detail = "" if passed else f"fixed_members not preserved: {_truncate(offenders)}"
    return CheckResult(check=CHECK_FIXED_PRESERVED, passed=passed, detail=detail)


# --------------------------------------------------------------------------- #
# Public entry point                                                          #
# --------------------------------------------------------------------------- #
def validate_output(output: AgentOutput, ctx: ValidationContext) -> ValidationResult:
    """Run all seven checks on ``output`` against ``ctx`` and aggregate the result.

    Pure function: no I/O, no DB access. Every check runs (so callers/logs see all
    failures, not just the first), and ``valid`` is true only when every check passes.
    """
    checks = [
        _check_member_exists(output, ctx),
        _check_new_ready(output, ctx),
        _check_no_dup(output, ctx),
        _check_trade_headcount(output, ctx),
        _check_total_cost(output, ctx),
        _check_no_conflict_assignment(output, ctx),
        _check_fixed_preserved(output, ctx),
    ]
    return ValidationResult(valid=all(c.passed for c in checks), checks=checks)
