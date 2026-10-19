#!/usr/bin/env python3
"""ZuvoCloud load test driver (pilot scale). TEST ENVIRONMENTS ONLY.

Sends Meta-signed synthetic WhatsApp webhooks through the real edge (Caddy -> nginx -> gateway) and follows them through
Kafka -> persister -> ai-router -> lead-service -> agent-worker (-> RAG, drafter) while sampling container CPU/memory,
Kafka consumer lag and PostgreSQL connections. Gemini must be the local mock (deploy/loadtest/docker-compose.loadtest.yml)
and META_DRY_RUN=true, so no paid AI calls and no real WhatsApp messages are produced.

Scenarios
  steady   --rate R --duration D            R messages/s for D seconds
  burst    --count N --concurrency C        N messages as fast as C workers can send them
  outage   --rate R --duration D --outage-at T --outage-for O
                                            steady load; the Gemini mock returns 503 from T to T+O seconds
  api      --count N --concurrency C        dashboard read APIs with a minted JWT (p50/p95/p99)
  rag      --count N --concurrency C        direct RAG /retrieve calls from inside the compose network
  sample   --duration D                     only sample resources (e.g. while restarting the stack: startup peaks)

Usage: COMPOSE_PROJECT_NAME=... ZUVO_ENV_FILE=... ZUVO_STATE_DIR=... \\
       python3 scripts/loadtest/zuvo_load.py steady --rate 1 --duration 120 --out /tmp/lt-steady.json
Safety: refuses unless PUBLIC_DOMAIN=localhost (or --allow-remote-test-host), META_DRY_RUN=true and the mock is active.
"""
from __future__ import annotations

import argparse
import base64
import concurrent.futures as cf
import hashlib
import hmac
import json
import os
import random
import ssl
import statistics
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import preflight  # noqa: E402  (env-file parser)

DCP = [str(ROOT / "scripts/deploy/dcp")]
QUESTIONS = ["what is the on-road price of the new SUV?", "do you have test drive slots this weekend?",
             "what are your showroom timings on sunday?", "is there an exchange offer on my old car?",
             "what is the waiting period for the automatic variant?", "hi", "do you offer car loans with low interest?",
             "can I book a service appointment?", "which colours are available?", "what is the mileage of the diesel model?"]


# ---------------------------------------------------------------------------------------------------------------- utils
def pct(values, q):
    if not values:
        return None
    s = sorted(values)
    return round(s[min(len(s) - 1, int(q * len(s)))], 1)


def summary(values):
    return {"n": len(values), "p50": pct(values, .5), "p95": pct(values, .95), "p99": pct(values, .99),
            "max": round(max(values), 1) if values else None, "mean": round(statistics.mean(values), 1) if values else None}


def dc(*args, input_=None, timeout=60) -> str:
    r = subprocess.run(DCP + list(args), input=input_, capture_output=True, text=True, timeout=timeout)
    return r.stdout


def psql(sql: str) -> list[list[str]]:
    out = dc("exec", "-T", "postgres", "psql", "-X", "-U", "zuvo_owner", "-d", "zuvo_db", "-tAq", "-F", "\t", "-c", sql)
    return [line.split("\t") for line in out.splitlines() if line.strip()]


def mock(path: str, body: dict | None = None) -> dict:
    code = ("import json,sys,urllib.request as u;"
            "d=sys.stdin.read().encode() or None;"
            f"r=u.urlopen(u.Request('http://gemini-mock:8080{path}',data=d,headers={{'Content-Type':'application/json'}}),timeout=5);"
            "print(r.read().decode())")
    out = dc("exec", "-T", "ai-router", "python", "-c", code, input_=json.dumps(body) if body is not None else "")
    return json.loads(out) if out.strip().startswith("{") else {}


