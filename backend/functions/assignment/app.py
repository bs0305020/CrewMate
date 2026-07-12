"""assignment Lambda (F-A5) — 이 PRD의 핵심.

작업조 승인 → 조원 전체를 원자적으로 READY → RESERVED → RUNNING 전환.

Route:
  POST /office/crews/{crewId}/approve          작업조 승인 → 배차
  POST /office/emergency/{eventId}/approve     긴급 작업조 승인 (커밋 8)

동시성 원칙 (공유 계약 1.2 / F-A5):
- 신규 조원 전체를 TransactWriteItems 로 원자 처리한다.
- 각 근로자는 ConditionExpression(state = READY / RESERVED) 조건부 쓰기로만 전환한다.
- 한 명이라도 실패하면 트랜잭션 전체가 취소되어 어떤 근로자도 상태가 변하지 않는다.
- 일부만 RUNNING 인 불완전 작업조를 만들지 않는다 (실패 시 RESERVED → READY 복구).
"""

from __future__ import annotations

import logging
from typing import Any

from boto3.dynamodb.types import TypeSerializer
from botocore.exceptions import ClientError

from shared.auth import Principal
from shared.crew import validate_candidates, validate_members_unique, validate_required_coverage
from shared.db import (
    TABLE_NAME,
    crew_gsi1sk,
    crew_pk,
    get_client,
    get_item,
    put_item,
    request_gsi1sk,
    request_pk,
    worker_gsi1sk,
    worker_pk,
)
from shared.responses import ApiError, ErrorCode, success
from shared.routing import Router
from shared.schemas import build_notification, crew_view, now_iso
from shared.state import CrewStatus, RequestStatus, Role, WorkerState

logger = logging.getLogger()
router = Router()

META_SK = "META"
PROFILE_SK = "PROFILE"

_serializer = TypeSerializer()


def _ser(value: Any) -> dict[str, Any]:
    return _serializer.serialize(value)


# ---------------------------------------------------------------------------
# TransactWriteItems 엔트리 빌더
# ---------------------------------------------------------------------------
def _worker_transition_entry(
    worker_id: str,
    *,
    to_state: str,
    from_state: str,
    now: str,
    crew_id: str | None = None,
    clear_crew: bool = False,
) -> dict[str, Any]:
    set_expr = "SET #s = :to, GSI1SK = :gsi, state_changed_at = :t, updated_at = :t"
    values = {
        ":to": _ser(to_state),
        ":from": _ser(from_state),
        ":gsi": _ser(worker_gsi1sk(to_state, worker_id)),
        ":t": _ser(now),
    }
    if crew_id is not None:
        set_expr += ", current_crew_id = :cid"
        values[":cid"] = _ser(crew_id)
    elif clear_crew:
        set_expr += ", current_crew_id = :cid"
        values[":cid"] = _ser(None)
    return {
        "Update": {
            "TableName": TABLE_NAME,
            "Key": {"PK": _ser(worker_pk(worker_id)), "SK": _ser(PROFILE_SK)},
            "UpdateExpression": set_expr,
            "ConditionExpression": "#s = :from",
            "ExpressionAttributeNames": {"#s": "state"},
            "ExpressionAttributeValues": values,
        }
    }


def _status_entry(
    pk: str,
    *,
    gsi1sk: str,
    to_status: str,
    from_statuses: list[str],
    now: str,
    extra_set: dict[str, Any] | None = None,
) -> dict[str, Any]:
    set_expr = "SET #s = :to, GSI1SK = :gsi, updated_at = :t"
    values: dict[str, Any] = {":to": _ser(to_status), ":gsi": _ser(gsi1sk), ":t": _ser(now)}
    for i, key in enumerate(extra_set or {}):
        set_expr += f", {key} = :e{i}"
        values[f":e{i}"] = _ser(extra_set[key])

    cond_parts = []
    for i, st in enumerate(from_statuses):
        cond_parts.append(f"#s = :f{i}")
        values[f":f{i}"] = _ser(st)
    return {
        "Update": {
            "TableName": TABLE_NAME,
            "Key": {"PK": _ser(pk), "SK": _ser(META_SK)},
            "UpdateExpression": set_expr,
            "ConditionExpression": "(" + " OR ".join(cond_parts) + ")",
            "ExpressionAttributeNames": {"#s": "status"},
            "ExpressionAttributeValues": values,
        }
    }


