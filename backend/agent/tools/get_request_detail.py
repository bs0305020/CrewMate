"""``get_request_detail`` - read-only Agent tool (Requirement 5.2).

Returns the detail conditions of a work request. Backed solely by 담당자 A's
read-only ``db.get_work_request`` helper - no write/transition helper is called.

Structure (see ``_shared`` for the rationale):
- :func:`_read_request_detail` is the plain, dependency-injectable core read
  logic. It stays directly callable/testable without the Strands SDK.
- :func:`get_request_detail` is the thin ``@tool``-decorated wrapper the Agent
  sees; its signature is kept clean (only ``request_id``) so the generated tool
  spec never exposes internal plumbing.
"""
from __future__ import annotations

from typing import Any

from ._shared import resolve_db, tool


def _read_request_detail(request_id: str, *, db: Any = None) -> Any:
    """Core read logic: return the work request detail for ``request_id``.

    Read-only. Calls only ``db.get_work_request`` (Requirement 5.2). ``db`` may be
    dependency-injected for tests; otherwise 담당자 A's ``shared.db`` is
    resolved lazily.
    """
    return resolve_db(db).get_work_request(request_id)


@tool
def get_request_detail(request_id: str) -> Any:
    """Return the detail conditions of the given work request.

    Use this to read a work request's required trades and headcount, budget,
    priority weights, site, work date, and start time before composing a crew.
    This tool is read-only and never changes any state.

    Args:
        request_id: The identifier of the work request to look up.

    Returns:
        The work request detail conditions.
    """
    return _read_request_detail(request_id)
