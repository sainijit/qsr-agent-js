"""Event contracts shared by every service (schema_version 1)."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = 1


class Topics:
    INBOUND_RECEIVED = "inbound.message.received"
    MESSAGE_PERSISTED = "message.persisted"
    INTENT_EXTRACTED = "ai.intent.extracted"
    REVIEW_FLAGGED = "ai.review.flagged"
    OUTBOUND_REQUESTED = "outbound.reply.requested"
    OUTBOUND_SENT = "outbound.message.sent"
    MESSAGE_STATUS = "message.status"
    LEAD_UPDATED = "lead.updated"
    VISIT_PLANNED = "visit.planned"
    FOLLOWUP_DUE = "followup.due"
    AGENT_REPLY_PROPOSED = "agent.reply.proposed"
    APPROVAL_REQUESTED = "approval.requested"
    APPROVAL_DECIDED = "approval.decided"

    ALL = (
        INBOUND_RECEIVED,
        MESSAGE_PERSISTED,
        INTENT_EXTRACTED,
        REVIEW_FLAGGED,
        OUTBOUND_REQUESTED,
        OUTBOUND_SENT,
        MESSAGE_STATUS,
        LEAD_UPDATED,
        VISIT_PLANNED,
        FOLLOWUP_DUE,
        AGENT_REPLY_PROPOSED,
        APPROVAL_REQUESTED,
        APPROVAL_DECIDED,
    )

    @staticmethod
    def dlq(topic: str) -> str:
        return f"{topic}.DLQ"


class Headers:
    TENANT_ID = "tenant_id"
    WORKER_ID = "worker_id"
    EVENT_ID = "event_id"
    EVENT_TYPE = "event_type"
    SCHEMA_VERSION = "schema_version"
    ERROR = "error"
    ORIGINAL_TOPIC = "original_topic"
    # DLQ metadata (added by ConsumerContract; stripped again by the replay tool)
    DLQ_GROUP = "dlq_group"
    DLQ_ERROR_CLASS = "dlq_error_class"
    DLQ_ATTEMPTS = "dlq_attempts"
    DLQ_FAILED_AT = "dlq_failed_at"
    DLQ_PARTITION = "dlq_original_partition"
    DLQ_OFFSET = "dlq_original_offset"
    REPLAYED_FROM = "replayed_from"
    REPLAY_FOR_GROUP = "replay_for_group"  # only this consumer group processes a replayed event
    DLQ_HEADERS = (ERROR, ORIGINAL_TOPIC, DLQ_GROUP, DLQ_ERROR_CLASS, DLQ_ATTEMPTS, DLQ_FAILED_AT, DLQ_PARTITION, DLQ_OFFSET)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Envelope(BaseModel):
    """Canonical event envelope (spec §4). `payload` is stage-specific."""

    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    event_type: str
    schema_version: int = SCHEMA_VERSION
    tenant_id: str
    worker_id: Optional[str] = None
    customer_id: Optional[str] = None
    wamid: Optional[str] = None
    occurred_at: datetime = Field(default_factory=_now)
    payload: dict[str, Any] = Field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.tenant_id}:{self.worker_id or '-'}"

    def headers(self) -> list[tuple[str, bytes]]:
        h = {
            Headers.TENANT_ID: self.tenant_id,
            Headers.EVENT_ID: self.event_id,
            Headers.EVENT_TYPE: self.event_type,
            Headers.SCHEMA_VERSION: str(self.schema_version),
        }
        if self.worker_id:
            h[Headers.WORKER_ID] = self.worker_id
        return [(k, v.encode()) for k, v in h.items()]


# ---------------------------------------------------------------- payloads


class InboundMessagePayload(BaseModel):
    """Payload of inbound.message.received."""

    worker_key: str
    worker_role: str
    whatsapp_number_id: str
    meta_phone_number_id: str
    display_phone_number: Optional[str] = None
    customer_phone: str
    customer_name: Optional[str] = None
    message_type: str
    text: Optional[str] = None
    media_id: Optional[str] = None
    media_mime_type: Optional[str] = None
    meta_timestamp: Optional[str] = None
    tenant_policy: Optional[str] = None
    referral: Optional[dict[str, Any]] = None


class Intent(str, Enum):
    CREATE_ORDER = "CREATE_ORDER"
    INQUIRY_PRICE_STOCK = "INQUIRY_PRICE_STOCK"
    PAYMENT_COMMITMENT = "PAYMENT_COMMITMENT"
    COMPLAINT_RETURN = "COMPLAINT_RETURN"
    GENERAL_CHAT = "GENERAL_CHAT"
    VISIT_PLAN = "VISIT_PLAN"
    FAQ = "FAQ"


class LineItem(BaseModel):
    sku_name: str
    quantity: Optional[float] = None
    unit: Optional[str] = None
    requested_rate: Optional[float] = None


class LeadFields(BaseModel):
    customer_name: Optional[str] = None
    interest: Optional[str] = None
    budget_min: Optional[float] = None
    budget_max: Optional[float] = None
    timeline: Optional[str] = None
    location: Optional[str] = None


class VisitIntent(BaseModel):
    """A customer's stated plan to visit the shop/showroom."""

    text: str
    date_expr: Optional[str] = None
    resolved_date: Optional[str] = None  # YYYY-MM-DD in the tenant timezone
    time_window: Optional[str] = None
    is_reschedule: bool = False
    is_cancel: bool = False
    confidence: float = Field(0.0, ge=0.0, le=1.0)


