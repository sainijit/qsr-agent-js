# zuvo-ai-rag — Multi-tenant, Gemini-only RAG service (plan)

Status: **IMPLEMENTED (P0–P7)** — see §12 for what shipped and deviations.
Owner: ZuvoCloud platform. Last updated: 2026-10-19.

## 0. Provenance

`zuvo-ai/zuvo-ai-rag/` is a verbatim copy of
`voice-enabled-interactions/smart-kiosk-assistant/rag-service` at commit
`edff388` (excluding `models/`, `storage/`, `.cache/`, `.env`, `__pycache__`).
The upstream repo has no LICENSE file; confirm the licence/ownership before
this code is shipped commercially or published. Keep upstream file headers.

Upstream baseline: FastAPI :8020, Chroma, OpenVINO `bge-large-en-v1.5`
embeddings, OpenVINO `bge-reranker-base` cross-encoder, Qwen3-4B on OVMS
(answers + LLM chunk splitting), Google ADK ordering agent with kiosk plugins
and MCP tool discovery. ~12.7k lines of Python; OpenVINO/OVMS touches 17 files.

## 1. Goal

A **retrieval service** for ZuvoCloud that:

1. is **multi-tenant** (hard isolation per `tenant_id`, no cross-tenant reads,
   deletes or caches);
2. uses **Gemini only** — Gemini embeddings, optional Gemini rerank, Gemini
   for optional LLM chunk-splitting / playground answers. No OpenVINO, OVMS,
   torch, transformers, GPU or model downloads;
3. stores vectors in the **existing Postgres** (pgvector) under the same RLS
   as the rest of the platform;
4. is consumed by `agent-worker` as a drop-in replacement for lexical
   `rank_texts`, while **agent-worker keeps ownership of the reply**
   (Gemini drafter → number guard → `decide()` → approvals/learning).

Non-goals (this phase): voice, ordering/cart agent, ADK runtime, the
OpenAI-compatible `/v1/chat/completions` surface, kiosk guards.

## 2. Target architecture

```
tenants/<slug>/knowledge/*.md ──▶ migrator.sync_knowledge ──▶ kb_documents (checksum)
                                         │ Kafka: kb.document.changed {tenant_id, document_id, checksum}
                                         ▼
                                zuvo-ai-rag (indexer consumer)
                                 chunk (markdown/atomic/recursive)
                                 embed (Gemini, RETRIEVAL_DOCUMENT)
                                 upsert kb_chunks(+embedding, tsv)  [RLS: tenant]
Dashboard upload (pdf/docx) ──▶ dashboard-api ──▶ zuvo-ai-rag POST /documents/file

WhatsApp msg ─▶ agent-worker ─▶ POST zuvo-ai-rag /v1/tenants/{t}/retrieve
                    │              embed query (RETRIEVAL_QUERY, Redis-cached)
                    │              hybrid: pgvector cosine ∪ Postgres FTS → RRF
                    │              optional Gemini Flash-Lite rerank → threshold
                    │◀──────────── chunks + scores + strategy
                    └─▶ drafter (Gemini, FACTS only) → number guard → decide() → approvals
                        fallback: lexical rank_texts if RAG times out / 5xx
```

## 3. Key decisions

