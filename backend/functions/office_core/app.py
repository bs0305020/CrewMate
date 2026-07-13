"""office_core Lambda (F-A4).

인력사무소의 근로자/후보 조회 및 수동 작업조 편성.

Route:
  GET  /office/workers          소속 근로자 조회 (필터: state, trade, min_skill_level,
                                min_wage, max_wage, region)
  POST /office/crews/manual     수동 작업조 생성 (Crew status = DRAFT)

권한: OFFICE 만. 자기 office_id 소속 근로자/요청만 접근한다.
승인(approve)·긴급 승인은 assignment Lambda 담당.
"""

from __future__ import annotations

from typing import Any

from shared.auth import Principal
from shared.crew import (
    validate_candidates,
    validate_members_unique,
    validate_required_coverage,
)
from shared.db import (
    get_item,
    put_item,
    query_office_all_workers,
    query_office_workers_by_state,
    request_pk,
    worker_pk,
)
from shared.responses import ApiError, ErrorCode, success
from shared.routing import Router
from shared.schemas import (
    build_crew,
    crew_view,
    parse_body,
    require_fields,
    validate_trade,
    worker_office_view,
)
from shared.state import RequestStatus, Role, WorkerState

router = Router()

META_SK = "META"
PROFILE_SK = "PROFILE"


def _query_params(event: dict[str, Any]) -> dict[str, str]:
    return event.get("queryStringParameters") or {}


def _apply_filters(workers: list[dict[str, Any]], qp: dict[str, str]) -> list[dict[str, Any]]:
    trade = qp.get("trade")
    region = qp.get("region")
    min_skill = qp.get("min_skill_level")
    min_wage = qp.get("min_wage")
    max_wage = qp.get("max_wage")

    if trade:
        validate_trade(trade)

    result = []
    for w in workers:
        if trade and w.get("trade") != trade:
            continue
        if region and w.get("region") != region:
            continue
        if min_skill and int(w.get("skill_level", 0)) < int(min_skill):
            continue
        wage = int(w.get("desired_daily_wage", 0))
        if min_wage and wage < int(min_wage):
            continue
        if max_wage and wage > int(max_wage):
            continue
        result.append(w)
    return result


# ---------------------------------------------------------------------------
# 근로자/후보 조회
# ---------------------------------------------------------------------------
@router.route("GET", "/office/workers")
def list_workers(event: dict[str, Any], principal: Principal, _params: dict[str, str]):
    principal.require_role(Role.OFFICE)
    qp = _query_params(event)

    state = qp.get("state")
    if state:
        if state not in WorkerState.ALL:
            raise ApiError(ErrorCode.VALIDATION_ERROR, f"알 수 없는 상태입니다: {state}")
        workers = query_office_workers_by_state(principal.office_id, state)
    else:
        workers = query_office_all_workers(principal.office_id)

    workers = _apply_filters(workers, qp)
    return success({"workers": [worker_office_view(w) for w in workers]})


# ---------------------------------------------------------------------------
# 수동 작업조 편성
# ---------------------------------------------------------------------------
@router.route("POST", "/office/crews/manual")
def create_manual_crew(event: dict[str, Any], principal: Principal, _params: dict[str, str]):
    principal.require_role(Role.OFFICE)
    body = parse_body(event)
    require_fields(body, ["request_id", "member_ids"])

    request_id = body["request_id"]
    member_ids = body["member_ids"]
    if not isinstance(member_ids, list):
        raise ApiError(ErrorCode.CREW_INVALID, "member_ids는 배열이어야 합니다.")
    validate_members_unique(member_ids)

    # 요청 검증 (존재 + 소유 사무소)
    request = get_item(request_pk(request_id), META_SK)
    if not request:
        raise ApiError(ErrorCode.REQUEST_NOT_FOUND, "요청을 찾을 수 없습니다.")
    if request.get("office_id") != principal.office_id:
        raise ApiError(ErrorCode.FORBIDDEN, "다른 사무소의 요청에는 편성할 수 없습니다.")
    if request["status"] in (RequestStatus.APPROVED, RequestStatus.RUNNING, RequestStatus.COMPLETED):
        raise ApiError(ErrorCode.REQUEST_ALREADY_ASSIGNED, "이미 배정이 완료된 요청입니다.")

    # 후보 근로자 로드 및 검증 (동일 사무소 + READY)
    members = []
    for mid in member_ids:
        w = get_item(worker_pk(mid), PROFILE_SK)
        if not w:
            raise ApiError(ErrorCode.WORKER_NOT_FOUND, f"근로자를 찾을 수 없습니다: {mid}")
        members.append(w)
    validate_candidates(members, office_id=principal.office_id, require_state=WorkerState.READY)

    # 필수 직종 인원 충족 검증
    validate_required_coverage(members, request.get("required_workers", []))

    crew = build_crew(
        office_id=principal.office_id,
        request_id=request_id,
        member_ids=member_ids,
        source="MANUAL",
    )
    put_item(crew)
    return success(crew_view(crew), status_code=201)


def lambda_handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    return router.dispatch(event)
