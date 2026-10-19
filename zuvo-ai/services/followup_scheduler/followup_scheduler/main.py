"""Follow-up scheduler: the only time-driven component.

Every tick, per bundle tenant:
  * claims due SCHEDULED follow-up tasks (FOR UPDATE SKIP LOCKED -> QUEUED) and enqueues followup.due (outbox)
  * enqueues the holding reply for reply-approvals past their SLA (once)
  * marks open leads DORMANT after `lead.dormant_after_days` (every 10 min)
  * rolls up metrics_daily for today and yesterday (every 5 min)
Also hosts the transactional-outbox relay (every OUTBOX_RELAY_SECONDS, default 2 s) and the hourly retention
cleanup for outbox_events / processed_events.
Safe to run as several replicas: claiming and relaying use row locks and every publish is idempotent downstream."""
from __future__ import annotations

import asyncio
import os
import time
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from zuvo_common import leads as L
from zuvo_common.bus import EventBus, KafkaEventBus
from zuvo_common.bundle import TenantBundle
from zuvo_common.db import make_sessionmaker, tenant_session
from zuvo_common.events import Envelope, FollowupDuePayload, Topics
from zuvo_common.logging import get_logger
from zuvo_common.runtime import heartbeat, install_shutdown
from zuvo_common.metrics import compute_day, upsert_day
from zuvo_common.models import Approval, Conversation, Customer, FollowupTask, Lead
from zuvo_common.outbound import outbound_envelope
from zuvo_common import outbox as ob
from zuvo_common.settings import get_settings
from zuvo_common.tenancy import TenantRouting

log = get_logger("followup-scheduler")
_NS = uuid.UUID("2a4c6e80-1b3d-4f5a-8c7e-9d0f1a2b3c4d")
BATCH = 100


class Scheduler:
    def __init__(self, sm: async_sessionmaker[AsyncSession], bus: EventBus, routing: TenantRouting):
        self.sm, self.bus, self.routing = sm, bus, routing
        self.outbox = ob.Outbox(sm, bus)
        self._last: dict[str, float] = {}

    def _every(self, key: str, seconds: float) -> bool:
        now = time.monotonic()
        if now - self._last.get(key, -1e9) >= seconds:
            self._last[key] = now
            return True
        return False

    async def claim_due(self, b: TenantBundle, now: datetime | None = None) -> int:
        now = now or datetime.now(timezone.utc)
        tid = b.tenant_id
        claimed = 0
        async with self.outbox.session(tid) as s:
            tasks = (await s.execute(
                select(FollowupTask).where(FollowupTask.status == "SCHEDULED", FollowupTask.due_at <= now)
                .order_by(FollowupTask.due_at).limit(BATCH).with_for_update(skip_locked=True)
            )).scalars().all()
            for t in tasks:
                lead = await s.get(Lead, t.lead_id)
                cust = await s.get(Customer, lead.customer_id) if lead else None
                reason = None
                if not lead or lead.closed_at is not None:
                    reason = "lead_closed"
                elif cust and cust.opted_out:
                    reason = "opted_out"
                elif t.kind == "no_reply" and lead.last_customer_msg_at and lead.last_customer_msg_at > t.created_at:
                    reason = "customer_replied"
                if reason:
                    t.status, t.skip_reason, t.completed_at = "SKIPPED", reason, now
                    if lead:
                        await L.refresh_next_followup(s, lead)
                    continue
                t.status = "QUEUED"
                # same transaction as the status change: a task can no longer be QUEUED without its event
                await self.outbox.add(s, Topics.FOLLOWUP_DUE, Envelope(
                    event_id=str(uuid.uuid5(_NS, f"due:{t.id}:{t.attempt}")), event_type=Topics.FOLLOWUP_DUE, tenant_id=tid,
                    payload=FollowupDuePayload(task_id=t.id, lead_id=t.lead_id, rule_id=t.rule_id).model_dump()))
                claimed += 1
        return claimed

    async def holding_replies(self, b: TenantBundle, now: datetime | None = None) -> int:
        cfg = b.config
        if cfg.approvals.on_timeout != "send_holding_reply":
            return 0
        now = now or datetime.now(timezone.utc)
        out = 0
        async with self.outbox.session(b.tenant_id) as s:
            rows = (await s.execute(
                select(Approval).where(Approval.status == "PENDING", Approval.kind == "reply", Approval.holding_sent_at.is_(None),
                                       Approval.sla_due_at.is_not(None), Approval.sla_due_at <= now)
                .limit(BATCH).with_for_update(skip_locked=True)
            )).scalars().all()
            for a in rows:
                a.holding_sent_at = now
                cust = await s.get(Customer, a.customer_id) if a.customer_id else None
                line = self.routing.line_by_id(a.whatsapp_number_id) if a.whatsapp_number_id else None
                if not cust or not line or cust.opted_out:
                    continue
                last_in = await s.scalar(select(Conversation.last_inbound_at).where(
                    Conversation.whatsapp_number_id == a.whatsapp_number_id, Conversation.customer_id == cust.id))
                if not last_in or now - last_in > timedelta(hours=24):
                    continue
                await self.outbox.add(s, Topics.OUTBOUND_REQUESTED, outbound_envelope(
                    b.tenant_id, line, cust.phone, cfg.agent.holding_reply, requested_by="system:sla", sent_by="system",
                    request_key=f"holding:{a.id}", lead_id=a.lead_id, customer_id=cust.id))
                out += 1
        return out

    async def dormant(self, b: TenantBundle) -> int:
        async with tenant_session(self.sm, b.tenant_id) as s:
            return await L.dormant_sweep(s, b.config)

    async def rollup(self, b: TenantBundle) -> None:
        today = L.today_local(b.config)
        async with tenant_session(self.sm, b.tenant_id) as s:
            for day in (today - timedelta(days=1), today):
                await upsert_day(s, b.tenant_id, day, await compute_day(s, day, b.config.tenant.timezone))

    async def tick(self) -> None:
        bundles = self.routing.bundles.all()
        do_dormant = self._every("dormant", 600)
        do_rollup = self._every("rollup", 300)
        for b in bundles:
            try:
                n = await self.claim_due(b)
                h = await self.holding_replies(b)
                d = await self.dormant(b) if do_dormant else 0
                if do_rollup:
                    await self.rollup(b)
                if n or h or d:
                    log.info("tick", extra={"ctx": {"tenant": b.slug, "due": n, "holding": h, "dormant": d}})
            except Exception:  # noqa: BLE001 - one tenant must not stop the others
                log.exception(f"tick failed for {b.slug}")


