"""API Gateway 표준 응답 포맷터 (공유 계약 1.6).

성공: { "success": true, "data": {} }
실패: { "success": false, "error": { "code": "...", "message": "..." } }

모든 코어 Lambda는 이 모듈의 success()/error() 로 응답을 생성한다.
"""

from __future__ import annotations

import decimal
import json
from typing import Any


# ---------------------------------------------------------------------------
# 오류 코드 (공유 계약 1.6) — 임의 추가 금지
# ---------------------------------------------------------------------------
class ErrorCode:
    UNAUTHORIZED = "UNAUTHORIZED"
    FORBIDDEN = "FORBIDDEN"
    WORKER_NOT_FOUND = "WORKER_NOT_FOUND"
    WORKER_NOT_READY = "WORKER_NOT_READY"
    WORKER_ALREADY_RUNNING = "WORKER_ALREADY_RUNNING"
    REQUEST_NOT_FOUND = "REQUEST_NOT_FOUND"
    REQUEST_ALREADY_ASSIGNED = "REQUEST_ALREADY_ASSIGNED"
    CREW_INVALID = "CREW_INVALID"
    AGENT_OUTPUT_INVALID = "AGENT_OUTPUT_INVALID"
    AGENT_RETRY_FAILED = "AGENT_RETRY_FAILED"
    STATE_CONFLICT = "STATE_CONFLICT"
    GAP_EVENT_NOT_FOUND = "GAP_EVENT_NOT_FOUND"
    # 입력 검증용 일반 오류 (계약 목록 외 내부용)
    VALIDATION_ERROR = "VALIDATION_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"


# HTTP 상태 코드 매핑
_STATUS_BY_CODE: dict[str, int] = {
    ErrorCode.UNAUTHORIZED: 401,
    ErrorCode.FORBIDDEN: 403,
    ErrorCode.WORKER_NOT_FOUND: 404,
    ErrorCode.REQUEST_NOT_FOUND: 404,
    ErrorCode.GAP_EVENT_NOT_FOUND: 404,
    ErrorCode.WORKER_NOT_READY: 409,
    ErrorCode.WORKER_ALREADY_RUNNING: 409,
    ErrorCode.REQUEST_ALREADY_ASSIGNED: 409,
    ErrorCode.STATE_CONFLICT: 409,
    ErrorCode.CREW_INVALID: 422,
    ErrorCode.AGENT_OUTPUT_INVALID: 422,
    ErrorCode.AGENT_RETRY_FAILED: 502,
    ErrorCode.VALIDATION_ERROR: 400,
    ErrorCode.INTERNAL_ERROR: 500,
}

_CORS_HEADERS = {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type,Authorization",
    "Access-Control-Allow-Methods": "GET,POST,PUT,DELETE,OPTIONS",
}


class _DecimalEncoder(json.JSONEncoder):
    """DynamoDB가 반환하는 Decimal을 JSON 숫자로 직렬화한다."""

    def default(self, o: Any) -> Any:  # noqa: D102
        if isinstance(o, decimal.Decimal):
            # 정수면 int, 아니면 float
            if o % 1 == 0:
                return int(o)
            return float(o)
        return super().default(o)


def _build(status_code: int, body: dict[str, Any]) -> dict[str, Any]:
    return {
        "statusCode": status_code,
        "headers": _CORS_HEADERS,
        "body": json.dumps(body, cls=_DecimalEncoder, ensure_ascii=False),
    }


def success(data: Any = None, status_code: int = 200) -> dict[str, Any]:
    """성공 응답을 생성한다."""
    return _build(status_code, {"success": True, "data": data if data is not None else {}})


def error(code: str, message: str, status_code: int | None = None) -> dict[str, Any]:
    """실패 응답을 생성한다."""
    resolved = status_code if status_code is not None else _STATUS_BY_CODE.get(code, 400)
    return _build(resolved, {"success": False, "error": {"code": code, "message": message}})


class ApiError(Exception):
    """핸들러에서 던지면 error() 응답으로 변환되는 예외."""

    def __init__(self, code: str, message: str, status_code: int | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code

    def to_response(self) -> dict[str, Any]:
        return error(self.code, self.message, self.status_code)