def _run_transaction(items: list[dict[str, Any]]) -> None:
    get_client().transact_write_items(TransactItems=items)


def _is_conflict(exc: ClientError) -> bool:
    return exc.response["Error"]["Code"] in (
        "TransactionCanceledException",
        "ConditionalCheckFailedException",
    )


# ---------------------------------------------------------------------------
# 핵심: 조원 배차 (READY → RESERVED → RUNNING)
# ---------------------------------------------------------------------------
def activate_members(
    *,
    new_member_ids: list[str],
    crew_id: str,
    request_id: str,
    now: str | None = None,
) -> None:
    """new_member_ids 전원을 원자적으로 READY → RESERVED → RUNNING 전환한다.

    실패 시 STATE_CONFLICT 를 던지며, RESERVED 까지 진행된 경우 READY 로 복구한다.
    Crew / Request 상태도 함께 전이한다.
    """
    now = now or now_iso()

    # 1단계: READY → RESERVED + Crew/Request → APPROVED (원자)
    reserve_items = [
        _worker_transition_entry(
            mid, to_state=WorkerState.RESERVED, from_state=WorkerState.READY, now=now
        )
        for mid in new_member_ids
    ]
    reserve_items.append(
        _status_entry(
            crew_pk(crew_id),
            gsi1sk=crew_gsi1sk(CrewStatus.APPROVED, crew_id),
            to_status=CrewStatus.APPROVED,
            from_statuses=[CrewStatus.DRAFT, CrewStatus.PROPOSED],
            now=now,
        )
    )
    reserve_items.append(
        _status_entry(
            request_pk(request_id),
            gsi1sk=request_gsi1sk(RequestStatus.APPROVED, request_id),
            to_status=RequestStatus.APPROVED,
            from_statuses=[
                RequestStatus.REQUESTED,
                RequestStatus.COMPOSING,
                RequestStatus.PROPOSED,
            ],
            now=now,
        )
    )
    try:
        _run_transaction(reserve_items)
    except ClientError as exc:
        if _is_conflict(exc):
            raise ApiError(
                ErrorCode.STATE_CONFLICT,
                "승인 도중 일부 근로자 또는 요청의 상태가 변경되어 배차를 완료할 수 없습니다.",
            )
        raise

    # 2단계: RESERVED → RUNNING + current_crew_id + Crew/Request → RUNNING (원자)
    run_items = [
        _worker_transition_entry(
            mid,
            to_state=WorkerState.RUNNING,
            from_state=WorkerState.RESERVED,
            now=now,
            crew_id=crew_id,
        )
        for mid in new_member_ids
    ]
    run_items.append(
        _status_entry(
            crew_pk(crew_id),
            gsi1sk=crew_gsi1sk(CrewStatus.RUNNING, crew_id),
            to_status=CrewStatus.RUNNING,
            from_statuses=[CrewStatus.APPROVED],
            now=now,
        )
    )
    run_items.append(
        _status_entry(
            request_pk(request_id),
            gsi1sk=request_gsi1sk(RequestStatus.RUNNING, request_id),
            to_status=RequestStatus.RUNNING,
            from_statuses=[RequestStatus.APPROVED],
            now=now,
            extra_set={"crew_id": crew_id},
        )
    )
    try:
        _run_transaction(run_items)
    except ClientError as exc:
        if _is_conflict(exc):
            _rollback_reserved(new_member_ids, crew_id, request_id, now)
            raise ApiError(
                ErrorCode.STATE_CONFLICT,
                "배차 완료 처리 중 충돌이 발생하여 예약을 취소했습니다.",
            )
        raise


