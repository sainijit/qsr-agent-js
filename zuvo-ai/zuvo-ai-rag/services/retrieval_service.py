"""Tenant-scoped hybrid retrieval: Gemini query embedding (Redis-cached) ->
pgvector cosine + Postgres full-text, fused by RRF -> optional Gemini listwise
rerank -> cosine-similarity floor."""
from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import asdict, dataclass, field
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from zuvo_common.db import tenant_session

from components.gemini import classify
from components.metrics import REGISTRY
from services.store import Hit, PgVectorStore

logger = logging.getLogger(__name__)


class RetrievalError(RuntimeError):
    """category: embedding_unavailable | store_unavailable; cause: classify() of the underlying error."""

    def __init__(self, category: str, cause: str):
        super().__init__(f"{category}: {cause}")
        self.category, self.cause = category, cause


@dataclass
class RetrievalResult:
    chunks: list[dict]
    strategy: str
    below_threshold: bool
    top_similarity: Optional[float]
    embed_model: str
    latency_ms: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


class Retriever:
    def __init__(self, sm: async_sessionmaker[AsyncSession], store: PgVectorStore, embedder, *, top_k: int = 3,
                 fetch_k: int = 10, rrf_k: int = 60, min_similarity: float = 0.6, reranker=None,
                 rerank_min_score: float = 5.0, redis=None, cache_ttl: int = 604800):
        self.sm, self.store, self.embedder = sm, store, embedder
        self.top_k, self.fetch_k, self.rrf_k, self.min_similarity = top_k, fetch_k, rrf_k, min_similarity
        self.reranker, self.rerank_min_score = reranker, rerank_min_score
        self.redis, self.cache_ttl = redis, cache_ttl

    async def _query_vector(self, query: str) -> tuple[list[float], bool]:
        key = "rag:qemb:" + hashlib.sha256(f"{self.embedder.model_id}|{query.strip().lower()}".encode()).hexdigest()
        if self.redis is not None:
            try:
                cached = await self.redis.get(key)
                if cached:
                    return json.loads(cached), True
            except Exception:  # noqa: BLE001 - cache is best-effort
                logger.warning("query cache read failed")
        vec = await self.embedder.embed_query(query)
        if self.redis is not None:
            try:
                await self.redis.set(key, json.dumps(vec), ex=self.cache_ttl)
            except Exception:  # noqa: BLE001
                logger.warning("query cache write failed")
        return vec, False

    async def retrieve(self, tenant_id: str, query: str, top_k: Optional[int] = None,
                       min_similarity: Optional[float] = None) -> RetrievalResult:
        k = max(1, min(top_k or self.top_k, 10))
        floor = self.min_similarity if min_similarity is None else min_similarity
        t0 = time.perf_counter()
        try:
            qvec, cached = await self._query_vector(query)
        except Exception as exc:
            self._fail("embedding_unavailable", exc, t0)
            REGISTRY.inc("zuvo_rag_embed_failures_total", operation="query", category=classify(exc))
            raise RetrievalError("embedding_unavailable", classify(exc)) from exc
        t1 = time.perf_counter()
        try:
            async with tenant_session(self.sm, tenant_id) as s:
                hits = await self.store.search(s, qvec, query, self.embedder.model_id, max(k, self.fetch_k), self.rrf_k)
        except Exception as exc:
            self._fail("store_unavailable", exc, t0)
            raise RetrievalError("store_unavailable", exc.__class__.__name__) from exc
        t2 = time.perf_counter()
        strategy = "hybrid_rrf"
        if self.reranker is not None and len(hits) > 1:
            try:
                scores = await self.reranker.score(query, [h.text for h in hits])
                for h, sc in zip(hits, scores):
                    h.rerank = sc
                hits = sorted((h for h in hits if (h.rerank or 0) >= self.rerank_min_score),
                              key=lambda h: h.rerank or 0, reverse=True)
                strategy = "hybrid_llm_rerank"
            except Exception:  # noqa: BLE001 - fall back to fusion order
                logger.exception("rerank failed")
        t3 = time.perf_counter()
        top_sim = max((h.similarity for h in hits), default=None)
        kept: list[Hit] = [h for h in hits if h.similarity >= floor][:k]
        result = RetrievalResult(
            chunks=[{"chunk_id": h.chunk_id, "document_id": h.document_id, "title": h.title, "source_path": h.source_path,
                     "section": h.section, "text": h.text, "similarity": round(h.similarity, 4), "rrf": round(h.rrf, 5),
                     "vector_rank": h.vector_rank, "text_rank": h.text_rank, "rerank": h.rerank, "rank": i + 1,
                     "document_version": h.document_version}
                    for i, h in enumerate(kept)],
            strategy=strategy, below_threshold=not kept,
            top_similarity=round(top_sim, 4) if top_sim is not None else None, embed_model=self.embedder.model_id,
            latency_ms={"embed": int((t1 - t0) * 1000), "search": int((t2 - t1) * 1000),
                        "rerank": int((t3 - t2) * 1000), "total": int((t3 - t0) * 1000), "query_cache_hit": cached})
        logger.info("retrieve", extra={"ctx": {
            "tenant_id": tenant_id, "operation": "rag.retrieve", "outcome": "hit" if kept else "below_threshold",
            "duration_ms": result.latency_ms["total"], "k": k, "hits": len(hits), "kept": len(kept),
            "top_similarity": result.top_similarity, "strategy": strategy, "cache_hit": cached}})
        REGISTRY.inc("zuvo_rag_retrieve_total", outcome="hit" if kept else ("below_threshold" if hits else "empty"))
        REGISTRY.observe("zuvo_rag_retrieve_seconds", t3 - t0)
        return result

    @staticmethod
    def _fail(category: str, exc: Exception, t0: float) -> None:
        REGISTRY.inc("zuvo_rag_retrieve_total", outcome="error")
        REGISTRY.inc("zuvo_rag_retrieve_errors_total", category=category)
        REGISTRY.observe("zuvo_rag_retrieve_seconds", time.perf_counter() - t0)
        logger.warning("retrieve failed category=%s cause=%s", category, classify(exc))
