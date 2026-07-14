"""company_request Lambda (계약 v2).

Route:
  POST /company/requests                          요청 생성 (status = REQUESTED)
  PUT  /company/requests/{requestId}              요청 수정
  GET  /company/requests                          내 요청 목록 (GSI2)
  GET  /company/requests/{requestId}              요청 상세 (+ 확정 작업조 + activeGap)
  POST /company/crews/{crewId}/checkin/{workerId}   출근 처리 (트랜잭션 4)
  POST /company/crews/{crewId}/checkout/{workerId}  퇴근 처리 (트랜잭션 5)
  POST /company/crews/{crewId}/gap-events            결원 등록 (트랜잭션 6)

권한: COMPANY. 자기 company_id 리소스만 접근한다.
COMPANY 응답에는 성실도 카운트/부정 라벨을 포함하지 않는다.
"""

from __future__ import annotations

import logging
from typing import Any

from botocore.exceptions import ClientError

from shared import db, txn
from shared.auth import Principal
from shared.crew import assemble_crew_members
from shared.responses import ApiError, ErrorCode, success
from shared.routing import Router
from shared.schemas import (
    build_gap_event,
    build_notification,
    build_request,
    gap_view,
    now_iso,
    parse_body,
    request_view,
    require_fields,
    validate_trade,
)
from shared.state import (
    Acceptance,
    AssignmentStatus,
    CrewStatus,
    GapStatus,
    GapType,
    RequestStatus,
    Role,
    WorkerState,
)

logger = logging.getLogger()
router = Router()

_EDITABLE_FIELDS = (
    "site_name", "work_date", "start_time", "location_text",
    "required_workers", "budget", "priority", "notes",
)


def _validate_required_workers(required_workers: Any) -> None:
    if not isinstance(required_workers, list) or not required_workers:
        raise ApiError(ErrorCode.VALIDATION_ERROR, "required_workers는 비어 있을 수 없습니다.")
    for spec in required_workers:
        if not isinstance(spec, dict) or "trade" not in spec or "count" not in spec:
            raise ApiError(ErrorCode.VALIDATION_ERROR, "required_workers 항목은 trade와 count가 필요합니다.")
        validate_trade(spec["trade"])
        try:
            if int(spec["count"]) <= 0:
                raise ValueError
        except (ValueError, TypeError):
            raise ApiError(ErrorCode.VALIDATION_ERROR, "count는 1 이상의 정수여야 합니다.")


def _load_own_request(principal: Principal, request_id: str) -> dict[str, Any]:
    req = db.get_request(request_id)
    if not req:
        raise ApiError(ErrorCode.REQUEST_NOT_FOUND, "요청을 찾을 수 없습니다.")
    principal.require_company(req["company_id"])
    return req


def _worker_id_param(event, params) -> str:
    body = parse_body(event)
    return params.get("workerId") or body.get("worker_id") or ""


# ---------------------------------------------------------------------------
# 요청 CRUD
# ---------------------------------------------------------------------------
@router.route("POST", "/company/requests")
def create_request(event, principal: Principal, _params):
    principal.require_role(Role.COMPANY)
    body = parse_body(event)
    require_fields(body, ["office_id", "site_name", "work_date", "start_time",
                          "location_text", "required_workers", "budget"])
    _validate_required_workers(body["required_workers"])
    item = build_request(
        company_id=principal.company_id,
        office_id=body["office_id"],
        site_name=body["site_name"],
        work_date=body["work_date"],
        start_time=body["start_time"],
        location_text=body["location_text"],
        required_workers=body["required_workers"],
        budget=int(body["budget"]),
        priority=body.get("priority") or {},
        notes=body.get("notes") or "",
    )
    db.put_request(item)
    return success(request_view(item), status_code=201)


@router.route("PUT", "/company/requests/{requestId}")
def update_request(event, principal: Principal, params):
    principal.require_role(Role.COMPANY)
    req = _load_own_request(principal, params["requestId"])
    if req["status"] not in (RequestStatus.REQUESTED, RequestStatus.COMPOSING):
        raise ApiError(ErrorCode.REQUEST_ALREADY_ASSIGNED, "편성이 진행 중이거나 완료된 요청은 수정할 수 없습니다.")
    body = parse_body(event)
    updates = {f: body[f] for f in _EDITABLE_FIELDS if f in body and body[f] is not None}
    if not updates:
        raise ApiError(ErrorCode.VALIDATION_ERROR, "수정할 항목이 없습니다.")
    if "required_workers" in updates:
        _validate_required_workers(updates["required_workers"])
    if "budget" in updates:
        updates["budget"] = int(updates["budget"])

    from shared.schemas import to_decimal
    now = now_iso()
    set_parts = ["updated_at = :t"]
    expr_values: dict[str, Any] = {":t": now}
    expr_names: dict[str, str] = {}
    for i, (key, value) in enumerate(updates.items()):
        set_parts.append(f"#f{i} = :v{i}")
        expr_names[f"#f{i}"] = key
        expr_values[f":v{i}"] = to_decimal(value)

    resp = db.update_request(
        req["request_id"],
        UpdateExpression="SET " + ", ".join(set_parts),
        ExpressionAttributeNames=expr_names,
        ExpressionAttributeValues=expr_values,
        ReturnValues="ALL_NEW",
    )
    return success(request_view(resp["Attributes"]))


