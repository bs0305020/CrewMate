"""Reset the deployed demo database and create a coherent showcase dataset.

The script resolves all DynamoDB table names and the Cognito pool from a SAM
stack, clears the demo-stage tables, writes 72 synthetic workers plus diverse
construction requests, crews and assignments, then relinks the demo accounts.

Usage:
  python scripts/seed/seed_showcase.py --stack-name crewmate --stage dev \
      --region ap-northeast-2 --confirm-reset dev
  python scripts/seed/seed_showcase.py --dry-run

Only synthetic names/phone numbers are generated. No resident registration
numbers or real applicant records are used.
"""

from __future__ import annotations

import argparse
import os
from collections import Counter
from datetime import date, timedelta
from typing import Any

import boto3

from common import reset_all, write_items  # noqa: E402
from seed_cognito import DEFAULT_PASSWORD, DEMO_ACCOUNTS, _create_user, _link_entity  # noqa: E402
from seed_workers import (  # noqa: E402
    _init_seed,
    generate_companies,
    generate_offices,
    generate_workers,
)
from shared.db import worker_gsi1sk  # noqa: E402
from shared.schemas import (  # noqa: E402
    build_assignment,
    build_crew,
    build_offer,
    build_request,
    now_iso,
)
from shared.state import (  # noqa: E402
    Acceptance,
    AssignmentStatus,
    CrewStatus,
    RequestStatus,
    WorkerState,
)


OUTPUT_ENV = {
    "WorkersTableName": "WORKERS_TABLE",
    "OfficesTableName": "OFFICES_TABLE",
    "CompaniesTableName": "COMPANIES_TABLE",
    "RequestsTableName": "REQUESTS_TABLE",
    "CrewsTableName": "CREWS_TABLE",
    "AssignmentsTableName": "ASSIGNMENTS_TABLE",
    "GapEventsTableName": "GAP_EVENTS_TABLE",
    "NotificationsTableName": "NOTIFICATIONS_TABLE",
}

SITE_SPECS: list[dict[str, Any]] = [
    {"name": "센텀 업무시설 골조 현장", "days": 1, "status": RequestStatus.REQUESTED,
     "required": [{"trade": "FORMWORK", "count": 3}, {"trade": "REBAR", "count": 2}], "budget": 950000},
    {"name": "마린시티 주상복합 보수", "days": 3, "status": RequestStatus.REQUESTED,
     "required": [{"trade": "MASONRY", "count": 2}, {"trade": "GENERAL", "count": 2}], "budget": 680000},
    {"name": "부산역 복합환승센터", "days": 5, "status": RequestStatus.COMPOSING,
     "required": [{"trade": "ANY", "count": 4}], "budget": 660000},
    {"name": "에코델타시티 공동주택 1공구", "days": 7, "status": RequestStatus.PROPOSED,
     "required": [{"trade": "FORMWORK", "count": 2}, {"trade": "GENERAL", "count": 2}], "budget": 760000},
    {"name": "명지 지식산업센터", "days": 8, "status": RequestStatus.APPROVED,
     "required": [{"trade": "REBAR", "count": 2}, {"trade": "MATERIAL_CARRY", "count": 2}], "budget": 720000},
    {"name": "해운대 생활형숙박시설", "days": 2, "status": RequestStatus.DISPATCHED,
     "required": [{"trade": "MASONRY", "count": 2}, {"trade": "GENERAL", "count": 1}], "budget": 540000},
    {"name": "사상 물류센터 증축", "days": 0, "status": RequestStatus.RUNNING,
     "required": [{"trade": "MATERIAL_CARRY", "count": 3}, {"trade": "GENERAL", "count": 2}], "budget": 820000},
    {"name": "동래 공동주택 골조", "days": -4, "status": RequestStatus.COMPLETED,
     "required": [{"trade": "FORMWORK", "count": 2}, {"trade": "REBAR", "count": 2}], "budget": 760000},
    {"name": "수영구 근린생활시설", "days": -9, "status": RequestStatus.COMPLETED,
     "required": [{"trade": "MASONRY", "count": 2}, {"trade": "GENERAL", "count": 2}], "budget": 650000},
    {"name": "연산동 오피스텔 마감", "days": -16, "status": RequestStatus.COMPLETED,
     "required": [{"trade": "ANY", "count": 3}], "budget": 480000},
    {"name": "강서구 공장 신축", "days": 10, "status": RequestStatus.PROPOSED,
     "required": [{"trade": "REBAR", "count": 3}, {"trade": "GENERAL", "count": 2}], "budget": 700000},
    {"name": "부산진구 상가 리모델링", "days": 12, "status": RequestStatus.REQUESTED,
     "required": [{"trade": "MASONRY", "count": 1}, {"trade": "MATERIAL_CARRY", "count": 2}], "budget": 500000},
    {"name": "광안리 호텔 리뉴얼", "days": 15, "status": RequestStatus.CANCELLED,
     "required": [{"trade": "GENERAL", "count": 3}], "budget": 450000},
    {"name": "기장 산업단지 창고", "days": 18, "status": RequestStatus.REJECTED,
     "required": [{"trade": "FORMWORK", "count": 4}], "budget": 500000},
]


