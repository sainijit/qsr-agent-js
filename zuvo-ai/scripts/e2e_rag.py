#!/usr/bin/env python3
"""End-to-end check of zuvo-ai-rag in the reply path (tenant must have knowledge.retrieval: rag).

Sends signed inbound webhooks, then reads the agent_runs row the agent-worker wrote for each
question (via psql in the postgres container) and checks strategy / topic / decision.

Usage:  set -a; . ./.env; set +a; python scripts/e2e_rag.py [--line PNID_ALPHA_01] [--rag-down]
  --rag-down  stops zuvo-ai-rag first and expects strategy=lexical_fallback (restarts it afterwards)
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import random
import subprocess
import sys
import time
import urllib.request

G, R, D, X = "\033[32m", "\033[31m", "\033[2m", "\033[0m"
FAILS: list[str] = []


def ok(m): print(f"  {G}✔{X} {m}")


def bad(m):
    FAILS.append(m)
    print(f"  {R}✘ {m}{X}")


def sql(q: str) -> str:
    return subprocess.run(["docker", "compose", "exec", "-T", "postgres", "psql", "-U", "zuvo_owner", "-d", "zuvo_db", "-At", "-c", q],
                          capture_output=True, text=True, check=True).stdout.strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gateway", default="http://localhost:8000")
    ap.add_argument("--line", default="PNID_ALPHA_01")
    ap.add_argument("--rag-down", action="store_true")
    a = ap.parse_args()
    secret = os.environ.get("META_APP_SECRET", "")

    cases = [  # (question, expect_knowledge)
        ("What are your workshop timings?", True),
        ("pickup and drop service milta hai kya?", True),
        ("Can you deliver pizza to my house?", False),
    ]
    if a.rag_down:
        subprocess.run(["docker", "compose", "stop", "zuvo-ai-rag"], capture_output=True, check=True)
        print(f"{D}zuvo-ai-rag stopped{X}")
    try:
        for q, expect in cases:
            phone = f"9197{random.randint(10_000_000, 99_999_999)}"
            msg = {"from": phone, "id": f"wamid.E2ERAG.{random.getrandbits(64):x}", "timestamp": str(int(time.time())),
                   "type": "text", "text": {"body": q}}
            body = json.dumps({"object": "whatsapp_business_account", "entry": [{"id": "WABA_E2E", "changes": [{"field": "messages", "value": {
                "messaging_product": "whatsapp", "metadata": {"display_phone_number": "e2e", "phone_number_id": a.line},
                "contacts": [{"profile": {"name": "E2E RAG"}, "wa_id": phone}], "messages": [msg]}}]}]}).encode()
            sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest() if secret else ""
            req = urllib.request.Request(f"{a.gateway}/api/v1/whatsapp/webhook", data=body, method="POST",
                                         headers={"Content-Type": "application/json", "X-Hub-Signature-256": sig})
            urllib.request.urlopen(req, timeout=10).read()
            print(f"{q!r}")
            row = ""
            for _ in range(60):
                row = sql("SELECT r.task, r.topic, r.decision, r.confidence, r.retrieved::text, coalesce(r.proposed_text,'') "
                          "FROM agent_runs r JOIN leads l ON l.id = r.lead_id JOIN customers c ON c.id = l.customer_id "
                          f"WHERE c.phone = '{phone}' ORDER BY r.created_at DESC LIMIT 1")
                if row:
                    break
                time.sleep(1)
            if not row:
                intent = sql(f"SELECT coalesce(parsed_intent,'') || '|' || ai_status FROM messages WHERE wamid = '{msg['id']}'")
                if not expect and intent and not intent.startswith("FAQ|") and intent.endswith("|EXTRACTED"):
                    # the extractor may classify off-topic text as non-FAQ: then nothing is answered, which is the safe outcome
                    ok(f"off-topic classified as {intent.split('|')[0] or 'unknown'}: no reply drafted")
                else:
                    bad(f"no agent_run (message intent/status={intent or 'missing'})")
                continue
            task, topic, decision, conf, retrieved, text = row.split("|", 5)
            meta = json.loads(retrieved)
            print(f"  {D}task={task} topic={topic} decision={decision} conf={conf} strategy={meta.get('strategy')} "
                  f"top_sim={meta.get('top_similarity')}{X}\n  {D}reply: {text[:140]!r}{X}")
            if task != "faq":
                ok(f"routed to task={task} (not faq): no retrieval expected") if not expect else bad(f"task={task}, expected faq")
                continue
            if meta.get("example_id"):
                ok("reused an owner-approved answer (learning takes priority over retrieval)")
                continue
            want = "lexical_fallback" if a.rag_down else "hybrid_rrf"
            ok(f"strategy={want}") if meta.get("strategy") == want else bad(f"strategy={meta.get('strategy')}, expected {want}")
            if a.rag_down:
                continue
            if expect:
                ok("answered from knowledge") if topic != "faq:unknown" else bad("no knowledge found for an in-domain question")
            else:
                ok("off-topic -> holding reply") if topic == "faq:unknown" else bad(f"off-topic matched {topic}")
    finally:
        if a.rag_down:
            subprocess.run(["docker", "compose", "start", "zuvo-ai-rag"], capture_output=True)
            print(f"{D}zuvo-ai-rag restarted{X}")
    print(f"\n{R}FAIL ({len(FAILS)}){X}" if FAILS else f"\n{G}PASS{X}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
