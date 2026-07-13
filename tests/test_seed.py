"""시드 스크립트 (F-A7) 테스트 — 생성 로직 및 리셋을 moto로 검증."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# scripts/seed 를 import 경로에 추가 (스크립트는 `from common import ...` 사용)
_SEED_DIR = Path(__file__).resolve().parents[1] / "scripts" / "seed"
sys.path.insert(0, str(_SEED_DIR))

faker = pytest.importorskip("faker")  # noqa: F841


def test_generate_workers_count_and_distribution(table):
    import seed_workers

    seed_workers._init_seed()
    workers = seed_workers.generate_workers(60)
    assert len(workers) == 60
    # 두 사무소에 분산
    offices = {w["office_id"] for w in workers}
    assert offices == {"OFFICE001", "OFFICE002"}
    # 주민등록번호 필드 없음, 상태 유효
    for w in workers:
        assert "rrn" not in w and "resident_id" not in w
        assert w["state"] in ("READY", "INACTIVE")
        assert 1 <= w["skill_level"] <= 5


def test_generate_workers_deterministic(table):
    import seed_workers

    seed_workers._init_seed()
    first = [w["name"] for w in seed_workers.generate_workers(10)]
    seed_workers._init_seed()
    second = [w["name"] for w in seed_workers.generate_workers(10)]
    assert first == second  # seed=42 고정 → 재현 가능


def test_generate_requests(table):
    import seed_workers

    seed_workers._init_seed()
    requests = seed_workers.generate_requests(7)
    assert len(requests) == 7
    for r in requests:
        assert r["status"] == "REQUESTED"
        assert len(r["required_workers"]) >= 1


def test_write_and_reset(table):
    import seed_workers
    from common import reset_table, write_items

    seed_workers._init_seed()
    write_items(seed_workers.generate_workers(20))
    # 스캔으로 20건 확인
    assert table.scan()["Count"] == 20
    removed = reset_table()
    assert removed == 20
    assert table.scan()["Count"] == 0


def test_history_derives_collaborations(table):
    import seed_history
    import seed_workers
    from common import write_items

    seed_workers._init_seed()
    write_items(seed_workers.generate_workers(30))

    seed_history._init_seed()
    crews, collabs = seed_history.generate_history(num_crews_per_office=6)
    assert len(crews) > 0
    # 협업 아이템은 양방향(짝수)
    assert len(collabs) % 2 == 0
    for c in crews:
        assert c["status"] == "COMPLETED"


def test_demo_scenario_set(table):
    import seed_demo_scenario as demo
    from common import write_items

    items = demo.build_demo_items()
    write_items(items)

    by_id = {}
    for w in demo.DEMO_WORKERS:
        wid = w[0]
        from shared.db import worker_pk

        by_id[wid] = table.get_item(Key={"PK": worker_pk(wid), "SK": "PROFILE"})["Item"]

    # A,B,C RUNNING + current_crew_id, D,E,F READY
    for wid in ("DEMO-A", "DEMO-B", "DEMO-C"):
        assert by_id[wid]["state"] == "RUNNING"
        assert by_id[wid]["current_crew_id"] == demo.CREW_ID
    for wid in ("DEMO-D", "DEMO-E", "DEMO-F"):
        assert by_id[wid]["state"] == "READY"

    # 작업조 A+B+C RUNNING, 요청 crew_id 연결
    from shared.db import crew_pk, request_pk

    crew = table.get_item(Key={"PK": crew_pk(demo.CREW_ID), "SK": "META"})["Item"]
    assert set(crew["member_ids"]) == {"DEMO-A", "DEMO-B", "DEMO-C"}
    assert crew["status"] == "RUNNING"
    req = table.get_item(Key={"PK": request_pk(demo.REQUEST_ID), "SK": "META"})["Item"]
    assert req["crew_id"] == demo.CREW_ID

    # E는 A·B와 협업 이력 보유
    from shared.db import query_worker_collaborations

    e_collabs = {c["other_id"] for c in query_worker_collaborations("DEMO-E")}
    assert {"DEMO-A", "DEMO-B"} <= e_collabs


def test_demo_reset(table):
    import seed_demo_scenario as demo
    from common import write_items

    write_items(demo.build_demo_items())
    before = table.scan()["Count"]
    assert before > 0
    demo.reset_demo()
    assert table.scan()["Count"] == 0
