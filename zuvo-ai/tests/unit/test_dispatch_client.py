"""Phase 7: Meta client outcome classification against a mocked Graph API (no network)."""
import asyncio

import httpx
import pytest

from dispatch import main as dm


class _S:
    meta_dry_run, meta_system_user_token = False, "real-token"
    meta_graph_base_url, meta_api_version = "https://graph.invalid", "v25.0"


def _client(monkeypatch, handler):
    calls = []

    def wrapped(request: httpx.Request):
        calls.append(request)
        return handler(request, len(calls))

    real = httpx.AsyncClient

    class Mocked(real):
        def __init__(self, *a, **kw):
            kw["transport"] = httpx.MockTransport(wrapped)
            super().__init__(*a, **kw)

    monkeypatch.setattr(dm.httpx, "AsyncClient", Mocked)
    return dm.MetaWhatsAppClient(_S(), retries=3, backoff=0.0), calls


def _send(c, callback="cb-1"):
    return asyncio.run(c.send_text("123456", "919800000000", "hi", callback=callback))


def _raise(exc_type):
    def h(request, n):
        raise exc_type("boom", request=request)
    return h


def test_accepted_returns_wamid_and_sends_callback(monkeypatch):
    c, calls = _client(monkeypatch, lambda r, n: httpx.Response(200, json={"messages": [{"id": "wamid.REAL1"}]}))
    r = _send(c, callback="00000000-0000-0000-0000-000000000001")
    assert (r.success, r.wamid, r.category, r.dry_run) == (True, "wamid.REAL1", "accepted", False)
    import json
    body = json.loads(calls[0].content)
    assert body["biz_opaque_callback_data"] == "00000000-0000-0000-0000-000000000001"
    assert body["to"] == "919800000000" and body["type"] == "text"
    assert calls[0].headers["authorization"].endswith("real-token")


def test_no_callback_field_when_not_given(monkeypatch):
    c, calls = _client(monkeypatch, lambda r, n: httpx.Response(200, json={"messages": [{"id": "w"}]}))
    _send(c, callback=None)
    import json
    assert "biz_opaque_callback_data" not in json.loads(calls[0].content)


@pytest.mark.parametrize("exc", [httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout])
def test_connection_failures_are_not_sent_and_retried(monkeypatch, exc):
    c, calls = _client(monkeypatch, _raise(exc))
    r = _send(c)
    assert not r.success and r.category == "not_sent" and len(calls) == 3


@pytest.mark.parametrize("exc", [httpx.ReadTimeout, httpx.WriteTimeout, httpx.RemoteProtocolError, httpx.ReadError])
def test_after_request_left_is_ambiguous_and_never_retried(monkeypatch, exc):
    c, calls = _client(monkeypatch, _raise(exc))
    r = _send(c)
    assert not r.success and r.category == "ambiguous" and len(calls) == 1
    assert r.error.startswith("transport: ")


@pytest.mark.parametrize("status,body", [
    (429, {}),
    (503, {}),
    (400, {"error": {"code": 131056, "message": "pair rate limit"}}),
    (400, {"error": {"code": 130429, "message": "throughput"}}),
    (400, {"error": {"code": 80007, "message": "rate limit"}}),
])
def test_explicit_not_processed_is_not_sent(monkeypatch, status, body):
    c, calls = _client(monkeypatch, lambda r, n: httpx.Response(status, json=body))
    r = _send(c)
    assert r.category == "not_sent" and len(calls) == 3 and r.error.startswith(f"meta_http_{status}")


def test_retry_then_success(monkeypatch):
    c, calls = _client(monkeypatch, lambda r, n: httpx.Response(429) if n == 1 else
                       httpx.Response(200, json={"messages": [{"id": "wamid.OK"}]}))
    r = _send(c)
    assert r.success and r.wamid == "wamid.OK" and len(calls) == 2


def test_connect_error_then_success_sends_once_accepted(monkeypatch):
    def h(request, n):
        if n == 1:
            raise httpx.ConnectError("refused", request=request)
        return httpx.Response(200, json={"messages": [{"id": "wamid.OK2"}]})
    c, calls = _client(monkeypatch, h)
    assert _send(c).wamid == "wamid.OK2" and len(calls) == 2


@pytest.mark.parametrize("status", [500, 502, 504])
def test_server_errors_are_ambiguous(monkeypatch, status):
    c, calls = _client(monkeypatch, lambda r, n: httpx.Response(status, json={"error": {"code": 1}}))
    r = _send(c)
    assert r.category == "ambiguous" and len(calls) == 1


@pytest.mark.parametrize("status,code", [(400, 131026), (400, 100), (401, 190), (403, 10)])
def test_client_errors_are_rejected(monkeypatch, status, code):
    c, calls = _client(monkeypatch, lambda r, n: httpx.Response(status, json={"error": {"code": code}}))
    r = _send(c)
    assert r.category == "rejected" and len(calls) == 1 and r.error == f"meta_http_{status}:{code}"


def test_non_json_error_body(monkeypatch):
    c, _ = _client(monkeypatch, lambda r, n: httpx.Response(400, text="<html>bad</html>"))
    r = _send(c)
    assert r.category == "rejected" and r.error == "meta_http_400"


def test_simulated_line_never_calls_http(monkeypatch):
    c, calls = _client(monkeypatch, lambda r, n: httpx.Response(500))
    r = asyncio.run(c.send_text("PNID_JF_01", "9198", "hi", callback="x"))
    assert r.success and r.dry_run and not calls


def test_retry_after_is_bounded():
    for attempts in range(1, 12):
        d = dm.retry_after_seconds(attempts)
        assert 0 < d <= dm.RETRY_CAP_S
    assert dm.retry_after_seconds(1) <= dm.RETRY_BASE_S
