# RAG and AI execution reliability (Phase 8)

Scope: `zuvo-ai-rag` (indexer, store, retrieval, Gemini adapters), the agent-worker FAQ path, and `agent_runs`.
The RAG stack was improved in place, not replaced. Migration `0006_rag_ai_reliability` is additive.

## 1. Document index state machine

`kb_documents.index_state` (CHECK constraint):

```
            upsert / bundle sync / force_reindex / model change
                         │
                         ▼
   ┌──────────────► pending ──claim──► processing ──swap ok──► indexed
   │                   ▲                  │   │
   │   changed while   │   release        │   └─ embed/validate error ──► failed
   │   embedding ──────┘ (attempt refunded)│                                │
   │                                       └─ stale >10 min: re-claimable   │
   └──────────────── backoff elapsed (5 min · 2^(attempts-1), cap 6 h) ◄───┘
```

| Column | Meaning |
|---|---|
| `index_state` | `pending`, `processing`, `indexed`, `failed` (API reports `inactive` for deactivated docs) |
| `index_attempts` | consecutive attempts since the last success. Drives backoff and resets to 0 on success or new content |
| `index_started_at` | when the current claim was taken. A claim older than 10 min is treated as abandoned |
| `indexed_version` | `version` whose chunks are currently served |
| `chunk_count` | embedded chunks in the served version |
| `index_error` | `<category>: <ExceptionClass>: <message>` (≤500 chars). Cleared on success or new content |
| `indexed_checksum`, `indexed_model`, `indexed_at`, `index_error_at` | as before |

**Claim:** `Indexer.process` runs `pending → processing` in its own short transaction before any API call.
A second indexer replica, or the synchronous upload path, cannot take a fresh claim.

**Atomic version switch:**
- The new version is chunked, embedded and validated entirely outside the database.
- `replace_chunks` then runs one transaction that row-locks the document, deletes the old *embedded* chunks, inserts the new ones and sets `indexed_*`/`index_state='indexed'`.
- Readers see either the complete previous version or the complete new one. A partially indexed version is never served.

**Failure:** the document goes to `failed`, but the previous version's chunks stay and keep serving.
This is verified by the golden test `test_failed_reindex_keeps_previous_version_serving`.

**Superseded:** if the content changes while it is being embedded, `lock_if_current` detects the checksum change.
The claim is released back to `pending` and the attempt is refunded. Nothing is written.

**Chunk ownership:**
- `embed_model IS NULL`: lexical chunks written by bundle sync. These are the fallback when RAG is unavailable.
- `embed_model IS NOT NULL`: embedded chunks owned by `zuvo-ai-rag`.

Bundle sync now replaces only lexical chunks, so a bundle edit no longer wipes the served embeddings before re-indexing.
The agent's lexical fallback prefers lexical chunks per document and uses embedded chunks only for uploads, which have no lexical copy.

**Deterministic chunk IDs:** `uuid5(namespace, "{doc_id}:{checksum}:{model_id}:{ordinal}")`.
Re-indexing identical content with the same model yields identical IDs, so agent-run references stay stable across forced re-indexes.

**Deletion:** `deactivate` deletes the chunks of uploads in the same transaction.
Removed bundle docs become `is_active=false`, and search filters on `d.is_active`.

## 2. Embedding consistency

- `kb_chunks.embedding` is `vector(768)`. The embedder is `gemini-embedding-001` with `output_dimensionality=768`, L2-normalised.
- **Startup guard:** `check_schema` compares `atttypmod` of `kb_chunks.embedding` with `embedder.dimensions`.
  - On mismatch, `/readyz` returns 503 with `schema: embedding_dimension_mismatch: db=… embedder=…`.
  - The background indexer stays off, and sync ingest and reindex skip indexing. This avoids marking every document failed.
