import uuid
from types import SimpleNamespace

from fastapi.testclient import TestClient

from main import RagState, create_app
from services.retrieval_service import RetrievalResult

TID = str(uuid.uuid4())


class _Retriever:
    def __init__(self):
        self.calls = []

    async def retrieve(self, tid, query, top_k=None, min_similarity=None):
        self.calls.append((tid, query, top_k, min_similarity))
        return RetrievalResult(chunks=[{"title": "T", "text": "Open 9-7.", "similarity": 0.8}], strategy="hybrid_rrf",
                               below_threshold=False, top_similarity=0.8, embed_model="m@768")


def _client(token="s3cret"):
    retr = _Retriever()
    state = RagState(sm=None, store=None, embedder=SimpleNamespace(model_id="m@768"), retriever=retr, indexer=None,
                     tenants=lambda: [TID], service_token=token)
    c = TestClient(create_app(state))
    c.__enter__()  # run lifespan (sets app.state.rag)
    return c, retr


H = {"Authorization": "Bearer s3cret"}


def test_healthz_is_open():
    c, _ = _client()
    assert c.get("/healthz").json() == {"status": "ok"}


def test_auth_required():
    c, _ = _client()
    assert c.post(f"/v1/tenants/{TID}/retrieve", json={"query": "hi"}).status_code == 401
    assert c.post(f"/v1/tenants/{TID}/retrieve", json={"query": "hi"}, headers={"Authorization": "Bearer nope"}).status_code == 401


def test_token_unset_is_503():
    c, _ = _client(token="")
    assert c.post(f"/v1/tenants/{TID}/retrieve", json={"query": "hi"}, headers=H).status_code == 503


def test_unknown_or_malformed_tenant_404():
    c, _ = _client()
    assert c.post(f"/v1/tenants/{uuid.uuid4()}/retrieve", json={"query": "hi"}, headers=H).status_code == 404
    assert c.post("/v1/tenants/not-a-uuid/retrieve", json={"query": "hi"}, headers=H).status_code == 404


def test_retrieve_passes_tenant_and_params():
    c, retr = _client()
    r = c.post(f"/v1/tenants/{TID}/retrieve", json={"query": "timings?", "top_k": 2, "min_similarity": 0.5}, headers=H)
    assert r.status_code == 200 and r.json()["chunks"][0]["text"] == "Open 9-7."
    assert retr.calls == [(TID, "timings?", 2, 0.5)]


def test_ingest_rejects_bundle_paths_and_traversal():
    c, _ = _client()
    for path in ("knowledge/x.md", "../etc/passwd"):
        r = c.post(f"/v1/tenants/{TID}/documents", json={"source_path": path, "text": "hello"}, headers=H)
        assert r.status_code in (400, 422), (path, r.status_code)
