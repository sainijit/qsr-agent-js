"""Gemini API stand-in for load tests: no paid requests, controllable latency, errors and outages.

The google-genai SDK honours GOOGLE_GEMINI_BASE_URL, so services are pointed here by
deploy/loadtest/docker-compose.loadtest.yml without code changes. Never used in production
(the overlay is only added explicitly via ZUVO_COMPOSE_EXTRA).

Endpoints
  POST /v1beta/models/<m>:generateContent      extraction (schema has the Intent enum), draft (schema has "reply"), or text
  POST /v1beta/models/<m>:embedContent | :batchEmbedContents   feature-hashed bag-of-words unit vectors (requested dim)
  GET  /_stats                                 request counts, injected errors, server-side latency percentiles
  POST /_control  {"outage": bool, "latency_ms": int, "jitter_ms": int, "error_rate": float, "reset": bool}

Env defaults: MOCK_LATENCY_MS=700 (generate), MOCK_EMBED_LATENCY_MS=120, MOCK_JITTER_MS=250, MOCK_ERROR_RATE=0.0
(the defaults approximate Flash-Lite generate / embedding latency; they are assumptions, not measurements of Gemini).
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import random
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOCK = threading.Lock()
CFG = {
    "outage": False,
    "latency_ms": int(os.getenv("MOCK_LATENCY_MS", "700")),
    "embed_latency_ms": int(os.getenv("MOCK_EMBED_LATENCY_MS", "120")),
    "jitter_ms": int(os.getenv("MOCK_JITTER_MS", "250")),
    "error_rate": float(os.getenv("MOCK_ERROR_RATE", "0")),
}
STATS: dict = {}


def _reset():
    STATS.clear()
    STATS.update({"requests": {}, "errors_injected": 0, "outage_rejects": 0, "latency_ms": []})


_reset()


def _vector(text: str, dim: int) -> list[float]:
    """Feature-hashed bag of words plus a shared component. Every pair of texts has cosine >= ~0.7, so retrieval
    clears the default min_similarity (0.6) and the full RAG -> drafter path is exercised; ranking follows word
    overlap. Retrieval *quality* under the mock is meaningless - this only produces realistic load."""
    v = [0.0] * dim
    for w in re.findall(r"[a-z0-9]{3,}", text.lower()):
        h = int.from_bytes(hashlib.sha256(w.encode()).digest()[:8], "big")
        v[h % dim] += 1.0 if (h >> 40) & 1 else -1.0
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    v = [x / n for x in v]
    v[dim - 1] += SHARED
    n = math.sqrt(sum(x * x for x in v))
    return [round(x / n, 6) for x in v]


SHARED = math.sqrt(2.5)  # cos(a, b) = (2.5 + bow_a . bow_b) / 3.5


def _texts(contents) -> list[str]:
    out = []
    for c in contents if isinstance(contents, list) else [contents]:
        if isinstance(c, dict):
            out.append(" ".join(p.get("text", "") for p in c.get("parts", []) if isinstance(p, dict)))
        else:
            out.append(str(c))
    return out


def _dim(obj: dict) -> int:
    return int(obj.get("outputDimensionality") or (obj.get("config") or {}).get("outputDimensionality") or 768)


def _generate(body: dict) -> dict:
    gc = body.get("generationConfig") or {}
    schema = json.dumps(gc.get("responseSchema") or gc.get("responseJsonSchema") or {})
    user = " ".join(_texts(body.get("contents") or []))
    if "CREATE_ORDER" in schema:  # ai-router extraction
        msg = re.sub(r"\s+", " ", user)[:200]
        greeting = bool(re.match(r"^\s*(hi|hello|namaste)\b", msg, re.I))
        text = json.dumps({"intent": "GENERAL_CHAT" if greeting else "FAQ", "confidence": 0.91, "lang": "english",
                           "question": None if greeting else msg})
    elif '"reply"' in schema:      # agent-worker draft
        text = json.dumps({"reply": "Thanks for your message. Our showroom team will help you shortly. (load-test mock)"})
    else:
        text = "Mock answer for load testing."
    return {"candidates": [{"content": {"role": "model", "parts": [{"text": text}]}, "finishReason": "STOP", "index": 0}],
            "usageMetadata": {"promptTokenCount": 350, "candidatesTokenCount": 40, "totalTokenCount": 390},
            "modelVersion": "mock"}


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):  # quiet
        pass

    def _send(self, code: int, obj: dict):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.startswith("/_stats"):
            with LOCK:
                lat = sorted(STATS["latency_ms"])
                pct = lambda q: lat[min(len(lat) - 1, int(q * len(lat)))] if lat else None  # noqa: E731
                return self._send(200, {"config": CFG, "requests": STATS["requests"], "errors_injected": STATS["errors_injected"],
                                        "outage_rejects": STATS["outage_rejects"],
                                        "latency_ms": {"n": len(lat), "p50": pct(.5), "p95": pct(.95), "p99": pct(.99)}})
        self._send(200, {"status": "ok"})

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        if self.path.startswith("/_control"):
            req = json.loads(raw or b"{}")
            with LOCK:
                if req.pop("reset", False):
                    _reset()
                CFG.update({k: v for k, v in req.items() if k in CFG})
            return self._send(200, CFG)
        m = re.search(r":(generateContent|embedContent|batchEmbedContents)$", self.path.split("?")[0])
        if not m:
            return self._send(404, {"error": {"code": 404, "message": "unknown mock path", "status": "NOT_FOUND"}})
        op = m.group(1)
        with LOCK:
            STATS["requests"][op] = STATS["requests"].get(op, 0) + 1
            cfg = dict(CFG)
        if cfg["outage"]:
            with LOCK:
                STATS["outage_rejects"] += 1
            return self._send(503, {"error": {"code": 503, "message": "mock outage", "status": "UNAVAILABLE"}})
        base = cfg["latency_ms"] if op == "generateContent" else cfg["embed_latency_ms"]
        delay = max(0, base + random.randint(-cfg["jitter_ms"], cfg["jitter_ms"])) / 1000 if base else 0
        time.sleep(delay)
        with LOCK:
            STATS["latency_ms"].append(int(delay * 1000))
        if random.random() < cfg["error_rate"]:
            with LOCK:
                STATS["errors_injected"] += 1
            return self._send(503, {"error": {"code": 503, "message": "mock overloaded", "status": "UNAVAILABLE"}})
        body = json.loads(raw or b"{}")
        if op == "generateContent":
            return self._send(200, _generate(body))
        if op == "embedContent":
            return self._send(200, {"embedding": {"values": _vector(" ".join(_texts(body.get("content", {}))), _dim(body))}})
        reqs = body.get("requests") or []
        return self._send(200, {"embeddings": [{"values": _vector(" ".join(_texts(r.get("content", {}))), _dim(r))} for r in reqs]})


if __name__ == "__main__":
    port = int(os.getenv("MOCK_PORT", "8080"))
    ThreadingHTTPServer.daemon_threads = True
    print(f"gemini mock on :{port} {CFG}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", port), H).serve_forever()
