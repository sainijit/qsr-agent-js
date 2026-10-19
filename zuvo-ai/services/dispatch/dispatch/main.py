"""Dispatch: outbound.reply.requested -> Meta Graph API -> outbound.message.sent.

Durable per-request state lives in `outbound_messages` (see zuvo_common.outbound_state and
docs/outbound-delivery.md). Delivery to WhatsApp is at-most-once per request for ambiguous outcomes and
at-least-once only where Meta provably did not process the request. It is not exactly-once.
"""
from __future__ import annotations

import asyncio
import os
import random
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional, Protocol

import httpx
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from zuvo_common import outbound_state as ost
from zuvo_common.bus import EventBus, KafkaEventBus, Record
from zuvo_common.contract import ConsumerContract, PermanentError
from zuvo_common.db import make_sessionmaker, tenant_session
from zuvo_common.events import Envelope, OutboundRequestPayload, TemplateSpec, Topics
from zuvo_common.idempotency import IdempotencyStore, RedisIdempotencyStore
from zuvo_common.logging import get_logger, mask_phone
from zuvo_common.outbox import Outbox
from zuvo_common.runtime import install_shutdown
from zuvo_common.models import Conversation, Customer
from zuvo_common.settings import Settings, get_settings
from zuvo_common.tenancy import TenantRouting

log = get_logger("dispatch")
_NS = uuid.UUID("3e2f9c1a-7b6d-4e5f-9a8b-1c2d3e4f5a6b")
SERVICE_WINDOW = timedelta(hours=24)
MAX_ATTEMPTS = 5            # dispatch-level attempts for provably-unsent requests (each may do 3 quick HTTP tries)
RETRY_BASE_S, RETRY_CAP_S = 30.0, 600.0
SENDING_STALE_S = 120       # > worst-case client time (3 x (5 s connect + 10 s) + backoff)


@dataclass
class SendResult:
    success: bool
    wamid: Optional[str]
    dry_run: bool
    error: Optional[str] = None
    # accepted | not_sent (provably not delivered to Meta / Meta said retry) | rejected (permanent 4xx)
    # | ambiguous (may have reached Meta) | policy (never attempted)
    category: str = "accepted"


class MessagingClient(Protocol):
    async def send_text(self, meta_phone_number_id: str, to: str, text: str, callback: Optional[str] = None) -> SendResult: ...

    async def send_template(self, meta_phone_number_id: str, to: str, template: TemplateSpec,
                            callback: Optional[str] = None) -> SendResult: ...


RETRY_HTTP = {429, 503}
# Graph API codes that mean "not processed, try later": 2 temporarily unavailable, 4/80007 app/WABA rate limit,
# 130429 throughput, 131056 pair rate limit
RETRY_META_CODES = {2, 4, 80007, 130429, 131056}


def _meta_code(r: httpx.Response) -> Optional[int]:
    try:
        code = (r.json().get("error") or {}).get("code")
        return int(code) if code is not None else None
    except (ValueError, TypeError, AttributeError):
        return None


