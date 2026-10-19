"""Transactional outbox (Phase 6.1).

Producers that change PostgreSQL *and* must emit an event add the event to `outbox_events` in the
same transaction (`Outbox.session()` + `Outbox.add()`). After commit the same process tries to
publish immediately (fast path). Anything that did not make it, for example because Kafka was
down or the process crashed between commit and publish, is published later by `relay_once()`,
which runs in followup-scheduler.

Guarantees and non-guarantees:
* At-least-once. A crash after publishing but before `published_at` is written republishes the same
  bytes with the same `event_id`; consumers dedupe on it. Not exactly-once.
* Per-tenant FIFO by `created_at`. On the first failure in a batch the publisher stops, so later rows for
  the same key cannot overtake an earlier one.
* Rows are read inside `tenant_session` (RLS). The publisher also re-checks that the stored envelope
  and the `tenant_id` header belong to the tenant it is publishing for. A mismatch is never published.
"""
from __future__ import annotations

import asyncio
import json
import random
from contextlib import asynccontextmanager
from typing import AsyncIterator, Iterable, Optional, Sequence

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .bus import EventBus
from .db import tenant_session
from .events import Envelope, Headers
from .logging import get_logger
from .tracing import outgoing_headers

log = get_logger("outbox")

PUBLISH_TIMEOUT_S = 10.0
MAX_BACKOFF_S = 300.0
_PENDING = "zuvo_outbox_ids"


def backoff_seconds(attempts: int, base: float = 1.0, cap: float = MAX_BACKOFF_S) -> float:
    """Exponential backoff with 'equal jitter': half fixed, half random. Never 0, never above cap."""
    d = min(cap, base * (2 ** max(0, attempts - 1)))
    return d / 2 + random.uniform(0, d / 2)


async def enqueue(s: AsyncSession, topic: str, env: Envelope) -> str:
    """Insert an event in the caller's transaction. Idempotent on `event_id` (stable ids => redelivery-safe)."""
    headers = [[k, v.decode()] for k, v in env.headers() + outgoing_headers(env.event_id)]
    await s.execute(
        # clock_timestamp(), not now(): events enqueued in one transaction must keep their enqueue order
        text("INSERT INTO outbox_events (id, tenant_id, topic, msg_key, payload, headers, created_at) "
             "VALUES (:id, CAST(:t AS uuid), :topic, :key, :payload, CAST(:headers AS jsonb), clock_timestamp()) "
             "ON CONFLICT (id) DO NOTHING"),
        {"id": env.event_id, "t": env.tenant_id, "topic": topic, "key": env.key,
         "payload": env.model_dump_json(), "headers": json.dumps(headers)},
    )
    s.info.setdefault(_PENDING, []).append(env.event_id)
    return env.event_id


def _belongs(tenant_id: str, payload: str, headers: list) -> bool:
    try:
        body_tenant = json.loads(payload).get("tenant_id")
    except (ValueError, AttributeError):
        return False
    header_tenant = next((v for k, v in headers if k == Headers.TENANT_ID), None)
    return body_tenant == tenant_id and header_tenant == tenant_id


async def publish_pending(sm: async_sessionmaker[AsyncSession], bus: EventBus, tenant_id: str, *,
                          ids: Optional[Sequence[str]] = None, limit: int = 100, min_age_s: float = 0.0,
                          timeout_s: float = PUBLISH_TIMEOUT_S) -> tuple[int, int]:
    """Publish unpublished rows for one tenant. Returns (published, failed). Never raises for Kafka errors."""
    published = failed = 0
    where = ["published_at IS NULL", "next_attempt_at <= now()"]
    params: dict = {"n": limit}
    if ids is not None:
        if not ids:
            return 0, 0
        where.append("id = ANY(:ids)")
        params["ids"] = list(ids)
    if min_age_s > 0:
        where.append("created_at <= now() - make_interval(secs => :age)")
        params["age"] = min_age_s
    async with tenant_session(sm, tenant_id) as s:
        rows = (await s.execute(text(
            f"SELECT id, topic, msg_key, payload, headers, attempts FROM outbox_events WHERE {' AND '.join(where)} "
            "ORDER BY created_at, id LIMIT :n FOR UPDATE SKIP LOCKED"), params)).all()
        for row in rows:
            if not _belongs(tenant_id, row.payload, row.headers):
                failed += 1
                log.error("outbox row tenant mismatch; not published", extra={"ctx": {
                    "tenant_id": tenant_id, "event_id": row.id, "operation": "outbox.publish", "outcome": "rejected",
                    "error_code": "tenant_mismatch"}})
                await s.execute(text("UPDATE outbox_events SET attempts = attempts + 1, last_error = 'tenant_mismatch', "
                                     "next_attempt_at = 'infinity' WHERE id = :id"), {"id": row.id})
                continue
            try:
                await asyncio.wait_for(bus.publish_raw(
                    row.topic, row.payload.encode(), row.msg_key, [(k, v.encode()) for k, v in row.headers]), timeout_s)
            except Exception as exc:  # noqa: BLE001 - transient by nature (broker down / timeout); retried later
                failed += 1
                attempts = row.attempts + 1
                await s.execute(text(
                    "UPDATE outbox_events SET attempts = :a, last_error = :e, "
                    "next_attempt_at = now() + make_interval(secs => :d) WHERE id = :id"),
                    {"id": row.id, "a": attempts, "e": f"{exc.__class__.__name__}: {exc}"[:500], "d": backoff_seconds(attempts)})
                log.warning("outbox publish failed", extra={"ctx": {
                    "tenant_id": tenant_id, "event_id": row.id, "operation": "outbox.publish", "outcome": "retry",
                    "attempts": attempts, "error_code": exc.__class__.__name__}})
                break  # keep per-tenant order: do not let later rows overtake this one
            await s.execute(text("UPDATE outbox_events SET published_at = now(), attempts = attempts + 1, last_error = NULL "
                                 "WHERE id = :id"), {"id": row.id})
            published += 1
    return published, failed


