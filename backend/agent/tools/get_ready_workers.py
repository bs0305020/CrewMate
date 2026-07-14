"""``get_ready_workers`` - read-only Agent tool (Requirement 5.3).

Returns the READY candidate workers of a given office, optionally filtered to the
required trades. Backed solely by 담당자 A's read-only ``db.query_ready_workers``
helper, which already scopes results to ``office_id`` + ``state=READY`` (the only
candidate scope the Agent may operate on - Requirement 1.8 / 6.3).

Structure mirrors the other tools: a plain, dependency-injectable core
(:func:`_read_ready_workers`) plus a thin ``@tool`` wrapper (:func:`get_ready_workers`).
"""
from __future__ import annotations

from typing import Any, List, Optional, Sequence


from ._shared import resolve_db, tool


def _read_ready_workers(
    office_id: str,
    required_trades: Optional[Sequence[str]] = None,
    *,
    db: Any = None,
) -> Any:
    """Core read logic: return READY candidates for ``office_id``.

    Read-only. Calls only ``db.query_ready_workers(office_id, trades)``
    (Requirement 5.3), which restricts results to workers whose ``office_id``
    matches and whose ``state`` is READY. When ``required_trades`` is provided the
    helper further narrows to those trades. ``db`` may be dependency-injected for
    tests; otherwise 담당자 A's ``shared.db`` is resolved lazily.
    """
    trades: Optional[List[str]] = (
        list(required_trades) if required_trades is not None else None
    )
    return resolve_db(db).query_ready_workers(office_id, trades)


@tool
def get_ready_workers(office_id: str, required_trades: Optional[List[str]] = None) -> Any:
    """Return the READY candidate workers for the given office.

    Use this to fetch the pool of available (READY) workers the crew may be
    composed from. Optionally pass the required trades to narrow the pool to the
    trades the request needs. Results are already limited to this office and to
    workers in the READY state. This tool is read-only and never changes any state.

    Args:
        office_id: The office whose READY workers should be returned.
        required_trades: Optional list of trades to filter the candidates by
            (e.g. ["FORMWORK", "REBAR"]). When omitted, all READY workers of the
            office are returned.

    Returns:
        The READY candidate workers of the office (optionally trade-filtered).
    """
    return _read_ready_workers(office_id, required_trades)
