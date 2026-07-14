"""``get_current_crew`` - read-only Agent tool (Requirement 5.5).

Returns the current crew record for a ``crew_id`` - its current members, active
members, gaps, and required conditions - used mainly during EMERGENCY
re-composition to know which members remain and what must be back-filled. Backed
solely by 담당자 A's read-only ``db.get_crew`` helper - no write/transition helper
is called.

Structure mirrors the other tools: a plain, dependency-injectable core
(:func:`_read_current_crew`) plus a thin ``@tool`` wrapper (:func:`get_current_crew`).
"""
from __future__ import annotations

from typing import Any

from ._shared import resolve_db, tool


def _read_current_crew(crew_id: str, *, db: Any = None) -> Any:
    """Core read logic: return the current crew for ``crew_id``.

    Read-only. Calls only ``db.get_crew`` (Requirement 5.5). ``db`` may be
    dependency-injected for tests; otherwise 담당자 A's ``shared.db`` is
    resolved lazily.
    """
    return resolve_db(db).get_crew(crew_id)


@tool
def get_current_crew(crew_id: str) -> Any:
    """Return the current crew: members, active members, gaps, and requirements.

    Use this during emergency re-composition to see which members are still on the
    crew, which are active, where the gaps are, and the crew's required conditions,
    so the retained members can be kept and only the shortage filled. This tool is
    read-only and never changes any state.

    Args:
        crew_id: The identifier of the crew to look up.

    Returns:
        The current crew record (current members, active members, gaps, and
        required conditions).
    """
    return _read_current_crew(crew_id)
