"""Persistence for validated Agent recommendations (담당자 B, task 5.2).

This module stores a validated :class:`~agent.schemas.Recommendation` as a
``Crew(status=PROPOSED, source=AGENT)`` via 담당자 A's ``backend/shared/db`` helper.
It deliberately keeps the **NORMAL** and **EMERGENCY** save flows on separate,
explicit code paths so that only the NORMAL path ever transitions the associated
WorkRequest.

NORMAL-only crew persistence (option-1 EMERGENCY hand-off)
---------------------------------------------------------
Only the **NORMAL** flow persists a Crew here:

- **NORMAL** : save the Crew, THEN transition the WorkRequest ``COMPOSING → PROPOSED``
  (Req 8.1, 8.2). The invoke handler already acquired the lock at entry
  (``REQUESTED → COMPOSING``), so this terminal transition closes the compose flow.
- **EMERGENCY** : NO Crew is created. Under the option-1 emergency hand-off the validated
  1..3 recommendations are written straight onto the GapEvent item (retained
  ``fixed_member_ids`` + ``recommendations``) by the gap_event Lambda via
  ``shared_gateway.record_gap_recommendations``; 담당자 A's emergency approval API reads
  them and the OFFICE approves a ``replacement_member_ids`` set. So the EMERGENCY compose
  flow never calls a save function here, never touches the WorkRequest (it may be
  ``RUNNING``), and never transitions the GapEvent (gap_event owns that terminal transition).

Safety invariant (underpins Property 9, tested in task 5.4)
-----------------------------------------------------------
The NORMAL save performs no worker state change, approval, or assignment — those are
delegated to 담당자 A's approval API. Its only side effects are ``save_crew`` and
``transition_request_status``.

shared helper consumption (design.md → "소비하는 shared 계약")
------------------------------------------------------------
``backend/shared/*`` is 담당자 A's and is **consumed, never implemented**. It does
not exist on disk in this scope (see ``tests/test_scaffolding.py``); tests install
in-memory stubs under ``shared.*`` via ``install_shared_stubs(monkeypatch)``.
The ``db`` module is therefore imported **lazily inside each function** so it resolves
at call time (the real Layer module in deployment, the installed stub during tests)
regardless of import order.

Python 3.9 note
---------------
``from __future__ import annotations`` keeps annotations lazy so the design's
builtin-generic / ``Literal`` annotation style resolves cleanly on Python 3.9.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Literal, Optional

from agent.schemas import Recommendation

__all__ = [
    "SaveContext",
    "save_normal_proposal",
    "save_proposal",
]

# --------------------------------------------------------------------------- #
# Contract constants — mirror shared.state values                      #
# (CrewStatus.PROPOSED / Source.AGENT / RequestStatus.COMPOSING|PROPOSED).      #
# Declared locally so this module stays importable standalone (the real        #
# state module is 담당자 A's and absent on disk here). Values are fixed by the  #
# shared contract glossary in requirements.md and verified against             #
# tests/mocks/shared_stubs.py.                                                  #
# --------------------------------------------------------------------------- #
MODE_NORMAL = "NORMAL"
MODE_EMERGENCY = "EMERGENCY"

_CREW_PROPOSED = "PROPOSED"  # CrewStatus.PROPOSED
_SOURCE_AGENT = "AGENT"  # Source.AGENT
_REQ_COMPOSING = "COMPOSING"  # RequestStatus.COMPOSING (expected state at save time)
_REQ_PROPOSED = "PROPOSED"  # RequestStatus.PROPOSED (target after NORMAL save)


@dataclass(frozen=True)
class SaveContext:
    """Minimal, immutable context needed to build and persist a proposed Crew.

    Built by the invoke handler (task 5.3) after validation passes and passed to the
    matching save function. Kept intentionally small: only what a Crew item needs plus
    the EMERGENCY linkage fields.

    Attributes
    ----------
    mode:
        ``"NORMAL"`` or ``"EMERGENCY"`` — selects the save flow. Only NORMAL transitions
        the WorkRequest.
    request_id:
        The associated WorkRequest id (stored on the Crew; used as the transition key in
        the NORMAL flow).
    office_id:
        Owning office (linkage; enables office-scoped queries). Optional.
    work_date:
        Work date for the composed crew (linkage). Optional.
    current_crew_id:
        EMERGENCY only — the Crew being re-composed/superseded. Optional.
    gap_event_id:
        EMERGENCY only — the originating GapEvent. Optional.
    source:
        Provenance marker; always ``AGENT`` in this scope.
    crew_id:
        Explicit Crew id. When ``None`` the db helper assigns one and returns it.
    """

    mode: Literal["NORMAL", "EMERGENCY"]
    request_id: str
    office_id: Optional[str] = None
    work_date: Optional[str] = None
    current_crew_id: Optional[str] = None
    gap_event_id: Optional[str] = None
    source: str = _SOURCE_AGENT
    crew_id: Optional[str] = None


def _fold_rationale(recommendation: Recommendation) -> str:
    """Fold the recommendation ``reason`` + ``considerations`` into one ``rationale`` string.

    The canonical Crew schema (``schemas.build_crew``) has a single free-text ``rationale``
    field and no ``considerations`` list, so the considerations are appended to the reason
    text rather than dropped (design decision — see the module docstring of
    ``shared_gateway``). ``rank`` has no canonical home and is not persisted.
    """
    reason = recommendation.reason or ""
    considerations = [c for c in recommendation.considerations if c]
    if considerations:
        joined = " · ".join(considerations)
        return f"{reason} (고려사항: {joined})" if reason else f"고려사항: {joined}"
    return reason


def _save_crew_via_adapter(db: Any, recommendation: Recommendation, ctx: SaveContext) -> str:
    """Persist the chosen recommendation as a ``Crew(PROPOSED, source=AGENT)`` via the adapter.

    Maps the recommendation onto the adapter's ``save_crew`` kwargs, which build the canonical
    ``schemas.build_crew`` item: ``reason → rationale`` (with ``considerations`` folded in),
    ``total_cost → estimated_cost``, preserving ``member_ids`` / ``source``. The
    EMERGENCY linkage (``gap_event_id`` / ``current_crew_id``) is intentionally NOT persisted
    on the Crew item — the canonical schema has no such field — and is surfaced only in the
    agent_invoke response (sourced from ``SaveContext``). ``rank`` is dropped (no canonical
    home). Returns the crew id assigned by the adapter/db helper.
    """
    return db.save_crew(
        office_id=ctx.office_id,
        request_id=ctx.request_id,
        member_ids=list(recommendation.member_ids),
        rationale=_fold_rationale(recommendation),
        estimated_cost=recommendation.total_cost,
        source=ctx.source,
        status=_CREW_PROPOSED,
        crew_id=ctx.crew_id,
    )


def save_normal_proposal(recommendation: Recommendation, ctx: SaveContext) -> str:
    """NORMAL: save the Crew, THEN transition the WorkRequest ``COMPOSING → PROPOSED``.

    Order matters (Req 8.1 then 8.2): the Crew is persisted first, then the WorkRequest
    is advanced. The expected state is ``COMPOSING`` because the handler moved the
    request ``REQUESTED → COMPOSING`` at entry and holds that lock throughout the
    compose flow, so this terminal transition is expected to succeed.

    Returns the ``crew_id`` (assigned by the db helper when ``ctx.crew_id`` is unset).
    Performs no worker state change, approval, or assignment (delegated to 담당자 A).
    """
    from functions.agent_invoke import shared_gateway as db  # high-level adapter

    crew_id = _save_crew_via_adapter(db, recommendation, ctx)
    db.transition_request_status(ctx.request_id, _REQ_COMPOSING, _REQ_PROPOSED)
    return crew_id


def save_proposal(recommendation: Recommendation, ctx: SaveContext) -> str:
    """Dispatch the (NORMAL-only) crew save flow (design.md ``save_proposal``).

    Only NORMAL persists a Crew. EMERGENCY does NOT create a Crew under the option-1
    hand-off (the gap_event Lambda records the recommendations onto the GapEvent instead),
    so an ``EMERGENCY`` context here is a programming error and raises rather than silently
    saving a crew. An unknown mode likewise raises (defensive: a malformed mode must never
    trigger a state-mutating save).
    """
    if ctx.mode == MODE_NORMAL:
        return save_normal_proposal(recommendation, ctx)
    raise ValueError(
        f"save_proposal only persists NORMAL crews; got mode={ctx.mode!r} "
        "(EMERGENCY recommendations are recorded on the GapEvent, not saved as a Crew)"
    )
