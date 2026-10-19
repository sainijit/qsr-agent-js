"""SQLAlchemy models. Every tenant table carries tenant_id and is protected by RLS
(see migrations/versions/0001_initial.py). Services connect as the non-owner `zuvo_app` role."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from datetime import date as Date

from sqlalchemy import (
    ARRAY,
    Boolean,
    Date as SADate,
    DateTime,
    Integer,
    Float,
    ForeignKey,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


UUIDStr = UUID(as_uuid=False)


class Base(DeclarativeBase):
    pass


class Tenant(Base):
    __tablename__ = "tenants"
    id: Mapped[str] = mapped_column(UUIDStr, primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(255))
    city: Mapped[Optional[str]] = mapped_column(String(100))
    currency: Mapped[str] = mapped_column(String(8), default="INR")
    industry: Mapped[Optional[str]] = mapped_column(String(64))
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Kolkata")
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE")
    plan: Mapped[Optional[str]] = mapped_column(String(32))
    monthly_ai_budget_inr: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("500"))
    onboarded_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Worker(Base):
    __tablename__ = "workers"
    __table_args__ = (UniqueConstraint("tenant_id", "worker_key"),)
    id: Mapped[str] = mapped_column(UUIDStr, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(UUIDStr, ForeignKey("tenants.id"), index=True)
    worker_key: Mapped[str] = mapped_column(String(100))
    display_name: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(32))
    whatsapp_phone: Mapped[Optional[str]] = mapped_column(String(20))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WhatsAppNumber(Base):
    __tablename__ = "whatsapp_numbers"
    id: Mapped[str] = mapped_column(UUIDStr, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(UUIDStr, ForeignKey("tenants.id"), index=True)
    worker_id: Mapped[str] = mapped_column(UUIDStr, ForeignKey("workers.id"))
    meta_phone_number_id: Mapped[str] = mapped_column(String(64))
    display_phone_number: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE")
    waba_id: Mapped[Optional[str]] = mapped_column(String(64))
    label: Mapped[Optional[str]] = mapped_column(String(100))
    purpose: Mapped[Optional[str]] = mapped_column(String(16))


class Customer(Base):
    __tablename__ = "customers"
    __table_args__ = (UniqueConstraint("tenant_id", "phone"),)
    id: Mapped[str] = mapped_column(UUIDStr, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(UUIDStr, ForeignKey("tenants.id"), index=True)
    phone: Mapped[str] = mapped_column(String(20))
    display_name: Mapped[Optional[str]] = mapped_column(String(255))
    account_name: Mapped[Optional[str]] = mapped_column(String(255))
    credit_limit: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("50000"))
    outstanding: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"))
    is_blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    source: Mapped[Optional[str]] = mapped_column(String(32))
    language: Mapped[Optional[str]] = mapped_column(String(20))
    tags: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    opted_out: Mapped[bool] = mapped_column(Boolean, default=False)
    opted_out_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    consent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (UniqueConstraint("tenant_id", "whatsapp_number_id", "customer_id"),)
    id: Mapped[str] = mapped_column(UUIDStr, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(UUIDStr, ForeignKey("tenants.id"), index=True)
    whatsapp_number_id: Mapped[str] = mapped_column(UUIDStr, ForeignKey("whatsapp_numbers.id"))
    worker_id: Mapped[str] = mapped_column(UUIDStr, ForeignKey("workers.id"))
    customer_id: Mapped[str] = mapped_column(UUIDStr, ForeignKey("customers.id"))
    last_message_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_inbound_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (UniqueConstraint("tenant_id", "wamid"),)
    id: Mapped[str] = mapped_column(UUIDStr, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(UUIDStr, ForeignKey("tenants.id"), index=True)
    conversation_id: Mapped[str] = mapped_column(UUIDStr, ForeignKey("conversations.id"))
    whatsapp_number_id: Mapped[str] = mapped_column(UUIDStr, ForeignKey("whatsapp_numbers.id"))
    worker_id: Mapped[str] = mapped_column(UUIDStr, ForeignKey("workers.id"), index=True)
    customer_id: Mapped[str] = mapped_column(UUIDStr, ForeignKey("customers.id"), index=True)
    wamid: Mapped[str] = mapped_column(String(255))
    direction: Mapped[str] = mapped_column(String(10))
    message_type: Mapped[str] = mapped_column(String(20), default="text")
    body: Mapped[str] = mapped_column(Text, default="")
    media_id: Mapped[Optional[str]] = mapped_column(String(255))
    lang: Mapped[Optional[str]] = mapped_column(String(20))
    parsed_intent: Mapped[Optional[str]] = mapped_column(String(50))
    confidence: Mapped[Optional[float]] = mapped_column(Float)
    extraction: Mapped[Optional[dict]] = mapped_column(JSONB)
    ai_status: Mapped[str] = mapped_column(String(20), default="PENDING")
    status: Mapped[str] = mapped_column(String(20), default="RECEIVED")
    lead_id: Mapped[Optional[str]] = mapped_column(UUIDStr)
    agent_run_id: Mapped[Optional[str]] = mapped_column(UUIDStr)
    sent_by: Mapped[Optional[str]] = mapped_column(String(16))
    referral: Mapped[Optional[dict]] = mapped_column(JSONB)
    delivery_status: Mapped[Optional[str]] = mapped_column(String(16))
    delivered_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    read_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[str] = mapped_column(UUIDStr, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(UUIDStr, ForeignKey("tenants.id"), index=True)
    event_type: Mapped[str] = mapped_column(String(64))
    actor: Mapped[str] = mapped_column(String(128))
    entity_type: Mapped[Optional[str]] = mapped_column(String(64))
    entity_id: Mapped[Optional[str]] = mapped_column(String(255))
    details: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# ===================================================================== Phase 1 (0002)


def _id() -> Mapped[str]:
    return mapped_column(UUIDStr, primary_key=True)


def _tenant() -> Mapped[str]:
    return mapped_column(UUIDStr, ForeignKey("tenants.id"), index=True)


def _created() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), default=utcnow)


class TenantConfigVersion(Base):
    __tablename__ = "tenant_config_versions"
    id: Mapped[str] = _id()
    tenant_id: Mapped[str] = _tenant()
    kind: Mapped[str] = mapped_column(String(16))  # config|skill|pricing|knowledge
    version: Mapped[int] = mapped_column(Integer)
    checksum: Mapped[str] = mapped_column(String(64))
    content: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE")
    created_by: Mapped[str] = mapped_column(String(128), default="onboarding")
    created_at: Mapped[datetime] = _created()


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = _id()
    email: Mapped[str] = mapped_column(String(255), unique=True)
    display_name: Mapped[Optional[str]] = mapped_column(String(255))
    password_hash: Mapped[Optional[str]] = mapped_column(String(255))
    is_platform_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = _created()


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("user_id", "tenant_id"),)
    id: Mapped[str] = _id()
    user_id: Mapped[str] = mapped_column(UUIDStr, ForeignKey("users.id"))
    tenant_id: Mapped[str] = mapped_column(UUIDStr, ForeignKey("tenants.id"))
    role: Mapped[str] = mapped_column(String(32))
    worker_id: Mapped[Optional[str]] = mapped_column(UUIDStr)
    created_at: Mapped[datetime] = _created()


class PriceList(Base):
    __tablename__ = "price_lists"
    id: Mapped[str] = _id()
    tenant_id: Mapped[str] = _tenant()
    version: Mapped[int] = mapped_column(Integer)
    source_file: Mapped[str] = mapped_column(String(255))
    checksum: Mapped[str] = mapped_column(String(64))
    row_count: Mapped[int] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    imported_by: Mapped[str] = mapped_column(String(128), default="onboarding")
    imported_at: Mapped[datetime] = _created()


class PriceItem(Base):
    __tablename__ = "price_items"
    id: Mapped[str] = _id()
    tenant_id: Mapped[str] = _tenant()
    price_list_id: Mapped[str] = mapped_column(UUIDStr, ForeignKey("price_lists.id"))
    sku: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(255))
    category: Mapped[Optional[str]] = mapped_column(String(100))
    attrs: Mapped[dict] = mapped_column(JSONB, default=dict)
    price: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2))
    price_min: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2))
    price_max: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2))
    currency: Mapped[str] = mapped_column(String(8), default="INR")
    unit: Mapped[str] = mapped_column(String(32), default="unit")
    tax_included: Mapped[bool] = mapped_column(Boolean, default=False)
    valid_until: Mapped[Optional[Date]] = mapped_column(SADate)
    aliases: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    notes: Mapped[Optional[str]] = mapped_column(Text)


class KbDocument(Base):
    __tablename__ = "kb_documents"
    __table_args__ = (UniqueConstraint("tenant_id", "source_path"),)
    id: Mapped[str] = _id()
    tenant_id: Mapped[str] = _tenant()
    title: Mapped[str] = mapped_column(String(255))
    source_path: Mapped[str] = mapped_column(String(255))
    version: Mapped[int] = mapped_column(Integer, default=1)
    checksum: Mapped[str] = mapped_column(String(64))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = _created()
    body: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # source text for zuvo-ai-rag indexing
    index_state: Mapped[str] = mapped_column(String(16), default="pending")  # pending|processing|indexed|failed (zuvo-ai-rag)


class KbChunk(Base):
    __tablename__ = "kb_chunks"
    id: Mapped[str] = _id()
    tenant_id: Mapped[str] = _tenant()
    document_id: Mapped[str] = mapped_column(UUIDStr, ForeignKey("kb_documents.id"))
    ordinal: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    # NULL = lexical chunk owned by bundle sync; set = embedded chunk owned by zuvo-ai-rag
    embed_model: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)


class Campaign(Base):
    __tablename__ = "campaigns"
    __table_args__ = (UniqueConstraint("tenant_id", "platform", "external_id"),)
    id: Mapped[str] = _id()
    tenant_id: Mapped[str] = _tenant()
    platform: Mapped[str] = mapped_column(String(32))
    external_id: Mapped[str] = mapped_column(String(255))
    name: Mapped[Optional[str]] = mapped_column(String(255))
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    spend_inr: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2))
    created_at: Mapped[datetime] = _created()


class Lead(Base):
    __tablename__ = "leads"
    id: Mapped[str] = _id()
    tenant_id: Mapped[str] = _tenant()
    customer_id: Mapped[str] = mapped_column(UUIDStr, ForeignKey("customers.id"))
    whatsapp_number_id: Mapped[Optional[str]] = mapped_column(UUIDStr, ForeignKey("whatsapp_numbers.id"))
    assigned_worker_id: Mapped[Optional[str]] = mapped_column(UUIDStr, ForeignKey("workers.id"))
    campaign_id: Mapped[Optional[str]] = mapped_column(UUIDStr, ForeignKey("campaigns.id"))
    source: Mapped[Optional[str]] = mapped_column(String(32))
    referral: Mapped[Optional[dict]] = mapped_column(JSONB)
    interest: Mapped[Optional[str]] = mapped_column(String(255))
    budget_min: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2))
    budget_max: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2))
    timeline_text: Mapped[Optional[str]] = mapped_column(String(255))
    location: Mapped[Optional[str]] = mapped_column(String(255))
    stage: Mapped[str] = mapped_column(String(32), default="NEW")
    score: Mapped[int] = mapped_column(Integer, default=0)
    lost_reason: Mapped[Optional[str]] = mapped_column(String(255))
    first_contact_at: Mapped[datetime] = _created()
    first_response_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_customer_msg_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_business_msg_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    next_followup_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _created()


class LeadEvent(Base):
    __tablename__ = "lead_events"
    id: Mapped[str] = _id()
    tenant_id: Mapped[str] = _tenant()
    lead_id: Mapped[str] = mapped_column(UUIDStr, ForeignKey("leads.id"))
    type: Mapped[str] = mapped_column(String(32))
    from_value: Mapped[Optional[str]] = mapped_column(String(255))
    to_value: Mapped[Optional[str]] = mapped_column(String(255))
    actor: Mapped[str] = mapped_column(String(128))
    message_id: Mapped[Optional[str]] = mapped_column(UUIDStr)
    details: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = _created()


class VisitPlan(Base):
    __tablename__ = "visit_plans"
    id: Mapped[str] = _id()
    tenant_id: Mapped[str] = _tenant()
    lead_id: Mapped[str] = mapped_column(UUIDStr, ForeignKey("leads.id"))
    source_message_id: Mapped[Optional[str]] = mapped_column(UUIDStr)
    raw_text: Mapped[str] = mapped_column(Text, default="")
    resolved_date: Mapped[Date] = mapped_column(SADate)
    resolved_time_window: Mapped[Optional[str]] = mapped_column(String(16))
    precision: Mapped[str] = mapped_column(String(16))
    confidence: Mapped[float] = mapped_column(Float, default=0)
    status: Mapped[str] = mapped_column(String(16), default="PLANNED")
    rescheduled_from_id: Mapped[Optional[str]] = mapped_column(UUIDStr)
    outcome_by: Mapped[Optional[str]] = mapped_column(String(128))
    outcome_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()


class FollowupTask(Base):
    __tablename__ = "followup_tasks"
    id: Mapped[str] = _id()
    tenant_id: Mapped[str] = _tenant()
    lead_id: Mapped[str] = mapped_column(UUIDStr, ForeignKey("leads.id"))
    visit_plan_id: Mapped[Optional[str]] = mapped_column(UUIDStr)
    rule_id: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(32))
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    mode: Mapped[str] = mapped_column(String(16))
    channel: Mapped[str] = mapped_column(String(16), default="whatsapp")
    template_name: Mapped[Optional[str]] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(20), default="SCHEDULED")
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    skip_reason: Mapped[Optional[str]] = mapped_column(String(128))
    result_message_id: Mapped[Optional[str]] = mapped_column(String(255))
    created_at: Mapped[datetime] = _created()
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class AgentRun(Base):
    __tablename__ = "agent_runs"
    id: Mapped[str] = _id()
    tenant_id: Mapped[str] = _tenant()
    message_id: Mapped[Optional[str]] = mapped_column(UUIDStr)
    lead_id: Mapped[Optional[str]] = mapped_column(UUIDStr)
    followup_task_id: Mapped[Optional[str]] = mapped_column(UUIDStr)
    intent: Mapped[Optional[str]] = mapped_column(String(50))
    task: Mapped[str] = mapped_column(String(32))
    topic: Mapped[Optional[str]] = mapped_column(String(128))
    question_text: Mapped[Optional[str]] = mapped_column(Text)
    retrieved: Mapped[dict] = mapped_column(JSONB, default=dict)
    proposed_text: Mapped[Optional[str]] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float, default=0)
    decision: Mapped[str] = mapped_column(String(20))
    decision_reasons: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    model: Mapped[Optional[str]] = mapped_column(String(64))
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    cost_inr: Mapped[Decimal] = mapped_column(Numeric(12, 4), default=Decimal("0"))
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    config_checksum: Mapped[Optional[str]] = mapped_column(String(64))
    # ok | no_knowledge | rag_unavailable | draft_failed | draft_rejected | ai_budget_exhausted | low_confidence | skipped
    outcome: Mapped[Optional[str]] = mapped_column(String(32))
    diagnostics: Mapped[dict] = mapped_column(JSONB, default=dict)  # ids, scores, models, latencies - never prompts/reasoning
    created_at: Mapped[datetime] = _created()


class Approval(Base):
    __tablename__ = "approvals"
    id: Mapped[str] = _id()
    tenant_id: Mapped[str] = _tenant()
    agent_run_id: Mapped[Optional[str]] = mapped_column(UUIDStr)
    lead_id: Mapped[Optional[str]] = mapped_column(UUIDStr)
    customer_id: Mapped[Optional[str]] = mapped_column(UUIDStr)
    whatsapp_number_id: Mapped[Optional[str]] = mapped_column(UUIDStr)
    followup_task_id: Mapped[Optional[str]] = mapped_column(UUIDStr)
    kind: Mapped[str] = mapped_column(String(16))
    topic: Mapped[Optional[str]] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16), default="PENDING")
    question_text: Mapped[Optional[str]] = mapped_column(Text)
    proposed_text: Mapped[str] = mapped_column(Text)
    final_text: Mapped[Optional[str]] = mapped_column(Text)
    template_name: Mapped[Optional[str]] = mapped_column(String(128))
    template_params: Mapped[Optional[list]] = mapped_column(JSONB)
    edit_distance: Mapped[Optional[int]] = mapped_column(Integer)
    reject_reason: Mapped[Optional[str]] = mapped_column(Text)
    reviewer: Mapped[Optional[str]] = mapped_column(String(128))
    sla_due_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    holding_sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()


class LearnedExample(Base):
    __tablename__ = "learned_examples"
    id: Mapped[str] = _id()
    tenant_id: Mapped[str] = _tenant()
    task: Mapped[str] = mapped_column(String(32))
    topic: Mapped[str] = mapped_column(String(128))
    question_text: Mapped[str] = mapped_column(Text)
    answer_text: Mapped[Optional[str]] = mapped_column(Text)
    reject_reason: Mapped[Optional[str]] = mapped_column(Text)
    price_item_skus: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    source_approval_id: Mapped[Optional[str]] = mapped_column(UUIDStr)
    outcome: Mapped[str] = mapped_column(String(16))
    weight: Mapped[float] = mapped_column(Float, default=1.0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    use_count: Mapped[int] = mapped_column(Integer, default=0)
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()


class AutonomyStat(Base):
    __tablename__ = "autonomy_stats"
    tenant_id: Mapped[str] = mapped_column(UUIDStr, ForeignKey("tenants.id"), primary_key=True)
    task: Mapped[str] = mapped_column(String(32), primary_key=True)
    topic: Mapped[str] = mapped_column(String(128), primary_key=True)
    recent: Mapped[list] = mapped_column(JSONB, default=list)
    approved_unchanged: Mapped[int] = mapped_column(Integer, default=0)
    edited: Mapped[int] = mapped_column(Integer, default=0)
    rejected: Mapped[int] = mapped_column(Integer, default=0)
    auto_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = _created()


class MetricsDaily(Base):
    __tablename__ = "metrics_daily"
    tenant_id: Mapped[str] = mapped_column(UUIDStr, ForeignKey("tenants.id"), primary_key=True)
    date: Mapped[Date] = mapped_column(SADate, primary_key=True)
    data: Mapped[dict] = mapped_column(JSONB, default=dict)
    updated_at: Mapped[datetime] = _created()


class AiUsageDaily(Base):
    __tablename__ = "ai_usage_daily"
    tenant_id: Mapped[str] = mapped_column(UUIDStr, ForeignKey("tenants.id"), primary_key=True)
    date: Mapped[Date] = mapped_column(SADate, primary_key=True)
    service: Mapped[str] = mapped_column(String(32), primary_key=True)
    model: Mapped[str] = mapped_column(String(64), primary_key=True)
    calls: Mapped[int] = mapped_column(Integer, default=0)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    cost_inr: Mapped[Decimal] = mapped_column(Numeric(12, 4), default=Decimal("0"))
