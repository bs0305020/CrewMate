"""assignment Lambda 진입점 (F-A5, 이 PRD의 핵심).

승인 → RESERVED → RUNNING 조건부 쓰기 및 긴급 배차는 커밋 6·8에서 구현한다.
현재는 골격만 제공한다.
"""

from __future__ import annotations

from typing import Any

from shared.responses import ErrorCode, error


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    return error(ErrorCode.INTERNAL_ERROR, "assignment 미구현 (커밋 6 예정)", 501)
