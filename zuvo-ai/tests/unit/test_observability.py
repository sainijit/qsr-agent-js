"""Phase 5: structured logs, trace/correlation propagation, HTTP observability, metrics rendering."""
import asyncio
import io
import json
import logging

from fastapi import FastAPI
from fastapi.testclient import TestClient

from zuvo_common import logging as zlog
from zuvo_common import tracing
from zuvo_common.bus import InMemoryEventBus
from zuvo_common.contract import ConsumerContract
from zuvo_common.events import Envelope
from zuvo_common.httpobs import install_request_observability
from zuvo_common.idempotency import InMemoryIdempotencyStore
from zuvo_common.opsmetrics import Metric, render_prometheus

T = "3f6c2a10-6d1e-4f0b-9a51-1c2d3e4f5a01"


def _capture(name: str) -> tuple[logging.Logger, io.StringIO]:
    log = zlog.get_logger(name)
    buf = io.StringIO()
    log.handlers[0].stream = buf
    return log, buf


def _lines(buf: io.StringIO) -> list[dict]:
    return [json.loads(x) for x in buf.getvalue().splitlines() if x.strip()]


# ---------------------------------------------------------------- logging
def test_scrub_removes_credentials_and_masks_phones():
    raw = ("Bearer abcdefghijklmnop EAAB" + "x" * 30 + " AIza" + "y" * 35 + " sha256=" + "a" * 64 +
           " postgresql://u:hunter2@db/x password=s3cret eyJhbGciOi.eyJzdWIiOiJ4In0.c2lnbmF0dXJlMTIz +919876543210")
    out = zlog.scrub(raw)
    for secret in ("abcdefghijklmnop", "x" * 30, "y" * 35, "a" * 64, "hunter2", "s3cret", "eyJhbGciOi", "9876543210"):
        assert secret not in out


def test_log_line_has_standard_fields_and_redacts_secret_keys(monkeypatch):
    log, buf = _capture("t.fields")
    with zlog.bind(tenant_id=T, correlation_id="c-1"):
        with zlog.bind(event_id="e-1"):
            log.info("done", extra={"ctx": {"duration_ms": 12, "outcome": "ok", "api_key": "k", "phone": "+919876543210"}})
        log.info("after")
    first, second = _lines(buf)
    assert {"timestamp", "level", "service", "environment", "deployment_version"} <= first.keys()
    assert (first["tenant_id"], first["correlation_id"], first["event_id"], first["duration_ms"]) == (T, "c-1", "e-1", 12)
    assert first["ctx"]["api_key"] == "<redacted>" and "9876543210" not in first["ctx"]["phone"]
    assert "event_id" not in second and second["correlation_id"] == "c-1"


def test_id_fields_are_never_masked():
    log, buf = _capture("t.ids")
    with zlog.bind(trace_id="0123456789abcdef0123456789abcdef", event_id="e-12345678901234"):
        log.info("x")
    line = _lines(buf)[0]
    assert line["trace_id"] == "0123456789abcdef0123456789abcdef" and line["event_id"] == "e-12345678901234"


def test_exception_text_is_scrubbed():
    log, buf = _capture("t.exc")
    try:
        raise RuntimeError("connect postgresql://app:topsecret@db:5432/x failed")
    except RuntimeError:
        log.exception("boom")
    assert "topsecret" not in buf.getvalue()


# ---------------------------------------------------------------- tracing
def test_traceparent_roundtrip_and_rejects_garbage():
    tid = tracing.new_trace_id()
    assert tracing.parse_traceparent(tracing.format_traceparent(tid))[0] == tid
    for bad in (None, "", "00-" + "0" * 32 + "-" + "1" * 16 + "-01", "zz-abc", "00-xyz-abc-01"):
        assert tracing.parse_traceparent(bad) is None


