"""계약 v2 통합 테스트 (moto 8테이블).

핵심 플로우: 지원서→대기→요청→편성→승인→수락→출근→퇴근→이력/성실도,
거절→결원, 동시성 STATE_CONFLICT, 성실도 노출 규칙, Agent 편성.
"""

from __future__ import annotations

import json

OFFICE = "OFFICE001"
COMPANY = "COMPANY001"


def make_event(method, path, *, role=None, sub="user-1", body=None,
               office_id=None, company_id=None, path_params=None):
    claims = {"sub": sub}
    if role:
        claims["custom:role"] = role
    if office_id:
        claims["custom:office_id"] = office_id
    if company_id:
        claims["custom:company_id"] = company_id
    return {
        "httpMethod": method,
        "path": path,
        "body": json.dumps(body) if body is not None else None,
        "pathParameters": path_params,
        "requestContext": {"authorizer": {"claims": claims}},
    }


def body_of(response):
    return json.loads(response["body"])


def _seed_worker(db, wid, *, state="READY", trade="GENERAL", wage=150000, office=OFFICE):
    from shared.schemas import build_worker
    w = build_worker(
        user_id=wid, worker_id=wid, name=f"근로자{wid}", phone="010-0000-0000",
        office_id=office, preferred_trades=[trade], excluded_trades=[],
        career_years=5, age=30, region="부산 해운대구",
        desired_daily_wage=wage, state=state,
    )
    db.put_worker(w)
    return w


def _seed_request(db, rid="REQ1", *, trade="GENERAL", count=2, budget=400000, status="REQUESTED"):
    from shared.schemas import build_request
    r = build_request(
        company_id=COMPANY, office_id=OFFICE, site_name="현장", work_date="2026-08-01",
        start_time="07:00", location_text="부산", required_workers=[{"trade": trade, "count": count}],
        budget=budget, request_id=rid, status=status,
    )
    db.put_request(r)
    return r


def _call(module_path, event):
    import importlib
    mod = importlib.import_module(module_path)
    return mod.lambda_handler(event, None)


# ---------------------------------------------------------------------------
def test_worker_application_and_ready(tables):
    ev = make_event("POST", "/worker/application", role="WORKER", sub="w1", body={
        "name": "홍길동", "phone": "010-1111-2222", "office_id": OFFICE,
        "preferred_trades": ["GENERAL"], "excluded_trades": ["REBAR"],
        "career_years": 4, "age": 33, "region": "부산 해운대구",
        "desired_daily_wage": 160000,
        "certifications": ["철근기능사"],
        "abilities": ["철근가공 조립검사"],
        "introduction": "철근 현장 경험이 있습니다.",
    })
    resp = _call("functions.worker_api.app", ev)
    assert resp["statusCode"] == 201
    data = body_of(resp)["data"]
    assert data["state"] == "INACTIVE"
    # 본인 응답: 작업 실적과 지원서 원문을 노출한다.
    assert data["completed_count"] == 0
    assert data["dispatched_count"] == 0
    assert "no_show_count" not in data
    assert data["preferred_trades"] == ["GENERAL"]
    assert data["abilities"] == ["철근가공 조립검사"]
    assert data["introduction"] == "철근 현장 경험이 있습니다."

    ready = _call("functions.worker_api.app", make_event("POST", "/worker/state/ready", role="WORKER", sub="w1"))
    assert body_of(ready)["data"]["state"] == "READY"


