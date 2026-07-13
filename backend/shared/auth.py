"""Cognito JWT claim 추출 및 역할 권한 체크 (F-A1, 공유 계약 1.7).

API Gateway의 Cognito Authorizer가 검증한 JWT claim은
event.requestContext.authorizer.claims 에 들어온다.
custom claim으로 role, office_id, company_id 를 전달한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .responses import ApiError, ErrorCode
from .state import Role


@dataclass
class Principal:
    """인증된 사용자 컨텍스트."""

    user_id: str          # cognito sub
    role: str             # WORKER / OFFICE / COMPANY
    office_id: str | None = None
    company_id: str | None = None
    username: str | None = None

    def require_role(self, *roles: str) -> None:
        """지정한 역할이 아니면 FORBIDDEN."""
        if self.role not in roles:
            raise ApiError(
                ErrorCode.FORBIDDEN,
                f"이 작업은 {', '.join(roles)} 역할만 수행할 수 있습니다.",
            )

    def require_office(self, office_id: str) -> None:
        """다른 사무소 리소스 접근 차단."""
        if self.role != Role.OFFICE or self.office_id != office_id:
            raise ApiError(ErrorCode.FORBIDDEN, "다른 사무소의 리소스에 접근할 수 없습니다.")

    def require_company(self, company_id: str) -> None:
        """다른 건설사 리소스 접근 차단."""
        if self.role != Role.COMPANY or self.company_id != company_id:
            raise ApiError(ErrorCode.FORBIDDEN, "다른 건설사의 리소스에 접근할 수 없습니다.")


def _extract_claims(event: dict[str, Any]) -> dict[str, Any]:
    ctx = event.get("requestContext") or {}
    authorizer = ctx.get("authorizer") or {}
    # REST API (Cognito Authorizer): authorizer.claims
    claims = authorizer.get("claims")
    if claims:
        return claims
    # HTTP API (JWT authorizer): authorizer.jwt.claims
    jwt = authorizer.get("jwt") or {}
    if jwt.get("claims"):
        return jwt["claims"]
    return {}


def get_principal(event: dict[str, Any]) -> Principal:
    """이벤트에서 인증 주체를 추출한다. claim이 없으면 UNAUTHORIZED."""
    claims = _extract_claims(event)
    user_id = claims.get("sub")
    if not user_id:
        raise ApiError(ErrorCode.UNAUTHORIZED, "인증 정보가 없습니다.")

    # custom claim 접두사(custom:) 및 순수 키 모두 허용
    role = claims.get("custom:role") or claims.get("role")
    if not role or role not in Role.ALL:
        raise ApiError(ErrorCode.UNAUTHORIZED, "역할 정보가 유효하지 않습니다.")

    office_id = claims.get("custom:office_id") or claims.get("office_id")
    company_id = claims.get("custom:company_id") or claims.get("company_id")
    username = claims.get("cognito:username") or claims.get("username")

    return Principal(
        user_id=user_id,
        role=role,
        office_id=office_id or None,
        company_id=company_id or None,
        username=username,
    )
