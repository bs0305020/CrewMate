"""작업조 구성 검증 및 조원 조립 헬퍼 (계약 v2).

office_core(수동 편성/fill-gap), assignment(승인), agent_invoke(검증)에서 공용.

공통 검증 (PROMPT §4):
- assigned_trade ∉ worker.excluded_trades
- sum(offered_wage) ≤ request.budget (offered_wage 미지정 시 desired_daily_wage)
- 필수 직종·인원 충족
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from . import db
from .responses import ApiError, ErrorCode
from .schemas import crew_member_view, validate_trade
from .state import Acceptance, WorkerState


def validate_members_unique(member_ids: list[str]) -> None:
    if not member_ids:
        raise ApiError(ErrorCode.CREW_INVALID, "작업조에 최소 1명의 근로자가 필요합니다.")
    if len(member_ids) != len(set(member_ids)):
        raise ApiError(ErrorCode.CREW_INVALID, "동일 근로자를 중복 선택할 수 없습니다.")


def normalize_member_inputs(
    raw_members: Any, workers_by_id: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    """members 입력 [{worker_id, assigned_trade, offered_wage?}] 를 정규화한다.

    offered_wage 미지정 시 근로자의 desired_daily_wage 를 사용한다.
    """
    if not isinstance(raw_members, list) or not raw_members:
        raise ApiError(ErrorCode.CREW_INVALID, "members는 비어 있을 수 없습니다.")
    result: list[dict[str, Any]] = []
    for m in raw_members:
        if not isinstance(m, dict) or "worker_id" not in m or "assigned_trade" not in m:
            raise ApiError(
                ErrorCode.CREW_INVALID,
                "각 멤버는 worker_id와 assigned_trade가 필요합니다.",
            )
        wid = m["worker_id"]
        trade = m["assigned_trade"]
        validate_trade(trade)
        worker = workers_by_id.get(wid)
        wage = m.get("offered_wage")
        if wage in (None, ""):
            wage = int((worker or {}).get("desired_daily_wage", 0))
        result.append(
            {
                "worker_id": wid,
                "assigned_trade": trade,
                "offered_wage": int(wage),
                "is_replacement": bool(m.get("is_replacement", False)),
            }
        )
    return result


def validate_excluded_trades(
    members: list[dict[str, Any]], workers_by_id: dict[str, dict[str, Any]]
) -> None:
    for m in members:
        worker = workers_by_id.get(m["worker_id"])
        if not worker:
            raise ApiError(ErrorCode.WORKER_NOT_FOUND, f"근로자를 찾을 수 없습니다: {m['worker_id']}")
        excluded = worker.get("excluded_trades") or []
        if m["assigned_trade"] in excluded:
            raise ApiError(
                ErrorCode.CREW_INVALID,
                f"{worker.get('name', m['worker_id'])}님은 {m['assigned_trade']} 직종을 희망하지 않습니다.",
            )


def validate_candidates_state(
    members: list[dict[str, Any]],
    workers_by_id: dict[str, dict[str, Any]],
    *,
    office_id: str,
    require_state: str = WorkerState.READY,
) -> None:
    for m in members:
        worker = workers_by_id.get(m["worker_id"])
        if not worker:
            raise ApiError(ErrorCode.WORKER_NOT_FOUND, f"근로자를 찾을 수 없습니다: {m['worker_id']}")
        if worker.get("office_id") != office_id:
            raise ApiError(
                ErrorCode.CREW_INVALID,
                f"다른 사무소 근로자는 편성할 수 없습니다: {m['worker_id']}",
            )
        if worker.get("state") != require_state:
            raise ApiError(
                ErrorCode.WORKER_NOT_READY,
                f"{worker.get('name', m['worker_id'])}님은 {require_state} 상태가 아닙니다.",
            )


def validate_budget(members: list[dict[str, Any]], budget: int, *, fixed_cost: int = 0) -> int:
    total = fixed_cost + sum(int(m.get("offered_wage", 0)) for m in members)
    if budget and budget > 0 and total > int(budget):
        raise ApiError(ErrorCode.CREW_INVALID, "총예산을 초과합니다.")
    return total


def validate_required_coverage(
    members: list[dict[str, Any]],
    required_workers: list[dict[str, Any]],
) -> None:
    """assigned_trade 기준 필수 직종별 인원 충족 검증."""
    trade_counts: Counter[str] = Counter(m.get("assigned_trade") for m in members)
    for spec in required_workers or []:
        trade = spec.get("trade")
        needed = int(spec.get("count", 0))
        available = trade_counts.get(trade, 0)
        if available < needed:
            raise ApiError(
                ErrorCode.CREW_INVALID,
                f"필수 직종 인원이 부족합니다: {trade} {available}/{needed}명",
            )


# ---------------------------------------------------------------------------
# 조원 조립 (Assignments 조인 → CrewMember 리스트)
# ---------------------------------------------------------------------------
_GONE_STATUSES = {"DECLINED", "NO_SHOW", "LEFT_SITE", "CANCELLED"}


def assemble_crew_members(crew: dict[str, Any], *, active_only: bool = True) -> list[dict[str, Any]]:
    """작업조의 조원 상세를 구성한다.

    승인 후에는 Assignments(단일 진실 원천)를 조인하고, 승인 전(DRAFT/PROPOSED)에는
    Crew.proposed_members 를 근로자 프로필과 조인한다.

    - active_only=True(기본): 편성 계산용. 거절·이탈·노쇼·취소 배치는 제외(고정 인원만).
    - active_only=False: 화면 표시용. 거절/이탈 멤버도 acceptance/status 그대로 포함해
      프론트가 부분 재편성·긴급 UI를 판단할 수 있게 한다.
    """
    assignments = db.query_crew_assignments(crew["crew_id"])
    if assignments:
        members = []
        for a in assignments:
            gone = a.get("acceptance") == Acceptance.DECLINED or a.get("status") in _GONE_STATUSES
            if active_only and gone:
                continue
            worker = db.get_worker(a["worker_id"])
            members.append(crew_member_view(a, worker))
        return members

    # 승인 전: proposed_members
    members = []
    for pm in crew.get("proposed_members", []):
        worker = db.get_worker(pm["worker_id"])
        pseudo_assignment = {
            "worker_id": pm["worker_id"],
            "assigned_trade": pm.get("assigned_trade"),
            "offered_wage": pm.get("offered_wage"),
            "acceptance": Acceptance.PENDING,
            "is_replacement": pm.get("is_replacement", False),
            "notified_at": None,
        }
        members.append(crew_member_view(pseudo_assignment, worker))
    return members