def test_full_cycle_manual_approve_accept_checkin_checkout(tables):
    from shared.schemas import build_company

    tables.put_company(build_company(company_id=COMPANY, name="해운대건설"))
    _seed_worker(tables, "w1")
    _seed_worker(tables, "w2")
    _seed_request(tables, "REQ1", count=2)

    # 수동 편성 (OFFICE)
    manual = _call("functions.office_core.app", make_event(
        "POST", "/office/crews/manual", role="OFFICE", office_id=OFFICE,
        body={"request_id": "REQ1", "members": [
            {"worker_id": "w1", "assigned_trade": "GENERAL", "offered_wage": 150000},
            {"worker_id": "w2", "assigned_trade": "GENERAL", "offered_wage": 150000},
        ]}))
    crew = body_of(manual)["data"]
    crew_id = crew["crew_id"]
    assert crew["status"] == "DRAFT"
    assert len(crew["members"]) == 2
    assert crew["members"][0]["assigned_trade"] == "GENERAL"

    # 승인 (트랜잭션 1): 전원 NOTIFIED
    approve = _call("functions.assignment.app", make_event(
        "POST", f"/office/crews/{crew_id}/approve", role="OFFICE", office_id=OFFICE,
        path_params={"crewId": crew_id}))
    assert body_of(approve)["data"]["status"] == "NOTIFIED"
    assert tables.get_worker("w1")["state"] == "NOTIFIED"
    assert tables.get_worker("w1")["current_offer"]["crew_id"] == crew_id

    # 수락 (트랜잭션 2)
    for w in ("w1", "w2"):
        acc = _call("functions.worker_api.app", make_event("POST", "/worker/offer/accept", role="WORKER", sub=w))
        assert body_of(acc)["data"]["state"] == "RESERVED"
    # 전원 수락 → DISPATCHED
    assert tables.get_request("REQ1")["status"] == "DISPATCHED"
    assert tables.get_worker("w1")["dispatched_count"] == 1

    assignment_detail = _call(
        "functions.worker_api.app",
        make_event("GET", "/worker/assignments", role="WORKER", sub="w1"),
    )
    current_job = body_of(assignment_detail)["data"][0]
    assert current_job["assigned_trade"] == "GENERAL"
    assert current_job["offered_wage"] == 150000
    assert current_job["required_workers"] == [{"trade": "GENERAL", "count": 2}]

    # 출근 (트랜잭션 4)
    for w in ("w1", "w2"):
        _call("functions.company_request.app", make_event(
            "POST", f"/company/crews/{crew_id}/checkin/{w}", role="COMPANY", company_id=COMPANY,
            path_params={"crewId": crew_id, "workerId": w}))
    assert tables.get_request("REQ1")["status"] == "RUNNING"

    # 퇴근 (트랜잭션 5)
    for w in ("w1", "w2"):
        _call("functions.company_request.app", make_event(
            "POST", f"/company/crews/{crew_id}/checkout/{w}", role="COMPANY", company_id=COMPANY,
            path_params={"crewId": crew_id, "workerId": w}, body={"rating": 5}))
    assert tables.get_request("REQ1")["status"] == "COMPLETED"
    w1 = tables.get_worker("w1")
    assert w1["state"] == "INACTIVE"
    assert w1["completed_count"] == 1
    assert int(w1.get("rating_count", 0)) == 0

    # 이력 조회
    hist = _call("functions.worker_api.app", make_event("GET", "/worker/history", role="WORKER", sub="w1"))
    entries = body_of(hist)["data"]
    assert len(entries) == 1 and entries[0]["assigned_trade"] == "GENERAL"
    assert entries[0]["company_name"] == "해운대건설"
    assert entries[0]["start_time"] == "07:00"
    assert entries[0]["location_text"] == "부산"
    assert entries[0]["required_workers"] == [{"trade": "GENERAL", "count": 2}]


def test_decline_creates_gap_and_returns_ready(tables):
    _seed_worker(tables, "w1")
    _seed_worker(tables, "w2")
    _seed_request(tables, "REQ1", count=2)
    manual = _call("functions.office_core.app", make_event(
        "POST", "/office/crews/manual", role="OFFICE", office_id=OFFICE,
        body={"request_id": "REQ1", "members": [
            {"worker_id": "w1", "assigned_trade": "GENERAL", "offered_wage": 150000},
            {"worker_id": "w2", "assigned_trade": "GENERAL", "offered_wage": 150000},
        ]}))
    crew_id = body_of(manual)["data"]["crew_id"]
    _call("functions.assignment.app", make_event(
        "POST", f"/office/crews/{crew_id}/approve", role="OFFICE", office_id=OFFICE,
        path_params={"crewId": crew_id}))

    dec = _call("functions.worker_api.app", make_event("POST", "/worker/offer/decline", role="WORKER", sub="w1"))
    assert body_of(dec)["data"]["state"] == "READY"  # PROMPT §2 txn3
    req = tables.get_request("REQ1")
    assert req["status"] == "COMPOSING"
    assert "w1" in req["declined_worker_ids"]
    # DECLINED 유형 GapEvent 생성
    gaps = tables.query_office_gap_events(OFFICE)
    assert any(g["type"] == "DECLINED" for g in gaps)

    # M2: 사무소 상세의 crew.members 에 거절 멤버가 acceptance=DECLINED 로 남아야
    #     프론트가 부분 재편성 UI를 판단할 수 있다.
    detail = _call("functions.office_core.app", make_event(
        "GET", "/office/requests/REQ1", role="OFFICE", office_id=OFFICE, path_params={"requestId": "REQ1"}))
    members = body_of(detail)["data"]["crew"]["members"]
    accept_by_id = {m["worker_id"]: m["acceptance"] for m in members}
    assert accept_by_id.get("w1") == "DECLINED"
    assert "w2" in accept_by_id


