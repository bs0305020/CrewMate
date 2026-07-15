"""엔터티 빌더/직렬화 헬퍼 (계약 v2).

- 아이템 빌더: Worker / Office / Company / Request / Crew / Assignment / GapEvent / Notification
- 뷰 필터: 역할별 응답에서 내부/성실도 데이터 노출 통제
- 유틸: UUID, ISO8601 timestamp, float -> Decimal 변환, 본문 파싱

성실도 노출 규칙 (README §4 / PROMPT §3):
- completed_count / dispatched_count 원시값만 저장.
- OFFICE 응답에만 두 카운트를 포함한다.
- WORKER 본인 응답·COMPANY 응답에는 포함하지 않는다.
- no_show_count 등 부정 라벨 필드는 저장·반환하지 않는다.
"""

from __future__ import annotations

import decimal
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from .db import (
    crew_gsi1sk,
    gap_gsi1sk,
    notification_sk,
    request_gsi1sk,
    worker_gsi1sk,
)
from .responses import ApiError, ErrorCode
from .state import (
    Acceptance,
    CrewStatus,
    GapStatus,
    RequestStatus,
    Trade,
    WorkerState,
)


# ---------------------------------------------------------------------------
# 유틸
# ---------------------------------------------------------------------------
def new_id(prefix: str = "") -> str:
    uid = str(uuid.uuid4())
    return f"{prefix}{uid}" if prefix else uid


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def to_decimal(value: Any) -> Any:
    """float를 DynamoDB 저장용 Decimal로 변환한다 (재귀)."""
    if isinstance(value, float):
        return decimal.Decimal(str(value))
    if isinstance(value, list):
        return [to_decimal(v) for v in value]
    if isinstance(value, dict):
        return {k: to_decimal(v) for k, v in value.items()}
    return value


def clean(value: Any) -> Any:
    """DynamoDB Decimal을 JSON 친화적인 int/float로 되돌린다 (응답 직렬화용)."""
    if isinstance(value, decimal.Decimal):
        return int(value) if value % 1 == 0 else float(value)
    if isinstance(value, list):
        return [clean(v) for v in value]
    if isinstance(value, dict):
        return {k: clean(v) for k, v in value.items()}
    return value


def parse_body(event: dict[str, Any]) -> dict[str, Any]:
    raw = event.get("body")
    if raw is None or raw == "":
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        raise ApiError(ErrorCode.VALIDATION_ERROR, "요청 본문이 유효한 JSON이 아닙니다.")


def require_fields(data: dict[str, Any], fields: list[str]) -> None:
    missing = [f for f in fields if data.get(f) in (None, "")]
    if missing:
        raise ApiError(
            ErrorCode.VALIDATION_ERROR,
            f"필수 항목이 누락되었습니다: {', '.join(missing)}",
        )


def validate_trade(trade: str) -> None:
    if trade not in Trade.ALL:
        raise ApiError(ErrorCode.VALIDATION_ERROR, f"알 수 없는 직종입니다: {trade}")


def validate_required_trade(trade: str) -> None:
    """요청(required_workers)의 직종 검증. 실제 직종 + 직종 무관(ANY) 허용."""
    if trade not in Trade.REQUIRED_ALL:
        raise ApiError(ErrorCode.VALIDATION_ERROR, f"알 수 없는 직종입니다: {trade}")


def validate_trades(trades: Any, field: str) -> list[str]:
    if trades is None:
        return []
    if not isinstance(trades, list):
        raise ApiError(ErrorCode.VALIDATION_ERROR, f"{field}는 배열이어야 합니다.")
    for t in trades:
        validate_trade(t)
    return list(trades)


