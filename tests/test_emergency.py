"""assignment Lambda 긴급 재편성 (F-A5 emergency) 테스트.

데모 시나리오 3: 작업조 A+B+C에서 C 노쇼 → 대체 인력 E 승인 → A+B+E.
"""

from __future__ import annotations

import pytest
from conftest import body_of, make_event

OFFICE_SUB = "office-1"
OFFICE_ID = "OFFICE001"


def _handler():
    from functions.assignment.app import lambda_handler

    return lambda_handler


def _seed_worker(table, worker_id, *, state, trade="GENERAL", crew_id=None, office_id=OFFICE_ID):
    from shared.schemas import build_worker

    w = build_worker(
        user_id=worker_id, worker_id=worker_id, name=f"근로자{worker_id}", phone="010-0000-0000",
        office_id=office_id, trade=trade, skill_level=3, career_years=5, age=40,
        region="BUSAN_HAEUNDAE", desired_daily_wage=150000, state=state,
    )
    if crew_id:
        w["current_crew_id"] = crew_id
    table.put_item(Item=w)
    return w


def _setup_scenario(table, *, required_workers=None, replacement_state="READY"):
    """A,B RUNNING(고정) / C RUNNING(노쇼) / E replacement. crew=A+B+C, gap missing=C."""
    from shared.schemas import build_crew, build_gap_event, build_request

    required_workers = required_workers or [{"trade": "GENERAL", "count": 3}]

    req = build_request(
        company_id="COMPANY001", office_id=OFFICE_ID, site_name="해운대 현장", work_date="2026-07-13",
        start_time="07:00", location_text="부산 해운대", required_workers=required_workers,
        budget=1000000, status="RUNNING",
    )
    table.put_item(Item=req)

    crew = build_crew(office_id=OFFICE_ID, request_id=req["request_id"], member_ids=["A", "B", "C"], status="RUNNING")
    table.put_item(Item=crew)

    _seed_worker(table, "A", state="RUNNING", crew_id=crew["crew_id"])
    _seed_worker(table, "B", state="RUNNING", crew_id=crew["crew_id"])
    _seed_worker(table, "C", state="RUNNING", crew_id=crew["crew_id"])
    _seed_worker(table, "E", state=replacement_state)

    gap = build_gap_event(
        office_id=OFFICE_ID, crew_id=crew["crew_id"], request_id=req["request_id"],
        gap_type="NO_SHOW", missing_worker_ids=["C"],
    )
    table.put_item(Item=gap)
    return {"req": req, "crew": crew, "gap": gap}


def _emergency_event(event_id, replacements, *, sub=OFFICE_SUB, office_id=OFFICE_ID):
    return make_event(
        "POST", "/office/emergency/{eventId}/approve", role="OFFICE", sub=sub,
        office_id=office_id, path_params={"eventId": event_id},
        body={"replacement_member_ids": replacements},
    )


def test_emergency_replaces_no_show(table):
    from shared.db import crew_pk, worker_pk

    ctx = _setup_scenario(table)
    resp = _handler()(_emergency_event(ctx["gap"]["event_id"], ["E"]), None)
    assert resp["statusCode"] == 200
    assert body_of(resp)["data"]["status"] == "FILLED"

    # 대체 인력 E는 RUNNING + current_crew_id
    e = table.get_item(Key={"PK": worker_pk("E"), "SK": "PROFILE"})["Item"]
    assert e["state"] == "RUNNING"
    assert e["current_crew_id"] == ctx["crew"]["crew_id"]

    # 이탈자 C는 INACTIVE + current_crew_id 해제
    c = table.get_item(Key={"PK": worker_pk("C"), "SK": "PROFILE"})["Item"]
    assert c["state"] == "INACTIVE"
    assert c["current_crew_id"] is None

    # 기존 팀원 A,B는 그대로 RUNNING
    for wid in ("A", "B"):
        w = table.get_item(Key={"PK": worker_pk(wid), "SK": "PROFILE"})["Item"]
        assert w["state"] == "RUNNING"

    # Crew member_ids 갱신 (A,B,E)
    crew = table.get_item(Key={"PK": crew_pk(ctx["crew"]["crew_id"]), "SK": "META"})["Item"]
    assert set(crew["member_ids"]) == {"A", "B", "E"}
    assert crew["status"] == "RUNNING"


def test_emergency_gap_not_found(table):
    resp = _handler()(_emergency_event("no-such", ["E"]), None)
    assert resp["statusCode"] == 404
    assert body_of(resp)["error"]["code"] == "GAP_EVENT_NOT_FOUND"


def test_emergency_replacement_not_ready(table):
    ctx = _setup_scenario(table, replacement_state="INACTIVE")
    resp = _handler()(_emergency_event(ctx["gap"]["event_id"], ["E"]), None)
    assert resp["statusCode"] == 409
    assert body_of(resp)["error"]["code"] == "WORKER_NOT_READY"


def test_emergency_insufficient_coverage(table):
    # 필수 GENERAL 4명인데 새 조합은 A,B,E = 3명 → 부족
    ctx = _setup_scenario(table, required_workers=[{"trade": "GENERAL", "count": 4}])
    resp = _handler()(_emergency_event(ctx["gap"]["event_id"], ["E"]), None)
    assert resp["statusCode"] == 422
    assert body_of(resp)["error"]["code"] == "CREW_INVALID"


def test_emergency_other_office_forbidden(table):
    ctx = _setup_scenario(table)
    resp = _handler()(_emergency_event(ctx["gap"]["event_id"], ["E"], sub="office-2", office_id="OFFICE999"), None)
    assert resp["statusCode"] == 403


def test_emergency_already_filled_conflict(table):
    ctx = _setup_scenario(table)
    _handler()(_emergency_event(ctx["gap"]["event_id"], ["E"]), None)
    # 두 번째 승인 시도 → 이미 FILLED
    resp = _handler()(_emergency_event(ctx["gap"]["event_id"], ["E"]), None)
    assert resp["statusCode"] == 409
    assert body_of(resp)["error"]["code"] == "STATE_CONFLICT"
