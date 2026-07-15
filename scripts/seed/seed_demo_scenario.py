"""seed_demo_scenario.py (계약 v2).

긴급 재편성 데모(시나리오 3) 고정 세트를 보장한다.

  A·B·C: RUNNING (작업조 DEMO-CREW-001, 요청 DEMO-REQ-001 RUNNING). C가 노쇼 대상.
  D·E·F: READY. E는 A·B와 과거 협업 이력(완료 작업조 공유). 성실도 낮은 근로자 G(READY) 포함.

데모: 건설사가 C 결원 등록 → 사무소 agent-recompose(EMERGENCY) → E 추천 → 긴급 승인 → E 수락 → A+B+E.

사용법:
  python scripts/seed/seed_demo_scenario.py
  python scripts/seed/seed_demo_scenario.py --no-reset
"""

from __future__ import annotations

import argparse
from typing import Any

from common import write_items

from shared import db  # noqa: E402
from shared.schemas import (  # noqa: E402
    build_assignment,
    build_crew,
    build_request,
    build_worker,
)
from shared.state import (  # noqa: E402
    Acceptance,
    AssignmentStatus,
    CrewStatus,
    RequestStatus,
    WorkerState,
)

OFFICE_ID = "OFFICE001"
COMPANY_ID = "COMPANY001"
CREW_ID = "DEMO-CREW-001"
REQUEST_ID = "DEMO-REQ-001"
PAST_CREW_ID = "DEMO-CREW-PAST"
PAST_REQUEST_ID = "DEMO-REQ-PAST"

# (worker_id, 이름, 상태, completed, dispatched)
DEMO_WORKERS = [
    ("DEMO-A", "김철수", WorkerState.RUNNING, 30, 31),
    ("DEMO-B", "이영호", WorkerState.RUNNING, 28, 29),
    ("DEMO-C", "박민수", WorkerState.RUNNING, 20, 22),
    ("DEMO-D", "정대현", WorkerState.READY, 15, 16),
    ("DEMO-E", "최강현", WorkerState.READY, 40, 41),  # A·B와 협업 이력
    ("DEMO-F", "한지훈", WorkerState.READY, 12, 12),
    ("DEMO-G", "오준석", WorkerState.READY, 5, 12),   # 성실도 낮음(시연용)
]
DEMO_IDS = [w[0] for w in DEMO_WORKERS]


def reset_demo() -> int:
    removed = 0
    for wid in DEMO_IDS:
        if db.get_worker(wid):
            db.workers_table().delete_item(Key={"worker_id": wid})
            removed += 1
    for crew_id in (CREW_ID, PAST_CREW_ID):
        for a in db.query_crew_assignments(crew_id):
            db.assignments_table().delete_item(Key={"crew_id": crew_id, "worker_id": a["worker_id"]})
            removed += 1
        db.crews_table().delete_item(Key={"crew_id": crew_id})
        removed += 1
    for rid in (REQUEST_ID, PAST_REQUEST_ID):
        db.requests_table().delete_item(Key={"request_id": rid})
        removed += 1
    return removed


def build_demo():
    workers, crews, requests, assignments = [], [], [], []

    for wid, name, state, completed, dispatched in DEMO_WORKERS:
        w = build_worker(
            user_id=f"demo-user-{wid}", worker_id=wid, name=name,
            phone="010-1234-5678", office_id=OFFICE_ID,
            preferred_trades=["GENERAL"], excluded_trades=[],
            career_years=5, age=40,
            region="부산 해운대구", desired_daily_wage=150000,
            state=state, completed_count=completed, dispatched_count=dispatched,
        )
        if state == WorkerState.RUNNING:
            w["current_crew_id"] = CREW_ID
        workers.append(w)

    # 현재 RUNNING 요청/작업조 (A+B+C)
    req = build_request(
        company_id=COMPANY_ID, office_id=OFFICE_ID,
        site_name="해운대 데모 현장", work_date="2026-07-15", start_time="07:00",
        location_text="부산 해운대구 센텀로 100",
        required_workers=[{"trade": "GENERAL", "count": 3}],
        budget=450000, priority={"cost": 1, "career": 2, "teamwork": 3},
        notes="데모 시나리오", request_id=REQUEST_ID, status=RequestStatus.RUNNING,
    )
    requests.append(req)
    crew = build_crew(
        office_id=OFFICE_ID, request_id=REQUEST_ID,
        proposed_members=[{"worker_id": m, "assigned_trade": "GENERAL", "offered_wage": 150000}
                          for m in ("DEMO-A", "DEMO-B", "DEMO-C")],
        status=CrewStatus.RUNNING, crew_id=CREW_ID, source="MANUAL",
    )
    crews.append(crew)
    for m in ("DEMO-A", "DEMO-B", "DEMO-C"):
        assignments.append(build_assignment(
            crew_id=CREW_ID, worker_id=m, assigned_trade="GENERAL", offered_wage=150000,
            acceptance=Acceptance.ACCEPTED, status=AssignmentStatus.RUNNING,
        ))

    # 과거 완료 작업조: E가 A·B와 협업 (협업 이력 원천)
    past_req = build_request(
        company_id=COMPANY_ID, office_id=OFFICE_ID,
        site_name="과거 협업 현장", work_date="2026-06-01", start_time="07:00",
        location_text="부산 해운대구", required_workers=[{"trade": "GENERAL", "count": 3}],
        budget=450000, request_id=PAST_REQUEST_ID, status=RequestStatus.COMPLETED,
    )
    requests.append(past_req)
    past_crew = build_crew(
        office_id=OFFICE_ID, request_id=PAST_REQUEST_ID,
        proposed_members=[{"worker_id": m, "assigned_trade": "GENERAL", "offered_wage": 150000}
                          for m in ("DEMO-A", "DEMO-B", "DEMO-E")],
        status=CrewStatus.COMPLETED, crew_id=PAST_CREW_ID, source="MANUAL",
    )
    crews.append(past_crew)
    for m in ("DEMO-A", "DEMO-B", "DEMO-E"):
        assignments.append(build_assignment(
            crew_id=PAST_CREW_ID, worker_id=m, assigned_trade="GENERAL", offered_wage=150000,
            acceptance=Acceptance.ACCEPTED, status=AssignmentStatus.COMPLETED,
            created_at="2026-06-01T07:00:00+00:00",
        ))

    return workers, requests, crews, assignments


def main() -> None:
    parser = argparse.ArgumentParser(description="긴급 재편성 데모 세트 시드")
    parser.add_argument("--no-reset", action="store_true", help="기존 데모 데이터를 지우지 않음")
    args = parser.parse_args()

    if not args.no_reset:
        print(f"[reset] 기존 데모 아이템 {reset_demo()}건 삭제")

    workers, requests, crews, assignments = build_demo()
    write_items("workers", workers)
    write_items("requests", requests)
    write_items("crews", crews)
    write_items("assignments", assignments)
    print(f"[seed] 데모 세트 생성 완료: A·B·C RUNNING(crew={CREW_ID}), D·E·F·G READY, "
          f"E는 A·B 협업 이력, G는 성실도 낮음")


if __name__ == "__main__":
    main()