async def relay_loop(sched: Scheduler, stop: asyncio.Event, interval: float) -> None:
    """Publishes outbox rows that the producers' fast path did not (Kafka down, crash after commit)."""
    last_cleanup = 0.0
    while not stop.is_set():
        tenant_ids = [b.tenant_id for b in sched.routing.bundles.all()]
        try:
            await ob.relay_once(sched.sm, sched.bus, tenant_ids)
            if time.monotonic() - last_cleanup >= 3600:
                last_cleanup = time.monotonic()
                o, p = await ob.cleanup(sched.sm, tenant_ids)
                if o or p:
                    log.info("retention cleanup", extra={"ctx": {"operation": "outbox.cleanup", "outbox_deleted": o,
                                                                 "processed_deleted": p}})
        except Exception:  # noqa: BLE001 - keep relaying
            log.exception("relay loop iteration failed")
        try:
            await asyncio.wait_for(stop.wait(), interval)
        except asyncio.TimeoutError:
            pass


async def main() -> None:
    settings = get_settings()
    bus = KafkaEventBus(settings.kafka_bootstrap_servers, "followup-scheduler")
    routing = TenantRouting(settings.tenant_routing_path)
    sched = Scheduler(make_sessionmaker(settings.database_url, pool_size=3), bus, routing)
    interval = float(os.environ.get("FOLLOWUP_TICK_SECONDS", "30"))
    stop = asyncio.Event()
    await bus.start()
    install_shutdown(stop.set, log)
    log.info(f"followup-scheduler running every {interval}s")
    relay = asyncio.create_task(relay_loop(sched, stop, float(os.environ.get("OUTBOX_RELAY_SECONDS", "2"))))
    try:
        while not stop.is_set():
            routing.reload()
            await sched.tick()
            heartbeat(force=True)
            try:
                await asyncio.wait_for(stop.wait(), interval)
            except asyncio.TimeoutError:
                pass
    finally:
        stop.set()
        await relay
        await bus.stop()


if __name__ == "__main__":
    asyncio.run(main())