class MetaWhatsAppClient:
    def __init__(self, settings: Settings, retries: int = 3, backoff: float = 1.0):
        self.s, self.retries, self.backoff = settings, retries, backoff
        self.dry_run = settings.meta_dry_run or not settings.meta_system_user_token or settings.meta_system_user_token.startswith("mock_")

    async def _post(self, meta_phone_number_id: str, to: str, body: dict) -> SendResult:
        if self.dry_run:
            log.info("[DRY RUN] outbound message", extra={"ctx": {"to": mask_phone(to), "type": body.get("type")}})
            return SendResult(True, f"wamid.DRYRUN.{uuid.uuid4().hex}", True)
        if not meta_phone_number_id.isdigit():
            # simulator line (PNID_*): it can never reach Meta, so treat it as a dry run even when live sending is on
            log.info("[SIMULATED LINE] outbound message", extra={"ctx": {"to": mask_phone(to), "line": meta_phone_number_id}})
            return SendResult(True, f"wamid.SIM.{uuid.uuid4().hex}", True)
        url = f"{self.s.meta_graph_base_url}/{self.s.meta_api_version}/{meta_phone_number_id}/messages"
        payload = {"messaging_product": "whatsapp", "recipient_type": "individual", "to": to, **body}
        error = "unknown"
        for attempt in range(self.retries):
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(10, connect=5)) as client:
                    r = await client.post(url, headers={"Authorization": f"Bearer {self.s.meta_system_user_token}"}, json=payload)
            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
                error = f"transport: {exc.__class__.__name__}"  # no connection => the request never reached Meta
            except httpx.TransportError as exc:
                # read/write timeout, reset, protocol error: the request may have reached Meta -> never retry blindly
                return SendResult(False, None, False, f"transport: {exc.__class__.__name__}", "ambiguous")
            else:
                if r.status_code in (200, 201):
                    try:
                        wamid = (r.json().get("messages") or [{}])[0].get("id")
                    except (ValueError, AttributeError, IndexError):
                        wamid = None
                    return SendResult(True, wamid, False)
                code = _meta_code(r)
                error = f"meta_http_{r.status_code}" + (f":{code}" if code is not None else "")
                if r.status_code in RETRY_HTTP or code in RETRY_META_CODES:
                    pass  # explicitly not processed: safe to retry
                elif r.status_code >= 500:
                    return SendResult(False, None, False, error, "ambiguous")
                else:
                    return SendResult(False, None, False, error, "rejected")
            if attempt + 1 < self.retries:
                d = self.backoff * (2 ** attempt)
                await asyncio.sleep(d / 2 + random.uniform(0, d / 2))
        return SendResult(False, None, False, error, "not_sent")

    @staticmethod
    def _with_callback(body: dict, callback: Optional[str]) -> dict:
        # Meta echoes biz_opaque_callback_data in status webhooks: lets an UNKNOWN send be matched to its receipt
        return {**body, "biz_opaque_callback_data": callback} if callback else body

    async def send_text(self, meta_phone_number_id: str, to: str, text: str, callback: Optional[str] = None) -> SendResult:
        return await self._post(meta_phone_number_id, to, self._with_callback({"type": "text", "text": {"body": text}}, callback))

    async def send_template(self, meta_phone_number_id: str, to: str, template: TemplateSpec,
                            callback: Optional[str] = None) -> SendResult:
        tpl: dict = {"name": template.name, "language": {"code": template.language}}
        if template.body_params:
            tpl["components"] = [{"type": "body", "parameters": [{"type": "text", "text": v} for v in template.body_params]}]
        return await self._post(meta_phone_number_id, to, self._with_callback({"type": "template", "template": tpl}, callback))


def retry_after_seconds(attempts: int) -> float:
    d = min(RETRY_CAP_S, RETRY_BASE_S * (2 ** max(0, attempts - 1)))
    return d / 2 + random.uniform(0, d / 2)


