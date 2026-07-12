"""notification Lambda 진입점 (F-A6).

라우트는 커밋 7에서 구현한다. 현재는 골격만 제공한다.
"""

from __future__ import annotations

from typing import Any

from shared.responses import ErrorCode, error


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    return error(ErrorCode.INTERNAL_ERROR, "notification 미구현 (커밋 7 예정)", 501)
