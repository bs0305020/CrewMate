"""TransactWriteItems 엔트리 빌더 (계약 v2).

다중 테이블 상태 전환을 하나의 원자 트랜잭션으로 처리하기 위한 저수준 엔트리 빌더.
worker_api / company_request / office_core / assignment 에서 공용으로 사용한다.

모든 엔트리는 boto3 저수준 클라이언트(TransactWriteItems) 형식이다.
"""

from __future__ import annotations

from typing import Any

from boto3.dynamodb.types import TypeSerializer

from . import db
from .schemas import to_decimal
from .state import WorkerState

_serializer = TypeSerializer()

_SENTINEL = object()


def ser(value: Any) -> dict[str, Any]:
    return _serializer.serialize(to_decimal(value))


# ---------------------------------------------------------------------------
# Worker 전이
# ---------------------------------------------------------------------------
def worker_entry(
    worker_id: str,
    *,
    now: str,
    to_state: str | None = None,
    from_states: list[str] | None = None,
    current_offer: Any = _SENTINEL,
    current_crew_id: Any = _SENTINEL,
    inc_dispatched: bool = False,
    inc_completed: bool = False,
    inc_attended: bool = False,
    dec_dispatched: bool = False,
    add_rating: int | None = None,
    require_no_offer: bool = False,
) -> dict[str, Any]:
    """Workers 테이블 조건부 Update 엔트리.

    - to_state 지정 시 state/gsi1sk/state_changed_at 갱신.
    - from_states 지정 시 `state ∈ from_states` 조건.
    - require_no_offer=True 시 current_offer 미보유 조건 (중복 배치 방지 관문).
    - current_offer / current_crew_id 는 None 포함 명시적 설정 가능(_SENTINEL이면 미변경).
    - inc_dispatched / inc_completed 로 성실도 카운트 증가.
    """
    set_parts = ["updated_at = :t"]
    add_parts: list[str] = []
    names: dict[str, str] = {}
    values: dict[str, Any] = {":t": ser(now)}

    if to_state is not None:
        set_parts += ["#s = :to", "gsi1sk = :gsi", "state_changed_at = :t"]
        names["#s"] = "state"
        values[":to"] = ser(to_state)
        values[":gsi"] = ser(db.worker_gsi1sk(to_state, worker_id))

    if current_offer is not _SENTINEL:
        set_parts.append("current_offer = :offer")
        values[":offer"] = ser(current_offer)

    if current_crew_id is not _SENTINEL:
        set_parts.append("current_crew_id = :cc")
        values[":cc"] = ser(current_crew_id)

    if inc_dispatched:
        add_parts.append("dispatched_count :one")
        values[":one"] = ser(1)
    if inc_completed:
        add_parts.append("completed_count :one")
        values[":one"] = ser(1)
    if inc_attended:
        add_parts.append("attended_count :one")
        values[":one"] = ser(1)
    if dec_dispatched:
        add_parts.append("dispatched_count :neg_one")
        values[":neg_one"] = ser(-1)
    if add_rating is not None:
        add_parts.append("rating_sum :rating")
        add_parts.append("rating_count :one")
        values[":rating"] = ser(int(add_rating))
        values[":one"] = ser(1)

    update_expr = "SET " + ", ".join(set_parts)
    if add_parts:
        update_expr += " ADD " + ", ".join(add_parts)

    conditions: list[str] = []
    if from_states:
        cond_parts = []
        for i, st in enumerate(from_states):
            cond_parts.append(f"#s = :f{i}")
            values[f":f{i}"] = ser(st)
        names["#s"] = "state"
        conditions.append("(" + " OR ".join(cond_parts) + ")")
    if require_no_offer:
        conditions.append("(attribute_not_exists(current_offer) OR current_offer = :null)")
        values[":null"] = ser(None)

    entry: dict[str, Any] = {
        "TableName": db.table_name("workers"),
        "Key": {"worker_id": ser(worker_id)},
        "UpdateExpression": update_expr,
        "ExpressionAttributeValues": values,
    }
    if names:
        entry["ExpressionAttributeNames"] = names
    if conditions:
        entry["ConditionExpression"] = " AND ".join(conditions)
    return {"Update": entry}


def worker_inactive_entry(worker_id: str, now: str, *, inc_completed: bool = False) -> dict[str, Any]:
    """이탈/노쇼/퇴근 등: 무조건 INACTIVE + offer/crew 정리 (멱등)."""
    return worker_entry(
        worker_id,
        now=now,
        to_state=WorkerState.INACTIVE,
        current_offer=None,
        current_crew_id=None,
        inc_completed=inc_completed,
    )


