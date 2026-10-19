"""Webhook HMAC verification and JWT tenant auth (G1)."""
from __future__ import annotations

import hashlib
import hmac
import time
from dataclasses import dataclass
from typing import Optional

import jwt

from .tenancy import Role

_ALG = "HS256"


def verify_meta_signature(app_secret: str, raw_body: bytes, signature_header: Optional[str]) -> bool:
    if not app_secret or not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header.removeprefix("sha256="))


def sign_meta_payload(app_secret: str, raw_body: bytes) -> str:
    return "sha256=" + hmac.new(app_secret.encode(), raw_body, hashlib.sha256).hexdigest()


@dataclass(frozen=True)
class Principal:
    subject: str
    tenant_id: str
    role: Role
    worker_id: Optional[str]

    @property
    def is_worker(self) -> bool:
        return self.role == Role.SALES_WORKER


class AuthError(Exception):
    pass


def issue_token(secret: str, principal: Principal, ttl_seconds: int) -> str:
    if not secret:
        raise AuthError("JWT_SECRET is not configured")
    now = int(time.time())
    return jwt.encode(
        {
            "sub": principal.subject,
            "tenant_id": principal.tenant_id,
            "role": principal.role.value,
            "worker_id": principal.worker_id,
            "iat": now,
            "exp": now + ttl_seconds,
            "iss": "zuvocloud",
        },
        secret,
        algorithm=_ALG,
    )


def decode_token(secret: str, token: str) -> Principal:
    if not secret:
        raise AuthError("JWT_SECRET is not configured")
    try:
        claims = jwt.decode(token, secret, algorithms=[_ALG], issuer="zuvocloud", options={"require": ["exp", "tenant_id", "role"]})
        return Principal(
            subject=claims["sub"],
            tenant_id=claims["tenant_id"],
            role=Role(claims["role"]),
            worker_id=claims.get("worker_id"),
        )
    except (jwt.PyJWTError, ValueError, KeyError) as exc:
        raise AuthError("invalid token") from exc
