"""Release-gate tests for the highest-risk edges not covered elsewhere: readiness under dependency failure,
webhook signature tampering, JWT forgery/expiry, and duplicate Meta status receipts at the gateway.
(RLS, outbox, ledger, dispatch state machine and RAG isolation are covered in tests/integration/.)"""
import json
import time

import jwt
import pytest
from fastapi.testclient import TestClient

from zuvo_common.bus import InMemoryEventBus
from zuvo_common.events import Topics
from zuvo_common.idempotency import InMemoryIdempotencyStore
from zuvo_common.security import AuthError, Principal, decode_token, issue_token, sign_meta_payload, verify_meta_signature
from zuvo_common.settings import Settings
from zuvo_common.tenancy import Role

from test_services import _post, meta_payload

SECRET = "x" * 40


class _Down:
    """Wraps a dependency so ping() reports it unavailable."""

    def __init__(self, inner):
        self._inner = inner

    async def ping(self, *a, **k):
        return False

    def __getattr__(self, name):
        return getattr(self._inner, name)


def _client(bus=None, idem=None):
    from gateway.main import create_app
    settings = Settings(_env_file=None)
    return TestClient(create_app(settings, bus or InMemoryEventBus(), idem or InMemoryIdempotencyStore())), settings


# ---- health / readiness -------------------------------------------------------------------------------------------

@pytest.mark.parametrize("down", ["redis", "kafka", "both"])
def test_gateway_readyz_is_503_when_a_dependency_is_down_but_livez_stays_200(down):
    bus, idem = InMemoryEventBus(), InMemoryIdempotencyStore()
    if down in ("kafka", "both"):
        bus = _Down(bus)
    if down in ("redis", "both"):
        idem = _Down(idem)
    client, _ = _client(bus, idem)
    with client:
        r = client.get("/readyz")
        assert r.status_code == 503 and r.json()["status"] == "not_ready"
        comps = r.json()["components"]
        assert comps["redis"] == ("down" if down in ("redis", "both") else "ok")
        assert comps["kafka"] == ("down" if down in ("kafka", "both") else "ok")
        assert client.get("/livez").status_code == 200  # never restarts the container because a downstream is down


def test_gateway_readyz_ok_when_dependencies_are_up():
    client, _ = _client()
    with client:
        r = client.get("/readyz")
        assert r.status_code == 200 and r.json() == {"status": "ready", "components": {"redis": "ok", "kafka": "ok"}}


def test_webhook_with_kafka_down_asks_meta_to_retry_and_releases_dedupe_claim():
    class FailingBus(InMemoryEventBus):
        fail = True

        async def publish(self, *a, **k):
            if self.fail:
                raise RuntimeError("broker unavailable")
            return await super().publish(*a, **k)

    bus = FailingBus()
    client, settings = _client(bus)
    with client:
        payload = meta_payload("PNID_002", "wamid.retry", "hello")
        assert _post(client, settings, payload).status_code == 503
        bus.fail = False
        assert _post(client, settings, payload).status_code == 200   # Meta's retry is not swallowed as a duplicate
        assert len(bus.on(Topics.INBOUND_RECEIVED)) == 1


# ---- Meta webhook signature ---------------------------------------------------------------------------------------

def test_signature_verification_rejects_tampering_wrong_secret_and_malformed_headers():
    body = b'{"object":"whatsapp_business_account"}'
    good = sign_meta_payload(SECRET, body)
    assert verify_meta_signature(SECRET, body, good)
    assert not verify_meta_signature(SECRET, body + b" ", good)                      # body changed after signing
    assert not verify_meta_signature(SECRET, body, sign_meta_payload("y" * 40, body))  # other app's secret
    for header in (None, "", "sha256=", "sha1=" + good.split("=", 1)[1], good.split("=", 1)[1], "sha256=zz"):
        assert not verify_meta_signature(SECRET, body, header)
    assert not verify_meta_signature("", body, sign_meta_payload("", body))           # unset secret never verifies


