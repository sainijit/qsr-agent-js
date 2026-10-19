#!/usr/bin/env python3
"""Local end-to-end trace of one WhatsApp message through the whole stack, without a public URL.

It sends a payload signed exactly as Meta would sign it to the gateway on localhost, then checks every hop:
  webhook verify -> signature check -> gateway -> Kafka -> persister -> Postgres -> ai-router (Gemini)
  -> dashboard-api feed + SSE -> outbound reply -> dispatch -> persisted OUTBOUND -> no DLQ growth.

Usage (stack must be running: docker compose up -d):
  python3 scripts/e2e_local.py [--text "..."] [--line PNID_002] [--phone 919900000001]
Standard library only. Reads META_APP_SECRET / META_VERIFY_TOKEN from ./.env.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # never route localhost via a proxy

GREEN, RED, YELLOW, DIM, BOLD, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[1m", "\033[0m"
failures: list[str] = []


def ok(msg: str) -> None:
    print(f"  {GREEN}✔{RESET} {msg}")


def fail(msg: str) -> None:
    failures.append(msg)
    print(f"  {RED}✘ {msg}{RESET}")


def warn(msg: str) -> None:
    print(f"  {YELLOW}!{RESET} {msg}")


def step(n: int, title: str) -> None:
    print(f"\n{BOLD}[{n}] {title}{RESET}")


def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    path = ROOT / ".env"
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.split(" #", 1)[0].strip().strip('"').strip("'")
    return {**env, **{k: v for k, v in os.environ.items() if k.startswith(("META_", "ZUVO_"))}}


def http(method: str, url: str, body: bytes | None = None, headers: dict | None = None, timeout: float = 30):
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with OPENER.open(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def api(method: str, base: str, path: str, token: str | None = None, payload: dict | None = None, params: dict | None = None):
    url = base + path + ("?" + urllib.parse.urlencode(params) if params else "")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    status, raw = http(method, url, json.dumps(payload).encode() if payload is not None else None, headers)
    try:
        return status, json.loads(raw or b"null")
    except json.JSONDecodeError:
        return status, raw.decode(errors="replace")


def wait_for(fn, timeout: float, interval: float = 0.5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        result = fn()
        if result:
            return result
        time.sleep(interval)
    return None


def dlq_offsets() -> dict[str, int] | None:
    cmd = ["docker", "compose", "exec", "-T", "kafka", "/opt/kafka/bin/kafka-get-offsets.sh",
           "--bootstrap-server", "kafka:9092", "--topic", ".*\\.DLQ", "--time", "-1"]
    try:
        out = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    totals: dict[str, int] = {}
    for line in out.stdout.splitlines():
        parts = line.strip().split(":")
        if len(parts) == 3 and parts[2].isdigit():
            totals[parts[0]] = totals.get(parts[0], 0) + int(parts[2])
    return totals


class SseCollector(threading.Thread):
    def __init__(self, url: str):
        super().__init__(daemon=True)
        self.url, self.events, self.connected = url, [], threading.Event()

    def run(self) -> None:
        try:
            with OPENER.open(urllib.request.Request(self.url, headers={"Accept": "text/event-stream"}), timeout=300) as r:
                self.connected.set()
                for raw in r:
                    line = raw.decode(errors="replace").rstrip("\n")
                    if line.startswith("data:"):
                        try:
                            self.events.append(json.loads(line[5:].strip()))
                        except json.JSONDecodeError:
                            pass
        except Exception:  # noqa: BLE001 - collector ends when the script exits
            self.connected.set()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ui", default="http://localhost:3000", help="frontend/nginx base URL")
    ap.add_argument("--gateway", default=f"http://localhost:{os.environ.get('GATEWAY_PORT', '8000')}", help="gateway base URL (tunnel target)")
    ap.add_argument("--tenant", default="3f6c2a10-6d1e-4f0b-9a51-1c2d3e4f5a01", help="tenant id (default Jaipur)")
    ap.add_argument("--login-as", default="rajesh-owner", help="dev identity worker_key")
    ap.add_argument("--line", default="PNID_002", help="Meta phone_number_id of the receiving staff line")
    ap.add_argument("--phone", default=None, help="customer phone (default: unique test number)")
    ap.add_argument("--name", default="E2E Local Test")
    ap.add_argument("--text", default="Bhaiya 20mm CPVC pipe ke 50 box aur 10 elbow kal subah tak bhej do, payment Friday ko karenge.")
    ap.add_argument("--reply", default="Order confirm hai, dispatch kal subah 10 baje tak.")
    ap.add_argument("--ai-timeout", type=float, default=90)
    args = ap.parse_args()

    env = load_env()
    secret, verify_token = env.get("META_APP_SECRET", ""), env.get("META_VERIFY_TOKEN", "")
    phone = args.phone or f"9199{int(time.time()) % 10**8:08d}"
    wamid = f"wamid.local.{uuid.uuid4().hex}"
    print(f"{BOLD}ZuvoCloud local E2E{RESET}  {DIM}customer={phone} line={args.line} wamid={wamid[:28]}…{RESET}")

    step(1, "Health")
    for name, base in (("dashboard-api (via nginx)", args.ui), ("gateway", args.gateway)):
        s, body = api("GET", base, "/api/v1/health")
        ok(f"{name}: {body.get('status')}") if s == 200 and isinstance(body, dict) else fail(f"{name} unreachable ({s})")
    if failures:
        print(f"\n{RED}Stack not reachable. Start it with: docker compose up -d{RESET}")
        return 1
    before_dlq = dlq_offsets()

    step(2, "Meta webhook verification handshake (GET)")
    if verify_token:
        challenge = uuid.uuid4().hex[:10]
        q = urllib.parse.urlencode({"hub.mode": "subscribe", "hub.verify_token": verify_token, "hub.challenge": challenge})
        s, raw = http("GET", f"{args.gateway}/api/v1/whatsapp/webhook?{q}")
        ok("challenge echoed") if s == 200 and raw.decode() == challenge else fail(f"verify returned {s}")
        s, _ = http("GET", f"{args.gateway}/api/v1/whatsapp/webhook?hub.mode=subscribe&hub.verify_token=wrong&hub.challenge=x")
        ok("wrong verify token rejected (403)") if s == 403 else fail(f"wrong verify token returned {s}")
    else:
        warn("META_VERIFY_TOKEN not set; skipped")

    step(3, "Dashboard login + live stream (SSE)")
    s, login = api("POST", args.ui, "/api/v1/auth/dev-login", payload={"tenant_id": args.tenant, "worker_key": args.login_as})
    if s != 200:
        fail(f"dev-login failed ({s}): {login}")
        return 1
    token = login["access_token"]
    ok(f"logged in as {args.login_as} ({login['role']})")
    sse = SseCollector(f"{args.ui}/api/v1/dashboard/stream?access_token={urllib.parse.quote(token)}")
    sse.start()
    sse.connected.wait(5)
    time.sleep(1)
    ok("SSE stream connected") if sse.is_alive() else fail("SSE stream did not connect")

    step(4, "Inbound webhook exactly as Meta sends it")
    body = json.dumps({"object": "whatsapp_business_account", "entry": [{"id": "WABA_LOCAL", "changes": [{"field": "messages", "value": {
        "messaging_product": "whatsapp",
        "metadata": {"display_phone_number": "local", "phone_number_id": args.line},
        "contacts": [{"profile": {"name": args.name}, "wa_id": phone}],
        "messages": [{"from": phone, "id": wamid, "timestamp": str(int(time.time())), "type": "text", "text": {"body": args.text}}],
    }}]}]}).encode()
    hook = f"{args.gateway}/api/v1/whatsapp/webhook"
    s, _ = http("POST", hook, body, {"Content-Type": "application/json", "X-Hub-Signature-256": "sha256=" + "0" * 64})
    ok("forged signature rejected (401)") if s == 401 else warn(f"forged signature returned {s} (REQUIRE_HMAC_VERIFICATION off?)")
    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest() if secret else ""
    t0 = time.time()
    s, raw = http("POST", hook, body, {"Content-Type": "application/json", "X-Hub-Signature-256": sig})
    ack_ms = (time.time() - t0) * 1000
    if s == 200:
        ok(f"signed webhook accepted in {ack_ms:.0f} ms {DIM}{raw.decode()[:120]}{RESET}")
        (ok if ack_ms < 2000 else warn)(f"ack latency {'within' if ack_ms < 2000 else 'over'} Meta's ~2 s budget")
    else:
        fail(f"signed webhook returned {s}: {raw.decode()[:200]}")
        return 1
    s, _ = http("POST", hook, body, {"Content-Type": "application/json", "X-Hub-Signature-256": sig})
    ok("Meta retry (same wamid) accepted for dedupe") if s == 200 else fail(f"duplicate delivery returned {s}")

    def thread_msgs():
        st, data = api("GET", args.ui, "/api/v1/dashboard/feed", token, params={"customer_phone": phone, "limit": 50})
        return data.get("messages", []) if st == 200 and isinstance(data, dict) else []

    step(5, "Persisted to Postgres (gateway → Kafka → persister)")
    msgs = wait_for(lambda: [m for m in thread_msgs() if m["wamid"] == wamid], timeout=30)
    message_id = msgs[0]["id"] if msgs else None
    if msgs:
        m = msgs[0]
        ok(f"stored in {time.time() - t0:.1f} s · worker={m['worker_name']} · customer={m['customer_name']}")
        time.sleep(1.5)
        n = len([x for x in thread_msgs() if x["wamid"] == wamid])
        ok("duplicate delivery deduplicated (1 row)") if n == 1 else fail(f"{n} rows for one wamid")
    else:
        fail("message never appeared in the feed")
        return 1

    step(6, "AI extraction (ai-router → Gemini → persister)")
    m = wait_for(lambda: next((x for x in thread_msgs() if x["wamid"] == wamid and x.get("ai_status") in ("EXTRACTED", "NEEDS_REVIEW", "AI_PENDING", "SKIPPED")), None),
                 timeout=args.ai_timeout, interval=1)
    if not m:
        fail(f"no AI result within {args.ai_timeout:.0f} s (check: docker compose logs ai-router)")
    else:
        extraction = m.get("extraction") or {}
        reason = extraction.get("reason")
        line = f"{m['ai_status']} in {time.time() - t0:.1f} s · intent={m.get('parsed_intent')} · confidence={m.get('confidence_score')}"
        if reason == "ai_unavailable" or m["ai_status"] == "AI_PENDING":
            fail(f"{line} · Gemini unavailable (key/egress/quota)")
        else:
            ok(line)
        if extraction:
            print(f"    {DIM}{json.dumps(extraction, ensure_ascii=False)[:600]}{RESET}")

    step(7, "Live push to dashboard (SSE)")
    got = wait_for(lambda: [e for e in sse.events if e.get("message_id") == message_id], timeout=10)
    if got:
        ok(f"{len(got)} SSE event(s) for this message: " + ", ".join(sorted({str(e.get('change')) for e in got})))
    else:
        fail(f"no SSE event for this message ({len(sse.events)} total received)")

    step(8, "Outbound reply (dashboard-api → Kafka → dispatch → persister)")
    s, res = api("POST", args.ui, "/api/v1/messages/send", token, payload={"recipient_phone": phone, "message_text": args.reply, "sender_number_id": args.line})
    if s == 202:
        ok(f"reply queued · request_id={res['request_id'][:8]}…")
        t1 = time.time()
        out = wait_for(lambda: [x for x in thread_msgs() if x["direction"] == "OUTBOUND" and x["content"] == args.reply], timeout=30)
        if out:
            o = out[0]
            dry = env.get("META_DRY_RUN", "true").lower() == "true"
            ok(f"OUTBOUND persisted in {time.time() - t1:.1f} s · from={o['worker_name']} · status={o.get('status')}"
               f"{' (META_DRY_RUN: not sent to Meta)' if dry else ''}")
        else:
            fail("outbound reply never persisted (check: docker compose logs dispatch persister)")
    else:
        fail(f"send returned {s}: {res}")

    step(9, "Dead-letter queues")
    after_dlq = dlq_offsets()
    if before_dlq is None or after_dlq is None:
        warn("could not read DLQ offsets (docker compose exec kafka failed)")
    else:
        grew = {t: after_dlq.get(t, 0) - before_dlq.get(t, 0) for t in after_dlq if after_dlq.get(t, 0) != before_dlq.get(t, 0)}
        ok(f"no new DLQ records {DIM}(totals: {sum(after_dlq.values())}){RESET}") if not grew else fail(f"DLQ grew during the run: {grew}")

    print()
    if failures:
        print(f"{RED}{BOLD}FAILED{RESET} {len(failures)} check(s): " + "; ".join(failures))
        return 1
    print(f"{GREEN}{BOLD}PASSED{RESET} end-to-end · open http://localhost:3000 and select +{phone} to see the thread")
    return 0


if __name__ == "__main__":
    sys.exit(main())
