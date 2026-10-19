"""Ingestion core: resolve tenant -> load policy -> dedupe wamid -> publish. No DB access."""
from __future__ import annotations

import uuid
from typing import Any, Optional

from zuvo_common.bus import EventBus
from zuvo_common.events import Envelope, InboundMessagePayload, MessageStatusPayload, Topics
from zuvo_common.idempotency import IdempotencyStore
from zuvo_common.logging import get_logger
from zuvo_common.phone import normalize_phone
from zuvo_common.tenancy import TenantPolicyLoader, TenantRouting

log = get_logger("gateway.ingest")
# stable event ids: a Meta webhook retry of the same wamid/status maps to the same event_id, so downstream
# dedupe still works even if the gateway Redis key was lost
_NS = uuid.UUID("0f6a2d1e-3c5b-4a79-9e8d-7b6c5a4f3e21")


class PublishError(Exception):
    """Event could not be published; caller must return non-200 so Meta retries."""


class IngestStats(dict):
    def bump(self, key: str) -> None:
        self[key] = self.get(key, 0) + 1


class Ingestor:
    def __init__(
        self,
        routing: TenantRouting,
        policies: TenantPolicyLoader,
        bus: EventBus,
        idempotency: IdempotencyStore,
        ttl_seconds: int = 86400,
    ):
        self.routing = routing
        self.policies = policies
        self.bus = bus
        self.idem = idempotency
        self.ttl = ttl_seconds

    async def ingest(self, payload: dict[str, Any]) -> IngestStats:
        stats = IngestStats()
        for entry in payload.get("entry") or []:
            for change in entry.get("changes") or []:
                if change.get("field", "messages") != "messages":
                    stats.bump("skipped_field")
                    continue
                value = change.get("value") or {}
                messages = value.get("messages") or []
                statuses = value.get("statuses") or []
                if not messages and not statuses:
                    continue
                meta = value.get("metadata") or {}
                line = self.routing.resolve(meta.get("phone_number_id"), meta.get("display_phone_number"))
                if line and statuses:
                    for st in statuses:
                        await self._status(line, st, stats)
                if not messages:
                    continue
                if not line:
                    log.warning("no active tenant line for phone_number_id", extra={"ctx": {"pnid": meta.get("phone_number_id")}})
                    stats["unrouted"] = stats.get("unrouted", 0) + len(messages)
                    continue
                policy_ok = self._policy_ok(line.skill_file, line.tenant_id)
                names = {
                    c.get("wa_id"): ((c.get("profile") or {}).get("name"))
                    for c in value.get("contacts") or []
                }
                for msg in messages:
                    await self._one(line, msg, names, policy_ok, stats)
        return stats

    def _policy_ok(self, skill_file: str, tenant_id: str) -> bool:
        try:
            self.policies.load(skill_file, tenant_id)
            return True
        except Exception as exc:  # noqa: BLE001 - never drop a message because of a policy file
            log.error("tenant policy failed to load", extra={"ctx": {"tenant_id": tenant_id, "error": str(exc)[:200]}})
            return False

    async def _status(self, line, st: dict, stats: IngestStats) -> None:
        wamid, status = st.get("id"), st.get("status")
        if not wamid or status not in ("sent", "delivered", "read", "failed", "deleted"):
            stats.bump("invalid_status")
            return
        key = f"zuvo:status:{line.tenant_id}:{wamid}:{status}"
        if not await self.idem.claim(key, self.ttl):
            stats.bump("duplicate_statuses")
            return
        errors = st.get("errors") or []
        payload = MessageStatusPayload(
            wamid=wamid, status=status, timestamp=st.get("timestamp"), recipient_phone=st.get("recipient_id"),
            error=(errors[0].get("title") or errors[0].get("message")) if errors else None,
            callback_data=(st.get("biz_opaque_callback_data") or None) and str(st["biz_opaque_callback_data"])[:512],
        )
        try:
            await self.bus.publish(Topics.MESSAGE_STATUS, Envelope(
                event_id=str(uuid.uuid5(_NS, f"status:{line.tenant_id}:{wamid}:{status}")),
                event_type=Topics.MESSAGE_STATUS, tenant_id=line.tenant_id, wamid=wamid, payload=payload.model_dump()))
        except Exception as exc:
            # receipts drive delivery state (Phase 7): let Meta retry; the stable event id makes a retry harmless
            await self.idem.release(key)
            raise PublishError(str(exc)) from exc
        stats.bump("statuses")

    async def _one(self, line, msg: dict, names: dict, policy_ok: bool, stats: IngestStats) -> Optional[str]:
        wamid = msg.get("id")
        sender = msg.get("from")
        if not wamid or not sender:
            stats.bump("invalid")
            return None
        try:
            customer_phone = normalize_phone(sender)
        except ValueError:
            stats.bump("invalid")
            return None

        dedupe_key = f"zuvo:wamid:{line.tenant_id}:{wamid}"
        if not await self.idem.claim(dedupe_key, self.ttl):
            stats.bump("duplicates")
            return None

        mtype = msg.get("type", "text")
        media = msg.get(mtype) if mtype in ("audio", "voice", "image", "document", "video") else None
        text = (msg.get("text") or {}).get("body") if mtype == "text" else (media or {}).get("caption")
        payload = InboundMessagePayload(
            worker_key=line.worker_key,
            worker_role=line.role.value,
            whatsapp_number_id=line.whatsapp_number_id,
            meta_phone_number_id=line.meta_phone_number_id,
            display_phone_number=line.whatsapp_number,
            customer_phone=customer_phone,
            customer_name=names.get(sender),
            message_type=mtype,
            text=text,
            media_id=(media or {}).get("id"),
            media_mime_type=(media or {}).get("mime_type"),
            meta_timestamp=msg.get("timestamp"),
            tenant_policy=line.skill_file if policy_ok else None,
            referral=msg.get("referral") or None,
        )
        envelope = Envelope(
            event_id=str(uuid.uuid5(_NS, f"inbound:{line.tenant_id}:{wamid}")),
            event_type=Topics.INBOUND_RECEIVED,
            tenant_id=line.tenant_id,
            worker_id=line.worker_id,
            wamid=wamid,
            payload=payload.model_dump(),
        )
        try:
            await self.bus.publish(Topics.INBOUND_RECEIVED, envelope)
        except Exception as exc:
            await self.idem.release(dedupe_key)
            raise PublishError(str(exc)) from exc
        stats.bump("published")
        return wamid
