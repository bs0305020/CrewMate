"""company_request Lambda (F-A3).

건설사 인력 요청 CRUD 및 결원 이벤트 등록.

Route:
  POST /company/requests                        요청 생성 (status = REQUESTED)
  PUT  /company/requests/{requestId}            요청 수정
  GET  /company/requests                        내 요청 목록 (GSI2)
  GET  /company/requests/{requestId}            요청 상세 (+ 확정 작업조)
  POST /company/crews/{crewId}/gap-events        결원 이벤트 등록 (저장 후 EventBridge 발행)

권한: COMPANY 만. 자기 company_id 리소스만 접근한다.
법·윤리 제약: 확정 작업조 조회 응답에는 근로자 name/trade/skill_level 만 노출한다.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import boto3
from botocore.exceptions import ClientError

from shared.auth import Principal
from shared.db import (
    crew_pk,
    get_item,
    put_item,
    query_company_requests,
    request_pk,
    update_item,
    worker_pk,
)
from shared.responses import ApiError, ErrorCode, success
from shared.routing import Router
from shared.schemas import (
    build_gap_event,
    build_request,
    gap_view,
    now_iso,
    parse_body,
    request_view,
    require_fields,
    validate_trade,
    worker_public_view,
)
from shared.state import GapType, RequestStatus, Role

logger = logging.getLogger()
router = Router()

META_SK = "META"
EVENT_BUS = os.environ.get("EVENT_BUS_NAME", "default")

# 요청 수정 가능 필드
_EDITABLE_FIELDS = (
    "site_name",
    "work_date",
    "start_time",
    "location_text",
    "required_workers",
    "budget",
    "priority",
    "notes",
)


def _validate_required_workers(required_workers: Any) -> None:
    if not isinstance(required_workers, list) or not required_workers:
        raise ApiError(ErrorCode.VALIDATION_ERROR, "required_workers는 비어 있을 수 없습니다.")
    for spec in required_workers:
        if not isinstance(spec, dict) or "trade" not in spec or "count" not in spec:
            raise ApiError(
                ErrorCode.VALIDATION_ERROR,
                "required_workers 항목은 trade와 count가 필요합니다.",
            )
        validate_trade(spec["trade"])
        try:
            if int(spec["count"]) <= 0:
                raise ValueError
        except (ValueError, TypeError):
            raise ApiError(ErrorCode.VALIDATION_ERROR, "count는 1 이상의 정수여야 합니다.")


def _load_own_request(principal: Principal, request_id: str) -> dict[str, Any]:
    req = get_item(request_pk(request_id), META_SK)
    if not req:
        raise ApiError(ErrorCode.REQUEST_NOT_FOUND, "요청을 찾을 수 없습니다.")
    principal.require_company(req["company_id"])
    return req


# ---------------------------------------------------------------------------
# 요청 CRUD
# ---------------------------------------------------------------------------
@router.route("POST", "/company/requests")
def create_request(event: dict[str, Any], principal: Principal, _params: dict[str, str]):
    principal.require_role(Role.COMPANY)
    body = parse_body(event)
    require_fields(
        body,
        [
            "office_id",
            "site_name",
            "work_date",
            "start_time",
            "location_text",
            "required_workers",
            "budget",
        ],
    )
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
        status=RequestStatus.REQUESTED,
    )
    put_item(item)
    return success(request_view(item), status_code=201)


@router.route("PUT", "/company/requests/{requestId}")
def update_request(event: dict[str, Any], principal: Principal, params: dict[str, str]):
    principal.require_role(Role.COMPANY)
    req = _load_own_request(principal, params["requestId"])

    # 이미 편성이 진행된 요청은 수정 불가
    if req["status"] not in (RequestStatus.REQUESTED, RequestStatus.COMPOSING):
        raise ApiError(
            ErrorCode.REQUEST_ALREADY_ASSIGNED,
            "편성이 진행 중이거나 완료된 요청은 수정할 수 없습니다.",
        )

    body = parse_body(event)
    updates: dict[str, Any] = {
        f: body[f] for f in _EDITABLE_FIELDS if f in body and body[f] is not None
    }
    if not updates:
        raise ApiError(ErrorCode.VALIDATION_ERROR, "수정할 항목이 없습니다.")
    if "required_workers" in updates:
        _validate_required_workers(updates["required_workers"])
    if "budget" in updates:
        updates["budget"] = int(updates["budget"])

    now = now_iso()
    set_parts = ["updated_at = :t"]
    expr_values: dict[str, Any] = {":t": now}
    expr_names: dict[str, str] = {}
    for i, (key, value) in enumerate(updates.items()):
        set_parts.append(f"#f{i} = :v{i}")
        expr_names[f"#f{i}"] = key
        expr_values[f":v{i}"] = value

    resp = update_item(
        request_pk(req["request_id"]),
        META_SK,
        UpdateExpression="SET " + ", ".join(set_parts),
        ExpressionAttributeNames=expr_names,
        ExpressionAttributeValues=expr_values,
        ReturnValues="ALL_NEW",
    )
    return success(request_view(resp["Attributes"]))


@router.route("GET", "/company/requests")
def list_requests(_event: dict[str, Any], principal: Principal, _params: dict[str, str]):
    principal.require_role(Role.COMPANY)
    items = query_company_requests(principal.company_id)
    return success({"requests": [request_view(r) for r in items]})


@router.route("GET", "/company/requests/{requestId}")
def get_request(_event: dict[str, Any], principal: Principal, params: dict[str, str]):
    principal.require_role(Role.COMPANY)
    req = _load_own_request(principal, params["requestId"])
    result = request_view(req)

    # 확정 작업조가 연결돼 있으면 근로자 공개 정보만 첨부
    crew_id = req.get("crew_id")
    if crew_id:
        crew = get_item(crew_pk(crew_id), META_SK)
        if crew:
            members = []
            for mid in crew.get("member_ids", []):
                w = get_item(worker_pk(mid), "PROFILE")
                if w:
                    members.append(worker_public_view(w))
            result["crew"] = {
                "crew_id": crew.get("crew_id"),
                "status": crew.get("status"),
                "members": members,
            }
    return success(result)


# ---------------------------------------------------------------------------
# 결원 이벤트 등록 (→ EventBridge → 담당자 B의 gap_event Lambda)
# ---------------------------------------------------------------------------
@router.route("POST", "/company/crews/{crewId}/gap-events")
def create_gap_event(event: dict[str, Any], principal: Principal, params: dict[str, str]):
    principal.require_role(Role.COMPANY)
    crew_id = params["crewId"]

    crew = get_item(crew_pk(crew_id), META_SK)
    if not crew:
        raise ApiError(ErrorCode.CREW_INVALID, "작업조를 찾을 수 없습니다.")

    # 이 작업조가 속한 요청을 통해 소유권 검증
    req = get_item(request_pk(crew["request_id"]), META_SK)
    if not req:
        raise ApiError(ErrorCode.REQUEST_NOT_FOUND, "연결된 요청을 찾을 수 없습니다.")
    principal.require_company(req["company_id"])

    body = parse_body(event)
    require_fields(body, ["gap_type", "missing_worker_ids"])
    gap_type = body["gap_type"]
    if gap_type not in GapType.ALL:
        raise ApiError(ErrorCode.VALIDATION_ERROR, f"알 수 없는 결원 유형입니다: {gap_type}")
    missing = body["missing_worker_ids"]
    if not isinstance(missing, list) or not missing:
        raise ApiError(ErrorCode.VALIDATION_ERROR, "missing_worker_ids는 비어 있을 수 없습니다.")

    gap = build_gap_event(
        office_id=crew["office_id"],
        crew_id=crew_id,
        request_id=crew["request_id"],
        gap_type=gap_type,
        missing_worker_ids=missing,
    )
    put_item(gap)

    _publish_gap_event(gap)
    return success(gap_view(gap), status_code=201)


def _publish_gap_event(gap: dict[str, Any]) -> None:
    """EventBridge에 결원 이벤트를 발행한다 (best-effort; 저장은 이미 완료)."""
    detail = {
        "event_id": gap["event_id"],
        "office_id": gap["office_id"],
        "crew_id": gap["crew_id"],
        "request_id": gap["request_id"],
        "gap_type": gap["gap_type"],
        "missing_worker_ids": gap["missing_worker_ids"],
    }
    try:
        client = boto3.client("events")
        client.put_events(
            Entries=[
                {
                    "Source": "crewmate.company",
                    "DetailType": "GapEventDetected",
                    "Detail": json.dumps(detail, ensure_ascii=False),
                    "EventBusName": EVENT_BUS,
                }
            ]
        )
    except ClientError:
        logger.exception("gap_event_publish_failed event_id=%s", gap["event_id"])


def lambda_handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    return router.dispatch(event)
