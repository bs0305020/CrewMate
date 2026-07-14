"""Pydantic input/output schemas for the Crew Composition Agent (담당자 B).

This module is the canonical I/O contract shared by BOTH Lambda function packages
(``backend/functions/agent_invoke`` and ``backend/functions/gap_event``). During
local dev/test it resolves from the workspace root on ``sys.path`` (see the
``pythonpath`` setting in ``pyproject.toml`` and the root ``conftest.py``), standing
in for the AWS Lambda Layer packaging used in deployment.

Design references
-----------------
- ``design.md`` → "Data Models" → "Agent 입력 스키마" / "Agent 출력 스키마".
- ``requirements.md`` → Requirement 2 (Agent 입력/출력 스키마).

Strict parsing (Requirement 2.5 / 2.6 / 2.7, task 1.2)
------------------------------------------------------
Every schema uses ``strict=True`` and ``extra="forbid"`` so that JSON with mixed
text / extra keys, missing required fields, or wrong-typed values is rejected at
*parse time* instead of being silently coerced. Agent output that does not conform
to :class:`AgentOutput` therefore fails to parse, which the invoke Lambda treats as
a validation failure (no lax coercion, no dropped/added fields).

Python 3.9 note
---------------
``from __future__ import annotations`` keeps all annotations lazy so the design's
builtin-generic annotation style (``list[...]``) resolves cleanly on the local
Python 3.9 runtime. Models are declared in dependency order so each lazy forward
reference resolves against an already-defined class at model-build time.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "Priority",
    "TradeRequirement",
    "RequestSpec",
    "Candidate",
    "FixedMember",
    "CollaborationPair",
    "AgentInput",
    "Recommendation",
    "AgentOutput",
]


class _StrictModel(BaseModel):
    """Base model enforcing strict, closed-world parsing for every schema.

    - ``strict=True``   → no lax type coercion. ``"5"`` or ``5.0`` are not accepted
      for an ``int`` field; ``123`` is not accepted for a ``str`` field.
    - ``extra="forbid"`` → unknown/extra keys are rejected, guarding against mixed
      text or hallucinated fields leaking through Agent output parsing.
    """

    model_config = ConfigDict(strict=True, extra="forbid")


# --------------------------------------------------------------------------- #
# Agent input schema (design.md → "Agent 입력 스키마")                          #
# --------------------------------------------------------------------------- #
class Priority(_StrictModel):
    """Request priority weights across the three composition factors."""

    cost: Literal["LOW", "MEDIUM", "HIGH"]
    skill: Literal["LOW", "MEDIUM", "HIGH"]
    teamwork: Literal["LOW", "MEDIUM", "HIGH"]


class TradeRequirement(_StrictModel):
    """A required trade and the headcount needed for it."""

    trade: str  # FORMWORK | REBAR | MASONRY | MATERIAL_CARRY | GENERAL ...
    count: int = Field(gt=0)  # 필요 인원 (> 0)


class RequestSpec(_StrictModel):
    """The work request conditions the Agent composes against."""

    request_id: str
    required_workers: list[TradeRequirement]
    budget: int = Field(gt=0)
    priority: Priority
    site: str
    work_date: str  # ISO8601 date
    start_time: str


class Candidate(_StrictModel):
    """A READY candidate worker eligible for composition."""

    worker_id: str
    trade: str
    skill_level: int = Field(ge=1, le=5)  # 1 ~ 5
    desired_daily_wage: int = Field(gt=0)
    certifications: list[str] = []
    career_years: int


class FixedMember(_StrictModel):
    """A retained RUNNING member kept in place during EMERGENCY re-composition."""

    worker_id: str
    trade: str
    desired_daily_wage: int = Field(gt=0)  # total_cost 계산 일관성을 위해 포함


class CollaborationPair(_StrictModel):
    """How many times two workers have previously collaborated."""

    worker_a: str
    worker_b: str
    count: int = Field(gt=0)


class AgentInput(_StrictModel):
    """The full Agent input payload for a single compose run.

    ``fixed_members`` is populated only in EMERGENCY mode; ``collaboration_pairs``
    defaults to empty when no shared history is available.
    """

    mode: Literal["NORMAL", "EMERGENCY"]
    request: RequestSpec
    fixed_members: list[FixedMember] = []  # EMERGENCY 에서만 채워짐
    candidates: list[Candidate]
    collaboration_pairs: list[CollaborationPair] = []


# --------------------------------------------------------------------------- #
# Agent output schema (design.md → "Agent 출력 스키마")                         #
# --------------------------------------------------------------------------- #
class Recommendation(_StrictModel):
    """One crew recommendation produced by the Agent."""

    rank: int
    member_ids: list[str]
    total_cost: int
    reason: str
    considerations: list[str]


class AgentOutput(_StrictModel):
    """The Agent output payload.

    The recommendation count (1~3) and every rule-compliance constraint are
    enforced downstream by the code validator (``validator.py``, task 3.x), not by
    this schema. This schema guarantees only structural/type conformance.
    """

    mode: Literal["NORMAL", "EMERGENCY"]
    request_id: str
    recommendations: list[Recommendation]