def _stack_outputs(stack_name: str, region: str) -> dict[str, str]:
    response = boto3.client("cloudformation", region_name=region).describe_stacks(
        StackName=stack_name
    )
    return {
        item["OutputKey"]: item["OutputValue"]
        for item in response["Stacks"][0].get("Outputs", [])
    }


def configure_stack(stack_name: str, region: str) -> dict[str, str]:
    outputs = _stack_outputs(stack_name, region)
    missing = [key for key in OUTPUT_ENV if key not in outputs]
    if missing:
        raise SystemExit(f"stack outputs missing: {', '.join(missing)}")
    os.environ["AWS_DEFAULT_REGION"] = region
    for output_key, env_key in OUTPUT_ENV.items():
        os.environ[env_key] = outputs[output_key]
    return outputs


def _reset_named_table(table_name: str, keys: tuple[str, ...], region: str) -> int:
    table = boto3.resource("dynamodb", region_name=region).Table(table_name)
    names = {f"#{key}": key for key in keys}
    kwargs: dict[str, Any] = {
        "ProjectionExpression": ", ".join(names),
        "ExpressionAttributeNames": names,
    }
    deleted = 0
    while True:
        response = table.scan(**kwargs)
        with table.batch_writer() as batch:
            for item in response.get("Items", []):
                batch.delete_item(Key={key: item[key] for key in keys})
                deleted += 1
        if "LastEvaluatedKey" not in response:
            return deleted
        kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]


def _member_count(required: list[dict[str, Any]]) -> int:
    return sum(int(item["count"]) for item in required)


def _assigned_trades(required: list[dict[str, Any]]) -> list[str]:
    trades: list[str] = []
    fallback = "GENERAL"
    for requirement in required:
        trade = fallback if requirement["trade"] == "ANY" else requirement["trade"]
        trades.extend([trade] * int(requirement["count"]))
    return trades