@router.route("GET", "/company/requests")
def list_requests(_event, principal: Principal, _params):
    principal.require_role(Role.COMPANY)
    items = db.query_company_requests(principal.company_id)
    return success([request_view(r) for r in items])


@router.route("GET", "/company/requests/{requestId}")
def get_request(_event, principal: Principal, params):
    principal.require_role(Role.COMPANY)
    req = _load_own_request(principal, params["requestId"])
    result = request_view(req)
    result["crew"] = _active_crew_view(req["request_id"])
    result["activeGap"] = _active_gap(req["request_id"])
    return success(result)


def _active_crew_view(request_id: str):
    crews = [c for c in db.query_crews_by_request(request_id) if c.get("status") != CrewStatus.CANCELLED]
    if not crews:
        return None
    crew = sorted(crews, key=lambda c: c.get("created_at", ""))[-1]
    members = assemble_crew_members(crew, active_only=False)  # 거절 멤버 포함(표시용)
    return {
        "crew_id": crew["crew_id"],
        "request_id": crew["request_id"],
        "office_id": crew["office_id"],
        "status": crew["status"],
        "source": crew.get("source"),
        "member_ids": crew.get("member_ids", []),
        "members": members,
    }


def _active_gap(request_id: str):
    # request_id 기준 결원 이벤트 조회 (scan 폴백)
    from boto3.dynamodb.conditions import Key
    from shared.db import gap_events_table
    items = gap_events_table().scan(FilterExpression=Key("request_id").eq(request_id)).get("Items", [])
    if not items:
        return None
    latest = sorted(items, key=lambda g: g.get("created_at", ""))[-1]
    return gap_view(latest)


# ---------------------------------------------------------------------------
# 출근 (트랜잭션 4)
# ---------------------------------------------------------------------------
@router.route("POST", "/company/crews/{crewId}/checkin/{workerId}")
def checkin(event, principal: Principal, params):
    principal.require_role(Role.COMPANY)
    crew_id = params["crewId"]
    worker_id = _worker_id_param(event, params)
    crew, worker = _load_crew_worker(principal, crew_id, worker_id)
    if worker["state"] != WorkerState.RESERVED:
        raise ApiError(ErrorCode.STATE_CONFLICT, "출근 처리는 배차확정(RESERVED) 상태에서만 가능합니다.")

    now = now_iso()
    entries = [
        txn.worker_entry(
            worker_id, now=now, to_state=WorkerState.RUNNING,
            from_states=[WorkerState.RESERVED], current_crew_id=crew_id,
        ),
        txn.assignment_update_entry(crew_id, worker_id, now=now, status=AssignmentStatus.RUNNING),
    ]
    _run(entries, "출근")
    _rollup_running(crew)
    return success(_worker_company_response(worker_id))


# ---------------------------------------------------------------------------
# 퇴근 (트랜잭션 5)
# ---------------------------------------------------------------------------
@router.route("POST", "/company/crews/{crewId}/checkout/{workerId}")
def checkout(event, principal: Principal, params):
    principal.require_role(Role.COMPANY)
    crew_id = params["crewId"]
    worker_id = _worker_id_param(event, params)
    crew, worker = _load_crew_worker(principal, crew_id, worker_id)
    if worker["state"] != WorkerState.RUNNING:
        raise ApiError(ErrorCode.STATE_CONFLICT, "퇴근 처리는 작업중(RUNNING) 상태에서만 가능합니다.")

    now = now_iso()
    entries = [
        txn.worker_inactive_entry(worker_id, now, inc_completed=True),
        txn.assignment_update_entry(crew_id, worker_id, now=now, status=AssignmentStatus.COMPLETED),
    ]
    _run(entries, "퇴근")
    _rollup_completed(crew)
    return success(_worker_company_response(worker_id))


