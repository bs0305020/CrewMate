"""Deterministic local demo-fallback composer for the agent_invoke Lambda (담당자 B).

Design references
-----------------
- ``design.md`` -> "Components and Interfaces" -> "4. Agent Invoke Lambda"
  (``fallback.py``) and "Data Models" -> "Demo Fallback 응답".
- ``design.md`` -> "Correctness Properties" -> Property 13 (폴백 산출물의 유효성).
- ``requirements.md`` -> Requirement 9.3 / 9.4 (재시도 및 Bedrock 폴백).

Purpose
-------
When Bedrock is unavailable / times out and the fallback flag is enabled, the invoke
Lambda substitutes the LLM with :func:`demo_fallback`: a pure, deterministic local
composer that builds crew recommendations directly from the already-assembled
``AgentInput``. There is no LLM, no Bedrock, and no network / DB I/O.

The output is engineered so that, given sufficient candidates, it always passes the
same server-side ``validate_output`` checks (Correctness Properties 1-7). See Property
13 -- task 6.2 exercises this function against the real validator.

Composition strategy (deterministic)
------------------------------------
- For every required trade, candidates are ordered *cost-first* (lowest
  ``desired_daily_wage`` first) and the exact required headcount is filled.
- EMERGENCY: every ``fixed_members`` worker is retained in every recommendation, and
  only the remaining shortage per trade is filled from candidates. Fixed members are
  never duplicated and count toward the trade headcount (only ``count - fixed`` new
  workers are drawn per trade).
- ``total_cost`` is the exact server-style sum of every member's ``desired_daily_wage``
  (fixed + newly picked), matching the validator's computed sum (Property 5).
- Determinism: a fixed seed (``SEED = 42``) drives tie-breaking so the same input
  always yields the same output. Cost is the primary ordering key; ties break under the
  seed, with ``worker_id`` as the final stable key. Alternative recommendations are
  produced by rotating the per-trade candidate window (cheapest crew ranked first).

Reason / considerations text is work-focused and neutral (Requirement 3): no negative
worker evaluations, no probability figures, and no optimality-guarantee wording.

Python 3.9 note: ``from __future__ import annotations`` keeps annotations lazy so the
builtin-generic style resolves on the local Python 3.9 runtime.
"""
from __future__ import annotations

import random
from collections import defaultdict
from typing import Dict, List, Sequence, Set

from agent.schemas import (
    AgentInput,
    AgentOutput,
    Candidate,
    FixedMember,
    Recommendation,
)

__all__ = ["demo_fallback", "SEED"]

# Fixed seed for deterministic tie-breaking -> stable demo output (Req 9.3 / 9.4).
SEED = 42

# The validator accepts 1..3 recommendations (Property 4); the composer emits up to this
# many *distinct* alternatives when candidate supply allows, cheapest ranked first.
_MAX_RECOMMENDATIONS = 3


def demo_fallback(agent_input: AgentInput) -> AgentOutput:
    """Compose crew recommendations deterministically, with no LLM / Bedrock call.

    For each required trade the exact headcount is filled cost-first from the assembled
    candidates. In EMERGENCY mode every ``fixed_members`` worker is kept in every
    recommendation and only the per-trade shortage is drawn from candidates. The result
    is built to satisfy the seven server-side validation checks whenever candidate supply
    is sufficient (Property 13); when supply is short the composer still returns a
    well-formed :class:`AgentOutput` on a best-effort basis (validation would then reject
    it upstream, which is the correct degraded behaviour).

    The function is pure and deterministic: the same ``AgentInput`` always yields the
    same ``AgentOutput``.
    """
    fixed_members = list(agent_input.fixed_members)
    fixed_ids = {fm.worker_id for fm in fixed_members}
    fixed_member_ids = [fm.worker_id for fm in fixed_members]

    wage_by_worker = _wage_lookup(agent_input.candidates, fixed_members)
    required_by_trade = _aggregate_required(agent_input)
    fixed_count_by_trade = _count_by_trade(fixed_members)

    # Shortage to fill from candidates per trade (fixed members already cover part of the
    # headcount in EMERGENCY; never negative).
    remaining_by_trade: Dict[str, int] = {
        trade: max(0, count - fixed_count_by_trade.get(trade, 0))
        for trade, count in required_by_trade.items()
    }

    candidates_by_trade = _candidates_by_trade(agent_input.candidates, fixed_ids)

    # Build up to _MAX_RECOMMENDATIONS distinct candidate crews by rotating the per-trade
    # window, then rank them cheapest-first.
    seen: Set[frozenset] = set()
    crews: List[List[str]] = []
    for offset in range(_MAX_RECOMMENDATIONS):
        # Start from all fixed members (EMERGENCY); empty in NORMAL. Property 7 keeps
        # every fixed member in every recommendation.
        member_ids = list(fixed_member_ids)
        for trade in sorted(required_by_trade):
            need = remaining_by_trade.get(trade, 0)
            picks = _pick_window(candidates_by_trade.get(trade, []), need, offset)
            member_ids.extend(c.worker_id for c in picks)

        # Skip crews identical to one already collected (e.g. when every trade has exactly
        # enough candidates all rotations coincide). Guarantees 1..3 distinct outputs.
        key = frozenset(member_ids)
        if key in seen:
            continue
        seen.add(key)
        crews.append(member_ids)

    # Cheapest crew first; tie-break on the sorted member ids for full determinism.
    crews.sort(key=lambda ids: (sum(wage_by_worker.get(w, 0) for w in ids), sorted(ids)))

    recommendations: List[Recommendation] = []
    for rank, member_ids in enumerate(crews, start=1):
        total_cost = sum(wage_by_worker.get(wid, 0) for wid in member_ids)
        recommendations.append(
            Recommendation(
                rank=rank,
                member_ids=member_ids,
                total_cost=total_cost,
                reason=_reason(agent_input.mode, rank),
                considerations=_considerations(
                    agent_input.mode, agent_input.request.budget, total_cost
                ),
            )
        )

    return AgentOutput(
        mode=agent_input.mode,
        request_id=agent_input.request.request_id,
        recommendations=recommendations,
    )


