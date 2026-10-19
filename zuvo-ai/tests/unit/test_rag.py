"""zuvo-ai-rag integration on the agent-worker side (client + FAQ proposal)."""
from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest

from zuvo_common.bundle import load_bundle
from zuvo_common.events import Extraction, Intent
from zuvo_common.rag_client import RagClient, RagUnavailable

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def cfg():
    return load_bundle(ROOT / "tenants" / "alpha-automobile").config


def _x(q="what are your timings?", conf=0.9):
    return Extraction(intent=Intent.FAQ, confidence=conf, question=q)


def _rag(sim=0.8, below=False, text="We are open 9 am to 7 pm. Closed on public holidays. Free pickup within 5 km."):
    chunks = [] if below else [{"chunk_id": "c1", "title": "Workshop", "text": text, "similarity": sim}]
    return {"chunks": chunks, "strategy": "hybrid_rrf", "below_threshold": below, "top_similarity": sim,
            "embed_model": "gemini-embedding-001@768", "latency_ms": {"total": 120}}


def test_knowledge_defaults_to_lexical(cfg):
    assert cfg.knowledge.retrieval in ("lexical", "rag") and 1 <= cfg.knowledge.top_k <= 10


def test_rag_faq_confident_hit(cfg):
    from agent_worker.logic import faq_proposal

    p = faq_proposal(cfg, [], _x(), "timings?", rag=_rag(0.8))
    assert p.topic == "faq:workshop" and "9 am to 7 pm" in p.text
    assert p.confidence == pytest.approx(0.9)
    assert p.retrieved["strategy"] == "hybrid_rrf" and p.retrieved["chunk_ids"] == ["c1"]
    assert p.allowed_texts and p.facts


def test_rag_faq_confidence_ramps_with_similarity(cfg):
    from agent_worker.logic import faq_proposal

    assert faq_proposal(cfg, [], _x(), "q", rag=_rag(0.65)).confidence == pytest.approx(0.45)
    assert faq_proposal(cfg, [], _x(), "q", rag=_rag(0.5)).confidence == 0.0


def test_rag_faq_below_threshold_is_holding_reply(cfg):
    from agent_worker.logic import faq_proposal

    p = faq_proposal(cfg, [], _x(), "pizza?", rag=_rag(0.4, below=True))
    assert p.text == cfg.agent.holding_reply and p.confidence == 0.0 and "no_knowledge" in p.reasons


def test_lexical_path_unchanged(cfg):
    from agent_worker.logic import faq_proposal

    chunks = [({"id": "k1", "title": "Workshop"}, "Workshop timings are 9 am to 7 pm on all days.")]
    p = faq_proposal(cfg, chunks, _x("workshop timings"), "workshop timings")
    assert "9 am" in p.text and p.retrieved["strategy"] == "lexical"


def test_rag_client_success_and_errors():
    seen = {}

    def handler(req: httpx.Request):
        seen["auth"], seen["path"], seen["body"] = req.headers["authorization"], req.url.path, req.content
        if "fail" in req.url.path:
            return httpx.Response(500)
        return httpx.Response(200, json=_rag())

    async def run():
        c = RagClient("http://rag:8020/", "tok", transport=httpx.MockTransport(handler))
        r = await c.retrieve("t1", "timings", 2, 0.6)
        with pytest.raises(RagUnavailable):
            await c.retrieve("fail", "x")
        await c.aclose()
        return r

    r = asyncio.run(run())
    assert r["chunks"][0]["chunk_id"] == "c1" and seen["auth"] == "Bearer tok"

    def boom(req):
        raise httpx.ConnectError("down")

    async def run2():
        c = RagClient("http://rag:8020", "tok", transport=httpx.MockTransport(boom))
        with pytest.raises(RagUnavailable):
            await c.retrieve("t1", "x")
        await c.aclose()

    asyncio.run(run2())


