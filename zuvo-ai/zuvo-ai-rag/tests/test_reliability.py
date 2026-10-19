"""Phase 8: embedding validation, failure classification, bounded retries, metrics and the 503 error contract."""
import asyncio
import math
import uuid
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from components import gemini
from components.gemini import AsyncHashingEmbedder, EmbeddingError, classify, validate_vectors, with_retries
from components.metrics import REGISTRY
from main import RagState, check_schema, create_app
from services.retrieval_service import RetrievalError, Retriever
from services.store import chunk_id

TID = str(uuid.uuid4())
H = {"Authorization": "Bearer s3cret"}


class _HTTPErr(Exception):
    def __init__(self, code):
        super().__init__(f"HTTP {code}")
        self.code = code


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    REGISTRY.reset()

    async def nosleep(_s):
        return None

    monkeypatch.setattr(gemini.asyncio, "sleep", nosleep)


# ------------------------------------------------------------ validation

def test_validate_vectors_accepts_good_batch():
    validate_vectors([[0.1, 0.2, 0.3], [1.0, 0.0, 0.0]], 2, 3)


@pytest.mark.parametrize("vecs,count,dim,msg", [
    ([[1.0, 0.0]], 2, 2, "expected 2"),
    ([[1.0, 0.0, 0.0]], 1, 2, "dimension 3"),
    ([[1.0, math.nan]], 1, 2, "non-finite"),
    ([[1.0, math.inf]], 1, 2, "non-finite"),
    ([[0.0, 0.0]], 1, 2, "zero vector"),
])
def test_validate_vectors_rejects(vecs, count, dim, msg):
    with pytest.raises(EmbeddingError, match=msg):
        validate_vectors(vecs, count, dim)


# ------------------------------------------------------------ classification / retries

@pytest.mark.parametrize("exc,cat", [
    (asyncio.TimeoutError(), "timeout"),
    (_HTTPErr(429), "rate_limited"),
    (_HTTPErr(503), "server_error"),
    (_HTTPErr(400), "client_error"),
    (httpx.ConnectError("down"), "network"),
    (EmbeddingError("bad"), "invalid_embedding"),
    (ValueError("x"), "internal"),
])
def test_classify(exc, cat):
    assert classify(exc) == cat


def _flaky(errors):
    calls = {"n": 0}

    async def call():
        calls["n"] += 1
        if errors:
            raise errors.pop(0)
        return "ok"
    return call, calls


@pytest.mark.parametrize("err", [_HTTPErr(429), _HTTPErr(500), httpx.ReadError("reset"), asyncio.TimeoutError()])
def test_with_retries_retries_transient(err):
    call, calls = _flaky([err, err])
    assert asyncio.run(with_retries(call, 3, 1.0)) == "ok" and calls["n"] == 3


def test_with_retries_is_bounded():
    call, calls = _flaky([_HTTPErr(503)] * 5)
    with pytest.raises(_HTTPErr):
        asyncio.run(with_retries(call, 3, 1.0))
    assert calls["n"] == 3


@pytest.mark.parametrize("err", [_HTTPErr(400), _HTTPErr(403), EmbeddingError("dim")])
def test_with_retries_does_not_retry_permanent(err):
    call, calls = _flaky([err])
    with pytest.raises(type(err)):
        asyncio.run(with_retries(call, 3, 1.0))
    assert calls["n"] == 1


def test_with_retries_backoff_is_jittered_and_capped(monkeypatch):
    delays = []

    async def rec(s):
        delays.append(s)
    monkeypatch.setattr(gemini.asyncio, "sleep", rec)
    call, _ = _flaky([_HTTPErr(500)] * 6)
    with pytest.raises(_HTTPErr):
        asyncio.run(with_retries(call, 6, 1.0, base_delay=1.0, max_delay=4.0))
    assert len(delays) == 5 and all(0 <= d <= 4.0 for d in delays)


def test_chunk_ids_are_deterministic():
    a = chunk_id("d1", "sum", "m@768", 0)
    assert a == chunk_id("d1", "sum", "m@768", 0)
    assert len({a, chunk_id("d1", "sum", "m@768", 1), chunk_id("d1", "sum2", "m@768", 0), chunk_id("d1", "sum", "m@3", 0)}) == 4


# ------------------------------------------------------------ retriever error mapping + metrics

class _DeadEmbedder(AsyncHashingEmbedder):
    async def embed_query(self, text):
        raise _HTTPErr(429)


def test_retriever_embedding_failure_is_typed_and_counted():
    r = Retriever(sm=None, store=None, embedder=_DeadEmbedder(8))
    with pytest.raises(RetrievalError) as ei:
        asyncio.run(r.retrieve(TID, "hours?"))
    assert ei.value.category == "embedding_unavailable" and ei.value.cause == "rate_limited"
    assert REGISTRY.value("zuvo_rag_retrieve_total", outcome="error") == 1
    assert REGISTRY.value("zuvo_rag_embed_failures_total", operation="query", category="rate_limited") == 1


def test_metrics_render_has_no_tenant_labels():
    REGISTRY.inc("zuvo_rag_retrieve_total", outcome="hit")
    REGISTRY.observe("zuvo_rag_retrieve_seconds", 0.2)
    text = REGISTRY.render()
    assert 'zuvo_rag_retrieve_total{outcome="hit"} 1' in text
    assert 'zuvo_rag_retrieve_seconds_bucket{le="0.25"} 1' in text and "zuvo_rag_retrieve_seconds_count 1" in text
    assert "tenant" not in text


# ------------------------------------------------------------ API contract

class _FailingRetriever:
    def __init__(self, category):
        self.category = category

    async def retrieve(self, *a, **k):
        raise RetrievalError(self.category, "timeout")


def _client(retriever, **kw):
    state = RagState(sm=None, store=None, embedder=SimpleNamespace(model_id="m@768"), retriever=retriever, indexer=None,
                     tenants=lambda: [TID], service_token="s3cret", **kw)
    c = TestClient(create_app(state))
    c.__enter__()
    return c


@pytest.mark.parametrize("category", ["embedding_unavailable", "store_unavailable"])
def test_retrieve_failure_is_503_with_category(category):
    r = _client(_FailingRetriever(category)).post(f"/v1/tenants/{TID}/retrieve", json={"query": "hi"}, headers=H)
    assert r.status_code == 503 and r.json()["error"] == category and "hi" not in r.text


def test_metrics_endpoint_is_prometheus_text():
    r = _client(_FailingRetriever("x")).get("/metrics")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain")
    assert "# TYPE zuvo_rag_index_total counter" in r.text


def test_dimension_mismatch_fails_readiness_and_disables_indexing():
    class _Store:
        async def embedding_dimension(self, s):
            return 768

    class _Sess:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    state = RagState(sm=lambda: _Sess(), store=_Store(), embedder=SimpleNamespace(model_id="m@3072", dimensions=3072),
                     retriever=None, indexer=None, tenants=lambda: [TID], service_token="s3cret")
    asyncio.run(check_schema(state))
    assert state.schema_error == "embedding_dimension_mismatch: db=768 embedder=3072"
    state.embedder = SimpleNamespace(model_id="m@768", dimensions=768)
    state.schema_error = None
    asyncio.run(check_schema(state))
    assert state.schema_error is None