def test_office_can_reject_after_worker_decline_and_release_remaining_crew(tables):
    _seed_worker(tables, "w1")
    _seed_worker(tables, "w2")
    _seed_request(tables, "REQ1", count=2)
    manual = _call("functions.office_core.app", make_event(
        "POST", "/office/crews/manual", role="OFFICE", office_id=OFFICE,
        body={"request_id": "REQ1", "members": [
            {"worker_id": "w1", "assigned_trade": "GENERAL", "offered_wage": 150000},
            {"worker_id": "w2", "assigned_trade": "GENERAL", "offered_wage": 150000},
        ]}))
    crew_id = body_of(manual)["data"]["crew_id"]
    _call("functions.assignment.app", make_event(
        "POST", f"/office/crews/{crew_id}/approve", role="OFFICE", office_id=OFFICE,
        path_params={"crewId": crew_id}))
    _call("functions.worker_api.app", make_event(
        "POST", "/worker/offer/decline", role="WORKER", sub="w1"))

    rejected = _call("functions.office_core.app", make_event(
        "POST", "/office/requests/REQ1/reject", role="OFFICE", office_id=OFFICE,
        body={"reason": "대체 인력 부족"}, path_params={"requestId": "REQ1"}))

    assert rejected["statusCode"] == 200
    assert tables.get_request("REQ1")["status"] == "REJECTED"
    assert tables.get_crew(crew_id)["status"] == "CANCELLED"
    assert tables.get_worker("w2")["state"] == "READY"
    assert tables.get_worker("w2").get("current_crew_id") is None


def test_company_can_cancel_dispatched_request_and_restore_workers(tables):
    _seed_worker(tables, "w1")
    _seed_worker(tables, "w2")
    _seed_request(tables, "REQ1", count=2)
    manual = _call("functions.office_core.app", make_event(
        "POST", "/office/crews/manual", role="OFFICE", office_id=OFFICE,
        body={"request_id": "REQ1", "members": [
            {"worker_id": "w1", "assigned_trade": "GENERAL", "offered_wage": 150000},
            {"worker_id": "w2", "assigned_trade": "GENERAL", "offered_wage": 150000},
        ]}))
    crew_id = body_of(manual)["data"]["crew_id"]
    _call("functions.assignment.app", make_event(
        "POST", f"/office/crews/{crew_id}/approve", role="OFFICE", office_id=OFFICE,
        path_params={"crewId": crew_id}))
    for worker_id in ("w1", "w2"):
        _call("functions.worker_api.app", make_event(
            "POST", "/worker/offer/accept", role="WORKER", sub=worker_id))

    cancelled = _call("functions.company_request.app", make_event(
        "POST", "/company/requests/REQ1/cancel", role="COMPANY", company_id=COMPANY,
        body={"reason": "현장 일정 취소"}, path_params={"requestId": "REQ1"}))

    assert cancelled["statusCode"] == 200
    assert tables.get_request("REQ1")["status"] == "CANCELLED"
    assert tables.get_crew(crew_id)["status"] == "CANCELLED"
    for worker_id in ("w1", "w2"):
        worker = tables.get_worker(worker_id)
        assert worker["state"] == "READY"
        assert worker["dispatched_count"] == 0


