"""DynamoDB 엔터티별 테이블 접근 헬퍼 (계약 v2).

단일 테이블을 폐기하고 8개 엔터티 테이블로 분리한다. 테이블 이름은 SAM 파라미터로
Lambda 환경 변수에 주입한다 (기본값은 로컬/테스트 편의를 위한 것).

| 테이블        | env var             | PK          | SK                      | GSI |
|--------------|---------------------|-------------|-------------------------|-----|
| Workers      | WORKERS_TABLE       | worker_id   | —                       | GSI1: office_id + state#worker_id |
| Offices      | OFFICES_TABLE       | office_id   | —                       | — |
| Companies    | COMPANIES_TABLE     | company_id  | —                       | — |
| Requests     | REQUESTS_TABLE      | request_id  | —                       | GSI1: office_id + status#request_id / GSI2: company_id + request_id |
| Crews        | CREWS_TABLE         | crew_id     | —                       | GSI1: office_id + status#crew_id |
| Assignments  | ASSIGNMENTS_TABLE   | crew_id     | worker_id               | GSI1: worker_id + created_at |
| GapEvents    | GAP_EVENTS_TABLE    | event_id    | —                       | GSI1: office_id + status#event_id |
| Notifications| NOTIFICATIONS_TABLE | user_id     | created_at#notification_id | — |

다중 테이블 상태 전환은 반드시 하나의 TransactWriteItems 로 원자 처리한다
(transact_write / build_* 엔트리 빌더 참고).
"""

from __future__ import annotations

import os
from typing import Any, Iterable

import boto3
from boto3.dynamodb.conditions import Key

GSI1 = "GSI1"
GSI2 = "GSI2"


# ---------------------------------------------------------------------------
# 테이블 이름 (환경 변수 주입)
# ---------------------------------------------------------------------------
def _tn(env_key: str, default: str) -> str:
    return os.environ.get(env_key, default)


WORKERS_TABLE = _tn("WORKERS_TABLE", "CrewMate-Workers")
OFFICES_TABLE = _tn("OFFICES_TABLE", "CrewMate-Offices")
COMPANIES_TABLE = _tn("COMPANIES_TABLE", "CrewMate-Companies")
REQUESTS_TABLE = _tn("REQUESTS_TABLE", "CrewMate-Requests")
CREWS_TABLE = _tn("CREWS_TABLE", "CrewMate-Crews")
ASSIGNMENTS_TABLE = _tn("ASSIGNMENTS_TABLE", "CrewMate-Assignments")
GAP_EVENTS_TABLE = _tn("GAP_EVENTS_TABLE", "CrewMate-GapEvents")
NOTIFICATIONS_TABLE = _tn("NOTIFICATIONS_TABLE", "CrewMate-Notifications")


def _table_names() -> dict[str, str]:
    """환경 변수를 다시 읽어 최신 테이블 이름 맵을 반환한다 (테스트에서 monkeypatch 대응)."""
    return {
        "workers": os.environ.get("WORKERS_TABLE", WORKERS_TABLE),
        "offices": os.environ.get("OFFICES_TABLE", OFFICES_TABLE),
        "companies": os.environ.get("COMPANIES_TABLE", COMPANIES_TABLE),
        "requests": os.environ.get("REQUESTS_TABLE", REQUESTS_TABLE),
        "crews": os.environ.get("CREWS_TABLE", CREWS_TABLE),
        "assignments": os.environ.get("ASSIGNMENTS_TABLE", ASSIGNMENTS_TABLE),
        "gap_events": os.environ.get("GAP_EVENTS_TABLE", GAP_EVENTS_TABLE),
        "notifications": os.environ.get("NOTIFICATIONS_TABLE", NOTIFICATIONS_TABLE),
    }


_resource = None
_client = None
_tables: dict[str, Any] = {}


def _get_resource():
    global _resource
    if _resource is None:
        _resource = boto3.resource("dynamodb")
    return _resource


def get_client():
    """TransactWriteItems 등 저수준 호출용 클라이언트 (지연 초기화)."""
    global _client
    if _client is None:
        _client = boto3.client("dynamodb")
    return _client


def _table(key: str):
    name = _table_names()[key]
    cached = _tables.get(name)
    if cached is None:
        cached = _get_resource().Table(name)
        _tables[name] = cached
    return cached


def workers_table():
    return _table("workers")


def offices_table():
    return _table("offices")


def companies_table():
    return _table("companies")


def requests_table():
    return _table("requests")


def crews_table():
    return _table("crews")


def assignments_table():
    return _table("assignments")


def gap_events_table():
    return _table("gap_events")


def notifications_table():
    return _table("notifications")


def table_name(key: str) -> str:
    """TransactWriteItems 용 테이블 이름 (key: workers/requests/crews/... )."""
    return _table_names()[key]


