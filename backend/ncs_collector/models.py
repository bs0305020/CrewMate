"""Strict input, deterministic analysis, evidence, and report contracts."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _camel(name: str) -> str:
    head, *tail = name.split("_")
    return head + "".join(part.capitalize() for part in tail)


class StrictModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=_camel,
        populate_by_name=True,
        extra="forbid",
        str_strip_whitespace=True,
        use_enum_values=True,
    )


class Experience(StrictModel):
    work: str = Field(min_length=1, max_length=200)
    months: int = Field(ge=0, le=1200)


class ApplicantSpecInput(StrictModel):
    target_trade: str = Field(min_length=1, max_length=200)
    certifications: list[str] = Field(default_factory=list, max_length=100)
    abilities: list[str] = Field(default_factory=list, max_length=300)
    target_specialty: str | None = Field(default=None, max_length=300)
    job_posting_text: str | None = Field(default=None, max_length=50000)
    experience: list[Experience] = Field(default_factory=list, max_length=100)
    education: list[str] = Field(default_factory=list, max_length=100)
    persist_report: bool = False

    @field_validator("certifications", "abilities", "education")
    @classmethod
    def reject_blank_items(cls, values: list[str]) -> list[str]:
        if any(not str(value).strip() for value in values):
            raise ValueError("blank list items are not allowed")
        return values


class NormalizedCertification(StrictModel):
    input_name: str
    normalized_name: str | None = None
    matched: bool
    notation_type: str | None = None
    qualification_type: str | None = None
    qualification_status: str | None = None
    qnet_url: str | None = None


class CertificationGroupRequirement(StrictModel):
    target_trade: str
    group_name: str
    importance: str
    selection_rule: str
    certification_names: list[str]


class AbilityRequirement(StrictModel):
    target_trade: str
    ncs_code: str
    ability_name: str
    ncs_subcategory: str


class CertificationGroupDecision(StrictModel):
    group_name: str
    importance: str
    selection_rule: str
    certification_names: list[str]
    matched_certifications: list[str]
    satisfied: bool


class AbilityDecision(StrictModel):
    ability_name: str
    ncs_code: str
    ncs_subcategory: str
    matched: bool
    matched_input: str | None = None


class AbilityCoverage(StrictModel):
    matched: int
    required: int
    percentage: float


class PriorityAction(StrictModel):
    priority: int
    item_name: str
    item_type: str
    reason: str


class StructuredGapAnalysis(StrictModel):
    target_trade: str
    target_specialty: str | None = None
    analysis_scope: str
    normalized_certifications: list[NormalizedCertification]
    satisfied_certification_groups: list[CertificationGroupDecision]
    missing_core_certification_groups: list[CertificationGroupDecision]
    recommended_certification_groups: list[CertificationGroupDecision]
    matched_abilities: list[AbilityDecision]
    missing_abilities: list[AbilityDecision]
    ability_coverage: AbilityCoverage
    priority_actions: list[PriorityAction]
    human_review_items: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class EvidenceType(str, Enum):
    STRUCTURED_DATA = "STRUCTURED_DATA"
    BEDROCK_KB = "BEDROCK_KB"
    QNET = "QNET"
    STRUCTURED_AND_KB = "STRUCTURED_AND_KB"
    STRUCTURED_KB_AND_QNET = "STRUCTURED_KB_AND_QNET"
    LOCAL_KEYWORD = "LOCAL_KEYWORD"


class Confidence(str, Enum):
    CONFIRMED = "CONFIRMED"
    HIGH = "HIGH"
    REFERENCE = "REFERENCE"
    HUMAN_REVIEW_REQUIRED = "HUMAN_REVIEW_REQUIRED"


class Evidence(StrictModel):
    text: str
    score: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    document_id: str | None = None
    source_location: str | None = None
    evidence_type: EvidenceType


class RequirementEvidenceResult(StrictModel):
    status: str
    evidence: list[Evidence] = Field(default_factory=list)
    error: str | None = None


class QualificationEvidence(StrictModel):
    normalized_name: str
    official_name: str | None = None
    status: str | None = None
    issuing_organization: str | None = None
    duties: str | None = None
    eligibility: str | None = None
    acquisition_method: str | None = None
    exam_schedule: str | None = None
    fees: str | None = None
    # Backward-compatible alias for older cached/report payloads. New rendering
    # uses acquisition_method, exam_schedule, and fees explicitly.
    exam_information: str | None = None
    source_url: str | None = None
    checked_at: str | None = None
    fetch_status: str
    error: str | None = None
    from_cache: bool = False


class ItemEvidence(StrictModel):
    item_name: str
    item_type: str
    importance: str | None = None
    decision: str
    reason: str
    local_document_ids: list[str] = Field(default_factory=list)
    ncs_codes: list[str] = Field(default_factory=list)
    qnet_official_url: str | None = None
    qnet_checked_at: str | None = None
    evidence_types: list[EvidenceType] = Field(default_factory=list)
    confidence: Confidence
    conflicts: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class AgentItemEvidenceDraft(StrictModel):
    """Narrative-only KB evidence fields that the LLM is allowed to author."""

    item_name: str
    item_type: str
    importance: str | None = None
    reason: str
    local_document_ids: list[str] = Field(default_factory=list)
    ncs_codes: list[str] = Field(default_factory=list)
    confidence: Confidence
    conflicts: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class AgentReportDraft(StrictModel):
    """Short LLM-authored overlay; authoritative and source fields are Lambda-owned."""

    knowledge_base_evidence: list[AgentItemEvidenceDraft] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    human_review_items: list[str] = Field(default_factory=list)


class Citation(StrictModel):
    item_name: str
    source_type: str
    document_id: str | None = None
    source_url: str | None = None
    checked_at: str | None = None


class SpecGapReport(StrictModel):
    report_id: str
    target_trade: str
    target_specialty: str | None = None
    analysis_scope: str
    normalized_certifications: list[NormalizedCertification]
    satisfied_certification_groups: list[CertificationGroupDecision]
    missing_core_certification_groups: list[CertificationGroupDecision]
    recommended_certification_groups: list[CertificationGroupDecision]
    ability_coverage: AbilityCoverage
    matched_abilities: list[AbilityDecision]
    missing_abilities: list[AbilityDecision]
    priority_actions: list[PriorityAction]
    knowledge_base_evidence: list[ItemEvidence] = Field(default_factory=list)
    qnet_evidence: list[QualificationEvidence] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    human_review_items: list[str] = Field(default_factory=list)
    generated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
