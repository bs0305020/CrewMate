"""seed_history.py (F-A7).

작업 이력(완료된 작업조) 100건 내외와, 거기서 파생된 협업 이력 50~100쌍을
양방향 아이템으로 생성한다. seed_workers 이후 실행한다.

사용법:
  python scripts/seed/seed_history.py
"""

from __future__ import annotations

import argparse
import random
from itertools import combinations
from typing import Any

from faker import Faker

from common import (  # noqa: E402
    OFFICES,
    SEED,
    build_collaboration,
    write_items,
)

from shared.db import query_office_all_workers  # noqa: E402
from shared.schemas import build_crew  # noqa: E402
from shared.state import CrewStatus  # noqa: E402

_fake = Faker("ko_KR")


def _init_seed() -> None:
    random.seed(SEED)
    Faker.seed(SEED)


def _workers_by_office() -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for office_id in OFFICES:
        items = query_office_all_workers(office_id)
        result[office_id] = [w["worker_id"] for w in items]
    return result


def generate_history(num_crews_per_office: int = 6) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """완료 작업조 이력 및 협업 이력(양방향)을 생성한다."""
    crews: list[dict[str, Any]] = []
    collabs: list[dict[str, Any]] = []
    seen_pairs: set[tuple[str, str, str]] = set()

    by_office = _workers_by_office()
    for office_id, worker_ids in by_office.items():
        if len(worker_ids) < 3:
            continue
        for n in range(num_crews_per_office):
            size = random.randint(3, min(5, len(worker_ids)))
            members = random.sample(worker_ids, k=size)
            date = _fake.date_between("-90d", "-1d").isoformat()
            crew = build_crew(
                office_id=office_id,
                request_id=f"HIST-{office_id}-{n:02d}",
                member_ids=members,
                status=CrewStatus.COMPLETED,
                source="MANUAL",
            )
            crews.append(crew)

            # 동일 작업조 공동 투입 → 협업 이력 파생 (양방향)
            for a, b in combinations(sorted(members), 2):
                key = (a, b, date)
                if key in seen_pairs:
                    continue
                seen_pairs.add(key)
                collabs.extend(build_collaboration(a, b, date))

    return crews, collabs


def main() -> None:
    parser = argparse.ArgumentParser(description="작업/협업 이력 시드")
    parser.add_argument("--crews-per-office", type=int, default=6)
    args = parser.parse_args()

    _init_seed()
    crews, collabs = generate_history(args.crews_per_office)
    n_c = write_items(crews)
    n_col = write_items(collabs)
    pairs = n_col // 2
    print(f"[seed] 완료 작업조 {n_c}건, 협업 이력 {pairs}쌍({n_col}아이템) 생성 완료 (seed={SEED})")


if __name__ == "__main__":
    main()
