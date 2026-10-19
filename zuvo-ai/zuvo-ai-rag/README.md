# zuvo-ai-rag

Multi-tenant knowledge retrieval for ZuvoCloud, built on **Gemini embeddings** and **pgvector**
(hybrid vector + full-text search, fused with RRF). It runs as an internal service on port `8020`.
It has no published port and no OpenVINO/OVMS dependency.

> Provenance: the chunking pipeline (`components/chunker_component.py`, `components/chunking/`) and
> `services/prompt_builder.py` come from `voice-enabled-interactions/smart-kiosk-assistant/rag-service`
> @ `edff388`. The kiosk plugins, ADK agent, OpenAI-compatible API, Chroma store and OpenVINO models were removed.
> The upstream repo has no LICENSE file, so confirm ownership before commercial release.
> Design and decisions: [`docs/ZUVO_AI_RAG_PLAN.md`](../docs/ZUVO_AI_RAG_PLAN.md).

## How it fits

```
tenants/<slug>/knowledge/*.md ──migrator──► kb_documents.body (checksum)  ◄── POST /documents (uploads/…)
                                                   │ indexer loop (10 s): pending = checksum ≠ indexed_checksum
                                                   ▼
                     chunk (markdown-aware) ─► Gemini gemini-embedding-001 @768 ─► kb_chunks.embedding + tsv
agent-worker (faq, tenant knowledge.retrieval=rag) ──POST /v1/tenants/{id}/retrieve──► hybrid search (RLS) ─► chunks
              └─ RAG down / timeout (2 s) ─► lexical fallback (strategy=lexical_fallback)
```

* **Tenant isolation:** every query runs in `tenant_session` (Postgres RLS on `app.current_tenant`), and the
  tenant in the path must be a known bundle UUID.
* **Bundle docs** (`knowledge/…`) belong to the migrator. The API only manages `uploads/…` documents.
* The agent-worker keeps its guards: the drafter only sees FACTS, the number guard applies, and so do the approval and learning gates.
  An owner-approved example still takes priority over retrieval.

## API (Bearer `RAG_SERVICE_TOKEN` on every `/v1` route)

| Method & path | Purpose |
|---|---|
| `GET /healthz`, `GET /readyz` | liveness / DB + embedder readiness |
| `POST /v1/tenants/{id}/retrieve` `{query, top_k?, min_similarity?}` | ranked chunks + `below_threshold`, `top_similarity`, `strategy`, latency |
| `POST /v1/tenants/{id}/answer` `{query, history?}` | playground: retrieve + Gemini answer (not the customer reply path) |
| `POST /v1/tenants/{id}/documents` `{source_path, title?, text}` | add/replace an `uploads/` document (indexed inline) |
| `POST /v1/tenants/{id}/documents/file` | multipart txt/md/docx/pdf, ≤ 10 MB |
| `GET /v1/tenants/{id}/documents`, `DELETE …/documents/{doc_id}` | list / deactivate (uploads only) |
| `POST /v1/tenants/{id}/reindex`, `GET …/stats` | force re-embed / index health |

## Configuration

`config.yaml` holds the defaults. Override them with `ZUVO_RAG__SECTION__KEY` env vars or `ZUVO_RAG_CONFIG_OVERRIDE_PATHS`.
Key settings:

| Key | Default | Notes |
|---|---|---|
| `gemini.embed_model` / `embed_dimensions` | `gemini-embedding-001` / 768 | changing the dimension needs a migration; changing the model needs a reindex (stale chunks are excluded by `embed_model`) |
| `retrieval.min_similarity` | 0.60 | calibrated in P6 (below) |
| `retrieval.rerank.mode` | `none` | `llm` = Gemini listwise rerank, which is better at off-topic rejection but adds about 0.6 s |
| `indexer.poll_seconds` | 10 | reconcile loop; 0 disables |

Per-tenant settings live in `tenants/<slug>/config.yaml`:
`knowledge: { retrieval: rag | lexical, top_k: 3, min_similarity: null }`.

## Evaluation (golden set `eval/golden.yaml`, 25 in-domain + 9 out-of-domain, EN + Hinglish)

```
docker compose exec zuvo-ai-rag python scripts/evaluate_rag.py [--rerank]
```

| | top-1 in-domain | off-topic rejected | median latency |
|---|---|---|---|
| lexical baseline | 21/25 | 7/9 | – |
| RAG, floor 0.60 | 24/25 | 8/9 | ~0.47 s |
| RAG + LLM rerank, floor 0.58 | 25/25 | 9/9 | ~1.1 s |

## Develop & test

```
docker compose build zuvo-ai-rag
docker run --rm -u root -v $PWD/zuvo-ai-rag/tests:/app/tests:ro -v $PWD/zuvo-ai-rag/pytest.ini:/app/pytest.ini:ro \
  zuvo-ai-zuvo-ai-rag sh -c "pip install -q pytest && python -m pytest -q"        # 16 unit/API tests
set -a; . ./.env; set +a; python scripts/e2e_rag.py [--rag-down]                     # reply-path e2e (Alpha)
```