def test_concurrency_double_approve_conflict(tables):
    _seed_worker(tables, "w1")
    _seed_request(tables, "REQ1", count=1)
    _seed_request(tables, "REQ2", count=1)

    def manual(rid):
        r = _call("functions.office_core.app", make_event(
            "POST", "/office/crews/manual", role="OFFICE", office_id=OFFICE,
            body={"request_id": rid, "members": [{"worker_id": "w1", "assigned_trade": "GENERAL", "offered_wage": 150000}]}))
        return body_of(r)["data"]["crew_id"]

    c1, c2 = manual("REQ1"), manual("REQ2")
    a1 = _call("functions.assignment.app", make_event(
        "POST", f"/office/crews/{c1}/approve", role="OFFICE", office_id=OFFICE, path_params={"crewId": c1}))
    assert a1["statusCode"] == 200
    # 두 번째 승인: w1이 이미 NOTIFIED(current_offer 보유) → STATE_CONFLICT
    a2 = _call("functions.assignment.app", make_event(
        "POST", f"/office/crews/{c2}/approve", role="OFFICE", office_id=OFFICE, path_params={"crewId": c2}))
    assert body_of(a2)["success"] is False
    assert body_of(a2)["error"]["code"] == "STATE_CONFLICT"


def test_integrity_exposure_office_vs_company(tables):
    _seed_worker(tables, "w1")
    tables.update_worker("w1", UpdateExpression="SET completed_count = :c, dispatched_count = :d",
                         ExpressionAttributeValues={":c": 10, ":d": 11})
    # OFFICE 응답에는 성실도 포함
    ow = _call("functions.office_core.app", make_event("GET", "/office/workers", role="OFFICE", office_id=OFFICE))
    worker = body_of(ow)["data"][0]
    assert worker["completed_count"] == 10 and worker["dispatched_count"] == 11
    # 부정 라벨 필드 부재
    assert "no_show_count" not in worker


def test_office_can_open_own_worker_application_detail_only(tables):
    from shared.schemas import build_worker

    worker = build_worker(
        user_id="w1", worker_id="w1", name="근로자", phone="010-1111-2222",
        office_id=OFFICE, preferred_trades=["GENERAL"], excluded_trades=[],
        career_years=3, age=29, region="부산", desired_daily_wage=150000,
        certifications=["철근기능사"], abilities=["시공 전 준비"], introduction="성실하게 일합니다.",
    )
    tables.put_worker(worker)

    own = _call("functions.office_core.app", make_event(
        "GET", "/office/workers/w1", role="OFFICE", office_id=OFFICE,
        path_params={"workerId": "w1"},
    ))
    detail = body_of(own)["data"]
    assert detail["phone"] == "010-1111-2222"
    assert detail["abilities"] == ["시공 전 준비"]
    assert detail["introduction"] == "성실하게 일합니다."

    other = _call("functions.office_core.app", make_event(
        "GET", "/office/workers/w1", role="OFFICE", office_id="OFFICE999",
        path_params={"workerId": "w1"},
    ))
    assert other["statusCode"] == 403


def test_company_response_hides_integrity(tables):
    _seed_worker(tables, "w1")
    _seed_request(tables, "REQ1", count=1)
    manual = _call("functions.office_core.app", make_event(
        "POST", "/office/crews/manual", role="OFFICE", office_id=OFFICE,
        body={"request_id": "REQ1", "members": [{"worker_id": "w1", "assigned_trade": "GENERAL", "offered_wage": 150000}]}))
    crew_id = body_of(manual)["data"]["crew_id"]
    _call("functions.assignment.app", make_event(
        "POST", f"/office/crews/{crew_id}/approve", role="OFFICE", office_id=OFFICE, path_params={"crewId": crew_id}))
    _call("functions.worker_api.app", make_event("POST", "/worker/offer/accept", role="WORKER", sub="w1"))

    detail = _call("functions.company_request.app", make_event(
        "GET", "/company/requests/REQ1", role="COMPANY", company_id=COMPANY, path_params={"requestId": "REQ1"}))
    crew = body_of(detail)["data"]["crew"]
    for m in crew["members"]:
        assert "completed_count" not in m and "dispatched_count" not in m and "no_show_count" not in m


