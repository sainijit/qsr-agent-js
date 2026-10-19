# Runbook: RAG indexing stuck or failing

> Conventions: run on the VM from `/opt/zuvo` as the deploy user. `D=scripts/deploy/dcp` (compose pinned to the current
> release). `M="$D exec -T dashboard-api python -m zuvo_common.opsmetrics"`. Never use `down -v`. Never paste secrets,
> payloads or phone numbers into tickets or chat.

## Symptoms
- `metric:zuvo_rag_documents` (failed), `metric:zuvo_rag_processing_stuck`, `metric:zuvo_rag_oldest_pending_seconds`, `metric:zuvo_rag_failed_max_attempts` alerts. A tenant's new knowledge is not used in replies.

## First diagnostics
```bash
$D logs --tail 200 zuvo-ai-rag | grep -E 'index|embed' | tail -30
$D exec -T zuvo-ai-rag python -c "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8020/readyz').read().decode())"
$D exec -T postgres psql -U zuvo_owner -d zuvo_db -c "select tenant_id, index_state, index_attempts, left(index_error,60) from kb_documents where index_state <> 'indexed'"
```
## Logs and metrics
- `index_error` prefix gives the category (`docs/rag-reliability.md` §3). `/readyz` 503 `embedding_dimension_mismatch` means the config and the DB `vector(N)` disagree.

## Safe recovery
1. Transient categories: nothing to do. Backoff retries (5 min · 2^(n-1), cap 6 h). The previous indexed version keeps serving.
2. `processing` stuck: `$D up -d --no-deps --force-recreate zuvo-ai-rag`. Stale claims are re-taken after 10 min.
3. `client_error` / `invalid_embedding`: fix the model/key config, then retry immediately with `POST /v1/tenants/{id}/reindex` on zuvo-ai-rag (re-indexes that tenant's documents) (`docs/rag-reliability.md` §8).
4. Dimension mismatch: revert the config change. Changing dimensions is a migration plus a full re-index (a planned release).

## Requires approval
- Force re-indexing all documents (Gemini cost), embedding model or dimension changes.

## Verify
`zuvo_rag_documents{state="failed"}` is 0, the document is `indexed` with the new `document_version`, and a test question uses the new content.
