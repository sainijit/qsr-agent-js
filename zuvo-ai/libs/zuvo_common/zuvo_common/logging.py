"""Structured JSON logging with PII masking (DPDP), secret scrubbing and bound context.

Context fields (`bind(...)`) are contextvars, so they follow the current asyncio task: a consumer binds
tenant/event/correlation ids once and every log line emitted while handling that event carries them.
"""
import contextvars
import json
import logging
import os
import re
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

_PHONE_RE = re.compile(r"\+?\d{10,15}")

# promoted to top-level JSON keys (brief §5.1); anything else bound or passed via extra={"ctx": ...} stays under "ctx"
STANDARD_FIELDS = ("trace_id", "span_id", "request_id", "correlation_id", "event_id", "causation_id", "event_type",
                   "tenant_id", "conversation_id", "wamid", "agent_run_id", "operation", "duration_ms", "outcome",
                   "error_code")
_ID_FIELDS = frozenset({"trace_id", "span_id", "request_id", "correlation_id", "event_id", "causation_id",
                        "tenant_id", "conversation_id", "wamid", "agent_run_id", "duration_ms"})
_CTX: contextvars.ContextVar[dict] = contextvars.ContextVar("zuvo_log_ctx", default={})

_SECRET_PATTERNS = [
    (re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]{8,}"), r"\1 <redacted>"),
    (re.compile(r"\bEAA[A-Za-z0-9]{20,}"), "<redacted-meta-token>"),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{30,}"), "<redacted-api-key>"),
    (re.compile(r"\bsha256=[0-9a-fA-F]{16,}"), "sha256=<redacted>"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}"), "<redacted-jwt>"),
    (re.compile(r"(?i)((?:password|passwd|secret|api[_-]?key|access_token|token)[\"']?\s*[:=]\s*[\"']?)[^\s\"',&}]+"),
     r"\1<redacted>"),
    (re.compile(r"(?i)(://[^:/@\s]+:)[^@\s]+@"), r"\1<redacted>@"),
]
_SECRET_KEYS = re.compile(r"(?i)(password|secret|token|api_?key|authorization|signature|cookie)")


def mask_phone(phone: str | None) -> str:
    if not phone:
        return ""
    digits = str(phone)
    if len(digits) < 6:
        return "***"
    return f"{digits[:3]}****{digits[-3:]}"


def mask_text(text: str | None) -> str:
    """Masks phone-like sequences inside free text."""
    if not text:
        return ""
    return _PHONE_RE.sub(lambda m: mask_phone(m.group(0)), text)


def scrub(text: str | None) -> str:
    """Redact credentials (bearer/JWT/Meta/Google keys, signatures, URL passwords) and mask phone numbers."""
    if not text:
        return ""
    for rx, repl in _SECRET_PATTERNS:
        text = rx.sub(repl, text)
    return mask_text(text)


def _clean(value: Any, key: str = "") -> Any:
    if key and _SECRET_KEYS.search(key) and value not in (None, "", 0):
        return "<redacted>"
    if isinstance(value, str):
        return scrub(value)
    if isinstance(value, dict):
        return {k: _clean(v, str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    return value


@contextmanager
def bind(**fields: Any) -> Iterator[dict]:
    """Attach fields to every log line emitted in this task until the block exits."""
    merged = {**_CTX.get(), **{k: v for k, v in fields.items() if v is not None}}
    token = _CTX.set(merged)
    try:
        yield merged
    finally:
        _CTX.reset(token)


def current_context() -> dict:
    return dict(_CTX.get())


class _JsonFormatter(logging.Formatter):
    def __init__(self, service: str | None = None):
        super().__init__()
        self.service = service or os.environ.get("SERVICE_NAME")
        self.environment = os.environ.get("APP_ENV", "development")
        self.version = os.environ.get("ZUVO_VERSION", "dev")

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname,
            "service": getattr(record, "service", None) or self.service or record.name,
            "logger": record.name,
            "environment": self.environment,
            "deployment_version": self.version,
            "msg": scrub(record.getMessage()),
        }
        ctx = {**_CTX.get(), **(getattr(record, "ctx", None) or {})}
        for k in STANDARD_FIELDS:
            if k in ctx:
                v = ctx.pop(k)
                # system-generated ids are not user content; phone masking would corrupt digit runs in hex ids
                payload[k] = v if k in _ID_FIELDS else _clean(v, k)
        if ctx:
            payload["ctx"] = _clean(ctx)
        if record.exc_info:
            payload["exc"] = scrub(self.formatException(record.exc_info))
        return json.dumps(payload, default=str)


def get_logger(name: str, level: str = "INFO") -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(_JsonFormatter())
        logger.addHandler(handler)
        logger.propagate = False
    logger.setLevel(level.upper())
    return logger


_SECRET_QUERY_RE = re.compile(r"((?:access_token|token|hub\.verify_token)=)[^&\s\"]*", re.IGNORECASE)


def redact_query_secrets(text: str) -> str:
    return _SECRET_QUERY_RE.sub(r"\1<redacted>", text)


class _AccessRedactionFilter(logging.Filter):
    """uvicorn.access logs the full path; SSE passes the JWT as ?access_token= and Meta sends hub.verify_token."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(redact_query_secrets(a) if isinstance(a, str) else a for a in record.args)
        elif isinstance(record.msg, str):
            record.msg = redact_query_secrets(record.msg)
        return True


def install_access_log_redaction() -> None:
    access = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, _AccessRedactionFilter) for f in access.filters):
        access.addFilter(_AccessRedactionFilter())


def install_json_logging(service: str, level: str = "INFO") -> None:
    """Route the root logger (and uvicorn/third-party loggers) through the JSON formatter."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_JsonFormatter(service))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers[:] = []
        lg.propagate = True
