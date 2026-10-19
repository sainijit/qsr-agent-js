import json
import os

import pytest
from fastapi.testclient import TestClient

from conftest import DELHI, JAIPUR
from zuvo_common.bus import InMemoryEventBus
from zuvo_common.events import Envelope, Extraction, Intent, Topics
from zuvo_common.idempotency import InMemoryIdempotencyStore
from zuvo_common.security import Principal, issue_token, sign_meta_payload
from zuvo_common.settings import Settings
from zuvo_common.tenancy import Role, TenantPolicyLoader, TenantRouting, worker_uuid


def meta_payload(pnid: str, wamid: str, text: str, sender="919876543210") -> dict:
    return {
        "object": "whatsapp_business_account",
        "entry": [{"id": "x", "changes": [{"field": "messages", "value": {
            "messaging_product": "whatsapp",
            "metadata": {"display_phone_number": "x", "phone_number_id": pnid},
            "contacts": [{"wa_id": sender, "profile": {"name": "Sharma Ji"}}],
            "messages": [{"from": sender, "id": wamid, "timestamp": "1700000000", "type": "text", "text": {"body": text}}],
        }}]}],
    }


@pytest.fixture
def gateway():
    from gateway.main import create_app
    bus, idem = InMemoryEventBus(), InMemoryIdempotencyStore()
    settings = Settings(_env_file=None)
    with TestClient(create_app(settings, bus, idem)) as client:
        yield client, bus, settings


def _post(client, settings, payload, sign=True):
    body = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    if sign:
        headers["X-Hub-Signature-256"] = sign_meta_payload(settings.meta_app_secret, body)
    return client.post("/api/v1/whatsapp/webhook", content=body, headers=headers)


def test_gateway_rejects_unsigned(gateway):
    client, bus, settings = gateway
    assert _post(client, settings, meta_payload("PNID_002", "w1", "hi"), sign=False).status_code == 401
    assert bus.published == []


def test_gateway_g2_attribution_and_dedupe(gateway):
    client, bus, settings = gateway
    for i, pnid in enumerate(["1312713255253790", "PNID_002", "PNID_003", "PNID_004", "PNID_005"]):
        assert _post(client, settings, meta_payload(pnid, f"wamid.{i}", "50 box pipe")).status_code == 200
    _post(client, settings, meta_payload("PNID_002", "wamid.1", "50 box pipe"))  # Meta retry
    events = bus.on(Topics.INBOUND_RECEIVED)
    assert len(events) == 5
    assert {e.payload["worker_key"] for e in events} == {"ramesh-counter", "suresh-field", "mukesh-godown", "deepak-accounts", "amit-support"}
    assert all(e.tenant_id == JAIPUR for e in events)
    rec = bus.published[0]
    assert rec.headers["tenant_id"] == JAIPUR and str(rec.key).startswith(JAIPUR)


def test_gateway_unrouted_number_is_not_published(gateway):
    client, bus, settings = gateway
    r = _post(client, settings, meta_payload("NOT_A_LINE", "w9", "hello"))
    assert r.status_code == 200 and bus.published == []


