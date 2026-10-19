"""Phase 6: transactional outbox + processed-event ledger against a real PostgreSQL.

Same opt-in harness and synthetic tenants as test_rls.py (`scripts/test_rls.sh` runs both).
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from zuvo_common import outbox as ob
from zuvo_common.db import tenant_session
from zuvo_common.events import Envelope
from zuvo_common.processed import first_delivery

from test_rls import _Bus, pytestmark, world  # noqa: F401  (fixture + skip markers)

TOPIC = "test.outbox"


def _env(tenant: str, event_id: str | None = None) -> Envelope:
    kw = {"event_id": event_id} if event_id else {}
    return Envelope(event_type=TOPIC, tenant_id=tenant, payload={"n": 1}, **kw)


async def _rows(world, tenant: str) -> list:
    async with world.owner_sm() as s:
        return (await s.execute(text(
            "SELECT id, published_at, attempts, last_error, next_attempt_at > now() AS deferred "
            "FROM outbox_events WHERE tenant_id = :t ORDER BY created_at, id"), {"t": tenant})).all()


@pytest.mark.asyncio
async def test_rollback_discards_enqueued_event(world):
    t = world.a["tenant"]
    with pytest.raises(RuntimeError):
        async with ob.Outbox(world.sm, _Bus()).session(t) as s:
            await ob.enqueue(s, TOPIC, _env(t))
            raise RuntimeError("business write failed")
    assert await _rows(world, t) == []


@pytest.mark.asyncio
async def test_commit_publishes_and_marks_row(world):
    t, bus = world.a["tenant"], _Bus()
    env = _env(t)
    async with ob.Outbox(world.sm, bus).session(t) as s:
        await ob.enqueue(s, TOPIC, env)
    assert [(tp, e.event_id) for tp, e in bus.events] == [(TOPIC, env.event_id)]
    [row] = await _rows(world, t)
    assert row.published_at is not None and row.attempts == 1 and row.last_error is None


@pytest.mark.asyncio
async def test_duplicate_enqueue_is_ignored(world):
    t, bus = world.a["tenant"], _Bus(fail=True)
    eid = str(uuid.uuid4())
    for _ in range(2):
        async with ob.Outbox(world.sm, bus).session(t) as s:
            await ob.enqueue(s, TOPIC, _env(t, eid))
    assert [r.id for r in await _rows(world, t)] == [eid]


@pytest.mark.asyncio
async def test_kafka_down_leaves_row_pending_then_relay_publishes(world):
    t = world.a["tenant"]
    env = _env(t)
    async with ob.Outbox(world.sm, _Bus(fail=True)).session(t) as s:
        await ob.enqueue(s, TOPIC, env)
    [row] = await _rows(world, t)
    assert row.published_at is None and row.attempts == 1 and row.deferred
    assert row.last_error.startswith("ConnectionError")

    good = _Bus()
    assert await ob.relay_once(world.sm, good, [t], min_age_s=0) == (0, 0)  # still backing off
    async with world.owner_sm.begin() as s:
        await s.execute(text("UPDATE outbox_events SET next_attempt_at = now() WHERE id = :id"), {"id": env.event_id})
    assert await ob.relay_once(world.sm, good, [t], min_age_s=0) == (1, 0)
    assert [e.event_id for _, e in good.events] == [env.event_id]
    # already published -> never re-sent by a later relay pass
    assert await ob.relay_once(world.sm, good, [t], min_age_s=0) == (0, 0)


@pytest.mark.asyncio
async def test_failure_stops_batch_to_keep_order(world):
    t = world.a["tenant"]
    async with ob.Outbox(world.sm, _Bus(fail=True)).session(t) as s:
        for _ in range(3):
            await ob.enqueue(s, TOPIC, _env(t))
    rows = await _rows(world, t)
    assert [r.attempts for r in rows] == [1, 0, 0]  # first failed, later rows not attempted
    assert all(r.published_at is None for r in rows)


@pytest.mark.asyncio
async def test_first_delivery_once_and_rolls_back_with_transaction(world):
    t = world.a["tenant"]
    env = _env(t)
    with pytest.raises(RuntimeError):
        async with tenant_session(world.sm, t) as s:
            assert await first_delivery(s, "grp", env)
            raise RuntimeError("handler crashed before commit")
    async with tenant_session(world.sm, t) as s:
        assert await first_delivery(s, "grp", env)  # rollback => still first
    async with tenant_session(world.sm, t) as s:
        assert not await first_delivery(s, "grp", env)
        assert await first_delivery(s, "other-grp", env)  # ledger is per consumer group


@pytest.mark.asyncio
async def test_redelivery_after_commit_emits_no_duplicates(world):
    """Crash window: handler committed, process died before the Kafka offset commit -> event redelivered."""
    t = world.a["tenant"]
    inbound = _env(t)
    out_id = str(uuid.uuid4())

    async def handler(bus):
        outbox = ob.Outbox(world.sm, bus)
        async with outbox.session(t) as s:
            if not await first_delivery(s, "test-handler", inbound):
                return
            await outbox.add(s, TOPIC, _env(t, out_id))

    await handler(_Bus(fail=True))  # committed, publish lost
    second = _Bus()
    await handler(second)  # redelivery: skipped, emits nothing new
    assert second.events == []
    assert [r.id for r in await _rows(world, t)] == [out_id]
    async with world.owner_sm.begin() as s:
        await s.execute(text("UPDATE outbox_events SET next_attempt_at = now() WHERE id = :id"), {"id": out_id})
    relay = _Bus()
    assert await ob.relay_once(world.sm, relay, [t], min_age_s=0) == (1, 0)  # the original event is not lost
    assert [e.event_id for _, e in relay.events] == [out_id]


@pytest.mark.asyncio
async def test_outbox_and_ledger_are_tenant_isolated(world):
    a, b = world.a["tenant"], world.b["tenant"]
    with pytest.raises(DBAPIError, match="row-level security"):
        async with tenant_session(world.sm, a) as s:
            await ob.enqueue(s, TOPIC, _env(b))
    async with ob.Outbox(world.sm, _Bus(fail=True)).session(b) as s:
        await ob.enqueue(s, TOPIC, _env(b))
        await first_delivery(s, "grp", _env(b))
    async with tenant_session(world.sm, a) as s:
        assert (await s.execute(text("SELECT count(*) FROM outbox_events"))).scalar() == 0
        assert (await s.execute(text("SELECT count(*) FROM processed_events"))).scalar() == 0
    # relaying for A never touches B's pending row
    async with world.owner_sm.begin() as s:
        await s.execute(text("UPDATE outbox_events SET next_attempt_at = now() WHERE tenant_id = :t"), {"t": b})
    bus = _Bus()
    assert await ob.relay_once(world.sm, bus, [a], min_age_s=0) == (0, 0)
    assert bus.events == []


@pytest.mark.asyncio
async def test_row_with_mismatched_tenant_is_quarantined(world):
    a, b = world.a["tenant"], world.b["tenant"]
    forged = _env(b)  # body + headers say B, row is stored under A (e.g. a bug in a producer)
    async with world.owner_sm.begin() as s:
        await s.execute(text(
            "INSERT INTO outbox_events (id, tenant_id, topic, payload, headers) "
            "VALUES (:id, :a, :tp, :p, CAST(:h AS jsonb))"),
            {"id": forged.event_id, "a": a, "tp": TOPIC, "p": forged.model_dump_json(),
             "h": '[["tenant_id", "%s"]]' % b})
    bus = _Bus()
    assert await ob.relay_once(world.sm, bus, [a], min_age_s=0) == (0, 1)
    assert bus.events == []
    async with world.owner_sm() as s:
        row = (await s.execute(text("SELECT last_error, next_attempt_at = 'infinity' FROM outbox_events WHERE id = :id"),
                               {"id": forged.event_id})).one()
    assert tuple(row) == ("tenant_mismatch", True)


@pytest.mark.asyncio
async def test_cleanup_keeps_unpublished_rows(world):
    t = world.a["tenant"]
    old_pub, old_pending = _env(t), _env(t)
    async with ob.Outbox(world.sm, _Bus()).session(t) as s:
        await ob.enqueue(s, TOPIC, old_pub)
    async with ob.Outbox(world.sm, _Bus(fail=True)).session(t) as s:
        await ob.enqueue(s, TOPIC, old_pending)
        await first_delivery(s, "grp", old_pub)
    async with world.owner_sm.begin() as s:
        await s.execute(text("UPDATE outbox_events SET created_at = now() - interval '30 days', "
                             "published_at = CASE WHEN published_at IS NULL THEN NULL ELSE now() - interval '30 days' END "
                             "WHERE tenant_id = :t"), {"t": t})
        await s.execute(text("UPDATE processed_events SET processed_at = now() - interval '30 days' WHERE tenant_id = :t"),
                        {"t": t})
    assert await ob.cleanup(world.sm, [t]) == (1, 1)
    assert [r.id for r in await _rows(world, t)] == [old_pending.event_id]
