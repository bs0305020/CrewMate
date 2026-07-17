"""Deterministic fallback report and Markdown rendering."""

from __future__ import annotations

import re
from uuid import uuid4

from ncs_collector.models import (
    AgentReportDraft,
    Citation,
    Confidence,
    EvidenceType,
    ItemEvidence,
    QualificationEvidence,
    RequirementEvidenceResult,
    SpecGapReport,
    StructuredGapAnalysis,
)
from ncs_collector.text import comparison_key


_NCS_CODE = re.compile(r"\s*\(?\b\d{8,12}_\d+(?:v\d+)?\b\)?", re.IGNORECASE)


def _user_facing(value: object) -> str:
    """Remove internal NCS identifiers from prose while preserving structured JSON fields."""
    text = _NCS_CODE.sub("", str(value or ""))
    text = re.sub(r"\(\s*\)", "", text)
    text = re.sub(r"\s+—\s*$", "", text)
    return re.sub(r"[ \t]{2,}", " ", text).strip()


def _qualification_names(report: SpecGapReport) -> list[str]:
    """Return only rule-backed missing/recommended qualifications in display order."""
    names: list[str] = []
    for group in (
        report.missing_core_certification_groups
        + report.recommended_certification_groups
    ):
        names.extend(group.certification_names)
    return list(dict.fromkeys(names))


def _safe_qnet_url(value: str | None) -> str | None:
    if value and re.match(r"^https://(?:www\.)?q-net\.or\.kr(?:/|$)", value, re.IGNORECASE):
        return value
    return None


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
        ncs_codes = [
            str(code)
            for item in result.evidence
            if (code := item.metadata.get("ncs_code") or item.metadata.get("NCS코드"))
        ]
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
        else:
            missing_fields = [
                label for label, value in (
                    ("시행상태", item.status),
                    ("시행기관", item.issuing_organization),
                    ("수행직무", item.duties),
                    ("응시자격", item.eligibility),
                )
                if not value
            ]
            if missing_fields:
                limitations.append(
                    f"{item.normalized_name}: Q-Net 상세정보 일부 미확인({', '.join(missing_fields)})"
                )
                review.append(f"{item.normalized_name}: Q-Net 상세정보 보완 확인 필요")
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


def materialize_agent_report(
    structured: StructuredGapAnalysis,
    draft: AgentReportDraft,
    kb_results: dict[str, RequirementEvidenceResult],
    qnet_results: dict[str, QualificationEvidence],
) -> SpecGapReport:
    """Combine a short Agent narrative with Lambda-owned facts and citations."""
    report = build_fallback_report(structured, kb_results, qnet_results)
    expected = {comparison_key(name): result for name, result in kb_results.items()}
    report.knowledge_base_evidence = [
        ItemEvidence(
            item_name=item.item_name,
            item_type=item.item_type,
            importance=item.importance,
            decision="AUTHORITATIVE_RESULT_UNCHANGED",
            reason=item.reason,
            local_document_ids=item.local_document_ids,
            ncs_codes=item.ncs_codes,
            evidence_types=list(dict.fromkeys(
                evidence.evidence_type
                for evidence in expected.get(
                    comparison_key(item.item_name),
                    RequirementEvidenceResult(status="NOT_RETRIEVED"),
                ).evidence
            )),
            confidence=item.confidence,
            conflicts=item.conflicts,
            limitations=item.limitations,
        )
        for item in draft.knowledge_base_evidence
    ]
    report.conflicts = list(dict.fromkeys(report.conflicts + draft.conflicts))
    report.limitations = list(dict.fromkeys(report.limitations + draft.limitations))
    report.human_review_items = list(
        dict.fromkeys(report.human_review_items + draft.human_review_items)
    )
    return report


