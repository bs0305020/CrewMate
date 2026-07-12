"""worker_api Lambda (F-A2).

근로자 지원서 CRUD 및 대기 상태 전환.

Route:
  POST /worker/application       지원서 생성 (state = INACTIVE)
  PUT  /worker/application       지원서 수정
  GET  /worker/me                내 프로필·상태 조회
  POST /worker/state/ready       대기 시작 (INACTIVE -> READY)
  POST /worker/state/inactive    대기 취소 (READY -> INACTIVE)
  GET  /worker/assignments       내 배정 조회

설계: 자가 등록 근로자는 worker_id = user_id(cognito sub)로 생성하여
      PK = WORKER#{user_id} 로 자기 리소스를 조회한다.
법·윤리 제약: 근로자 본인 응답에도 no_show_count 등 부정 데이터는 노출하지 않는다.
"""

from __future__ import annotations

from typing import Any

from botocore.exceptions import ClientError

from shared.auth import Principal
from shared.db import (
    crew_pk,
    get_item,
    put_item,
    request_pk,
    update_item,
    worker_gsi1sk,
    worker_pk,
)
from shared.responses import ApiError, ErrorCode, success
from shared.routing import Router
from shared.schemas import (
    build_worker,
    crew_view,
    now_iso,
    parse_body,
    request_view,
    require_fields,
    validate_skill_level,
    validate_trade,
)
from shared.state import Role, WorkerState

router = Router()

PROFILE_SK = "PROFILE"

# 지원서에서 수정 가능한 프로필 필드
_EDITABLE_FIELDS = (
    "name",
    "phone",
    "trade",
    "skill_level",
    "career_years",
    "age",
    "region",
    "desired_daily_wage",
    "certifications",
)

# 근로자 본인에게 노출하지 않는 내부/부정 데이터 및 키 (공유 계약 §8)
_WORKER_SELF_HIDDEN = frozenset(
    {"no_show_count", "PK", "SK", "GSI1PK", "GSI1SK", "user_id"}
)


def _self_view(worker: dict[str, Any]) -> dict[str, Any]:
    """근로자 본인 응답용 뷰 (부정 데이터 제외)."""
    return {k: v for k, v in worker.items() if k not in _WORKER_SELF_HIDDEN}


def _load_own_worker(principal: Principal) -> dict[str, Any]:
    worker = get_item(worker_pk(principal.user_id), PROFILE_SK)
    if not worker:
        raise ApiError(
            ErrorCode.WORKER_NOT_FOUND,
            "등록된 지원서가 없습니다. 먼저 지원서를 등록하세요.",
        )
    return worker