# ---------------------------------------------------------------------------
# GSI Sort Key 빌더 (상태 접두사)
# ---------------------------------------------------------------------------
def worker_gsi1sk(state: str, worker_id: str) -> str:
    return f"{state}#{worker_id}"


def request_gsi1sk(status: str, request_id: str) -> str:
    return f"{status}#{request_id}"


def crew_gsi1sk(status: str, crew_id: str) -> str:
    return f"{status}#{crew_id}"


def gap_gsi1sk(status: str, event_id: str) -> str:
    return f"{status}#{event_id}"


def notification_sk(created_at: str, notification_id: str) -> str:
    return f"{created_at}#{notification_id}"


# ---------------------------------------------------------------------------
# Workers
# ---------------------------------------------------------------------------
def get_worker(worker_id: str) -> dict[str, Any] | None:
    resp = workers_table().get_item(Key={"worker_id": worker_id})
    return resp.get("Item")


def get_worker_by_user(user_id: str) -> dict[str, Any] | None:
    """user_id(cognito sub)로 근로자 조회. 자가 등록 시 worker_id == user_id 규약."""
    worker = get_worker(user_id)
    if worker:
        return worker
    # 폴백: worker_id != user_id 인 경우 스캔 (시드 데이터 호환)
    resp = workers_table().scan(
        FilterExpression=Key("user_id").eq(user_id),
        Limit=1,
    )
    items = resp.get("Items", [])
    return items[0] if items else None


def put_worker(item: dict[str, Any], condition: str | None = None) -> None:
    kwargs: dict[str, Any] = {"Item": item}
    if condition:
        kwargs["ConditionExpression"] = condition
    workers_table().put_item(**kwargs)


def update_worker(worker_id: str, **kwargs: Any) -> dict[str, Any]:
    return workers_table().update_item(Key={"worker_id": worker_id}, **kwargs)


def query_office_workers_by_state(office_id: str, state: str) -> list[dict[str, Any]]:
    resp = workers_table().query(
        IndexName=GSI1,
        KeyConditionExpression=Key("office_id").eq(office_id)
        & Key("gsi1sk").begins_with(f"{state}#"),
    )
    return resp.get("Items", [])


def query_office_all_workers(office_id: str) -> list[dict[str, Any]]:
    resp = workers_table().query(
        IndexName=GSI1,
        KeyConditionExpression=Key("office_id").eq(office_id),
    )
    return resp.get("Items", [])


def scan_workers() -> list[dict[str, Any]]:
    """전체 근로자 스캔 (경력 연차별 통계 등 집계용; 데모 규모 한정)."""
    return workers_table().scan().get("Items", [])


# ---------------------------------------------------------------------------
# Offices / Companies
# ---------------------------------------------------------------------------
def get_office(office_id: str) -> dict[str, Any] | None:
    return offices_table().get_item(Key={"office_id": office_id}).get("Item")


def put_office(item: dict[str, Any]) -> None:
    offices_table().put_item(Item=item)


def list_offices() -> list[dict[str, Any]]:
    return offices_table().scan().get("Items", [])


def count_office_workers(office_id: str) -> int:
    resp = workers_table().query(
        IndexName=GSI1,
        KeyConditionExpression=Key("office_id").eq(office_id),
        Select="COUNT",
    )
    return int(resp.get("Count", 0))


def get_company(company_id: str) -> dict[str, Any] | None:
    return companies_table().get_item(Key={"company_id": company_id}).get("Item")


def put_company(item: dict[str, Any]) -> None:
    companies_table().put_item(Item=item)


# ---------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------
def get_request(request_id: str) -> dict[str, Any] | None:
    return requests_table().get_item(Key={"request_id": request_id}).get("Item")


def put_request(item: dict[str, Any]) -> None:
    requests_table().put_item(Item=item)


def update_request(request_id: str, **kwargs: Any) -> dict[str, Any]:
    return requests_table().update_item(Key={"request_id": request_id}, **kwargs)


def query_office_requests(office_id: str, status: str | None = None) -> list[dict[str, Any]]:
    if status:
        cond = Key("office_id").eq(office_id) & Key("gsi1sk").begins_with(f"{status}#")
    else:
        cond = Key("office_id").eq(office_id)
    resp = requests_table().query(IndexName=GSI1, KeyConditionExpression=cond)
    return resp.get("Items", [])


def query_company_requests(company_id: str) -> list[dict[str, Any]]:
    resp = requests_table().query(
        IndexName=GSI2,
        KeyConditionExpression=Key("company_id").eq(company_id),
    )
    return resp.get("Items", [])