| # | Decision | Choice | Why |
|---|---|---|---|
| D1 | Service role | Retrieve-only primary API; optional `/answer` for the dashboard "Test my KB" playground | Keeps grounding, number guard, approvals and learning in one place (agent-worker) |
| D2 | Embeddings | Gemini `gemini-embedding-001` via `google-genai`, `task_type` RETRIEVAL_DOCUMENT / RETRIEVAL_QUERY, `output_dimensionality=768` | Multilingual (Hinglish), no local model; 768 via MRL keeps index small. Model + dim configurable; verify latest model name/pricing at build time |
| D3 | Vector store | **pgvector** in existing Postgres; extend `kb_chunks` | Reuses RLS tenant isolation, one backup, hybrid SQL in one query. Chroma removed |
| D4 | Rerank | Hybrid vector + FTS fused by RRF; optional Gemini Flash-Lite listwise rerank (`RAG_RERANK=none\|llm`) | Gemini API has no cross-encoder. Vertex AI Ranking API is a future option (needs GCP project) |
| D5 | Chunking | Keep pure-Python markdown/atomic/recursive/size splitters; LLM marker splitter → Gemini, **off by default** | Tenant KBs are small; avoid ingest-time LLM cost. HF tokenizer → char estimator (`≈4 chars/token`) |
| D6 | Tenancy | `tenant_id` in path + verified against service token; every query `SET app.tenant_id`; source key = `(tenant_id, source_path)`; per-tenant caches | Upstream is a process-wide singleton with one collection |
| D7 | Auth | Internal-only network, no published port; `Authorization: Bearer $RAG_SERVICE_TOKEN` | Upstream has no auth |
| D8 | Index trigger | Kafka `kb.document.changed` consumed by zuvo-ai-rag; idempotent on `checksum` + `embed_model` | Fits existing event-driven stack; re-index on model change |
| D9 | Failure mode | agent-worker timeout (default 2 s) → lexical fallback, `agent_runs.retrieved.strategy="lexical_fallback"` | Messaging must never block on RAG |
| D10 | Agent/plugins/MCP | Removed from this service. Later: ZuvoCloud exposes tenant-scoped MCP tools (`kb_search`, `get_price`, `book_visit`) from its own MCP server | Kiosk plugin model is one domain per process |

## 4. Keep / change / delete (file map)