def _rollback_reserved(
    member_ids: list[str], crew_id: str, request_id: str, now: str
) -> None:
    """RESERVED → READY 복구 및 Crew/Request 상태 되돌리기 (best-effort)."""
    items = [
        _worker_transition_entry(
            mid,
            to_state=WorkerState.READY,
            from_state=WorkerState.RESERVED,
            now=now,
            clear_crew=True,
        )
        for mid in member_ids
    ]
    items.append(
        _status_entry(
            crew_pk(crew_id),
            gsi1sk=crew_gsi1sk(CrewStatus.DRAFT, crew_id),
            to_status=CrewStatus.DRAFT,
            from_statuses=[CrewStatus.APPROVED],
            now=now,
        )
    )
    items.append(
        _status_entry(
            request_pk(request_id),
            gsi1sk=request_gsi1sk(RequestStatus.REQUESTED, request_id),
            to_status=RequestStatus.REQUESTED,
            from_statuses=[RequestStatus.APPROVED],
            now=now,
        )
    )
    try:
        _run_transaction(items)
    except ClientError:
        logger.exception("rollback_failed crew_id=%s request_id=%s", crew_id, request_id)


def notify_members(
    workers: list[dict[str, Any]], request: dict[str, Any], *, kind: str, title: str
) -> None:
    """조원별 인앱 알림 생성 (배차 정보 포함, best-effort)."""
    message = (
        f"{request.get('site_name')} · {request.get('work_date')} "
        f"{request.get('start_time')} · {request.get('location_text')}"
    )
    for w in workers:
        target = w.get("user_id") or w.get("worker_id")
        try:
            put_item(
                build_notification(
                    user_id=target,
                    kind=kind,
                    title=title,
                    message=message,
                    payload={
                        "crew_id": request.get("crew_id"),
                        "request_id": request.get("request_id"),
                    },
                )
            )
        except ClientError:
            logger.exception("notification_failed worker=%s", w.get("worker_id"))


# ---------------------------------------------------------------------------
# 승인 라우트
# ---------------------------------------------------------------------------
@router.route("POST", "/office/crews/{crewId}/approve")
def approve_crew(_event: dict[str, Any], principal: Principal, params: dict[str, str]):
    principal.require_role(Role.OFFICE)
    crew_id = params["crewId"]

    crew = get_item(crew_pk(crew_id), META_SK)
    if not crew:
        raise ApiError(ErrorCode.CREW_INVALID, "작업조를 찾을 수 없습니다.")
    principal.require_office(crew["office_id"])

    if crew["status"] not in (CrewStatus.DRAFT, CrewStatus.PROPOSED):
        raise ApiError(ErrorCode.CREW_INVALID, "이미 승인되었거나 승인할 수 없는 작업조입니다.")

    request = get_item(request_pk(crew["request_id"]), META_SK)
    if not request:
        raise ApiError(ErrorCode.REQUEST_NOT_FOUND, "연결된 요청을 찾을 수 없습니다.")
    if request["status"] in (
        RequestStatus.APPROVED,
        RequestStatus.RUNNING,
        RequestStatus.COMPLETED,
    ):
        raise ApiError(ErrorCode.REQUEST_ALREADY_ASSIGNED, "이미 배정이 완료된 요청입니다.")

    member_ids = crew.get("member_ids", [])
    validate_members_unique(member_ids)

    # 승인 시점 READY 재검증 (조건부 쓰기 전 사전 검증)
    workers = []
    for mid in member_ids:
        w = get_item(worker_pk(mid), PROFILE_SK)
        if not w:
            raise ApiError(ErrorCode.WORKER_NOT_FOUND, f"근로자를 찾을 수 없습니다: {mid}")
        workers.append(w)
    validate_candidates(workers, office_id=crew["office_id"], require_state=WorkerState.READY)
    validate_required_coverage(workers, request.get("required_workers", []))

    # 원자적 배차 (READY → RESERVED → RUNNING)
    activate_members(new_member_ids=member_ids, crew_id=crew_id, request_id=crew["request_id"])

    # 알림 생성 (best-effort)
    request["crew_id"] = crew_id
    notify_members(workers, request, kind="ASSIGNED", title="작업 배정 안내")

    updated_crew = get_item(crew_pk(crew_id), META_SK)
    return success(crew_view(updated_crew))


def lambda_handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    return router.dispatch(event)
