"""엔터티 검증/직렬화 헬퍼 (공유 계약 1.4 / 1.5).

- 아이템 빌더: Worker / WorkRequest / Crew / GapEvent / Notification / Collaboration
- 검증: trade/skill_level/필수 필드
- 뷰 필터: COMPANY 응답에서 내부 데이터(no_show_count 등) 제거
- 유틸: UUID, ISO8601 timestamp, float -> Decimal 변환
"""

from __future__ import annotations

import decimal
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from .db import (
    company_gsi2pk,
    crew_gsi1sk,
    crew_pk,
    gap_gsi1sk,
    gap_pk,
    office_gsi1pk,
    request_gsi1sk,
    request_pk,
    user_pk,
    worker_gsi1sk,
    worker_pk,
)
from .responses import ApiError, ErrorCode
from .state import CrewStatus, GapStatus, ItemType, RequestStatus, Trade, WorkerState


# ---------------------------------------------------------------------------
# 유틸
# ---------------------------------------------------------------------------
def new_id() -> str:
    return str(uuid.uuid4())


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


def parse_body(event: dict[str, Any]) -> dict[str, Any]:
    """API Gateway 이벤트 본문을 dict로 파싱한다."""
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


def validate_skill_level(level: Any) -> int:
    try:
        level_int = int(level)
    except (ValueError, TypeError):
        raise ApiError(ErrorCode.VALIDATION_ERROR, "skill_level은 1~5 정수여야 합니다.")
    if not 1 <= level_int <= 5:
        raise ApiError(ErrorCode.VALIDATION_ERROR, "skill_level은 1~5 정수여야 합니다.")
    return level_int


# ---------------------------------------------------------------------------
# Worker 아이템 빌더
# ---------------------------------------------------------------------------
# 주민등록번호 필드는 어떤 형태로도 추가하지 않는다 (공유 계약 / 금지 사항 7).
def build_worker(
    *,
    user_id: str,
    name: str,
    phone: str,
    office_id: str,
    trade: str,
    skill_level: int,
    career_years: int,
    age: int,
    region: str,
    desired_daily_wage: int,
    certifications: list[str] | None = None,
    worker_id: str | None = None,
    state: str = WorkerState.INACTIVE,
    completed_count: int = 0,
    no_show_count: int = 0,
) -> dict[str, Any]:
    validate_trade(trade)
    skill_level = validate_skill_level(skill_level)
    wid = worker_id or new_id()
    ts = now_iso()
    item = {
        "PK": worker_pk(wid),
        "SK": "PROFILE",
        "GSI1PK": office_gsi1pk(office_id),
        "GSI1SK": worker_gsi1sk(state, wid),
        "item_type": ItemType.WORKER,
        "worker_id": wid,
        "user_id": user_id,
        "name": name,
        "phone": phone,
        "office_id": office_id,
        "state": state,
        "trade": trade,
        "skill_level": skill_level,
        "career_years": career_years,
        "age": age,
        "region": region,
        "desired_daily_wage": desired_daily_wage,
        "certifications": certifications or [],
        "completed_count": completed_count,
        "no_show_count": no_show_count,
        "current_crew_id": None,
        "state_changed_at": ts,
        "created_at": ts,
        "updated_at": ts,
    }
    return to_decimal(item)


# COMPANY 응답 및 Agent 추천 사유에 노출 금지인 내부 필드
_WORKER_INTERNAL_FIELDS = frozenset(
    {"no_show_count", "phone", "user_id", "age", "PK", "SK", "GSI1PK", "GSI1SK"}
)


def worker_public_view(worker: dict[str, Any]) -> dict[str, Any]:
    """COMPANY 응답용: 내부/부정 데이터 제거 (name, trade, skill_level 위주)."""
    return {
        "worker_id": worker.get("worker_id"),
        "name": worker.get("name"),
        "trade": worker.get("trade"),
        "skill_level": worker.get("skill_level"),
    }


def worker_office_view(worker: dict[str, Any]) -> dict[str, Any]:
    """OFFICE 응답용: 내부 운영 데이터 포함, DynamoDB 키만 제거."""
    return {k: v for k, v in worker.items() if k not in {"PK", "SK", "GSI1PK", "GSI1SK"}}


