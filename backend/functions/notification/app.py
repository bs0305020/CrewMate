"""notification Lambda (F-A6).

인앱 알림 조회 (프론트 폴링 대상).

Route:
  GET /notifications        내 알림 목록 (최신순)

알림 아이템 생성은 배정/긴급배정 시 assignment Lambda 등이 build_notification 으로
직접 수행한다. P0는 DB 인앱 알림만 사용하며 SMS/푸시는 사용하지 않는다.
"""

from __future__ import annotations

from typing import Any

from shared.auth import Principal
from shared.db import query_notifications
from shared.responses import success
from shared.routing import Router
from shared.schemas import notification_view

router = Router()

_MAX_LIMIT = 100
_DEFAULT_LIMIT = 50


@router.route("GET", "/notifications")
def list_notifications(event: dict[str, Any], principal: Principal, _params: dict[str, str]):
    qp = event.get("queryStringParameters") or {}
    limit = _DEFAULT_LIMIT
    if qp.get("limit"):
        try:
            limit = max(1, min(_MAX_LIMIT, int(qp["limit"])))
        except (ValueError, TypeError):
            limit = _DEFAULT_LIMIT

    items = query_notifications(principal.user_id, limit=limit)
    unread = sum(1 for n in items if not n.get("read", False))
    return success(
        {
            "notifications": [notification_view(n) for n in items],
            "unread_count": unread,
        }
    )


def lambda_handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    return router.dispatch(event)