- **Per-response validation** (`validate_vectors`): checks count, dimension, finite values and non-zero norm. A violation is `invalid_embedding` and is never retried.
- **Model change:** `indexed_model` is `model@dim`. Changing either makes every document pending, and search filters `c.embed_model = <current>`.
  - This is NOT VERIFIED for a dimension change: that needs a migration that alters `vector(N)`, and the guard above stops the service until it runs.

## 3. Gemini failure handling

`components/gemini.py`:

| Category (`classify`) | Source | Retried |
|---|---|---|
| `timeout` | `asyncio.TimeoutError` (per-call `timeout_seconds`) | yes |
| `rate_limited` | HTTP 429 | yes |
| `server_error` | HTTP ≥ 500 | yes |
| `network` | `httpx.TransportError` / `OSError` | yes |
| `client_error` | other 4xx | no |
| `invalid_embedding` | validation failure | no |
| `internal` | anything else | no |

- Retries are bounded by `gemini.max_attempts` and use full-jitter exponential backoff (`uniform(0, min(8 s, 1 s·2^(n-1)))`).
- After the indexer's in-call retries, document-level retries come from the reconcile loop with the state-machine backoff, so a quota outage cannot hammer the API.

## 4. Retrieval

- **Hybrid search:** `pgvector` cosine (top `fetch_k`) and Postgres FTS (top `fetch_k`), fused by RRF (`Σ 1/(rrf_k + rank)`), with an optional Gemini rerank and then the cosine floor (`min_similarity`).
- **Tenant isolation is RLS** on `kb_chunks`/`kb_documents` for both arms. It is covered by `test_rls.py::test_rag_search_never_returns_other_tenant` and the golden `test_no_cross_tenant_retrieval`.
- **Response:** each chunk now carries `document_version`.
- **Failures:** HTTP **503** `{"error": "embedding_unavailable" | "store_unavailable", "cause": <category>}`.
  - This is distinct from "no knowledge", which is 200 with `below_threshold: true`.
  - Query text is never included.
- **`rag_client.RagUnavailable.category`:** `timeout`, `network`, `unauthorized`, `unknown_tenant`, the 503 `error`, or `http_<status>`.
- **Fallback:** the agent falls back to lexical retrieval and records the category.

## 5. Agent-run diagnostics

`agent_runs.outcome` is the primary reason the run did or did not yield a confident, grounded reply. It is evaluated in this order:

| Outcome | Meaning |
|---|---|
| `skipped` | decision SKIPPED (mode off, opted out, line not routable) |
| `rag_unavailable` | RAG failed; lexical fallback used |
| `no_knowledge` | nothing relevant above the floor; holding reply |
| `ai_budget_exhausted` | monthly AI budget reached; deterministic text |
| `draft_rejected` | LLM draft failed the number/fact guard; deterministic text |
| `draft_failed` | LLM draft call errored; deterministic text |
| `low_confidence` | extraction flagged or confidence below the task threshold |
| `ok` | grounded and confident (it may still need approval because of `mode_draft` or `learning_gate`; see `decision_reasons`) |

`agent_runs.diagnostics` (JSONB) contains only IDs, versions, scores, models and latencies:

```json
{"extraction": {"tier": "tier2", "model": "gemini-…", "latency_ms": 1343, "input_tokens": 591, "output_tokens": 91},
 "retrieval":  {"strategy": "hybrid_rrf", "embed_model": "gemini-embedding-001@768", "top_similarity": 0.69,
                "below_threshold": false, "latency_ms": 40, "query_cache_hit": false, "rag_error": "timeout",
                "documents": [{"document_id": "…", "document_version": 1, "chunk_id": "…", "similarity": 0.69}]},
 "draft":      {"model": "gemini-…", "latency_ms": 990, "tokens_in": 846, "tokens_out": 33, "error": "TimeoutError"}}
```

- It never contains prompts, customer text, chunk text or model reasoning.
- Existing columns are unchanged: intent, confidence, `decision`, `decision_reasons`, `model`, tokens, `cost_inr`, `latency_ms`, `retrieved`.
- `retrieved.rejected_draft` keeps its existing behaviour (the guarded draft is shown to the approver).