class Outbox:
    """Producer-side helper bound to a sessionmaker + bus."""

    def __init__(self, sm: async_sessionmaker[AsyncSession], bus: EventBus, flush_timeout_s: float = PUBLISH_TIMEOUT_S):
        # HTTP callers pass a short timeout so a Kafka outage costs the user ~2 s, not 10 s; the relay catches up
        self.sm, self.bus, self.flush_timeout_s = sm, bus, flush_timeout_s

    @asynccontextmanager
    async def session(self, tenant_id: str) -> AsyncIterator[AsyncSession]:
        """`tenant_session` that publishes whatever was `add()`-ed once the transaction has committed.
        Early `return` inside the block still commits and flushes; an exception rolls the events back with the data."""
        async with tenant_session(self.sm, tenant_id) as s:
            yield s
            ids = list(s.info.pop(_PENDING, []))
        if ids:
            await self.flush(tenant_id, ids)

    async def add(self, s: AsyncSession, topic: str, env: Envelope) -> str:
        return await enqueue(s, topic, env)

    async def add_all(self, s: AsyncSession, events: Iterable[tuple[str, Envelope]]) -> None:
        for topic, env in events:
            await enqueue(s, topic, env)

    async def flush(self, tenant_id: str, ids: Sequence[str]) -> None:
        """Fast path. Failures are logged and left to the relay; the business transaction is already durable."""
        try:
            await publish_pending(self.sm, self.bus, tenant_id, ids=ids, limit=len(ids), timeout_s=self.flush_timeout_s)
        except Exception as exc:  # noqa: BLE001
            log.warning("outbox fast-path flush failed; relay will retry", extra={"ctx": {
                "tenant_id": tenant_id, "operation": "outbox.flush", "outcome": "deferred", "error_code": exc.__class__.__name__}})


async def relay_once(sm: async_sessionmaker[AsyncSession], bus: EventBus, tenant_ids: Iterable[str], *,
                     batch: int = 200, min_age_s: float = 5.0) -> tuple[int, int]:
    """Publish rows the fast path missed. `min_age_s` avoids racing a fast path that is still in flight."""
    total_ok = total_failed = 0
    for tid in tenant_ids:
        try:
            ok, failed = await publish_pending(sm, bus, tid, limit=batch, min_age_s=min_age_s)
        except Exception:  # noqa: BLE001 - one tenant (or a DB blip) must not stop the others
            log.exception("outbox relay failed", extra={"ctx": {"tenant_id": tid, "operation": "outbox.relay"}})
            continue
        total_ok, total_failed = total_ok + ok, total_failed + failed
    if total_ok or total_failed:
        log.info("outbox relay", extra={"ctx": {"operation": "outbox.relay", "published": total_ok, "failed": total_failed}})
    return total_ok, total_failed


async def cleanup(sm: async_sessionmaker[AsyncSession], tenant_ids: Iterable[str], *,
                  outbox_days: int = 7, processed_days: int = 14) -> tuple[int, int]:
    """Retention. Published outbox rows are only kept for diagnosis; ledger rows must outlive Kafka retention
    (168 h) so a redelivered offset is still recognised, hence 14 days by default. Unpublished rows are never deleted."""
    o = p = 0
    for tid in tenant_ids:
        async with tenant_session(sm, tid) as s:
            o += (await s.execute(text("DELETE FROM outbox_events WHERE published_at < now() - make_interval(days => :d)"),
                                  {"d": outbox_days})).rowcount or 0
            p += (await s.execute(text("DELETE FROM processed_events WHERE processed_at < now() - make_interval(days => :d)"),
                                  {"d": processed_days})).rowcount or 0
    return o, p
