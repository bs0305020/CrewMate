"""Pure gap-computation logic for the gap_event Lambda (담당자 B).

When a worker goes missing mid-job (NO_SHOW / LEFT_SITE / UNAVAILABLE), the gap_event
Lambda must work out (a) which teammates are still on the crew and stay RUNNING, and
(b) how many workers of each trade are now short so the Crew Composition Agent can fill
the shortage in EMERGENCY mode. This module implements that arithmetic as two **pure
functions** operating on plain data - no I/O, no DB calls, no worker-state mutation -
so the gap logic is deterministic and property-testable.

Design references
-----------------
- ``design.md`` -> "Components and Interfaces" -> "6. Gap Event Lambda"
  (``gap_logic.py`` signatures).
- ``design.md`` -> "Data Models" -> "GapEvent 처리 모델" (the :class:`Member` model).
- ``design.md`` -> "Correctness Properties" 11 (fixed_members = active - departed,
  no mutation) and 12 (per-trade shortage + coverage guarantee).
- ``requirements.md`` -> Requirement 10.3 / 10.4 / 10.5.

What each function does
-----------------------
``compute_fixed_members(active_members, departed_ids) -> list[FixedMember]``
    Returns the active members MINUS anyone whose ``worker_id`` is in ``departed_ids``,
    each converted to a :class:`~agent.schemas.FixedMember` (worker_id / trade /
    desired_daily_wage). The result is directly consumable as the EMERGENCY payload's
    ``fixed_members`` (task 8.4 ``build_emergency_payload``). It is a *pure* set-
    difference-by-worker-id in original input order; it never includes a departed
    worker and never touches (reads-only) the input member objects or their ``state``
    (Property 11 / Req 10.3, 10.4). Note the design sketch's return type was
    ``list[Member]``; task 8.1 refines it to ``list[FixedMember]`` so the emergency
    payload assembler can consume it without an extra conversion step.

``compute_missing(required_workers, fixed_members) -> list[TradeRequirement]``
    For each required trade, the shortage is ``max(0, required_count - fixed_cover)``
    where ``fixed_cover`` is the number of retained fixed members of that trade
    (Property 12 / Req 10.5). Repeated trade entries in ``required_workers`` are
    aggregated by summing their counts first. Trades whose shortage is 0 (fully covered
    or over-covered by fixed members) are OMITTED from the returned list - see below.

Zero-missing / over-covered trades are DROPPED (design decision)
----------------------------------------------------------------
:class:`~agent.schemas.TradeRequirement` constrains ``count`` to ``> 0`` (``Field(gt=0)``),
so a zero shortage cannot be represented as a ``TradeRequirement``. Therefore
``compute_missing`` returns **only trades with a positive shortage** and omits any trade
already fully covered (``fixed_cover == required``) or over-covered
(``fixed_cover > required``, shortage clamped to 0 by ``max(0, ...)``).

The coverage invariant of Property 12 is expressed against the *capped* fixed coverage:
for every required trade,

    min(fixed_cover, required) + missing == required

where ``missing`` is the returned shortage for that trade, or ``0`` when the trade is
absent from the result. This holds in both regimes:

- ``fixed_cover <= required``: ``fixed_cover + (required - fixed_cover) == required``.
- ``fixed_cover >  required``: ``required + 0 == required`` (excess is ignored; a crew
  is never asked to shed workers just because a trade is over-staffed).

Capping with ``min(fixed_cover, required)`` is what makes "잔여 + 결원 = 요구 인원 정확
충족" exact: over-coverage of one trade never inflates the satisfied headcount. Fixed
members whose trade is not required at all simply do not reduce any shortage (they are
counted but never looked up), which is the intended behaviour.

Purity guarantees
-----------------
Both functions are pure: they read their inputs, allocate new result objects, and return
them. ``compute_fixed_members`` builds brand-new ``FixedMember`` instances and never
assigns to any input ``Member`` attribute, so input objects and their ``state`` are left
untouched (Property 11). Neither function performs I/O.

Python 3.9 note
---------------
``from __future__ import annotations`` keeps annotations lazy so the design's builtin
generics (``list[...]`` / ``dict[...]``, PEP 585) resolve on the local Python 3.9
runtime. This mirrors ``agent/schemas.py`` and ``agent_invoke/validator.py``. The
:class:`Member` model uses no PEP 604 (``X | None``) unions, so nothing needs
``eval_type_backport`` at model-build time.
"""
from __future__ import annotations

from typing import Dict, Iterable, List

