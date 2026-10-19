"""Durable processed-event ledger (Phase 6.2).

Redis (`zuvo:evt:{group}:{event_id}`, 24 h TTL) remains the cheap first check in `ConsumerContract`.
Handlers whose side effects are *not* naturally idempotent (counters, state transitions that decide
whether a follow-up event is emitted) also call `first_delivery()` as the first statement of their own
transaction. The ledger row then commits or rolls back together with the side effects, so:

* Redis loss or TTL expiry -> the row is still there -> handler skips its writes.
* crash after DB commit, before Kafka offset commit -> redelivery finds the row -> skip.
* handler error -> transaction rolls back including the row -> retry processes normally.

Events the handler emitted from that transaction were written to the outbox atomically, so skipping a
duplicate never loses a downstream event.
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .events import Envelope


async def first_delivery(s: AsyncSession, group: str, env: Envelope) -> bool:
    """True exactly once per (tenant, group, event_id) across restarts. Must run inside `tenant_session`."""
    row = await s.execute(
        text("INSERT INTO processed_events (tenant_id, consumer_group, event_id, event_type) "
             "VALUES (CAST(:t AS uuid), :g, :e, :et) ON CONFLICT DO NOTHING RETURNING 1"),
        {"t": env.tenant_id, "g": group, "e": env.event_id, "et": env.event_type},
    )
    return row.first() is not None
