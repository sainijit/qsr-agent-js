"""End-to-end checks against the running compose stack (docker compose up -d).

Run: ZUVO_E2E_URL=http://localhost:3000 pytest tests/e2e -q
Requires AUTH_DEV_LOGIN=true and ENABLE_SIMULATOR=true. Uses real Gemini if GEMINI_API_KEY is set.
"""
import os
import time
import uuid

import httpx
import pytest

BASE = os.environ.get("ZUVO_E2E_URL", "http://localhost:3000")
JAIPUR = "3f6c2a10-6d1e-4f0b-9a51-1c2d3e4f5a01"
DELHI = "7b9e4c20-2a3f-4d5e-8f61-2b3c4d5e6f02"
LINES = {"ramesh-counter": "1312713255253790", "suresh-field": "PNID_002", "mukesh-godown": "PNID_003",
         "deepak-accounts": "PNID_004", "amit-support": "PNID_005"}


@pytest.fixture(scope="module")
def client():
    with httpx.Client(base_url=BASE, timeout=30, trust_env=False) as c:
        try:
            c.get("/api/v1/health").raise_for_status()
        except Exception:
            pytest.skip(f"stack not reachable at {BASE}")
        yield c


def login(client, tenant_id, worker_key) -> dict:
    r = client.post("/api/v1/auth/dev-login", json={"tenant_id": tenant_id, "worker_key": worker_key})
    r.raise_for_status()
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def wait_for(fn, timeout=60, interval=1.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        result = fn()
        if result:
            return result
        time.sleep(interval)
    raise AssertionError("condition not met in time")


def simulate(client, headers, pnid, phone, text):
    r = client.post("/api/v1/messages/simulate-inbound", headers=headers,
                    json={"phone_number_id": pnid, "sender_phone": phone, "sender_name": "E2E", "message_text": text})
    assert r.status_code == 200, r.text
    return r.json()


def feed(client, headers, **params):
    r = client.get("/api/v1/dashboard/feed", headers=headers, params={"limit": 200, **params})
    assert r.status_code == 200, r.text
    return r.json()


def test_auth_required(client):
    assert client.get("/api/v1/dashboard/feed").status_code == 401


def test_g1_cross_tenant_denied(client):
    jaipur = login(client, JAIPUR, "rajesh-owner")
    assert client.get("/api/v1/dashboard/feed", headers=jaipur, params={"tenant_id": DELHI}).status_code == 403
    assert client.get("/api/v1/dashboard/customers", headers=jaipur, params={"tenant_id": DELHI}).status_code == 403
    tenants = client.get("/api/v1/dashboard/tenants", headers=jaipur).json()
    assert [t["tenant_id"] for t in tenants] == [JAIPUR]
    r = client.post("/api/v1/messages/send", headers=jaipur,
                    json={"recipient_phone": "9876500000", "message_text": "x", "sender_number_id": "PNID_D_101"})
    assert r.status_code == 403


def test_g2_g6_five_lines_attributed_and_filtered(client):
    owner = login(client, JAIPUR, "rajesh-owner")
    run = uuid.uuid4().hex[:6]
    phone = f"9{int(run, 16) % 10**9:09d}"
    for key, pnid in LINES.items():
        simulate(client, owner, pnid, phone, f"namaste {run} {key}")

    def all_landed():
        msgs = [m for m in feed(client, owner, customer_phone=phone)["messages"] if run in m["content"]]
        return msgs if len(msgs) == 5 else None

    msgs = wait_for(all_landed)
    by_worker = {m["worker_name"]: m for m in msgs}
    assert set(by_worker) == {"Ramesh Sharma", "Suresh Verma", "Mukesh Saini", "Deepak Gupta", "Amit Joshi"}
    assert {m["customer_id"] for m in msgs} == {msgs[0]["customer_id"]}  # one customer across 5 lines

    suresh_id = by_worker["Suresh Verma"]["worker_id"]
    only_suresh = feed(client, owner, worker_id=suresh_id, customer_phone=phone)["messages"]
    assert only_suresh and all(m["worker_id"] == suresh_id for m in only_suresh)

    # a SALES_WORKER sees only their own line, and cannot ask for another's
    suresh = login(client, JAIPUR, "suresh-field")
    own = feed(client, suresh, customer_phone=phone)["messages"]
    assert own and all(m["worker_id"] == suresh_id for m in own)
    other = by_worker["Mukesh Saini"]["worker_id"]
    assert client.get("/api/v1/dashboard/feed", headers=suresh, params={"worker_id": other}).status_code == 403

    # Delhi owner sees none of it (RLS)
    delhi = login(client, DELHI, "vikas-owner")
    assert not [m for m in feed(client, delhi)["messages"] if run in m["content"]]


def test_tier0_keyword_and_gemini_extraction(client):
    owner = login(client, JAIPUR, "rajesh-owner")
    phone = f"98{int(time.time()) % 10**8:08d}"
    simulate(client, owner, "PNID_002", phone, "Sharma Traders ko 50 box pipe kal bhej do")

    def extracted():
        msgs = feed(client, owner, customer_phone=phone)["messages"]
        return msgs[0] if msgs and msgs[0]["ai_status"] in ("EXTRACTED", "NEEDS_REVIEW") else None

    m = wait_for(extracted, timeout=90)
    if m["ai_status"] == "NEEDS_REVIEW" and (m.get("extraction") or {}).get("reason") == "ai_unavailable":
        pytest.skip("Gemini unavailable (no key or no egress)")
    assert m["parsed_intent"] == "CREATE_ORDER"
    assert m["confidence_score"] is not None


def test_outbound_dry_run_is_persisted(client):
    owner = login(client, JAIPUR, "rajesh-owner")
    phone = f"97{int(time.time()) % 10**8:08d}"
    simulate(client, owner, "PNID_003", phone, "hello")  # opens the 24h window
    wait_for(lambda: feed(client, owner, customer_phone=phone)["messages"])
    r = client.post("/api/v1/messages/send", headers=owner,
                    json={"recipient_phone": phone, "message_text": "Order confirm, dispatch kal.", "sender_number_id": "PNID_003"})
    assert r.status_code == 202 and r.json()["status"] == "queued"
    out = wait_for(lambda: [m for m in feed(client, owner, customer_phone=phone)["messages"] if m["direction"] == "OUTBOUND"])
    assert out[0]["worker_name"] == "Mukesh Saini"


def test_duplicate_webhook_ignored(client):
    """Same wamid twice through the real webhook path -> one message (requires META_APP_SECRET in env)."""
    secret = os.environ.get("META_APP_SECRET")
    if not secret:
        pytest.skip("META_APP_SECRET not exported to the test environment")
    import hashlib, hmac, json
    phone = f"96{int(time.time()) % 10**8:08d}"
    wamid = f"wamid.e2e.{uuid.uuid4().hex}"
    body = json.dumps({"object": "whatsapp_business_account", "entry": [{"id": "x", "changes": [{"field": "messages", "value": {
        "metadata": {"display_phone_number": "x", "phone_number_id": "PNID_004"},
        "messages": [{"from": phone, "id": wamid, "timestamp": "1700000000", "type": "text", "text": {"body": "ok"}}]}}]}]}).encode()
    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    for _ in range(2):
        r = client.post("/api/v1/whatsapp/webhook", content=body, headers={"X-Hub-Signature-256": sig, "Content-Type": "application/json"})
        assert r.status_code == 200
    owner = login(client, JAIPUR, "rajesh-owner")
    msgs = wait_for(lambda: feed(client, owner, customer_phone=phone)["messages"])
    time.sleep(3)
    assert len(feed(client, owner, customer_phone=phone)["messages"]) == 1
