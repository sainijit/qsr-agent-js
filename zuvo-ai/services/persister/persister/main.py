"""Persister: the single writer of conversation/message rows. Every write runs under RLS."""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from zuvo_common import outbound_state as ost
from zuvo_common.audit import write_audit
from zuvo_common.bus import EventBus, KafkaEventBus, Record
from zuvo_common.contract import ConsumerContract, PermanentError
from zuvo_common.processed import first_delivery
from zuvo_common.db import make_sessionmaker, tenant_session
from zuvo_common.events import (
    Envelope,
    ExtractionPayload,
    InboundMessagePayload,
    MessageStatusPayload,
    OutboundRequestPayload,
    OutboundSentPayload,
    Topics,
)
from zuvo_common.idempotency import IdempotencyStore, RedisIdempotencyStore
from zuvo_common.logging import get_logger
from zuvo_common.outbox import Outbox
from zuvo_common.runtime import install_shutdown
from zuvo_common.models import Conversation, Customer, Message
from zuvo_common.settings import get_settings
from zuvo_common.tenancy import TenantRouting
from zuvo_common.tenant_sync import sync_tenant
from zuvo_common.usage import record_usage

log = get_logger("persister")
_NS = uuid.UUID("0b8a7f0e-5d7e-4b0e-8f55-2c6a1f9d3e11")
ACTOR = "service:persister"


def persisted_event_id(tenant_id: str, ref: str, change: str) -> str:
    """Deterministic so a redelivered input produces the same downstream event_id."""
    return str(uuid.uuid5(_NS, f"{tenant_id}:{ref}:{change}"))


