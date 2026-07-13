"""worker_api Lambda (F-A2) 테스트."""

from __future__ import annotations

from conftest import body_of, make_event

WORKER_SUB = "worker-abc"

APPLICATION = {
    "name": "홍길동",
    "phone": "010-1234-5678",
    "office_id": "OFFICE001",
    "trade": "FORMWORK",
    "skill_level": 4,
    "career_years": 7,
    "age": 42,
    "region": "BUSAN_HAEUNDAE",
    "desired_daily_wage": 170000,
    "certifications": ["비계기능사"],
}


def _handler():
    from functions.worker_api.app import lambda_handler

    return lambda_handler


def _create(table):
    ev = make_event("POST", "/worker/application", role="WORKER", sub=WORKER_SUB, body=APPLICATION)
    return _handler()(ev, None)


def test_create_application_starts_inactive(table):
    resp = _create(table)
    assert resp["statusCode"] == 201
    data = body_of(resp)["data"]
    assert data["state"] == "INACTIVE"
    assert data["worker_id"] == WORKER_SUB
    # 부정 데이터 비노출 (공유 계약 §8)
    assert "no_show_count" not in data
    assert "user_id" not in data


def test_duplicate_application_rejected(table):
    _create(table)
    resp = _create(table)
    assert resp["statusCode"] == 400
    assert body_of(resp)["error"]["code"] == "VALIDATION_ERROR"


def test_missing_field_rejected(table):
    bad = {k: v for k, v in APPLICATION.items() if k != "trade"}
    ev = make_event("POST", "/worker/application", role="WORKER", sub=WORKER_SUB, body=bad)
    resp = _handler()(ev, None)
    assert resp["statusCode"] == 400
    assert body_of(resp)["error"]["code"] == "VALIDATION_ERROR"


def test_get_me(table):
    _create(table)
    ev = make_event("GET", "/worker/me", role="WORKER", sub=WORKER_SUB)
    resp = _handler()(ev, None)
    assert resp["statusCode"] == 200
    assert body_of(resp)["data"]["name"] == "홍길동"


def test_ready_transition_and_idempotent(table):
    _create(table)
    ev = make_event("POST", "/worker/state/ready", role="WORKER", sub=WORKER_SUB)
    resp = _handler()(ev, None)
    assert resp["statusCode"] == 200
    assert body_of(resp)["data"]["state"] == "READY"

    # 멱등: 다시 ready 눌러도 READY 유지
    resp2 = _handler()(ev, None)
    assert resp2["statusCode"] == 200
    assert body_of(resp2)["data"]["state"] == "READY"


def test_inactive_transition(table):
    _create(table)
    _handler()(make_event("POST", "/worker/state/ready", role="WORKER", sub=WORKER_SUB), None)
    resp = _handler()(
        make_event("POST", "/worker/state/inactive", role="WORKER", sub=WORKER_SUB), None
    )
    assert resp["statusCode"] == 200
    assert body_of(resp)["data"]["state"] == "INACTIVE"


def test_ready_without_application_not_found(table):
    ev = make_event("POST", "/worker/state/ready", role="WORKER", sub="no-such")
    resp = _handler()(ev, None)
    assert resp["statusCode"] == 404
    assert body_of(resp)["error"]["code"] == "WORKER_NOT_FOUND"


def test_wrong_role_forbidden(table):
    ev = make_event("GET", "/worker/me", role="OFFICE", sub="office-1", office_id="OFFICE001")
    resp = _handler()(ev, None)
    assert resp["statusCode"] == 403
    assert body_of(resp)["error"]["code"] == "FORBIDDEN"


def test_state_conflict_when_running(table):
    _create(table)
    # 강제로 RUNNING 상태로 바꾼 뒤 대기 시작 시도 -> STATE_CONFLICT
    from shared.db import worker_pk

    table.update_item(
        Key={"PK": worker_pk(WORKER_SUB), "SK": "PROFILE"},
        UpdateExpression="SET #s = :r",
        ExpressionAttributeNames={"#s": "state"},
        ExpressionAttributeValues={":r": "RUNNING"},
    )
    resp = _handler()(make_event("POST", "/worker/state/ready", role="WORKER", sub=WORKER_SUB), None)
    assert resp["statusCode"] == 409
    assert body_of(resp)["error"]["code"] == "STATE_CONFLICT"