def test_webhook_endpoint_rejects_body_tampered_after_signing():
    bus = InMemoryEventBus()
    client, settings = _client(bus)
    with client:
        body = json.dumps(meta_payload("PNID_002", "wamid.t", "hi")).encode()
        sig = sign_meta_payload(settings.meta_app_secret, body)
        tampered = body.replace(b"hi", b"ho")
        r = client.post("/api/v1/whatsapp/webhook", content=tampered,
                        headers={"Content-Type": "application/json", "X-Hub-Signature-256": sig})
        assert r.status_code == 401 and bus.published == []


# ---- JWT ----------------------------------------------------------------------------------------------------------

P = Principal(subject="u1", tenant_id="t-1", role=Role.OWNER, worker_id=None)


def test_jwt_roundtrip():
    assert decode_token(SECRET, issue_token(SECRET, P, 60)) == P


@pytest.mark.parametrize("case", ["expired", "wrong_secret", "tampered", "alg_none", "wrong_issuer", "no_tenant",
                                  "unknown_role", "garbage"])
def test_jwt_forgeries_and_expiry_are_rejected(case):
    now = int(time.time())
    claims = {"sub": "u1", "tenant_id": "t-1", "role": Role.OWNER.value, "iat": now, "exp": now + 60, "iss": "zuvocloud"}
    if case == "expired":
        token = issue_token(SECRET, P, -10)
    elif case == "wrong_secret":
        token = issue_token("z" * 40, P, 60)
    elif case == "tampered":
        h, _, s = issue_token(SECRET, P, 60).split(".")
        forged = jwt.utils.base64url_encode(json.dumps({**claims, "tenant_id": "t-2"}).encode()).decode()
        token = f"{h}.{forged}.{s}"   # other tenant, original signature
    elif case == "alg_none":
        token = jwt.encode(claims, None, algorithm="none")
    elif case == "wrong_issuer":
        token = jwt.encode({**claims, "iss": "evil"}, SECRET, algorithm="HS256")
    elif case == "no_tenant":
        token = jwt.encode({k: v for k, v in claims.items() if k != "tenant_id"}, SECRET, algorithm="HS256")
    elif case == "unknown_role":
        token = jwt.encode({**claims, "role": "superuser"}, SECRET, algorithm="HS256")
    else:
        token = "not.a.jwt"
    with pytest.raises(AuthError):
        decode_token(SECRET, token)


def test_jwt_requires_a_secret():
    with pytest.raises(AuthError):
        issue_token("", P, 60)
    with pytest.raises(AuthError):
        decode_token("", "x.y.z")


# ---- duplicate Meta status receipts -------------------------------------------------------------------------------

def _status_payload(pnid, wamid, status):
    return {"object": "whatsapp_business_account", "entry": [{"id": "x", "changes": [{"field": "messages", "value": {
        "messaging_product": "whatsapp", "metadata": {"display_phone_number": "x", "phone_number_id": pnid},
        "statuses": [{"id": wamid, "status": status, "timestamp": "1700000000", "recipient_id": "919876543210"}]}}]}]}


def test_duplicate_status_webhooks_publish_once_with_stable_event_ids():
    bus = InMemoryEventBus()
    client, settings = _client(bus)
    with client:
        for _ in range(3):  # Meta redelivers the same receipt
            assert _post(client, settings, _status_payload("PNID_002", "wamid.S1", "delivered")).status_code == 200
        assert _post(client, settings, _status_payload("PNID_002", "wamid.S1", "read")).status_code == 200
        events = bus.on(Topics.MESSAGE_STATUS)
        assert [e.payload["status"] for e in events] == ["delivered", "read"]

    # even if the Redis claim is lost (new store), the event id is identical, so the consumer ledger dedupes it
    bus2 = InMemoryEventBus()
    client2, _ = _client(bus2)
    with client2:
        _post(client2, settings, _status_payload("PNID_002", "wamid.S1", "delivered"))
    assert bus2.on(Topics.MESSAGE_STATUS)[0].event_id == events[0].event_id