def render_markdown(report: SpecGapReport) -> str:
    def names(items, attr="group_name"):
        values = [getattr(item, attr) for item in items]
        return ", ".join(values) if values else "없음"

    lines = [
        "# 지원자 스펙 보완 보고서",
        "",
        "## 1. 종합 의견",
        f"- {report.target_trade} 기준 능력 커버리지: {report.ability_coverage.matched}/{report.ability_coverage.required} ({report.ability_coverage.percentage}%)",
        f"- 부족 핵심 자격그룹: {names(report.missing_core_certification_groups)}",
        "",
        "## 2. 분석 범위",
        f"- {_user_facing(report.analysis_scope)}",
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
        "## 6. 추천 자격증과 취득 안내",
        f"- 우선 확인할 핵심 자격그룹: {names(report.missing_core_certification_groups)}",
        f"- 추가로 고려할 추천 자격그룹: {names(report.recommended_certification_groups)}",
    ]
    qnet_by_name = {
        comparison_key(item.normalized_name): item for item in report.qnet_evidence
    }
    status_labels = {
        "SUCCESS": "공식 정보 확인",
        "NAME_MISMATCH": "자격명 확인 필요",
        "UNAVAILABLE": "현재 확인 불가",
        "FAILED": "확인 실패",
        "NOT_FOUND": "공식 페이지 미확인",
    }
    for certification_name in _qualification_names(report):
        item = qnet_by_name.get(comparison_key(certification_name))
        if item is None:
            lines.append("")
            lines.append(f"### {_user_facing(certification_name)}")
            lines.append("- Q-Net 상세 정보를 불러오지 못했습니다. 자격명을 직접 확인해주세요.")
            continue
        status = status_labels.get(item.fetch_status, "확인 필요")
        safe_url = _safe_qnet_url(item.source_url)
        title = (
            f"[{_user_facing(certification_name)}]({safe_url})"
            if safe_url else _user_facing(certification_name)
        )
        lines.extend(["", f"### {title}"])
        if item.fetch_status != "SUCCESS":
            lines.append(f"- {status}. Q-Net에서 최신 정보를 직접 확인해주세요.")
            continue
        details = (
            ("시행기관", item.issuing_organization),
            ("하는 일", item.duties),
            ("응시자격", item.eligibility),
            ("취득방법", item.acquisition_method or item.exam_information),
            ("시험 일정", item.exam_schedule),
            ("수수료", item.fees),
        )
        for label, value in details:
            if value:
                lines.append(f"- {label}: {_user_facing(value)}")
        if not any(value for _, value in details):
            lines.append("- 자격 상세 내용은 연결된 Q-Net 공식 페이지에서 확인해주세요.")
    if not _qualification_names(report):
        lines.append("- 현재 추가로 추천할 자격증이 없습니다.")
    lines.extend([
        "",
        "## 7. 보유 능력과 부족 능력",
        f"- 보유: {names(report.matched_abilities, 'ability_name')}",
        f"- 부족: {names(report.missing_abilities, 'ability_name')}",
        "",
        "## 8. 우선 보완 계획",
    ])
    lines.extend(
        f"{item.priority}. {_user_facing(item.item_name)} — {_user_facing(item.reason)}"
        for item in report.priority_actions
    )
    lines.extend(["", "## 9. Bedrock Knowledge Base 근거"])
    lines.extend(
        f"- {_user_facing(item.item_name)}: {_user_facing(item.reason)}"
        for item in report.knowledge_base_evidence
    )
    if not report.knowledge_base_evidence:
        lines.append("- 검색 결과 없음")
    lines.extend(["", "## 10. 근거 및 출처"])
    citation_count = 0
    for item in report.citations:
        if item.source_type == "QNET":
            # Q-Net links are placed directly on the recommended certification
            # names above, so do not repeat a raw official-result dump here.
            continue
        elif item.source_url and str(item.source_url).startswith(("http://", "https://")):
            source = f"[원문 보기]({item.source_url})"
        else:
            source = "내부 기준 문서"
        lines.append(f"- {_user_facing(item.item_name)}: {source}")
        citation_count += 1
    if citation_count == 0:
        lines.append("- 연결된 외부 출처 없음")
    lines.extend(["", "## 11. 주의사항과 확인 필요 항목"])
    lines.extend(f"- 충돌: {_user_facing(item)}" for item in report.conflicts)
    lines.extend(f"- 한계: {_user_facing(item)}" for item in report.limitations)
    lines.extend(f"- 확인 필요: {_user_facing(item)}" for item in report.human_review_items)
    return "\n".join(lines).rstrip() + "\n"
