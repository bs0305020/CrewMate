"""seed_demo_scenario.py (F-A7).

긴급 재편성 데모(시나리오 3) 전용 고정 세트를 보장한다.

  A: RUNNING (기존 작업조)   B: RUNNING (기존 작업조)   C: RUNNING (노쇼 대상)
  D: READY                   E: READY (A·B와 협업 이력)  F: READY

작업조 DEMO-CREW-001 = A+B+C (RUNNING), 요청 DEMO-REQ-001 (RUNNING).
데모: 건설사가 C에 대해 결원 이벤트 등록 → 사무소가 E로 긴급 승인 → A+B+E.

재실행 시 데모 데이터만 초기화하는 리셋을 기본 수행한다 (반복 리허설용).

사용법:
  python scripts/seed/seed_demo_scenario.py
  python scripts/seed/seed_demo_scenario.py --no-reset
"""

from __future__ import annotations

import argparse
from typing import Any

from common import build_collaboration, write_items  # noqa: E402

from shared.db import (  # noqa: E402
    crew_pk,
    delete_item,
    get_table,
    query_worker_collaborations,
    request_pk,
    worker_pk,
)
from shared.schemas import build_crew, build_request, build_worker  # noqa: E402
from shared.state import CrewStatus, RequestStatus, WorkerState  # noqa: E402

OFFICE_ID = "OFFICE001"
COMPANY_ID = "COMPANY001"
CREW_ID = "DEMO-CREW-001"
REQUEST_ID = "DEMO-REQ-001"

# (worker_id, 이름, 상태)
DEMO_WORKERS = [
    ("DEMO-A", "김철수", WorkerState.RUNNING),
    ("DEMO-B", "이영호", WorkerState.RUNNING),
    ("DEMO-C", "박민수", WorkerState.RUNNING),  # 노쇼 대상
    ("DEMO-D", "정대현", WorkerState.READY),
    ("DEMO-E", "최강현", WorkerState.READY),   # A·B와 협업 이력
    ("DEMO-F", "한지훈", WorkerState.READY),
]
DEMO_IDS = [w[0] for w in DEMO_WORKERS]


def reset_demo() -> int:
    """데모 아이템만 삭제한다 (근로자/협업/작업조/요청)."""
    removed = 0
    for wid in DEMO_IDS:
        for collab in query_worker_collaborations(wid):
            get_table().delete_item(Key={"PK": collab["PK"], "SK": collab["SK"]})
            removed += 1
        delete_item(worker_pk(wid), "PROFILE")
        removed += 1
    delete_item(crew_pk(CREW_ID), "META")
    delete_item(request_pk(REQUEST_ID), "META")
    removed += 2
    return removed


def build_demo_items() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []

    # 근로자 6명 (전원 GENERAL: 새 조합 A+B+E 가 필수 인원 3명을 충족)
    for wid, name, state in DEMO_WORKERS:
        w = build_worker(
            user_id=f"demo-user-{wid}",
            worker_id=wid,
            name=name,
            phone="010-1234-5678",
            office_id=OFFICE_ID,
            trade="GENERAL",
            skill_level=3,
            career_years=5,
            age=40,
            region="BUSAN_HAEUNDAE",
            desired_daily_wage=150000,
            state=state,
            completed_count=30,
            no_show_count=0,
        )
        if state == WorkerState.RUNNING:
            w["current_crew_id"] = CREW_ID
        items.append(w)

    # 요청 (RUNNING, 작업조 연결)
    request = build_request(
        company_id=COMPANY_ID,
        office_id=OFFICE_ID,
        site_name="해운대 데모 현장",
        work_date="2026-07-13",
        start_time="07:00",
        location_text="부산 해운대구 센텀로 100",
        required_workers=[{"trade": "GENERAL", "count": 3}],
        budget=450000,
        priority={"cost": 0.4, "skill": 0.3, "proximity": 0.2, "stability": 0.1},
        notes="데모 시나리오",
        request_id=REQUEST_ID,
        status=RequestStatus.RUNNING,
    )
    request["crew_id"] = CREW_ID
    items.append(request)

    # 작업조 (RUNNING, A+B+C)
    crew = build_crew(
        office_id=OFFICE_ID,
        request_id=REQUEST_ID,
        member_ids=["DEMO-A", "DEMO-B", "DEMO-C"],
        status=CrewStatus.RUNNING,
        crew_id=CREW_ID,
        source="MANUAL",
    )
    items.append(crew)

    # 협업 이력: E 는 A·B 와 과거 협업 (긴급 편성 시 조합 품질 근거)
    items.extend(build_collaboration("DEMO-E", "DEMO-A", "2026-06-01"))
    items.extend(build_collaboration("DEMO-E", "DEMO-B", "2026-06-15"))

    return items


def main() -> None:
    parser = argparse.ArgumentParser(description="긴급 재편성 데모 세트 시드")
    parser.add_argument("--no-reset", action="store_true", help="기존 데모 데이터를 지우지 않음")
    args = parser.parse_args()

    if not args.no_reset:
        removed = reset_demo()
        print(f"[reset] 기존 데모 아이템 {removed}건 삭제")

    items = build_demo_items()
    n = write_items(items)
    print(
        f"[seed] 데모 세트 {n}건 생성 완료: "
        f"A·B·C RUNNING(crew={CREW_ID}), D·E·F READY, E는 A·B 협업 이력 보유"
    )


if __name__ == "__main__":
    main()
