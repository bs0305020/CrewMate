"""assignment Lambda (F-A5) 테스트 — 승인/배차 및 동시성."""

from __future__ import annotations

import pytest
from conftest import body_of, make_event

OFFICE_SUB = "office-1"
OFFICE_ID = "OFFICE001"


def _handler():
    from functions.assignment.app import lambda_handler

    return lambda_handler


def _seed_worker(table, worker_id, *, trade="FORMWORK", state="READY", office_id=OFFICE_ID):
    from shared.schemas import build_worker

    w = build_worker(
        user_id=worker_id, worker_id=worker_id, name=f"근로자{worker_id}", phone="010-0000-0000",
        office_id=office_id, trade=trade, skill_level=4, career_years=5, age=40,
        region="BUSAN_HAEUNDAE", desired_daily_wage=170000, state=state,
    )
    table.put_item(Item=w)
    return w


def _seed_request(table, *, required_workers, status="REQUESTED"):
    from shared.schemas import build_request

    r = build_request(
        company_id="COMPANY001", office_id=OFFICE_ID, site_name="해운대 현장", work_date="2026-07-13",
        start_time="07:00", location_text="부산 해운대", required_workers=required_workers, budget=1000000,
        status=status,
    )
    table.put_item(Item=r)
    return r


def _seed_crew(table, *, request_id, member_ids, status="DRAFT"):
    from shared.schemas import build_crew

    c = build_crew(office_id=OFFICE_ID, request_id=request_id, member_ids=member_ids, status=status)
    table.put_item(Item=c)
    return c


def _approve_event(crew_id):
    return make_event(
        "POST", "/office/crews/{crewId}/approve", role="OFFICE", sub=OFFICE_SUB,
        office_id=OFFICE_ID, path_params={"crewId": crew_id},
    )


def test_approve_runs_full_crew(table):
    from shared.db import crew_pk, request_pk, worker_pk

    _seed_worker(table, "W1", trade="FORMWORK")
    _seed_worker(table, "W2", trade="GENERAL")
    req = _seed_request(table, required_workers=[
        {"trade": "FORMWORK", "count": 1}, {"trade": "GENERAL", "count": 1},
    ])
    crew = _seed_crew(table, request_id=req["request_id"], member_ids=["W1", "W2"])

    resp = _handler()(_approve_event(crew["crew_id"]), None)
    assert resp["statusCode"] == 200
    assert body_of(resp)["data"]["status"] == "RUNNING"

    # 조원 전원 RUNNING + current_crew_id
    for wid in ("W1", "W2"):
        w = table.get_item(Key={"PK": worker_pk(wid), "SK": "PROFILE"})["Item"]
        assert w["state"] == "RUNNING"
        assert w["current_crew_id"] == crew["crew_id"]

    # 요청 RUNNING + crew_id 연결
    r = table.get_item(Key={"PK": request_pk(req["request_id"]), "SK": "META"})["Item"]
    assert r["status"] == "RUNNING"
    assert r["crew_id"] == crew["crew_id"]

    # 알림 생성 확인
    from shared.db import query_notifications

    assert len(query_notifications("W1")) == 1


def test_approve_rejects_already_approved(table):
    _seed_worker(table, "W1")
    req = _seed_request(table, required_workers=[{"trade": "FORMWORK", "count": 1}])
    crew = _seed_crew(table, request_id=req["request_id"], member_ids=["W1"], status="RUNNING")
    resp = _handler()(_approve_event(crew["crew_id"]), None)
    assert resp["statusCode"] == 422
    assert body_of(resp)["error"]["code"] == "CREW_INVALID"


def test_approve_member_not_ready(table):
    _seed_worker(table, "W1", state="INACTIVE")
    req = _seed_request(table, required_workers=[{"trade": "FORMWORK", "count": 1}])
    crew = _seed_crew(table, request_id=req["request_id"], member_ids=["W1"])
    resp = _handler()(_approve_event(crew["crew_id"]), None)
    assert resp["statusCode"] == 409
    assert body_of(resp)["error"]["code"] == "WORKER_NOT_READY"


def test_approve_other_office_forbidden(table):
    _seed_worker(table, "W1")
    req = _seed_request(table, required_workers=[{"trade": "FORMWORK", "count": 1}])
    crew = _seed_crew(table, request_id=req["request_id"], member_ids=["W1"])
    ev = make_event(
        "POST", "/office/crews/{crewId}/approve", role="OFFICE", sub="office-2",
        office_id="OFFICE999", path_params={"crewId": crew["crew_id"]},
    )
    resp = _handler()(ev, None)
    assert resp["statusCode"] == 403


def test_activate_members_transaction_conflict(table):
    """사전검증을 우회해 트랜잭션 조건부 쓰기 충돌 경로를 직접 검증한다."""
    from functions.assignment.app import activate_members
    from shared.responses import ApiError

    # RESERVED 상태(=READY 아님) 근로자 → 1단계 조건부 쓰기 실패
    _seed_worker(table, "W1", state="RESERVED")
    req = _seed_request(table, required_workers=[{"trade": "FORMWORK", "count": 1}])
    crew = _seed_crew(table, request_id=req["request_id"], member_ids=["W1"])

    with pytest.raises(ApiError) as exc:
        activate_members(new_member_ids=["W1"], crew_id=crew["crew_id"], request_id=req["request_id"])
    assert exc.value.code == "STATE_CONFLICT"

    # 충돌 시 근로자 상태는 변하지 않아야 함
    from shared.db import worker_pk

    w = table.get_item(Key={"PK": worker_pk("W1"), "SK": "PROFILE"})["Item"]
    assert w["state"] == "RESERVED"
