"""Crew Composition Agent definition (담당자 B).

This module defines the single Strands-Agents-SDK Agent that powers BOTH the NORMAL
(일반 편성) and EMERGENCY (긴급 재편성) flows, and the :func:`compose` entry point
the invoke Lambda calls.

Design references
-----------------
- ``design.md`` → "Components and Interfaces" → "1. Crew Composition Agent"
  (the ``build_agent`` / ``compose`` signatures).
- ``requirements.md`` → Requirement 1 (single agent, mode branch), 1.2/1.3 (NORMAL /
  EMERGENCY behavior), 2.7 (JSON only), 5.7 (pre-assembled single-call input).

Single agent, two modes (Req 1.1)
---------------------------------
:func:`build_agent` wires exactly one Agent (``agent/system_prompt.md`` + the four
read-only tools exported by ``agent.tools``). NORMAL and EMERGENCY are NOT separate
agents: the mode travels inside the :class:`AgentInput` payload and the system prompt
branches on it, so :func:`compose` runs the *same* instance for either mode.

SDK import guard (import-safe without strands-agents)
-----------------------------------------------------
The Strands Agents SDK (``strands-agents``) is a deploy-time dependency and is not
installed in the local Python 3.9 dev/test environment (it is only a TODO in
``requirements.txt``). Importing this module must therefore never require the SDK, so
the ``from strands import Agent`` / ``from strands.models import BedrockModel`` imports
are guarded. When the SDK is absent:

- the module still imports cleanly (the existing pytest suite and the task 2.4
  structure/smoke test keep working, and :func:`compose` stays unit-testable with an
  injected fake agent); and
- :func:`build_agent` raises a clear :class:`RuntimeError` *only when actually called*,
  explaining that the SDK is required to run the live agent.

Error standardization (Req 9, consumed by tasks 5.3/6.3)
--------------------------------------------------------
Every Bedrock invocation failure or timeout is normalized to :class:`BedrockUnavailable`
so the invoke Lambda can catch a single exception type to drive retry / demo fallback.
Output that is not pure, schema-conforming JSON (mixed prose + JSON, missing / wrong-typed
fields, extra keys) fails to parse into :class:`AgentOutput` and raises
``pydantic.ValidationError`` — a *parse failure* that is deliberately distinct from
:class:`BedrockUnavailable` and is handled downstream as an invalid-output / retry case.

Python 3.9 note
---------------
``from __future__ import annotations`` keeps annotations lazy so the ``-> Agent`` return
annotation resolves even when ``Agent`` is ``None`` (SDK absent) and the builtin-generic
annotation style stays 3.9-safe.
"""
from __future__ import annotations

import concurrent.futures
import os
from pathlib import Path
from typing import Any, Optional

from agent.schemas import AgentInput, AgentOutput
from agent.tools import READ_ONLY_TOOLS

__all__ = [
    "BedrockUnavailable",
    "STRANDS_AVAILABLE",
    "SYSTEM_PROMPT_PATH",
    "load_system_prompt",
    "build_agent",
    "compose",
]


# --------------------------------------------------------------------------- #
# Strands SDK import guard                                                     #
# --------------------------------------------------------------------------- #
# A deploy-time dependency that is absent in the local 3.9 dev/test env. Guarding the
# import keeps the module importable everywhere; only build_agent() needs the SDK.
try:
    from strands import Agent
    from strands.models import BedrockModel

    STRANDS_AVAILABLE = True
except Exception:  # noqa: BLE001 - any import failure means the SDK is unavailable
    Agent = None  # type: ignore[assignment,misc]
    BedrockModel = None  # type: ignore[assignment,misc]
    STRANDS_AVAILABLE = False


# --------------------------------------------------------------------------- #
# Model configuration (env-overridable)                                        #
# --------------------------------------------------------------------------- #
# Amazon Bedrock model id / region are env-overridable so deployment can pin an enabled
# model without code changes. A low temperature keeps composition stable and factual
# (the agent reasons over a fixed, pre-assembled candidate set — Req 5.7).
DEFAULT_MODEL_ID = os.environ.get(
    "CREW_AGENT_MODEL_ID", "anthropic.claude-3-5-sonnet-20241022-v2:0"
)
DEFAULT_REGION = (
    os.environ.get("CREW_AGENT_REGION") or os.environ.get("AWS_REGION") or "us-west-2"
)
DEFAULT_TEMPERATURE = float(os.environ.get("CREW_AGENT_TEMPERATURE", "0.2"))

