from __future__ import annotations

from pathlib import Path

import pytest

from ncs_collector.certifications import CertificationNormalizer
from ncs_collector.gap_analyzer import analyze_gap
from ncs_collector.models import (
    AbilityRequirement,
    ApplicantSpecInput,
    CertificationGroupRequirement,
)
from ncs_collector.text import comparison_key, normalize_text
from ncs_collector.trade_requirements import LocalRuleRepository

ROOT = Path(__file__).resolve().parents[1]
REPO = LocalRuleRepository(ROOT / "Archive")


def applicant(**overrides):
    payload = {
        "targetTrade": "방수시공",
        "certifications": ["방수 기능사"],
        "abilities": ["도막 방수", "바탕 처리"],
    }
    payload.update(overrides)
    return ApplicantSpecInput.model_validate(payload)


def test_01_certification_alias_normalization():
    item = REPO.certification_normalizer().normalize("도장 기능사")
    assert item.matched and item.normalized_name == "건축도장기능사"


def test_02_unicode_and_whitespace_normalization():
    assert normalize_text("  방수\u3000기능사  ") == "방수 기능사"
    assert comparison_key("바탕 처리") == comparison_key("바탕처리")


def test_03_one_or_more_group_is_satisfied():
    result = analyze_gap(applicant(), REPO)
    assert result.satisfied_certification_groups[0].group_name == "방수 직접 자격"


def test_04_other_certificate_in_satisfied_group_is_not_missing():
    result = analyze_gap(applicant(), REPO)
    assert not result.missing_core_certification_groups
    assert "방수산업기사" not in [action.item_name for action in result.priority_actions]


def test_05_missing_core_group():
    result = analyze_gap(applicant(certifications=[]), REPO)
    assert [group.group_name for group in result.missing_core_certification_groups] == ["방수 직접 자격"]


def test_06_ability_name_and_ncs_code_match():
    result = analyze_gap(
        applicant(abilities=["바탕 처리", "1403020308_14v2"]), REPO
    )
    assert {item.ability_name for item in result.matched_abilities} == {"바탕처리", "도막 방수"}


def test_07_ability_coverage_calculation():
    result = analyze_gap(applicant(), REPO)
    assert result.ability_coverage.matched == 2
    assert result.ability_coverage.required == 11
    assert result.ability_coverage.percentage == 18.18


class _SpecialtyRepo:
    def certification_normalizer(self):
        return CertificationNormalizer(
            [{
                "입력표기": "피복아크용접기능사",
                "정규화자격증명": "피복아크용접기능사",
                "표기구분": "표준명",
                "자격유형": "국가기술자격",
                "자격상태": "현행",
                "Q-NetURL": "",
            }]
        )

    def certification_groups(self, target_trade):
        return [CertificationGroupRequirement(
            target_trade=target_trade,
            group_name="용접 자격",
            importance="핵심",
            selection_rule="하나 이상",
            certification_names=["피복아크용접기능사"],
        )]

    def abilities(self, target_trade):
        return [AbilityRequirement(
            target_trade=target_trade,
            ncs_code="NCS-1",
            ability_name="용접 작업",
            ncs_subcategory="용접",
        )]


def test_08_missing_target_specialty_requires_review():
    value = ApplicantSpecInput.model_validate({
        "targetTrade": "용접시공",
        "certifications": [],
        "abilities": [],
    })
    result = analyze_gap(value, _SpecialtyRepo())
    assert any("targetSpecialty" in item for item in result.human_review_items)
    assert any("확정 판단하지 않았다" in item for item in result.limitations)


def test_unknown_selection_rule_is_rejected():
    repo = _SpecialtyRepo()
    original = repo.certification_groups
    repo.certification_groups = lambda trade: [original(trade)[0].model_copy(update={"selection_rule": "임의"})]
    with pytest.raises(ValueError):
        analyze_gap(ApplicantSpecInput.model_validate({"targetTrade": "용접", "certifications": [], "abilities": []}), repo)
