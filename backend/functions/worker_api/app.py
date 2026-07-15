"""worker_api Lambda (계약 v2).

Route:
  POST /worker/application       지원서 생성 (state = INACTIVE)
  PUT  /worker/application       지원서 수정
  GET  /worker/me                내 프로필·상태 조회
  POST /worker/state/ready       대기 시작 (INACTIVE -> READY)
  POST /worker/state/inactive    대기 취소 (READY -> INACTIVE)
  POST /worker/offer/accept      제안 수락 (트랜잭션 2, body: eta?)
  POST /worker/offer/decline     제안 거절 (트랜잭션 3)
  POST /worker/reservation/cancel 배차완료(RESERVED) 취소 (작업 24시간 전까지)
  GET  /worker/assignments       내 배정 조회
  GET  /worker/accepted-jobs     내가 수락한 작업 이력
  GET  /worker/attendance        출근일 집계 (히트맵용)
  GET  /worker/wage-stats/{careerYears}  경력 연차별 평균 희망 일당
  GET  /worker/history           작업 이력 (Assignments GSI1)

자가 등록 근로자는 worker_id = user_id(cognito sub)로 생성한다.
평점(rating)·출근 수(attended)·배차완료 수(dispatched)는 본인 응답에 노출한다.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from botocore.exceptions import ClientError

from shared import db, txn
from shared.auth import Principal
from shared.responses import ApiError, ErrorCode, success
from shared.routing import Router
from shared.schemas import (
    build_worker,
    clean,
    new_id,
    now_iso,
    parse_body,
    require_fields,
    validate_trades,
    work_history_entry,
    worker_self_view,
)
from shared.state import (
    Acceptance,
    AssignmentStatus,
    CrewStatus,
    GapType,
    RequestStatus,
    Role,
    WorkerState,
)

router = Router()

_EDITABLE_FIELDS = (
    "name",
    "phone",
    "preferred_trades",
    "excluded_trades",
    "career_years",
    "age",
    "region",
    "desired_daily_wage",
    "certifications",
)


def _load_own_worker(principal: Principal) -> dict[str, Any]:
    worker = db.get_worker_by_user(principal.user_id)
    if not worker:
        raise ApiError(ErrorCode.WORKER_NOT_FOUND, "등록된 지원서가 없습니다. 먼저 지원서를 등록하세요.")
    return worker


def _completed_history(worker_id: str) -> list[dict[str, Any]]:
    """Assignments GSI1에서 완료 이력을 유도한다."""
    history = []
    for a in db.query_worker_assignments(worker_id):
        if a.get("status") == AssignmentStatus.COMPLETED:
            crew = db.get_crew(a["crew_id"])
            req = db.get_request(crew["request_id"]) if crew else None
            history.append(work_history_entry(a, req))
    return history


# ---------------------------------------------------------------------------
# 지원서 CRUD
# ---------------------------------------------------------------------------
@router.route("POST", "/worker/application")
def create_application(event, principal: Principal, _params):
    principal.require_role(Role.WORKER)
    body = parse_body(event)
    require_fields(
        body,
        ["name", "phone", "office_id", "preferred_trades",
         "career_years", "age", "region", "desired_daily_wage"],
    )
    preferred = validate_trades(body.get("preferred_trades"), "preferred_trades")
    excluded = validate_trades(body.get("excluded_trades"), "excluded_trades")

    if db.get_worker(principal.user_id):
        raise ApiError(ErrorCode.VALIDATION_ERROR, "이미 지원서가 존재합니다. 수정(PUT)을 이용하세요.")

    item = build_worker(
        user_id=principal.user_id,
        worker_id=principal.user_id,
        name=body["name"],
        phone=body["phone"],
        office_id=body["office_id"],
        preferred_trades=preferred,
        excluded_trades=excluded,
        career_years=int(body["career_years"]),
        age=int(body["age"]),
        region=body["region"],
        desired_daily_wage=int(body["desired_daily_wage"]),
        certifications=body.get("certifications") or [],
        state=WorkerState.INACTIVE,
    )
    try:
        db.put_worker(item, condition="attribute_not_exists(worker_id)")
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            raise ApiError(ErrorCode.VALIDATION_ERROR, "이미 지원서가 존재합니다.")
        raise
    return success(worker_self_view(item), status_code=201)


@router.route("PUT", "/worker/application")
def update_application(event, principal: Principal, _params):
    principal.require_role(Role.WORKER)
    worker = _load_own_worker(principal)
    body = parse_body(event)

    updates: dict[str, Any] = {}
    for field in _EDITABLE_FIELDS:
        if field in body and body[field] is not None:
            updates[field] = body[field]
    if not updates:
        raise ApiError(ErrorCode.VALIDATION_ERROR, "수정할 항목이 없습니다.")

    if "preferred_trades" in updates:
        updates["preferred_trades"] = validate_trades(updates["preferred_trades"], "preferred_trades")
    if "excluded_trades" in updates:
        updates["excluded_trades"] = validate_trades(updates["excluded_trades"], "excluded_trades")
    for int_field in ("career_years", "age", "desired_daily_wage"):
        if int_field in updates:
            updates[int_field] = int(updates[int_field])

    now = now_iso()
    set_parts = ["updated_at = :t"]
    expr_values: dict[str, Any] = {":t": now}
    expr_names: dict[str, str] = {}
    from shared.schemas import to_decimal
    for i, (key, value) in enumerate(updates.items()):
        set_parts.append(f"#f{i} = :v{i}")
        expr_names[f"#f{i}"] = key
        expr_values[f":v{i}"] = to_decimal(value)

    resp = db.update_worker(
        worker["worker_id"],
        UpdateExpression="SET " + ", ".join(set_parts),
        ExpressionAttributeNames=expr_names,
        ExpressionAttributeValues=expr_values,
        ReturnValues="ALL_NEW",
    )
    return success(worker_self_view(resp["Attributes"]))


@router.route("GET", "/worker/me")
def get_me(_event, principal: Principal, _params):
    principal.require_role(Role.WORKER)
    worker = _load_own_worker(principal)
    return success(worker_self_view(worker, work_history=_completed_history(worker["worker_id"])))


# ---------------------------------------------------------------------------
# 대기 상태 전환
# ---------------------------------------------------------------------------
def _simple_transition(worker: dict[str, Any], to_state: str) -> dict[str, Any]:
    now = now_iso()
    try:
        resp = db.update_worker(
            worker["worker_id"],
            UpdateExpression="SET #s = :to, gsi1sk = :gsi, state_changed_at = :t, updated_at = :t",
            ConditionExpression="#s = :from",
            ExpressionAttributeNames={"#s": "state"},
            ExpressionAttributeValues={
                ":to": to_state,
                ":from": worker["state"],
                ":gsi": db.worker_gsi1sk(to_state, worker["worker_id"]),
                ":t": now,
            },
            ReturnValues="ALL_NEW",
        )
        return resp["Attributes"]
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            raise ApiError(ErrorCode.STATE_CONFLICT, "상태가 이미 변경되어 요청을 처리할 수 없습니다.")
        raise


@router.route("POST", "/worker/state/ready")
def start_ready(_event, principal: Principal, _params):
    principal.require_role(Role.WORKER)
    worker = _load_own_worker(principal)
    if worker["state"] == WorkerState.READY:
        return success(worker_self_view(worker))
    if worker["state"] != WorkerState.INACTIVE:
        raise ApiError(ErrorCode.WORKER_NOT_READY, "대기 시작은 INACTIVE 상태에서만 가능합니다.")
    updated = _simple_transition(worker, WorkerState.READY)
    return success(worker_self_view(updated))


@router.route("POST", "/worker/state/inactive")
def cancel_ready(_event, principal: Principal, _params):
    principal.require_role(Role.WORKER)
    worker = _load_own_worker(principal)
    if worker["state"] == WorkerState.INACTIVE:
        return success(worker_self_view(worker))
    if worker["state"] in (WorkerState.NOTIFIED, WorkerState.RESERVED, WorkerState.RUNNING):
        raise ApiError(ErrorCode.WORKER_ALREADY_RUNNING, "현재 상태에서는 대기를 취소할 수 없습니다.")
    updated = _simple_transition(worker, WorkerState.INACTIVE)
    return success(worker_self_view(updated))


# ---------------------------------------------------------------------------
# 제안 수락 (트랜잭션 2)
# ---------------------------------------------------------------------------
@router.route("POST", "/worker/offer/accept")
def accept_offer(event, principal: Principal, _params):
    principal.require_role(Role.WORKER)
    worker = _load_own_worker(principal)
    offer = worker.get("current_offer")
    if worker["state"] != WorkerState.NOTIFIED or not offer:
        raise ApiError(ErrorCode.STATE_CONFLICT, "수락할 배정 제안이 없습니다.")

    body = parse_body(event)
    eta = body.get("eta")
    crew_id = offer["crew_id"]
    now = now_iso()

    entries = [
        txn.worker_entry(
            worker["worker_id"],
            now=now,
            to_state=WorkerState.RESERVED,
            from_states=[WorkerState.NOTIFIED],
            inc_dispatched=True,
        ),
        txn.assignment_update_entry(
            crew_id,
            worker["worker_id"],
            now=now,
            acceptance=Acceptance.ACCEPTED,
            status=AssignmentStatus.RESERVED,
            eta=eta if eta else txn._SENTINEL,
            require_acceptance=Acceptance.PENDING,
        ),
    ]
    try:
        txn.run(entries)
    except ClientError as exc:
        if exc.response["Error"]["Code"] in ("TransactionCanceledException", "ConditionalCheckFailedException"):
            raise ApiError(ErrorCode.STATE_CONFLICT, "제안 상태가 변경되어 수락할 수 없습니다.")
        raise

    _rollup_after_accept(crew_id)
    updated = db.get_worker(worker["worker_id"])
    return success(worker_self_view(updated))


def _rollup_after_accept(crew_id: str) -> None:
    """조원 전원 수락 시 Crew/Request → DISPATCHED, 진행 중 GapEvent → FILLED (파생 롤업)."""
    assignments = db.query_crew_assignments(crew_id)
    active = [a for a in assignments if a.get("acceptance") != Acceptance.DECLINED]
    if not active or any(a.get("acceptance") != Acceptance.ACCEPTED for a in active):
        return

    crew = db.get_crew(crew_id)
    if not crew:
        return
    # 기존 팀원 중 작업 중(RUNNING)이 있으면 RUNNING, 아니면 DISPATCHED
    any_running = False
    for a in active:
        w = db.get_worker(a["worker_id"])
        if w and w.get("state") == WorkerState.RUNNING:
            any_running = True
            break
    crew_status = CrewStatus.RUNNING if any_running else CrewStatus.DISPATCHED
    req_status = RequestStatus.RUNNING if any_running else RequestStatus.DISPATCHED
    now = now_iso()

    db.update_crew(
        crew_id,
        UpdateExpression="SET #s = :cs, gsi1sk = :g, updated_at = :t",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={
            ":cs": crew_status,
            ":g": db.crew_gsi1sk(crew_status, crew_id),
            ":t": now,
        },
    )
    db.update_request(
        crew["request_id"],
        UpdateExpression="SET #s = :rs, gsi1sk = :g, updated_at = :t",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={
            ":rs": req_status,
            ":g": db.request_gsi1sk(req_status, crew["request_id"]),
            ":t": now,
        },
    )
    # 진행 중 결원 이벤트가 있으면 충원 완료 처리
    for gap in db.query_gap_events_by_crew(crew_id):
        if gap.get("status") in ("APPROVED", "PROPOSED", "RECOMPOSING", "DETECTED"):
            db.update_gap_event(
                gap["event_id"],
                UpdateExpression="SET #s = :s, gsi1sk = :g, updated_at = :t",
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={
                    ":s": "FILLED",
                    ":g": db.gap_gsi1sk("FILLED", gap["event_id"]),
                    ":t": now,
                },
            )


# ---------------------------------------------------------------------------
# 제안 거절 (트랜잭션 3)
# ---------------------------------------------------------------------------
@router.route("POST", "/worker/offer/decline")
def decline_offer(_event, principal: Principal, _params):
    principal.require_role(Role.WORKER)
    worker = _load_own_worker(principal)
    offer = worker.get("current_offer")
    if worker["state"] != WorkerState.NOTIFIED or not offer:
        raise ApiError(ErrorCode.STATE_CONFLICT, "거절할 배정 제안이 없습니다.")

    crew_id = offer["crew_id"]
    crew = db.get_crew(crew_id)
    request_id = crew["request_id"] if crew else offer.get("request_id")
    now = now_iso()

    gap = build_gap_event_stub(worker, crew, request_id)

    entries = [
        txn.worker_entry(
            worker["worker_id"],
            now=now,
            to_state=WorkerState.READY,
            from_states=[WorkerState.NOTIFIED],
            current_offer=None,
            current_crew_id=None,
        ),
        txn.assignment_update_entry(
            crew_id,
            worker["worker_id"],
            now=now,
            acceptance=Acceptance.DECLINED,
            status=AssignmentStatus.DECLINED,
        ),
    ]
    if gap is not None:
        entries.append(txn.put_entry("gap_events", gap))
    if request_id:
        entries.append(
            txn.request_status_entry(
                request_id,
                to_status=RequestStatus.COMPOSING,
                now=now,
                extra_set={"declined_worker_ids": _append_declined(request_id, worker["worker_id"])},
            )
        )
    try:
        txn.run(entries)
    except ClientError as exc:
        if exc.response["Error"]["Code"] in ("TransactionCanceledException", "ConditionalCheckFailedException"):
            raise ApiError(ErrorCode.STATE_CONFLICT, "제안 상태가 변경되어 거절할 수 없습니다.")
        raise

    updated = db.get_worker(worker["worker_id"])
    return success(worker_self_view(updated))


def _append_declined(request_id: str, worker_id: str) -> list[str]:
    req = db.get_request(request_id)
    declined = list((req or {}).get("declined_worker_ids") or [])
    if worker_id not in declined:
        declined.append(worker_id)
    return declined


def build_gap_event_stub(worker, crew, request_id):
    from shared.schemas import build_gap_event
    if not crew or not request_id:
        return None
    return build_gap_event(
        office_id=crew["office_id"],
        crew_id=crew["crew_id"],
        request_id=request_id,
        gap_type=GapType.DECLINED,
        affected_worker_id=worker["worker_id"],
        affected_worker_name=worker.get("name", ""),
    )


# ---------------------------------------------------------------------------
# 내 배정 / 이력
# ---------------------------------------------------------------------------
@router.route("GET", "/worker/assignments")
def get_assignments(_event, principal: Principal, _params):
    principal.require_role(Role.WORKER)
    worker = _load_own_worker(principal)
    crew_id = worker.get("current_crew_id")
    if not crew_id:
        return success([])
    crew = db.get_crew(crew_id)
    if not crew:
        return success([])
    request = db.get_request(crew["request_id"])
    if not request:
        return success([])
    return success([
        {
            "crew_id": crew["crew_id"],
            "request_id": request["request_id"],
            "site_name": request.get("site_name"),
            "work_date": request.get("work_date"),
            "start_time": request.get("start_time"),
            "location_text": request.get("location_text"),
            "status": crew.get("status"),
        }
    ])


@router.route("GET", "/worker/history")
def get_history(_event, principal: Principal, _params):
    principal.require_role(Role.WORKER)
    worker = _load_own_worker(principal)
    return success(_completed_history(worker["worker_id"]))


# ---------------------------------------------------------------------------
# 배차완료(RESERVED) 취소 — 작업 시작 24시간 전까지만 (C-8)
# ---------------------------------------------------------------------------
def _work_start_dt(src: dict[str, Any] | None) -> datetime | None:
    """work_date(+start_time)를 UTC datetime으로 파싱. 실패 시 None."""
    src = src or {}
    work_date = src.get("work_date")
    start_time = src.get("start_time") or "00:00"
    if not work_date:
        return None
    try:
        return datetime.fromisoformat(f"{work_date}T{start_time}:00+00:00")
    except (ValueError, TypeError):
        return None


@router.route("POST", "/worker/reservation/cancel")
def cancel_reservation(_event, principal: Principal, _params):
    principal.require_role(Role.WORKER)
    worker = _load_own_worker(principal)
    if worker["state"] != WorkerState.RESERVED:
        raise ApiError(ErrorCode.STATE_CONFLICT, "배차완료(RESERVED) 상태에서만 취소할 수 있습니다.")

    offer = worker.get("current_offer") or {}
    crew_id = offer.get("crew_id") or worker.get("current_crew_id")
    crew = db.get_crew(crew_id) if crew_id else None
    request_id = crew["request_id"] if crew else offer.get("request_id")
    request = db.get_request(request_id) if request_id else None

    # 24시간 규칙: 작업 시작 24시간 이내에는 취소 불가.
    start_dt = _work_start_dt(offer if offer.get("work_date") else request)
    if start_dt is not None and (start_dt - datetime.now(timezone.utc)) < timedelta(hours=24):
        raise ApiError(ErrorCode.STATE_CONFLICT, "작업 시작 24시간 이내에는 취소할 수 없습니다.")

    now = now_iso()
    gap = build_gap_event_stub(worker, crew, request_id)
    entries = [
        txn.worker_entry(
            worker["worker_id"], now=now, to_state=WorkerState.READY,
            from_states=[WorkerState.RESERVED],
            current_offer=None, current_crew_id=None,
            dec_dispatched=True,   # 24시간 이전 취소는 배차완료 카운트에서 제외
        ),
    ]
    if crew_id:
        entries.append(txn.assignment_update_entry(
            crew_id, worker["worker_id"], now=now,
            acceptance=Acceptance.DECLINED, status=AssignmentStatus.CANCELLED,
        ))
    if gap is not None:
        entries.append(txn.put_entry("gap_events", gap))
    if request_id:
        entries.append(txn.request_status_entry(
            request_id, to_status=RequestStatus.COMPOSING, now=now,
            extra_set={"declined_worker_ids": _append_declined(request_id, worker["worker_id"])},
        ))
    try:
        txn.run(entries)
    except ClientError as exc:
        if exc.response["Error"]["Code"] in ("TransactionCanceledException", "ConditionalCheckFailedException"):
            raise ApiError(ErrorCode.STATE_CONFLICT, "상태가 변경되어 취소할 수 없습니다.")
        raise

    if crew:
        office = db.get_office(crew["office_id"]) or {}
        _notify_office(office, request, worker)
    return success(worker_self_view(db.get_worker(worker["worker_id"])))


def _notify_office(office, request, worker):
    from shared.schemas import build_notification
    user_id = office.get("owner_user_id") or office.get("office_id")
    if not user_id:
        return
    try:
        db.put_notification(build_notification(
            user_id=user_id, type="GAP_EVENT", title="배차 취소",
            message=f"{worker.get('name', '')}님이 배차를 취소했습니다. 재편성이 필요합니다.",
        ))
    except ClientError:
        pass


# ---------------------------------------------------------------------------
# 내가 수락한 작업 이력 (C-12)
# ---------------------------------------------------------------------------
@router.route("GET", "/worker/accepted-jobs")
def get_accepted_jobs(_event, principal: Principal, _params):
    principal.require_role(Role.WORKER)
    worker = _load_own_worker(principal)
    jobs = []
    for a in db.query_worker_assignments(worker["worker_id"], limit=200):
        if a.get("acceptance") != Acceptance.ACCEPTED:
            continue
        crew = db.get_crew(a["crew_id"])
        req = db.get_request(crew["request_id"]) if crew else None
        jobs.append({
            "crew_id": a.get("crew_id"),
            "request_id": (req or {}).get("request_id"),
            "site_name": (req or {}).get("site_name"),
            "work_date": (req or {}).get("work_date"),
            "start_time": (req or {}).get("start_time"),
            "location_text": (req or {}).get("location_text"),
            "assigned_trade": a.get("assigned_trade"),
            "offered_wage": clean(a.get("offered_wage")),
            "status": a.get("status"),
            "accepted_at": a.get("updated_at") or a.get("created_at"),
        })
    return success(jobs)


# ---------------------------------------------------------------------------
# 출근일 집계 — 히트맵(잔디)용 (C-13)
#   출근(체크인)한 날만 집계: RUNNING/COMPLETED/LEFT_SITE. 노쇼/거절/취소는 제외.
# ---------------------------------------------------------------------------
_ATTENDED_STATUSES = {AssignmentStatus.RUNNING, AssignmentStatus.COMPLETED, AssignmentStatus.LEFT_SITE}


@router.route("GET", "/worker/attendance")
def get_attendance(_event, principal: Principal, _params):
    principal.require_role(Role.WORKER)
    worker = _load_own_worker(principal)
    counts: dict[str, int] = {}
    for a in db.query_worker_assignments(worker["worker_id"], limit=400):
        if a.get("status") not in _ATTENDED_STATUSES:
            continue
        crew = db.get_crew(a["crew_id"])
        req = db.get_request(crew["request_id"]) if crew else None
        work_date = (req or {}).get("work_date")
        if work_date:
            counts[work_date] = counts.get(work_date, 0) + 1
    return success(counts)


# ---------------------------------------------------------------------------
# 경력 연차별 평균 희망 일당 (C-10) — 직종 무관, 동일 연차 지원자 평균
# ---------------------------------------------------------------------------
@router.route("GET", "/worker/wage-stats/{careerYears}")
def wage_stats(_event, principal: Principal, params):
    principal.require_role(Role.WORKER)
    try:
        years = int(params.get("careerYears"))
    except (TypeError, ValueError):
        raise ApiError(ErrorCode.VALIDATION_ERROR, "career_years(정수)가 필요합니다.")
    wages = [
        int(w.get("desired_daily_wage", 0))
        for w in db.scan_workers()
        if int(w.get("career_years", -1)) == years and int(w.get("desired_daily_wage", 0)) > 0
    ]
    if not wages:
        return success({"career_years": years, "average_wage": None, "sample_count": 0})
    return success({
        "career_years": years,
        "average_wage": round(sum(wages) / len(wages)),
        "sample_count": len(wages),
    })


def lambda_handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    return router.dispatch(event)
