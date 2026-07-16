"""Unicode and comparison normalization shared by the rules layer."""

from __future__ import annotations

import re
import unicodedata

_WHITESPACE = re.compile(r"\s+")
_COMPARISON_IGNORED = re.compile(r"[\s\-·ㆍ_/()]+")


def normalize_text(value: str | None) -> str:
    """Return NFKC-normalized text with trimmed, collapsed whitespace."""
    if value is None:
        return ""
    return _WHITESPACE.sub(" ", unicodedata.normalize("NFKC", str(value))).strip()


def comparison_key(value: str | None) -> str:
    """Build a conservative exact-lookup key; this is not fuzzy/vector matching."""
    return _COMPARISON_IGNORED.sub("", normalize_text(value)).casefold()