# ---------------------------------------------------------------------------
# 상태 엔티티(Request/Crew/GapEvent) 전이
# ---------------------------------------------------------------------------
def _status_entry(
    table_key: str,
    key: dict[str, Any],
    *,
    gsi1sk: str | None,
    to_status: str,
    from_statuses: list[str] | None,
    now: str,
    extra_set: dict[str, Any] | None = None,
) -> dict[str, Any]:
    set_parts = ["#st = :to", "updated_at = :t"]
    names = {"#st": "status"}
    values: dict[str, Any] = {":to": ser(to_status), ":t": ser(now)}
    if gsi1sk is not None:
        set_parts.append("gsi1sk = :gsi")
        values[":gsi"] = ser(gsi1sk)
    for i, (k, v) in enumerate(sorted((extra_set or {}).items())):
        set_parts.append(f"#e{i} = :e{i}")
        names[f"#e{i}"] = k
        values[f":e{i}"] = ser(v)

    entry: dict[str, Any] = {
        "TableName": db.table_name(table_key),
        "Key": {kk: ser(vv) for kk, vv in key.items()},
        "UpdateExpression": "SET " + ", ".join(set_parts),
        "ExpressionAttributeNames": names,
        "ExpressionAttributeValues": values,
    }
    if from_statuses:
        cond_parts = []
        for i, st in enumerate(from_statuses):
            cond_parts.append(f"#st = :c{i}")
            values[f":c{i}"] = ser(st)
        entry["ConditionExpression"] = "(" + " OR ".join(cond_parts) + ")"
    return {"Update": entry}


def request_status_entry(request_id, *, to_status, from_statuses=None, now, extra_set=None):
    return _status_entry(
        "requests",
        {"request_id": request_id},
        gsi1sk=db.request_gsi1sk(to_status, request_id),
        to_status=to_status,
        from_statuses=from_statuses,
        now=now,
        extra_set=extra_set,
    )


def crew_status_entry(crew_id, *, to_status, from_statuses=None, now, extra_set=None):
    return _status_entry(
        "crews",
        {"crew_id": crew_id},
        gsi1sk=db.crew_gsi1sk(to_status, crew_id),
        to_status=to_status,
        from_statuses=from_statuses,
        now=now,
        extra_set=extra_set,
    )


def gap_status_entry(event_id, *, to_status, from_statuses=None, now, extra_set=None):
    return _status_entry(
        "gap_events",
        {"event_id": event_id},
        gsi1sk=db.gap_gsi1sk(to_status, event_id),
        to_status=to_status,
        from_statuses=from_statuses,
        now=now,
        extra_set=extra_set,
    )


# ---------------------------------------------------------------------------
# Assignment
# ---------------------------------------------------------------------------
def put_entry(table_key: str, item: dict[str, Any]) -> dict[str, Any]:
    return {
        "Put": {
            "TableName": db.table_name(table_key),
            "Item": {k: ser(v) for k, v in item.items()},
        }
    }


def assignment_put_entry(item: dict[str, Any]) -> dict[str, Any]:
    return put_entry("assignments", item)


def assignment_update_entry(
    crew_id: str,
    worker_id: str,
    *,
    now: str,
    acceptance: str | None = None,
    status: str | None = None,
    eta: Any = _SENTINEL,
    require_acceptance: str | None = None,
) -> dict[str, Any]:
    set_parts = ["updated_at = :t"]
    values: dict[str, Any] = {":t": ser(now)}
    names: dict[str, str] = {}
    if acceptance is not None:
        set_parts.append("acceptance = :acc")
        values[":acc"] = ser(acceptance)
    if status is not None:
        set_parts.append("#st = :stt")
        names["#st"] = "status"
        values[":stt"] = ser(status)
    if eta is not _SENTINEL:
        set_parts.append("eta = :eta")
        values[":eta"] = ser(eta)

    entry: dict[str, Any] = {
        "TableName": db.table_name("assignments"),
        "Key": {"crew_id": ser(crew_id), "worker_id": ser(worker_id)},
        "UpdateExpression": "SET " + ", ".join(set_parts),
        "ExpressionAttributeValues": values,
    }
    if names:
        entry["ExpressionAttributeNames"] = names
    if require_acceptance is not None:
        entry["ConditionExpression"] = "acceptance = :req"
        values[":req"] = ser(require_acceptance)
    return {"Update": entry}


def run(entries: list[dict[str, Any]]) -> None:
    db.transact_write(entries)
