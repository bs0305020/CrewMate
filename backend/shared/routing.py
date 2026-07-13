"""경량 라우터 및 핸들러 래퍼.

각 Lambda는 (METHOD, path_template) -> handler(event, principal, path_params) 를
등록하고, dispatch()가 매칭·인증·오류 변환을 담당한다.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from .auth import Principal, get_principal
from .responses import ApiError, ErrorCode, error

logger = logging.getLogger()
logger.setLevel(logging.INFO)

Handler = Callable[[dict[str, Any], Principal, dict[str, str]], dict[str, Any]]


class Router:
    def __init__(self) -> None:
        self._routes: list[tuple[str, list[str], Handler]] = []

    def add(self, method: str, path_template: str, handler: Handler) -> None:
        """path_template 예: '/company/requests/{requestId}'."""
        segments = [s for s in path_template.strip("/").split("/")]
        self._routes.append((method.upper(), segments, handler))

    def route(self, method: str, path_template: str):
        def deco(fn: Handler) -> Handler:
            self.add(method, path_template, fn)
            return fn
        return deco

    def _match(self, method: str, path: str) -> tuple[Handler, dict[str, str]] | None:
        req_segments = [s for s in path.strip("/").split("/")]
        for m, segments, handler in self._routes:
            if m != method or len(segments) != len(req_segments):
                continue
            params: dict[str, str] = {}
            ok = True
            for tmpl, actual in zip(segments, req_segments):
                if tmpl.startswith("{") and tmpl.endswith("}"):
                    params[tmpl[1:-1]] = actual
                elif tmpl != actual:
                    ok = False
                    break
            if ok:
                return handler, params
        return None

    def dispatch(self, event: dict[str, Any]) -> dict[str, Any]:
        method = (event.get("httpMethod") or "").upper()
        # API Gateway REST: resource 는 템플릿 경로, path 는 실제 경로.
        # 프록시 통합에서는 resource 에 {proxy+} 가 올 수 있으므로 path 우선 사용.
        path = event.get("path") or event.get("resource") or ""

        if method == "OPTIONS":
            from .responses import success
            return success({})

        match = self._match(method, path)
        if match is None:
            return error(ErrorCode.VALIDATION_ERROR, f"경로를 찾을 수 없습니다: {method} {path}", 404)

        handler, path_params = match
        # API Gateway가 파싱한 pathParameters 를 우선 병합
        if event.get("pathParameters"):
            path_params = {**path_params, **{k: v for k, v in event["pathParameters"].items() if v is not None}}

        try:
            principal = get_principal(event)
            return handler(event, principal, path_params)
        except ApiError as exc:
            logger.info("api_error code=%s", exc.code)
            return exc.to_response()
        except Exception:  # noqa: BLE001
            logger.exception("unhandled_error method=%s path=%s", method, path)
            return error(ErrorCode.INTERNAL_ERROR, "내부 오류가 발생했습니다.")
