# Runbook: Gemini errors, latency, rate limiting, or cost

> Conventions: run on the VM from `/opt/zuvo` as the deploy user. `D=scripts/deploy/dcp` (compose pinned to the current
> release). `M="$D exec -T dashboard-api python -m zuvo_common.opsmetrics"`. Never use `down -v`. Never paste secrets,
> payloads or phone numbers into tickets or chat.

## Symptoms
- `metric:zuvo_agent_latency_p95_ms`, `ratio:ai_failed`, `ratio:agent_outcomes`, `metric:ai_cost` alerts. Inbound messages stuck in `AI_PENDING` / `NEEDS_REVIEW` / `FAILED`. Tenants get approval cards without drafts.

## First diagnostics
```bash
$D logs --since 30m ai-router agent-worker zuvo-ai-rag | grep -E '"operation": "gemini|rag\.' | grep -v '"outcome": "ok"' | tail -20
$M | grep -E 'zuvo_ai_|agent_outcomes|agent_latency'
```
## Logs and metrics
- `error_code` (category): `rate_limited` (429, quota), `server_error` (5xx), `timeout`, `network`, `client_error` (bad key or model name), `invalid_embedding`.
- Quota and billing: Google AI Studio / Cloud console → the API key's project → quotas.

## Safe recovery
1. `rate_limited` / `server_error` / `timeout`: transient. Calls retry with jitter and RAG falls back to lexical retrieval. Wait, and confirm on https://status.cloud.google.com.
2. `client_error` after a config change: revert the model name in the tenant/agent config and roll the change out (deploy).
3. Proxy/egress (`network`): check `curl -sI https://generativelanguage.googleapis.com` from the VM and the security group egress rules.
4. Cost over budget: find the tenant with `$D exec -T dashboard-api python -m zuvo_common.opsmetrics` (per-day totals) and `ai_usage` in the DB; consider lowering limits in that tenant's bundle.
5. After an outage: messages whose extraction retries ran out stay `AI_PENDING`. They are **not reprocessed automatically** when Gemini
   recovers (measured in `docs/load-testing.md` §3.5). They are counted in the dashboard pending queue, and staff must answer them from the
   dashboard. To list how many there are per tenant (counts only, no message text):
   ```bash
   $D exec -T postgres sh -c 'psql -X -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT tenant_id, count(*), min(created_at) FROM messages WHERE ai_status = '"'"'AI_PENDING'"'"' AND created_at > now() - interval '"'"'1 day'"'"' GROUP BY 1"'
   ```
   Tell the affected tenants' owners to check their pending queue.

## Requires approval
- Rotating the Gemini API key (`security.md` §4), changing the model or tier, raising quota (billing).

## Verify
New inbound messages reach `EXTRACTED`, `agent_outcomes` shows `drafted`/`approved` again, and p95 < 15 s on the next watch runs.