def _transition_state(worker_id: str, from_state: str, to_state: str) -> None:
    """state == from_state 조건부 쓰기로 to_state 전환. GSI1SK도 함께 갱신."""
    now = now_iso()
    try:
        update_item(
            worker_pk(worker_id),
            PROFILE_SK,
            UpdateExpression="SET #s = :to, GSI1SK = :gsi, state_changed_at = :t, updated_at = :t",
            ConditionExpression="#s = :from",
            ExpressionAttributeNames={"#s": "state"},
            ExpressionAttributeValues={
                ":to": to_state,
                ":from": from_state,
                ":gsi": worker_gsi1sk(to_state, worker_id),
                ":t": now,
            },
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            raise ApiError(
                ErrorCode.STATE_CONFLICT,
                "상태가 이미 변경되어 요청을 처리할 수 없습니다.",
            )
        raise


# ---------------------------------------------------------------------------
# 지원서 CRUD
# ---------------------------------------------------------------------------
@router.route("POST", "/worker/application")
def create_application(event: dict[str, Any], principal: Principal, _params: dict[str, str]):
    principal.require_role(Role.WORKER)
    body = parse_body(event)
    require_fields(
        body,
        [
            "name",
            "phone",
            "office_id",
            "trade",
            "skill_level",
            "career_years",
            "age",
            "region",
            "desired_daily_wage",
        ],
    )

    if get_item(worker_pk(principal.user_id), PROFILE_SK):
        raise ApiError(
            ErrorCode.VALIDATION_ERROR,
            "이미 지원서가 존재합니다. 수정(PUT /worker/application)을 이용하세요.",
        )

    item = build_worker(
        user_id=principal.user_id,
        worker_id=principal.user_id,  # 자가 등록: worker_id = cognito sub
        name=body["name"],
        phone=body["phone"],
        office_id=body["office_id"],
        trade=body["trade"],
        skill_level=body["skill_level"],
        career_years=int(body["career_years"]),
        age=int(body["age"]),
        region=body["region"],
        desired_daily_wage=int(body["desired_daily_wage"]),
        certifications=body.get("certifications") or [],
        state=WorkerState.INACTIVE,
    )
    # 동시 중복 생성 방지
    try:
        put_item(item, condition="attribute_not_exists(PK)")
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            raise ApiError(ErrorCode.VALIDATION_ERROR, "이미 지원서가 존재합니다.")
        raise
    return success(_self_view(item), status_code=201)


@router.route("PUT", "/worker/application")
def update_application(event: dict[str, Any], principal: Principal, _params: dict[str, str]):
    principal.require_role(Role.WORKER)
    worker = _load_own_worker(principal)
    body = parse_body(event)

    updates: dict[str, Any] = {}
    for field in _EDITABLE_FIELDS:
        if field in body and body[field] is not None:
            updates[field] = body[field]
    if not updates:
        raise ApiError(ErrorCode.VALIDATION_ERROR, "수정할 항목이 없습니다.")

    if "trade" in updates:
        validate_trade(updates["trade"])
    if "skill_level" in updates:
        updates["skill_level"] = validate_skill_level(updates["skill_level"])
    for int_field in ("career_years", "age", "desired_daily_wage"):
        if int_field in updates:
            updates[int_field] = int(updates[int_field])

    now = now_iso()
    set_parts = ["updated_at = :t"]
    expr_values: dict[str, Any] = {":t": now}
    expr_names: dict[str, str] = {}
    for i, (key, value) in enumerate(updates.items()):
        set_parts.append(f"#f{i} = :v{i}")
        expr_names[f"#f{i}"] = key
        expr_values[f":v{i}"] = value

    resp = update_item(
        worker_pk(worker["worker_id"]),
        PROFILE_SK,
        UpdateExpression="SET " + ", ".join(set_parts),
        ExpressionAttributeNames=expr_names,
        ExpressionAttributeValues=expr_values,
        ReturnValues="ALL_NEW",
    )
    return success(_self_view(resp["Attributes"]))


@router.route("GET", "/worker/me")
def get_me(_event: dict[str, Any], principal: Principal, _params: dict[str, str]):
    principal.require_role(Role.WORKER)
    worker = _load_own_worker(principal)
    return success(_self_view(worker))


# ---------------------------------------------------------------------------
# 대기 상태 전환
# ---------------------------------------------------------------------------
@router.route("POST", "/worker/state/ready")
def start_ready(_event: dict[str, Any], principal: Principal, _params: dict[str, str]):
    principal.require_role(Role.WORKER)
    worker = _load_own_worker(principal)
    state = worker["state"]

    if state == WorkerState.READY:
        return success(_self_view(worker))  # 멱등 처리
    if state != WorkerState.INACTIVE:
        raise ApiError(
            ErrorCode.STATE_CONFLICT,
            "배정/작업 중에는 대기를 시작할 수 없습니다.",
        )

    _transition_state(worker["worker_id"], WorkerState.INACTIVE, WorkerState.READY)
    worker["state"] = WorkerState.READY
    return success(_self_view(worker))


@router.route("POST", "/worker/state/inactive")
def cancel_ready(_event: dict[str, Any], principal: Principal, _params: dict[str, str]):
    principal.require_role(Role.WORKER)
    worker = _load_own_worker(principal)
    state = worker["state"]

    if state == WorkerState.INACTIVE:
        return success(_self_view(worker))  # 멱등 처리
    if state != WorkerState.READY:
        raise ApiError(
            ErrorCode.STATE_CONFLICT,
            "배정/작업 중에는 대기를 취소할 수 없습니다.",
        )

    _transition_state(worker["worker_id"], WorkerState.READY, WorkerState.INACTIVE)
    worker["state"] = WorkerState.INACTIVE
    return success(_self_view(worker))


# ---------------------------------------------------------------------------
# 내 배정 조회
# ---------------------------------------------------------------------------
@router.route("GET", "/worker/assignments")
def get_assignments(_event: dict[str, Any], principal: Principal, _params: dict[str, str]):
    principal.require_role(Role.WORKER)
    worker = _load_own_worker(principal)

    crew_id = worker.get("current_crew_id")
    if not crew_id:
        return success({"assignments": []})

    crew = get_item(crew_pk(crew_id), "META")
    if not crew:
        return success({"assignments": []})

    assignment = {"crew": crew_view(crew)}
    request = get_item(request_pk(crew["request_id"]), "META")
    if request:
        # 근로자에게는 현장 배정 정보(장소·날짜·시간)만 노출
        req = request_view(request)
        assignment["work"] = {
            "site_name": req.get("site_name"),
            "work_date": req.get("work_date"),
            "start_time": req.get("start_time"),
            "location_text": req.get("location_text"),
        }
    return success({"assignments": [assignment]})


def lambda_handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    return router.dispatch(event)