| Area | Files | Action |
|---|---|---|
| App shell | `main.py`, `utils/config_loader.py`, `utils/logger_config.py`, `utils/latency_store.py` | **Keep**; drop model preload/ensure; add tenant + auth middleware |
| Config | `config.yaml` | **Rewrite**: `gemini.*`, `store.*` (pg DSN), `retrieval.{top_k,fetch_k,rrf_k,rerank,threshold}`, `chunking.*` |
| Chunking | `components/chunking/{markdown_splitter,atomic_blocks,recursive_splitter,size_splitter,profile_detector,text_utils,types,semantic_merger}.py`, `components/chunker_component.py` | **Keep**; replace tokenizer dependency with estimator; `llm_splitter.py` → Gemini text generator, disabled by default |
| Embedding | `components/embedding_component.py` | **Replace** with `GeminiEmbedder` (batch ≤100, retry/backoff like `GEMINI_MAX_ATTEMPTS`, Redis cache for query vectors) |
| Rerank | `components/reranker_component.py` | **Replace** with `GeminiListwiseReranker` (one Flash-Lite call, JSON scores 0–10) |
| LLM | `services/llm_service.py`, `services/ovms_llm_service.py`, `components/ov_ir_llm.py` | **Replace** with `services/gemini_service.py` (generate + stream) |
| Pipeline | `pipeline.py` | **Rewrite** as stateless `RagService` (no singleton collection); tenant passed per call |
| Ingestion | `services/ingestion_service.py` | **Change**: tenant-scoped replace-by-source, checksum idempotency, writes `kb_chunks` |
| Retrieval | `services/retrieval_service.py` | **Change**: hybrid SQL + RRF + optional rerank; returns `strategy`, scores, `chunk_id` |
| Prompting | `services/prompt_builder.py` | **Keep** for `/answer` playground only |
| API | `api/custom_endpoints.py` | **Rewrite** to tenant routes (§5) |
| API | `api/openai_endpoints.py`, `api/agent_endpoints.py` | **Delete** |
| Agentic | `agentic/*` (ADK runtime, ordering_agent, plugin_loader, mcp_client, domain_config, action_result, llm_metrics, config, resources) | **Delete**; port the *pinned root section* idea from `tools/knowledge_lookup_tool.py` into retrieval (per tenant: always include the doc's top section for name/hours/address questions) |
| Utils | `utils/ensure_model.py`, `utils/preload_models.py` | **Delete** |
| Tests | `tests/test_chunker.py` | **Keep** |
| Tests | `tests/test_{ordering_agent,menu_guard,price_guard,removal_guard,confirm_guard,cart_state_guard,item_intent_guard,reply_templates,mcp_client,directive_mode_http_client,knowledge_lookup_tool}.py` | **Delete** (kiosk/agent) |
| Eval | `scripts/evaluate_rag.py`, `generate_sample_qna.py`, `run_qna_batch.py`, `generate_report.py` | **Adapt** to tenant golden sets and Gemini |
| Packaging | `Dockerfile`, `requirements.txt`, `docker-compose.yml`, `docs/*`, `README.md`, `plugins/` | **Rewrite**: `python:3.12-slim`; deps `fastapi uvicorn pydantic pyyaml google-genai psycopg[binary] pgvector redis aiokafka pypdf python-docx python-multipart`; remove local compose & plugins dir |

Expected size after pruning: ~3–3.5k lines; image ~250 MB (from multi-GB).

## 5. API contract (v1)

All routes require `Authorization: Bearer <RAG_SERVICE_TOKEN>`; `tenant_id` is a UUID.

| Method | Path | Body / result |
|---|---|---|
| POST | `/v1/tenants/{tenant_id}/documents` | `{source_path, title, text, checksum?, metadata?}` → `{document_id, chunks, skipped:bool}` |
| POST | `/v1/tenants/{tenant_id}/documents/file` | multipart pdf/docx/md/txt (25k-token cap kept) → same |
| DELETE | `/v1/tenants/{tenant_id}/documents/{document_id}` | deactivates doc + deletes its chunks (tenant-scoped) |
| POST | `/v1/tenants/{tenant_id}/retrieve` | `{query, top_k=3, history?:[str]}` → `{chunks:[{chunk_id, document_id, source_path, section, text, score, rank}], strategy:"hybrid_rrf"\|"hybrid_llm_rerank", below_threshold:bool, latency_ms}` |
| POST | `/v1/tenants/{tenant_id}/answer` | playground only: `{query}` → `{answer, chunks}` (Gemini, FACTS-only prompt) |
| POST | `/v1/tenants/{tenant_id}/reindex` | re-embed all active docs (model change) |
| GET | `/v1/tenants/{tenant_id}/stats` | docs, chunks, embed_model, last_indexed_at |
| GET | `/healthz`, `/readyz` | readyz checks Postgres + Gemini key present |

Kafka: topic `kb.document.changed` (key = tenant_id) `{tenant_id, document_id, checksum, op:"upsert"|"delete"}`.

## 6. Data model — migration `0003_rag_pgvector`

* Postgres image → `pgvector/pgvector:pg16` (Debian). Moving from `postgres:16-alpine`
  changes libc collation: in dev, recreate the volume or run `REINDEX DATABASE`; in
  prod, dump/restore.
* `CREATE EXTENSION IF NOT EXISTS vector;`
* `ALTER TABLE kb_chunks ADD`:
  `section TEXT`, `char_count INT`, `embedding vector(768)`, `embed_model VARCHAR(64)`,
  `tsv tsvector GENERATED ALWAYS AS (to_tsvector('simple', text)) STORED`,
  `created_at TIMESTAMPTZ DEFAULT now()`.
* Indexes: `hnsw (embedding vector_cosine_ops)`, `gin (tsv)`, existing `(tenant_id, document_id)`.
  Use `SET hnsw.iterative_scan = relaxed_order` (pgvector ≥0.8) so tenant-filtered ANN keeps recall.
* `kb_documents ADD indexed_checksum VARCHAR(64), indexed_at TIMESTAMPTZ, index_error TEXT`.
* RLS already enforced on both tables; zuvo-ai-rag connects as `zuvo_app` and sets
  `app.tenant_id` per transaction (same pattern as other services).

Hybrid query (sketch):
```sql
WITH v AS (SELECT id, row_number() OVER (ORDER BY embedding <=> :qvec) r
           FROM kb_chunks c JOIN kb_documents d ON d.id=c.document_id AND d.is_active
           ORDER BY embedding <=> :qvec LIMIT :fetch_k),
     t AS (SELECT id, row_number() OVER (ORDER BY ts_rank(tsv, q) DESC) r
           FROM kb_chunks c JOIN kb_documents d ON d.id=c.document_id AND d.is_active,
                websearch_to_tsquery('simple', :query) q
           WHERE tsv @@ q LIMIT :fetch_k)
SELECT id, sum(1.0/(:rrf_k + r)) score FROM (SELECT * FROM v UNION ALL SELECT * FROM t) x
GROUP BY id ORDER BY score DESC LIMIT :fetch_k;
```

## 7. agent-worker integration

* New `zuvo_common/rag_client.py`: httpx client, `RAG_URL`, `RAG_SERVICE_TOKEN`,
  `RAG_TIMEOUT_SECONDS=2`, `RAG_ENABLED` feature flag (per tenant via `config.yaml`
  `knowledge.retrieval: lexical|rag`, default `lexical` until eval passes).
* `logic.faq_proposal`: replace `rank_texts(chunks)` with `rag_client.retrieve()`;
  on error/timeout fall back to current lexical path. Sentence selection from top
  chunk(s) unchanged; drafter + number guard unchanged.
* Confidence: map `below_threshold` → current "no facts" path (holding reply /
  approval). Keep existing `decide()` thresholds.
* Persist `agent_runs.retrieved = {strategy, chunks:[{chunk_id, score}], latency_ms}`
  for the dashboard and for learning analysis.
* Migrator: after `sync_knowledge`, publish `kb.document.changed` for changed /
  deactivated docs. Existing `kb_chunks` text-only rows are re-chunked by the
  indexer (it owns chunking once enabled).

## 8. Phases & estimates

| Phase | Work | Est. | Exit criteria |
|---|---|---|---|
| P0 | Import code (**done**) + prune kiosk/agent/OpenVINO files; slim requirements & Dockerfile; chunker tests green | 0.5 d | `pytest zuvo-ai-rag/tests` passes; image builds without torch/openvino |
| P1 | Gemini adapters: `GeminiEmbedder`, `GeminiTextGenerator`, token estimator; deterministic `FakeEmbedder` for CI | 1 d | unit tests with fake; live smoke embeds 1 chunk |
| P2 | Migration 0003 + `PgVectorStore` (upsert, delete, hybrid RRF) + RLS session | 1.5 d | isolation test: tenant A query never returns tenant B chunk |
| P3 | Tenant API (§5), auth, per-tenant pinned-section cache, upload limits | 1 d | API tests: 401 without token, 404 cross-tenant doc |
| P4 | Indexer: Kafka consumer, checksum idempotency, reindex endpoint; migrator publishes events | 1 d | edit `workshop.md` → re-indexed within seconds; unchanged docs skipped |
| P5 | agent-worker `rag_client` + fallback + `agent_runs.retrieved` | 1 d | `e2e_phase1.py` passes with `RAG_ENABLED=true` and with RAG stopped |
| P6 | Eval: golden Q&A per tenant (Alpha, Jaipur; EN + Hinglish, ~30 each incl. out-of-domain); port `evaluate_rag.py`; calibrate RRF/LLM-rerank threshold; compare vs lexical | 1 d | hit@3 ≥ 0.9 in-domain, ≤ 10% OOD leakage, better than lexical baseline |
| P7 | Compose service (`zuvo-ai-rag`, internal only), health, metrics, README + `zuvo-dev` skill, flip Alpha to `rag` | 0.5 d | stack up with 15 containers; skill documents ops |

**Total ≈ 7.5 dev days.**

## 9. Testing

* Unit: chunker (kept), embedder/reranker adapters with mocked `google-genai`,
  RRF fusion, tenant guard, checksum idempotency.
* Integration (needs Postgres, no Gemini): `FakeEmbedder` (hash → vector) so CI runs offline.
* Contract: agent-worker ↔ rag `retrieve` schema test.
* E2E: existing `scripts/e2e_phase1.py` + a RAG-down variant.
* Quality: `scripts/evaluate_rag.py --tenant alpha-automobile` golden set; track in CI as a non-blocking report.

## 10. Risks & mitigations

| Risk | Mitigation |
|---|---|
| Gemini 429 / latency on query embedding | Retry with backoff, Redis cache (`sha256(model+query)`), 2 s client timeout → lexical fallback |
| Embedding model upgrade changes vectors | Store `embed_model` per chunk; `/reindex`; query only rows matching active model |
| Tenant KB data sent to Google | Already the case for drafting; document in DPA; never send PII-bearing chat history to embeddings |
| Postgres image switch (alpine→debian) | Volume recreate in dev; dump/restore in prod |
| LLM rerank cost/latency | Off by default; enable per tenant only if eval shows gain |
| Over-retrieval of unrelated text | Threshold calibrated in P6 + drafter FACTS-only + number guard + approval gate remain |
| Upstream licence unclear | Confirm before commercial release (§0) |

## 11. Open questions

1. Confirm pgvector-in-Postgres (recommended) vs keeping Chroma with one collection per tenant.
2. Keep the `/answer` playground endpoint, or retrieval-only?
3. Enable Gemini LLM rerank by default, or only after P6 shows a gain?
4. Licence/ownership of the imported upstream code.

## 12. Implementation status (2026-10-19)

Decisions taken: pgvector ✔, `/answer` playground kept ✔, LLM rerank **off** (evaluated below).

| Phase | Result |
|---|---|
| P0–P1 | Kiosk/agent/OpenVINO code removed; `components/gemini.py` (embedder, text, listwise reranker, hashing embedder for tests); image ≈ 490 MB, no torch |
| P2 | `infra/postgres/Dockerfile` (postgres:16-alpine + pgvector v0.8.0, same libc → existing volume kept); migration `0003_rag_pgvector`; `services/store.py` hybrid search (HNSW cosine + `tsv` GIN, RRF), `hnsw.iterative_scan` |
| P3 | `api/routes.py` v1 tenant API, Bearer token (constant-time), UUID + known-tenant guard, uploads limited to `uploads/` |
| P4 | Indexer **reconcile loop** (10 s) instead of a Kafka `kb.document.changed` event — the migrator only writes `body`/`checksum`; pending = `checksum ≠ indexed_checksum` or model change; errors back off 5 min |
| P5 | `zuvo_common.rag_client`, `KnowledgeCfg` (`retrieval: lexical|rag`), agent-worker `faq_proposal(rag=…)`; confidence = extraction conf × ramp(sim 0.55→0.75); 2 s timeout → `lexical_fallback` |
| P6 | Golden set + `scripts/evaluate_rag.py` (results in `zuvo-ai-rag/README.md`): RAG 24/25 vs lexical 21/25 in-domain; floor 0.60 kept; rerank gives 25/25 + 9/9 OOD at 0.58 but ~2× latency |
| P7 | Compose service `zuvo-ai-rag` (internal), `RAG_SERVICE_TOKEN`, Alpha flipped to `rag`, docs + `zuvo-dev` skill |

Tests: zuvo-ai-rag 16 (unit + API), platform unit 46 (+6 RAG), legacy e2e 6, `e2e_phase1.py`, `scripts/e2e_rag.py` (normal and `--rag-down`).

Follow-ups: confirm upstream licence; dashboard "Test my knowledge" UI on `/answer` + document upload via dashboard-api
proxy; consider per-tenant rerank once traffic data exists; FAQ confidence ramp may be tightened to 0.60→0.72 after
real approvals accumulate.
