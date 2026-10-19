"""FastAPI helpers shared by HTTP services (import only where fastapi is installed)."""
from __future__ import annotations

from typing import Optional

from fastapi import Depends, HTTPException, Query, Request, status

from .security import AuthError, Principal, decode_token
from .settings import get_settings


def _extract_token(request: Request, access_token: Optional[str]) -> str:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    if access_token:  # EventSource cannot send headers; SSE passes the token as a query param
        return access_token
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")


def current_principal(
    request: Request, access_token: Optional[str] = Query(None, include_in_schema=False)
) -> Principal:
    try:
        return decode_token(get_settings().jwt_secret, _extract_token(request, access_token))
    except AuthError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token")


def enforce_tenant(principal: Principal, requested_tenant_id: Optional[str]) -> str:
    """G1: a caller may only address its own tenant; anything else is 403."""
    if requested_tenant_id and requested_tenant_id != principal.tenant_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "cross-tenant access denied")
    return principal.tenant_id


PrincipalDep = Depends(current_principal)
