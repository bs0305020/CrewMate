"""Agent 구조·프롬프트 스모크 테스트 (task 2.4).

Structure/smoke checks for the Crew Composition Agent wiring. These are plain, fast
pytest assertions - no Hypothesis, no markers.

What is asserted
----------------
- **Req 5.1** - the read-only tool registry contains EXACTLY the four required tools
  (``get_request_detail``, ``get_ready_workers``, ``get_worker_history``,
  ``get_current_crew``) and nothing else.
- **Req 5.6** - no write-capable tool is registered: the six forbidden write tools
  (``update_worker_state``, ``approve_crew``, ``assign_worker``, ``mark_running``,
  ``delete_worker``, ``update_company_request``) never appear in the registry.
- **Req 4.1** - ``agent/system_prompt.md`` exists and carries the core constraint
  phrases: READY-only candidates (Req 4.3), JSON-only output (Req 4.9 / 2.7), and the
  no-negative / no-probability language rule (Req 3.2 / 3.4).

Runs without the Strands SDK
----------------------------
The Strands Agents SDK is a deploy-time dependency that is absent from the local
dev/test environment. This test therefore only imports the tool REGISTRY and reads the
prompt file - it never calls ``build_agent`` (which needs the SDK). ``crew_agent`` is
imported solely for the ``SYSTEM_PROMPT_PATH`` constant, which resolves via a
``__file__``-relative path and does not require the SDK.

_Requirements: 5.1, 5.6, 4.1_
"""
from __future__ import annotations

from agent.crew_agent import SYSTEM_PROMPT_PATH
from agent.tools import READ_ONLY_TOOLS, TOOL_REGISTRY, WRITE_TOOL_NAMES

# The exact four read-only tools mandated by Requirement 5.1.
EXPECTED_READ_ONLY_TOOL_NAMES = frozenset(
    {
        "get_request_detail",
        "get_ready_workers",
        "get_worker_history",
        "get_current_crew",
    }
)

# The six write-capable tools that MUST NOT be exposed to the Agent (Requirement 5.6).
EXPECTED_WRITE_TOOL_NAMES = frozenset(
    {
        "update_worker_state",
        "approve_crew",
        "assign_worker",
        "mark_running",
        "delete_worker",
        "update_company_request",
    }
)


# --------------------------------------------------------------------------- #
# Req 5.1 - exactly four read-only tools are registered                       #
# --------------------------------------------------------------------------- #
def test_tool_registry_has_exactly_four_tools() -> None:
    """The registry exposes exactly the four required read-only tools (Req 5.1)."""
    assert len(READ_ONLY_TOOLS) == 4
    assert len(TOOL_REGISTRY) == 4
    assert set(TOOL_REGISTRY) == EXPECTED_READ_ONLY_TOOL_NAMES


def test_read_only_tools_list_matches_registry() -> None:
    """``READ_ONLY_TOOLS`` and ``TOOL_REGISTRY`` describe the same four callables."""
    # No duplicate callables masking a smaller effective set.
    assert len(set(READ_ONLY_TOOLS)) == 4
    assert set(READ_ONLY_TOOLS) == set(TOOL_REGISTRY.values())
    for name, tool in TOOL_REGISTRY.items():
        assert callable(tool), f"registered tool {name!r} is not callable"


# --------------------------------------------------------------------------- #
# Req 5.6 - no write-capable tool is registered                               #
# --------------------------------------------------------------------------- #
def test_no_write_tools_registered() -> None:
    """None of the six forbidden write tools appear in the registry (Req 5.6)."""
    # The documented forbidden set is precisely the six write tools.
    assert WRITE_TOOL_NAMES == EXPECTED_WRITE_TOOL_NAMES
    # No write-tool name is a registered tool ...
    assert WRITE_TOOL_NAMES.isdisjoint(TOOL_REGISTRY)
    # ... asserted per name too, for a clear failure message if one ever leaks in.
    for name in EXPECTED_WRITE_TOOL_NAMES:
        assert name not in TOOL_REGISTRY
    # The registry is exactly the read-only set - no write tools, no extras of any kind.
    assert set(TOOL_REGISTRY) == EXPECTED_READ_ONLY_TOOL_NAMES


# --------------------------------------------------------------------------- #
# Req 4.1 - system_prompt.md exists and carries the core constraints          #
# --------------------------------------------------------------------------- #
def test_system_prompt_file_exists() -> None:
    """``agent/system_prompt.md`` exists on disk (Req 4.1)."""
    assert SYSTEM_PROMPT_PATH.name == "system_prompt.md"
    assert SYSTEM_PROMPT_PATH.is_file()


def test_system_prompt_contains_core_constraints() -> None:
    """The prompt states the READY-only, JSON-only and no-negative/probability rules.

    Maps to Req 4.3 (READY-only candidates), Req 4.9 / 2.7 (JSON-only output) and
    Req 3.2 / 3.4 (reasons free of negative worker evaluations and probability /
    optimality-guarantee expressions).
    """
    text = SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")

    assert "READY 전용" in text
    assert "JSON only" in text
    assert "부정표현·확률표현 금지" in text
