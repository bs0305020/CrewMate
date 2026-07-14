"""Read-only Agent tools registry (담당자 B).

Exactly four read-only tools are registered here (Requirement 5.1):
``get_request_detail``, ``get_ready_workers``, ``get_worker_history``, and
``get_current_crew``. Each calls only 담당자 A's read-only ``backend/shared/db``
helpers; none can write or transition state.

No write-capable tool is ever defined or registered (Requirement 5.6): there is no
``update_worker_state``, ``approve_crew``, ``assign_worker``, ``mark_running``,
``delete_worker``, or ``update_company_request`` here. Their names are recorded in
:data:`WRITE_TOOL_NAMES` purely so the structure/smoke test (task 2.4) can assert
their absence from the registry - they are documentation, not tools.

Registry shape (consumed by task 2.3's ``build_agent`` and asserted by task 2.4):

- :data:`READ_ONLY_TOOLS`  - ordered list of the four tool callables, suitable for
  ``Agent(tools=READ_ONLY_TOOLS)`` once the Strands SDK is installed.
- :data:`TOOL_REGISTRY`    - mapping of tool name -> callable. Keyed by explicit
  string names so assertions do not depend on SDK-specific introspection (the
  ``@tool`` wrappers differ between the SDK-present and SDK-absent environments).
- :data:`WRITE_TOOL_NAMES` - the forbidden write-tool names, guaranteed disjoint
  from the registry.

The four tool modules import cleanly with or without the Strands SDK and do not
touch ``backend/shared`` at import time (the db helper is resolved lazily only when
a tool is actually invoked), so importing this package never requires the SDK or
담당자 A's package to be present.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List

from .get_current_crew import get_current_crew
from .get_ready_workers import get_ready_workers
from .get_request_detail import get_request_detail
from .get_worker_history import get_worker_history

__all__ = [
    "get_request_detail",
    "get_ready_workers",
    "get_worker_history",
    "get_current_crew",
    "READ_ONLY_TOOLS",
    "TOOL_REGISTRY",
    "WRITE_TOOL_NAMES",
]

# Ordered list of the four read-only tool callables (Requirement 5.1). This is the
# exact set passed to the single Strands Agent in task 2.3.
READ_ONLY_TOOLS: List[Callable[..., Any]] = [
    get_request_detail,
    get_ready_workers,
    get_worker_history,
    get_current_crew,
]

# Name -> callable registry. Keys are explicit strings (not derived via SDK
# introspection) so exactly-four / absence assertions are stable in every
# environment.
TOOL_REGISTRY: Dict[str, Callable[..., Any]] = {
    "get_request_detail": get_request_detail,
    "get_ready_workers": get_ready_workers,
    "get_worker_history": get_worker_history,
    "get_current_crew": get_current_crew,
}

# Write-capable tools that MUST NOT be provided to the Agent (Requirement 5.6).
# Listed for documentation and to let the structure test assert they are absent
# from TOOL_REGISTRY. These are intentionally NOT defined anywhere in this package.
WRITE_TOOL_NAMES = frozenset(
    {
        "update_worker_state",
        "approve_crew",
        "assign_worker",
        "mark_running",
        "delete_worker",
        "update_company_request",
    }
)
