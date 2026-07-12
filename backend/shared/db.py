"""DynamoDB 단일 테이블 접근 헬퍼 (공유 계약 1.4).

테이블: CrewMate (환경변수 TABLE_NAME)
GSI1: OFFICE 기준 조회 (READY 후보 / 요청 목록 / 작업조 목록 / 결원 목록)
GSI2: COMPANY 기준 요청 목록 조회

키 스키마
| 엔터티        | PK                   | SK                       | GSI1PK / GSI1SK                              | GSI2PK / GSI2SK              |
| Worker        | WORKER#{worker_id}   | PROFILE                  | OFFICE#{office_id} / STATE#{state}#W#{id}    | —                            |
| WorkRequest   | REQ#{request_id}     | META                     | OFFICE#{office_id} / REQ#{status}#{id}       | COMPANY#{company_id} / REQ#{id} |
| Crew          | CREW#{crew_id}       | META                     | OFFICE#{office_id} / CREW#{status}#{id}      | —                            |
| GapEvent      | GAP#{event_id}       | META                     | OFFICE#{office_id} / GAP#{status}#{id}       | —                            |
| Notification  | USER#{user_id}       | NOTI#{created_at}#{id}   | —                                            | —                            |
| Collaboration | WORKER#{worker_id}   | COLLAB#{other_id}#{date} | —                                            | —                            |
"""

from __future__ import annotations

import os
from typing import Any, Iterable

import boto3
from boto3.dynamodb.conditions import Key

TABLE_NAME = os.environ.get("TABLE_NAME", "CrewMate")

GSI1 = "GSI1"
GSI2 = "GSI2"

_resource = None
_table = None


def get_table():
    """지연 초기화된 DynamoDB Table 리소스를 반환한다."""
    global _resource, _table
    if _table is None:
        _resource = boto3.resource("dynamodb")
        _table = _resource.Table(TABLE_NAME)
    return _table


def get_client():
    """TransactWriteItems 등 저수준 호출용 클라이언트."""
    return boto3.client("dynamodb")


# ---------------------------------------------------------------------------
# 키 빌더
# ---------------------------------------------------------------------------
def worker_pk(worker_id: str) -> str:
    return f"WORKER#{worker_id}"


def request_pk(request_id: str) -> str:
    return f"REQ#{request_id}"


def crew_pk(crew_id: str) -> str:
    return f"CREW#{crew_id}"


def gap_pk(event_id: str) -> str:
    return f"GAP#{event_id}"


def user_pk(user_id: str) -> str:
    return f"USER#{user_id}"


def office_gsi1pk(office_id: str) -> str:
    return f"OFFICE#{office_id}"


def company_gsi2pk(company_id: str) -> str:
    return f"COMPANY#{company_id}"


def worker_gsi1sk(state: str, worker_id: str) -> str:
    return f"STATE#{state}#W#{worker_id}"


def request_gsi1sk(status: str, request_id: str) -> str:
    return f"REQ#{status}#{request_id}"


def crew_gsi1sk(status: str, crew_id: str) -> str:
    return f"CREW#{status}#{crew_id}"


def gap_gsi1sk(status: str, event_id: str) -> str:
    return f"GAP#{status}#{event_id}"


# ---------------------------------------------------------------------------
# 기본 CRUD
# ---------------------------------------------------------------------------
def get_item(pk: str, sk: str) -> dict[str, Any] | None:
    resp = get_table().get_item(Key={"PK": pk, "SK": sk})
    return resp.get("Item")


def put_item(item: dict[str, Any], condition: str | None = None,
             expr_names: dict[str, str] | None = None,
             expr_values: dict[str, Any] | None = None) -> None:
    kwargs: dict[str, Any] = {"Item": item}
    if condition:
        kwargs["ConditionExpression"] = condition
    if expr_names:
        kwargs["ExpressionAttributeNames"] = expr_names
    if expr_values:
        kwargs["ExpressionAttributeValues"] = expr_values
    get_table().put_item(**kwargs)


def update_item(pk: str, sk: str, **kwargs: Any) -> dict[str, Any]:
    return get_table().update_item(Key={"PK": pk, "SK": sk}, **kwargs)


def delete_item(pk: str, sk: str) -> None:
    get_table().delete_item(Key={"PK": pk, "SK": sk})


# ---------------------------------------------------------------------------
# 조회 (GSI)
# ---------------------------------------------------------------------------
def query_office_workers_by_state(office_id: str, state: str) -> list[dict[str, Any]]:
    """사무소의 특정 상태 근로자 목록 (READY 후보 조회 등)."""
    resp = get_table().query(
        IndexName=GSI1,
        KeyConditionExpression=Key("GSI1PK").eq(office_gsi1pk(office_id))
        & Key("GSI1SK").begins_with(f"STATE#{state}#"),
    )
    return resp.get("Items", [])


def query_office_all_workers(office_id: str) -> list[dict[str, Any]]:
    """사무소 소속 전체 근로자 (상태 무관)."""
    resp = get_table().query(
        IndexName=GSI1,
        KeyConditionExpression=Key("GSI1PK").eq(office_gsi1pk(office_id))
        & Key("GSI1SK").begins_with("STATE#"),
    )
    return resp.get("Items", [])


def query_office_requests(office_id: str, status: str | None = None) -> list[dict[str, Any]]:
    prefix = f"REQ#{status}#" if status else "REQ#"
    resp = get_table().query(
        IndexName=GSI1,
        KeyConditionExpression=Key("GSI1PK").eq(office_gsi1pk(office_id))
        & Key("GSI1SK").begins_with(prefix),
    )
    return resp.get("Items", [])


def query_office_crews(office_id: str, status: str | None = None) -> list[dict[str, Any]]:
    prefix = f"CREW#{status}#" if status else "CREW#"
    resp = get_table().query(
        IndexName=GSI1,
        KeyConditionExpression=Key("GSI1PK").eq(office_gsi1pk(office_id))
        & Key("GSI1SK").begins_with(prefix),
    )
    return resp.get("Items", [])


def query_company_requests(company_id: str) -> list[dict[str, Any]]:
    resp = get_table().query(
        IndexName=GSI2,
        KeyConditionExpression=Key("GSI2PK").eq(company_gsi2pk(company_id))
        & Key("GSI2SK").begins_with("REQ#"),
    )
    return resp.get("Items", [])


def query_notifications(user_id: str, limit: int = 50) -> list[dict[str, Any]]:
    """자기 알림 목록 (최신순)."""
    resp = get_table().query(
        KeyConditionExpression=Key("PK").eq(user_pk(user_id))
        & Key("SK").begins_with("NOTI#"),
        ScanIndexForward=False,
        Limit=limit,
    )
    return resp.get("Items", [])


def query_worker_collaborations(worker_id: str) -> list[dict[str, Any]]:
    resp = get_table().query(
        KeyConditionExpression=Key("PK").eq(worker_pk(worker_id))
        & Key("SK").begins_with("COLLAB#"),
    )
    return resp.get("Items", [])


# ---------------------------------------------------------------------------
# 배치 쓰기 (시드 스크립트용)
# ---------------------------------------------------------------------------
def batch_put(items: Iterable[dict[str, Any]]) -> None:
    table = get_table()
    with table.batch_writer() as batch:
        for item in items:
            batch.put_item(Item=item)
