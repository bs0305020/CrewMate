"""Exact dictionary-based certification normalization."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterable, TextIO

from .models import NormalizedCertification
from .text import comparison_key, normalize_text


class CertificationNormalizer:
    """Normalize aliases using the reviewed master file, never vector/fuzzy search."""

    REQUIRED_COLUMNS = {
        "입력표기",
        "정규화자격증명",
        "표기구분",
        "자격유형",
        "자격상태",
        "Q-NetURL",
    }

    def __init__(self, rows: Iterable[dict[str, str]]):
        self._by_key: dict[str, dict[str, str]] = {}
        for row in rows:
            raw = normalize_text(row.get("입력표기"))
            if not raw:
                continue
            key = comparison_key(raw)
            if key in self._by_key and self._by_key[key].get("정규화자격증명") != row.get("정규화자격증명"):
                raise ValueError(f"ambiguous certification alias: {raw}")
            self._by_key[key] = {k: normalize_text(v) for k, v in row.items()}

    @classmethod
    def from_file(cls, path: str | Path) -> "CertificationNormalizer":
        with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
            return cls._from_handle(handle)

    @classmethod
    def _from_handle(cls, handle: TextIO) -> "CertificationNormalizer":
        reader = csv.DictReader(handle)
        columns = set(reader.fieldnames or [])
        missing = cls.REQUIRED_COLUMNS - columns
        if missing:
            raise ValueError(f"normalization master missing columns: {sorted(missing)}")
        return cls(reader)

    def normalize(self, input_name: str) -> NormalizedCertification:
        cleaned = normalize_text(input_name)
        row = self._by_key.get(comparison_key(cleaned))
        if not row:
            return NormalizedCertification(input_name=cleaned, matched=False)
        return NormalizedCertification(
            input_name=cleaned,
            normalized_name=row["정규화자격증명"],
            matched=True,
            notation_type=row.get("표기구분") or None,
            qualification_type=row.get("자격유형") or None,
            qualification_status=row.get("자격상태") or None,
            qnet_url=row.get("Q-NetURL") or None,
        )

    def normalize_many(self, values: Iterable[str]) -> list[NormalizedCertification]:
        return [self.normalize(value) for value in values]