# --------------------------------------------------------------------------- #
# Pure helpers                                                                 #
# --------------------------------------------------------------------------- #
def _aggregate_required(agent_input: AgentInput) -> Dict[str, int]:
    """Total required headcount per trade (summing any repeated trade entries)."""
    required: Dict[str, int] = defaultdict(int)
    for req in agent_input.request.required_workers:
        required[req.trade] += req.count
    return dict(required)


def _count_by_trade(fixed_members: Sequence[FixedMember]) -> Dict[str, int]:
    """How many retained fixed members belong to each trade."""
    counts: Dict[str, int] = defaultdict(int)
    for fm in fixed_members:
        counts[fm.trade] += 1
    return dict(counts)


def _wage_lookup(
    candidates: Sequence[Candidate], fixed_members: Sequence[FixedMember]
) -> Dict[str, int]:
    """Map ``worker_id -> desired_daily_wage`` for candidates and fixed members.

    This mirrors the server-side wage source used by the validator's ``total_cost``
    check (Property 5), so the composed ``total_cost`` matches exactly.
    """
    wages: Dict[str, int] = {c.worker_id: c.desired_daily_wage for c in candidates}
    for fm in fixed_members:
        wages[fm.worker_id] = fm.desired_daily_wage
    return wages


def _candidates_by_trade(
    candidates: Sequence[Candidate], exclude_ids: Set[str]
) -> Dict[str, List[Candidate]]:
    """Group candidates by trade (cost-first), excluding any fixed-member ids.

    Excluding fixed ids defends against a worker appearing both as a fixed member and a
    candidate, which would otherwise risk a duplicate in ``member_ids`` (Property 3).
    """
    grouped: Dict[str, List[Candidate]] = defaultdict(list)
    for c in candidates:
        if c.worker_id in exclude_ids:
            continue
        grouped[c.trade].append(c)
    return {trade: _cost_first(cands) for trade, cands in grouped.items()}


def _cost_first(cands: Sequence[Candidate]) -> List[Candidate]:
    """Order candidates cost-first with a seed-stable, fully reproducible tie-break.

    Primary key is the daily wage (cheapest first). Ties break under ``SEED`` via a
    per-worker deterministic jitter, with ``worker_id`` as the final stable key so the
    ordering is total and identical on every run and platform (the ``random`` module
    seeds string values through SHA-512, independent of ``PYTHONHASHSEED``). Because the
    jitter only reorders equal-wage workers, the cost-first guarantee is preserved.
    """

    def _key(c: Candidate):
        jitter = random.Random(f"{SEED}:{c.worker_id}").random()
        return (c.desired_daily_wage, jitter, c.worker_id)

    return sorted(cands, key=_key)


def _pick_window(
    sorted_cands: Sequence[Candidate], need: int, offset: int
) -> List[Candidate]:
    """Pick ``need`` candidates from a cost-first list using a wrapped rotation window.

    ``offset`` selects an alternative window so successive recommendations differ; the
    window wraps modulo the pool size, always yielding ``need`` distinct candidates when
    supply is sufficient (``len >= need``). With ``offset == 0`` the cheapest ``need``
    candidates are chosen, so the first recommendation is the lowest-cost crew. When
    supply is insufficient the whole pool is returned on a best-effort basis.
    """
    if need <= 0:
        return []
    n = len(sorted_cands)
    if n < need:
        return list(sorted_cands)  # insufficient supply: best-effort
    return [sorted_cands[(offset + i) % n] for i in range(need)]


def _reason(mode: str, rank: int) -> str:
    """Neutral, work-focused reason text (Requirement 3 -- no negative / probability)."""
    if mode == "EMERGENCY":
        base = "기존 팀원을 유지하고 부족한 직종 인원을 비용 우선으로 보충한 편성입니다."
    else:
        base = "필요 직종별 인원을 비용 우선으로 구성한 편성입니다."
    if rank == 1:
        return base
    return f"{base} (대안 {rank})"


def _considerations(mode: str, budget: int, total_cost: int) -> List[str]:
    """Neutral considerations list covering trade headcount, cost and budget."""
    items = [
        "직종별 필요 인원 충족",
        "일급 합계 기준 비용 우선 선정",
        f"요청 예산 {budget}원 대비 총 일급 {total_cost}원",
    ]
    if mode == "EMERGENCY":
        items.append("기존 팀원 유지")
    return items
