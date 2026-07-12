"""작업조 구성 검증 헬퍼 (F-A4 / F-A5 공용).

office_core(수동 편성)와 assignment(승인)에서 동일하게 사용한다.
- 후보 유효성: 동일 사무소 + READY + 중복 없음 + 존재
- 필수 직종 인원 충족 검증 (미충족 시 CREW_INVALID)
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from .responses import ApiError, ErrorCode
from .state import WorkerState


def validate_members_unique(member_ids: list[str]) -> None:
    if not member_ids:
        raise ApiError(ErrorCode.CREW_INVALID, "작업조에 최소 1명의 근로자가 필요합니다.")
    if len(member_ids) != len(set(member_ids)):
        raise ApiError(ErrorCode.CREW_INVALID, "동일 근로자를 중복 선택할 수 없습니다.")


def validate_candidates(
    workers: list[dict[str, Any]],
    *,
    office_id: str,
    require_state: str | None = WorkerState.READY,
) -> None:
    """후보 근로자들이 동일 사무소이며 지정 상태인지 검증한다."""
    for w in workers:
        if w.get("office_id") != office_id:
            raise ApiError(
                ErrorCode.CREW_INVALID,
                f"다른 사무소 근로자는 편성할 수 없습니다: {w.get('worker_id')}",
            )
        if require_state is not None and w.get("state") != require_state:
            raise ApiError(
                ErrorCode.WORKER_NOT_READY,
                f"{require_state} 상태가 아닌 근로자가 포함되어 있습니다: {w.get('worker_id')}",
            )


def validate_required_coverage(
    members: list[dict[str, Any]],
    required_workers: list[dict[str, Any]],
) -> None:
    """필수 직종별 인원 및 최소 기능등급 충족 여부를 검증한다.

    required_workers 항목: {trade, count, min_skill_level?}
    미충족 시 CREW_INVALID.
    """
    trade_counts: Counter[str] = Counter(m.get("trade") for m in members)

    for spec in required_workers:
        trade = spec.get("trade")
        needed = int(spec.get("count", 0))
        min_skill = spec.get("min_skill_level")

        if min_skill is not None:
            available = sum(
                1
                for m in members
                if m.get("trade") == trade and int(m.get("skill_level", 0)) >= int(min_skill)
            )
        else:
            available = trade_counts.get(trade, 0)

        if available < needed:
            raise ApiError(
                ErrorCode.CREW_INVALID,
                f"필수 직종 인원이 부족합니다: {trade} {available}/{needed}명"
                + (f" (최소 기능등급 {min_skill})" if min_skill is not None else ""),
            )
