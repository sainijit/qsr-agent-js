"""zuvo-ai-rag: multi-tenant, Gemini-only knowledge retrieval for ZuvoCloud."""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Optional

from fastapi import FastAPI

from zuvo_common.httpobs import install_request_observability
from zuvo_common.logging import install_access_log_redaction, install_json_logging
from zuvo_common.settings import get_settings

from api.routes import router
from services.prompt_builder import PromptBuilder
from services.retrieval_service import Retriever
from services.store import PgVectorStore
from utils.config_loader import config

logger = logging.getLogger("zuvo-ai-rag")


@dataclass
class RagState:
    sm: Any
    store: PgVectorStore
    embedder: Any
    retriever: Retriever
    indexer: Any
    tenants: Callable[[], Iterable[str]]
    service_token: str
    text: Any = None
    prompt_builder: Optional[PromptBuilder] = None
    max_ingest_chars: int = 100_000
    answer_max_tokens: int = 300
    poll_seconds: float = 0.0  # 0 disables the background reconcile loop
    schema_error: Optional[str] = None  # set at startup when kb_chunks.embedding cannot hold the embedder's vectors


def _prompt_builder() -> PromptBuilder:
    a = config.answering
    return PromptBuilder(system_prompt=a.system_prompt, max_context_chars=int(a.max_context_chars),
                         history_turns=int(a.history_turns), include_source_markers=bool(a.include_source_markers),
                         fallback_to_general_knowledge=bool(a.fallback_to_general_knowledge))


def build_state() -> RagState:
    from redis.asyncio import Redis

    from components.gemini import GeminiEmbedder, GeminiListwiseReranker, GeminiText
    from services.indexer import Indexer, build_chunker
    from zuvo_common.db import make_sessionmaker
    from zuvo_common.tenancy import TenantRouting

    st = get_settings()
    g, r = config.gemini, config.retrieval
    if not st.gemini_api_key:
        raise SystemExit("GEMINI_API_KEY is required for zuvo-ai-rag")
    sm = make_sessionmaker(st.database_url, pool_size=5)
    store = PgVectorStore()
    timeout, attempts = float(g.timeout_seconds), int(g.max_attempts)
    embedder = GeminiEmbedder(st.gemini_api_key, g.embed_model, int(g.embed_dimensions), int(g.embed_batch_size),
                              timeout, attempts)
    text = GeminiText(st.gemini_api_key, g.text_model or st.gemini_model, timeout, attempts)
    reranker = GeminiListwiseReranker(text) if str(r.rerank.mode).lower() == "llm" else None
    routing = TenantRouting(st.tenant_routing_path)

    def tenants() -> list[str]:
        return [b.tenant_id for b in routing.bundles.all()]

    chunker = build_chunker(text.sync_generator() if bool(config.chunking.llm_split) else None)
    indexer = Indexer(sm, store, embedder, chunker, tenants, int(config.indexer.max_docs_per_tick))
    retriever = Retriever(sm, store, embedder, top_k=int(r.top_k), fetch_k=int(r.fetch_k), rrf_k=int(r.rrf_k),
                          min_similarity=float(r.min_similarity), reranker=reranker,
                          rerank_min_score=float(r.rerank.min_score), redis=Redis.from_url(st.redis_url),
                          cache_ttl=int(r.query_cache_ttl_seconds))
    return RagState(sm=sm, store=store, embedder=embedder, retriever=retriever, indexer=indexer, tenants=tenants,
                    service_token=os.environ.get("RAG_SERVICE_TOKEN", ""), text=text, prompt_builder=_prompt_builder(),
                    max_ingest_chars=int(config.chunking.max_ingest_chars),
                    answer_max_tokens=int(config.answering.max_tokens),
                    poll_seconds=float(config.indexer.poll_seconds) if bool(config.indexer.enabled) else 0.0)


async def check_schema(rag: RagState) -> None:
    """Embedding-dimension guard: a mismatch would fail every insert and every search. Readiness reports it and the
    indexer stays off rather than marking every document failed."""
    if rag.embedder is None:
        return
    want = int(getattr(rag.embedder, "dimensions", 0) or 0)
    try:
        async with rag.sm() as s:  # catalog query only; no tenant data
            have = await rag.store.embedding_dimension(s)
    except Exception as exc:  # noqa: BLE001 - DB not reachable yet: readiness reports db=false instead
        logger.warning("embedding dimension check skipped: %s", exc.__class__.__name__)
        return
    if have is not None and want and have != want:
        rag.schema_error = f"embedding_dimension_mismatch: db={have} embedder={want}"
        logger.error("%s - indexing disabled, readiness failing", rag.schema_error)


def create_app(state: Optional[RagState] = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.rag = state or build_state()
        rag: RagState = app.state.rag
        if not rag.service_token:
            logger.error("RAG_SERVICE_TOKEN is empty: all /v1 routes will return 503")
        await check_schema(rag)
        task = None
        if rag.poll_seconds > 0 and rag.indexer is not None and not rag.schema_error:
            task = asyncio.create_task(rag.indexer.run_forever(rag.poll_seconds))
        try:
            yield
        finally:
            if task:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task

    app = FastAPI(title="zuvo-ai-rag", version="1.0.0", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.include_router(router)
    install_request_observability(app, "zuvo-ai-rag")
    return app


def _configure_logging() -> None:
    install_json_logging("zuvo-ai-rag", get_settings().log_level)
    install_access_log_redaction()
    for noisy in ("components.chunker_component", "components.chunking", "httpx"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


if __name__ == "__main__":
    import uvicorn

    _configure_logging()
    uvicorn.run(create_app(), host=str(config.server.host), port=int(config.server.port), proxy_headers=False,
                log_config=None, access_log=False)
