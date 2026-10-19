"""HTTP API (v1). Internal only: every route except health requires
`Authorization: Bearer $RAG_SERVICE_TOKEN`, and the tenant in the path must be
a known tenant. Data isolation itself is enforced by Postgres RLS."""
from __future__ import annotations

import hashlib
import hmac
import io
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

from zuvo_common.db import db_ping, tenant_session

from components.metrics import REGISTRY
from services.retrieval_service import RetrievalError
from services.types import RetrievalRecord

router = APIRouter()
_ALLOWED_SUFFIXES = {".txt", ".md", ".docx", ".pdf"}
_MAX_UPLOAD_BYTES = 10 * 1024 * 1024


class RetrieveRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    top_k: Optional[int] = Field(None, ge=1, le=10)
    min_similarity: Optional[float] = Field(None, ge=0, le=1)


class AnswerRequest(RetrieveRequest):
    history: list[tuple[str, str]] = Field(default_factory=list, max_length=10)


class DocumentRequest(BaseModel):
    source_path: str = Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9._/\- ]+$")
    title: Optional[str] = Field(None, max_length=200)
    text: str = Field(min_length=1)


def app_state(request: Request):
    return request.app.state.rag


def require_token(request: Request, authorization: Optional[str] = Header(None)) -> None:
    expected = request.app.state.rag.service_token
    if not expected:
        raise HTTPException(503, "RAG_SERVICE_TOKEN not configured")
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(token.encode(), expected.encode()):
        raise HTTPException(401, "invalid service token")


def tenant(tenant_id: str, request: Request) -> str:
    try:
        tid = str(uuid.UUID(tenant_id))
    except ValueError:
        raise HTTPException(404, "unknown tenant") from None
    if tid not in set(request.app.state.rag.tenants()):
        raise HTTPException(404, "unknown tenant")
    return tid


auth = [Depends(require_token)]


@router.get("/healthz")
async def healthz():
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(rag=Depends(app_state)):
    ok_db = await db_ping(rag.sm)
    schema_error = getattr(rag, "schema_error", None)
    ready = ok_db and rag.embedder is not None and not schema_error
    if not ready:
        raise HTTPException(503, {"db": ok_db, "embedder": rag.embedder is not None, "schema": schema_error or "ok"})
    return {"status": "ready", "embed_model": rag.embedder.model_id}


@router.get("/metrics")
async def metrics():
    """Process-local Prometheus counters; no tenant labels. Internal network only (not published by compose)."""
    return PlainTextResponse(REGISTRY.render(), media_type="text/plain; version=0.0.4")


def _unavailable(exc: RetrievalError) -> JSONResponse:
    # 503 + category lets callers distinguish "no knowledge" (200, below_threshold) from "service failed"
    return JSONResponse({"detail": "retrieval unavailable", "error": exc.category, "cause": exc.cause}, status_code=503)


@router.post("/v1/tenants/{tenant_id}/retrieve", dependencies=auth)
async def retrieve(body: RetrieveRequest, tid: str = Depends(tenant), rag=Depends(app_state)):
    try:
        res = await rag.retriever.retrieve(tid, body.query, body.top_k, body.min_similarity)
    except RetrievalError as exc:
        return _unavailable(exc)
    return res.to_dict()


@router.post("/v1/tenants/{tenant_id}/answer", dependencies=auth)
async def answer(body: AnswerRequest, tid: str = Depends(tenant), rag=Depends(app_state)):
    """Playground ("Test my knowledge base"). Customer replies go through agent-worker."""
    if rag.text is None:
        raise HTTPException(503, "answer model not configured")
    try:
        res = await rag.retriever.retrieve(tid, body.query, body.top_k, body.min_similarity)
    except RetrievalError as exc:
        return _unavailable(exc)
    if res.below_threshold:
        return {"answer": None, "reason": "no_relevant_knowledge", "retrieval": res.to_dict()}
    records = [RetrievalRecord(c["title"], c["text"], c["similarity"], {}) for c in res.chunks]
    prompt = rag.prompt_builder.build(body.query, records, history=[(r, t) for r, t in body.history])
    text = await rag.text.generate(prompt, max_tokens=rag.answer_max_tokens, temperature=0.2)
    return {"answer": text, "model": rag.text.model, "retrieval": res.to_dict()}


