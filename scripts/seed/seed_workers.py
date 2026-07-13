"""seed_workers.py (F-A7).

근로자 50~100명(사무소 2곳 분산), 건설사 2~3곳, 인력 요청 5~10건을 생성한다.
seed = 42 고정.

사용법:
  python scripts/seed/seed_workers.py            # 기존 데이터 유지하고 추가
  python scripts/seed/seed_workers.py --reset    # 테이블 초기화 후 시드
"""

from __future__ import annotations

import argparse
import random
from typing import Any

from faker import Faker

from common import (  # noqa: E402
    COMPANIES,
    OFFICES,
    REGIONS,
    SEED,
    CERTIFICATIONS,
    TRADES,
    reset_table,
    write_items,
)

# backend/shared (common 이 sys.path 설정)
from shared.schemas import build_request, build_worker  # noqa: E402
from shared.state import WorkerState  # noqa: E402

_fake = Faker("ko_KR")


def _init_seed() -> None:
    random.seed(SEED)
    Faker.seed(SEED)


def generate_workers(num_workers: int = 60) -> list[dict[str, Any]]:
    """근로자 프로필을 생성한다. 절반 정도는 READY 로 시작한다."""
    workers = []
    for i in range(num_workers):
        office_id = OFFICES[i % len(OFFICES)]
        trade = random.choice(TRADES)
        skill = random.randint(1, 5)
        state = WorkerState.READY if random.random() < 0.5 else WorkerState.INACTIVE
        certs = random.sample(CERTIFICATIONS, k=random.randint(0, 2))
        completed = random.randint(0, 60)
        # 노쇼는 내부 운영용 데이터 (COMPANY/Agent 응답에는 노출되지 않음)
        no_show = random.randint(0, 3)
        worker = build_worker(
            user_id=f"seed-user-{i:03d}",
            name=_fake.name(),
            phone=_fake.numerify("010-####-####"),
            office_id=office_id,
            trade=trade,
            skill_level=skill,
            career_years=random.randint(0, 25),
            age=random.randint(20, 63),
            region=random.choice(REGIONS),
            desired_daily_wage=random.randrange(130000, 240000, 5000),
            certifications=certs,
            state=state,
            completed_count=completed,
            no_show_count=no_show,
        )
        workers.append(worker)
    return workers


def generate_requests(num_requests: int = 7) -> list[dict[str, Any]]:
    """건설사 인력 요청을 생성한다 (직종별 필수 인원 포함)."""
    requests = []
    for i in range(num_requests):
        n_trades = random.randint(1, 3)
        chosen = random.sample(TRADES, k=n_trades)
        required = [{"trade": t, "count": random.randint(1, 5)} for t in chosen]
        total = sum(r["count"] for r in required)
        req = build_request(
            company_id=random.choice(COMPANIES),
            office_id=random.choice(OFFICES),
            site_name=f"{_fake.city()} 현장 {i + 1}",
            work_date=_fake.date_between("+1d", "+14d").isoformat(),
            start_time=random.choice(["07:00", "08:00", "06:30"]),
            location_text=_fake.address().replace("\n", " "),
            required_workers=required,
            budget=total * random.randrange(150000, 200000, 10000),
            priority={"cost": 0.4, "skill": 0.3, "proximity": 0.2, "stability": 0.1},
            notes=random.choice(["비용 우선", "숙련 우선", "근접 우선", ""]),
        )
        requests.append(req)
    return requests


def main() -> None:
    parser = argparse.ArgumentParser(description="근로자/요청 시드")
    parser.add_argument("--reset", action="store_true", help="시드 전에 테이블 초기화")
    parser.add_argument("--workers", type=int, default=60, help="생성할 근로자 수 (50~100 권장)")
    parser.add_argument("--requests", type=int, default=7, help="생성할 요청 수 (5~10 권장)")
    args = parser.parse_args()

    _init_seed()
    if args.reset:
        removed = reset_table()
        print(f"[reset] 기존 아이템 {removed}건 삭제")

    workers = generate_workers(args.workers)
    requests = generate_requests(args.requests)
    n_w = write_items(workers)
    n_r = write_items(requests)
    ready = sum(1 for w in workers if w["state"] == WorkerState.READY)
    print(f"[seed] 근로자 {n_w}명 (READY {ready}), 요청 {n_r}건 생성 완료 (seed={SEED})")


if __name__ == "__main__":
    main()
