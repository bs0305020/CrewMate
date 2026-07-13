"""company_request Lambda (F-A3) 테스트."""

from __future__ import annotations

from conftest import body_of, make_event

COMPANY_SUB = "company-1"
COMPANY_ID = "COMPANY001"
OFFICE_ID = "OFFICE001"

REQUEST = {
    "office_id": OFFICE_ID,
    "site_name": "해운대 현장",
    "work_date": "2026-07-13",
    "start_time": "07:00",
    "location_text": "부산 해운대구 ...",
    "required_workers": [
        {"trade": "FORMWORK", "count": 4},
        {"trade": "GENERAL", "count": 2},
    ],
    "budget": 2000000,
    "priority": {"cost": 0.5, "skill": 0.5},
    "notes": "비용 우선",
}


def _handler():
    from functions.company_request.app import lambda_handler

    return lambda_handler


def _create(table, **overrides):
    body = {**REQUEST, **overrides}
    ev = make_event(
        "POST", "/company/requests", role="COMPANY", sub=COMPANY_SUB, company_id=COMPANY_ID, body=body
    )
    return _handler()(ev, None)


def test_create_request(table):
    resp = _create(table)
    assert resp["statusCode"] == 201
    data = body_of(resp)["data"]
    assert data["status"] == "REQUESTED"
    assert data["company_id"] == COMPANY_ID
    assert data["office_id"] == OFFICE_ID


def test_create_request_bad_trade(table):
    resp = _create(table, required_workers=[{"trade": "UNKNOWN", "count": 1}])
    assert resp["statusCode"] == 400
    assert body_of(resp)["error"]["code"] == "VALIDATION_ERROR"


def test_create_request_empty_workers(table):
    resp = _create(table, required_workers=[])
    assert resp["statusCode"] == 400


def test_list_requests_only_own(table):
    _create(table)
    _create(table, site_name="사상 현장")
    ev = make_event("GET", "/company/requests", role="COMPANY", sub=COMPANY_SUB, company_id=COMPANY_ID)
    resp = _handler()(ev, None)
    assert resp["statusCode"] == 200
    assert len(body_of(resp)["data"]["requests"]) == 2


def test_get_request_detail(table):
    rid = body_of(_create(table))["data"]["request_id"]
    ev = make_event(
        "GET", "/company/requests/{requestId}", role="COMPANY", sub=COMPANY_SUB,
        company_id=COMPANY_ID, path_params={"requestId": rid},
    )
    resp = _handler()(ev, None)
    assert resp["statusCode"] == 200
    assert body_of(resp)["data"]["request_id"] == rid


def test_get_other_company_request_forbidden(table):
    rid = body_of(_create(table))["data"]["request_id"]
    ev = make_event(
        "GET", "/company/requests/{requestId}", role="COMPANY", sub="company-2",
        company_id="COMPANY999", path_params={"requestId": rid},
    )
    resp = _handler()(ev, None)
    assert resp["statusCode"] == 403
    assert body_of(resp)["error"]["code"] == "FORBIDDEN"


def test_update_request(table):
    rid = body_of(_create(table))["data"]["request_id"]
    ev = make_event(
        "PUT", "/company/requests/{requestId}", role="COMPANY", sub=COMPANY_SUB,
        company_id=COMPANY_ID, path_params={"requestId": rid}, body={"budget": 2500000},
    )
    resp = _handler()(ev, None)
    assert resp["statusCode"] == 200
    assert body_of(resp)["data"]["budget"] == 2500000


def test_worker_role_forbidden(table):
    ev = make_event("GET", "/company/requests", role="WORKER", sub="w1")
    resp = _handler()(ev, None)
    assert resp["statusCode"] == 403


def test_gap_event_registration(table):
    from shared.schemas import build_crew, build_request

    # 요청 + 작업조를 사전 저장
    req = build_request(
        company_id=COMPANY_ID, office_id=OFFICE_ID, site_name="s", work_date="2026-07-13",
        start_time="07:00", location_text="loc", required_workers=[{"trade": "FORMWORK", "count": 1}],
        budget=100000,
    )
    table.put_item(Item=req)
    crew = build_crew(office_id=OFFICE_ID, request_id=req["request_id"], member_ids=["W-A", "W-B", "W-C"])
    table.put_item(Item=crew)

    ev = make_event(
        "POST", "/company/crews/{crewId}/gap-events", role="COMPANY", sub=COMPANY_SUB,
        company_id=COMPANY_ID, path_params={"crewId": crew["crew_id"]},
        body={"gap_type": "NO_SHOW", "missing_worker_ids": ["W-C"]},
    )
    resp = _handler()(ev, None)
    assert resp["statusCode"] == 201
    data = body_of(resp)["data"]
    assert data["status"] == "DETECTED"
    assert data["gap_type"] == "NO_SHOW"
    assert data["missing_worker_ids"] == ["W-C"]
