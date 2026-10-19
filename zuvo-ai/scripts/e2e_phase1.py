#!/usr/bin/env python3
"""Phase 1 end-to-end check against the local stack (simulator line, no real WhatsApp traffic).

  ad lead (CTWA referral) -> lead NEW + campaign -> price question -> agent draft -> approval (edit) -> outbound
  -> learned example + autonomy stat -> visit "Saturday" -> visit plan + follow-ups -> opt-out -> follow-ups cancelled

Usage:  set -a; . ./.env; set +a; python scripts/e2e_phase1.py [--tenant-slug jaipur-furniture --line PNID_JF_01]
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import random
import sys
import time
import urllib.error
import urllib.request
from datetime import date, timedelta

G, R, Y, D, X = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"
failures = 0


def ok(m: str) -> None:
    print(f"  {G}✔{X} {m}")


def bad(m: str) -> None:
    global failures
    failures += 1
    print(f"  {R}✘ {m}{X}")


def warn(m: str) -> None:
    print(f"  {Y}!{X} {m}")


def http(method: str, url: str, body=None, headers=None, token=None):
    h = {"Content-Type": "application/json", **(headers or {})}
    if token:
        h["Authorization"] = f"Bearer {token}"
    data = body if isinstance(body, (bytes, type(None))) else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
            return r.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, raw.decode()


def wait_for(fn, timeout=60.0, interval=1.0):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(interval)
    return None


def next_weekday(today: date, wd: int) -> date:
    d = (wd - today.weekday()) % 7
    return today + timedelta(days=d or 7)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gateway", default="http://localhost:8000")
    ap.add_argument("--ui", default="http://localhost:3000")
    ap.add_argument("--tenant-slug", default="jaipur-furniture")
    ap.add_argument("--line", default="PNID_JF_01")
    ap.add_argument("--password", default=os.environ.get("ZUVO_ONBOARD_DEFAULT_PASSWORD", "Zuvo@2026"))
    a = ap.parse_args()
    secret = os.environ.get("META_APP_SECRET", "")
    phone = f"9198{random.randint(10_000_000, 99_999_999)}"
    print(f"{D}customer {phone} on line {a.line}{X}")

    def inbound(text: str, referral: dict | None = None) -> None:
        msg = {"from": phone, "id": f"wamid.E2E1.{random.getrandbits(64):x}", "timestamp": str(int(time.time())), "type": "text",
               "text": {"body": text}}
        if referral:
            msg["referral"] = referral
        body = json.dumps({"object": "whatsapp_business_account", "entry": [{"id": "WABA_E2E", "changes": [{"field": "messages", "value": {
            "messaging_product": "whatsapp", "metadata": {"display_phone_number": "e2e", "phone_number_id": a.line},
            "contacts": [{"profile": {"name": "E2E Lead"}, "wa_id": phone}], "messages": [msg]}}]}]}).encode()
        sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest() if secret else ""
        s, r = http("POST", f"{a.gateway}/api/v1/whatsapp/webhook", body, {"X-Hub-Signature-256": sig})
        ok(f"inbound accepted: {text!r}") if s == 200 else bad(f"webhook {s}: {r}")

    print("1. password login")
    s, login = http("POST", f"{a.ui}/api/v1/auth/login", {"email": f"owner@{a.tenant_slug}.demo", "password": a.password})
    if s != 200:
        bad(f"login failed {s}: {login}")
        return 1
    tok = login["access_token"]
    ok(f"logged in as {login['email']} ({login['role']}), tenants={len(login['tenants'])}")
    s, bad_login = http("POST", f"{a.ui}/api/v1/auth/login", {"email": f"owner@{a.tenant_slug}.demo", "password": "wrong"})
    ok("wrong password rejected") if s == 401 else bad(f"wrong password returned {s}")

    def lead():
        s, r = http("GET", f"{a.ui}/api/v1/leads?q={phone}", token=tok)
        return r["items"][0] if s == 200 and r["items"] else None

    print("2. ad lead (click-to-WhatsApp referral) asking for price")
    inbound("Hi, sofa set ka price kya hai?", referral={"source_type": "ad", "source_id": "AD_E2E_1", "headline": "Diwali Sofa Sale",
                                                        "source_url": "https://fb.me/e2e", "ctwa_clid": "clid-e2e"})
    ld = wait_for(lead, 30)
    if not ld:
        bad("lead not created")
        return 1
    ok(f"lead {ld['id'][:8]} stage={ld['stage']} source={ld['source']}")
    ok("source is ctwa_ad") if ld["source"] == "ctwa_ad" else bad(f"source={ld['source']}")

    def pending_approval():
        s, r = http("GET", f"{a.ui}/api/v1/approvals?status=PENDING", token=tok)
        return next((x for x in r["items"] if x["lead_id"] == ld["id"]), None) if s == 200 else None

    appr = wait_for(pending_approval, 60)
    if not appr:
        bad("no pending approval for the price question (check agent-worker logs)")
    else:
        ok(f"approval {appr['id'][:8]} kind={appr['kind']} conf={appr['confidence']} reasons={appr['reasons']}")
        print(f"     {D}{appr['proposed_text'][:200]!r}{X}")
        print("3. owner edits and approves")
        edited = appr["proposed_text"].rstrip() + "\nShowroom visit par extra 5% off milega."
        s, r = http("POST", f"{a.ui}/api/v1/approvals/{appr['id']}/decide", {"decision": "edit", "text": edited}, token=tok)
        ok(f"decided {r['status']} edit_distance={r['edit_distance']}") if s == 200 and r["status"] == "EDITED" else bad(f"decide {s}: {r}")
        s, r = http("POST", f"{a.ui}/api/v1/approvals/{appr['id']}/decide", {"decision": "approve"}, token=tok)
        ok("double decision rejected (409)") if s == 409 else bad(f"double decide returned {s}")

        def learned():
            s, r = http("GET", f"{a.ui}/api/v1/learning/examples", token=tok)
            return next((e for e in r if e["answer"] == edited), None) if s == 200 else None

        ex = wait_for(learned, 30)
        ok(f"learned example stored (outcome={ex['outcome']}, weight={ex['weight']})") if ex else bad("no learned example")

        def outbound_sent():
            s, r = http("GET", f"{a.ui}/api/v1/leads/{ld['id']}", token=tok)
            return next((m for m in r["messages"] if m["direction"] == "OUTBOUND" and m["sent_by"] == "agent"), None) if s == 200 else None

        m = wait_for(outbound_sent, 30)
        ok("approved reply sent (simulated line) and persisted with sent_by=agent") if m else bad("approved reply not persisted")

    print("4. visit intent")
    inbound("Theek hai, main Saturday ko showroom aaunga shaam ko")

    def visit():
        s, r = http("GET", f"{a.ui}/api/v1/leads/{ld['id']}", token=tok)
        return r if s == 200 and r["visits"] else None

    d = wait_for(visit, 60)
    if not d:
        bad("no visit plan extracted")
    else:
        v = d["visits"][0]
        sat = next_weekday(date.fromisoformat(http("GET", f"{a.ui}/api/v1/visits", token=tok)[1]["today"]), 5)
        ok(f"visit {v['date']} ({v['precision']}, {v['time_window']}) status={v['status']} lead stage={d['lead']['stage']}")
        ok("visit date resolved to next Saturday") if v["date"] == sat.isoformat() else warn(f"expected {sat}, got {v['date']}")
        sched = [t for t in d["followups"] if t["status"] == "SCHEDULED"]
        ok(f"{len(sched)} follow-ups scheduled: {[t['rule_id'] for t in sched]}") if sched else bad("no follow-ups scheduled")

    print("5. dashboard")
    s, t = http("GET", f"{a.ui}/api/v1/dashboard/today", token=tok)
    if s == 200:
        m = t["metrics"]
        ok(f"today: leads_new={m['leads_new']} visits_next_7d={m['visits_next_7d']} approvals_pending={m['approvals_pending']} "
           f"agent_drafted={m['agent_drafted']}")
    else:
        bad(f"/dashboard/today {s}: {t}")
    s, ins = http("GET", f"{a.ui}/api/v1/insights?days=7", token=tok)
    ok(f"insights: campaigns={[(c['name'], c['leads']) for c in ins['campaigns']][:3]}") if s == 200 else bad(f"/insights {s}")

    print("6. opt-out")
    inbound("STOP")

    def opted():
        s, r = http("GET", f"{a.ui}/api/v1/leads/{ld['id']}", token=tok)
        if s == 200 and r["lead"]["opted_out"]:
            return r
        return None

    d = wait_for(opted, 30)
    if d:
        live = [t for t in d["followups"] if t["status"] in ("SCHEDULED", "QUEUED")]
        ok("customer opted out") if True else None
        ok("all pending follow-ups cancelled") if not live else bad(f"{len(live)} follow-ups still live after opt-out")
    else:
        bad("opt-out not recorded")

    print("7. tenant isolation")
    s, other = http("POST", f"{a.ui}/api/v1/auth/login", {"email": "owner@alpha-automobile.demo", "password": a.password})
    if s == 200:
        s, r = http("GET", f"{a.ui}/api/v1/leads/{ld['id']}", token=other["access_token"])
        ok("other tenant cannot read the lead (404)") if s == 404 else bad(f"cross-tenant read returned {s}")
    print(f"\n{'PASS' if not failures else f'{failures} FAILURE(S)'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