def test_rag_client_error_categories():
    responses = {
        "/v1/tenants/a/retrieve": httpx.Response(503, json={"detail": "retrieval unavailable", "error": "embedding_unavailable"}),
        "/v1/tenants/b/retrieve": httpx.Response(401),
        "/v1/tenants/c/retrieve": httpx.Response(404),
        "/v1/tenants/d/retrieve": httpx.Response(500, text="boom"),
        "/v1/tenants/e/retrieve": httpx.Response(503, text="not json"),
    }

    async def run():
        c = RagClient("http://rag", "tok", transport=httpx.MockTransport(lambda req: responses[req.url.path]))
        out = {}
        for t in "abcde":
            with pytest.raises(RagUnavailable) as ei:
                await c.retrieve(t, "secret question")
            out[t] = ei.value.category
        await c.aclose()
        return out

    assert asyncio.run(run()) == {"a": "embedding_unavailable", "b": "unauthorized", "c": "unknown_tenant",
                                  "d": "http_500", "e": "http_503"}

    def slow(req):
        raise httpx.ReadTimeout("slow")

    async def run2():
        c = RagClient("http://rag", "tok", transport=httpx.MockTransport(slow))
        with pytest.raises(RagUnavailable) as ei:
            await c.retrieve("t", "x")
        await c.aclose()
        return ei.value.category

    assert asyncio.run(run2()) == "timeout"


@pytest.mark.parametrize("decision,reasons,kw,expected", [
    ("AUTO_SENT", [], {}, "ok"),
    ("SKIPPED", ["mode_off"], {"rag_error": "timeout"}, "skipped"),
    ("PENDING_APPROVAL", ["no_knowledge"], {"rag_error": "network"}, "rag_unavailable"),
    ("PENDING_APPROVAL", ["no_knowledge", "confidence<0.8"], {}, "no_knowledge"),
    ("PENDING_APPROVAL", ["ai_budget_exhausted"], {}, "ai_budget_exhausted"),
    ("PENDING_APPROVAL", ["draft_guard"], {"draft_error": None}, "draft_rejected"),
    ("AUTO_SENT", [], {"draft_error": "TimeoutError"}, "draft_failed"),
    ("PENDING_APPROVAL", ["confidence<0.8"], {}, "low_confidence"),
    ("PENDING_APPROVAL", ["low_confidence"], {}, "low_confidence"),
    ("PENDING_APPROVAL", ["mode_draft"], {}, "ok"),
])
def test_run_outcome(decision, reasons, kw, expected):
    from agent_worker.logic import run_outcome

    assert run_outcome(decision, reasons, **kw) == expected


def test_run_diagnostics_has_ids_scores_but_no_text(cfg):
    from agent_worker.logic import faq_proposal, run_diagnostics

    rag = _rag()
    rag["chunks"][0].update(document_id="d1", document_version=3)
    p = faq_proposal(cfg, [], _x("workshop timings secret"), "workshop timings secret", rag=rag)
    d = run_diagnostics(p, extraction={"tier": "tier2", "model": "flash", "latency_ms": 120, "input_tokens": None},
                        rag=rag, usage={"model": "flash", "latency_ms": 300, "tokens_in": 10, "tokens_out": 5,
                                        "cost": 1})
    doc = d["retrieval"]["documents"][0]
    assert doc == {"document_id": rag["chunks"][0]["document_id"], "document_version": 3, "chunk_id": "c1",
                   "similarity": rag["chunks"][0]["similarity"]}
    assert d["extraction"] == {"tier": "tier2", "model": "flash", "latency_ms": 120}
    assert d["draft"] == {"model": "flash", "latency_ms": 300, "tokens_in": 10, "tokens_out": 5}
    flat = str(d)
    assert "secret" not in flat and rag["chunks"][0]["text"] not in flat


def test_run_diagnostics_records_rag_fallback(cfg):
    from agent_worker.logic import faq_proposal, run_diagnostics

    p = faq_proposal(cfg, [({"id": "k1", "title": "W"}, "Workshop opens 9 am.")], _x("workshop"), "workshop")
    p.retrieved["strategy"] = "lexical_fallback"
    d = run_diagnostics(p, rag=None, rag_error="embedding_unavailable")
    assert d["retrieval"] == {"strategy": "lexical_fallback", "rag_error": "embedding_unavailable"}