# ---------------------------------------------------------------------------------------------------------------- sampler
class Sampler(threading.Thread):
    def __init__(self, project: str, interval: float):
        super().__init__(daemon=True)
        self.project, self.interval, self.stop_ev, self.samples = project, interval, threading.Event(), []

    def _docker_stats(self):
        out = subprocess.run(["docker", "stats", "--no-stream", "--format", "{{json .}}"], capture_output=True, text=True,
                             timeout=30).stdout
        res = {}
        for line in out.splitlines():
            d = json.loads(line)
            name = d["Name"]
            if not name.startswith(self.project + "-"):
                continue
            svc = name[len(self.project) + 1:].rsplit("-", 1)[0]
            mem = d["MemUsage"].split("/")[0].strip()
            mult = {"KiB": 1 / 1024, "MiB": 1, "GiB": 1024, "B": 1 / 1048576}
            unit = next(u for u in ("KiB", "MiB", "GiB", "B") if mem.endswith(u))
            res[svc] = {"cpu": float(d["CPUPerc"].rstrip("%") or 0), "mem_mib": round(float(mem[:-len(unit)]) * mult[unit], 1)}
        return res

    def _lag(self):
        # opsmetrics (Python/aiokafka in dashboard-api) instead of kafka-consumer-groups.sh: the CLI starts a JVM inside
        # the kafka container on every sample and would distort that container's CPU/memory figures
        out = dc("exec", "-T", "dashboard-api", "python", "-m", "zuvo_common.opsmetrics", timeout=60)
        lag: dict = {}
        for m in json.loads(out or "[]"):
            if m["name"] == "zuvo_kafka_consumer_lag":
                g = m["labels"].get("group", "?")
                lag[g] = lag.get(g, 0) + int(m["value"])
        return lag

    def _pg(self):
        rows = psql("SELECT usename, state, count(*) FROM pg_stat_activity WHERE datname = 'zuvo_db' GROUP BY 1, 2")
        return {f"{r[0]}:{r[1] or 'none'}": int(r[2]) for r in rows if len(r) == 3}

    def run(self):
        t0 = time.time()
        while not self.stop_ev.is_set():
            s = {"t": round(time.time() - t0, 1)}
            for key, fn in (("containers", self._docker_stats), ("lag", self._lag), ("pg", self._pg)):
                try:
                    s[key] = fn()
                except Exception as exc:  # noqa: BLE001 - a failed sample must not stop the test
                    s[key] = {"error": type(exc).__name__}
            self.samples.append(s)
            self.stop_ev.wait(self.interval)

    def report(self):
        peaks: dict = {}
        for s in self.samples:
            for svc, v in (s.get("containers") or {}).items():
                if not isinstance(v, dict) or "cpu" not in v:
                    continue
                p = peaks.setdefault(svc, {"cpu_max": 0, "mem_max_mib": 0, "cpu": [], "mem": []})
                p["cpu_max"] = max(p["cpu_max"], v["cpu"])
                p["mem_max_mib"] = max(p["mem_max_mib"], v["mem_mib"])
                p["cpu"].append(v["cpu"])
                p["mem"].append(v["mem_mib"])
        containers = {k: {"cpu_avg": round(statistics.mean(v["cpu"]), 1), "cpu_max": v["cpu_max"],
                          "mem_avg_mib": round(statistics.mean(v["mem"]), 1), "mem_max_mib": v["mem_max_mib"]}
                      for k, v in sorted(peaks.items())}
        totals = [sum(v["mem_mib"] for v in (s.get("containers") or {}).values() if isinstance(v, dict) and "mem_mib" in v)
                  for s in self.samples]
        lag_peak: dict = {}
        for s in self.samples:
            for g, v in (s.get("lag") or {}).items():
                if isinstance(v, int):
                    lag_peak[g] = max(lag_peak.get(g, 0), v)
        pg_peak = max((sum(v for v in (s.get("pg") or {}).values() if isinstance(v, int)) for s in self.samples), default=0)
        return {"samples": len(self.samples), "containers": containers,
                "stack_mem_mib": {"avg": round(statistics.mean(totals), 1) if totals else None, "max": max(totals, default=None)},
                "kafka_lag_peak": lag_peak,
                "kafka_lag_timeline": [{"t": s["t"], **{g: v for g, v in (s.get("lag") or {}).items() if isinstance(v, int) and v}}
                                       for s in self.samples],
                "pg_connections_peak": pg_peak}


