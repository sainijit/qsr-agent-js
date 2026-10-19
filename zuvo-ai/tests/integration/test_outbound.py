"""Phase 7: outbound delivery state machine against a real PostgreSQL with a scripted (mock) Meta client.

Same opt-in harness and synthetic tenants as test_rls.py (`scripts/test_rls.sh` runs all integration tests).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from zuvo_common import outbound_state as ost
from zuvo_common.db import tenant_session
from zuvo_common.events import OutboundRequestPayload, TemplateSpec, Topics

from dispatch.main import MAX_ATTEMPTS, Dispatcher, SendResult
from test_rls import _Bus, pytestmark, world  # noqa: F401  (fixture + skip markers)


class _Routing:
    def __init__(self, world):
        self._lines = {w["number"]: SimpleNamespace(tenant_id=w["tenant"], worker_id=w["worker"],
                                                    meta_phone_number_id="1000000001", whatsapp_number_id=w["number"])
                       for w in (world.a, world.b)}

    def line_by_id(self, number_id):
        return self._lines.get(number_id)


class _Meta:
    """Scripted Meta client: pops one outcome per call (last one repeats)."""

    def __init__(self, *outcomes: SendResult):
        self.outcomes, self.calls = list(outcomes), []

    async def _next(self, pnid, to, callback):
        self.calls.append((pnid, to, callback))
        return self.outcomes.pop(0) if len(self.outcomes) > 1 else self.outcomes[0]

    async def send_text(self, pnid, to, text, callback=None):
        return await self._next(pnid, to, callback)

    async def send_template(self, pnid, to, template, callback=None):
        return await self._next(pnid, to, callback)


OK = SendResult(True, "wamid.HBgTEST", False)
AMBIG = SendResult(False, None, False, "transport: ReadTimeout", "ambiguous")
NOT_SENT = SendResult(False, None, False, "meta_http_429", "not_sent")
REJECTED = SendResult(False, None, False, "meta_http_400:131026", "rejected")


def _phone(hot: int) -> str:
    return f"+91999{hot:07d}"


def _req(w, hot=1, **kw) -> OutboundRequestPayload:
    base = dict(request_id=str(uuid.uuid4()), whatsapp_number_id=w["number"], meta_phone_number_id="1000000001",
                recipient_phone=_phone(hot), text="hello", requested_by="test")
    return OutboundRequestPayload(**{**base, **kw})


async def _open_window(world, w):
    async with world.owner_sm.begin() as s:
        await s.execute(text("UPDATE conversations SET last_inbound_at = now() WHERE id = :c"), {"c": w["conv"]})


async def _row(world, tenant, oid):
    async with tenant_session(world.sm, tenant) as s:
        return await ost.get(s, oid)


def _setup(world, *outcomes):
    bus, meta = _Bus(), _Meta(*outcomes)
    return Dispatcher(world.sm, bus, _Routing(world), meta), bus, meta


def _sent(bus):
    return [e for t, e in bus.events if t == Topics.OUTBOUND_SENT]


@pytest.mark.asyncio
async def test_accepted_and_redelivery_does_not_resend(world):
    a = world.a
    await _open_window(world, a)
    d, bus, meta = _setup(world, OK)
    p = _req(a)
    assert await d.attempt(a["tenant"], p) == "ACCEPTED"
    assert await d.attempt(a["tenant"], p) == "ACCEPTED"  # Kafka redelivery
    assert len(meta.calls) == 1
    oid = ost.outbound_id(a["tenant"], p.request_id)
    assert meta.calls[0][2] == oid  # callback id sent to Meta
    row = await _row(world, a["tenant"], oid)
    assert (row.status, row.wamid, row.attempts, row.request) == ("ACCEPTED", "wamid.HBgTEST", 1, None)
    events = _sent(bus)
    assert {e.event_id for e in events} == {events[0].event_id}  # same state => same event id (dedupe downstream)
    assert events[0].payload["state"] == "ACCEPTED" and events[0].payload["outbound_id"] == oid


@pytest.mark.asyncio
async def test_ambiguous_becomes_unknown_and_is_never_resent(world):
    a = world.a
    await _open_window(world, a)
    d, bus, meta = _setup(world, AMBIG, OK)
    p = _req(a)
    assert await d.attempt(a["tenant"], p) == "UNKNOWN"
    assert await d.attempt(a["tenant"], p) == "UNKNOWN"
    await d.sweep([a["tenant"]])
    assert len(meta.calls) == 1
    row = await _row(world, a["tenant"], ost.outbound_id(a["tenant"], p.request_id))
    assert row.status == "UNKNOWN" and row.last_error.startswith("delivery_unknown") and row.request is not None
    [e] = {e.event_id: e for e in _sent(bus)}.values()
    assert e.payload["state"] == "UNKNOWN" and e.payload["success"] is False


@pytest.mark.asyncio
async def test_not_sent_backs_off_then_sweeper_retries(world):
    a = world.a
    await _open_window(world, a)
    d, bus, meta = _setup(world, NOT_SENT, OK)
    p = _req(a)
    oid = ost.outbound_id(a["tenant"], p.request_id)
    assert await d.attempt(a["tenant"], p) == "PENDING"
    row = await _row(world, a["tenant"], oid)
    assert row.next_attempt_at > datetime.now(timezone.utc) and row.last_error_category == "not_sent"
    assert _sent(bus) == []  # no result event while retrying
    assert await d.attempt(a["tenant"], p) == "PENDING" and len(meta.calls) == 1  # redelivery respects the backoff
    await d.sweep([a["tenant"]])
    assert len(meta.calls) == 1  # not due yet
    async with world.owner_sm.begin() as s:
        await s.execute(text("UPDATE outbound_messages SET next_attempt_at = now() - interval '1 second' WHERE id = :i"), {"i": oid})
    await d.sweep([a["tenant"]])
    row = await _row(world, a["tenant"], oid)
    assert (row.status, row.attempts, len(meta.calls)) == ("ACCEPTED", 2, 2)


@pytest.mark.asyncio
async def test_not_sent_exhausts_to_failed(world):
    a = world.a
    await _open_window(world, a)
    d, bus, meta = _setup(world, NOT_SENT)
    p = _req(a)
    oid = ost.outbound_id(a["tenant"], p.request_id)
    for _ in range(MAX_ATTEMPTS):
        async with world.owner_sm.begin() as s:
            await s.execute(text("UPDATE outbound_messages SET next_attempt_at = NULL WHERE id = :i"), {"i": oid})
        await d.attempt(a["tenant"], p)
    row = await _row(world, a["tenant"], oid)
    assert (row.status, row.last_error_category, row.attempts) == ("FAILED", "retry_exhausted", MAX_ATTEMPTS)
    assert len(meta.calls) == MAX_ATTEMPTS
    assert [e.payload["state"] for e in _sent(bus)] == ["FAILED"]


@pytest.mark.asyncio
async def test_rejected_is_failed_without_retry(world):
    a = world.a
    await _open_window(world, a)
    d, bus, meta = _setup(world, REJECTED)
    p = _req(a)
    assert await d.attempt(a["tenant"], p) == "FAILED"
    await d.sweep([a["tenant"]])
    assert len(meta.calls) == 1
    row = await _row(world, a["tenant"], ost.outbound_id(a["tenant"], p.request_id))
    assert row.last_error_category == "rejected" and row.request is None


@pytest.mark.asyncio
async def test_policy_failures_never_call_meta(world):
    a = world.a
    d, bus, meta = _setup(world, OK)
    # no inbound in 24h and no template
    assert await d.attempt(a["tenant"], _req(a)) == "FAILED"
    # opted out (agent send)
    async with world.owner_sm.begin() as s:
        await s.execute(text("UPDATE customers SET opted_out = true WHERE id = :c"), {"c": a["customer"]})
    await _open_window(world, a)
    assert await d.attempt(a["tenant"], _req(a, sent_by="agent")) == "FAILED"
    assert meta.calls == []
    errs = sorted(e.payload["error"] for e in _sent(bus))
    assert errs == ["customer_opted_out", "outside_24h_window_template_required"]
    # a template is allowed outside the window (customer opted back in)
    async with world.owner_sm.begin() as s:
        await s.execute(text("UPDATE customers SET opted_out = false WHERE id = :c"), {"c": a["customer"]})
        await s.execute(text("UPDATE conversations SET last_inbound_at = NULL WHERE id = :c"), {"c": a["conv"]})
    assert await d.attempt(a["tenant"], _req(a, template=TemplateSpec(name="visit_reminder"))) == "ACCEPTED"


@pytest.mark.asyncio
async def test_stale_sending_becomes_unknown_not_resent(world):
    a = world.a
    await _open_window(world, a)
    d, bus, meta = _setup(world, OK)
    p = _req(a)
    oid = ost.outbound_id(a["tenant"], p.request_id)
    # simulate a crash after the SENDING commit: insert the row as a dispatcher would have left it
    async with world.owner_sm.begin() as s:
        await s.execute(text(
            "INSERT INTO outbound_messages (id, tenant_id, request_id, whatsapp_number_id, recipient_phone, kind, sent_by, "
            "status, attempts, sending_at, request) VALUES (:id, :t, :r, :n, :ph, 'text', 'human', 'SENDING', 1, "
            "now() - interval '10 minutes', CAST(:req AS jsonb))"),
            {"id": oid, "t": a["tenant"], "r": p.request_id, "n": a["number"], "ph": p.recipient_phone, "req": p.model_dump_json()})
    assert await d.attempt(a["tenant"], p) == "SENDING"  # redelivery while SENDING: hands off
    await d.sweep([a["tenant"]])
    row = await _row(world, a["tenant"], oid)
    assert row.status == "UNKNOWN" and "interrupted" in row.last_error
    assert meta.calls == []
    assert [e.payload["state"] for e in _sent(bus)] == ["UNKNOWN"]


@pytest.mark.asyncio
@pytest.mark.parametrize("match", ["callback", "wamid"])
async def test_webhook_reconciles_unknown(world, match):
    a = world.a
    await _open_window(world, a)
    d, bus, _ = _setup(world, AMBIG)
    p = _req(a)
    oid = ost.outbound_id(a["tenant"], p.request_id)
    await d.attempt(a["tenant"], p)
    if match == "wamid":  # e.g. an operator recorded the wamid already; receipt carries no callback
        async with world.owner_sm.begin() as s:
            await s.execute(text("UPDATE outbound_messages SET wamid = 'wamid.LATE' WHERE id = :i"), {"i": oid})
    at = datetime.now(timezone.utc)
    async with tenant_session(world.sm, a["tenant"]) as s:
        res = await ost.apply_status(s, "wamid.LATE", "delivered", at, oid if match == "callback" else None, None)
    assert res["reconciled"] and res["row"].status == "UNKNOWN"
    row = await _row(world, a["tenant"], oid)
    assert (row.status, row.wamid, row.delivery_status, row.reconciled_by) == ("ACCEPTED", "wamid.LATE", "delivered", "meta_webhook")
    assert row.request is None and row.delivered_at is not None
    # duplicate + out-of-order receipts: no change, never backwards
    async with tenant_session(world.sm, a["tenant"]) as s:
        dup = await ost.apply_status(s, "wamid.LATE", "delivered", at, oid, None)
        old = await ost.apply_status(s, "wamid.LATE", "sent", at, oid, None)
    assert not dup["reconciled"] and not dup["changed"] and not old["changed"]
    async with tenant_session(world.sm, a["tenant"]) as s:
        await ost.apply_status(s, "wamid.LATE", "read", at, None, None)
    row = await _row(world, a["tenant"], oid)
    assert (row.status, row.delivery_status) == ("ACCEPTED", "read")


@pytest.mark.asyncio
async def test_failed_receipt_on_unknown_marks_failed(world):
    a = world.a
    await _open_window(world, a)
    d, _, _ = _setup(world, AMBIG)
    p = _req(a)
    oid = ost.outbound_id(a["tenant"], p.request_id)
    await d.attempt(a["tenant"], p)
    async with tenant_session(world.sm, a["tenant"]) as s:
        res = await ost.apply_status(s, "wamid.F", "failed", datetime.now(timezone.utc), oid, "131049 ecosystem")
    assert res["reconciled"]
    row = await _row(world, a["tenant"], oid)
    assert (row.status, row.last_error_category, row.delivery_status) == ("FAILED", "meta_failed", "failed")


@pytest.mark.asyncio
async def test_receipt_before_dispatch_commit_is_quietly_accepted(world):
    """Meta's webhook can beat dispatch's second transaction; the row must not be double-counted as a reconciliation."""
    a = world.a
    await _open_window(world, a)
    p = _req(a)
    oid = ost.outbound_id(a["tenant"], p.request_id)

    class _Racing(_Meta):
        async def _next(self, pnid, to, callback):
            async with tenant_session(world.sm, a["tenant"]) as s:
                res = await ost.apply_status(s, "wamid.RACE", "sent", datetime.now(timezone.utc), callback, None)
            assert res and not res["reconciled"]
            return SendResult(True, "wamid.RACE", False)

    bus = _Bus()
    d = Dispatcher(world.sm, bus, _Routing(world), _Racing())
    assert await d.attempt(a["tenant"], p) == "ACCEPTED"
    row = await _row(world, a["tenant"], oid)
    assert (row.status, row.wamid, row.delivery_status, row.reconciled_at) == ("ACCEPTED", "wamid.RACE", "sent", None)
    assert [e.payload["state"] for e in _sent(bus)] == ["ACCEPTED"]


@pytest.mark.asyncio
async def test_manual_resolve_unknown(world):
    a = world.a
    await _open_window(world, a)
    d, _, _ = _setup(world, AMBIG)
    p1, p2 = _req(a), _req(a)
    for p in (p1, p2):
        await d.attempt(a["tenant"], p)
    o1, o2 = (ost.outbound_id(a["tenant"], p.request_id) for p in (p1, p2))
    async with tenant_session(world.sm, a["tenant"]) as s:
        before = await ost.resolve_unknown(s, o1, "accepted", "ops@test", wamid="wamid.MANUAL")
        await ost.resolve_unknown(s, o2, "failed", "ops@test", note="customer did not receive")
    assert before.status == "UNKNOWN" and before.request is not None
    r1, r2 = await _row(world, a["tenant"], o1), await _row(world, a["tenant"], o2)
    assert (r1.status, r1.wamid, r1.reconciled_by) == ("ACCEPTED", "wamid.MANUAL", "ops@test")
    assert (r2.status, r2.last_error_category, r2.last_error) == ("FAILED", "manual", "customer did not receive")
    env = ost.sent_envelope(a["tenant"], r1, OutboundRequestPayload.model_validate(before.request))
    assert env.payload["state"] == "ACCEPTED" and env.payload["success"]
    with pytest.raises(ValueError):  # only UNKNOWN can be reconciled
        async with tenant_session(world.sm, a["tenant"]) as s:
            await ost.resolve_unknown(s, o1, "failed", "ops@test")


@pytest.mark.asyncio
async def test_receipts_and_reconcile_are_tenant_isolated(world):
    a, b = world.a, world.b
    await _open_window(world, a)
    d, _, _ = _setup(world, AMBIG)
    p = _req(a)
    oid = ost.outbound_id(a["tenant"], p.request_id)
    await d.attempt(a["tenant"], p)
    async with tenant_session(world.sm, b["tenant"]) as s:
        assert await ost.apply_status(s, "wamid.X", "delivered", datetime.now(timezone.utc), oid, None) is None
        with pytest.raises(LookupError):
            await ost.resolve_unknown(s, oid, "accepted", "attacker")
    assert (await _row(world, a["tenant"], oid)).status == "UNKNOWN"


@pytest.mark.asyncio
async def test_same_request_id_in_two_tenants_is_independent(world):
    a, b = world.a, world.b
    for w in (a, b):
        await _open_window(world, w)
    d, _, meta = _setup(world, OK)
    rid = str(uuid.uuid4())
    assert await d.attempt(a["tenant"], _req(a, 1, request_id=rid)) == "ACCEPTED"
    assert await d.attempt(b["tenant"], _req(b, 2, request_id=rid)) == "ACCEPTED"
    assert len(meta.calls) == 2


# ---------------------------------------------------------------- persister: message history follows the ledger
class _PRouting(_Routing):
    version = "test"
    config = None

    def tenant(self, tid):
        return object()


def _persister(world, bus):
    from persister.main import Persister

    p = Persister(world.sm, bus, _PRouting(world))
    p._synced = {world.a["tenant"]: "test", world.b["tenant"]: "test"}  # synthetic tenants: skip routing sync
    return p


async def _messages(world, tenant, rid_wamids):
    async with world.owner_sm() as s:
        return (await s.execute(text(
            "SELECT wamid, status, delivery_status, delivered_at FROM messages WHERE tenant_id = :t AND wamid = ANY(:w)"),
            {"t": tenant, "w": rid_wamids})).all()


@pytest.mark.asyncio
async def test_unknown_placeholder_resolved_by_webhook(world):
    from zuvo_common.events import Envelope, MessageStatusPayload

    a = world.a
    await _open_window(world, a)
    d, bus, _ = _setup(world, AMBIG)
    per = _persister(world, bus)
    p = _req(a)
    oid = ost.outbound_id(a["tenant"], p.request_id)
    await d.attempt(a["tenant"], p)
    [unknown] = _sent(bus)
    await per.on_outbound(unknown)
    await per.on_outbound(unknown)  # redelivery
    wamids = [f"unknown.{p.request_id}", "wamid.LATE2"]
    assert [tuple(r)[:3] for r in await _messages(world, a["tenant"], wamids)] == [(f"unknown.{p.request_id}", "UNKNOWN", None)]

    status = Envelope(event_type=Topics.MESSAGE_STATUS, tenant_id=a["tenant"], worker_id=a["worker"],
                      payload=MessageStatusPayload(wamid="wamid.LATE2", status="delivered", timestamp="1760000000",
                                                   callback_data=oid).model_dump())
    await per.on_status(status)
    await per.on_status(status)  # duplicate webhook
    resolved = [e for e in _sent(bus) if e.payload["state"] == "ACCEPTED"]
    assert len({e.event_id for e in resolved}) == 1 and resolved[0].wamid == "wamid.LATE2"
    await per.on_outbound(resolved[0])
    await per.on_outbound(resolved[0])
    rows = await _messages(world, a["tenant"], wamids)
    assert len(rows) == 1
    assert (rows[0].wamid, rows[0].status, rows[0].delivery_status) == ("wamid.LATE2", "SENT", "delivered")
    assert rows[0].delivered_at is not None


@pytest.mark.asyncio
async def test_persister_rejects_other_tenants_line(world):
    from zuvo_common.contract import PermanentError

    a, b = world.a, world.b
    await _open_window(world, a)
    d, bus, _ = _setup(world, OK)
    await d.attempt(a["tenant"], _req(a))
    [env] = _sent(bus)
    forged = env.model_copy(update={"tenant_id": b["tenant"]})
    with pytest.raises(PermanentError):
        await _persister(world, bus).on_outbound(forged)