def test_status_for_unknown_number_or_invalid_status_is_not_published():
    bus = InMemoryEventBus()
    client, settings = _client(bus)
    with client:
        _post(client, settings, _status_payload("NOT_A_LINE", "wamid.X", "delivered"))
        _post(client, settings, _status_payload("PNID_002", "wamid.X", "bogus"))
        assert bus.on(Topics.MESSAGE_STATUS) == []


# ---- dashboard-api authn / authz ----------------------------------------------------------------------------------

# /api/v1/health: status only, no tenant data. /internal/metrics: compose network only (asserted below).
PUBLIC_DASHBOARD_ROUTES = {"/livez", "/readyz", "/api/v1/health", "/internal/metrics", "/api/v1/auth/login",
                           "/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}


def _dashboard():
    """Build the app without running lifespan: no DB/Kafka is touched, auth runs before any handler body."""
    from dashboard_api.main import create_app
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    sm = async_sessionmaker(create_async_engine("postgresql+asyncpg://u:p@127.0.0.1:1/none"))
    return create_app(Settings(_env_file=None), InMemoryEventBus(), sm)


def _deps(route):
    seen, stack = set(), [route.dependant]
    while stack:
        d = stack.pop()
        if d.call is not None:
            seen.add(getattr(d.call, "__name__", ""))
        stack.extend(d.dependencies)
    return seen


def test_every_dashboard_route_requires_a_principal_except_the_public_allowlist():
    app = _dashboard()
    unauth = sorted(r.path for r in app.routes
                    if getattr(r, "dependant", None) and r.path not in PUBLIC_DASHBOARD_ROUTES
                    and "current_principal" not in _deps(r))
    assert unauth == [], f"routes reachable without a JWT: {unauth}"


def test_dashboard_rejects_missing_forged_and_expired_tokens_before_touching_data():
    settings = Settings(_env_file=None)
    client = TestClient(_dashboard())  # no lifespan: a 401 proves auth ran before any DB access
    p = Principal("o", "t-1", Role.OWNER, None)
    for headers in ({}, {"Authorization": "Bearer garbage"},
                    {"Authorization": "Bearer " + issue_token("z" * 40, p, 60)},
                    {"Authorization": "Bearer " + issue_token(settings.jwt_secret, p, -5)}):
        assert client.get("/api/v1/auth/me", headers=headers).status_code == 401
    assert client.get("/api/v1/auth/me", headers={"Authorization": "Bearer " + issue_token(settings.jwt_secret, p, 60)}).json()["tenant_id"] == "t-1"


def test_tenant_and_role_guards():
    from fastapi import HTTPException
    from dashboard_api.phase1 import _require
    from zuvo_common.web import enforce_tenant
    owner = Principal("o", "t-1", Role.OWNER, None)
    worker = Principal("w", "t-1", Role.SALES_WORKER, "w-1")
    assert enforce_tenant(owner, None) == "t-1" and enforce_tenant(owner, "t-1") == "t-1"
    with pytest.raises(HTTPException) as e:
        enforce_tenant(owner, "t-2")
    assert e.value.status_code == 403
    _require(owner, Role.OWNER, Role.MANAGER)
    with pytest.raises(HTTPException) as e:
        _require(worker, Role.OWNER, Role.MANAGER)
    assert e.value.status_code == 403


def test_internal_endpoints_are_not_routed_by_the_edge():
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    nginx = (root / "frontend/nginx.conf").read_text()
    caddy = (root / "deploy/caddy/Caddyfile").read_text()
    assert "/internal" not in nginx and "/internal" not in caddy
    import re
    proxied = re.findall(r"location\s+(?:=\s+)?(\S+)\s*\{[^}]*proxy_pass", nginx)
    assert proxied and all(loc.startswith("/api/") for loc in proxied), proxied
    assert "reverse_proxy frontend:80" in caddy  # Caddy only fronts nginx; it never reaches services directly
