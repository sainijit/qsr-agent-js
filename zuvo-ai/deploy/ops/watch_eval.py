#!/usr/bin/env python3
"""Evaluate a ZuvoCloud host snapshot against alert rules and send notifications.

Stdlib only (runs on the VM's system python3). Called by deploy/ops/zuvo-watch.sh, which collects the snapshot:

    {"collected_at": epoch, "deploy_in_progress": bool,
     "host": {"disks": [{"path": str, "used_pct": float}], "mem_available_pct": float, "swap_used_pct": float|null},
     "containers": [{"service", "state", "health", "oom": bool, "restarts": int}] | null,
     "expected_services": [str],
     "backup": {"dir": str, "newest_age_hours": float|null, "unverified": int} | null,
     "edge": {"url": str, "code": int} | null,
     "metrics": [{"name", "labels", "value"}] | null, "metrics_error": str|null,
     "thresholds": {"ai_daily_budget_inr": float|null, "backup_max_age_hours": float}}

Messages contain only metric names, service names and numbers - never payloads, phone numbers or secrets.
State (consecutive counts, last alert times, previous DLQ/restart values) lives in a small JSON file.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

WARNING, CRITICAL = "warning", "critical"


@dataclass
class Finding:
    key: str           # stable identity, e.g. "container:gateway" or "metric:zuvo_outbound_unknown"
    severity: str
    message: str
    for_checks: int = 1  # condition must hold for N consecutive runs before it alerts
    runbook: str = ""


def _m(metrics, name, **labels):
    return [m for m in metrics if m["name"] == name and all(m["labels"].get(k) == v for k, v in labels.items())]


def _v(metrics, name, default=0.0, **labels):
    rows = _m(metrics, name, **labels)
    return sum(float(r["value"]) for r in rows) if rows else default


def evaluate(snap: dict, state: dict) -> list[Finding]:
    f: list[Finding] = []
    th = snap.get("thresholds") or {}
    host = snap.get("host") or {}

    # ---- host
    for d in host.get("disks", []):
        u = float(d["used_pct"])
        if u >= 80:
            f.append(Finding(f"disk:{d['path']}", CRITICAL if u >= 90 else WARNING, f"disk {d['path']} {u:.0f}% used",
                             runbook="postgres-down-or-disk-full"))
    mem = host.get("mem_available_pct")
    if mem is not None and mem < 10:
        f.append(Finding("host:memory", CRITICAL if mem < 5 else WARNING, f"memory available {mem:.0f}%", 2, "container-crash-oom"))
    swap = host.get("swap_used_pct")
    if swap is not None and swap > 50:
        f.append(Finding("host:swap", WARNING, f"swap used {swap:.0f}%", 2, "container-crash-oom"))

    # ---- backups
    b = snap.get("backup")
    if b is not None:
        age, max_age = b.get("newest_age_hours"), float(th.get("backup_max_age_hours", 8))
        if age is None:
            f.append(Finding("backup:age", CRITICAL, f"no verified backup in {b['dir']}", runbook="backup-failure"))
        elif age > max_age:
            f.append(Finding("backup:age", CRITICAL, f"newest backup is {age:.1f} h old (limit {max_age:g} h)", runbook="backup-failure"))
        if b.get("unverified"):
            f.append(Finding("backup:unverified", WARNING, f"{b['unverified']} backup(s) failed verification (*.unverified)",
                             runbook="backup-failure"))

    # ---- containers (suppressed while a deploy/rollback holds the lock: recreations are expected)
    deploying = bool(snap.get("deploy_in_progress"))
    containers = snap.get("containers")
    if containers is not None and not deploying:
        seen = {c["service"]: c for c in containers}
        prev_restarts = state.get("restarts", {})
        for svc in snap.get("expected_services", []):
            c = seen.get(svc)
            if c is None:
                f.append(Finding(f"container:{svc}", CRITICAL, f"{svc}: no container", 2, "application-unavailable"))
                continue
            if c["oom"]:
                f.append(Finding(f"oom:{svc}", CRITICAL, f"{svc}: OOM-killed", runbook="container-crash-oom"))
            if c["state"] != "running" or c["health"] not in ("healthy", "none"):
                f.append(Finding(f"container:{svc}", CRITICAL, f"{svc}: state={c['state']} health={c['health']}", 2,
                                 "application-unavailable"))
            last = prev_restarts.get(svc)
            if last is not None and c["restarts"] > last:
                f.append(Finding(f"restart:{svc}", WARNING, f"{svc}: restarted {c['restarts'] - last}x since last check",
                                 runbook="container-crash-oom"))
        state["restarts"] = {s: c["restarts"] for s, c in seen.items()}

    edge = snap.get("edge")
    if edge is not None and not deploying and edge.get("code") != 200:
        f.append(Finding("edge:health", CRITICAL, f"{edge['url']} returned {edge.get('code')}", 2, "application-unavailable"))

    # ---- application metrics (dashboard-api opsmetrics)
    metrics = snap.get("metrics")
    if metrics is None:
        if not deploying:
            f.append(Finding("metrics:collect", CRITICAL, f"ops metrics unavailable: {snap.get('metrics_error') or 'unknown'}", 2,
                             "application-unavailable"))
        return f
    for r in _m(metrics, "zuvo_collect_ok"):
        if float(r["value"]) == 0:
            sec = r["labels"].get("section", "?")
            f.append(Finding(f"collect:{sec}", WARNING, f"metrics section '{sec}' failed to collect", 2,
                             "postgres-down-or-disk-full" if sec == "postgres" else "kafka-consumer-lag"))

    for r in _m(metrics, "zuvo_kafka_consumer_lag"):
        if float(r["value"]) > 100:
            g, t = r["labels"].get("group"), r["labels"].get("topic")
            f.append(Finding(f"lag:{g}:{t}", WARNING, f"consumer lag {float(r['value']):.0f} on {g}/{t}", 2, "kafka-consumer-lag"))
    for r in _m(metrics, "zuvo_kafka_oldest_unprocessed_seconds"):
        if float(r["value"]) > 300:
            g = r["labels"].get("group")
            f.append(Finding(f"lagage:{g}", WARNING, f"{g}: oldest unprocessed event {float(r['value']):.0f} s", 1,
                             "kafka-consumer-lag"))

    # DLQ: alert on growth (records stay for the 7-day retention, so "> 0" would alert all week)
    prev_dlq = state.get("dlq", {})
    cur_dlq = {}
    for r in _m(metrics, "zuvo_kafka_dlq_messages"):
        t, v = r["labels"].get("topic"), float(r["value"])
        cur_dlq[t] = v
        if v > prev_dlq.get(t, 0):
            f.append(Finding(f"dlq:{t}", WARNING, f"{t}: {v - prev_dlq.get(t, 0):.0f} new DLQ record(s) (total {v:.0f})",
                             runbook="dead-letter-growth"))
    state["dlq"] = cur_dlq

    def gt(name, limit, sev, msg, runbook, for_checks=1, **labels):
        v = _v(metrics, name, **labels)
        if v > limit:
            f.append(Finding(f"metric:{name}", sev, msg.format(v=v), for_checks, runbook))

    gt("zuvo_outbox_oldest_pending_seconds", 120, WARNING, "outbox oldest pending {v:.0f} s", "kafka-consumer-lag")
    gt("zuvo_outbox_failing", 0, WARNING, "{v:.0f} outbox event(s) failing to publish", "kafka-consumer-lag", 2)
    gt("zuvo_outbox_max_attempts", 10, CRITICAL, "outbox event at {v:.0f} publish attempts", "kafka-consumer-lag")
    gt("zuvo_outbound_unknown", 0, WARNING, "{v:.0f} outbound message(s) in UNKNOWN", "outbound-unknown", 3)
    gt("zuvo_outbound_unknown_oldest_seconds", 86400, CRITICAL, "outbound UNKNOWN for {v:.0f} s", "outbound-unknown")
    gt("zuvo_outbound_sending_stuck", 0, CRITICAL, "{v:.0f} outbound send(s) stuck in SENDING (sweeper not running?)",
       "outbound-unknown")
    pr = _v(metrics, "zuvo_outbound_pending_retry")
    if pr > 20:
        f.append(Finding("metric:zuvo_outbound_pending_retry", CRITICAL if pr > 100 else WARNING,
                         f"{pr:.0f} outbound message(s) waiting to retry (Meta throttling/outage?)", 1, "meta-webhook-failures"))
    gt("zuvo_agent_latency_p95_ms", 15000, WARNING, "agent p95 latency {v:.0f} ms", "gemini-errors", 2)
    gt("zuvo_rag_processing_stuck", 0, WARNING, "{v:.0f} RAG document(s) stuck in processing", "rag-indexing")
    gt("zuvo_rag_failed_max_attempts", 3, CRITICAL, "RAG document failed {v:.0f} consecutive times", "rag-indexing")
    gt("zuvo_rag_oldest_pending_seconds", 900, WARNING, "RAG document pending for {v:.0f} s", "rag-indexing")
    gt("zuvo_rag_documents", 0, WARNING, "{v:.0f} RAG document(s) in failed state", "rag-indexing", 1, state="failed")
    gt("zuvo_followups_stuck", 0, WARNING, "{v:.0f} follow-up(s) stuck", "kafka-consumer-lag", 2)

    maxc = _v(metrics, "zuvo_pg_max_connections")
    conns = _v(metrics, "zuvo_pg_connections")
    if maxc and conns / maxc > 0.8:
        f.append(Finding("metric:pg_connections", WARNING, f"postgres connections {conns:.0f}/{maxc:.0f}", 2,
                         "postgres-down-or-disk-full"))

    budget = th.get("ai_daily_budget_inr")
    if budget:
        cost = _v(metrics, "zuvo_ai_cost_inr_today")
        if cost > float(budget):
            f.append(Finding("metric:ai_cost", WARNING, f"Gemini spend today INR {cost:.2f} > budget {float(budget):.2f}", 1,
                             "gemini-errors"))

    # ratios need a minimum volume, so one failure among three messages does not page anyone
    out = _m(metrics, "zuvo_messages_24h", direction="OUTBOUND")
    out_total = sum(float(r["value"]) for r in out)
    out_failed = sum(float(r["value"]) for r in out if str(r["labels"].get("status", "")).lower() == "failed")
    if out_total >= 20 and out_failed / out_total > 0.05:
        f.append(Finding("ratio:outbound_failed", WARNING, f"outbound failed {out_failed:.0f}/{out_total:.0f} in 24 h",
                         1, "meta-webhook-failures"))
    ai = _m(metrics, "zuvo_inbound_ai_status_24h")
    ai_total = sum(float(r["value"]) for r in ai)
    ai_failed = sum(float(r["value"]) for r in ai if str(r["labels"].get("ai_status", "")).upper() == "FAILED")
    if ai_total >= 10 and ai_failed / ai_total > 0.10:
        f.append(Finding("ratio:ai_failed", WARNING, f"AI extraction failed {ai_failed:.0f}/{ai_total:.0f} in 24 h", 1,
                         "gemini-errors"))
    oc = _m(metrics, "zuvo_agent_outcomes_24h")
    oc_total = sum(float(r["value"]) for r in oc)
    oc_bad = sum(float(r["value"]) for r in oc if r["labels"].get("outcome") in ("rag_unavailable", "draft_failed"))
    if oc_total >= 10 and oc_bad / oc_total > 0.10:
        f.append(Finding("ratio:agent_outcomes", WARNING,
                         f"agent runs degraded (rag_unavailable/draft_failed) {oc_bad:.0f}/{oc_total:.0f}", 1, "gemini-errors"))
    return f


def decide(findings: list[Finding], state: dict, now: float, remind_seconds: float) -> tuple[list[Finding], list[str]]:
    """Apply consecutive-check gating and de-duplication. Returns (to_alert, resolved_keys) and updates state."""
    counts = state.get("counts", {})
    alerted = state.get("alerted", {})  # key -> {"at": epoch, "severity": str}
    active = {x.key for x in findings}
    new_counts = {k: counts.get(k, 0) + 1 for k in active}
    to_alert = []
    for x in findings:
        if new_counts[x.key] < x.for_checks:
            continue
        prev = alerted.get(x.key)
        escalated = prev is not None and prev["severity"] == WARNING and x.severity == CRITICAL
        if prev is None or escalated or now - prev["at"] >= remind_seconds:
            to_alert.append(x)
            alerted[x.key] = {"at": now, "severity": x.severity}
    resolved = [k for k in list(alerted) if k not in active]
    for k in resolved:
        del alerted[k]
    state["counts"], state["alerted"] = new_counts, alerted
    return to_alert, resolved


def render(to_alert: list[Finding], resolved: list[str], host: str) -> tuple[str, str]:
    sev = CRITICAL if any(x.severity == CRITICAL for x in to_alert) else (WARNING if to_alert else "resolved")
    subject = f"[zuvo {sev.upper()}] {host}: {len(to_alert)} alert(s)" + (f", {len(resolved)} resolved" if resolved else "")
    lines = [f"{x.severity.upper():8} {x.message}  (runbook: docs/runbooks/{x.runbook}.md)" for x in to_alert]
    lines += [f"RESOLVED {k}" for k in resolved]
    return subject[:99], "\n".join(lines)  # SNS email subjects must be < 100 chars


def destinations() -> dict:
    return {"sns": os.environ.get("ZUVO_ALERT_SNS_TOPIC_ARN", "").strip(),
            "webhook": os.environ.get("ZUVO_ALERT_WEBHOOK_URL", "").strip()}


def notify(subject: str, body: str) -> list[str]:
    """Send to every configured destination. Returns a list of errors (empty = all ok)."""
    errors = []
    d = destinations()
    if d["sns"]:
        try:
            r = subprocess.run(["aws", "sns", "publish", "--topic-arn", d["sns"], "--subject", subject, "--message", body],
                               capture_output=True, text=True, timeout=30)
            if r.returncode != 0:
                errors.append(f"sns publish failed (exit {r.returncode})")
        except (OSError, subprocess.TimeoutExpired) as e:
            errors.append(f"sns publish failed ({type(e).__name__})")
    if d["webhook"]:
        # {"text": ...} is accepted by Slack incoming webhooks and Google Chat webhooks
        req = urllib.request.Request(d["webhook"], data=json.dumps({"text": f"*{subject}*\n{body}"}).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                if resp.status >= 300:
                    errors.append(f"webhook returned {resp.status}")
        except Exception as e:  # noqa: BLE001 - report the class only; the URL itself is a secret
            errors.append(f"webhook failed ({type(e).__name__})")
    return errors


def load_state(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, ValueError):
        return {}


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".watch-state")
    with os.fdopen(fd, "w") as fh:
        json.dump(state, fh)
    os.replace(tmp, path)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True, type=Path)
    ap.add_argument("--remind-hours", type=float, default=float(os.environ.get("ZUVO_ALERT_REMIND_HOURS", 6)))
    ap.add_argument("--no-notify", action="store_true", help="evaluate and print only (operator status view)")
    ap.add_argument("--host", default=os.uname().nodename)
    a = ap.parse_args(argv)

    snap = json.load(sys.stdin)
    state = load_state(a.state)
    findings = evaluate(snap, state)
    now = float(snap.get("collected_at") or time.time())
    rc = 2 if any(x.severity == CRITICAL for x in findings) else (1 if findings else 0)

    if snap.get("deploy_in_progress"):
        print("NOTE     deploy/rollback in progress: container, edge and metrics-availability checks suppressed")
    for x in sorted(findings, key=lambda x: (x.severity != CRITICAL, x.key)):
        print(f"{x.severity.upper():8} {x.message}  [docs/runbooks/{x.runbook}.md]")
    if not findings:
        print("OK       no findings")
    if a.no_notify:
        return rc  # status view: state (DLQ baselines, restart counts) is not advanced

    to_alert, resolved = decide(findings, state, now, a.remind_hours * 3600)
    if to_alert or resolved:
        subject, body = render(to_alert, resolved, a.host)
        if not any(destinations().values()):
            print("WARNING  no alert destination configured (ZUVO_ALERT_SNS_TOPIC_ARN / ZUVO_ALERT_WEBHOOK_URL): nothing was sent",
                  file=sys.stderr)
            for x in to_alert:  # not marked as alerted: it is sent once a destination exists
                state["alerted"].pop(x.key, None)
            rc = max(rc, 3)
        else:
            errs = notify(subject, body)
            if errs:
                print("ERROR    notification failed: " + "; ".join(errs), file=sys.stderr)
                for x in to_alert:  # retried on the next run
                    state["alerted"].pop(x.key, None)
                rc = 4
            else:
                print(f"sent: {subject}")
    save_state(a.state, state)
    return rc


if __name__ == "__main__":
    sys.exit(main())
