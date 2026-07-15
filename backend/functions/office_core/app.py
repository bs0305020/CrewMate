"""office_core Lambda (계약 v2).

Route:
  GET  /office/workers                          소속 근로자 조회 (필터)
  GET  /office/requests                         요청 목록 (GSI1, 토큰 office_id 기준)
  GET  /office/requests/{requestId}             요청 상세 (+작업조)
  POST /office/requests/{requestId}/reject      요청 거절 (+COMPANY 알림)
  POST /office/crews/manual                     수동 편성 (Crew DRAFT)
  POST /office/crews/{crewId}/fill-gap          결원 수동 충원 (대체자만 NOTIFIED)
  POST /office/crews/{crewId}/cancel-composition 편성 취소 (트랜잭션 7)
  POST /office/cancel-offer                     제안 취소 (트랜잭션 7)
  GET  /office/gap-events                        결원 이벤트 목록
  GET  /office/gap-events/{eventId}             결원 이벤트 상세

권한: OFFICE. 토큰의 office_id 기준(하드코딩 금지).
"""

from __future__ import annotations

import logging
from typing import Any

from botocore.exceptions import ClientError

from shared import db, txn
from shared.auth import Principal
from shared.crew import (
    assemble_crew_members,
    normalize_member_inputs,
    validate_budget,
    validate_candidates_state,
    validate_excluded_trades,
    validate_members_unique,
    validate_required_coverage,
)
from shared.responses import ApiError, ErrorCode, success
from shared.schemas import (
    build_assignment,
    build_crew,
    build_gap_event,
    build_notification,
    build_offer,
    crew_view,
    gap_view,
    now_iso,
    parse_body,
    request_view,
    require_fields,
    validate_trade,
    worker_office_view,
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
from shared.routing import Router

logger = logging.getLogger()
router = Router()


def _qp(event) -> dict[str, str]:
    return event.get("queryStringParameters") or {}


# ---------------------------------------------------------------------------
# 근로자 조회
# ---------------------------------------------------------------------------
@router.route("GET", "/office/workers")
def list_workers(event, principal: Principal, _params):
    principal.require_role(Role.OFFICE)
    qp = _qp(event)
    state = qp.get("state")
    if state:
        if state not in WorkerState.ALL:
            raise ApiError(ErrorCode.VALIDATION_ERROR, f"알 수 없는 상태입니다: {state}")
        workers = db.query_office_workers_by_state(principal.office_id, state)
    else:
        workers = db.query_office_all_workers(principal.office_id)
    workers = _apply_filters(workers, qp)
    return success([worker_office_view(w) for w in workers])


def _apply_filters(workers, qp):
    trade = qp.get("trade")
    region = qp.get("region")
    min_career = qp.get("min_career_years")
    min_wage = qp.get("min_wage")
    max_wage = qp.get("max_wage")
    if trade:
        validate_trade(trade)
    result = []
    for w in workers:
        if trade and trade not in (w.get("preferred_trades") or []):
            continue
        if region and w.get("region") != region:
            continue
        if min_career and int(w.get("career_years", 0)) < int(min_career):
            continue
        wage = int(w.get("desired_daily_wage", 0))
        if min_wage and wage < int(min_wage):
            continue
        if max_wage and wage > int(max_wage):
            continue
        result.append(w)
    return result


# ---------------------------------------------------------------------------
# 요청 조회 / 거절
# ---------------------------------------------------------------------------
_company_name_cache: dict[str, str] = {}


def _company_name(company_id: str | None) -> str:
    """요청한 건설사 이름 (office 화면 표시용). 캐시로 반복 조회 완화."""
    if not company_id:
        return ""
    if company_id not in _company_name_cache:
        company = db.get_company(company_id) or {}
        _company_name_cache[company_id] = company.get("name") or company_id
    return _company_name_cache[company_id]


@router.route("GET", "/office/requests")
def list_requests(_event, principal: Principal, _params):
    principal.require_role(Role.OFFICE)
    items = db.query_office_requests(principal.office_id)
    return success([
        {**request_view(r), "company_name": _company_name(r.get("company_id"))}
        for r in items
    ])


@router.route("GET", "/office/requests/{requestId}")
def get_request(_event, principal: Principal, params):
    principal.require_role(Role.OFFICE)
    req = db.get_request(params["requestId"])
    if not req:
        raise ApiError(ErrorCode.REQUEST_NOT_FOUND, "요청을 찾을 수 없습니다.")
    principal.require_office(req["office_id"])
    result = request_view(req)
    result["company_name"] = _company_name(req.get("company_id"))
    result["crew"] = _active_crew_view(req["request_id"])
    return success(result)


def _active_crew_view(request_id: str):
    crews = [c for c in db.query_crews_by_request(request_id) if c.get("status") != CrewStatus.CANCELLED]
    if not crews:
        return None
    crew = sorted(crews, key=lambda c: c.get("created_at", ""))[-1]
    return crew_view(crew, assemble_crew_members(crew, active_only=False))


@router.route("POST", "/office/requests/{requestId}/reject")
def reject_request(event, principal: Principal, params):
    principal.require_role(Role.OFFICE)
    req = db.get_request(params["requestId"])
    if not req:
        raise ApiError(ErrorCode.REQUEST_NOT_FOUND, "요청을 찾을 수 없습니다.")
    principal.require_office(req["office_id"])
    if req["status"] != RequestStatus.REQUESTED:
        raise ApiError(ErrorCode.STATE_CONFLICT, "이미 처리된 요청입니다.")
    body = parse_body(event)
    reason = body.get("reason") or ""
    now = now_iso()
    resp = db.update_request(
        req["request_id"],
        UpdateExpression="SET #s = :s, gsi1sk = :g, rejection_reason = :r, updated_at = :t",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={
            ":s": RequestStatus.REJECTED,
            ":g": db.request_gsi1sk(RequestStatus.REJECTED, req["request_id"]),
            ":r": reason,
            ":t": now,
        },
        ReturnValues="ALL_NEW",
    )
    company = db.get_company(req["company_id"]) or {}
    _notify(company.get("owner_user_id") or req["company_id"], "REQUEST_REJECTED", "요청 거절",
            f"'{req.get('site_name')}' 요청이 거절되었습니다. 사유: {reason}")
    return success(request_view(resp["Attributes"]))


# ---------------------------------------------------------------------------
# 수동 편성
# ---------------------------------------------------------------------------
@router.route("POST", "/office/crews/manual")
def create_manual_crew(event, principal: Principal, _params):
    principal.require_role(Role.OFFICE)
    body = parse_body(event)
    require_fields(body, ["request_id", "members"])
    request = db.get_request(body["request_id"])
    if not request:
        raise ApiError(ErrorCode.REQUEST_NOT_FOUND, "요청을 찾을 수 없습니다.")
    principal.require_office(request["office_id"])
    if request["status"] in (RequestStatus.RUNNING, RequestStatus.COMPLETED, RequestStatus.REJECTED):
        raise ApiError(ErrorCode.REQUEST_ALREADY_ASSIGNED, "편성할 수 없는 요청 상태입니다.")

    workers_by_id = _load_workers(m["worker_id"] for m in body["members"])
    members = normalize_member_inputs(body["members"], workers_by_id)
    validate_members_unique([m["worker_id"] for m in members])
    validate_candidates_state(members, workers_by_id, office_id=principal.office_id)
    validate_excluded_trades(members, workers_by_id)
    validate_required_coverage(members, request.get("required_workers", []))
    total_cost = validate_budget(members, request.get("budget", 0))

    _cancel_existing_crews(request["request_id"])
    crew = build_crew(
        office_id=principal.office_id,
        request_id=request["request_id"],
        proposed_members=members,
        source="MANUAL",
        total_cost=total_cost,
    )
    db.put_crew(crew)
    return success(crew_view(crew, assemble_crew_members(crew)), status_code=201)


# ---------------------------------------------------------------------------
# 결원 수동 충원 (대체자만 직접 NOTIFIED) — 트랜잭션 1 경로(대체자 한정)
# ---------------------------------------------------------------------------
@router.route("POST", "/office/crews/{crewId}/fill-gap")
def fill_gap(event, principal: Principal, params):
    principal.require_role(Role.OFFICE)
    crew_id = params["crewId"]
    crew = db.get_crew(crew_id)
    if not crew:
        raise ApiError(ErrorCode.CREW_INVALID, "작업조를 찾을 수 없습니다.")
    principal.require_office(crew["office_id"])
    request = db.get_request(crew["request_id"])
    if not request:
        raise ApiError(ErrorCode.REQUEST_NOT_FOUND, "요청을 찾을 수 없습니다.")

    body = parse_body(event)
    require_fields(body, ["members"])
    workers_by_id = _load_workers(m["worker_id"] for m in body["members"])
    new_members = normalize_member_inputs(body["members"], workers_by_id)
    for m in new_members:
        m["is_replacement"] = True
    validate_members_unique([m["worker_id"] for m in new_members])
    validate_candidates_state(new_members, workers_by_id, office_id=crew["office_id"])
    validate_excluded_trades(new_members, workers_by_id)

    # 고정 인원 = 기존 활성 조원 (거절/이탈 제외)
    fixed = assemble_crew_members(crew)
    fixed_cost = sum(int(m.get("offered_wage", 0)) for m in fixed)
    validate_budget(new_members, request.get("budget", 0), fixed_cost=fixed_cost)

    now = now_iso()
    entries = []
    for m in new_members:
        offer = build_offer(crew_id, request, m, is_emergency=True)
        entries.append(txn.worker_entry(
            m["worker_id"], now=now, to_state=WorkerState.NOTIFIED,
            from_states=[WorkerState.READY], current_offer=offer,
            current_crew_id=crew_id, require_no_offer=True,
        ))
        entries.append(txn.assignment_put_entry(build_assignment(
            crew_id=crew_id, worker_id=m["worker_id"],
            assigned_trade=m["assigned_trade"], offered_wage=m["offered_wage"],
            acceptance=Acceptance.PENDING, status=AssignmentStatus.RESERVED,
            is_replacement=True, notified_at=now,
        )))
    entries.append(txn.crew_status_entry(crew_id, to_status=CrewStatus.NOTIFIED, now=now))
    _run(entries, "결원 충원")

    # member_ids 갱신 (고정 + 신규)
    new_ids = [m["worker_id"] for m in fixed] + [m["worker_id"] for m in new_members]
    db.update_crew(crew_id, UpdateExpression="SET member_ids = :m, updated_at = :t",
                   ExpressionAttributeValues={":m": new_ids, ":t": now})
    for m in new_members:
        w = workers_by_id.get(m["worker_id"], {})
        _notify(w.get("user_id"), "OFFER", "배정 제안",
                f"{request.get('site_name')} 대체 인력 제안이 도착했습니다.")

    crew = db.get_crew(crew_id)
    return success(crew_view(crew, assemble_crew_members(crew)))


# ---------------------------------------------------------------------------
# 제안 취소 (트랜잭션 7) — 단일 근로자 NOTIFIED→READY
# ---------------------------------------------------------------------------
@router.route("POST", "/office/cancel-offer")
def cancel_offer(event, principal: Principal, _params):
    principal.require_role(Role.OFFICE)
    body = parse_body(event)
    require_fields(body, ["worker_id"])
    worker_id = body["worker_id"]
    worker = db.get_worker(worker_id)
    if not worker:
        raise ApiError(ErrorCode.WORKER_NOT_FOUND, "근로자를 찾을 수 없습니다.")
    if worker.get("office_id") != principal.office_id:
        raise ApiError(ErrorCode.FORBIDDEN, "다른 사무소의 근로자입니다.")
    if worker["state"] != WorkerState.NOTIFIED:
        raise ApiError(ErrorCode.STATE_CONFLICT, "제안 취소는 NOTIFIED 상태에서만 가능합니다.")

    crew_id = (worker.get("current_offer") or {}).get("crew_id") or worker.get("current_crew_id")
    crew = db.get_crew(crew_id) if crew_id else None
    request = db.get_request(crew["request_id"]) if crew else None
    now = now_iso()
    entries = [
        txn.worker_entry(worker_id, now=now, to_state=WorkerState.READY,
                         from_states=[WorkerState.NOTIFIED],
                         current_offer=None, current_crew_id=None),
    ]
    if crew_id:
        entries.append(txn.assignment_update_entry(
            crew_id, worker_id, now=now,
            acceptance=Acceptance.DECLINED, status=AssignmentStatus.CANCELLED,
        ))
    # 빈 자리(결원)로 등록해 AI/수동 재편성 경로를 열어준다 (B-6).
    gap = None
    if crew and request:
        gap = build_gap_event(
            office_id=crew["office_id"], crew_id=crew_id, request_id=request["request_id"],
            gap_type=GapType.UNAVAILABLE, affected_worker_id=worker_id,
            affected_worker_name=worker.get("name", ""),
        )
        entries.append(txn.put_entry("gap_events", gap))
        declined = list(request.get("declined_worker_ids") or [])
        if worker_id not in declined:
            declined.append(worker_id)
        entries.append(txn.request_status_entry(
            request["request_id"], to_status=RequestStatus.COMPOSING, now=now,
            extra_set={"declined_worker_ids": declined},
        ))
    _run(entries, "제안 취소")
    _notify(worker.get("user_id"), "OFFER_CANCELLED", "제안 취소", "배정 제안이 취소되었습니다.")
    if request:
        office = db.get_office(crew["office_id"]) or {}
        _notify(office.get("owner_user_id") or crew["office_id"], "GAP_EVENT", "빈 자리 발생",
                f"{worker.get('name', '')}님의 제안이 취소되어 빈 자리가 발생했습니다. 재편성이 필요합니다.")
    return success(worker_office_view(db.get_worker(worker_id)))


# ---------------------------------------------------------------------------
# 편성 취소 (트랜잭션 7) — 전 조원 원상 복구, Crew CANCELLED, Request REQUESTED
# ---------------------------------------------------------------------------
@router.route("POST", "/office/crews/{crewId}/cancel-composition")
def cancel_composition(_event, principal: Principal, params):
    principal.require_role(Role.OFFICE)
    crew_id = params["crewId"]
    crew = db.get_crew(crew_id)
    if not crew:
        raise ApiError(ErrorCode.CREW_INVALID, "작업조를 찾을 수 없습니다.")
    principal.require_office(crew["office_id"])
    # 이미 취소되었거나 작업이 시작/완료된 편성은 취소할 수 없다.
    if crew["status"] in (CrewStatus.CANCELLED, CrewStatus.RUNNING, CrewStatus.COMPLETED):
        raise ApiError(ErrorCode.STATE_CONFLICT, "이미 작업이 시작/완료되었거나 취소된 편성입니다.")
    request = db.get_request(crew["request_id"])
    if request and request["status"] in (RequestStatus.RUNNING, RequestStatus.COMPLETED):
        raise ApiError(ErrorCode.STATE_CONFLICT, "작업이 시작/완료된 요청의 편성은 취소할 수 없습니다.")

    now = now_iso()
    entries = []
    restored_workers = []
    for a in db.query_crew_assignments(crew_id):
        # 이미 이탈/거절/노쇼/취소된 배치는 되돌릴 것이 없다.
        if a.get("acceptance") == Acceptance.DECLINED or a.get("status") in (
            AssignmentStatus.DECLINED, AssignmentStatus.CANCELLED,
            AssignmentStatus.NO_SHOW, AssignmentStatus.LEFT_SITE,
        ):
            continue
        worker = db.get_worker(a["worker_id"])
        if worker and worker.get("state") in (WorkerState.NOTIFIED, WorkerState.RESERVED):
            entries.append(txn.worker_entry(
                a["worker_id"], now=now, to_state=WorkerState.READY,
                from_states=[WorkerState.NOTIFIED, WorkerState.RESERVED],
                current_offer=None, current_crew_id=None,
            ))
            entries.append(txn.assignment_update_entry(
                crew_id, a["worker_id"], now=now,
                acceptance=Acceptance.DECLINED, status=AssignmentStatus.CANCELLED,
            ))
            restored_workers.append(worker)
    entries.append(txn.crew_status_entry(crew_id, to_status=CrewStatus.CANCELLED, now=now))
    if request:
        entries.append(txn.request_status_entry(
            request["request_id"], to_status=RequestStatus.CANCELLED, now=now,
        ))
    _run(entries, "편성 취소")

    # 이 편성에 연결된 진행 중 결원 이벤트를 종료(FAILED)해 댕글링을 막는다.
    for g in db.query_gap_events_by_crew(crew_id):
        if g.get("status") in (GapStatus.DETECTED, GapStatus.RECOMPOSING,
                               GapStatus.PROPOSED, GapStatus.APPROVED):
            db.update_gap_event(
                g["event_id"],
                UpdateExpression="SET #s = :s, gsi1sk = :g, updated_at = :t",
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={
                    ":s": GapStatus.FAILED,
                    ":g": db.gap_gsi1sk(GapStatus.FAILED, g["event_id"]),
                    ":t": now,
                },
            )

    for w in restored_workers:
        _notify(w.get("user_id"), "COMPOSITION_CANCELLED", "편성 취소",
                f"{(request or {}).get('site_name', '현장')} 작업조 편성이 취소되었습니다.")
    if request:
        company = db.get_company(request["company_id"]) or {}
        _notify(company.get("owner_user_id") or request["company_id"], "COMPOSITION_CANCELLED",
                "편성 취소", f"'{request.get('site_name')}' 요청의 편성이 취소되었습니다.")

    crew = db.get_crew(crew_id)
    return success(crew_view(crew, assemble_crew_members(crew)))


# ---------------------------------------------------------------------------
# 결원 이벤트 조회
# ---------------------------------------------------------------------------
@router.route("GET", "/office/gap-events")
def list_gap_events(_event, principal: Principal, _params):
    principal.require_role(Role.OFFICE)
    items = db.query_office_gap_events(principal.office_id)
    return success([gap_view(g) for g in items])


@router.route("GET", "/office/gap-events/{eventId}")
def get_gap_event(_event, principal: Principal, params):
    principal.require_role(Role.OFFICE)
    gap = db.get_gap_event(params["eventId"])
    if not gap:
        raise ApiError(ErrorCode.GAP_EVENT_NOT_FOUND, "결원 이벤트를 찾을 수 없습니다.")
    principal.require_office(gap["office_id"])
    return success(gap_view(gap))


# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------
def _load_workers(worker_ids) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for wid in worker_ids:
        if wid not in out:
            w = db.get_worker(wid)
            if w:
                out[wid] = w
    return out


def _cancel_existing_crews(request_id: str):
    now = now_iso()
    for c in db.query_crews_by_request(request_id):
        if c.get("status") in (CrewStatus.CANCELLED, CrewStatus.RUNNING, CrewStatus.COMPLETED, CrewStatus.DISPATCHED):
            continue
        db.update_crew(
            c["crew_id"],
            UpdateExpression="SET #s = :s, gsi1sk = :g, updated_at = :t",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":s": CrewStatus.CANCELLED,
                ":g": db.crew_gsi1sk(CrewStatus.CANCELLED, c["crew_id"]),
                ":t": now,
            },
        )


def _run(entries, label):
    try:
        txn.run(entries)
    except ClientError as exc:
        if exc.response["Error"]["Code"] in ("TransactionCanceledException", "ConditionalCheckFailedException"):
            raise ApiError(ErrorCode.STATE_CONFLICT, f"{label} 처리 중 상태 충돌이 발생했습니다.")
        raise


def _notify(user_id, ntype, title, message):
    if not user_id:
        return
    try:
        db.put_notification(build_notification(user_id=user_id, type=ntype, title=title, message=message))
    except ClientError:
        logger.exception("notify_failed user=%s", user_id)


def lambda_handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    return router.dispatch(event)