class Dispatcher:
    """Owns `outbound_messages`. The PENDING->SENDING transition is committed *before* calling Meta and is the
    only path to a send, so neither a redelivery nor a second replica can send the same request twice."""

    def __init__(self, sm: async_sessionmaker[AsyncSession], bus: EventBus, routing: TenantRouting, client: MessagingClient,
                 idem: Optional[IdempotencyStore] = None):
        self.sm, self.bus, self.routing, self.client = sm, bus, routing, client
        self.outbox = Outbox(sm, bus)

    async def _recipient_state(self, s: AsyncSession, number_id: str, phone: str):
        """(within 24h customer-service window, opted out, customer_id, conversation_id)."""
        cust = (await s.execute(select(Customer).where(Customer.phone == phone))).scalar_one_or_none()
        if not cust:
            return False, False, None, None
        conv = (await s.execute(select(Conversation.id, Conversation.last_inbound_at).where(
            Conversation.whatsapp_number_id == number_id, Conversation.customer_id == cust.id))).first()
        last_in = conv.last_inbound_at if conv else None
        within = bool(last_in and datetime.now(timezone.utc) - last_in <= SERVICE_WINDOW)
        return within, bool(getattr(cust, "opted_out", False)), cust.id, (conv.id if conv else None)

    async def handle(self, env: Envelope, _record: Record) -> None:
        p = OutboundRequestPayload.model_validate(env.payload)
        line = self.routing.line_by_id(p.whatsapp_number_id)
        if not line or line.tenant_id != env.tenant_id:
            raise PermanentError("sender line does not belong to tenant")
        if not p.approved:
            raise PermanentError("outbound request is not approved")
        await self.attempt(env.tenant_id, p)

    async def attempt(self, tenant_id: str, p: OutboundRequestPayload) -> str:
        """Run one send attempt if the durable state allows it. Returns the resulting state."""
        oid = ost.outbound_id(tenant_id, p.request_id)
        async with self.outbox.session(tenant_id) as s:
            await s.execute(text(
                "INSERT INTO outbound_messages (id, tenant_id, request_id, whatsapp_number_id, recipient_phone, kind, "
                "template_name, sent_by, followup_task_id, status, request) VALUES (:id, CAST(:t AS uuid), :rid, "
                "CAST(:wn AS uuid), :to, :kind, :tpl, :by, CAST(:ft AS uuid), 'PENDING', CAST(:req AS jsonb)) "
                "ON CONFLICT (tenant_id, request_id) DO NOTHING"),
                {"id": oid, "t": tenant_id, "rid": p.request_id, "wn": p.whatsapp_number_id, "to": p.recipient_phone,
                 "kind": "template" if p.template else "text", "tpl": p.template.name if p.template else None,
                 "by": p.sent_by, "ft": p.followup_task_id, "req": p.model_dump_json()})
            row = await ost.get(s, oid, lock=True)
            if row.status in ost.TERMINAL:
                await self._emit(s, tenant_id, row, p)  # redelivery: make sure the result event exists (idempotent)
                return row.status
            if row.status == "SENDING":
                return row.status  # in flight elsewhere, or crashed mid-send: the sweeper turns stale SENDING into UNKNOWN
            if row.next_attempt_at and row.next_attempt_at > datetime.now(timezone.utc):
                return row.status  # backing off; the sweeper owns the retry
            within, opted_out, cust_id, conv_id = await self._recipient_state(s, p.whatsapp_number_id, p.recipient_phone)
            policy = ("customer_opted_out" if opted_out and p.sent_by != "human"
                      else None if p.template is not None or within else "outside_24h_window_template_required")
            if policy:
                await s.execute(text(
                    "UPDATE outbound_messages SET status = 'FAILED', last_error_category = 'policy', last_error = :e, "
                    "failed_at = now(), request = NULL, next_attempt_at = NULL, customer_id = :c, conversation_id = :cv, "
                    "updated_at = now() WHERE id = :id"), {"id": oid, "e": policy, "c": cust_id, "cv": conv_id})
                await self._emit(s, tenant_id, await ost.get(s, oid), p)
                self._log(tenant_id, p, "FAILED", "policy", policy, False)
                return "FAILED"
            await s.execute(text(
                "UPDATE outbound_messages SET status = 'SENDING', attempts = attempts + 1, sending_at = now(), "
                "next_attempt_at = NULL, customer_id = :c, conversation_id = :cv, updated_at = now() WHERE id = :id"),
                {"id": oid, "c": cust_id, "cv": conv_id})
        # committed: from here on a crash leaves SENDING, which can only become UNKNOWN - never a second send

        line = self.routing.line_by_id(p.whatsapp_number_id)
        try:
            if p.template is not None:
                result = await self.client.send_template(line.meta_phone_number_id, p.recipient_phone, p.template, callback=oid)
            else:
                result = await self.client.send_text(line.meta_phone_number_id, p.recipient_phone, p.text, callback=oid)
        except Exception as exc:  # noqa: BLE001 - an unexpected client bug must not leave the outcome unrecorded
            result = SendResult(False, None, False, f"client: {exc.__class__.__name__}", "ambiguous")

        async with self.outbox.session(tenant_id) as s:
            row = await ost.get(s, oid, lock=True)
            if row.status in ("SENDING", "UNKNOWN"):
                state, sets, params = self._outcome(row, result)
                if sets:
                    await s.execute(text(f"UPDATE outbound_messages SET {sets}, updated_at = now() WHERE id = :id"),
                                    {"id": oid, **params})
            row = await ost.get(s, oid)
            await self._emit(s, tenant_id, row, p, dry_run=result.dry_run)
        self._log(tenant_id, p, row.status, result.category, result.error, result.dry_run)
        return row.status

    @staticmethod
    def _outcome(row, r: SendResult) -> tuple[str, str, dict]:
        if r.success:
            return "ACCEPTED", ("status = 'ACCEPTED', wamid = COALESCE(:w, wamid), accepted_at = COALESCE(accepted_at, now()), "
                                "last_error = NULL, last_error_category = NULL, request = NULL"), {"w": r.wamid}
        err = (r.error or r.category)[:200]
        if row.status == "UNKNOWN":
            return "UNKNOWN", "", {}  # already flagged by the sweeper; a failure here proves nothing more
        if r.category == "not_sent" and row.attempts < MAX_ATTEMPTS:
            return "PENDING", ("status = 'PENDING', last_error_category = 'not_sent', last_error = :e, "
                               "next_attempt_at = now() + make_interval(secs => :d)"), {"e": err, "d": retry_after_seconds(row.attempts)}
        if r.category == "not_sent":
            return "FAILED", ("status = 'FAILED', last_error_category = 'retry_exhausted', last_error = :e, failed_at = now(), "
                              "request = NULL"), {"e": err}
        if r.category == "rejected":
            return "FAILED", ("status = 'FAILED', last_error_category = 'rejected', last_error = :e, failed_at = now(), "
                              "request = NULL"), {"e": err}
        return "UNKNOWN", "status = 'UNKNOWN', last_error_category = 'ambiguous', last_error = :e", {"e": f"delivery_unknown: {err}"[:200]}

    async def _emit(self, s: AsyncSession, tenant_id: str, row, p: OutboundRequestPayload, dry_run: Optional[bool] = None) -> None:
        if row is None or row.status not in ost.TERMINAL:
            return
        line = self.routing.line_by_id(p.whatsapp_number_id)
        await self.outbox.add(s, Topics.OUTBOUND_SENT, ost.sent_envelope(tenant_id, row, p, line.worker_id if line else None, dry_run))

    @staticmethod
    def _log(tenant_id: str, p: OutboundRequestPayload, state: str, category: str, error: Optional[str], dry_run: bool) -> None:
        log.info("dispatched", extra={"ctx": {
            "tenant_id": tenant_id, "sent_by": p.sent_by, "template": bool(p.template), "state": state, "category": category,
            "success": state == "ACCEPTED", "dry_run": dry_run, "error": error, "operation": "meta.send",
            "outcome": {"ACCEPTED": "sent", "PENDING": "retry", "UNKNOWN": "unknown"}.get(state, "failed")}})

    async def sweep(self, tenant_ids, batch: int = 20) -> dict:
        """Stale SENDING -> UNKNOWN (never resent); due PENDING -> one more attempt."""
        stats = {"unknown": 0, "retried": 0}
        for tid in tenant_ids:
            try:
                async with self.outbox.session(tid) as s:
                    stale = (await s.execute(text(
                        "UPDATE outbound_messages SET status = 'UNKNOWN', last_error_category = 'interrupted', "
                        "last_error = 'delivery_unknown: interrupted during send', updated_at = now() "
                        "WHERE status = 'SENDING' AND sending_at < now() - make_interval(secs => :age) RETURNING id, request"),
                        {"age": SENDING_STALE_S})).all()
                    for r in stale:
                        if r.request:
                            await self._emit(s, tid, await ost.get(s, str(r.id)), OutboundRequestPayload.model_validate(r.request))
                        log.warning("outbound outcome unknown (interrupted)", extra={"ctx": {
                            "tenant_id": tid, "outbound_id": str(r.id), "operation": "meta.send", "outcome": "unknown"}})
                    stats["unknown"] += len(stale)
                async with tenant_session(self.sm, tid) as s:
                    due = (await s.execute(text(
                        "SELECT request FROM outbound_messages WHERE status = 'PENDING' AND next_attempt_at <= now() "
                        "AND request IS NOT NULL ORDER BY next_attempt_at LIMIT :n"), {"n": batch})).scalars().all()
                for req in due:
                    await self.attempt(tid, OutboundRequestPayload.model_validate(req))
                    stats["retried"] += 1
            except Exception:  # noqa: BLE001 - one tenant must not stop the sweep
                log.exception("outbound sweep failed", extra={"ctx": {"tenant_id": tid, "operation": "outbound.sweep"}})
        return stats


