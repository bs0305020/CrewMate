"""office_core Lambda (F-A4) 테스트."""

from __future__ import annotations

from conftest import body_of, make_event

OFFICE_SUB = "office-1"
OFFICE_ID = "OFFICE001"


def _handler():
    from functions.office_core.app import lambda_handler

    return lambda_handler


def _seed_worker(table, worker_id, *, trade="FORMWORK", state="READY", skill=4, wage=170000,
                 region="BUSAN_HAEUNDAE", office_id=OFFICE_ID):
    from shared.schemas import build_worker

    w = build_worker(
        user_id=worker_id, worker_id=worker_id, name=f"근로자{worker_id}", phone="010-0000-0000",
        office_id=office_id, trade=trade, skill_level=skill, career_years=5, age=40,
        region=region, desired_daily_wage=wage, state=state,
    )
    table.put_item(Item=w)
    return w


def _seed_request(table, *, required_workers, office_id=OFFICE_ID, company_id="COMPANY001"):
    from shared.schemas import build_request

    r = build_request(
        company_id=company_id, office_id=office_id, site_name="현장", work_date="2026-07-13",
        start_time="07:00", location_text="loc", required_workers=required_workers, budget=1000000,
    )
    table.put_item(Item=r)
    return r


def _office_event(method, path, **kw):
    return make_event(method, path, role="OFFICE", sub=OFFICE_SUB, office_id=OFFICE_ID, **kw)


def test_list_ready_candidates(table):
    _seed_worker(table, "W1", state="READY")
    _seed_worker(table, "W2", state="READY")
    _seed_worker(table, "W3", state="INACTIVE")
    resp = _handler()(_office_event("GET", "/office/workers", path_params={}) | {"queryStringParameters": {"state": "READY"}}, None)
    assert resp["statusCode"] == 200
    workers = body_of(resp)["data"]["workers"]
    assert len(workers) == 2
    # OFFICE 뷰는 내부 데이터 포함
    assert "no_show_count" in workers[0]


def test_list_workers_filter_trade(table):
    _seed_worker(table, "W1", trade="FORMWORK")
    _seed_worker(table, "W2", trade="REBAR")
    ev = _office_event("GET", "/office/workers") | {"queryStringParameters": {"trade": "REBAR"}}
    resp = _handler()(ev, None)
    workers = body_of(resp)["data"]["workers"]
    assert len(workers) == 1
    assert workers[0]["trade"] == "REBAR"


def test_list_workers_wage_range(table):
    _seed_worker(table, "W1", wage=150000)
    _seed_worker(table, "W2", wage=200000)
    ev = _office_event("GET", "/office/workers") | {"queryStringParameters": {"max_wage": "180000"}}
    resp = _handler()(ev, None)
    workers = body_of(resp)["data"]["workers"]
    assert len(workers) == 1
    assert workers[0]["worker_id"] == "W1"


def test_manual_crew_creation(table):
    _seed_worker(table, "W1", trade="FORMWORK")
    _seed_worker(table, "W2", trade="GENERAL")
    req = _seed_request(table, required_workers=[
        {"trade": "FORMWORK", "count": 1}, {"trade": "GENERAL", "count": 1},
    ])
    ev = _office_event("POST", "/office/crews/manual", body={"request_id": req["request_id"], "member_ids": ["W1", "W2"]})
    resp = _handler()(ev, None)
    assert resp["statusCode"] == 201
    data = body_of(resp)["data"]
    assert data["status"] == "DRAFT"
    assert data["source"] == "MANUAL"
    assert set(data["member_ids"]) == {"W1", "W2"}


def test_manual_crew_duplicate_member(table):
    _seed_worker(table, "W1")
    req = _seed_request(table, required_workers=[{"trade": "FORMWORK", "count": 1}])
    ev = _office_event("POST", "/office/crews/manual", body={"request_id": req["request_id"], "member_ids": ["W1", "W1"]})
    resp = _handler()(ev, None)
    assert resp["statusCode"] == 422
    assert body_of(resp)["error"]["code"] == "CREW_INVALID"


def test_manual_crew_not_ready_member(table):
    _seed_worker(table, "W1", state="INACTIVE")
    req = _seed_request(table, required_workers=[{"trade": "FORMWORK", "count": 1}])
    ev = _office_event("POST", "/office/crews/manual", body={"request_id": req["request_id"], "member_ids": ["W1"]})
    resp = _handler()(ev, None)
    assert resp["statusCode"] == 409
    assert body_of(resp)["error"]["code"] == "WORKER_NOT_READY"


def test_manual_crew_insufficient_coverage(table):
    _seed_worker(table, "W1", trade="FORMWORK")
    req = _seed_request(table, required_workers=[{"trade": "FORMWORK", "count": 2}])
    ev = _office_event("POST", "/office/crews/manual", body={"request_id": req["request_id"], "member_ids": ["W1"]})
    resp = _handler()(ev, None)
    assert resp["statusCode"] == 422
    assert body_of(resp)["error"]["code"] == "CREW_INVALID"


def test_other_office_worker_rejected(table):
    _seed_worker(table, "W1", office_id="OFFICE999")
    req = _seed_request(table, required_workers=[{"trade": "FORMWORK", "count": 1}])
    ev = _office_event("POST", "/office/crews/manual", body={"request_id": req["request_id"], "member_ids": ["W1"]})
    resp = _handler()(ev, None)
    # 후보 검증에서 다른 사무소 근로자 거부
    assert resp["statusCode"] == 422
    assert body_of(resp)["error"]["code"] == "CREW_INVALID"


def test_worker_role_forbidden(table):
    ev = make_event("GET", "/office/workers", role="WORKER", sub="w1")
    resp = _handler()(ev, None)
    assert resp["statusCode"] == 403