# ---------------------------------------------------------------------------
# Crews
# ---------------------------------------------------------------------------
def get_crew(crew_id: str) -> dict[str, Any] | None:
    return crews_table().get_item(Key={"crew_id": crew_id}).get("Item")


def put_crew(item: dict[str, Any]) -> None:
    crews_table().put_item(Item=item)


def update_crew(crew_id: str, **kwargs: Any) -> dict[str, Any]:
    return crews_table().update_item(Key={"crew_id": crew_id}, **kwargs)


def query_office_crews(office_id: str, status: str | None = None) -> list[dict[str, Any]]:
    if status:
        cond = Key("office_id").eq(office_id) & Key("gsi1sk").begins_with(f"{status}#")
    else:
        cond = Key("office_id").eq(office_id)
    resp = crews_table().query(IndexName=GSI1, KeyConditionExpression=cond)
    return resp.get("Items", [])


def query_crews_by_request(request_id: str) -> list[dict[str, Any]]:
    """요청에 연결된 작업조 (scan 폴백; 요청당 작업조 수가 적음)."""
    resp = crews_table().scan(FilterExpression=Key("request_id").eq(request_id))
    return resp.get("Items", [])


# ---------------------------------------------------------------------------
# Assignments (CrewMember 원본)
# ---------------------------------------------------------------------------
def get_assignment(crew_id: str, worker_id: str) -> dict[str, Any] | None:
    return (
        assignments_table()
        .get_item(Key={"crew_id": crew_id, "worker_id": worker_id})
        .get("Item")
    )


def put_assignment(item: dict[str, Any]) -> None:
    assignments_table().put_item(Item=item)


def query_crew_assignments(crew_id: str) -> list[dict[str, Any]]:
    resp = assignments_table().query(
        KeyConditionExpression=Key("crew_id").eq(crew_id)
    )
    return resp.get("Items", [])


def query_worker_assignments(worker_id: str, limit: int = 100) -> list[dict[str, Any]]:
    """근로자별 배치 이력 (최신순). GSI1: worker_id + created_at."""
    resp = assignments_table().query(
        IndexName=GSI1,
        KeyConditionExpression=Key("worker_id").eq(worker_id),
        ScanIndexForward=False,
        Limit=limit,
    )
    return resp.get("Items", [])


# ---------------------------------------------------------------------------
# GapEvents
# ---------------------------------------------------------------------------
def get_gap_event(event_id: str) -> dict[str, Any] | None:
    return gap_events_table().get_item(Key={"event_id": event_id}).get("Item")


def put_gap_event(item: dict[str, Any]) -> None:
    gap_events_table().put_item(Item=item)


def update_gap_event(event_id: str, **kwargs: Any) -> dict[str, Any]:
    return gap_events_table().update_item(Key={"event_id": event_id}, **kwargs)


def query_gap_events_by_crew(crew_id: str) -> list[dict[str, Any]]:
    """작업조의 결원 이벤트 (scan 폴백; 작업조당 이벤트 수가 적음)."""
    resp = gap_events_table().scan(FilterExpression=Key("crew_id").eq(crew_id))
    return resp.get("Items", [])


def query_office_gap_events(office_id: str, status: str | None = None) -> list[dict[str, Any]]:
    if status:
        cond = Key("office_id").eq(office_id) & Key("gsi1sk").begins_with(f"{status}#")
    else:
        cond = Key("office_id").eq(office_id)
    resp = gap_events_table().query(IndexName=GSI1, KeyConditionExpression=cond)
    return resp.get("Items", [])


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------
def put_notification(item: dict[str, Any]) -> None:
    notifications_table().put_item(Item=item)


def query_notifications(user_id: str, limit: int = 50) -> list[dict[str, Any]]:
    resp = notifications_table().query(
        KeyConditionExpression=Key("user_id").eq(user_id),
        ScanIndexForward=False,
        Limit=limit,
    )
    return resp.get("Items", [])


def update_notification_read(user_id: str, sk: str) -> None:
    notifications_table().update_item(
        Key={"user_id": user_id, "sk": sk},
        UpdateExpression="SET #r = :t",
        ExpressionAttributeNames={"#r": "read"},
        ExpressionAttributeValues={":t": True},
    )


# ---------------------------------------------------------------------------
# 트랜잭션 / 배치
# ---------------------------------------------------------------------------
def transact_write(items: list[dict[str, Any]]) -> None:
    """여러 테이블에 걸친 원자 쓰기. 저수준 클라이언트 형식의 TransactItems 사용."""
    get_client().transact_write_items(TransactItems=items)


def batch_put(table_key: str, items: Iterable[dict[str, Any]]) -> None:
    """시드 스크립트용 배치 쓰기 (table_key: workers/offices/...)."""
    table = _table(table_key)
    with table.batch_writer() as batch:
        for item in items:
            batch.put_item(Item=item)