# ---------------------------------------------------------------------------------------------------------------- driver
class Driver:
    def __init__(self, a, env):
        self.a, self.env = a, env
        domain, port = env.get("PUBLIC_DOMAIN", "localhost"), env.get("HTTPS_PORT", "443")
        self.base = a.base or (f"https://{domain}" + ("" if port == "443" else f":{port}"))
        self.ctx = ssl.create_default_context()
        if domain == "localhost":
            self.ctx.check_hostname, self.ctx.verify_mode = False, ssl.CERT_NONE
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=self.ctx))
        self.run_id = f"lt{int(time.time())}"
        self.phone_prefix = f"9199{random.randint(0, 999):03d}"  # synthetic, unique per run
        self.sent: list[dict] = []
        self.lock = threading.Lock()

    def _req(self, method, path, body=None, headers=None):
        req = urllib.request.Request(self.base + path, data=body, method=method, headers=headers or {})
        t0 = time.perf_counter()
        try:
            with self.opener.open(req, timeout=30) as r:
                r.read()
                code = r.status
        except urllib.error.HTTPError as e:
            code = e.code
        except Exception:  # noqa: BLE001
            code = 0
        return code, (time.perf_counter() - t0) * 1000

    def send_one(self, i: int):
        lines = self.a.lines.split(",")
        phone = f"{self.phone_prefix}{i % self.a.customers:05d}"
        wamid = f"wamid.{self.run_id}.{i}"
        now = int(time.time())
        payload = {"object": "whatsapp_business_account", "entry": [{"id": "lt", "changes": [{"field": "messages", "value": {
            "messaging_product": "whatsapp", "metadata": {"display_phone_number": "x", "phone_number_id": lines[i % len(lines)]},
            "contacts": [{"wa_id": phone, "profile": {"name": f"Load Test {i % self.a.customers}"}}],
            "messages": [{"from": phone, "id": wamid, "timestamp": str(now), "type": "text",
                          "text": {"body": random.choice(QUESTIONS)}}]}}]}]}
        body = json.dumps(payload).encode()
        sig = "sha256=" + hmac.new(self.env["META_APP_SECRET"].encode(), body, hashlib.sha256).hexdigest()
        sent_at = time.time()
        code, ms = self._req("POST", "/api/v1/whatsapp/webhook", body,
                             {"Content-Type": "application/json", "X-Hub-Signature-256": sig})
        with self.lock:
            self.sent.append({"wamid": wamid, "sent_at": sent_at, "code": code, "ms": ms, "phone": phone})

    def steady(self, rate, duration, on_tick=None):
        start, i = time.time(), 0
        with cf.ThreadPoolExecutor(max_workers=32) as ex:
            while (el := time.time() - start) < duration:
                if on_tick:
                    on_tick(el)
                target = int(el * rate)
                while i < target:
                    ex.submit(self.send_one, i)
                    i += 1
                time.sleep(0.02)

    def burst(self, count, conc):
        with cf.ThreadPoolExecutor(max_workers=conc) as ex:
            list(ex.map(self.send_one, range(count)))

    def pipeline(self, settle_timeout):
        """Wait until every accepted message is persisted and has an agent run (or timeout), then compute latencies."""
        accepted = [s for s in self.sent if s["code"] == 200]
        # drained = every message persisted, none still awaiting extraction, and the agent-run count stable for 2 polls
        deadline, rows, prev_runs, stable = time.time() + settle_timeout, [], -1, 0
        t_start = time.time()
        while time.time() < deadline:
            rows = psql(f"""SELECT m.wamid, extract(epoch from m.created_at), m.ai_status, m.parsed_intent,
                              extract(epoch from r.created_at), r.decision
                            FROM messages m LEFT JOIN agent_runs r ON r.message_id = m.id
                            WHERE m.wamid LIKE 'wamid.{self.run_id}.%'""")
            runs = sum(1 for r in rows if r[4])
            extracting = sum(1 for r in rows if r[2] in ("PENDING", "AI_PENDING"))
            stable = stable + 1 if runs == prev_runs else 0
            prev_runs = runs
            if len(rows) >= len(accepted) and extracting == 0 and stable >= 2:
                break
            time.sleep(3)
        settled = round(time.time() - t_start, 1)
        sent_at = {s["wamid"]: s["sent_at"] for s in accepted}
        runs_total = sum(1 for r in rows if r[4])
        first: dict = {}  # one row per message: a message can yield several agent runs (e.g. faq + visit_ack)
        for r in rows:
            if r[0] not in first or (r[4] and (not first[r[0]][4] or float(r[4]) < float(first[r[0]][4]))):
                first[r[0]] = r
        rows = list(first.values())
        persist = [(float(r[1]) - sent_at[r[0]]) * 1000 for r in rows if r[0] in sent_at]
        intents: dict = {}
        for r in rows:
            intents[r[3] or "NULL"] = intents.get(r[3] or "NULL", 0) + 1
        agent = [(float(r[4]) - sent_at[r[0]]) * 1000 for r in rows if r[0] in sent_at and r[4]]
        ai_status: dict = {}
        for r in rows:
            ai_status[r[2] or "NULL"] = ai_status.get(r[2] or "NULL", 0) + 1
        out = psql(f"""SELECT recipient_phone, status, extract(epoch from created_at), extract(epoch from accepted_at)
                       FROM outbound_messages WHERE recipient_phone LIKE '{self.phone_prefix}%'""")
        by_phone: dict = {}
        for snt in accepted:
            by_phone.setdefault(snt["phone"], []).append(snt["sent_at"])
        reply_ms, ob_status = [], {}
        for o in out:
            ob_status[o[1]] = ob_status.get(o[1], 0) + 1
            prior = [t for t in by_phone.get(o[0], []) if t <= float(o[2])]
            if prior and o[3]:
                reply_ms.append((float(o[3]) - max(prior)) * 1000)
        outbound = {"rows": len(out), "status": ob_status, "inbound_to_accepted_ms": summary(reply_ms)}
        return {"outbound": outbound, "accepted": len(accepted), "persisted": len(rows), "with_agent_run": len(agent), "agent_runs_total": runs_total,
                "ai_status": ai_status, "intents": intents, "persist_latency_ms": summary(persist), "to_agent_run_ms": summary(agent),
                "drain_seconds_after_load": settled, "drained": time.time() < deadline}

    def webhook_report(self):
        codes: dict = {}
        for s in self.sent:
            codes[str(s["code"])] = codes.get(str(s["code"]), 0) + 1
        return {"sent": len(self.sent), "status_codes": codes,
                "latency_ms": summary([s["ms"] for s in self.sent if s["code"] == 200])}

    def jwt(self):
        tenant = self.a.tenant or psql("SELECT tenant_id FROM whatsapp_numbers WHERE meta_phone_number_id = "
                                       f"'{self.a.lines.split(',')[0]}'")[0][0]
        b64 = lambda d: base64.urlsafe_b64encode(json.dumps(d, separators=(",", ":")).encode()).rstrip(b"=").decode()  # noqa: E731
        now = int(time.time())
        h, p = b64({"alg": "HS256", "typ": "JWT"}), b64({"sub": "loadtest", "tenant_id": tenant, "role": "OWNER", "worker_id": None,
                                                        "iat": now, "exp": now + 900, "iss": "zuvocloud"})
        sig = base64.urlsafe_b64encode(hmac.new(self.env["JWT_SECRET"].encode(), f"{h}.{p}".encode(), hashlib.sha256).digest())
        return f"{h}.{p}.{sig.rstrip(b'=').decode()}"

    def api(self, count, conc, rate=0.0):
        token = self.jwt()
        paths = ["/api/v1/auth/me", "/api/v1/dashboard/today", "/api/v1/leads", "/api/v1/dashboard/customers", "/api/v1/approvals"]
        res: dict = {p: [] for p in paths}
        codes: dict = {}
        t0 = time.time()

        def one(i):
            if rate > 0:  # pace below the nginx per-IP limit (30 r/s) so latency, not 429s, is measured
                time.sleep(max(0.0, t0 + i / rate - time.time()))
            p = paths[i % len(paths)]
            code, ms = self._req("GET", p, headers={"Authorization": f"Bearer {token}"})
            with self.lock:
                codes[f"{p} {code}"] = codes.get(f"{p} {code}", 0) + 1
                if code == 200:
                    res[p].append(ms)
        with cf.ThreadPoolExecutor(max_workers=conc) as ex:
            list(ex.map(one, range(count)))
        return {"status_codes": codes, "latency_ms": {p: summary(v) for p, v in res.items()},
                "all_200_ms": summary([x for v in res.values() for x in v])}

    def approve(self, conc, settle_timeout):
        """Approve this run's PENDING drafts via the dashboard API, then time decided_at -> outbound accepted (dry-run)."""
        ids = [r[0] for r in psql(f"""SELECT a.id FROM approvals a JOIN agent_runs r ON r.id = a.agent_run_id
                                      JOIN messages m ON m.id = r.message_id
                                      WHERE m.wamid LIKE 'wamid.{self.run_id}.%' AND a.status = 'PENDING'""")]
        if not ids:
            return {"approvals": 0}
        token, codes, api_ms = self.jwt(), {}, []
        t_start = time.time()

        def one(aid):
            code, ms = self._req("POST", f"/api/v1/approvals/{aid}/decide", body=b'{"decision":"approve"}',
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
            with self.lock:
                codes[str(code)] = codes.get(str(code), 0) + 1
                if code == 200:
                    api_ms.append(ms)
        with cf.ThreadPoolExecutor(max_workers=conc) as ex:
            list(ex.map(one, ids))
        approved = codes.get("200", 0)
        deadline, out = time.time() + settle_timeout, []
        while time.time() < deadline:
            out = psql(f"""SELECT o.status, extract(epoch from d.decided_at), extract(epoch from o.created_at),
                                  extract(epoch from o.accepted_at)
                           FROM outbound_messages o
                           LEFT JOIN LATERAL (SELECT max(a.decided_at) AS decided_at FROM approvals a
                                              WHERE a.customer_id = o.customer_id AND a.decided_at <= o.created_at) d ON true
                           WHERE o.recipient_phone LIKE '{self.phone_prefix}%' AND o.created_at >= to_timestamp({t_start})""")
            if len(out) >= approved and all(o[0] not in ("PENDING", "SENDING") for o in out):
                break
            time.sleep(2)
        status: dict = {}
        for o in out:
            status[o[0]] = status.get(o[0], 0) + 1
        # decided_at = approve transaction (outbox write); created_at = dispatcher consumed it; accepted_at = Meta (dry-run) accepted
        to_row = [(float(o[2]) - float(o[1])) * 1000 for o in out if o[1]]
        to_acc = [(float(o[3]) - float(o[1])) * 1000 for o in out if o[1] and o[3]]
        return {"approvals": len(ids), "api_status_codes": codes, "decide_api_ms": summary(api_ms),
                "outbound_rows": len(out), "outbound_status": status, "decided_to_outbound_row_ms": summary(to_row),
                "decided_to_accepted_ms": summary(to_acc),
                "drain_seconds": round(time.time() - t_start, 1), "drained": time.time() < deadline}

    def rag(self, count, conc):
        tenant = self.a.tenant or psql("SELECT tenant_id FROM kb_documents GROUP BY 1 ORDER BY count(*) DESC LIMIT 1")[0][0]
        code = f"""
import json, os, time, urllib.request, concurrent.futures as cf
qs = {QUESTIONS!r}
url = 'http://zuvo-ai-rag:8020/v1/tenants/{tenant}/retrieve'
hdr = {{'Content-Type': 'application/json', 'Authorization': 'Bearer ' + os.environ['RAG_SERVICE_TOKEN']}}
def one(i):
    t0 = time.perf_counter()
    try:
        r = urllib.request.urlopen(urllib.request.Request(url, data=json.dumps({{'query': qs[i % len(qs)] + ' #' + str(i)}}).encode(), headers=hdr), timeout=10)
        body = json.loads(r.read()); return [r.status, (time.perf_counter() - t0) * 1000, body.get('latency_ms', {{}})]
    except urllib.error.HTTPError as e:
        return [e.code, (time.perf_counter() - t0) * 1000, {{}}]
with cf.ThreadPoolExecutor({conc}) as ex:
    print(json.dumps(list(ex.map(one, range({count})))))
"""
        out = dc("exec", "-T", "agent-worker", "python", "-c", code, timeout=600)
        rows = json.loads(out)
        codes: dict = {}
        for r in rows:
            codes[str(r[0])] = codes.get(str(r[0]), 0) + 1
        ok = [r for r in rows if r[0] == 200]
        stages = sorted({k for r in ok for k in r[2]})
        return {"tenant": tenant, "status_codes": codes, "client_ms": summary([r[1] for r in ok]),
                "server_stage_ms": {k: summary([r[2][k] for r in ok if k in r[2]]) for k in stages}}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("scenario", choices=["steady", "burst", "outage", "api", "rag", "sample"])
    ap.add_argument("--rate", type=float, default=1.0)
    ap.add_argument("--duration", type=float, default=60)
    ap.add_argument("--count", type=int, default=100)
    ap.add_argument("--concurrency", type=int, default=10)
    ap.add_argument("--outage-at", type=float, default=30)
    ap.add_argument("--outage-for", type=float, default=60)
    ap.add_argument("--lines", default="PNID_ALPHA_01", help="comma-separated Meta phone_number_ids of test lines")
    ap.add_argument("--customers", type=int, default=50, help="distinct synthetic customer numbers")
    ap.add_argument("--tenant", default="", help="tenant id for api/rag (default: derived)")
    ap.add_argument("--settle", type=float, default=300, help="seconds to wait for the pipeline to drain")
    ap.add_argument("--sample-interval", type=float, default=5)
    ap.add_argument("--base", default="")
    ap.add_argument("--allow-remote-test-host", action="store_true")
    ap.add_argument("--mock-error-rate", type=float, default=0.0, help="fraction of Gemini mock calls answered with 503")
    ap.add_argument("--api-rate", type=float, default=0.0, help="api scenario: requests/s pacing (0 = unpaced)")
    ap.add_argument("--approve", action="store_true", help="after the pipeline drains, approve the run's drafts via the API "
                    "and measure outbound dispatch (dry-run)")
    ap.add_argument("--out", default="")
    a = ap.parse_args()

    env = preflight.parse_env_file(Path(os.environ.get("ZUVO_ENV_FILE", ROOT / ".env")))
    if env.get("PUBLIC_DOMAIN", "localhost") != "localhost" and not a.allow_remote_test_host:
        sys.exit("refusing: PUBLIC_DOMAIN is not localhost (pass --allow-remote-test-host only for a disposable test VM)")
    if a.scenario in ("steady", "burst", "outage"):
        if env.get("META_DRY_RUN", "").lower() != "true":
            sys.exit("refusing: META_DRY_RUN must be true (no real WhatsApp messages)")
        base_url = dc("exec", "-T", "ai-router", "printenv", "GOOGLE_GEMINI_BASE_URL").strip()
        if "gemini-mock" not in base_url or not mock("/_stats"):
            sys.exit("refusing: ai-router is not using the Gemini mock (deploy/loadtest/docker-compose.loadtest.yml)")
        mock("/_control", {"reset": True, "outage": False, "error_rate": a.mock_error_rate})

    project = os.environ.get("COMPOSE_PROJECT_NAME", "zuvo-ai")
    d = Driver(a, env)
    sampler = Sampler(project, a.sample_interval)
    sampler.start()
    t0 = time.time()
    result: dict = {"scenario": a.scenario, "run_id": d.run_id, "args": {k: v for k, v in vars(a).items() if k != "out"},
                    "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    try:
        if a.scenario == "steady":
            d.steady(a.rate, a.duration)
        elif a.scenario == "burst":
            d.burst(a.count, a.concurrency)
        elif a.scenario == "outage":
            state = {"down": False, "up_at": None}

            def tick(el):
                if not state["down"] and a.outage_at <= el < a.outage_at + a.outage_for:
                    mock("/_control", {"outage": True})
                    state["down"] = True
                    result["outage_started_s"] = round(el, 1)
                elif state["down"] and el >= a.outage_at + a.outage_for:
                    mock("/_control", {"outage": False})
                    state["down"] = False
                    result["outage_ended_s"] = round(el, 1)
            d.steady(a.rate, a.duration, tick)
            if state["down"]:
                mock("/_control", {"outage": False})
                result["outage_ended_s"] = round(time.time() - t0, 1)
        elif a.scenario == "api":
            result["api"] = d.api(a.count, a.concurrency, a.api_rate)
        elif a.scenario == "rag":
            result["rag"] = d.rag(a.count, a.concurrency)
        else:
            time.sleep(a.duration)
        result["load_seconds"] = round(time.time() - t0, 1)
        if a.scenario in ("steady", "burst", "outage"):
            result["webhook"] = d.webhook_report()
            result["pipeline"] = d.pipeline(a.settle)
            if a.approve:
                result["approve"] = d.approve(a.concurrency, a.settle)
            result["gemini_mock"] = mock("/_stats")
    finally:
        sampler.stop_ev.set()
        sampler.join(timeout=60)
        if a.scenario in ("steady", "burst", "outage"):
            mock("/_control", {"outage": False, "error_rate": 0.0})
    result["resources"] = sampler.report()
    result["total_seconds"] = round(time.time() - t0, 1)
    text = json.dumps(result, indent=2)
    if a.out:
        Path(a.out).write_text(text)
    print(text)


if __name__ == "__main__":
    main()