class Persister:
    def __init__(self, sm: async_sessionmaker[AsyncSession], bus: EventBus, routing: TenantRouting):
        self.sm = sm
        self.bus = bus
        self.routing = routing
        self.outbox = Outbox(sm, bus)
        self._synced: dict[str, float] = {}

    async def _ensure_tenant(self, session: AsyncSession, tenant_id: str) -> None:
        self.routing.config  # hot-reload check; keeps last valid config on error
        cfg_version = self.routing.version
        if self._synced.get(tenant_id) == cfg_version:
            return
        entry = self.routing.tenant(tenant_id)
        if not entry:
            raise PermanentError("tenant not present in routing config")
        await sync_tenant(session, entry)
        self._synced[tenant_id] = cfg_version

    async def _customer(self, s: AsyncSession, tenant_id: str, phone: str, name: Optional[str],
                        source: Optional[str] = None) -> Customer:
        await s.execute(
            insert(Customer)
            .values(id=str(uuid.uuid4()), tenant_id=tenant_id, phone=phone, display_name=name, account_name=name,
                    source=source, tags=[], opted_out=False)
            .on_conflict_do_nothing(index_elements=["tenant_id", "phone"])
        )
        cust = (await s.execute(select(Customer).where(Customer.tenant_id == tenant_id, Customer.phone == phone))).scalar_one()
        if name and not cust.display_name:
            cust.display_name = name
        return cust

    async def _conversation(self, s: AsyncSession, tenant_id: str, number_id: str, worker_id: str, customer_id: str) -> Conversation:
        await s.execute(
            insert(Conversation)
            .values(id=str(uuid.uuid4()), tenant_id=tenant_id, whatsapp_number_id=number_id, worker_id=worker_id, customer_id=customer_id)
            .on_conflict_do_nothing(index_elements=["tenant_id", "whatsapp_number_id", "customer_id"])
        )
        return (
            await s.execute(
                select(Conversation).where(
                    Conversation.tenant_id == tenant_id,
                    Conversation.whatsapp_number_id == number_id,
                    Conversation.customer_id == customer_id,
                )
            )
        ).scalar_one()

    async def _publish_persisted(self, env: Envelope, change: str, message: Message, extra: dict | None = None,
                                 id_change: Optional[str] = None) -> None:
        await self.bus.publish(
            Topics.MESSAGE_PERSISTED,
            Envelope(
                event_id=persisted_event_id(env.tenant_id, message.wamid, id_change or change),
                event_type=Topics.MESSAGE_PERSISTED,
                tenant_id=env.tenant_id,
                worker_id=message.worker_id,
                customer_id=message.customer_id,
                wamid=message.wamid,
                payload={
                    "change": change,
                    "message_id": message.id,
                    "direction": message.direction,
                    "message_type": message.message_type,
                    **(extra or {}),
                },
            ),
        )

    # ------------------------------------------------------------ handlers

    async def on_inbound(self, env: Envelope) -> None:
        p = InboundMessagePayload.model_validate(env.payload)
        now = datetime.now(timezone.utc)
        async with tenant_session(self.sm, env.tenant_id) as s:
            await self._ensure_tenant(s, env.tenant_id)
            source = "ctwa_ad" if p.referral else "whatsapp"
            cust = await self._customer(s, env.tenant_id, p.customer_phone, p.customer_name, source)
            conv = await self._conversation(s, env.tenant_id, p.whatsapp_number_id, env.worker_id, cust.id)
            conv.last_message_at = now
            conv.last_inbound_at = now
            body = p.text or (f"[{p.message_type} message]" if p.message_type != "text" else "")
            result = await s.execute(
                insert(Message)
                .values(
                    id=str(uuid.uuid4()),
                    tenant_id=env.tenant_id,
                    conversation_id=conv.id,
                    whatsapp_number_id=p.whatsapp_number_id,
                    worker_id=env.worker_id,
                    customer_id=cust.id,
                    wamid=env.wamid,
                    direction="INBOUND",
                    message_type=p.message_type,
                    body=body,
                    media_id=p.media_id,
                    referral=p.referral,
                    ai_status="PENDING",
                    status="RECEIVED",
                    created_at=now,
                )
                .on_conflict_do_nothing(index_elements=["tenant_id", "wamid"])
                .returning(Message.id)
            )
            inserted = result.scalar_one_or_none() is not None
            message = (await s.execute(select(Message).where(Message.tenant_id == env.tenant_id, Message.wamid == env.wamid))).scalar_one()
            if inserted:
                await write_audit(
                    s, env.tenant_id, "MESSAGE_INGESTED", ACTOR, "message", message.id,
                    {"wamid": env.wamid, "worker_key": p.worker_key, "customer_phone": p.customer_phone, "type": p.message_type},
                )
        await self._publish_persisted(
            env, "created", message,
            {
                "text": message.body if message.message_type == "text" else None,
                "tenant_policy": p.tenant_policy,
                "referral": p.referral,
                "customer_phone": p.customer_phone,
                "conversation_id": message.conversation_id,
                "whatsapp_number_id": message.whatsapp_number_id,
                "at": message.created_at.isoformat(),
            },
        )

    async def on_extraction(self, env: Envelope, flagged: bool) -> None:
        p = ExtractionPayload.model_validate(env.payload)
        async with tenant_session(self.sm, env.tenant_id) as s:
            message = await s.get(Message, p.message_id)
            if not message:
                raise PermanentError("message not found for extraction")
            if p.extraction:
                message.parsed_intent = p.extraction.intent.value
                message.confidence = p.extraction.confidence
                message.lang = p.extraction.lang
                message.extraction = p.extraction.model_dump(mode="json")
            message.ai_status = (
                "AI_PENDING" if p.reason == "ai_unavailable"
                else "SKIPPED" if p.reason == "unsupported_media"
                else "NEEDS_REVIEW" if flagged
                else "EXTRACTED"
            )
            first = await first_delivery(s, "persister", env)  # usage counters + audit are not idempotent
            if first and p.tier == "tier2" and (p.input_tokens or p.output_tokens):
                await record_usage(s, env.tenant_id, datetime.now(timezone.utc).date(), "ai-router", p.model or "unknown",
                                   p.input_tokens or 0, p.output_tokens or 0)
            if first:
                await write_audit(
                    s, env.tenant_id, "AI_REVIEW_FLAGGED" if flagged else "AI_INTENT_EXTRACTED", "service:ai-router",
                    "message", message.id,
                    {
                        "tier": p.tier, "model": p.model, "reason": p.reason, "latency_ms": p.latency_ms,
                        "input_tokens": p.input_tokens, "output_tokens": p.output_tokens,
                        "intent": message.parsed_intent, "confidence": message.confidence,
                    },
                )
        await self._publish_persisted(env, f"ai:{env.event_id}", message, {"ai_status": message.ai_status})
        # lead-service / agent-worker consume ai.intent.extracted directly; this event only refreshes the UI

    async def on_outbound(self, env: Envelope) -> None:
        p = OutboundSentPayload.model_validate(env.payload)
        line = self.routing.line_by_id(p.whatsapp_number_id)
        if not line or line.tenant_id != env.tenant_id:
            raise PermanentError("outbound line does not belong to tenant")
        now = datetime.now(timezone.utc)
        state = p.state or ("ACCEPTED" if p.success else "FAILED")
        placeholder = f"unknown.{p.request_id}"
        wamid = p.wamid or env.wamid or (placeholder if state == "UNKNOWN" else f"failed.{p.request_id}")
        msg_status = {"ACCEPTED": "SENT", "FAILED": "FAILED", "UNKNOWN": "UNKNOWN"}[state]
        delivery = {"ACCEPTED": "sent", "FAILED": "failed"}.get(state)
        delivered_at = read_at = None
        async with tenant_session(self.sm, env.tenant_id) as s:
            await self._ensure_tenant(s, env.tenant_id)
            orow = await ost.get(s, p.outbound_id) if p.outbound_id else None
            if orow is not None and state == "ACCEPTED" and orow.delivery_status:
                # receipts may have been applied to the delivery ledger before this message row existed
                delivery, delivered_at, read_at = orow.delivery_status, orow.delivered_at, orow.read_at
            # a previously UNKNOWN send is now resolved: update its placeholder row instead of adding a second message
            message = None
            if state != "UNKNOWN":
                message = (await s.execute(select(Message).where(
                    Message.tenant_id == env.tenant_id, Message.wamid == placeholder).with_for_update())).scalar_one_or_none()
            if message is not None:
                if p.wamid:
                    message.wamid = p.wamid
                message.status, message.delivery_status = msg_status, delivery
                message.delivered_at = message.delivered_at or delivered_at
                message.read_at = message.read_at or read_at
            else:
                cust = await self._customer(s, env.tenant_id, p.recipient_phone, None)
                conv = await self._conversation(s, env.tenant_id, p.whatsapp_number_id, line.worker_id, cust.id)
                conv.last_message_at = now
                await s.execute(
                    insert(Message)
                    .values(
                        id=str(uuid.uuid4()), tenant_id=env.tenant_id, conversation_id=conv.id,
                        whatsapp_number_id=p.whatsapp_number_id, worker_id=line.worker_id, customer_id=cust.id,
                        wamid=wamid, direction="OUTBOUND", message_type="text", body=p.text,
                        ai_status="SKIPPED", status=msg_status, created_at=now,
                        sent_by=p.sent_by, lead_id=p.lead_id, agent_run_id=p.agent_run_id,
                        delivery_status=delivery, delivered_at=delivered_at, read_at=read_at,
                    )
                    .on_conflict_do_nothing(index_elements=["tenant_id", "wamid"])
                )
                message = (await s.execute(select(Message).where(Message.tenant_id == env.tenant_id, Message.wamid == wamid))).scalar_one()
        await self._publish_persisted(env, "outbound", message, {
            "success": p.success, "state": state, "sent_by": p.sent_by, "lead_id": p.lead_id, "agent_run_id": p.agent_run_id,
            "approval_id": p.approval_id, "followup_task_id": p.followup_task_id, "error": p.error,
            "customer_phone": p.recipient_phone, "whatsapp_number_id": p.whatsapp_number_id,
            "conversation_id": message.conversation_id, "at": message.created_at.isoformat(),
        }, id_change=f"outbound:{state}")

    _STATUS_RANK = ost.DELIVERY_RANK

    async def on_status(self, env: Envelope) -> None:
        p = MessageStatusPayload.model_validate(env.payload)
        at = datetime.fromtimestamp(int(p.timestamp), timezone.utc) if (p.timestamp or "").isdigit() else datetime.now(timezone.utc)
        async with self.outbox.session(env.tenant_id) as s:
            # delivery ledger first (RLS-scoped: a receipt can only touch this tenant's sends)
            res = await ost.apply_status(s, p.wamid, p.status, at, p.callback_data, p.error)
            if res and res["reconciled"] and res["row"].request:
                after = await ost.get(s, str(res["row"].id))
                req = OutboundRequestPayload.model_validate(res["row"].request)
                await self.outbox.add(s, Topics.OUTBOUND_SENT, ost.sent_envelope(env.tenant_id, after, req, env.worker_id))
                log.info("outbound outcome reconciled from status webhook", extra={"ctx": {
                    "tenant_id": env.tenant_id, "outbound_id": str(after.id), "state": after.status,
                    "operation": "outbound.reconcile", "outcome": "reconciled"}})
            message = (await s.execute(
                select(Message).where(Message.tenant_id == env.tenant_id, Message.wamid == p.wamid).with_for_update()
            )).scalar_one_or_none()
            if not message:
                return  # not (yet) a message row: sent outside ZuvoCloud, or an UNKNOWN placeholder fixed via the event above
            if self._STATUS_RANK[p.status] <= self._STATUS_RANK.get(message.delivery_status or "", 0):
                return  # receipts arrive out of order or twice; never move backwards
            message.delivery_status = p.status
            if p.status in ("delivered", "read") and not message.delivered_at:
                message.delivered_at = at
            if p.status == "read":
                message.read_at = at
            if p.status == "failed":
                message.status = "FAILED"
                await write_audit(s, env.tenant_id, "MESSAGE_DELIVERY_FAILED", ACTOR, "message", message.id,
                                  {"wamid": p.wamid, "error": p.error})
        await self._publish_persisted(env, f"status:{p.status}", message, {"delivery_status": p.status})

    async def handle(self, env: Envelope, _record: Record) -> None:
        if env.event_type == Topics.INBOUND_RECEIVED:
            await self.on_inbound(env)
        elif env.event_type == Topics.INTENT_EXTRACTED:
            await self.on_extraction(env, flagged=False)
        elif env.event_type == Topics.REVIEW_FLAGGED:
            await self.on_extraction(env, flagged=True)
        elif env.event_type == Topics.OUTBOUND_SENT:
            await self.on_outbound(env)
        elif env.event_type == Topics.MESSAGE_STATUS:
            await self.on_status(env)
        else:
            raise PermanentError(f"unexpected event_type {env.event_type}")

    async def audit_hook(self, tenant_id: Optional[str], event_type: str, details: dict) -> None:
        if not tenant_id or not self.routing.tenant(tenant_id):
            return
        async with tenant_session(self.sm, tenant_id) as s:
            await write_audit(s, tenant_id, event_type, ACTOR, details=details)


async def main() -> None:
    settings = get_settings()
    bus = KafkaEventBus(settings.kafka_bootstrap_servers, "persister")
    idem: IdempotencyStore = RedisIdempotencyStore(settings.redis_url)
    routing = TenantRouting(settings.tenant_routing_path)
    persister = Persister(make_sessionmaker(settings.database_url), bus, routing)
    await bus.start()
    install_shutdown(bus.request_stop, log)
    contract = ConsumerContract(
        bus, idem, "persister", persister.handle,
        audit=persister.audit_hook,
        authorized_tenants=lambda tid: routing.tenant(tid) is not None,
    )
    log.info("persister consuming")
    try:
        await bus.consume(
            [Topics.INBOUND_RECEIVED, Topics.INTENT_EXTRACTED, Topics.REVIEW_FLAGGED, Topics.OUTBOUND_SENT, Topics.MESSAGE_STATUS],
            "persister",
            contract,
        )
    finally:
        await bus.stop()


if __name__ == "__main__":
    asyncio.run(main())
