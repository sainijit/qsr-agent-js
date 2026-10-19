"""Mandatory consumer contract (spec §4):

1. Missing / mismatched `tenant_id` header -> DLQ (+ audit hook).
2. Handlers open a tenant-scoped DB session themselves (`tenant_session`) so RLS is active.
3. Skip already-processed `event_id` (Redis, 24h TTL). Handlers with non-idempotent side effects
   additionally use the durable ledger (`processed.first_delivery`) inside their own transaction.
4. Transient errors are retried with exponential backoff + jitter; permanent errors (bad data, constraint
   violations, `PermanentError`) are not. Exhausted / permanent -> `<topic>.DLQ` with the original
   headers plus DLQ metadata (+ audit hook).
5. A DLQ'd event is *not* marked processed, so a controlled replay (`python -m zuvo_common.dlq`) can
   process it again once the cause is fixed. Replays carry `replay_for_group`; other groups ignore them.

Offsets are committed by the bus only after this returns (at-least-once).
"""
from __future__ import annotations

import asyncio
import json
import random
import time
from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional

from pydantic import ValidationError
from sqlalchemy.exc import DataError, IntegrityError, ProgrammingError

from .bus import EventBus, Record
from .events import Envelope, Headers, Topics
from .idempotency import IdempotencyStore
from .logging import bind, get_logger
from .tracing import consume_context

EnvelopeHandler = Callable[[Envelope, Record], Awaitable[None]]
AuditHook = Callable[[Optional[str], str, dict], Awaitable[None]]


class PermanentError(Exception):
    """Raise from a handler to skip retries and go straight to DLQ."""


# Errors that will fail identically on every attempt: malformed payloads, programming errors, constraint
# violations. Everything else (DB/Kafka/HTTP connectivity, timeouts, deadlocks) is treated as transient.
PERMANENT_ERRORS: tuple[type[BaseException], ...] = (
    PermanentError, ValidationError, KeyError, TypeError, ValueError, LookupError, IntegrityError, DataError, ProgrammingError,
)


def is_permanent(exc: BaseException) -> bool:
    return isinstance(exc, PERMANENT_ERRORS)


def retry_delay(attempt: int, base: float, cap: float = 30.0) -> float:
    """Equal-jitter exponential backoff for attempt 0, 1, 2 ... (avoids synchronized retry storms)."""
    d = min(cap, base * (2 ** attempt))
    return d / 2 + random.uniform(0, d / 2)


class ConsumerContract:
    def __init__(
        self,
        bus: EventBus,
        idempotency: IdempotencyStore,
        group_id: str,
        handler: EnvelopeHandler,
        audit: Optional[AuditHook] = None,
        authorized_tenants: Optional[Callable[[str], bool]] = None,
        retries: int = 3,
        backoff_seconds: float = 0.5,
        ttl_seconds: int = 86400,
    ):
        self.bus = bus
        self.idem = idempotency
        self.group_id = group_id
        self.handler = handler
        self.audit = audit
        self.authorized = authorized_tenants
        self.retries = retries
        self.backoff = backoff_seconds
        self.ttl = ttl_seconds
        self.log = get_logger(f"consumer.{group_id}")

    async def _dlq(self, record: Record, tenant_id: Optional[str], error: str, *,
                   error_class: str = "ContractViolation", attempts: int = 0) -> None:
        meta = {
            Headers.ERROR: error[:500],
            Headers.ORIGINAL_TOPIC: record.topic,
            Headers.DLQ_GROUP: self.group_id,
            Headers.DLQ_ERROR_CLASS: error_class,
            Headers.DLQ_ATTEMPTS: str(attempts),
            Headers.DLQ_FAILED_AT: datetime.now(timezone.utc).isoformat(),
        }
        if record.partition is not None:
            meta[Headers.DLQ_PARTITION] = str(record.partition)
        if record.offset is not None:
            meta[Headers.DLQ_OFFSET] = str(record.offset)
        headers = [(k, v.encode()) for k, v in record.headers.items() if k not in Headers.DLQ_HEADERS]
        headers += [(k, v.encode()) for k, v in meta.items()]
        await self.bus.publish_raw(Topics.dlq(record.topic), record.value, record.key, headers)
        self.log.error("event sent to DLQ", extra={"ctx": {
            "topic": record.topic, "error": error[:200], "tenant_id": tenant_id, "error_code": error_class,
            "attempts": attempts, "outcome": "dlq"}})
        if self.audit:
            try:
                await self.audit(tenant_id, "EVENT_DLQ", {"topic": record.topic, "group": self.group_id, "error": error[:500],
                                                          "error_class": error_class, "attempts": attempts})
            except Exception:
                self.log.exception("audit hook failed")

    async def __call__(self, record: Record) -> None:
        target = record.headers.get(Headers.REPLAY_FOR_GROUP)
        if target and target != self.group_id:
            return  # a DLQ replay aimed at another consumer group of this topic
        header_tenant = record.headers.get(Headers.TENANT_ID)
        if not header_tenant:
            await self._dlq(record, None, "missing tenant_id header")
            return
        if self.authorized and not self.authorized(header_tenant):
            await self._dlq(record, header_tenant, "tenant not authorized for consumer")
            return
        try:
            envelope = Envelope.model_validate_json(record.value)
        except (ValidationError, json.JSONDecodeError) as exc:
            await self._dlq(record, header_tenant, f"invalid envelope: {exc.__class__.__name__}")
            return
        if envelope.tenant_id != header_tenant:
            await self._dlq(record, header_tenant, "tenant_id header/body mismatch")
            return

        with bind(tenant_id=envelope.tenant_id, wamid=envelope.wamid, event_type=envelope.event_type,
                  operation=f"{self.group_id}:{envelope.event_type}", **consume_context(record.headers, envelope.event_id)):
            await self._process(envelope, record)

    async def _process(self, envelope: Envelope, record: Record) -> None:
        key = f"zuvo:evt:{self.group_id}:{envelope.event_id}"
        if await self.idem.seen(key):
            self.log.info("event skipped", extra={"ctx": {"outcome": "duplicate"}})
            return

        started = time.perf_counter()
        last_error, error_class, attempts = "", "", 0
        for attempt in range(self.retries):
            attempts = attempt + 1
            try:
                await self.handler(envelope, record)
                await self.idem.claim(key, self.ttl)
                self.log.info("event handled", extra={"ctx": {
                    "outcome": "ok", "attempts": attempts, "duration_ms": int((time.perf_counter() - started) * 1000)}})
                return
            except Exception as exc:  # noqa: BLE001
                error_class = exc.__class__.__name__
                last_error = f"{error_class}: {exc}"
                if is_permanent(exc):
                    last_error = f"permanent: {last_error}"
                    break
                if attempts < self.retries:
                    delay = retry_delay(attempt, self.backoff)
                    self.log.warning("handler failed, retrying", extra={"ctx": {
                        "attempts": attempts, "outcome": "retry", "error_code": error_class, "error": last_error[:200],
                        "retry_in_ms": int(delay * 1000)}})
                    await asyncio.sleep(delay)
        await self._dlq(record, envelope.tenant_id, last_error, error_class=error_class, attempts=attempts)
        self.log.info("event handled", extra={"ctx": {
            "outcome": "dlq", "attempts": attempts, "error_code": error_class,
            "duration_ms": int((time.perf_counter() - started) * 1000)}})