# ``system_prompt.md`` lives next to this module; resolve via ``__file__`` so loading is
# independent of the process cwd (Lambda, pytest, or ad-hoc invocation).
SYSTEM_PROMPT_PATH = Path(__file__).resolve().parent / "system_prompt.md"


class BedrockUnavailable(Exception):
    """Raised when the Bedrock-backed agent call fails or times out.

    Standardizes *all* invocation-side failures (Bedrock / botocore errors, network
    issues, wall-clock timeout) into one exception type so the invoke Lambda (tasks
    5.3/6.3) can catch it to trigger the demo fallback (Req 9.3/9.4) or map it to
    ``AGENT_RETRY_FAILED`` (Req 9.2).

    NOTE: a *parse failure* of the model output is intentionally NOT a
    ``BedrockUnavailable`` — it surfaces as ``pydantic.ValidationError`` and is handled
    as an invalid-output / retry case (Req 7.9 / 9.1) instead.
    """


def load_system_prompt() -> str:
    """Return the Agent system prompt text from ``agent/system_prompt.md`` (Req 4.1).

    Loaded from disk via a ``__file__``-relative path so it resolves regardless of the
    current working directory.
    """
    return SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")


def _build_bedrock_model() -> "BedrockModel":
    """Construct the Amazon Bedrock model backing the Agent (SDK required)."""
    return BedrockModel(
        model_id=DEFAULT_MODEL_ID,
        region_name=DEFAULT_REGION,
        temperature=DEFAULT_TEMPERATURE,
    )


def build_agent(fallback_enabled: bool = False) -> "Agent":
    """Build the single Crew Composition Agent (system prompt + 4 read-only tools).

    The returned Agent serves BOTH modes (Req 1.1): the mode is carried in the
    :class:`AgentInput` payload passed to :func:`compose`, never by constructing separate
    agents. Exactly the four read-only tools from ``agent.tools`` (:data:`READ_ONLY_TOOLS`)
    are registered; no write-capable tool is ever attached (Req 5.1 / 5.6).

    Parameters
    ----------
    fallback_enabled:
        Preference recorded for the orchestration layer (``compose_flow``, task 6.3),
        which decides whether a :class:`BedrockUnavailable` should fall back to the
        deterministic demo composer (Req 9.3/9.4). It does NOT change how the agent is
        wired — the prompt and tool set are identical either way — so composition
        behavior stays consistent regardless of the flag.

    Returns
    -------
    Agent
        The configured single Strands Agent instance.

    Raises
    ------
    RuntimeError
        If the Strands Agents SDK is not installed. The module still imports without the
        SDK; only *building* a live agent requires it.
    """
    if not STRANDS_AVAILABLE:
        raise RuntimeError(
            "The Strands Agents SDK ('strands-agents') is required to build the Crew "
            "Composition Agent but is not installed. Install it and configure Amazon "
            "Bedrock credentials to run the live agent. (This module imports without the "
            "SDK so pure-logic tests and the structure/smoke test still run; inject a "
            "fake agent into compose(agent=...) to unit-test parsing.)"
        )

    agent = Agent(
        model=_build_bedrock_model(),
        tools=list(READ_ONLY_TOOLS),
        system_prompt=load_system_prompt(),
    )
    # Best-effort: record the fallback preference for observability / orchestration.
    try:
        agent._crewmate_fallback_enabled = bool(fallback_enabled)  # type: ignore[attr-defined]
    except Exception:  # pragma: no cover - defensive if Agent restricts attributes
        pass
    return agent


def _build_prompt(agent_input: AgentInput) -> str:
    """Serialize the pre-assembled :class:`AgentInput` into the single user message.

    Per Req 5.7 and the design's "pre-assembly first" path, the Lambda assembles all
    candidate data up front and passes it in one call; the agent reasons only over this
    payload. The system prompt already defines the input shape, the mode branch, the hard
    constraints, and the JSON-only output rule, so the message is the input JSON plus a
    short reinforcing instruction.
    """
    payload = agent_input.model_dump_json()
    return (
        "다음은 작업조 편성 요청 입력(JSON)입니다. 이 payload에 포함된 데이터만 사용하고, "
        "시스템 프롬프트의 규칙(mode 분기, 후보/고정 멤버 범위, 필수 직종·인원 준수, "
        "JSON only)을 지켜 추천 결과를 지정된 JSON 스키마로만 반환하세요.\n\n"
        f"{payload}"
    )


