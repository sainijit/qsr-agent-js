"""Lead service: turns conversations into leads, visit plans and follow-up tasks.

Consumes message.persisted (inbound/outbound), ai.intent.extracted and ai.review.flagged. Events from different
topics are not ordered relative to each other, so every handler first ensures the customer's open lead exists.
Tenants without a bundle (legacy routing only) are ignored."""
from __future__ import annotations

import asyncio
import re
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from zuvo_common.bus import EventBus, KafkaEventBus, Record
from zuvo_common.bundle import BundleRegistry, TenantConfig, default_bundles_dir
from zuvo_common.contract import ConsumerContract
from zuvo_common.dates import VisitWhen, parse_iso_date, resolve_visit
from zuvo_common.db import make_sessionmaker
from zuvo_common.events import Envelope, ExtractionPayload, Intent, Topics
from zuvo_common.idempotency import RedisIdempotencyStore
from zuvo_common import leads as L
from zuvo_common.logging import get_logger
from zuvo_common.outbox import Outbox
from zuvo_common.processed import first_delivery
from zuvo_common.runtime import install_shutdown
from zuvo_common.models import Campaign, Customer, FollowupTask, Lead, Message
from zuvo_common.settings import get_settings

log = get_logger("lead-service")
ACTOR = "service:lead"
GROUP = "lead-service"
_NS = uuid.UUID("5b0e2a44-8f1c-4c9e-9a7d-3e6f1b2c4d55")



is_opt_out = L.is_opt_out

VISIT_HINT = re.compile(
    r"\b(visit|come|coming|drop by|test ?drive|showroom|aa(u|o)?nga|aaunga|aayenge|aaenge|aayega|aaungi|aa (raha|rahe|rahi)|"
    r"milne|milte|milenge|chakkar|dekhne aa)\b", re.I)
MIN_VISIT_CONFIDENCE = 0.5


def _dt(v: Optional[str]) -> datetime:
    try:
        d = datetime.fromisoformat(v) if v else None
    except ValueError:
        d = None
    return d if d and d.tzinfo else datetime.now(timezone.utc)


