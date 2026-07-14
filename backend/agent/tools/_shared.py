"""Internal helpers for the read-only Agent tool modules (담당자 B).

This module is **not a tool** and is never placed in the tool registry. It holds
the two cross-cutting concerns the four read-only tools share, so each tool module
stays small, importable, and unit-testable *without* the Strands Agents SDK
installed and *without* 담당자 A's ``backend/shared`` package existing on disk:

1. :func:`tool` - a guarded re-export of Strands' ``@tool`` decorator. When the
   SDK is installed the real decorator turns the wrapped function into an Agent
   tool (auto-generating its spec from the signature/docstring). When the SDK is
   absent (e.g. the local Python 3.9 dev/test environment, where ``strands-agents``
   is only a TODO in ``requirements.txt``) it degrades to an identity decorator so
   the plain read logic remains directly callable.
2. :func:`resolve_db` - a **lazy** resolver for 담당자 A's read-only ``db`` helper.
   Importing ``shared.db`` is deferred to call time so the tool modules
   import cleanly even though ``backend/shared`` is 담당자 A's code and is not
   created in this repo (tests install a stub under ``shared.*``).

Design references: ``design.md`` -> "3. Agent Tools" / "주 실행 경로: 사전
조립(pre-assembly) 우선"; ``requirements.md`` -> Requirement 5.

Python 3.9 note: ``from __future__ import annotations`` keeps the builtin-generic
annotation style lazy so it resolves on the local 3.9 runtime.
"""
from __future__ import annotations

from typing import Any, Callable, TypeVar

__all__ = ["tool", "resolve_db", "STRANDS_AVAILABLE"]

F = TypeVar("F", bound=Callable[..., Any])


# --------------------------------------------------------------------------- #
# Strands ``@tool`` decorator guard                                            #
# --------------------------------------------------------------------------- #
# The tool modules always ``from ._shared import tool`` regardless of whether the
# SDK is present, so tool definitions read identically in both environments and
# task 2.3 can register them with a real Strands ``Agent(tools=[...])`` once the
# SDK lands. Registration (wrapping) is kept separate from the plain read logic:
# each tool module keeps a private ``_read_*`` core function (dependency-injectable
# and SDK-free) and exposes a thin ``@tool``-decorated wrapper on top of it.
try:  # canonical top-level re-export (documented: ``from strands import tool``)
    from strands import tool as _strands_tool  # type: ignore

    STRANDS_AVAILABLE = True
except Exception:  # noqa: BLE001 - any import failure means the SDK is unavailable
    try:  # fall back to the decorator's module path if the re-export moves
        from strands.tools.decorator import tool as _strands_tool  # type: ignore

        STRANDS_AVAILABLE = True
    except Exception:  # noqa: BLE001
        STRANDS_AVAILABLE = False

        def _strands_tool(func: Any = None, *args: Any, **kwargs: Any) -> Any:
            """No-op stand-in supporting both ``@tool`` and ``@tool(...)`` usage.

            Returns the undecorated function so the plain read logic stays directly
            callable/testable when the SDK is not installed.
            """
            if func is None:  # used as ``@tool(...)`` -> return the real decorator

                def _identity(inner: F) -> F:
                    return inner

                return _identity
            return func


# Public name the tool modules import. Aliased (not re-wrapped) so both bare
# ``@tool`` and parameterized ``@tool(...)`` usage pass straight through to the
# real SDK decorator when available.
tool = _strands_tool


# --------------------------------------------------------------------------- #
# Lazy resolver for 담당자 A's read-only ``db`` helper                          #
# --------------------------------------------------------------------------- #
def resolve_db(db: Any = None) -> Any:
    """Return the object exposing 담당자 A's read-only ``db`` helper functions.

    Parameters
    ----------
    db:
        Optional dependency-injected helper (an object/module exposing
        ``get_work_request``, ``query_ready_workers``, ``get_workers``,
        ``get_worker_collaborations``, ``get_crew``). Tests pass a
        ``FakeSharedDB`` here; the Agent-invoked tool wrappers pass nothing.

    When ``db`` is ``None`` the real ``shared.db`` module is imported
    **lazily** (at call time, not import time). In deployment this is 담당자 A's
    module packaged as a shared Lambda Layer; in tests it is the stub registered
    under ``shared.db`` by ``install_shared_stubs``. Deferring the import
    keeps every tool module importable even though ``backend/shared`` does not
    exist on disk in this repo.
    """
    if db is not None:
        return db
    # High-level adapter over 담당자 A's real shared.* (imported lazily by design).
    from functions.agent_invoke import shared_gateway as shared_db

    return shared_db
