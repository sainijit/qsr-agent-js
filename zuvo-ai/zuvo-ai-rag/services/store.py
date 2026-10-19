"""Postgres/pgvector store over kb_documents / kb_chunks.

Every call takes a session opened with `zuvo_common.db.tenant_session`, so RLS
restricts rows to that tenant; no query here filters on tenant_id by hand."""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from zuvo_common.pricing import STOPWORDS

_TOKEN = re.compile(r"[a-z0-9]+")
_CHUNK_NS = uuid.UUID("0b6f7f0e-31c2-4a57-9b7e-6a2d6c1f9e11")
STALE_PROCESSING_S = 600            # a 'processing' claim older than this is from a crashed indexer
RETRY_BASE_S, RETRY_CAP_S = 300, 21600  # failed documents: 5 min, 10, 20 ... capped at 6 h
# effective state: a writer that changed the document without resetting index_state still shows as pending
_STATE_SQL = ("CASE WHEN index_state = 'indexed' AND (indexed_checksum IS DISTINCT FROM checksum "
              "OR indexed_model IS DISTINCT FROM :m) THEN 'pending' ELSE index_state END")


def chunk_id(doc_id: str, checksum: str, model_id: str, ordinal: int) -> str:
    """Deterministic: re-indexing the same content with the same model yields the same chunk ids."""
    return str(uuid.uuid5(_CHUNK_NS, f"{doc_id}:{checksum}:{model_id}:{ordinal}"))


def vector_literal(v: list[float]) -> str:
    return "[" + ",".join(f"{x:.7f}" for x in v) + "]"


def ts_query(question: str, max_terms: int = 12) -> str:
    """OR-query of content words for to_tsquery('english', ...); '' when none."""
    seen: list[str] = []
    for w in _TOKEN.findall(question.lower()):
        if len(w) > 1 and w not in STOPWORDS and w not in seen:
            seen.append(w)
    return " | ".join(seen[:max_terms])


@dataclass
class PendingDoc:
    id: str
    title: str
    source_path: str
    checksum: str
    body: str


@dataclass
class Hit:
    chunk_id: str
    document_id: str
    title: str
    source_path: str
    section: Optional[str]
    text: str
    ordinal: int
    similarity: float
    rrf: float
    vector_rank: Optional[int] = None
    text_rank: Optional[int] = None
    rerank: Optional[float] = None
    document_version: Optional[int] = None
    extra: dict = field(default_factory=dict)


