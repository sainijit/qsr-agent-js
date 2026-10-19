"""Phase 8 golden RAG tests against a real PostgreSQL/pgvector: real Indexer + Retriever + store, deterministic
local embedder (no Gemini calls, no cost). Same opt-in harness and synthetic tenants as test_rls.py.

Covers: expected retrieval, cross-tenant isolation, relevant vs irrelevant vs missing knowledge, low-confidence
routing, failed re-index keeps the previous version serving, recovery, document update and deletion,
claim/stale-claim handling, superseded updates and deterministic chunk ids.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import text

from zuvo_common.bundle import load_bundle
from zuvo_common.db import tenant_session
from zuvo_common.events import Extraction, Intent

from test_rls import pytestmark, world  # noqa: F401  (fixture + skip markers; also puts zuvo-ai-rag on sys.path)
from components.gemini import AsyncHashingEmbedder  # noqa: E402
from components.metrics import REGISTRY  # noqa: E402
from services.indexer import Indexer, build_chunker  # noqa: E402
from services.retrieval_service import Retriever  # noqa: E402
from services.store import PgVectorStore  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
FLOOR = 0.25  # hashing-embedder cosine floor (Gemini uses config min_similarity)

DOCS_A = {
    "uploads/hours.md": "# Showroom hours\n\nOur showroom is open from 9 am to 8 pm, Monday to Saturday. "
                        "The showroom is closed on Sunday.",
    "uploads/finance.md": "# Car finance\n\nWe arrange car loans with HDFC and ICICI banks. "
                          "EMI tenure ranges from 12 to 84 months with a minimum down payment of 10 percent.",
    "uploads/service.md": "# Service centre\n\nThe service centre offers free pickup and drop within 5 km. "
                          "Periodic maintenance service takes about four hours.",
}
DOC_B = ("uploads/secret.md", "# Bravo warranty\n\nBravo flibbertigibbet extended warranty covers seven years.")


class _FailingEmbedder(AsyncHashingEmbedder):
    def __init__(self, exc):
        super().__init__(768)
        self.exc = exc

    async def embed_documents(self, texts):
        raise self.exc


class _BadDimEmbedder(AsyncHashingEmbedder):
    async def embed_documents(self, texts):
        return [v[:10] for v in await super().embed_documents(texts)]


class _RateLimited(Exception):
    code = 429


def _indexer(world, embedder=None, tenants=None):
    return Indexer(world.sm, PgVectorStore(), embedder or AsyncHashingEmbedder(768), build_chunker(),
                   lambda: tenants or [world.a["tenant"], world.b["tenant"]])


def _retriever(world):
    return Retriever(world.sm, PgVectorStore(), AsyncHashingEmbedder(768), top_k=3, fetch_k=10, min_similarity=FLOOR)


async def _upsert(world, tid, path, body):
    import hashlib
    async with tenant_session(world.sm, tid) as s:
        doc_id, _ = await PgVectorStore().upsert_document(s, tid, path, path.rsplit("/", 1)[-1], body,
                                                          hashlib.sha256(body.encode()).hexdigest())
    return doc_id


async def _doc(world, tid, doc_id) -> dict:
    async with tenant_session(world.sm, tid) as s:
        return next(d for d in await PgVectorStore().documents(s, "hashing@768") if d["document_id"] == doc_id)


@pytest_asyncio.fixture
async def kb(world):
    ids = {p: await _upsert(world, world.a["tenant"], p, body) for p, body in DOCS_A.items()}
    ids["b"] = await _upsert(world, world.b["tenant"], *DOC_B)
    await _indexer(world).tick()
    REGISTRY.reset()
    return ids


def _first_doc(res):
    return res.chunks[0]["document_id"] if res.chunks else None


# ------------------------------------------------------------ retrieval quality

@pytest.mark.asyncio
@pytest.mark.parametrize("question,path", [
    ("what time does the showroom open on saturday", "uploads/hours.md"),
    ("can i get a car loan, what is the emi tenure", "uploads/finance.md"),
    ("do you offer free pickup for service", "uploads/service.md"),
])
async def test_golden_questions_retrieve_expected_document(world, kb, question, path):
    res = await _retriever(world).retrieve(world.a["tenant"], question)
    assert not res.below_threshold and _first_doc(res) == kb[path]
    assert res.chunks[0]["document_version"] == 1 and res.embed_model == "hashing@768"


@pytest.mark.asyncio
async def test_indexed_state_and_metadata(world, kb):
    d = await _doc(world, world.a["tenant"], kb["uploads/hours.md"])
    assert (d["index_state"], d["indexed_version"], d["index_attempts"], d["index_error"]) == ("indexed", 1, 0, None)
    assert d["chunk_count"] >= 1


@pytest.mark.asyncio
async def test_no_cross_tenant_retrieval(world, kb):
    res = await _retriever(world).retrieve(world.a["tenant"], "bravo flibbertigibbet extended warranty seven years")
    assert kb["b"] not in {c["document_id"] for c in res.chunks}
    res_b = await _retriever(world).retrieve(world.b["tenant"], "bravo flibbertigibbet extended warranty seven years")
    assert _first_doc(res_b) == kb["b"]


@pytest.mark.asyncio
async def test_missing_knowledge_is_below_threshold_and_routes_to_holding_reply(world, kb):
    from agent_worker.logic import decide, faq_proposal, run_outcome

    cfg = load_bundle(ROOT / "tenants" / "alpha-automobile").config
    q = "do you sell helicopters or submarines"
    res = await _retriever(world).retrieve(world.a["tenant"], q)
    assert res.below_threshold and res.chunks == []
    p = faq_proposal(cfg, [], Extraction(intent=Intent.FAQ, confidence=0.95, question=q), q, rag=res.to_dict())
    assert p.topic == "faq:unknown" and p.confidence == 0.0 and "no_knowledge" in p.reasons
    decision, reasons = decide(cfg, p, "auto", flagged=False, topic_auto_enabled=True)
    assert decision == "PENDING_APPROVAL" and run_outcome(decision, reasons) == "no_knowledge"


@pytest.mark.asyncio
async def test_weak_match_is_low_confidence_not_auto_sent(world, kb):
    from agent_worker.logic import decide, faq_proposal, run_outcome

    cfg = load_bundle(ROOT / "tenants" / "alpha-automobile").config
    q = "showroom"
    res = await _retriever(world).retrieve(world.a["tenant"], q)
    assert not res.below_threshold
    p = faq_proposal(cfg, [], Extraction(intent=Intent.FAQ, confidence=0.95, question=q), q, rag=res.to_dict())
    # hashing similarity sits below the RAG confidence ramp: a hit, but never confident enough to auto-send
    decision, reasons = decide(cfg, p, "auto", flagged=False, topic_auto_enabled=True)
    assert decision == "PENDING_APPROVAL" and run_outcome(decision, reasons) == "low_confidence"


# ------------------------------------------------------------ failure handling / versioning

@pytest.mark.asyncio
@pytest.mark.parametrize("bad,category", [(_FailingEmbedder(_RateLimited("quota")), "rate_limited"),
                                          (_BadDimEmbedder(768), "invalid_embedding")])
async def test_failed_reindex_keeps_previous_version_serving(world, kb, bad, category):
    tid, doc_id = world.a["tenant"], kb["uploads/hours.md"]
    await _upsert(world, tid, "uploads/hours.md", "# Showroom hours\n\nThe showroom now opens at 10 am daily, "
                                                  "including Sunday.")
    d = await _doc(world, tid, doc_id)
    assert (d["version"], d["index_state"], d["indexed_version"]) == (2, "pending", 1)

    assert await _indexer(world, bad, [tid]).reconcile_tenant(tid) == 0
    d = await _doc(world, tid, doc_id)
    assert d["index_state"] == "failed" and d["index_error"].startswith(category + ":") and d["index_attempts"] == 1
    assert REGISTRY.value("zuvo_rag_index_errors_total", category=category) == 1

    res = await _retriever(world).retrieve(tid, "what time does the showroom open on saturday")
    assert _first_doc(res) == doc_id and res.chunks[0]["document_version"] == 1
    assert "9 am" in res.chunks[0]["text"] and "10 am" not in " ".join(c["text"] for c in res.chunks)

    # failed documents back off; the reconcile loop does not hammer the embedding API
    async with tenant_session(world.sm, tid) as s:
        assert doc_id not in [p.id for p in await PgVectorStore().pending(s, "hashing@768", 100)]
        assert doc_id in [p.id for p in await PgVectorStore().pending(s, "hashing@768", 100, error_backoff_seconds=0)]
    async with world.owner_sm.begin() as s:  # backoff elapsed
        await s.execute(text("UPDATE kb_documents SET index_error_at = now() - interval '1 hour' WHERE id = :d"), {"d": doc_id})

    assert await _indexer(world, tenants=[tid]).reconcile_tenant(tid) == 1
    d = await _doc(world, tid, doc_id)
    assert (d["index_state"], d["indexed_version"], d["index_attempts"], d["index_error"]) == ("indexed", 2, 0, None)
    res = await _retriever(world).retrieve(tid, "what time does the showroom open on sunday")
    assert _first_doc(res) == doc_id and res.chunks[0]["document_version"] == 2
    assert "9 am" not in " ".join(c["text"] for c in res.chunks if c["document_id"] == doc_id)


@pytest.mark.asyncio
async def test_deleted_document_stops_serving(world, kb):
    tid, doc_id = world.a["tenant"], kb["uploads/finance.md"]
    async with tenant_session(world.sm, tid) as s:
        assert await PgVectorStore().deactivate(s, doc_id)
    res = await _retriever(world).retrieve(tid, "can i get a car loan, what is the emi tenure")
    assert doc_id not in {c["document_id"] for c in res.chunks}
    assert (await _doc(world, tid, doc_id))["index_state"] == "inactive"


@pytest.mark.asyncio
async def test_claim_is_exclusive_and_stale_claims_are_recovered(world, kb):
    tid = world.a["tenant"]
    doc_id = await _upsert(world, tid, "uploads/hours.md", "# Hours\n\nOpen 24 hours.")
    store = PgVectorStore()
    async with tenant_session(world.sm, tid) as s:
        checksum = (await store.pending(s, "hashing@768", 100))[0].checksum
        assert await store.claim(s, doc_id, checksum)
    async with tenant_session(world.sm, tid) as s:
        assert not await store.claim(s, doc_id, checksum)  # a second indexer must not take it
        assert doc_id not in [p.id for p in await store.pending(s, "hashing@768", 100)]
    async with world.owner_sm.begin() as s:  # the first indexer crashed mid-way
        await s.execute(text("UPDATE kb_documents SET index_started_at = now() - interval '1 hour' WHERE id = :d"),
                        {"d": doc_id})
    assert await _indexer(world, tenants=[tid]).reconcile_tenant(tid) == 1
    d = await _doc(world, tid, doc_id)
    assert d["index_state"] == "indexed" and d["indexed_version"] == 2


@pytest.mark.asyncio
async def test_document_changed_during_embedding_is_superseded_not_failed(world, kb):
    tid, doc_id = world.a["tenant"], kb["uploads/service.md"]
    await _upsert(world, tid, "uploads/service.md", "# Service\n\nService takes two hours.")

    class _Racing(AsyncHashingEmbedder):
        async def embed_documents(self, texts):
            await _upsert(world, tid, "uploads/service.md", "# Service\n\nService takes three hours.")
            return await super().embed_documents(texts)

    assert await _indexer(world, _Racing(768), [tid]).reconcile_tenant(tid) == 0
    d = await _doc(world, tid, doc_id)
    assert (d["index_state"], d["index_attempts"], d["indexed_version"], d["version"]) == ("pending", 0, 1, 3)
    assert REGISTRY.value("zuvo_rag_index_total", outcome="superseded") == 1
    assert await _indexer(world, tenants=[tid]).reconcile_tenant(tid) == 1
    assert (await _doc(world, tid, doc_id))["indexed_version"] == 3


@pytest.mark.asyncio
async def test_reindex_produces_identical_chunk_ids_and_keeps_lexical_chunks(world, kb):
    tid, doc_id = world.a["tenant"], kb["uploads/hours.md"]
    q = "SELECT id::text FROM kb_chunks WHERE document_id = :d AND embed_model IS NOT NULL ORDER BY ordinal"
    async with world.owner_sm.begin() as s:
        before = (await s.execute(text(q), {"d": doc_id})).scalars().all()
        await s.execute(text("INSERT INTO kb_chunks (id, tenant_id, document_id, ordinal, text) "
                             "VALUES (gen_random_uuid(), :t, :d, 0, 'lexical copy')"), {"t": tid, "d": doc_id})
    async with tenant_session(world.sm, tid) as s:
        assert await PgVectorStore().force_reindex(s) >= 3
    await _indexer(world, tenants=[tid]).reconcile_tenant(tid)
    async with world.owner_sm.begin() as s:
        after = (await s.execute(text(q), {"d": doc_id})).scalars().all()
        lexical = await s.scalar(text("SELECT count(*) FROM kb_chunks WHERE document_id = :d AND embed_model IS NULL"),
                                 {"d": doc_id})
    assert before and before == after and lexical == 1