def build_showcase(
    workers: list[dict[str, Any]],
    *,
    today: date | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Build requests, crews and assignments while keeping live worker state coherent."""
    today = today or date.today()
    ready = [worker for worker in workers if worker["office_id"] == "OFFICE001" and worker["state"] == WorkerState.READY]
    if len(ready) < 20:
        raise ValueError("showcase requires at least 20 READY workers in OFFICE001")
    live_pool = iter(ready)
    requests: list[dict[str, Any]] = []
    crews: list[dict[str, Any]] = []
    assignments: list[dict[str, Any]] = []

    for index, spec in enumerate(SITE_SPECS, start=1):
        request_id = f"DEMO-REQUEST-{index:03d}"
        request = build_request(
            request_id=request_id,
            company_id="COMPANY001",
            office_id="OFFICE001",
            site_name=spec["name"],
            work_date=(today + timedelta(days=spec["days"])).isoformat(),
            start_time=["06:30", "07:00", "07:30", "08:00"][index % 4],
            location_text=["부산 해운대구", "부산 강서구", "부산 사상구", "부산 동래구"][index % 4],
            required_workers=spec["required"],
            budget=spec["budget"],
            priority={"cost": (index % 3) + 1, "career": ((index + 1) % 3) + 1, "teamwork": ((index + 2) % 3) + 1},
            notes=["안전교육 이수자 우대", "오전 작업, 안전화 필수", "현장 경험자 우대", "초보자도 지원 가능"][index % 4],
            status=spec["status"],
        )
        if spec["status"] == RequestStatus.CANCELLED:
            request["rejection_reason"] = "공사 일정 변경으로 요청을 취소했습니다."
        elif spec["status"] == RequestStatus.REJECTED:
            request["rejection_reason"] = "요청 조건에 맞는 인원 확보가 어려워 접수하지 못했습니다."
        requests.append(request)

        if spec["status"] not in {
            RequestStatus.PROPOSED,
            RequestStatus.APPROVED,
            RequestStatus.DISPATCHED,
            RequestStatus.RUNNING,
            RequestStatus.COMPLETED,
        }:
            continue

        count = _member_count(spec["required"])
        if spec["status"] in {RequestStatus.APPROVED, RequestStatus.DISPATCHED, RequestStatus.RUNNING}:
            members = [next(live_pool) for _ in range(count)]
        else:
            offset = (index * 3) % max(1, len(ready) - count)
            members = ready[offset:offset + count]
        assigned_trades = _assigned_trades(spec["required"])
        proposed = [
            {
                "worker_id": worker["worker_id"],
                "assigned_trade": trade,
                "offered_wage": worker["desired_daily_wage"],
            }
            for worker, trade in zip(members, assigned_trades)
        ]
        crew_status = {
            RequestStatus.PROPOSED: CrewStatus.PROPOSED,
            RequestStatus.APPROVED: CrewStatus.NOTIFIED,
            RequestStatus.DISPATCHED: CrewStatus.DISPATCHED,
            RequestStatus.RUNNING: CrewStatus.RUNNING,
            RequestStatus.COMPLETED: CrewStatus.COMPLETED,
        }[spec["status"]]
        crew = build_crew(
            crew_id=f"DEMO-CREW-{index:03d}",
            office_id="OFFICE001",
            request_id=request_id,
            proposed_members=proposed,
            status=crew_status,
            source="AGENT" if spec["status"] == RequestStatus.PROPOSED else "MANUAL",
            reason="요청 직종, 경력, 희망 일당을 기준으로 구성한 시연용 작업조입니다.",
            total_cost=sum(int(member["offered_wage"]) for member in proposed),
        )
        crews.append(crew)

        if spec["status"] == RequestStatus.PROPOSED:
            continue
        for worker, member in zip(members, proposed):
            acceptance = Acceptance.PENDING if spec["status"] == RequestStatus.APPROVED else Acceptance.ACCEPTED
            assignment_status = {
                RequestStatus.APPROVED: AssignmentStatus.RESERVED,
                RequestStatus.DISPATCHED: AssignmentStatus.RESERVED,
                RequestStatus.RUNNING: AssignmentStatus.RUNNING,
                RequestStatus.COMPLETED: AssignmentStatus.COMPLETED,
            }[spec["status"]]
            assignments.append(build_assignment(
                crew_id=crew["crew_id"],
                worker_id=worker["worker_id"],
                assigned_trade=member["assigned_trade"],
                offered_wage=member["offered_wage"],
                acceptance=acceptance,
                status=assignment_status,
                created_at=request["work_date"] + "T07:00:00+09:00",
            ))
            if spec["status"] == RequestStatus.COMPLETED:
                continue
            target_state = {
                RequestStatus.APPROVED: WorkerState.NOTIFIED,
                RequestStatus.DISPATCHED: WorkerState.RESERVED,
                RequestStatus.RUNNING: WorkerState.RUNNING,
            }[spec["status"]]
            worker["state"] = target_state
            worker["gsi1sk"] = worker_gsi1sk(target_state, worker["worker_id"])
            worker["state_changed_at"] = now_iso()
            worker["current_crew_id"] = crew["crew_id"] if target_state != WorkerState.NOTIFIED else None
            worker["current_offer"] = build_offer(crew["crew_id"], request, member) if target_state == WorkerState.NOTIFIED else None

    # A few additional requests prove that tenant filtering still isolates company1.
    for index in range(1, 5):
        request = build_request(
            request_id=f"DEMO-OTHER-REQUEST-{index:03d}",
            company_id="COMPANY002",
            office_id="OFFICE002",
            site_name=f"김해 산업시설 {index}공구",
            work_date=(today + timedelta(days=index + 2)).isoformat(),
            start_time="07:00",
            location_text="경남 김해시",
            required_workers=[{"trade": "ANY", "count": index + 1}],
            budget=(index + 1) * 170000,
            status=RequestStatus.REQUESTED,
        )
        requests.append(request)
    return requests, crews, assignments


def _link_demo_accounts(outputs: dict[str, str], region: str, password: str) -> None:
    pool_id = outputs["UserPoolId"]
    client = boto3.client("cognito-idp", region_name=region)
    for account in DEMO_ACCOUNTS:
        sub = _create_user(client, pool_id, account, password)
        _link_entity(account, sub)


def main() -> None:
    parser = argparse.ArgumentParser(description="CrewMate 시연 데이터 전체 초기화 및 생성")
    parser.add_argument("--stack-name", default="crewmate")
    parser.add_argument("--stage", default="dev")
    parser.add_argument("--region", default="ap-northeast-2")
    parser.add_argument("--workers", type=int, default=72)
    parser.add_argument("--password", default=DEFAULT_PASSWORD)
    parser.add_argument("--confirm-reset", help="초기화 대상 stage 이름을 다시 입력")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.workers < 50:
        raise SystemExit("--workers must be at least 50 for the showcase dataset")
    if not args.dry_run and args.confirm_reset != args.stage:
        raise SystemExit(f"destructive reset requires --confirm-reset {args.stage}")

    _init_seed()
    workers = generate_workers(args.workers)
    requests, crews, assignments = build_showcase(workers)
    counts = Counter(request["status"] for request in requests if request["company_id"] == "COMPANY001")
    if args.dry_run:
        print(f"[dry-run] workers={len(workers)}, requests={len(requests)}, crews={len(crews)}, assignments={len(assignments)}")
        print(f"[dry-run] company1 statuses={dict(counts)}")
        return

    outputs = configure_stack(args.stack_name, args.region)
    removed = reset_all()
    for output_key, keys in (
        ("QualificationEvidenceCacheTableName", ("normalized_name",)),
        ("SpecReportJobsTableName", ("report_id",)),
    ):
        table_name = outputs.get(output_key)
        if table_name:
            removed += _reset_named_table(table_name, keys, args.region)
    print(f"[reset] DynamoDB items removed={removed}")

    write_items("offices", generate_offices())
    write_items("companies", generate_companies())
    write_items("workers", workers)
    write_items("requests", requests)
    write_items("crews", crews)
    write_items("assignments", assignments)
    _link_demo_accounts(outputs, args.region, args.password)

    print(
        f"[seed] workers={len(workers)}+demo accounts, requests={len(requests)}, "
        f"crews={len(crews)}, assignments={len(assignments)}"
    )
    print(f"[seed] company1 statuses={dict(counts)}")
    print(f"[seed] login: worker1/office1/company1 (password={args.password})")


if __name__ == "__main__":
    main()