## 6. Metrics

**`zuvo-ai-rag` `GET /metrics`** (in-process, Prometheus text). It is unauthenticated, on the internal network only, and not published by compose. Labels are enums only, with no tenant labels.

| Metric | Labels |
|---|---|
| `zuvo_rag_retrieve_total` | `outcome` = hit, below_threshold, empty, error |
| `zuvo_rag_retrieve_errors_total` | `category` |
| `zuvo_rag_retrieve_seconds` (histogram) | – |
| `zuvo_rag_embed_failures_total` | `operation` = query/document, `category` |
| `zuvo_rag_index_total` | `outcome` = indexed, failed, superseded |
| `zuvo_rag_index_errors_total` | `category` |
| `zuvo_rag_index_seconds` (histogram), `zuvo_rag_index_chunks_total` | – |

Counters reset on restart (process-local).

**`opsmetrics` (database-derived):**
- `zuvo_rag_documents{state=indexed|pending|processing|failed}`
- `zuvo_rag_processing_stuck`
- `zuvo_rag_failed_max_attempts`
- `zuvo_agent_outcomes_24h{outcome}`

Runs created before 0006 report `outcome="unrecorded"`.

## 7. Tests (no paid Gemini calls)

| Suite | Covers |
|---|---|
| `zuvo-ai-rag/tests/test_reliability.py` (29) | vector validation, classification, bounded/jittered retries, non-retry of permanent errors, deterministic chunk IDs, retriever error typing and metrics, 503 contract, `/metrics`, dimension guard |
| `tests/unit/test_rag.py` (+13) | `RagUnavailable` categories, `run_outcome` table, diagnostics contain IDs/scores but no text, fallback diagnostics |
| `tests/integration/test_rag_golden.py` (13, real pgvector, deterministic hashing embedder) | 3 golden questions → expected doc and version; index metadata; no cross-tenant; missing knowledge → `no_knowledge`; weak match → `low_confidence`, not auto-sent; rate-limit and invalid-embedding failures keep the previous version serving, back off, then recover to v2; deletion; exclusive and stale claims; superseded update; identical chunk IDs on re-index and lexical chunks preserved |

Run them with:

```bash
cd zuvo-ai-rag && python -m pytest -q tests
python -m pytest -q tests/unit
PYTHON=… scripts/test_rls.sh tests/integration/test_rag_golden.py
```

## 8. Runbook

| Symptom | Check | Action |
|---|---|---|
| `zuvo_rag_documents{state="failed"} > 0` | `GET /v1/tenants/{id}/documents` → `index_error` prefix | `rate_limited`/`server_error`/`timeout`/`network`: wait, as backoff retries automatically. `invalid_embedding`/`client_error`: check model config and key. Then use `POST …/reindex` to retry immediately |
| `zuvo_rag_processing_stuck > 0` | `docker compose logs zuvo-ai-rag` | Restart `zuvo-ai-rag`; stale claims are re-taken after 10 min |
| `/readyz` 503 `embedding_dimension_mismatch` | config `gemini.embed_dimensions` vs DB | Revert the config, or migrate `vector(N)` and force re-index |
| `zuvo_agent_outcomes_24h{outcome="rag_unavailable"}` rising | `zuvo_rag_retrieve_errors_total` | Check Gemini quota/key and DB health; replies continue via lexical fallback |

## NOT VERIFIED

- Behaviour under real Gemini 429/5xx. It is simulated in unit and integration tests; no live quota exhaustion was induced.
- A live end-to-end `rag_unavailable` drill (RAG container stopped during an FAQ). The path is covered by unit tests only.
- An embedding-dimension change with an actual `vector(N)` migration.
- Multiple `zuvo-ai-rag` replicas. Claim exclusivity is tested against the database, but only one replica runs in compose.
