"""Structured, PII-free observability logging for the agent_invoke Lambda (담당자 B, task 9.1).

Every Crew Composition Agent execution emits one structured CloudWatch log line so a
composition run can be debugged after the fact - which mode ran, how many candidates went
in, how many recommendations came out, whether validation passed, whether a retry or the
demo fallback was used, and whether the proposal was finally saved. The line is a single
JSON object (one record per line) so CloudWatch Logs Insights can parse and filter it.

Design references
-----------------
- ``design.md`` -> "Data Models" -> "관측성 로그 레코드" (the :class:`AgentLogRecord` shape).
- ``requirements.md`` -> Requirement 12 (Agent 관측성 로그): 12.1 (required fields) and
  12.2 (exclude full worker PII).

PII exclusion is a hard requirement (Req 12.2)
----------------------------------------------
The log must NEVER contain full worker personal information (name, phone, email, address).
It records only ``worker_id``-level identifiers and aggregate counts / flags. Two guards
enforce this by construction:

1. :class:`AgentLogRecord` declares exactly the fields from the design - none of them PII -
   and sets ``extra="forbid"``, so attaching a stray ``name`` / ``phone`` key raises a
   ``ValidationError`` instead of silently leaking into the log.
2. The public surface only accepts a typed :class:`AgentLogRecord` (:func:`log_agent_execution`)
   or the named primitive parameters of :func:`build_agent_log_record` - there is no
   ``**kwargs`` / free-form dict path through which arbitrary fields could be logged.

Wiring note (task 9.2)
----------------------
This module intentionally does NOT touch ``compose_flow`` or the ``gap_event`` handler yet.
Task 9.2 will call :func:`build_agent_log_record` + :func:`log_agent_execution` from those
paths, sourcing only counts / ids / flags from the flow state (never names / phones).

Logging transport
-----------------
Uses the standard library ``logging`` module. In AWS Lambda the runtime configures a root
handler that captures stdout/stderr, so a module logger that propagates (the default) reaches
CloudWatch without this module installing its own handler (which would double-log in Lambda).
The module logger's level is set to ``INFO`` so the record is always emitted.

Python 3.9 note
---------------
``from __future__ import annotations`` keeps annotations lazy so the design's builtin-generic
style (``list[str]``) resolves on the local Python 3.9 runtime. The design's PEP 604 union
(``str | None``) is written as ``Optional[str]`` because Pydantic v2 on Python 3.9 cannot
evaluate the ``X | None`` form at model-build time (mirrors ``agent/schemas.py`` and
``validator.py``).
"""
from __future__ import annotations

import logging
import uuid
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict

__all__ = [
    "AgentLogRecord",
    "new_execution_id",
    "get_logger",
    "build_agent_log_record",
    "log_agent_execution",
    "LOGGER_NAME",
]

# Module logger name - also the CloudWatch filter dimension for these structured lines.
LOGGER_NAME = "agent_invoke.observability"

# Configured once at import: INFO so the structured record is always emitted, and left to
# propagate to the root handler (AWS Lambda / pytest caplog) rather than owning a handler.
_logger = logging.getLogger(LOGGER_NAME)
_logger.setLevel(logging.INFO)


class AgentLogRecord(BaseModel):
    """One structured, PII-free record of a single Agent composition run (Req 12.1).

    Field set mirrors ``design.md`` -> "관측성 로그 레코드" exactly. ``extra="forbid"`` makes
    the model reject any unexpected key (e.g. an accidental ``name`` / ``phone``), enforcing
    the PII-exclusion guarantee (Req 12.2) at construction time.

    Attributes
    ----------
    agent_execution_id:
        Per-run UUID correlating all log lines for one execution (see :func:`new_execution_id`).
    agent_mode:
        ``"NORMAL"`` or ``"EMERGENCY"`` - which composition path ran.
    request_id:
        The associated WorkRequest id (not PII).
    input_candidate_count:
        Number of assembled READY candidates passed to the Agent (aggregate count).
    recommendation_count:
        Number of recommendations the Agent returned (aggregate count).
    validation_passed:
        Whether the 7-check code validation passed.
    validation_failed_checks:
        Names of the checks that failed (empty when validation passed). Check *names*
        only - never worker details.
    retried:
        Whether the one allowed Agent retry occurred.
    fallback_used:
        Whether the deterministic demo fallback produced the output.
    saved:
        Final save result - whether a Crew(PROPOSED) was persisted.
    crew_id:
        The saved Crew id when ``saved`` is true, else ``None``.
    """

    model_config = ConfigDict(extra="forbid")

    agent_execution_id: str
    agent_mode: Literal["NORMAL", "EMERGENCY"]
    request_id: str
    input_candidate_count: int
    recommendation_count: int
    validation_passed: bool
    validation_failed_checks: list[str] = []
    retried: bool
    fallback_used: bool
    saved: bool
    crew_id: Optional[str] = None
    # PII (name / phone / email / address) is intentionally absent; worker_id-level ids
    # and aggregate counts only. ``extra="forbid"`` blocks stray fields (Req 12.2).


def new_execution_id() -> str:
    """Return a fresh per-run execution id (UUID4 string) for ``agent_execution_id``."""
    return str(uuid.uuid4())


def get_logger() -> logging.Logger:
    """Return the module's structured-logging logger (INFO, propagating to root)."""
    return _logger


def build_agent_log_record(
    *,
    agent_execution_id: str,
    agent_mode: str,
    request_id: str,
    input_candidate_count: int,
    recommendation_count: int,
    validation_passed: bool,
    validation_failed_checks: Optional[List[str]] = None,
    retried: bool = False,
    fallback_used: bool = False,
    saved: bool = False,
    crew_id: Optional[str] = None,
) -> AgentLogRecord:
    """Build an :class:`AgentLogRecord` from flow state - counts / ids / flags only.

    Keyword-only and fully explicit: there is no free-form / ``**kwargs`` path, so no
    caller can smuggle PII into the record through this builder (Req 12.2). Callers
    (task 9.2) pass aggregate counts and flags they already hold - for example
    ``recommendation_count=len(output.recommendations)``,
    ``validation_passed=result.valid``,
    ``validation_failed_checks=result.failed_checks()`` - never worker names or phones.

    ``validation_failed_checks`` defaults to an empty list when omitted or ``None``.
    """
    return AgentLogRecord(
        agent_execution_id=agent_execution_id,
        agent_mode=agent_mode,
        request_id=request_id,
        input_candidate_count=input_candidate_count,
        recommendation_count=recommendation_count,
        validation_passed=validation_passed,
        validation_failed_checks=list(validation_failed_checks or []),
        retried=retried,
        fallback_used=fallback_used,
        saved=saved,
        crew_id=crew_id,
    )


def log_agent_execution(
    record: AgentLogRecord, *, logger: Optional[logging.Logger] = None
) -> str:
    """Emit ``record`` as a single structured JSON log line at INFO; return that line.

    The emitted line is exactly ``record.model_dump_json()`` - one JSON object per line so
    CloudWatch Logs Insights can parse it. Accepting only a typed :class:`AgentLogRecord`
    (never a free-form dict) keeps the PII-exclusion guarantee intact (Req 12.2). The line
    is returned so callers / tests can inspect what was written.

    ``logger`` overrides the module logger (useful in tests); production callers omit it.
    """
    line = record.model_dump_json()
    (logger or _logger).info(line)
    return line