def test_correlation_and_causation_flow_across_two_hops():
    """webhook -> consumer A publishes -> consumer B: one trace, one correlation id, causation = parent event."""
    bus, idem = InMemoryEventBus(), InMemoryIdempotencyStore()
    seen = {}

    async def hop_a(env, record):
        seen["a"] = zlog.current_context()
        await bus.publish("t.second", Envelope(event_type="t.second", tenant_id=T))

    async def hop_b(env, record):
        seen["b"] = zlog.current_context()

    bus.subscribe(["t.first"], ConsumerContract(bus, idem, "grp-a", hop_a))
    bus.subscribe(["t.second"], ConsumerContract(bus, idem, "grp-b", hop_b))
    first = Envelope(event_type="t.first", tenant_id=T, wamid="wamid.X")
    asyncio.run(bus.publish("t.first", first))

    rec1, rec2 = bus.published
    assert rec1.headers["correlation_id"] == first.event_id and "causation_id" not in rec1.headers
    assert rec2.headers["correlation_id"] == first.event_id
    assert rec2.headers["causation_id"] == first.event_id
    assert seen["a"]["trace_id"] == seen["b"]["trace_id"] == tracing.parse_traceparent(rec1.headers["traceparent"])[0]
    assert seen["b"]["causation_id"] == first.event_id and seen["a"]["wamid"] == "wamid.X"
    assert seen["b"]["operation"] == "grp-b:t.second"
    assert zlog.current_context() == {}


def test_events_without_trace_headers_still_processed():
    """Old events already in Kafka (no traceparent/correlation headers) start a new chain."""
    ctx = tracing.consume_context({}, "e-1")
    assert ctx["correlation_id"] == "e-1" and len(ctx["trace_id"]) == 32 and ctx["causation_id"] is None


# ---------------------------------------------------------------- HTTP middleware
def _app():
    app = FastAPI()
    install_request_observability(app, "t-http")

    @app.get("/items/{item_id}")
    async def item(item_id: str):
        return {"trace": zlog.current_context()["trace_id"]}

    @app.get("/fail")
    async def fail():
        raise RuntimeError("x")

    return app


def test_http_request_id_route_template_and_trace_continuation():
    log, buf = _capture("t-http.http")
    c = TestClient(_app(), raise_server_exceptions=False)
    tid = tracing.new_trace_id()
    r = c.get("/items/9876543210?access_token=SECRET", headers={"traceparent": tracing.format_traceparent(tid),
                                                                "x-request-id": "req-abcdef12"})
    assert r.status_code == 200 and r.json()["trace"] == tid
    assert r.headers["x-request-id"] == "req-abcdef12"
    assert c.get("/fail").status_code == 500
    lines = _lines(buf)
    assert lines[0]["operation"] == "GET /items/{item_id}" and lines[0]["trace_id"] == tid and lines[0]["outcome"] == "ok"
    assert lines[1]["level"] == "ERROR" and lines[1]["outcome"] == "error"
    assert "SECRET" not in buf.getvalue() and "9876543210" not in buf.getvalue()


def test_http_rejects_unsafe_request_id():
    c = TestClient(_app())
    r = c.get("/items/1", headers={"x-request-id": "bad id\nwith newline"})
    assert r.headers["x-request-id"] != "bad id\nwith newline" and len(r.headers["x-request-id"]) == 16


# ---------------------------------------------------------------- metrics rendering
def test_prometheus_rendering():
    out = render_prometheus([Metric("zuvo_a", 2, {"group": "g"}, "help a"), Metric("zuvo_b", 0.5),
                             Metric("zuvo_a", 3, {"group": "h"}, "help a")])
    lines = out.splitlines()
    assert lines.index('zuvo_a{group="h"} 3') == lines.index('zuvo_a{group="g"} 2') + 1  # family kept contiguous
    assert out.count("# TYPE zuvo_a gauge") == 1
    assert 'zuvo_a{group="g"} 2' in out and 'zuvo_a{group="h"} 3' in out and "zuvo_b 0.5" in out
