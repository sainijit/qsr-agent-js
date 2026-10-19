"""Durable outbound delivery state (Phase 7). See docs/outbound-delivery.md.

PENDING --claim--> SENDING --Meta 2xx--> ACCEPTED --webhook failed--> FAILED
   ^                  |--not sent (retryable)--> PENDING (next_attempt_at) | FAILED retry_exhausted
   |                  |--rejected (4xx)--------> FAILED
   policy ----------> FAILED (never sent)
                      |--ambiguous / crash----> UNKNOWN --webhook (wamid/callback)--> ACCEPTED | FAILED
                                                        --operator (reconcile CLI)---> ACCEPTED | FAILED

UNKNOWN is never resent automatically: the request may have reached Meta.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_NS = uuid.UUID("5b1d7e2c-4a3f-4c6e-8d9b-0a1b2c3d4e5f")
TERMINAL = ("ACCEPTED", "FAILED", "UNKNOWN")
DELIVERY_RANK = {"sent": 1, "delivered": 2, "read": 3, "failed": 4, "deleted": 4}
COLUMNS = ("id, tenant_id, request_id, whatsapp_number_id, customer_id, recipient_phone, kind, template_name, sent_by, "
           "followup_task_id, status, attempts, last_error_category, last_error, next_attempt_at, request, wamid, "
           "delivery_status, created_at, updated_at, sending_at, accepted_at, delivered_at, read_at, failed_at, reconciled_at, "
           "reconciled_by")


def outbound_id(tenant_id: str, request_id: str) -> str:
    return str(uuid.uuid5(_NS, f"{tenant_id}:{request_id}"))


async def get(s: AsyncSession, oid: str, lock: bool = False):
    return (await s.execute(text(f"SELECT {COLUMNS} FROM outbound_messages WHERE id = :id{' FOR UPDATE' if lock else ''}"),
                            {"id": oid})).first()


async def find_for_status(s: AsyncSession, wamid: str, callback_id: Optional[str]):
    row = (await s.execute(text(f"SELECT {COLUMNS} FROM outbound_messages WHERE wamid = :w FOR UPDATE"), {"w": wamid})).first()
    if row is None and callback_id:
        try:
            uuid.UUID(callback_id)
        except ValueError:
            return None
        row = await get(s, callback_id, lock=True)
    return row


async def apply_status(s: AsyncSession, wamid: str, status: str, at: datetime, callback_id: Optional[str],
                       error: Optional[str]) -> Optional[dict]:
    """Apply one Meta status receipt (already tenant-scoped by RLS). Idempotent and monotonic.
    Returns {"row": <before>, "reconciled": bool} or None when the receipt is for a message we did not send."""
    row = await find_for_status(s, wamid, callback_id)
    if row is None:
        return None
    # SENDING: the receipt beat dispatch's own result commit - accept quietly, dispatch emits the event.
    # UNKNOWN / PENDING-after-attempt: our classification was wrong or ambiguous - this is a reconciliation.
    accept = row.status in ("UNKNOWN", "SENDING") or (row.status == "PENDING" and row.attempts > 0)
    reconciled = row.status == "UNKNOWN" or (row.status == "PENDING" and row.attempts > 0)
    sets = ["updated_at = now()"]
    params: dict = {"id": row.id, "w": wamid, "at": at}
    if row.wamid is None:
        sets.append("wamid = :w")
    if reconciled:
        sets += ["reconciled_at = now()", "reconciled_by = 'meta_webhook'", "request = NULL"]
    if accept:
        sets.append("next_attempt_at = NULL")
        if status != "failed":
            sets += ["status = 'ACCEPTED'", "accepted_at = COALESCE(accepted_at, :at)"]
    if DELIVERY_RANK[status] > DELIVERY_RANK.get(row.delivery_status or "", 0):
        sets.append("delivery_status = :st")
        params["st"] = status
        if status in ("delivered", "read"):
            sets.append("delivered_at = COALESCE(delivered_at, :at)")
        if status == "read":
            sets.append("read_at = :at")
        if status == "failed":
            sets += ["status = 'FAILED'", "last_error_category = 'meta_failed'", "last_error = :err", "failed_at = :at"]
            params["err"] = (error or "meta_failed")[:200]
    elif not accept and row.wamid is not None:
        return {"row": row, "reconciled": False, "changed": False}
    await s.execute(text(f"UPDATE outbound_messages SET {', '.join(sets)} WHERE id = :id"), params)
    return {"row": row, "reconciled": reconciled, "changed": True}


async def resolve_unknown(s: AsyncSession, oid: str, outcome: str, actor: str, wamid: Optional[str] = None,
                          note: Optional[str] = None):
    """Operator reconciliation of an UNKNOWN send after checking WhatsApp Manager / the customer's phone."""
    row = await get(s, oid, lock=True)
    if row is None:
        raise LookupError("outbound message not found")
    if row.status != "UNKNOWN":
        raise ValueError(f"only UNKNOWN can be reconciled (is {row.status})")
    if outcome == "accepted":
        await s.execute(text(
            "UPDATE outbound_messages SET status = 'ACCEPTED', accepted_at = COALESCE(accepted_at, now()), "
            "wamid = COALESCE(:w, wamid), reconciled_at = now(), reconciled_by = :a, request = NULL, updated_at = now() "
            "WHERE id = :id"), {"id": oid, "w": wamid, "a": actor[:128]})
    elif outcome == "failed":
        await s.execute(text(
            "UPDATE outbound_messages SET status = 'FAILED', last_error_category = 'manual', last_error = :n, "
            "failed_at = now(), reconciled_at = now(), reconciled_by = :a, request = NULL, updated_at = now() "
            "WHERE id = :id"), {"id": oid, "a": actor[:128], "n": (note or "operator: not delivered")[:200]})
    else:
        raise ValueError("outcome must be accepted or failed")
    return row


def sent_envelope(tenant_id: str, row, p, worker_id: Optional[str] = None, dry_run: Optional[bool] = None):
    """outbound.message.sent for a terminal row. One event id per (request, state), so a later state
    (UNKNOWN -> ACCEPTED/FAILED) is a new event while redeliveries of the same state stay idempotent."""
    from .events import Envelope, OutboundSentPayload, Topics

    ok = row.status == "ACCEPTED"
    if dry_run is None:
        dry_run = bool(row.wamid and row.wamid.startswith(("wamid.DRYRUN.", "wamid.SIM.")))
    sent = OutboundSentPayload(
        request_id=p.request_id, success=ok, dry_run=dry_run, whatsapp_number_id=p.whatsapp_number_id,
        recipient_phone=p.recipient_phone, text=p.text, error=None if ok else (row.last_error or row.last_error_category),
        wamid=row.wamid, sent_by=p.sent_by, lead_id=p.lead_id, agent_run_id=p.agent_run_id, approval_id=p.approval_id,
        followup_task_id=p.followup_task_id, state=row.status, error_category=None if ok else row.last_error_category,
        outbound_id=str(row.id),
    )
    return Envelope(
        event_id=str(uuid.uuid5(_NS, f"sent:{tenant_id}:{p.request_id}:{row.status}")),
        event_type=Topics.OUTBOUND_SENT, tenant_id=tenant_id, worker_id=worker_id,
        customer_id=str(row.customer_id) if row.customer_id else None, wamid=row.wamid, payload=sent.model_dump())