# ---------------------------------------------------------------------------
# Worker
#   주민등록번호 필드는 어떤 형태로도 추가하지 않는다.
# ---------------------------------------------------------------------------
def build_worker(
    *,
    user_id: str,
    name: str,
    phone: str,
    office_id: str,
    preferred_trades: list[str],
    excluded_trades: list[str],
    career_years: int,
    age: int,
    region: str,
    desired_daily_wage: int,
    certifications: list[str] | None = None,
    worker_id: str | None = None,
    state: str = WorkerState.INACTIVE,
    completed_count: int = 0,
    dispatched_count: int = 0,
    attended_count: int = 0,
    rating_sum: int = 0,
    rating_count: int = 0,
) -> dict[str, Any]:
    wid = worker_id or new_id()
    ts = now_iso()
    item = {
        "worker_id": wid,
        "user_id": user_id,
        "name": name,
        "phone": phone,
        "office_id": office_id,
        "gsi1sk": worker_gsi1sk(state, wid),
        "state": state,
        "preferred_trades": preferred_trades,
        "excluded_trades": excluded_trades,
        "career_years": career_years,
        "age": age,
        "region": region,
        "desired_daily_wage": desired_daily_wage,
        "certifications": certifications or [],
        "completed_count": completed_count,
        "dispatched_count": dispatched_count,   # 배차완료(수락 확정) 수
        "attended_count": attended_count,       # 출근(체크인) 수
        "rating_sum": rating_sum,               # 평점 합계(내부 누적)
        "rating_count": rating_count,           # 평점 개수(내부 누적)
        "current_crew_id": None,
        "current_offer": None,
        "state_changed_at": ts,
        "created_at": ts,
        "updated_at": ts,
    }
    return to_decimal(item)


_WORKER_INTERNAL_KEYS = frozenset({"gsi1sk", "rating_sum"})


def rating_average(worker: dict[str, Any]) -> float | None:
    """평점 평균(5점 만점, 소수 1자리). 평점이 없으면 None."""
    count = int(worker.get("rating_count", 0) or 0)
    if count <= 0:
        return None
    total = int(worker.get("rating_sum", 0) or 0)
    return round(total / count, 1)


def worker_office_view(worker: dict[str, Any], work_history: list | None = None) -> dict[str, Any]:
    """OFFICE 응답용: 성실도 카운트(completed/dispatched)·평점 포함, 내부 키만 제거."""
    view = {k: clean(v) for k, v in worker.items() if k not in _WORKER_INTERNAL_KEYS}
    view["rating"] = rating_average(worker)
    view["work_history"] = clean(work_history or [])
    return view


def worker_self_view(worker: dict[str, Any], work_history: list | None = None) -> dict[str, Any]:
    """WORKER 본인 응답용: 평점(rating)·출근 수(attended_count)·배차완료 수(dispatched_count)·
    완료 수(completed_count)를 노출한다. rating_sum(내부 누적)만 숨긴다."""
    view = {k: clean(v) for k, v in worker.items() if k not in _WORKER_INTERNAL_KEYS}
    view["rating"] = rating_average(worker)
    view["work_history"] = clean(work_history or [])
    return view


def worker_public_view(worker: dict[str, Any]) -> dict[str, Any]:
    """COMPANY/타 근로자 응답용: 성실도·연락처 등 제외."""
    return {
        "worker_id": worker.get("worker_id"),
        "name": worker.get("name"),
        "career_years": clean(worker.get("career_years")),
        "preferred_trades": clean(worker.get("preferred_trades") or []),
    }


# ---------------------------------------------------------------------------
# Office / Company
# ---------------------------------------------------------------------------
def build_office(
    *,
    name: str,
    region: str,
    office_id: str | None = None,
    location_text: str = "",
    owner_name: str = "",
    owner_user_id: str = "",
    phone: str = "",
) -> dict[str, Any]:
    oid = office_id or new_id("OFFICE_")
    return {
        "office_id": oid,
        "name": name,
        "region": region,
        "location_text": location_text,
        "owner_name": owner_name,
        "owner_user_id": owner_user_id,
        "phone": phone,
        "created_at": now_iso(),
    }


def office_public_view(office: dict[str, Any], worker_count: int = 0, active: bool = True) -> dict[str, Any]:
    """GET /offices 응답용 (Office 계약: office_id, name, region, worker_count, active)."""
    return {
        "office_id": office.get("office_id"),
        "name": office.get("name"),
        "region": office.get("region"),
        "worker_count": worker_count,
        "active": active,
    }


def build_company(
    *,
    name: str,
    region: str = "",
    company_id: str | None = None,
    owner_name: str = "",
    owner_user_id: str = "",
    phone: str = "",
) -> dict[str, Any]:
    cid = company_id or new_id("COMPANY_")
    return {
        "company_id": cid,
        "name": name,
        "region": region,
        "owner_name": owner_name,
        "owner_user_id": owner_user_id,
        "phone": phone,
        "created_at": now_iso(),
    }


