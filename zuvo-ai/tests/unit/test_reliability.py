"""Phase 6 unit tests: retry classification, DLQ metadata, replay targeting, outbox guards."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from zuvo_common import contract as C
from zuvo_common.bus import InMemoryEventBus, Record
from zuvo_common.dlq import meta, replay_headers
from zuvo_common.events import Envelope, Headers
from zuvo_common.idempotency import InMemoryIdempotencyStore
from zuvo_common.outbox import _belongs, backoff_seconds

TENANT = "11111111-1111-1111-1111-111111111111"


def _record(env: Envelope, topic: str = "t.in", **extra_headers) -> Record:
    h = {k: v.decode() for k, v in env.headers()}
    h.update({"traceparent": "00-" + "a" * 32 + "-" + "b" * 16 + "-01", "correlation_id": "corr-1", **extra_headers})
    return Record(topic, env.key, env.model_dump_json().encode(), h, partition=3, offset=42)


def _env(**kw) -> Envelope:
    return Envelope(event_type="t.in", tenant_id=TENANT, payload={"x": 1}, **kw)


def _contract(handler, group="grp", retries=3):
    bus, idem = InMemoryEventBus(), InMemoryIdempotencyStore()
    return bus, idem, C.ConsumerContract(bus, idem, group, handler, retries=retries, backoff_seconds=0)


@pytest.mark.asyncio
async def test_transient_error_is_retried_then_succeeds():
    calls = []

    async def h(env, rec):
        calls.append(1)
        if len(calls) < 3:
            raise ConnectionError("db blip")

    bus, _, c = _contract(h)
    await c(_record(_env()))
    assert len(calls) == 3
    assert not [r for r in bus.published if r.topic.endswith(".DLQ")]


@pytest.mark.asyncio
@pytest.mark.parametrize("exc", [C.PermanentError("nope"), ValueError("bad"), KeyError("k"), TypeError("t")])
async def test_permanent_error_goes_to_dlq_without_retry(exc):
    calls = []

    async def h(env, rec):
        calls.append(1)
        raise exc

    bus, _, c = _contract(h)
    await c(_record(_env()))
    assert len(calls) == 1
    [dlq] = [r for r in bus.published if r.topic == "t.in.DLQ"]
    assert dlq.headers[Headers.DLQ_ATTEMPTS] == "1"
    assert dlq.headers[Headers.DLQ_ERROR_CLASS] == exc.__class__.__name__
    assert dlq.headers[Headers.ERROR].startswith("permanent:")


@pytest.mark.asyncio
async def test_exhausted_retries_dlq_keeps_original_metadata():
    async def h(env, rec):
        raise TimeoutError("gemini slow")

    env = _env()
    bus, _, c = _contract(h, group="agent-worker")
    await c(_record(env))
    [dlq] = [r for r in bus.published if r.topic == "t.in.DLQ"]
    h_ = dlq.headers
    assert h_[Headers.ORIGINAL_TOPIC] == "t.in" and h_[Headers.DLQ_GROUP] == "agent-worker"
    assert h_[Headers.DLQ_ATTEMPTS] == "3" and h_[Headers.DLQ_ERROR_CLASS] == "TimeoutError"
    assert h_[Headers.DLQ_PARTITION] == "3" and h_[Headers.DLQ_OFFSET] == "42" and h_[Headers.DLQ_FAILED_AT]
    # original event identity + trace context survive
    assert h_[Headers.EVENT_ID] == env.event_id and h_[Headers.TENANT_ID] == TENANT
    assert h_["correlation_id"] == "corr-1" and h_["traceparent"].startswith("00-" + "a" * 32)
    assert dlq.value == _record(env).value  # payload byte-for-byte


@pytest.mark.asyncio
async def test_invalid_event_goes_to_dlq():
    async def h(env, rec):
        raise AssertionError("must not be called")

    bus, _, c = _contract(h)
    await c(Record("t.in", None, b"{not json", {Headers.TENANT_ID: TENANT}))
    await c(Record("t.in", None, b"{}", {}))  # no tenant header
    dlqs = [r for r in bus.published if r.topic == "t.in.DLQ"]
    assert len(dlqs) == 2
    assert {d.headers[Headers.DLQ_ERROR_CLASS] for d in dlqs} == {"ContractViolation"}


@pytest.mark.asyncio
async def test_repeated_delivery_is_processed_once():
    calls = []

    async def h(env, rec):
        calls.append(env.event_id)

    _, _, c = _contract(h)
    rec = _record(_env())
    for _ in range(3):
        await c(rec)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_dlqd_event_is_not_marked_processed_so_replay_works():
    state = {"fail": True}
    calls = []

    async def h(env, rec):
        calls.append(1)
        if state["fail"]:
            raise C.PermanentError("bad config")

    bus, _, c = _contract(h)
    rec = _record(_env())
    await c(rec)
    state["fail"] = False
    await c(rec)  # replay after fix
    assert len(calls) == 2
    assert len([r for r in bus.published if r.topic.endswith(".DLQ")]) == 1


@pytest.mark.asyncio
async def test_replay_targets_only_the_failed_group():
    seen = {"a": 0, "b": 0}

    async def ha(env, rec):
        seen["a"] += 1

    async def hb(env, rec):
        seen["b"] += 1

    _, _, ca = _contract(ha, group="grp-a")
    _, _, cb = _contract(hb, group="grp-b")
    rec = _record(_env(), **{Headers.REPLAY_FOR_GROUP: "grp-b"})
    await ca(rec)
    await cb(rec)
    assert seen == {"a": 0, "b": 1}


def test_replay_headers_strip_dlq_metadata_and_target_group():
    m = SimpleNamespace(topic="t.in.DLQ", partition=0, offset=7, key=None, value=b"{}", headers=[
        (Headers.TENANT_ID, TENANT.encode()), (Headers.EVENT_ID, b"e1"), ("traceparent", b"tp"),
        (Headers.ERROR, b"permanent: x"), (Headers.ORIGINAL_TOPIC, b"t.in"), (Headers.DLQ_GROUP, b"lead-service"),
        (Headers.DLQ_ATTEMPTS, b"1"), (Headers.DLQ_ERROR_CLASS, b"ValueError"),
    ])
    topic, headers = replay_headers(m)
    d = dict(headers)
    assert topic == "t.in"
    assert d[Headers.REPLAY_FOR_GROUP] == b"lead-service" and d[Headers.REPLAYED_FROM] == b"t.in.DLQ:0:7"
    assert d["traceparent"] == b"tp" and d[Headers.EVENT_ID] == b"e1"
    assert not set(d) & set(Headers.DLQ_HEADERS)
    with pytest.raises(ValueError):
        replay_headers(SimpleNamespace(topic="x.DLQ", partition=0, offset=0, headers=[]))
    # inspection output never includes the payload
    assert "value" not in meta(m) and meta(m)["group"] == "lead-service"


def test_backoff_is_jittered_and_bounded():
    for attempt in range(0, 8):
        d = min(30.0, 0.5 * 2 ** attempt)
        vals = {round(C.retry_delay(attempt, 0.5), 6) for _ in range(20)}
        assert all(d / 2 <= v <= d for v in vals)
        assert len(vals) > 1  # actually random
    assert all(0 < backoff_seconds(a) <= 300 for a in range(1, 30))


def test_outbox_refuses_rows_of_another_tenant():
    env = _env()
    payload = env.model_dump_json()
    headers = [[k, v.decode()] for k, v in env.headers()]
    assert _belongs(TENANT, payload, headers)
    other = "22222222-2222-2222-2222-222222222222"
    assert not _belongs(other, payload, headers)
    forged = [[k, other if k == Headers.TENANT_ID else v] for k, v in headers]
    assert not _belongs(TENANT, payload, forged)
    assert not _belongs(TENANT, "not json", headers)
    assert json.loads(payload)["tenant_id"] == TENANT
