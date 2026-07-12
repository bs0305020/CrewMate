"""notification Lambda (F-A6) 테스트."""

from __future__ import annotations

from conftest import body_of, make_event

USER_SUB = "worker-abc"


def _handler():
    from functions.notification.app import lambda_handler

    return lambda_handler


def _seed_notification(table, user_id, *, kind="ASSIGNED", title="배정", read=False):
    from shared.schemas import build_notification

    n = build_notification(user_id=user_id, kind=kind, title=title, message="msg")
    if read:
        n["read"] = True
    table.put_item(Item=n)
    return n


def test_list_empty(table):
    ev = make_event("GET", "/notifications", role="WORKER", sub=USER_SUB)
    resp = _handler()(ev, None)
    assert resp["statusCode"] == 200
    data = body_of(resp)["data"]
    assert data["notifications"] == []
    assert data["unread_count"] == 0


def test_list_returns_own_notifications(table):
    _seed_notification(table, USER_SUB, title="배정1")
    _seed_notification(table, USER_SUB, title="배정2", read=True)
    _seed_notification(table, "other-user", title="남의알림")

    ev = make_event("GET", "/notifications", role="WORKER", sub=USER_SUB)
    resp = _handler()(ev, None)
    data = body_of(resp)["data"]
    assert len(data["notifications"]) == 2
    assert data["unread_count"] == 1


def test_notification_view_hides_keys(table):
    _seed_notification(table, USER_SUB)
    ev = make_event("GET", "/notifications", role="WORKER", sub=USER_SUB)
    resp = _handler()(ev, None)
    noti = body_of(resp)["data"]["notifications"][0]
    assert "PK" not in noti and "SK" not in noti
    assert noti["kind"] == "ASSIGNED"


def test_unauthenticated_rejected(table):
    ev = {"httpMethod": "GET", "path": "/notifications", "requestContext": {"authorizer": {"claims": {}}}}
    resp = _handler()(ev, None)
    assert resp["statusCode"] == 401
