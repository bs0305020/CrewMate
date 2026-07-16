"""Post-agent validation against the authoritative deterministic result."""

from __future__ import annotations

from ncs_collector.models import SpecGapReport, StructuredGapAnalysis
from ncs_collector.text import comparison_key


class ReportValidationError(ValueError):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


_AUTHORITATIVE_FIELDS = (
    "target_trade",
    "target_specialty",
    "analysis_scope",
    "normalized_certifications",
    "satisfied_certification_groups",
    "missing_core_certification_groups",
    "recommended_certification_groups",
    "ability_coverage",
    "matched_abilities",
    "missing_abilities",
    "priority_actions",
)


def validate_report(report: SpecGapReport, structured: StructuredGapAnalysis) -> None:
    errors: list[str] = []
    for field in _AUTHORITATIVE_FIELDS:
        if getattr(report, field) != getattr(structured, field):
            errors.append(f"authoritative field changed: {field}")

    satisfied = {comparison_key(item.group_name) for item in structured.satisfied_certification_groups}
    missing = {comparison_key(item.group_name) for item in report.missing_core_certification_groups}
    if satisfied & missing:
        errors.append("a satisfied certification group was reported as missing")

    allowed_names = {comparison_key(structured.target_trade)}
    for item in structured.normalized_certifications:
        allowed_names.add(comparison_key(item.input_name))
        if item.normalized_name:
            allowed_names.add(comparison_key(item.normalized_name))
    for group in (
        structured.satisfied_certification_groups
        + structured.missing_core_certification_groups
        + structured.recommended_certification_groups
    ):
        allowed_names.add(comparison_key(group.group_name))
        allowed_names.update(comparison_key(name) for name in group.certification_names)
    for ability in structured.matched_abilities + structured.missing_abilities:
        allowed_names.add(comparison_key(ability.ability_name))
        allowed_names.add(comparison_key(ability.ncs_code))

    for item in report.knowledge_base_evidence:
        if comparison_key(item.item_name) not in allowed_names:
            errors.append(f"invented evidence item: {item.item_name}")
    for item in report.qnet_evidence:
        if comparison_key(item.normalized_name) not in allowed_names:
            errors.append(f"invented Q-Net qualification: {item.normalized_name}")
        if item.fetch_status == "SUCCESS" and (not item.source_url or not item.checked_at):
            errors.append(f"official Q-Net claim lacks URL/time: {item.normalized_name}")
    for citation in report.citations:
        if citation.source_type in {"BEDROCK_KB", "LOCAL_KEYWORD"} and not (citation.document_id or citation.source_url):
            errors.append(f"KB/local citation lacks document id/location: {citation.item_name}")

    mismatch_names = {item.normalized_name for item in report.qnet_evidence if item.fetch_status == "NAME_MISMATCH"}
    if mismatch_names and not any(any(name in review for name in mismatch_names) for review in report.human_review_items):
        errors.append("Q-Net name mismatch is not recorded for human review")
    if errors:
        raise ReportValidationError(errors)


def missing_evidence_items(report: SpecGapReport, evidence_plan: list[dict]) -> list[dict]:
    cited = {comparison_key(item.item_name) for item in report.knowledge_base_evidence}
    qnet = {comparison_key(item.normalized_name) for item in report.qnet_evidence}
    missing: list[dict] = []
    for item in evidence_plan:
        name = comparison_key(item["itemName"])
        if item["action"] == "KB" and name not in cited:
            missing.append(item)
        if item["action"] == "QNET" and name not in qnet:
            missing.append(item)
    return missing
