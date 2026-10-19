"""Builds outbound.reply.requested envelopes (shared by dashboard-api, agent-worker and followup-scheduler)."""
from __future__ import annotations

import uuid
from typing import Optional

from .events import Envelope, OutboundRequestPayload, TemplateSpec, Topics
from .tenancy import ResolvedLine

_NS = uuid.UUID("c1d2e3f4-a5b6-4c7d-8e9f-0a1b2c3d4e5f")


def outbound_envelope(tenant_id: str, line: ResolvedLine, recipient_phone: str, text: str, *, requested_by: str,
                      sent_by: str = "human", request_key: Optional[str] = None, template: Optional[TemplateSpec] = None,
                      lead_id: Optional[str] = None, agent_run_id: Optional[str] = None, approval_id: Optional[str] = None,
                      followup_task_id: Optional[str] = None, customer_id: Optional[str] = None) -> Envelope:
    """request_key makes the request id deterministic so a redelivered trigger cannot send twice (dispatch dedupes on it)."""
    request_id = str(uuid.uuid5(_NS, f"{tenant_id}:{request_key}")) if request_key else str(uuid.uuid4())
    payload = OutboundRequestPayload(
        request_id=request_id, whatsapp_number_id=line.whatsapp_number_id, meta_phone_number_id=line.meta_phone_number_id,
        recipient_phone=recipient_phone, text=text, requested_by=requested_by, approved=True, sent_by=sent_by,
        template=template, lead_id=lead_id, agent_run_id=agent_run_id, approval_id=approval_id, followup_task_id=followup_task_id,
    )
    return Envelope(
        event_id=request_id if request_key else str(uuid.uuid4()),
        event_type=Topics.OUTBOUND_REQUESTED, tenant_id=tenant_id, worker_id=line.worker_id, customer_id=customer_id,
        payload=payload.model_dump(mode="json"),
    )