# ---------------------------------------------------------------------------
# WorkRequest 아이템 빌더
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
        "PK": request_pk(rid),
        "SK": "META",
        "GSI1PK": office_gsi1pk(office_id),
        "GSI1SK": request_gsi1sk(status, rid),
        "GSI2PK": company_gsi2pk(company_id),
        "GSI2SK": f"REQ#{rid}",
        "item_type": ItemType.WORK_REQUEST,
        "request_id": rid,
        "company_id": company_id,
        "office_id": office_id,
        "site_name": site_name,
        "work_date": work_date,
        "start_time": start_time,
        "location_text": location_text,
        "required_workers": required_workers,  # [{trade, count, min_skill_level?}]
        "budget": budget,
        "priority": priority or {},
        "notes": notes,
        "status": status,
        "created_at": ts,
        "updated_at": ts,
    }
    return to_decimal(item)


def request_view(req: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in req.items() if k not in {"PK", "SK", "GSI1PK", "GSI1SK", "GSI2PK", "GSI2SK"}}


# ---------------------------------------------------------------------------
# Crew 아이템 빌더
# ---------------------------------------------------------------------------
def build_crew(
    *,
    office_id: str,
    request_id: str,
    member_ids: list[str],
    status: str = CrewStatus.DRAFT,
    crew_id: str | None = None,
    source: str = "MANUAL",       # MANUAL / AGENT
    rationale: str = "",
    estimated_cost: int | None = None,
) -> dict[str, Any]:
    cid = crew_id or new_id()
    ts = now_iso()
    item = {
        "PK": crew_pk(cid),
        "SK": "META",
        "GSI1PK": office_gsi1pk(office_id),
        "GSI1SK": crew_gsi1sk(status, cid),
        "item_type": ItemType.CREW,
        "crew_id": cid,
        "office_id": office_id,
        "request_id": request_id,
        "member_ids": member_ids,
        "status": status,
        "source": source,
        "rationale": rationale,
        "estimated_cost": estimated_cost,
        "created_at": ts,
        "updated_at": ts,
    }
    return to_decimal(item)


def crew_view(crew: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in crew.items() if k not in {"PK", "SK", "GSI1PK", "GSI1SK"}}


# ---------------------------------------------------------------------------
# GapEvent 아이템 빌더
# ---------------------------------------------------------------------------
def build_gap_event(
    *,
    office_id: str,
    crew_id: str,
    request_id: str,
    gap_type: str,
    missing_worker_ids: list[str],
    status: str = GapStatus.DETECTED,
    event_id: str | None = None,
) -> dict[str, Any]:
    eid = event_id or new_id()
    ts = now_iso()
    item = {
        "PK": gap_pk(eid),
        "SK": "META",
        "GSI1PK": office_gsi1pk(office_id),
        "GSI1SK": gap_gsi1sk(status, eid),
        "item_type": ItemType.GAP_EVENT,
        "event_id": eid,
        "office_id": office_id,
        "crew_id": crew_id,
        "request_id": request_id,
        "gap_type": gap_type,
        "missing_worker_ids": missing_worker_ids,
        "fixed_member_ids": [],
        "status": status,
        "created_at": ts,
        "updated_at": ts,
    }
    return to_decimal(item)


def gap_view(gap: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in gap.items() if k not in {"PK", "SK", "GSI1PK", "GSI1SK"}}


# ---------------------------------------------------------------------------
# Notification 아이템 빌더
# ---------------------------------------------------------------------------
def build_notification(
    *,
    user_id: str,
    kind: str,               # ASSIGNED / EMERGENCY_ASSIGNED / NEW_REQUEST / CREW_CHANGED
    title: str,
    message: str,
    payload: dict[str, Any] | None = None,
    notification_id: str | None = None,
) -> dict[str, Any]:
    nid = notification_id or new_id()
    ts = now_iso()
    item = {
        "PK": user_pk(user_id),
        "SK": f"NOTI#{ts}#{nid}",
        "item_type": ItemType.NOTIFICATION,
        "notification_id": nid,
        "user_id": user_id,
        "kind": kind,
        "title": title,
        "message": message,
        "payload": payload or {},
        "read": False,
        "created_at": ts,
    }
    return to_decimal(item)


def notification_view(noti: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in noti.items() if k not in {"PK", "SK"}}
