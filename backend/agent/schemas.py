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

from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = [
    "Priority",
    "TradeRequirement",
    "RequestSpec",
    "Candidate",
    "FixedMember",
    "CollaborationPair",
    "AgentInput",
    "RecommendedMember",
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
    """Request priority ranking across the three composition factors.

    Each axis holds its rank: 1 = 최우선(highest priority), 3 = 최하위(lowest).
    The three ranks must be a permutation of 1/2/3 (each used exactly once).
    """

    cost: int = Field(ge=1, le=3)
    career: int = Field(ge=1, le=3)
    teamwork: int = Field(ge=1, le=3)

    @model_validator(mode="after")
    def _ranks_are_distinct(self) -> "Priority":
        if sorted((self.cost, self.career, self.teamwork)) != [1, 2, 3]:
            raise ValueError(
                "priority ranks must be a permutation of 1, 2, 3 "
                "(cost/career/teamwork each assigned a distinct rank)"
            )
        return self


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
    """A READY candidate worker eligible for composition (계약 v2)."""

    worker_id: str
    preferred_trades: list[str] = []
    excluded_trades: list[str] = []
    desired_daily_wage: int = Field(gt=0)
    certifications: list[str] = []
    career_years: int


class FixedMember(_StrictModel):
    """A retained member kept in place during EMERGENCY re-composition."""

    worker_id: str
    assigned_trade: str
    offered_wage: int = Field(gt=0)  # total_cost 계산 일관성을 위해 포함


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
class RecommendedMember(_StrictModel):
    """One assigned member in a recommendation (계약 v2: assigned_trade 포함)."""

    worker_id: str
    assigned_trade: str
    offered_wage: int = Field(gt=0)


class Recommendation(_StrictModel):
    """One crew recommendation produced by the Agent."""

    rank: int
    members: list[RecommendedMember]
    total_cost: int
    reason: str
    considerations: list[str]

    @property
    def member_ids(self) -> list[str]:
        return [m.worker_id for m in self.members]


class AgentOutput(_StrictModel):
    """The Agent output payload.

    The recommendation count (1~3) and every rule-compliance constraint are
    enforced downstream by the code validator (``validator.py``, task 3.x), not by
    this schema. This schema guarantees only structural/type conformance.
    """

    mode: Literal["NORMAL", "EMERGENCY"]
    request_id: str
    recommendations: list[Recommendation]