from pydantic import BaseModel, Field

from agent.schemas import FixedMember, TradeRequirement

__all__ = [
    "Member",
    "compute_fixed_members",
    "compute_missing",
]


# --------------------------------------------------------------------------- #
# GapEvent processing model (design.md -> "Data Models" -> "GapEvent 처리 모델") #
# --------------------------------------------------------------------------- #
class Member(BaseModel):
    """An active crew member as seen by the gap_event Lambda before gap computation.

    Assembled by the handler (task 8.5) from 담당자 A's ``shared/db`` crew read; carries
    the fields the gap arithmetic needs plus the worker's current ``state`` (e.g.
    ``RUNNING``). ``state`` is carried for context only - the gap logic never reads it to
    make a decision and never modifies it (Property 11 keeps input state unchanged).

    ``desired_daily_wage`` mirrors :class:`~agent.schemas.FixedMember`'s ``> 0`` constraint
    (the conversion target in :func:`compute_fixed_members`); constraining it here rejects
    a non-positive wage at construction time instead of letting the later ``FixedMember``
    conversion raise.
    """

    worker_id: str
    trade: str
    desired_daily_wage: int = Field(gt=0)
    state: str


# --------------------------------------------------------------------------- #
# Pure gap computation                                                         #
# --------------------------------------------------------------------------- #
def compute_fixed_members(
    active_members: List[Member], departed_ids: Iterable[str]
) -> List[FixedMember]:
    """Return the retained team as ``FixedMember`` entries: active MINUS departed.

    Iterates ``active_members`` in order and keeps every member whose ``worker_id`` is
    not in ``departed_ids``, emitting a fresh :class:`~agent.schemas.FixedMember`
    (worker_id / trade / desired_daily_wage) for each. Input order is preserved and no
    departed worker is ever included (Property 11 / Req 10.3, 10.4).

    Pure: input :class:`Member` objects (and their ``state``) are only read; the result
    is composed of newly allocated ``FixedMember`` objects, so nothing about the inputs
    is mutated. ``departed_ids`` may be any iterable of ids (a ``set`` is ideal); it is
    copied into a set locally for O(1) membership tests and is not modified.
    """
    departed = set(departed_ids)
    return [
        FixedMember(
            worker_id=member.worker_id,
            trade=member.trade,
            desired_daily_wage=member.desired_daily_wage,
        )
        for member in active_members
        if member.worker_id not in departed
    ]


def compute_missing(
    required_workers: List[TradeRequirement], fixed_members: List[FixedMember]
) -> List[TradeRequirement]:
    """Return the per-trade shortage after retained ``fixed_members`` cover part of it.

    For each required trade the shortage is ``max(0, required_count - fixed_cover)``,
    where ``fixed_cover`` is the count of retained fixed members of that trade
    (Property 12 / Req 10.5). Repeated trade entries in ``required_workers`` are
    aggregated by summing their counts before the shortage is computed.

    Only trades with a positive shortage are returned, in the first-appearance order of
    the (aggregated) requirement. Fully covered and over-covered trades are omitted
    because :class:`~agent.schemas.TradeRequirement` cannot represent a zero ``count``;
    absence from the result therefore means "shortage 0" (see the module docstring's
    coverage-invariant explanation). Pure: no I/O, inputs are read-only.
    """
    required_by_trade = _aggregate_required(required_workers)
    fixed_cover_by_trade = _count_by_trade(fixed_members)

    missing: List[TradeRequirement] = []
    for trade, required_count in required_by_trade.items():
        covered = fixed_cover_by_trade.get(trade, 0)
        shortfall = max(0, required_count - covered)
        if shortfall > 0:
            missing.append(TradeRequirement(trade=trade, count=shortfall))
    return missing


# --------------------------------------------------------------------------- #
# Pure helpers                                                                 #
# --------------------------------------------------------------------------- #
def _aggregate_required(required_workers: Iterable[TradeRequirement]) -> Dict[str, int]:
    """Sum required headcount per trade, preserving first-appearance order."""
    aggregated: Dict[str, int] = {}
    for req in required_workers:
        aggregated[req.trade] = aggregated.get(req.trade, 0) + req.count
    return aggregated


def _count_by_trade(fixed_members: Iterable[FixedMember]) -> Dict[str, int]:
    """Count retained fixed members per trade (the coverage each trade already has)."""
    counts: Dict[str, int] = {}
    for member in fixed_members:
        counts[member.trade] = counts.get(member.trade, 0) + 1
    return counts