async def sweep_loop(dispatcher: Dispatcher, routing: TenantRouting, every: float) -> None:
    while True:
        await asyncio.sleep(every)
        await dispatcher.sweep([str(t.tenant_id) for t in routing.config.tenants])


async def main() -> None:
    settings = get_settings()
    bus = KafkaEventBus(settings.kafka_bootstrap_servers, "dispatch")
    idem = RedisIdempotencyStore(settings.redis_url)
    routing = TenantRouting(settings.tenant_routing_path)
    client = MetaWhatsAppClient(settings)
    log.info(f"dispatch starting (dry_run={client.dry_run})")
    dispatcher = Dispatcher(make_sessionmaker(settings.database_url), bus, routing, client, idem)
    await bus.start()
    install_shutdown(bus.request_stop, log)
    contract = ConsumerContract(bus, idem, "dispatch", dispatcher.handle, authorized_tenants=lambda tid: routing.tenant(tid) is not None)
    sweeper = asyncio.create_task(sweep_loop(dispatcher, routing, float(os.environ.get("DISPATCH_SWEEP_SECONDS", "10"))))
    try:
        await bus.consume([Topics.OUTBOUND_REQUESTED], "dispatch", contract)
    finally:
        sweeper.cancel()
        await bus.stop()


if __name__ == "__main__":
    asyncio.run(main())