class PgVectorStore:
    async def pending(self, s: AsyncSession, model_id: str, limit: int, error_backoff_seconds: Optional[int] = None) -> list[PendingDoc]:
        """Active documents whose indexed content/model is stale, skipping fresh 'processing' claims and failed
        documents still in backoff. `error_backoff_seconds=0` ignores the backoff (explicit ingest)."""
        backoff = ("" if error_backoff_seconds == 0 else
                   """AND (index_state <> 'failed' OR index_error_at IS NULL OR index_error_at < updated_at
                           OR index_error_at < now() - make_interval(secs => LEAST(:cap, :base * power(2, GREATEST(index_attempts - 1, 0)))))""")
        rows = (await s.execute(text(
            f"""SELECT id::text, title, source_path, checksum, body FROM kb_documents
               WHERE is_active AND body IS NOT NULL
                 AND (indexed_checksum IS DISTINCT FROM checksum OR indexed_model IS DISTINCT FROM :m)
                 AND (index_state <> 'processing' OR index_started_at < now() - make_interval(secs => :stale))
                 {backoff}
               ORDER BY updated_at LIMIT :n"""),
            {"m": model_id, "n": limit, "stale": STALE_PROCESSING_S, "base": error_backoff_seconds or RETRY_BASE_S,
             "cap": RETRY_CAP_S})).all()
        return [PendingDoc(*r) for r in rows]

    async def claim(self, s: AsyncSession, doc_id: str, checksum: str) -> bool:
        """pending|failed|stale-processing -> processing (own short transaction, before any API call)."""
        res = await s.execute(text(
            """UPDATE kb_documents SET index_state = 'processing', index_started_at = now(), index_attempts = index_attempts + 1
               WHERE id = :d AND is_active AND checksum = :c
                 AND (index_state <> 'processing' OR index_started_at < now() - make_interval(secs => :stale))"""),
            {"d": doc_id, "c": checksum, "stale": STALE_PROCESSING_S})
        return bool(res.rowcount)

    async def lock_if_current(self, s: AsyncSession, doc_id: str, checksum: str) -> bool:
        """Row-locks the document; False if it changed since it was chunked."""
        cur = await s.scalar(text("SELECT checksum FROM kb_documents WHERE id = :d AND is_active FOR UPDATE"), {"d": doc_id})
        return cur == checksum

    async def replace_chunks(self, s: AsyncSession, tenant_id: str, doc_id: str, checksum: str, model_id: str,
                             chunks: list[tuple[Optional[str], str]], vectors: list[list[float]]) -> int:
        """Swap the document's embedded chunks for the new version in the caller's transaction: readers see either
        the complete previous version or the complete new one. Lexical chunks (embed_model NULL) belong to bundle sync."""
        await s.execute(text("DELETE FROM kb_chunks WHERE document_id = :d AND embed_model IS NOT NULL"), {"d": doc_id})
        for i, ((section, body), vec) in enumerate(zip(chunks, vectors)):
            await s.execute(text(
                """INSERT INTO kb_chunks (id, tenant_id, document_id, ordinal, text, section, char_count, embedding, embed_model)
                   VALUES (:id, :t, :d, :o, :x, :sec, :n, CAST(:v AS vector), :m)"""),
                {"id": chunk_id(doc_id, checksum, model_id, i), "t": tenant_id, "d": doc_id, "o": i, "x": body, "sec": section,
                 "n": len(body), "v": vector_literal(vec), "m": model_id})
        await s.execute(text(
            """UPDATE kb_documents SET indexed_checksum = :c, indexed_model = :m, indexed_at = now(), indexed_version = version,
                      index_state = 'indexed', index_attempts = 0, chunk_count = :n, index_error = NULL, index_error_at = NULL
               WHERE id = :d"""), {"c": checksum, "m": model_id, "d": doc_id, "n": len(chunks)})
        return len(chunks)

    async def mark_error(self, s: AsyncSession, doc_id: str, error: str) -> None:
        await s.execute(text(
            "UPDATE kb_documents SET index_state = 'failed', index_error = :e, index_error_at = now() WHERE id = :d"),
            {"e": error[:500], "d": doc_id})

    async def release(self, s: AsyncSession, doc_id: str) -> None:
        """Give a claim back without counting it as a failure (document changed while it was being embedded)."""
        await s.execute(text(
            "UPDATE kb_documents SET index_state = 'pending', index_attempts = GREATEST(index_attempts - 1, 0) "
            "WHERE id = :d AND index_state = 'processing'"), {"d": doc_id})

    async def embedding_dimension(self, s: AsyncSession) -> Optional[int]:
        """Declared dimension of kb_chunks.embedding (vector(N)); None if unknown."""
        typmod = await s.scalar(text(
            "SELECT atttypmod FROM pg_attribute WHERE attrelid = 'kb_chunks'::regclass AND attname = 'embedding'"))
        return int(typmod) if typmod and typmod > 0 else None

    async def upsert_document(self, s: AsyncSession, tenant_id: str, source_path: str, title: str, body: str,
                              checksum: str) -> tuple[str, bool]:
        """Returns (document_id, changed). Tenant-scoped by (tenant_id, source_path)."""
        row = (await s.execute(text(
            "SELECT id::text, checksum, is_active, body IS NOT NULL FROM kb_documents WHERE source_path = :p"),
            {"p": source_path})).first()
        if row and row[1] == checksum and row[2] and row[3]:
            return row[0], False
        if row:
            await s.execute(text(
                """UPDATE kb_documents SET title = :ti, body = :b, checksum = :c, is_active = true,
                          version = version + 1, updated_at = now(), index_error = NULL, index_error_at = NULL,
                          indexed_checksum = NULL, index_state = 'pending', index_attempts = 0 WHERE id = :d"""),
                {"ti": title, "b": body, "c": checksum, "d": row[0]})
            return row[0], True
        doc_id = str(uuid.uuid4())
        await s.execute(text(
            """INSERT INTO kb_documents (id, tenant_id, title, source_path, version, checksum, is_active, body)
               VALUES (:d, :t, :ti, :p, 1, :c, true, :b)"""),
            {"d": doc_id, "t": tenant_id, "ti": title, "p": source_path, "c": checksum, "b": body})
        return doc_id, True

    async def deactivate(self, s: AsyncSession, doc_id: str) -> bool:
        res = await s.execute(text(
            """UPDATE kb_documents SET is_active = false, updated_at = now()
               WHERE id = :d AND source_path NOT LIKE 'knowledge/%'"""), {"d": doc_id})
        if not res.rowcount:
            return False  # unknown, or a bundle document (managed by tenants/<slug>/knowledge)
        await s.execute(text("DELETE FROM kb_chunks WHERE document_id = :d"), {"d": doc_id})
        return True

    async def force_reindex(self, s: AsyncSession) -> int:
        res = await s.execute(text(
            "UPDATE kb_documents SET indexed_checksum = NULL, index_error = NULL, index_error_at = NULL, index_state = 'pending', "
            "index_attempts = 0 WHERE is_active"))
        return res.rowcount or 0

    async def search(self, s: AsyncSession, qvec: list[float], question: str, model_id: str,
                     fetch_k: int, rrf_k: int) -> list[Hit]:
        tsq = ts_query(question)
        # keep scanning the HNSW graph until enough rows survive the RLS/active filters (pgvector >= 0.8)
        await s.execute(text("SET LOCAL hnsw.iterative_scan = strict_order"))
        text_arm = (
            """, fts AS (
                 SELECT c.id, row_number() OVER (ORDER BY ts_rank_cd(c.tsv, query) DESC) AS r
                 FROM kb_chunks c JOIN kb_documents d ON d.id = c.document_id, to_tsquery('english', :tsq) query
                 WHERE d.is_active AND c.embed_model = :m AND c.tsv @@ query
                 ORDER BY ts_rank_cd(c.tsv, query) DESC LIMIT :k)"""
            if tsq else ", fts AS (SELECT NULL::uuid AS id, NULL::bigint AS r WHERE false)")
        sql = f"""
            WITH vec AS (
                 SELECT c.id, row_number() OVER (ORDER BY c.embedding <=> CAST(:v AS vector)) AS r
                 FROM kb_chunks c JOIN kb_documents d ON d.id = c.document_id
                 WHERE d.is_active AND c.embed_model = :m AND c.embedding IS NOT NULL
                 ORDER BY c.embedding <=> CAST(:v AS vector) LIMIT :k){text_arm},
            fused AS (
                 SELECT id, sum(1.0 / (:rrf + r)) AS rrf FROM (SELECT id, r FROM vec UNION ALL SELECT id, r FROM fts) u
                 GROUP BY id)
            SELECT c.id::text, c.document_id::text, d.title, d.source_path, c.section, c.text, c.ordinal,
                   1 - (c.embedding <=> CAST(:v AS vector)) AS similarity, f.rrf,
                   (SELECT r FROM vec WHERE vec.id = c.id), (SELECT r FROM fts WHERE fts.id = c.id), d.indexed_version
            FROM fused f JOIN kb_chunks c ON c.id = f.id JOIN kb_documents d ON d.id = c.document_id
            ORDER BY f.rrf DESC, similarity DESC LIMIT :k"""
        params = {"v": vector_literal(qvec), "m": model_id, "k": fetch_k, "rrf": rrf_k}
        if tsq:
            params["tsq"] = tsq
        rows = (await s.execute(text(sql), params)).all()
        return [Hit(r[0], r[1], r[2], r[3], r[4], r[5], r[6], float(r[7]), float(r[8]), r[9], r[10],
                    document_version=r[11]) for r in rows]

    async def stats(self, s: AsyncSession, model_id: str) -> dict:
        r = (await s.execute(text(
            f"""SELECT count(*) FILTER (WHERE is_active),
                      count(*) FILTER (WHERE is_active AND indexed_checksum = checksum AND indexed_model = :m),
                      count(*) FILTER (WHERE is_active AND index_error IS NOT NULL),
                      max(indexed_at),
                      count(*) FILTER (WHERE is_active AND {_STATE_SQL} = 'pending'),
                      count(*) FILTER (WHERE is_active AND index_state = 'processing'),
                      count(*) FILTER (WHERE is_active AND index_state = 'failed')
               FROM kb_documents"""), {"m": model_id})).first()
        chunks = await s.scalar(text(
            """SELECT count(*) FROM kb_chunks c JOIN kb_documents d ON d.id = c.document_id
               WHERE d.is_active AND c.embed_model = :m"""), {"m": model_id})
        return {"documents": r[0], "indexed_documents": r[1], "documents_with_errors": r[2],
                "last_indexed_at": r[3].isoformat() if r[3] else None, "chunks": chunks, "embed_model": model_id,
                "states": {"pending": r[4], "processing": r[5], "failed": r[6]}}

    async def documents(self, s: AsyncSession, model_id: str = "") -> list[dict]:
        rows = (await s.execute(text(
            f"""SELECT id::text, title, source_path, version, is_active, checksum = indexed_checksum, indexed_model,
                      indexed_at, index_error, {_STATE_SQL}, index_attempts, index_started_at, indexed_version, chunk_count,
                      index_error_at
               FROM kb_documents ORDER BY source_path"""), {"m": model_id})).all()
        iso = lambda t: t.isoformat() if t else None  # noqa: E731
        return [{"document_id": r[0], "title": r[1], "source_path": r[2], "version": r[3], "is_active": r[4],
                 "indexed": bool(r[5]), "indexed_model": r[6], "indexed_at": iso(r[7]), "index_error": r[8],
                 "index_state": r[9] if r[4] else "inactive", "index_attempts": r[10], "index_started_at": iso(r[11]),
                 "indexed_version": r[12], "chunk_count": r[13], "index_error_at": iso(r[14])} for r in rows]
