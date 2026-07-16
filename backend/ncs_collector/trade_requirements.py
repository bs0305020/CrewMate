"""Repositories for authoritative structured rule files."""

from __future__ import annotations

import csv
from collections import OrderedDict
from pathlib import Path
from typing import Protocol

from .certifications import CertificationNormalizer
from .models import AbilityRequirement, CertificationGroupRequirement
from .text import comparison_key, normalize_text


class TradeNotFoundError(LookupError):
    pass


class RuleRepository(Protocol):
    def certification_normalizer(self) -> CertificationNormalizer: ...
    def certification_groups(self, target_trade: str) -> list[CertificationGroupRequirement]: ...
    def abilities(self, target_trade: str) -> list[AbilityRequirement]: ...


class LocalRuleRepository:
    """Read complete authoritative CSV files from a local directory."""

    NORMALIZATION_FILE = "자격증_정규화_마스터.csv"
    CERTIFICATION_FILE = "직종별_자격요건.csv"
    ABILITY_FILE = "직종별_능력요건.csv"

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self._normalizer: CertificationNormalizer | None = None

    def certification_normalizer(self) -> CertificationNormalizer:
        if self._normalizer is None:
            self._normalizer = CertificationNormalizer.from_file(self.root / self.NORMALIZATION_FILE)
        return self._normalizer

    @staticmethod
    def _rows(path: Path) -> list[dict[str, str]]:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return [{k: normalize_text(v) for k, v in row.items()} for row in csv.DictReader(handle)]

    def certification_groups(self, target_trade: str) -> list[CertificationGroupRequirement]:
        trade_key = comparison_key(target_trade)
        grouped: OrderedDict[tuple[str, str, str], list[str]] = OrderedDict()
        matched_trade = None
        for row in self._rows(self.root / self.CERTIFICATION_FILE):
            if comparison_key(row.get("직종")) != trade_key:
                continue
            matched_trade = row["직종"]
            key = (row["자격그룹"], row["중요도"], row["선택규칙"])
            grouped.setdefault(key, []).append(row["자격증명"])
        if matched_trade is None:
            raise TradeNotFoundError(target_trade)
        return [
            CertificationGroupRequirement(
                target_trade=matched_trade,
                group_name=group,
                importance=importance,
                selection_rule=rule,
                certification_names=list(dict.fromkeys(names)),
            )
            for (group, importance, rule), names in grouped.items()
        ]

    def abilities(self, target_trade: str) -> list[AbilityRequirement]:
        trade_key = comparison_key(target_trade)
        results = [
            AbilityRequirement(
                target_trade=row["직종"],
                ncs_code=row["NCS코드"],
                ability_name=row["능력명"],
                ncs_subcategory=row["NCS세분류"],
            )
            for row in self._rows(self.root / self.ABILITY_FILE)
            if comparison_key(row.get("직종")) == trade_key
        ]
        if not results:
            raise TradeNotFoundError(target_trade)
        return results