async def _ingest(rag, tid: str, source_path: str, title: Optional[str], text: str) -> dict:
    text = text.replace("\x00", "").strip()
    if not text:
        raise HTTPException(422, "document is empty")
    if len(text) > rag.max_ingest_chars:
        raise HTTPException(413, f"document too large: {len(text)} chars (limit {rag.max_ingest_chars})")
    if source_path.startswith("knowledge/"):
        raise HTTPException(422, "knowledge/ paths are managed by tenant bundles; use uploads/")
    if not source_path.startswith("uploads/"):
        source_path = "uploads/" + source_path.lstrip("/")
    if ".." in source_path.split("/"):
        raise HTTPException(422, "invalid source_path")
    title = title or next((l.lstrip("# ").strip() for l in text.splitlines() if l.strip()), Path(source_path).stem)[:200]
    checksum = hashlib.sha256(text.encode()).hexdigest()
    async with tenant_session(rag.sm, tid) as s:
        doc_id, changed = await rag.store.upsert_document(s, tid, source_path, title, text, checksum)
    chunks, state = 0, None
    if changed and rag.embedder is not None and rag.indexer is not None and not getattr(rag, "schema_error", None):
        async with tenant_session(rag.sm, tid) as s:
            pending = [d for d in await rag.store.pending(s, rag.embedder.model_id, 1000, 0) if d.id == doc_id]
        if pending:
            # a failure leaves the document 'failed' (retried by the reconcile loop with backoff); it is not an HTTP error
            state = await rag.indexer.process(tid, pending[0])
    async with tenant_session(rag.sm, tid) as s:
        doc = next((d for d in await rag.store.documents(s, rag.embedder.model_id if rag.embedder else "")
                    if d["document_id"] == doc_id), {})
    if state == "indexed":
        chunks = doc.get("chunk_count") or 0
    return {"document_id": doc_id, "source_path": source_path, "changed": changed, "chunks": chunks,
            "index_state": doc.get("index_state"), "index_error": doc.get("index_error")}


@router.post("/v1/tenants/{tenant_id}/documents", dependencies=auth)
async def add_document(body: DocumentRequest, tid: str = Depends(tenant), rag=Depends(app_state)):
    return await _ingest(rag, tid, body.source_path, body.title, body.text)


def extract_text(filename: str, raw: bytes) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix not in _ALLOWED_SUFFIXES:
        raise HTTPException(415, "Only .txt, .md, .docx and .pdf files are supported")
    if suffix in {".txt", ".md"}:
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise HTTPException(422, "file must be UTF-8 encoded") from exc
    if suffix == ".docx":
        from docx import Document as DocxDocument

        try:
            return "\n".join(p.text for p in DocxDocument(io.BytesIO(raw)).paragraphs)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(422, "unable to parse .docx file") from exc
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(raw))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(422, "unable to parse .pdf file") from exc
    if getattr(reader, "is_encrypted", False):
        try:
            reader.decrypt("")
        except Exception:  # noqa: BLE001
            pass
        if getattr(reader, "is_encrypted", False):
            raise HTTPException(422, "encrypted PDFs are not supported")
    return "\n".join(page.extract_text() or "" for page in reader.pages)


@router.post("/v1/tenants/{tenant_id}/documents/file", dependencies=auth)
async def add_document_file(file: UploadFile = File(...), title: Optional[str] = Form(None),
                            tid: str = Depends(tenant), rag=Depends(app_state)):
    raw = await file.read(_MAX_UPLOAD_BYTES + 1)
    if len(raw) > _MAX_UPLOAD_BYTES:
        raise HTTPException(413, "file too large (max 10 MB)")
    name = Path(file.filename or "upload.txt").name
    text = extract_text(name, raw)
    return await _ingest(rag, tid, f"uploads/{name}", title, text)


@router.get("/v1/tenants/{tenant_id}/documents", dependencies=auth)
async def list_documents(tid: str = Depends(tenant), rag=Depends(app_state)):
    async with tenant_session(rag.sm, tid) as s:
        return {"documents": await rag.store.documents(s, rag.embedder.model_id if rag.embedder else "")}


@router.delete("/v1/tenants/{tenant_id}/documents/{document_id}", dependencies=auth)
async def delete_document(document_id: uuid.UUID, tid: str = Depends(tenant), rag=Depends(app_state)):
    async with tenant_session(rag.sm, tid) as s:
        if not await rag.store.deactivate(s, str(document_id)):
            raise HTTPException(404, "document not found (bundle documents are managed in tenants/<slug>/knowledge)")
    return {"document_id": str(document_id), "deleted": True}


@router.post("/v1/tenants/{tenant_id}/reindex", dependencies=auth)
async def reindex(background: BackgroundTasks, tid: str = Depends(tenant), rag=Depends(app_state)):
    async with tenant_session(rag.sm, tid) as s:
        n = await rag.store.force_reindex(s)
    if rag.indexer is not None and not getattr(rag, "schema_error", None):
        background.add_task(rag.indexer.reconcile_tenant, tid)
    return {"documents": n, "status": "queued"}


@router.get("/v1/tenants/{tenant_id}/stats", dependencies=auth)
async def stats(tid: str = Depends(tenant), rag=Depends(app_state)):
    async with tenant_session(rag.sm, tid) as s:
        return await rag.store.stats(s, rag.embedder.model_id if rag.embedder else "")