class LeadService:
    def __init__(self, sm: async_sessionmaker[AsyncSession], bus: EventBus, bundles: BundleRegistry):
        self.sm = sm
        self.bus = bus
        self.bundles = bundles
        self.outbox = Outbox(sm, bus)

    async def _publish(self, s: AsyncSession, topic: str, env: Envelope, key: str, payload: dict) -> None:
        """Enqueue in the handler's transaction (outbox): emitted iff the state change commits."""
        await self.outbox.add(s, topic, Envelope(
            event_id=str(uuid.uuid5(_NS, f"{env.tenant_id}:{env.event_id}:{key}")),
            event_type=topic, tenant_id=env.tenant_id, customer_id=env.customer_id, worker_id=env.worker_id,
            payload=payload,
        ))

    async def _campaign(self, s: AsyncSession, tenant_id: str, referral: Optional[dict]) -> Optional[str]:
        if not referral:
            return None
        ext = str(referral.get("source_id") or referral.get("source_url") or "unknown")[:255]
        platform = "meta_ctwa" if referral.get("source_type") in ("ad", "post", None) else str(referral["source_type"])[:32]
        await s.execute(
            insert(Campaign).values(id=str(uuid.uuid4()), tenant_id=tenant_id, platform=platform, external_id=ext,
                                    name=(referral.get("headline") or None), started_at=datetime.now(timezone.utc))
            .on_conflict_do_nothing(index_elements=["tenant_id", "platform", "external_id"])
        )
        return await s.scalar(select(Campaign.id).where(Campaign.tenant_id == tenant_id, Campaign.platform == platform,
                                                        Campaign.external_id == ext))

    async def _lead(self, s: AsyncSession, env: Envelope, cust_id: str, number_id: Optional[str], worker_id: Optional[str],
                    at: datetime, referral: Optional[dict], message_id: Optional[str], source: Optional[str] = None) -> tuple[Lead, bool]:
        campaign_id = await self._campaign(s, env.tenant_id, referral)
        src = source or ("ctwa_ad" if referral else "whatsapp")
        return await L.ensure_open_lead(s, env.tenant_id, cust_id, whatsapp_number_id=number_id, worker_id=worker_id,
                                        at=at, source=src, referral=referral, campaign_id=campaign_id, message_id=message_id)

    # ------------------------------------------------------------ message.persisted

    async def on_persisted(self, env: Envelope, cfg: TenantConfig) -> None:
        p = env.payload
        change = p.get("change")
        if change == "created" and p.get("direction") == "INBOUND":
            await self._inbound(env, cfg)
        elif change == "outbound" and p.get("success"):
            await self._outbound(env, cfg)

    async def _inbound(self, env: Envelope, cfg: TenantConfig) -> None:
        p = env.payload
        at = _dt(p.get("at"))
        text = p.get("text") or ""
        async with self.outbox.session(env.tenant_id) as s:
            if not await first_delivery(s, GROUP, env):
                return  # counters / stage changes must not be applied twice
            cust = await s.get(Customer, env.customer_id)
            if not cust:
                return
            lead, created = await self._lead(s, env, cust.id, p.get("whatsapp_number_id"), env.worker_id, at,
                                             p.get("referral"), p.get("message_id"))
            await L.on_customer_message(s, cfg, lead, at, p.get("message_id"))
            await s.execute(Message.__table__.update().where(Message.id == p.get("message_id")).values(lead_id=lead.id))
            opted = False
            if is_opt_out(text, cfg.opt_out_keywords) and not cust.opted_out:
                await L.opt_out(s, cust.id, lead, p.get("message_id"))
                opted = True
            elif cust.opted_out and text.strip():
                # any new customer message after STOP restores consent (WhatsApp policy: user-initiated)
                cust.opted_out, cust.opted_out_at, cust.consent_at = False, None, at
            if created:
                lead.score = L.compute_score(lead, cfg)
            await self._publish(s, Topics.LEAD_UPDATED, env, "in", {
                "lead_id": lead.id, "stage": lead.stage, "change": "created" if created else ("opted_out" if opted else "customer_message")})

    async def _outbound(self, env: Envelope, cfg: TenantConfig) -> None:
        p = env.payload
        at = _dt(p.get("at"))
        async with self.outbox.session(env.tenant_id) as s:
            if not await first_delivery(s, GROUP, env):
                return
            cust = await s.get(Customer, env.customer_id)
            if not cust:
                return
            lead = None
            if p.get("lead_id"):
                lead = (await s.execute(select(Lead).where(Lead.id == p["lead_id"]).with_for_update())).scalar_one_or_none()
                if lead and lead.closed_at is not None:
                    lead = None
            if lead is None:
                lead, _ = await self._lead(s, env, cust.id, p.get("whatsapp_number_id"), env.worker_id, at, None,
                                           p.get("message_id"), source="outbound")
            attempt = None
            if p.get("followup_task_id"):
                task = await s.get(FollowupTask, p["followup_task_id"])
                attempt = task.attempt if task else None
            sent_by = p.get("sent_by") or "human"
            await L.on_business_message(s, cfg, lead, at, sent_by, attempt)
            await s.execute(Message.__table__.update().where(Message.id == p.get("message_id")).values(lead_id=lead.id))
            if sent_by == "human" and lead.stage == "NEW" and lead.last_customer_msg_at:
                await L.set_stage(s, cfg, lead, "ENGAGED", ACTOR)
            await self._publish(s, Topics.LEAD_UPDATED, env, "out", {"lead_id": lead.id, "stage": lead.stage, "change": "business_message"})

    # ------------------------------------------------------------ extraction

    def _visit_when(self, x, text: str, cfg: TenantConfig) -> tuple[Optional[VisitWhen], float]:
        today = L.today_local(cfg)
        v = x.visit if x else None
        is_visit = bool(x and (x.intent == Intent.VISIT_PLAN or v))
        if not is_visit and not VISIT_HINT.search(text or ""):
            return None, 0.0
        # deterministic resolver first: exact business-calendar semantics, no model drift
        for candidate in filter(None, [text, v.date_expr if v else None, v.text if v else None]):
            w = resolve_visit(candidate, today)
            if w:
                conf = max(v.confidence if v else 0.0, x.confidence if x else 0.6) if is_visit else 0.6
                if v and v.time_window and not w.time_window:
                    w = VisitWhen(w.date, w.precision, v.time_window, w.matched)
                return w, conf
        d = parse_iso_date(v.resolved_date if v else None, today)
        if d:
            return VisitWhen(d, "EXACT", v.time_window, v.date_expr or "model"), min(v.confidence or 0.6, x.confidence)
        return None, 0.0

    async def on_extraction(self, env: Envelope, cfg: TenantConfig, flagged: bool) -> None:
        p = ExtractionPayload.model_validate(env.payload)
        x = p.extraction
        events: list[tuple[str, str, dict]] = []
        async with self.outbox.session(env.tenant_id) as s:
            if not await first_delivery(s, GROUP, env):
                return
            msg = await s.get(Message, p.message_id)
            if not msg or msg.direction != "INBOUND":
                return
            lead, _ = await self._lead(s, env, msg.customer_id, msg.whatsapp_number_id, msg.worker_id, msg.created_at,
                                       msg.referral, msg.id)
            if lead.closed_at is not None:
                return
            changed: list[str] = []
            if x and x.lead:
                changed = await L.apply_lead_fields(s, cfg, lead, x.lead.model_dump(), "ai", msg.id)
            if x and x.price_query and not lead.interest and x.price_query.item_text:
                changed += await L.apply_lead_fields(s, cfg, lead, {"interest": x.price_query.item_text}, "ai", msg.id)
            if x and x.intent in (Intent.INQUIRY_PRICE_STOCK, Intent.VISIT_PLAN, Intent.FAQ) and lead.stage == "NEW":
                await L.set_stage(s, cfg, lead, "ENGAGED", "ai", message_id=msg.id)
            if x and x.opt_out:
                cust = await s.get(Customer, msg.customer_id)
                if cust and not cust.opted_out:
                    await L.opt_out(s, cust.id, lead, msg.id)
                    events.append((Topics.LEAD_UPDATED, "optout", {"lead_id": lead.id, "stage": lead.stage, "change": "opted_out"}))

            if x and x.visit and x.visit.is_cancel:
                n = await L.cancel_visit(s, lead, "customer", msg.id)
                if n:
                    events.append((Topics.LEAD_UPDATED, "vcancel", {"lead_id": lead.id, "stage": lead.stage, "change": "visit_cancelled"}))
            else:
                when, conf = self._visit_when(x, msg.body, cfg)
                if when and conf >= MIN_VISIT_CONFIDENCE:
                    plan = await L.plan_visit(s, cfg, lead, when, raw_text=msg.body, confidence=conf, actor="ai",
                                              source_message_id=msg.id)
                    events.append((Topics.VISIT_PLANNED, "visit", {
                        "lead_id": lead.id, "visit_plan_id": plan.id, "date": plan.resolved_date.isoformat(),
                        "time_window": plan.resolved_time_window, "precision": plan.precision, "describe": when.describe(),
                        "message_id": msg.id, "confidence": conf,
                    }))
            if changed:
                events.append((Topics.LEAD_UPDATED, "fields", {"lead_id": lead.id, "stage": lead.stage, "change": "fields", "fields": changed}))
            for topic, key, payload in events:
                await self._publish(s, topic, env, key, payload)

    async def handle(self, env: Envelope, _record: Record) -> None:
        cfg = self.bundles.config(env.tenant_id)
        if cfg is None:
            return
        if env.event_type == Topics.MESSAGE_PERSISTED:
            await self.on_persisted(env, cfg)
        elif env.event_type in (Topics.INTENT_EXTRACTED, Topics.REVIEW_FLAGGED):
            await self.on_extraction(env, cfg, env.event_type == Topics.REVIEW_FLAGGED)


async def main() -> None:
    settings = get_settings()
    bus = KafkaEventBus(settings.kafka_bootstrap_servers, "lead-service")
    bundles = BundleRegistry(default_bundles_dir(settings.tenant_routing_path))
    svc = LeadService(make_sessionmaker(settings.database_url, pool_size=5), bus, bundles)
    await bus.start()
    install_shutdown(bus.request_stop, log)
    contract = ConsumerContract(bus, RedisIdempotencyStore(settings.redis_url), "lead-service", svc.handle,
                                authorized_tenants=lambda tid: bundles.get(tid) is not None)
    log.info("lead-service consuming")
    try:
        await bus.consume([Topics.MESSAGE_PERSISTED, Topics.INTENT_EXTRACTED, Topics.REVIEW_FLAGGED], "lead-service", contract)
    finally:
        await bus.stop()


if __name__ == "__main__":
    asyncio.run(main())
