"""``get_worker_history`` - read-only Agent tool (Requirement 5.4).

Returns a **limited** work/collaboration history for the given workers - only the
judgment fields the Agent needs (collaboration counts, completed-job counts, and
similar neutral work signals). Full personal information (name, phone, etc.) is
**never** returned (Requirement 5.4), and negative operational data such as
``no_show_count`` is deliberately withheld to keep the Agent's reasoning inputs
work-focused and neutral (Requirement 3.2 / 3.3).

Backed solely by 담당자 A's read-only helpers ``db.get_worker_collaborations`` and
``db.get_workers`` - no write/transition helper is called.

Privacy design (Requirement 5.4)
---------------------------------
Rather than passing 담당자 A's full worker records through, the projection uses an
**allowlist** (:data:`_JUDGMENT_FIELDS`): only explicitly permitted judgment fields
are copied out. This is fail-closed - if 담당자 A adds new (possibly personal)
fields to a worker record later, they are excluded by default instead of leaking.
:data:`_EXCLUDED_FIELDS` documents representative PII / negative-signal fields the
allowlist intentionally drops.
"""
from __future__ import annotations

from typing import Any, Dict, List, Sequence

from ._shared import resolve_db, tool

# Allowlisted judgment fields copied verbatim from each worker record. Anything
# not listed here (notably PII and negative operational data) is dropped.
_JUDGMENT_FIELDS = (
    "trade",
    "skill_level",
    "career_years",
    "certifications",
    "completed_job_count",  # 완료 건수 - a positive/neutral work signal
)

# Documentation-only: representative fields the allowlist intentionally excludes.
# Enforcement is by the allowlist above, not by this set, so unknown future fields
# are excluded by default (fail-closed).
_EXCLUDED_FIELDS = frozenset(
    {
        # Personal information (Requirement 5.4)
        "name",
        "phone",
        "phone_number",
        "email",
        "address",
        "birth_date",
        "resident_registration_number",
        # Negative operational data (Requirement 3.2 / 3.3)
        "no_show_count",
    }
)


def _collaboration_count_by_worker(
    worker_ids: Sequence[str], pairs: List[Dict[str, Any]]
) -> Dict[str, int]:
    """Sum each worker's total collaboration count from the returned pairs.

    Provides a compact "협업 횟수" summary per worker in addition to the raw pair
    list, so the Agent can weigh teamwork without inspecting personal data.
    """
    totals: Dict[str, int] = {wid: 0 for wid in worker_ids}
    for pair in pairs:
        count = pair.get("count", 0) or 0
        for endpoint_key in ("worker_a", "worker_b"):
            wid = pair.get(endpoint_key)
            if wid in totals:
                totals[wid] += count
    return totals


def _project_worker(record: Dict[str, Any], collaboration_count: int) -> Dict[str, Any]:
    """Copy only allowlisted judgment fields out of a full worker record."""
    limited: Dict[str, Any] = {"worker_id": record.get("worker_id")}
    for field in _JUDGMENT_FIELDS:
        if field in record:
            limited[field] = record[field]
    limited["collaboration_count"] = collaboration_count  # 협업 횟수 summary
    return limited


def _read_worker_history(worker_ids: Sequence[str], *, db: Any = None) -> Dict[str, Any]:
    """Core read logic: return limited work/collaboration history.

    Read-only. Calls only ``db.get_worker_collaborations`` and ``db.get_workers``
    (Requirement 5.4). Projects each worker down to allowlisted judgment fields and
    a per-worker collaboration count; the raw collaboration pairs (already limited
    to the queried workers) are included for teamwork context. No PII is returned.
    ``db`` may be dependency-injected for tests; otherwise 담당자 A's
    ``shared.db`` is resolved lazily.
    """
    ids = list(worker_ids)
    helper = resolve_db(db)

    pairs = helper.get_worker_collaborations(ids)
    records = helper.get_workers(ids)

    counts = _collaboration_count_by_worker(ids, pairs)
    workers = [_project_worker(rec, counts.get(rec.get("worker_id"), 0)) for rec in records]

    return {"workers": workers, "collaboration_pairs": pairs}


@tool
def get_worker_history(worker_ids: List[str]) -> Dict[str, Any]:
    """Return a limited work and collaboration history for the given workers.

    Use this to weigh teamwork and experience when composing a crew. The result
    contains only work-related judgment signals - trade, skill level, career
    years, certifications, completed-job counts, and how often the workers have
    collaborated. It deliberately excludes all personal information (such as name
    or phone number) and any negative operational data. This tool is read-only and
    never changes any state.

    Args:
        worker_ids: The workers whose limited history should be returned.

    Returns:
        A mapping with two keys: ``workers`` (per-worker limited judgment fields
        plus a collaboration count) and ``collaboration_pairs`` (collaboration
        counts among the queried workers).
    """
    return _read_worker_history(worker_ids)