def test_gateway_verify_challenge(gateway):
    client, _, settings = gateway
    ok = client.get("/api/v1/whatsapp/webhook", params={"hub.mode": "subscribe", "hub.verify_token": settings.meta_verify_token, "hub.challenge": "42"})
    assert ok.status_code == 200 and ok.text == "42"
    bad = client.get("/api/v1/whatsapp/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "nope", "hub.challenge": "42"})
    assert bad.status_code == 403


def test_simulator_requires_auth_and_same_tenant(gateway):
    client, bus, settings = gateway
    body = {"phone_number_id": "PNID_002", "sender_phone": "9876543210", "message_text": "hi"}
    assert client.post("/api/v1/messages/simulate-inbound", json=body).status_code == 401
    delhi = issue_token(settings.jwt_secret, Principal("d", DELHI, Role.OWNER, None), 60)
    assert client.post("/api/v1/messages/simulate-inbound", json=body, headers={"Authorization": f"Bearer {delhi}"}).status_code == 403
    jaipur = issue_token(settings.jwt_secret, Principal("j", JAIPUR, Role.OWNER, None), 60)
    assert client.post("/api/v1/messages/simulate-inbound", json=body, headers={"Authorization": f"Bearer {jaipur}"}).status_code == 200
    assert len(bus.on(Topics.INBOUND_RECEIVED)) == 1


class FakeExtractor:
    def __init__(self, extraction=None, fail=False, error: Exception | None = None):
        self.extraction, self.fail, self.calls, self.error = extraction, fail, 0, error

    async def extract(self, text, policy):
        from ai_router.extractor import ExtractionResult
        self.calls += 1
        fail = self.fail if isinstance(self.fail, bool) else self.calls <= self.fail
        if fail:
            raise self.error or TimeoutError("gemini down")
        return ExtractionResult(extraction=self.extraction, model="fake", latency_ms=5, input_tokens=1, output_tokens=1)


def _persisted(text: str, message_type="text", change="created") -> Envelope:
    return Envelope(event_type=Topics.MESSAGE_PERSISTED, tenant_id=JAIPUR, worker_id=worker_uuid(JAIPUR, "suresh-field"),
                    customer_id="c1", wamid="w1",
                    payload={"change": change, "direction": "INBOUND", "message_id": "m1", "message_type": message_type, "text": text})


@pytest.mark.asyncio
@pytest.mark.parametrize("text,extraction,fail,mtype,topic,reason,calls", [
    ("namaste", None, False, "text", Topics.INTENT_EXTRACTED, None, 0),
    ("50 box pipe kal", Extraction(intent=Intent.CREATE_ORDER, confidence=0.95), False, "text", Topics.INTENT_EXTRACTED, None, 1),
    ("kuch bhi", Extraction(intent=Intent.CREATE_ORDER, confidence=0.4), False, "text", Topics.REVIEW_FLAGGED, "low_confidence", 1),
    ("50 box", None, True, "text", Topics.REVIEW_FLAGGED, "ai_unavailable", 3),
    ("50 box pipe", Extraction(intent=Intent.CREATE_ORDER, confidence=0.9), 2, "text", Topics.INTENT_EXTRACTED, None, 3),
    ("", None, False, "audio", Topics.REVIEW_FLAGGED, "unsupported_media", 0),
])
async def test_ai_router_tiers(text, extraction, fail, mtype, topic, reason, calls):
    from ai_router.main import AiRouter
    bus = InMemoryEventBus()
    fake = FakeExtractor(extraction, fail)
    router = AiRouter(bus, TenantRouting(os.environ["TENANT_ROUTING_PATH"]), TenantPolicyLoader(os.environ["TENANT_POLICY_ROOT"]), fake,
                      backoff_seconds=0)
    await router.handle(_persisted(text, mtype), None)
    await router.handle(_persisted(text, mtype, change="ai:x"), None)  # updates are ignored
    assert len(bus.published) == 1 and bus.published[0].topic == topic
    out = bus.on(topic)[0]
    assert out.payload["reason"] == reason and fake.calls == calls
    assert out.event_id == (await _again(router, text, mtype)).event_id  # deterministic


@pytest.mark.asyncio
async def test_ai_router_does_not_retry_permanent_errors():
    from ai_router.main import AiRouter

    class BadKey(Exception):
        code = 400

    bus = InMemoryEventBus()
    fake = FakeExtractor(fail=True, error=BadKey("API key not valid"))
    router = AiRouter(bus, TenantRouting(os.environ["TENANT_ROUTING_PATH"]), TenantPolicyLoader(os.environ["TENANT_POLICY_ROOT"]), fake,
                      backoff_seconds=0)
    await router.handle(_persisted("50 box pipe"), None)
    assert fake.calls == 1 and bus.on(Topics.REVIEW_FLAGGED)[0].payload["reason"] == "ai_unavailable"


async def _again(router, text, mtype):
    bus = InMemoryEventBus()
    router.bus = bus
    await router.handle(_persisted(text, mtype), None)
    return bus.published and Envelope.model_validate_json(bus.published[0].value)


def test_feed_broker_filters_tenant_and_worker():
    import asyncio
    from dashboard_api.broker import FeedBroker
    from zuvo_common.bus import Record

    async def run():
        broker = FeedBroker()
        owner = broker.subscribe(Principal("o", JAIPUR, Role.OWNER, None))
        suresh = broker.subscribe(Principal("s", JAIPUR, Role.SALES_WORKER, worker_uuid(JAIPUR, "suresh-field")))
        other = broker.subscribe(Principal("d", DELHI, Role.OWNER, None))
        env = Envelope(event_type=Topics.MESSAGE_PERSISTED, tenant_id=JAIPUR, worker_id=worker_uuid(JAIPUR, "mukesh-godown"), payload={"change": "created"})
        await broker.on_record(Record(Topics.MESSAGE_PERSISTED, b"", env.model_dump_json().encode(), {"tenant_id": JAIPUR}))
        return owner.queue.qsize(), suresh.queue.qsize(), other.queue.qsize()

    assert asyncio.run(run()) == (1, 0, 0)


def _status_payload(pnid: str, wamid: str, st: str) -> dict:
    return {"object": "whatsapp_business_account", "entry": [{"id": "x", "changes": [{"field": "messages", "value": {
        "messaging_product": "whatsapp", "metadata": {"display_phone_number": "x", "phone_number_id": pnid},
        "statuses": [{"id": wamid, "status": st, "timestamp": "1700000000", "recipient_id": "919876543210"}],
    }}]}]}


@pytest.mark.parametrize("kind", ["message", "status"])
def test_gateway_publish_failure_asks_meta_to_retry_and_retry_succeeds(gateway, kind):
    client, bus, settings = gateway
    payload = meta_payload("PNID_002", "wamid.r1", "hi") if kind == "message" else _status_payload("PNID_002", "wamid.r1", "delivered")
    topic = Topics.INBOUND_RECEIVED if kind == "message" else Topics.MESSAGE_STATUS
    real = bus.publish_raw

    async def down(*a, **k):
        raise ConnectionError("kafka down")

    bus.publish_raw = down
    assert _post(client, settings, payload).status_code == 503  # claim released, Meta will retry
    bus.publish_raw = real
    assert _post(client, settings, payload).status_code == 200
    assert _post(client, settings, payload).status_code == 200  # later duplicate retry is deduped
    events = bus.on(topic)
    assert len(events) == 1


def test_gateway_event_ids_are_stable_across_redis_loss():
    import asyncio

    from gateway.ingest import Ingestor
    settings = Settings(_env_file=None)
    routing = TenantRouting(settings.tenant_routing_path)
    ids = []
    for _ in range(2):  # fresh idempotency store each time == Redis key lost
        bus = InMemoryEventBus()
        ing = Ingestor(routing, TenantPolicyLoader(settings.tenant_policy_root), bus, InMemoryIdempotencyStore())
        asyncio.run(ing.ingest(meta_payload("PNID_002", "wamid.s1", "hi")))
        asyncio.run(ing.ingest(_status_payload("PNID_002", "wamid.s1", "read")))
        ids.append([Envelope.model_validate_json(r.value).event_id for r in bus.published])
    assert ids[0] == ids[1] and len(set(ids[0])) == 2
