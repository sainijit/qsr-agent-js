"""Lightweight W3C trace-context + correlation propagation (no SDK, no exporter).

Ids are OpenTelemetry-compatible (`traceparent` = 00-<32 hex trace>-<16 hex span>-01), so an OTel SDK/collector can
be added later without changing the wire format. Today they are only written to logs (see docs/observability.md).

Kafka headers (in addition to the existing tenant/event headers; envelope bodies are unchanged):
  traceparent     trace of the chain that produced this event
  correlation_id  id of the first event in the chain (inbound webhook message, approval, follow-up...)
  causation_id    event_id of the event whose handler published this one
"""
from __future__ import annotations

import re
import secrets
from typing import Mapping, Optional

from .logging import current_context

TRACEPARENT = "traceparent"
CORRELATION_ID = "correlation_id"
CAUSATION_ID = "causation_id"
_TP = re.compile(r"^00-([0-9a-f]{32})-([0-9a-f]{16})-[0-9a-f]{2}$")


def new_trace_id() -> str:
    return secrets.token_hex(16)


def new_span_id() -> str:
    return secrets.token_hex(8)


def parse_traceparent(value: Optional[str]) -> Optional[tuple[str, str]]:
    m = _TP.match((value or "").strip().lower())
    if not m or m.group(1) == "0" * 32:
        return None
    return m.group(1), m.group(2)


def format_traceparent(trace_id: str, span_id: Optional[str] = None) -> str:
    return f"00-{trace_id}-{span_id or new_span_id()}-01"


def outgoing_headers(event_id: str) -> list[tuple[str, bytes]]:
    """Headers for an event published now, derived from the bound log context (consumer or HTTP request)."""
    ctx = current_context()
    trace_id = ctx.get("trace_id") or new_trace_id()
    h = {TRACEPARENT: format_traceparent(trace_id), CORRELATION_ID: ctx.get("correlation_id") or event_id}
    if ctx.get("event_id") and ctx["event_id"] != event_id:
        h[CAUSATION_ID] = ctx["event_id"]
    return [(k, v.encode()) for k, v in h.items()]


def consume_context(headers: Mapping[str, str], event_id: str) -> dict:
    """Log/trace fields for handling an incoming event; old events without headers start a new chain."""
    tp = parse_traceparent(headers.get(TRACEPARENT))
    return {
        "trace_id": tp[0] if tp else new_trace_id(),
        "span_id": new_span_id(),
        "correlation_id": headers.get(CORRELATION_ID) or event_id,
        "causation_id": headers.get(CAUSATION_ID),
        "event_id": event_id,
    }


def http_context(headers: Mapping[str, str]) -> dict:
    """Fields for an inbound HTTP request: continue the caller's trace if valid, else start one."""
    tp = parse_traceparent(headers.get("traceparent"))
    rid = (headers.get("x-request-id") or "")[:64]
    return {"trace_id": tp[0] if tp else new_trace_id(), "span_id": new_span_id(),
            "request_id": rid if re.fullmatch(r"[A-Za-z0-9._-]{8,64}", rid or "") else secrets.token_hex(8)}
