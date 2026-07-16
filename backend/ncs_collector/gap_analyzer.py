"""Authoritative, deterministic certification-group and NCS ability analysis."""

from __future__ import annotations

from .models import (
    AbilityCoverage,
    AbilityDecision,
    ApplicantSpecInput,
    CertificationGroupDecision,
    PriorityAction,
    StructuredGapAnalysis,
)
from .text import comparison_key, normalize_text
from .trade_requirements import RuleRepository

_CORE_IMPORTANCE = {"핵심", "필수"}
_SPECIALTY_SENSITIVE_TERMS = ("용접", "건설기계", "굴착", "크레인", "배관")


def _group_satisfied(selection_rule: str, required: list[str], held: set[str]) -> tuple[bool, list[str]]:
    matches = [name for name in required if comparison_key(name) in held]
    rule = comparison_key(selection_rule)
    if rule in {comparison_key("하나 이상"), comparison_key("1개 이상")}:
        return bool(matches), matches
    if rule in {comparison_key("모두"), comparison_key("전체")}:
        return len(matches) == len(required), matches
    raise ValueError(f"unsupported selection rule: {selection_rule}")


def analyze_gap(applicant: ApplicantSpecInput, repository: RuleRepository) -> StructuredGapAnalysis:
    target_trade = normalize_text(applicant.target_trade)
    normalizer = repository.certification_normalizer()
    normalized = normalizer.normalize_many(applicant.certifications)
    held = {comparison_key(item.normalized_name) for item in normalized if item.matched and item.normalized_name}

    group_decisions: list[CertificationGroupDecision] = []
    for group in repository.certification_groups(target_trade):
        satisfied, matches = _group_satisfied(group.selection_rule, group.certification_names, held)
        group_decisions.append(
            CertificationGroupDecision(
                group_name=group.group_name,
                importance=group.importance,
                selection_rule=group.selection_rule,
                certification_names=group.certification_names,
                matched_certifications=matches,
                satisfied=satisfied,
            )
        )

    input_abilities = {comparison_key(value): normalize_text(value) for value in applicant.abilities}
    ability_decisions: list[AbilityDecision] = []
    for requirement in repository.abilities(target_trade):
        by_name = input_abilities.get(comparison_key(requirement.ability_name))
        by_code = input_abilities.get(comparison_key(requirement.ncs_code))
        matched_input = by_name or by_code
        ability_decisions.append(
            AbilityDecision(
                ability_name=requirement.ability_name,
                ncs_code=requirement.ncs_code,
                ncs_subcategory=requirement.ncs_subcategory,
                matched=matched_input is not None,
                matched_input=matched_input,
            )
        )

    satisfied_groups = [group for group in group_decisions if group.satisfied]
    missing_core = [
        group for group in group_decisions
        if not group.satisfied and group.importance in _CORE_IMPORTANCE
    ]
    recommended = [group for group in group_decisions if group.importance not in _CORE_IMPORTANCE]
    matched_abilities = [item for item in ability_decisions if item.matched]
    missing_abilities = [item for item in ability_decisions if not item.matched]
    required = len(ability_decisions)
    percentage = round((len(matched_abilities) / required * 100), 2) if required else 0.0

    actions: list[PriorityAction] = []
    for group in missing_core:
        actions.append(PriorityAction(
            priority=len(actions) + 1,
            item_name=group.group_name,
            item_type="CERTIFICATION_GROUP",
            reason=f"핵심 자격그룹 미충족: {group.selection_rule}",
        ))
    for ability in missing_abilities:
        actions.append(PriorityAction(
            priority=len(actions) + 1,
            item_name=ability.ability_name,
            item_type="ABILITY",
            reason=f"직종 요구 NCS 능력 보완 ({ability.ncs_code})",
        ))
    for group in recommended:
        if not group.satisfied:
            actions.append(PriorityAction(
                priority=len(actions) + 1,
                item_name=group.group_name,
                item_type="RECOMMENDED_CERTIFICATION_GROUP",
                reason="추천·관련 자격그룹 검토",
            ))

    scope = "채용공고 기준 포함 분석" if applicant.job_posting_text else "일반적인 직종 기준 분석"
    review_items: list[str] = []
    limitations = ["자격 및 능력 판정은 제공된 구조화 규칙의 현재 버전을 기준으로 한다."]
    if not applicant.job_posting_text:
        limitations.append("채용공고가 제공되지 않아 개별 공고의 요구사항은 반영하지 않았다.")
    if not applicant.target_specialty and any(term in target_trade for term in _SPECIALTY_SENSITIVE_TERMS):
        review_items.append("세부 작업(targetSpecialty)이 없어 세부 작업별 자격 요건은 확인이 필요하다.")
        limitations.append("세부 작업에 따라 자격이 달라질 수 있어 확정 판단하지 않았다.")

    return StructuredGapAnalysis(
        target_trade=target_trade,
        target_specialty=normalize_text(applicant.target_specialty) or None,
        analysis_scope=scope,
        normalized_certifications=normalized,
        satisfied_certification_groups=satisfied_groups,
        missing_core_certification_groups=missing_core,
        recommended_certification_groups=recommended,
        matched_abilities=matched_abilities,
        missing_abilities=missing_abilities,
        ability_coverage=AbilityCoverage(
            matched=len(matched_abilities), required=required, percentage=percentage
        ),
        priority_actions=actions,
        human_review_items=review_items,
        limitations=limitations,
    )