class PriceQuery(BaseModel):
    item_text: str
    attributes: list[str] = Field(default_factory=list)
    is_direct: bool = True
    discount_requested: bool = False


class Extraction(BaseModel):
    """NER contract (G3). Also used as the Gemini response_schema."""

    intent: Intent
    confidence: float = Field(ge=0.0, le=1.0)
    lang: Optional[str] = None
    customer_account: Optional[str] = None
    line_items: list[LineItem] = Field(default_factory=list)
    urgency: Optional[str] = None
    payment_reference: Optional[str] = None
    requested_discount_pct: Optional[float] = None
    lead: Optional[LeadFields] = None
    visit: Optional[VisitIntent] = None
    price_query: Optional[PriceQuery] = None
    question: Optional[str] = None
    opt_out: bool = False


class ExtractionPayload(BaseModel):
    """Payload of ai.intent.extracted / ai.review.flagged."""

    message_id: str
    extraction: Optional[Extraction] = None
    tier: Literal["tier0", "tier2", "none"]
    model: Optional[str] = None
    reason: Optional[Literal["low_confidence", "ai_unavailable", "unsupported_media", "intent_disabled"]] = None
    latency_ms: Optional[int] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None


class OutboundRequestPayload(BaseModel):
    """Payload of outbound.reply.requested."""

    request_id: str
    whatsapp_number_id: str
    meta_phone_number_id: str
    recipient_phone: str
    text: str
    requested_by: str
    approved: bool = True
    sent_by: Literal["human", "agent", "followup", "system"] = "human"
    template: Optional["TemplateSpec"] = None
    lead_id: Optional[str] = None
    agent_run_id: Optional[str] = None
    approval_id: Optional[str] = None
    followup_task_id: Optional[str] = None


class TemplateSpec(BaseModel):
    name: str
    language: str = "en_US"
    body_params: list[str] = Field(default_factory=list)


class OutboundSentPayload(BaseModel):
    request_id: str
    success: bool
    dry_run: bool
    whatsapp_number_id: str
    recipient_phone: str
    text: str
    error: Optional[str] = None
    wamid: Optional[str] = None
    sent_by: str = "human"
    lead_id: Optional[str] = None
    agent_run_id: Optional[str] = None
    approval_id: Optional[str] = None
    followup_task_id: Optional[str] = None
    # Phase 7: durable delivery state. UNKNOWN = the request may have reached Meta; never resend automatically.
    state: Optional[Literal["ACCEPTED", "FAILED", "UNKNOWN"]] = None
    error_category: Optional[str] = None
    outbound_id: Optional[str] = None


class MessageStatusPayload(BaseModel):
    """Payload of message.status (Meta delivery receipts)."""

    wamid: str
    status: Literal["sent", "delivered", "read", "failed", "deleted"]
    timestamp: Optional[str] = None
    recipient_phone: Optional[str] = None
    error: Optional[str] = None
    callback_data: Optional[str] = None  # biz_opaque_callback_data echoed by Meta (= outbound_messages.id)


class FollowupDuePayload(BaseModel):
    task_id: str
    lead_id: str
    rule_id: str


class ApprovalDecidedPayload(BaseModel):
    approval_id: str
    decision: Literal["APPROVED", "EDITED", "REJECTED"]
    reviewer: str


OutboundRequestPayload.model_rebuild()
