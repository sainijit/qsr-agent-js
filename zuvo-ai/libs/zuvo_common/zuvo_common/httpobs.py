"""ASGI middleware: per-request trace/request id, X-Request-ID response header, one structured log line.

Pure ASGI (not BaseHTTPMiddleware) so SSE/streaming responses are untouched. The logged route is the
matched path *template* (e.g. /api/v1/leads/{lead_id}) to keep log fields low-cardinality; query strings
are never logged.
"""
from __future__ import annotations

import time
from typing import Any

from .logging import bind, get_logger
from .tracing import http_context

_QUIET = ("/api/v1/health", "/healthz", "/livez", "/readyz")


class RequestObservability:
    def __init__(self, app: Any, service: str):
        self.app = app
        self.log = get_logger(f"{service}.http")

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        ctx = http_context(headers)
        status_holder = {"code": 500}
        started = time.perf_counter()

        async def _send(message):
            if message["type"] == "http.response.start":
                status_holder["code"] = message["status"]
                message.setdefault("headers", [])
                message["headers"] = list(message["headers"]) + [(b"x-request-id", ctx["request_id"].encode())]
            await send(message)

        with bind(**ctx):
            try:
                await self.app(scope, receive, _send)
            finally:
                route = scope.get("route")
                path = getattr(route, "path", None) or "<unmatched>"
                code = status_holder["code"]
                level = 20 if code < 500 else 40
                if path in _QUIET and code < 500:
                    level = 10
                self.log.log(level, "http request", extra={"ctx": {
                    "operation": f"{scope.get('method', '')} {path}", "status": code,
                    "outcome": "ok" if code < 400 else ("client_error" if code < 500 else "error"),
                    "duration_ms": int((time.perf_counter() - started) * 1000)}})


def install_request_observability(app: Any, service: str) -> None:
    app.add_middleware(RequestObservability, service=service)
