"""worker_api Lambda 진입점 (F-A2).

라우트는 커밋 3에서 구현한다. 현재는 골격만 제공하여 SAM 배포가 가능하도록 한다.
"""

from __future__ import annotations

from typing import Any

from shared.responses import ErrorCode, error


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    return error(ErrorCode.INTERNAL_ERROR, "worker_api 미구현 (커밋 3 예정)", 501)