def _extract_text(result: Any) -> str:
    """Extract the assistant's text from a Strands agent result (or a plain ``str``).

    Handles three shapes:
    - a plain ``str`` (used by injected fakes in tests);
    - a Strands ``AgentResult`` whose ``.message`` is
      ``{"role": ..., "content": [{"text": ...}, ...]}`` — only ``{"text": ...}`` blocks
      are concatenated, matching how the SDK renders final text output;
    - anything else, via ``str()``.
    """
    if result is None:
        return ""
    if isinstance(result, str):
        return result
    message = getattr(result, "message", None)
    if isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, list):
            texts = [
                block["text"]
                for block in content
                if isinstance(block, dict) and isinstance(block.get("text"), str)
            ]
            if texts:
                return "".join(texts)
    return str(result)


def _call_agent(agent: Any, prompt: str) -> Any:
    """Invoke the agent on a single prompt (isolated so it can run under a timeout)."""
    return agent(prompt)


def _invoke_with_timeout(agent: Any, prompt: str, timeout_s: Optional[float]) -> Any:
    """Run the agent call, standardizing failures / timeouts to :class:`BedrockUnavailable`.

    A positive ``timeout_s`` bounds the call on wall-clock time using a worker thread;
    exceeding it raises :class:`BedrockUnavailable`. Any exception from the call itself
    (Bedrock / botocore / network) is likewise normalized to :class:`BedrockUnavailable`.
    Parsing is done by the caller AFTER this returns, so output parse failures are never
    masked as ``BedrockUnavailable``.
    """
    if not timeout_s or timeout_s <= 0:
        try:
            return _call_agent(agent, prompt)
        except Exception as exc:  # noqa: BLE001 - normalize all invocation failures
            raise BedrockUnavailable(f"Bedrock agent call failed: {exc}") from exc

    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = executor.submit(_call_agent, agent, prompt)
    try:
        return future.result(timeout=timeout_s)
    except concurrent.futures.TimeoutError as exc:
        raise BedrockUnavailable(
            f"Bedrock agent call timed out after {timeout_s}s"
        ) from exc
    except Exception as exc:  # noqa: BLE001 - normalize all invocation failures
        raise BedrockUnavailable(f"Bedrock agent call failed: {exc}") from exc
    finally:
        # Do not block compose() on a slow/hung call; let the worker thread drain on its own.
        executor.shutdown(wait=False)


def compose(
    agent_input: AgentInput,
    *,
    timeout_s: float,
    agent: Optional["Agent"] = None,
) -> AgentOutput:
    """Run the agent for one compose request and return the parsed :class:`AgentOutput`.

    NORMAL vs EMERGENCY is decided by ``agent_input.mode`` on the SAME agent instance
    (Req 1.1 / 1.2 / 1.3). The model response is parsed as JSON ONLY into
    :class:`AgentOutput` (Req 2.7): pure conforming JSON succeeds, while mixed prose + JSON,
    missing / wrong-typed fields, or extra keys fail to parse (``pydantic.ValidationError``)
    because the output schema is strict + ``extra="forbid"``.

    Parameters
    ----------
    agent_input:
        The pre-assembled input (mode + request + candidates + fixed_members +
        collaboration_pairs).
    timeout_s:
        Wall-clock bound (seconds) for the Bedrock call; on timeout a
        :class:`BedrockUnavailable` is raised.
    agent:
        Optional injected / overridden agent. When ``None`` a fresh agent is built via
        :func:`build_agent` (which requires the SDK). Tests inject a fake here (or
        monkeypatch :func:`build_agent`) so no live Bedrock call is ever made.

    Returns
    -------
    AgentOutput
        The parsed, schema-conforming agent output. (Rule-level validation of its
        contents happens later in ``validator.py`` — task 3.x.)

    Raises
    ------
    BedrockUnavailable
        If the Bedrock call fails or exceeds ``timeout_s``.
    pydantic.ValidationError
        If the model output is not pure, schema-conforming JSON (a parse failure).
    """
    active_agent = agent if agent is not None else build_agent()
    prompt = _build_prompt(agent_input)
    raw_result = _invoke_with_timeout(active_agent, prompt, timeout_s)
    text = _extract_text(raw_result)
    # Strict JSON-only parse (Req 2.7). A ValidationError here is a parse failure and is
    # left to propagate — the invoke Lambda treats it as an invalid-output / retry case.
    return AgentOutput.model_validate_json(text.strip())
