"""Deterministic fallback report and Markdown rendering."""

from __future__ import annotations

from uuid import uuid4

from ncs_collector.models import (
    Citation,
    Confidence,
    EvidenceType,
    ItemEvidence,
    QualificationEvidence,
    RequirementEvidenceResult,
    SpecGapReport,
    StructuredGapAnalysis,
)


def build_fallback_report(
    structured: StructuredGapAnalysis,
    kb_results: dict[str, RequirementEvidenceResult],
    qnet_results: dict[str, QualificationEvidence],
    *,
    report_id: str | None = None,
    extra_limitations: list[str] | None = None,
) -> SpecGapReport:
    item_evidence: list[ItemEvidence] = []
    citations: list[Citation] = []
    limitations = list(structured.limitations)
    review = list(structured.human_review_items)
    conflicts: list[str] = []

    for item_name, result in kb_results.items():
        ids = [item.document_id for item in result.evidence if item.document_id]
        ncs_codes = [str(item.metadata.get("ncs_code")) for item in result.evidence if item.metadata.get("ncs_code")]
        evidence_types = list(dict.fromkeys(item.evidence_type for item in result.evidence))
        if result.status != "SUCCESS" or not result.evidence:
            reason = result.error or "검색 근거를 확인하지 못했다."
            limitations.append(f"{item_name}: {reason}")
            review.append(f"{item_name}: Knowledge Base 근거 확인 필요")
            confidence = Confidence.HUMAN_REVIEW_REQUIRED
        else:
            reason = "구조화 판정에 연결되는 검색 근거를 확인했다."
            confidence = Confidence.REFERENCE
        item_evidence.append(
            ItemEvidence(
                item_name=item_name,
                item_type="ABILITY" if ncs_codes else "CERTIFICATION",
                decision="AUTHORITATIVE_RESULT_UNCHANGED",
                reason=reason,
                local_document_ids=ids,
                ncs_codes=list(dict.fromkeys(ncs_codes)),
                evidence_types=evidence_types or [EvidenceType.STRUCTURED_DATA],
                confidence=confidence,
            )
        )
        for item in result.evidence:
            citations.append(
                Citation(
                    item_name=item_name,
                    source_type=item.evidence_type,
                    document_id=item.document_id,
                    source_url=item.source_location,
                )
            )

    qnet_values = list(qnet_results.values())
    for item in qnet_values:
        if item.fetch_status == "NAME_MISMATCH":
            conflicts.append(f"{item.normalized_name}: Q-Net 반환 페이지와 요청 자격명이 일치하지 않는다.")
            review.append(f"{item.normalized_name}: Q-Net 자격명 불일치 확인 필요")
        elif item.fetch_status != "SUCCESS":
            limitations.append(f"{item.normalized_name}: Q-Net 확인 실패({item.fetch_status})")
            review.append(f"{item.normalized_name}: Q-Net 공식 정보 확인 필요")
        if item.source_url:
            citations.append(
                Citation(
                    item_name=item.normalized_name,
                    source_type="QNET",
                    source_url=item.source_url,
                    checked_at=item.checked_at,
                )
            )

    return SpecGapReport(
        report_id=report_id or f"spec-{uuid4().hex}",
        target_trade=structured.target_trade,
        target_specialty=structured.target_specialty,
        analysis_scope=structured.analysis_scope,
        normalized_certifications=structured.normalized_certifications,
        satisfied_certification_groups=structured.satisfied_certification_groups,
        missing_core_certification_groups=structured.missing_core_certification_groups,
        recommended_certification_groups=structured.recommended_certification_groups,
        ability_coverage=structured.ability_coverage,
        matched_abilities=structured.matched_abilities,
        missing_abilities=structured.missing_abilities,
        priority_actions=structured.priority_actions,
        knowledge_base_evidence=item_evidence,
        qnet_evidence=qnet_values,
        citations=citations,
        conflicts=list(dict.fromkeys(conflicts)),
        limitations=list(dict.fromkeys(limitations + list(extra_limitations or []))),
        human_review_items=list(dict.fromkeys(review)),
    )


def render_markdown(report: SpecGapReport) -> str:
    def names(items, attr="group_name"):
        values = [getattr(item, attr) for item in items]
        return ", ".join(values) if values else "없음"

    lines = [
        f"# 지원자 스펙 Gap 보고서 ({report.report_id})",
        "",
        "## 1. 종합 의견",
        f"- {report.target_trade} 기준 능력 커버리지: {report.ability_coverage.matched}/{report.ability_coverage.required} ({report.ability_coverage.percentage}%)",
        f"- 부족 핵심 자격그룹: {names(report.missing_core_certification_groups)}",
        "",
        "## 2. 분석 범위",
        f"- {report.analysis_scope}",
        f"- 세부 작업: {report.target_specialty or '미지정'}",
        "",
        "## 3. 지원자 보유 스펙",
        f"- 정규화 자격: {', '.join(item.normalized_name or item.input_name for item in report.normalized_certifications) or '없음'}",
        f"- 매칭 능력: {names(report.matched_abilities, 'ability_name')}",
        "",
        "## 4. 충족한 자격 요건",
        f"- {names(report.satisfied_certification_groups)}",
        "",
        "## 5. 부족한 핵심 자격 요건",
        f"- {names(report.missing_core_certification_groups)}",
        "",
        "## 6. 추천 자격",
        f"- {names(report.recommended_certification_groups)}",
        "",
        "## 7. 보유 능력과 부족 능력",
        f"- 보유: {names(report.matched_abilities, 'ability_name')}",
        f"- 부족: {names(report.missing_abilities, 'ability_name')}",
        "",
        "## 8. 우선 보완 계획",
    ]
    lines.extend(f"{item.priority}. {item.item_name} — {item.reason}" for item in report.priority_actions)
    lines.extend(["", "## 9. Q-Net 공식 확인 결과"])
    lines.extend(
        f"- {item.normalized_name}: {item.fetch_status} ({item.source_url or 'URL 없음'}, {item.checked_at or '확인 시각 없음'})"
        for item in report.qnet_evidence
    )
    if not report.qnet_evidence:
        lines.append("- 확인 결과 없음")
    lines.extend(["", "## 10. Bedrock Knowledge Base 근거"])
    lines.extend(f"- {item.item_name}: {item.reason}" for item in report.knowledge_base_evidence)
    if not report.knowledge_base_evidence:
        lines.append("- 검색 결과 없음")
    lines.extend(["", "## 11. 근거 및 출처"])
    lines.extend(
        f"- {item.item_name}: {item.source_type} / {item.document_id or '-'} / {item.source_url or '-'}"
        for item in report.citations
    )
    if not report.citations:
        lines.append("- 연결된 외부 출처 없음")
    lines.extend(["", "## 12. 주의사항과 확인 필요 항목"])
    lines.extend(f"- 충돌: {item}" for item in report.conflicts)
    lines.extend(f"- 한계: {item}" for item in report.limitations)
    lines.extend(f"- 확인 필요: {item}" for item in report.human_review_items)
    return "\n".join(lines).rstrip() + "\n"
