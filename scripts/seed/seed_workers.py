"""seed_workers.py (계약 v2).

사무소 2곳, 건설사 2곳, 근로자 50~100명(희망/비희망 직종 배열, 성실도 카운트 다양화),
요청 5~10건을 생성한다. seed = 42 고정.

사용법:
  python scripts/seed/seed_workers.py            # 추가 시드
  python scripts/seed/seed_workers.py --reset    # 전체 테이블 초기화 후 시드
"""

from __future__ import annotations

import argparse
import random
from typing import Any

from faker import Faker

from common import (  # noqa: E402
    CERTIFICATIONS,
    COMPANIES,
    OFFICES,
    REGIONS,
    SEED,
    TRADES,
    reset_all,
    write_items,
)

from shared.schemas import build_company, build_office, build_request, build_worker  # noqa: E402
from shared.state import WorkerState  # noqa: E402

_fake = Faker("ko_KR")

OFFICE_META = {
    "OFFICE001": ("부산인력사무소", "부산 해운대구"),
    "OFFICE002": ("김해인력사무소", "경남 김해시"),
}
COMPANY_META = {
    "COMPANY001": ("해운대건설", "부산 해운대구"),
    "COMPANY002": ("김해종합건설", "경남 김해시"),
}


def _init_seed() -> None:
    random.seed(SEED)
    Faker.seed(SEED)


def generate_offices() -> list[dict[str, Any]]:
    return [
        build_office(office_id=oid, name=name, region=region, owner_name=name)
        for oid, (name, region) in OFFICE_META.items()
    ]


def generate_companies() -> list[dict[str, Any]]:
    return [
        build_company(company_id=cid, name=name, region=region)
        for cid, (name, region) in COMPANY_META.items()
    ]


def generate_workers(num_workers: int = 60) -> list[dict[str, Any]]:
    workers = []
    for i in range(num_workers):
        office_id = OFFICES[0] if i % 4 else OFFICES[1]  # OFFICE001 편중
        prefs = random.sample(TRADES, k=random.randint(1, 2))
        remaining = [t for t in TRADES if t not in prefs]
        excluded = random.sample(remaining, k=random.randint(0, 1))
        state = WorkerState.READY if random.random() < 0.55 else WorkerState.INACTIVE
        dispatched = random.randint(0, 60)
        # 성실도 다양화: 대부분 완료율 높음, 일부 낮음
        completed = dispatched if random.random() < 0.8 else max(0, dispatched - random.randint(1, 10))
        worker = build_worker(
            user_id=f"seed-user-{i:03d}",
            name=_fake.name(),
            phone=_fake.numerify("010-####-####"),
            office_id=office_id,
            preferred_trades=prefs,
            excluded_trades=excluded,
            career_years=random.randint(0, 25),
            age=random.randint(20, 63),
            region=random.choice(REGIONS),
            desired_daily_wage=random.randrange(130000, 240000, 5000),
            certifications=random.sample(CERTIFICATIONS, k=random.randint(0, 2)),
            state=state,
            completed_count=completed,
            dispatched_count=dispatched,
        )
        workers.append(worker)
    return workers


def generate_requests(num_requests: int = 7) -> list[dict[str, Any]]:
    requests = []
    for i in range(num_requests):
        chosen = random.sample(TRADES, k=random.randint(1, 3))
        required = [{"trade": t, "count": random.randint(1, 4)} for t in chosen]
        total = sum(r["count"] for r in required)
        req = build_request(
            company_id=random.choice(COMPANIES),
            office_id=OFFICES[0],
            site_name=f"{_fake.city()} 현장 {i + 1}",
            work_date=_fake.date_between("+1d", "+14d").isoformat(),
            start_time=random.choice(["07:00", "08:00", "06:30"]),
            location_text=_fake.address().replace("\n", " "),
            required_workers=required,
            budget=total * random.randrange(150000, 200000, 10000),
            priority=dict(zip(("cost", "career", "teamwork"), random.sample([1, 2, 3], 3))),
            notes=random.choice(["비용 우선", "경력 우선", ""]),
        )
        requests.append(req)
    return requests


def main() -> None:
    parser = argparse.ArgumentParser(description="사무소/건설사/근로자/요청 시드")
    parser.add_argument("--reset", action="store_true", help="시드 전에 전체 테이블 초기화")
    parser.add_argument("--workers", type=int, default=60)
    parser.add_argument("--requests", type=int, default=7)
    args = parser.parse_args()

    _init_seed()
    if args.reset:
        removed = reset_all()
        print(f"[reset] 전체 테이블 아이템 {removed}건 삭제")

    n_o = write_items("offices", generate_offices())
    n_c = write_items("companies", generate_companies())
    workers = generate_workers(args.workers)
    n_w = write_items("workers", workers)
    n_r = write_items("requests", generate_requests(args.requests))
    ready = sum(1 for w in workers if w["state"] == WorkerState.READY)
    print(f"[seed] 사무소 {n_o}, 건설사 {n_c}, 근로자 {n_w}명(READY {ready}), 요청 {n_r}건 (seed={SEED})")


if __name__ == "__main__":
    main()
