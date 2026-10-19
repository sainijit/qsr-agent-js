"""Append-only audit trail (G7). Details are PII-masked before they are stored."""
from __future__ import annotations

import uuid
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from .logging import mask_phone, mask_text
from .models import AuditLog

_PII_KEYS = {"phone", "customer_phone", "recipient_phone", "sender_phone", "from", "to"}
_TEXT_KEYS = {"text", "body", "message", "content"}


def mask_details(value: Any, key: str = "") -> Any:
    if isinstance(value, dict):
        return {k: mask_details(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [mask_details(v, key) for v in value]
    if isinstance(value, str):
        if key in _PII_KEYS:
            return mask_phone(value)
        if key in _TEXT_KEYS:
            return f"<{len(value)} chars>"
        return mask_text(value)
    return value


async def write_audit(
    session: AsyncSession,
    tenant_id: str,
    event_type: str,
    actor: str,
    entity_type: Optional[str] = None,
    entity_id: Optional[str] = None,
    details: Optional[dict[str, Any]] = None,
) -> None:
    session.add(
        AuditLog(
            id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            event_type=event_type,
            actor=actor,
            entity_type=entity_type,
            entity_id=entity_id,
            details=mask_details(details or {}),
        )
    )