# ---------------------------------------------------------------------------
# 결원 등록 (트랜잭션 6)
# ---------------------------------------------------------------------------
@router.route("POST", "/company/crews/{crewId}/gap-events")
def create_gap_event(event, principal: Principal, params):
    principal.require_role(Role.COMPANY)
    crew_id = params["crewId"]
    body = parse_body(event)
    require_fields(body, ["type", "affected_worker_id"])
    gap_type = body["type"]
    if gap_type not in GapType.ALL:
        raise ApiError(ErrorCode.VALIDATION_ERROR, f"알 수 없는 결원 유형입니다: {gap_type}")
    worker_id = body["affected_worker_id"]

    crew = db.get_crew(crew_id)
    if not crew:
        raise ApiError(ErrorCode.CREW_INVALID, "작업조를 찾을 수 없습니다.")
    req = db.get_request(crew["request_id"])
    if not req:
        raise ApiError(ErrorCode.REQUEST_NOT_FOUND, "연결된 요청을 찾을 수 없습니다.")
    principal.require_company(req["company_id"])
    worker = db.get_worker(worker_id)
    if not worker:
        raise ApiError(ErrorCode.WORKER_NOT_FOUND, "근로자를 찾을 수 없습니다.")

    gap = build_gap_event(
        office_id=crew["office_id"],
        crew_id=crew_id,
        request_id=crew["request_id"],
        gap_type=gap_type,
        affected_worker_id=worker_id,
        affected_worker_name=worker.get("name", ""),
        status=GapStatus.DETECTED,
    )
    now = now_iso()
    entries = [
        # 이탈자: INACTIVE (성실도 완료 미증가)
        txn.worker_inactive_entry(worker_id, now),
        txn.assignment_update_entry(
            crew_id, worker_id, now=now,
            acceptance=Acceptance.DECLINED, status=gap_type,
        ),
        txn.put_entry("gap_events", gap),
        txn.request_status_entry(crew["request_id"], to_status=RequestStatus.COMPOSING, now=now),
    ]
    _run(entries, "결원 등록")

    office = db.get_office(crew["office_id"]) or {}
    _notify(office.get("owner_user_id") or crew["office_id"], "GAP_EVENT", "결원 발생",
            f"{worker.get('name', '')}님의 결원이 발생했습니다. 재편성이 필요합니다.")
    return success(gap_view(gap), status_code=201)


# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------
def _load_crew_worker(principal: Principal, crew_id: str, worker_id: str):
    if not worker_id:
        raise ApiError(ErrorCode.VALIDATION_ERROR, "worker_id가 필요합니다.")
    crew = db.get_crew(crew_id)
    if not crew:
        raise ApiError(ErrorCode.CREW_INVALID, "작업조를 찾을 수 없습니다.")
    req = db.get_request(crew["request_id"])
    if not req:
        raise ApiError(ErrorCode.REQUEST_NOT_FOUND, "연결된 요청을 찾을 수 없습니다.")
    principal.require_company(req["company_id"])
    worker = db.get_worker(worker_id)
    if not worker:
        raise ApiError(ErrorCode.WORKER_NOT_FOUND, "근로자를 찾을 수 없습니다.")
    return crew, worker


def _run(entries, action_label):
    try:
        txn.run(entries)
    except ClientError as exc:
        if exc.response["Error"]["Code"] in ("TransactionCanceledException", "ConditionalCheckFailedException"):
            raise ApiError(ErrorCode.STATE_CONFLICT, f"{action_label} 처리 중 상태 충돌이 발생했습니다.")
        raise


def _active_worker_ids(crew) -> list[str]:
    active = []
    for a in db.query_crew_assignments(crew["crew_id"]):
        if a.get("acceptance") == Acceptance.DECLINED or a.get("status") in ("DECLINED", "NO_SHOW", "LEFT_SITE", "CANCELLED"):
            continue
        active.append(a["worker_id"])
    return active


def _rollup_running(crew):
    ids = _active_worker_ids(crew)
    if ids and all((db.get_worker(i) or {}).get("state") == WorkerState.RUNNING for i in ids):
        _set_crew_request(crew, CrewStatus.RUNNING, RequestStatus.RUNNING)


def _rollup_completed(crew):
    ids = _active_worker_ids(crew)
    if ids and all((db.get_worker(i) or {}).get("state") == WorkerState.INACTIVE for i in ids):
        _set_crew_request(crew, CrewStatus.COMPLETED, RequestStatus.COMPLETED)


def _set_crew_request(crew, crew_status, req_status):
    now = now_iso()
    db.update_crew(
        crew["crew_id"],
        UpdateExpression="SET #s = :cs, gsi1sk = :g, updated_at = :t",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":cs": crew_status, ":g": db.crew_gsi1sk(crew_status, crew["crew_id"]), ":t": now},
    )
    db.update_request(
        crew["request_id"],
        UpdateExpression="SET #s = :rs, gsi1sk = :g, updated_at = :t",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":rs": req_status, ":g": db.request_gsi1sk(req_status, crew["request_id"]), ":t": now},
    )


def _worker_company_response(worker_id: str) -> dict[str, Any]:
    """출근/퇴근 응답: Worker 형태이나 성실도 카운트는 제외 (COMPANY 노출 금지)."""
    from shared.schemas import worker_public_view, clean
    worker = db.get_worker(worker_id) or {}
    view = worker_public_view(worker)
    view["state"] = worker.get("state")
    view["current_crew_id"] = worker.get("current_crew_id")
    view["current_offer"] = clean(worker.get("current_offer"))
    return view


def _notify(user_id, ntype, title, message):
    try:
        db.put_notification(build_notification(user_id=user_id, type=ntype, title=title, message=message))
    except ClientError:
        logger.exception("notify_failed user=%s", user_id)


def lambda_handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    return router.dispatch(event)