# ---------------------------------------------------------------------------
# WorkRequest
# ---------------------------------------------------------------------------
def build_request(
    *,
    company_id: str,
    office_id: str,
    site_name: str,
    work_date: str,
    start_time: str,
    location_text: str,
    required_workers: list[dict[str, Any]],
    budget: int,
    priority: dict[str, Any] | None = None,
    notes: str = "",
    request_id: str | None = None,
    status: str = RequestStatus.REQUESTED,
) -> dict[str, Any]:
    rid = request_id or new_id()
    ts = now_iso()
    item = {
        "request_id": rid,
        "company_id": company_id,
        "office_id": office_id,
        "gsi1sk": request_gsi1sk(status, rid),
        "site_name": site_name,
        "work_date": work_date,
        "start_time": start_time,
        "location_text": location_text,
        "required_workers": required_workers,
        "budget": budget,
        "priority": priority or {},
        "notes": notes,
        "status": status,
        "rejection_reason": None,
        "declined_worker_ids": [],
        "created_at": ts,
        "updated_at": ts,
    }
    return to_decimal(item)


def request_view(req: dict[str, Any]) -> dict[str, Any]:
    return {k: clean(v) for k, v in req.items() if k not in {"gsi1sk"}}


# ---------------------------------------------------------------------------
# Crew
#   proposed_members: [{worker_id, assigned_trade, offered_wage, is_replacement?}]
#   승인 전 조원 상세는 여기서, 승인 후에는 Assignments 조인으로 구성한다.
# ---------------------------------------------------------------------------
def build_crew(
    *,
    office_id: str,
    request_id: str,
    proposed_members: list[dict[str, Any]],
    status: str = CrewStatus.DRAFT,
    crew_id: str | None = None,
    source: str = "MANUAL",       # MANUAL / AGENT
    reason: str = "",
    considerations: list[str] | None = None,
    total_cost: int | None = None,
    recommendations: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    cid = crew_id or new_id()
    ts = now_iso()
    member_ids = [m["worker_id"] for m in proposed_members]
    item = {
        "crew_id": cid,
        "office_id": office_id,
        "request_id": request_id,
        "gsi1sk": crew_gsi1sk(status, cid),
        "member_ids": member_ids,
        "proposed_members": proposed_members,
        "status": status,
        "source": source,
        "reason": reason,
        "considerations": considerations or [],
        "total_cost": total_cost,
        "recommendations": recommendations or [],
        "created_at": ts,
        "updated_at": ts,
    }
    return to_decimal(item)


def crew_view(crew: dict[str, Any], members: list[dict[str, Any]]) -> dict[str, Any]:
    """Crew 응답 (members는 caller가 Assignments/proposed_members 조인으로 전달)."""
    return {
        "crew_id": crew.get("crew_id"),
        "request_id": crew.get("request_id"),
        "office_id": crew.get("office_id"),
        "status": crew.get("status"),
        "source": crew.get("source"),
        "member_ids": clean(crew.get("member_ids") or []),
        "members": clean(members),
        "recommendations": clean(crew.get("recommendations") or []),
        "total_cost": clean(crew.get("total_cost")),
        "reason": crew.get("reason", ""),
        "considerations": clean(crew.get("considerations") or []),
        "created_at": crew.get("created_at"),
        "updated_at": crew.get("updated_at"),
    }


# ---------------------------------------------------------------------------
# Assignment (CrewMember 원본)
# ---------------------------------------------------------------------------
def build_assignment(
    *,
    crew_id: str,
    worker_id: str,
    assigned_trade: str,
    offered_wage: int,
    acceptance: str = Acceptance.PENDING,
    status: str = "RESERVED",
    is_replacement: bool = False,
    eta: str | None = None,
    notified_at: str | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    ts = created_at or now_iso()
    item = {
        "crew_id": crew_id,
        "worker_id": worker_id,
        "assigned_trade": assigned_trade,
        "offered_wage": offered_wage,
        "acceptance": acceptance,
        "status": status,
        "is_replacement": is_replacement,
        "eta": eta,
        "notified_at": notified_at or ts,
        "created_at": ts,
        "updated_at": ts,
    }
    return to_decimal(item)


def build_offer(crew_id: str, request: dict[str, Any], member: dict[str, Any], *, is_emergency: bool = False) -> dict[str, Any]:
    """Worker.current_offer 페이로드 (계약 v2 union)."""
    offer = {
        "crew_id": crew_id,
        "request_id": request.get("request_id"),
        "assigned_trade": member.get("assigned_trade"),
        "offered_wage": member.get("offered_wage"),
        "site_name": request.get("site_name"),
        "work_date": request.get("work_date"),
        "start_time": request.get("start_time"),
        "location_text": request.get("location_text"),
        "notified_at": now_iso(),
    }
    if is_emergency:
        offer["is_emergency"] = True
    return offer


def crew_member_view(assignment: dict[str, Any], worker: dict[str, Any] | None) -> dict[str, Any]:
    """CrewMember 계약: worker_id, name, assigned_trade, career_years, offered_wage,
    acceptance, notified_at?, is_replacement?, eta?, worker_state?."""
    view: dict[str, Any] = {
        "worker_id": assignment.get("worker_id"),
        "name": (worker or {}).get("name"),
        "assigned_trade": assignment.get("assigned_trade"),
        "career_years": clean((worker or {}).get("career_years")),
        "offered_wage": clean(assignment.get("offered_wage")),
        "acceptance": assignment.get("acceptance"),
    }
    if assignment.get("notified_at"):
        view["notified_at"] = assignment["notified_at"]
    if assignment.get("is_replacement"):
        view["is_replacement"] = True
    if assignment.get("eta"):
        view["eta"] = assignment["eta"]
    if worker is not None:
        view["worker_state"] = worker.get("state")
    return view


# ---------------------------------------------------------------------------
# GapEvent
#   계약: event_id, crew_id, request_id, office_id, type, affected_worker_id,
#         affected_worker_name?, status, recommendations?, created_at, updated_at
# ---------------------------------------------------------------------------
def build_gap_event(
    *,
    office_id: str,
    crew_id: str,
    request_id: str,
    gap_type: str,
    affected_worker_id: str,
    affected_worker_name: str = "",
    status: str = GapStatus.DETECTED,
    event_id: str | None = None,
) -> dict[str, Any]:
    eid = event_id or new_id()
    ts = now_iso()
    item = {
        "event_id": eid,
        "office_id": office_id,
        "crew_id": crew_id,
        "request_id": request_id,
        "gsi1sk": gap_gsi1sk(status, eid),
        "type": gap_type,
        "affected_worker_id": affected_worker_id,
        "affected_worker_name": affected_worker_name,
        "leaver_worker_id": affected_worker_id,
        "status": status,
        "recommendations": [],
        "created_at": ts,
        "updated_at": ts,
    }
    return to_decimal(item)


def gap_view(gap: dict[str, Any]) -> dict[str, Any]:
    return {k: clean(v) for k, v in gap.items() if k not in {"gsi1sk", "leaver_worker_id"}}


# ---------------------------------------------------------------------------
# Notification
#   계약: id, user_id, type, title, message, read, created_at
# ---------------------------------------------------------------------------
def build_notification(
    *,
    user_id: str,
    type: str,
    title: str,
    message: str,
    notification_id: str | None = None,
) -> dict[str, Any]:
    nid = notification_id or new_id("NOTI_")
    ts = now_iso()
    return {
        "user_id": user_id,
        "sk": notification_sk(ts, nid),
        "id": nid,
        "type": type,
        "title": title,
        "message": message,
        "read": False,
        "created_at": ts,
    }


def notification_view(noti: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": noti.get("id"),
        "user_id": noti.get("user_id"),
        "type": noti.get("type"),
        "title": noti.get("title"),
        "message": noti.get("message"),
        "read": bool(noti.get("read", False)),
        "created_at": noti.get("created_at"),
    }


def work_history_entry(assignment: dict[str, Any], request: dict[str, Any] | None) -> dict[str, Any]:
    """완료 Assignment → WorkHistoryEntry 계약."""
    req = request or {}
    return {
        "crew_id": assignment.get("crew_id"),
        "request_id": req.get("request_id"),
        "site_name": req.get("site_name"),
        "work_date": req.get("work_date"),
        "assigned_trade": assignment.get("assigned_trade"),
        "offered_wage": clean(assignment.get("offered_wage")),
        "completed_at": assignment.get("updated_at") or assignment.get("created_at"),
    }
