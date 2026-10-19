"""Knowledge indexer: chunk -> embed -> replace a document's chunks.

A reconcile loop (not an event consumer) re-indexes any active document whose
`checksum` or embedding model differs from what was last indexed, so missed
events, crashes, backfills and model upgrades all self-heal."""
from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Callable, Iterable, Optional

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from zuvo_common.db import tenant_session

from components.chunker_component import SemanticChunker
from components.gemini import HashingEmbedder, classify, validate_vectors
from components.metrics import REGISTRY
from services.store import PendingDoc, PgVectorStore

logger = logging.getLogger(__name__)
_CTX = re.compile(r"^\[Context: (.*?)\]\n\n", re.S)


def _no_llm(prompt: str, max_new_tokens=None, temperature=None) -> str:
    return ""  # forces the deterministic profile + recursive splitter path


def build_chunker(llm_generator: Optional[Callable] = None) -> SemanticChunker:
    return SemanticChunker(HashingEmbedder(256), llm_generator or _no_llm, llm_tokenizer=None)


def split_chunk(prefixed: str, metadata: dict) -> tuple[Optional[str], str, str]:
    """-> (section, display_text, embed_text). The upstream chunker prefixes the
    heading path; it is embedded but stored separately from the text."""
    m = _CTX.match(prefixed)
    body = prefixed[m.end():] if m else prefixed
    section = metadata.get("section") or (m.group(1) if m else None)
    return section, body.strip(), prefixed


class Indexer:
    def __init__(self, sm: async_sessionmaker[AsyncSession], store: PgVectorStore, embedder, chunker: SemanticChunker,
                 tenants: Callable[[], Iterable[str]], max_docs_per_tick: int = 20):
        self.sm, self.store, self.embedder, self.chunker = sm, store, embedder, chunker
        self.tenants, self.max_docs = tenants, max_docs_per_tick

    async def chunk(self, doc: PendingDoc) -> list[tuple[Optional[str], str, str]]:
        records = await asyncio.to_thread(self.chunker.chunk_text, doc.body, doc.source_path)
        out = [split_chunk(r.text, r.metadata) for r in records]
        return [c for c in out if c[1]]

    async def index_document(self, tenant_id: str, doc: PendingDoc) -> int:
        """Embeds a claimed document and swaps its chunks atomically. Returns chunks written, or -1 when the document
        changed meanwhile (claim released, picked up again next tick). Raises on failure (caller marks it failed)."""
        chunks = await self.chunk(doc)
        try:
            vectors = await self.embedder.embed_documents([c[2] for c in chunks])
        except Exception as exc:
            REGISTRY.inc("zuvo_rag_embed_failures_total", operation="document", category=classify(exc))
            raise
        validate_vectors(vectors, len(chunks), self.dimensions)
        async with tenant_session(self.sm, tenant_id) as s:
            if not await self.store.lock_if_current(s, doc.id, doc.checksum):
                await self.store.release(s, doc.id)
                logger.info("document changed while indexing, will retry: %s", doc.source_path)
                return -1
            n = await self.store.replace_chunks(s, tenant_id, doc.id, doc.checksum, self.embedder.model_id,
                                                [(c[0], c[1]) for c in chunks], vectors)
        logger.info("indexed tenant=%s doc=%s chunks=%d model=%s", tenant_id, doc.source_path, n, self.embedder.model_id)
        return n

    @property
    def dimensions(self) -> int:
        return int(getattr(self.embedder, "dimensions"))

    async def process(self, tenant_id: str, doc: PendingDoc) -> str:
        """claim -> index -> indexed | failed | superseded | skipped (claimed elsewhere)."""
        async with tenant_session(self.sm, tenant_id) as s:
            if not await self.store.claim(s, doc.id, doc.checksum):
                return "skipped"
        t0 = time.monotonic()
        try:
            n = await self.index_document(tenant_id, doc)
        except Exception as exc:  # noqa: BLE001 - one bad document must not block the rest
            category = classify(exc)
            logger.warning("indexing failed tenant=%s doc=%s category=%s", tenant_id, doc.source_path, category)
            REGISTRY.inc("zuvo_rag_index_total", outcome="failed")
            REGISTRY.inc("zuvo_rag_index_errors_total", category=category)
            async with tenant_session(self.sm, tenant_id) as s:
                await self.store.mark_error(s, doc.id, f"{category}: {exc.__class__.__name__}: {exc}")
            return "failed"
        outcome = "superseded" if n < 0 else "indexed"
        REGISTRY.inc("zuvo_rag_index_total", outcome=outcome)
        if n > 0:
            REGISTRY.inc("zuvo_rag_index_chunks_total", n)
        REGISTRY.observe("zuvo_rag_index_seconds", time.monotonic() - t0)
        return outcome

    async def reconcile_tenant(self, tenant_id: str) -> int:
        async with tenant_session(self.sm, tenant_id) as s:
            pending = await self.store.pending(s, self.embedder.model_id, self.max_docs)
        done = 0
        for doc in pending:
            if await self.process(tenant_id, doc) == "indexed":
                done += 1
        return done

    async def tick(self) -> int:
        total = 0
        for tid in list(self.tenants()):
            try:
                total += await self.reconcile_tenant(tid)
            except Exception:  # noqa: BLE001
                logger.exception("reconcile failed tenant=%s", tid)
        return total

    async def run_forever(self, poll_seconds: float) -> None:
        while True:
            await self.tick()
            await asyncio.sleep(poll_seconds)
