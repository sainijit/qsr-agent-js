import asyncio
import json

import pytest

from conftest import DELHI, JAIPUR
from zuvo_common.audit import mask_details
from zuvo_common.bus import InMemoryEventBus, Record
from zuvo_common.contract import ConsumerContract, PermanentError
from zuvo_common.events import Envelope, Topics
from zuvo_common.idempotency import InMemoryIdempotencyStore
from zuvo_common.phone import normalize_phone
from zuvo_common.security import AuthError, Principal, decode_token, issue_token, sign_meta_payload, verify_meta_signature
from zuvo_common.tenancy import Role, TenantPolicyLoader, TenantRouting, worker_uuid


def test_phone_normalization():
    assert normalize_phone("98290 22222") == "919829022222"
    assert normalize_phone("+91-98290-22222") == "919829022222"
    with pytest.raises(ValueError):
        normalize_phone("abc")


def test_routing_resolves_lines_and_tenants(tmp_path):
    import os
    r = TenantRouting(os.environ["TENANT_ROUTING_PATH"])
    line = r.resolve("PNID_002")
    assert line.tenant_id == JAIPUR and line.worker_name == "Suresh Verma"
    assert line.worker_id == worker_uuid(JAIPUR, "suresh-field")
    assert r.resolve("PNID_D_101").tenant_id == DELHI
    assert r.resolve("unknown") is None
    assert len(r.lines_for_tenant(JAIPUR)) == 6


def test_routing_keeps_last_valid_config_on_bad_reload(tmp_path):
    import os, time
    path = tmp_path / "routing.yaml"
    path.write_text(open(os.environ["TENANT_ROUTING_PATH"]).read())
    from conftest import ROOT
    r = TenantRouting(str(path), bundles_dir=str(ROOT / "tenants"))
    assert r.resolve("PNID_002") is not None
    time.sleep(0.01)
    path.write_text("version: 1\ntenants: [ {broken: true} ]\n")
    os.utime(path, (time.time() + 5, time.time() + 5))
    assert r.resolve("PNID_002") is not None


def test_policy_loader_rejects_traversal_and_mismatch(tmp_path):
    import os
    loader = TenantPolicyLoader(os.environ["TENANT_POLICY_ROOT"])
    assert loader.load("tenants/jaipur-hardware/config.yaml", JAIPUR).intents.confidence_threshold == 0.8
    with pytest.raises(ValueError):
        loader.load("../etc/passwd", JAIPUR)
    with pytest.raises(ValueError):
        loader.load("tenants/jaipur-hardware/config.yaml", DELHI)


def test_hmac_and_jwt():
    body = b'{"x":1}'
    sig = sign_meta_payload("s3cret", body)
    assert verify_meta_signature("s3cret", body, sig)
    assert not verify_meta_signature("s3cret", body + b" ", sig)
    assert not verify_meta_signature("s3cret", body, None)
    p = Principal(subject="a", tenant_id=JAIPUR, role=Role.OWNER, worker_id=None)
    tok = issue_token("k" * 32, p, 60)
    assert decode_token("k" * 32, tok) == p
    with pytest.raises(AuthError):
        decode_token("other" * 8, tok)


def test_audit_masks_pii():
    d = mask_details({"customer_phone": "919829022222", "text": "50 box pipe", "n": 3})
    assert "9829022222" not in json.dumps(d)
    assert d["text"] == "<11 chars>" and d["n"] == 3


def _record(env: Envelope, tenant_header=None) -> Record:
    headers = dict((k, v.decode()) for k, v in env.headers())
    if tenant_header is not None:
        headers["tenant_id"] = tenant_header
    return Record(Topics.INBOUND_RECEIVED, env.key, env.model_dump_json().encode(), headers)


@pytest.mark.asyncio
async def test_contract_dlq_idempotency_and_retries():
    bus, idem, calls = InMemoryEventBus(), InMemoryIdempotencyStore(), []

    async def handler(env, _r):
        calls.append(env.event_id)
        if env.payload.get("boom") == "permanent":
            raise PermanentError("bad")
        if env.payload.get("boom") == "transient" and calls.count(env.event_id) < 2:
            raise RuntimeError("flaky")

    c = ConsumerContract(bus, idem, "g", handler, backoff_seconds=0)
    ok = Envelope(event_type="t", tenant_id=JAIPUR, worker_id="w", payload={})
    await c(_record(ok))
    await c(_record(ok))  # duplicate delivery
    assert calls.count(ok.event_id) == 1

    await c(_record(ok, tenant_header=""))  # missing tenant header
    await c(_record(Envelope(event_type="t", tenant_id=JAIPUR, worker_id="w", payload={}), tenant_header=DELHI))
    flaky = Envelope(event_type="t", tenant_id=JAIPUR, worker_id="w", payload={"boom": "transient"})
    await c(_record(flaky))
    assert calls.count(flaky.event_id) == 2
    perm = Envelope(event_type="t", tenant_id=JAIPUR, worker_id="w", payload={"boom": "permanent"})
    await c(_record(perm))
    assert calls.count(perm.event_id) == 1

    dlq = [r for r in bus.published if r.topic == Topics.dlq(Topics.INBOUND_RECEIVED)]
    errors = [r.headers["error"] for r in dlq]
    assert len(dlq) == 3
    assert any("missing tenant_id" in e for e in errors)
    assert any("mismatch" in e for e in errors)
    assert any("permanent" in e for e in errors)