def test_agent_compose_produces_recommendations(tables):
    for i in range(3):
        _seed_worker(tables, f"w{i}", wage=150000 + i * 1000)
    _seed_request(tables, "REQ1", count=2, budget=400000)
    resp = _call("functions.agent_invoke.app", make_event(
        "POST", "/office/requests/REQ1/agent-compose", role="OFFICE", office_id=OFFICE,
        path_params={"requestId": "REQ1"}))
    data = body_of(resp)["data"]
    assert data["status"] == "PROPOSED"
    assert data["source"] == "AGENT"
    assert len(data["recommendations"]) >= 1
    rec = data["recommendations"][0]
    assert len(rec["members"]) == 2
    assert all(m["assigned_trade"] == "GENERAL" for m in rec["members"])
    assert rec["total_cost"] == sum(m["offered_wage"] for m in rec["members"])
    assert tables.get_request("REQ1")["status"] == "PROPOSED"


def test_general_slot_accepts_worker_without_general_preference(tables):
    _seed_worker(tables, "form-worker", trade="FORMWORK")
    _seed_request(tables, "REQ-GENERAL", trade="GENERAL", count=1, budget=200000)

    response = _call("functions.agent_invoke.app", make_event(
        "POST", "/office/requests/REQ-GENERAL/agent-compose",
        role="OFFICE", office_id=OFFICE,
        path_params={"requestId": "REQ-GENERAL"},
    ))

    recommendation = body_of(response)["data"]["recommendations"][0]
    assert recommendation["members"][0]["worker_id"] == "form-worker"
    assert recommendation["members"][0]["assigned_trade"] == "GENERAL"


def test_agent_returns_over_budget_combination_with_edit_warning(tables):
    _seed_worker(tables, "w-expensive", wage=180000)
    _seed_request(tables, "REQ-BUDGET", count=1, budget=100000)

    response = _call("functions.agent_invoke.app", make_event(
        "POST", "/office/requests/REQ-BUDGET/agent-compose",
        role="OFFICE", office_id=OFFICE,
        path_params={"requestId": "REQ-BUDGET"},
    ))

    assert response["statusCode"] == 200
    recommendation = body_of(response)["data"]["recommendations"][0]
    assert recommendation["total_cost"] == 180000
    assert "예산 80,000원 초과" in recommendation["reason"]
    assert any("임금 수정 필요" in item for item in recommendation["considerations"])


def test_async_agent_failure_persists_user_readable_reason(tables):
    _seed_worker(tables, "w1", trade="GENERAL")
    tables.update_worker(
        "w1",
        UpdateExpression="SET excluded_trades = :excluded",
        ExpressionAttributeValues={":excluded": ["REBAR"]},
    )
    _seed_request(tables, "REQ-NO-MATCH", trade="REBAR", count=1)
    event = make_event(
        "POST", "/office/requests/REQ-NO-MATCH/agent-compose",
        role="OFFICE", office_id=OFFICE,
        path_params={"requestId": "REQ-NO-MATCH"},
    )
    event.update({
        "_crewAgentAction": "RUN",
        "_entityType": "REQUEST",
        "_entityId": "REQ-NO-MATCH",
        "_previousStatus": "REQUESTED",
    })

    response = _call("functions.agent_invoke.app", event)

    assert response["statusCode"] == 502
    request = tables.get_request("REQ-NO-MATCH")
    assert request["status"] == "REQUESTED"
    assert "철근공 1명 부족" in request["composition_error"]


def test_agent_compose_can_start_asynchronously_without_forwarding_token(tables, monkeypatch):
    import functions.agent_invoke.app as agent_app

    _seed_request(tables, "REQ1", count=1)
    invoked = []
    monkeypatch.setattr(agent_app, "ASYNC_ENABLED", True)
    monkeypatch.setattr(agent_app, "_invoke_self", lambda event, _context: invoked.append(event))
    event = make_event(
        "POST", "/office/requests/REQ1/agent-compose",
        role="OFFICE", office_id=OFFICE, path_params={"requestId": "REQ1"},
    )
    event["headers"] = {"Authorization": "Bearer secret", "X-Test": "kept"}

    response = agent_app.lambda_handler(event, None)

    assert response["statusCode"] == 202
    assert tables.get_request("REQ1")["status"] == "COMPOSING"
    assert invoked[0]["_crewAgentAction"] == "RUN"
    assert "Authorization" not in invoked[0]["headers"]
    assert invoked[0]["headers"]["X-Test"] == "kept"
