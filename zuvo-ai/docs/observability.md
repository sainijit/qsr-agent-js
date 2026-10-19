# Observability

ZuvoCloud is a small, single-host Docker Compose deployment, so observability is kept small too:
**structured JSON logs + correlation IDs + a pull-based ops-metrics endpoint + host cron checks.**
There is no Prometheus server, Grafana, or tracing backend. That is a cost/RAM decision, not an omission; see §6.

## 1. Structured logs

Every Python service calls `zuvo_common.logging` (JSON formatter, one object per line to stdout).
Locally use `docker compose logs`. Prod keeps rotated `json-file` logs by default. Add `deploy/aws/docker-compose.awslogs.yml` as a third `-f` file to ship each service to CloudWatch log group `/zuvo/<service>` (instance-role IAM needed; Docker dual logging keeps `docker compose logs` working).

| Field | Meaning |
|---|---|
| `timestamp`, `level`, `msg`, `logger` | Standard. |
| `service` | Container's `SERVICE_NAME` (e.g. `ai-router`). |
| `environment`, `deployment_version` | `APP_ENV`, `ZUVO_VERSION`. Set `ZUVO_VERSION` to the git SHA/tag at deploy time. |
| `trace_id`, `span_id` | W3C trace context (OTel-compatible hex), propagated over HTTP `traceparent` and Kafka headers. |
| `request_id` | HTTP request id. nginx generates it and forwards it as `X-Request-ID`; the response echoes it in `x-request-id`. |
| `correlation_id` | Constant for a whole business flow (inbound message → AI → agent → dispatch). |
| `event_id`, `causation_id`, `event_type` | Kafka event being handled and the event that caused it. |
| `tenant_id`, `conversation_id`, `wamid`, `agent_run_id` | Business identifiers (never phone numbers). |
| `operation`, `outcome`, `duration_ms`, `error_code` | Per-operation result (`gemini.extract`, `gemini.draft`, `agent.decide`, `meta.send`, `rag.retrieve`, `<group>:<event_type>`, HTTP). |

Context is bound through a contextvar (`bind(...)`). The Kafka consumer wrapper (`contract.py`) binds the event fields automatically,
and the HTTP middleware (`httpobs.py`) binds the request fields. Every log line inside that scope inherits them.

**Correlation lives in Kafka headers** (`traceparent`, `correlation_id`, `causation_id`), not in the envelope body.
`Envelope` uses `extra="forbid"`, so header-only propagation stays compatible with old consumers during rolling deploys.

### Redaction
`scrub()` runs on every message and string field. It redacts Bearer tokens, Meta `EAA…` tokens, Google `AIza…` keys,
`sha256=` signatures, JWTs, `password=` / `token=` pairs and URL passwords, and it masks phone numbers.
Keys that look like secrets (`*token*`, `*secret*`, `*password*`, `authorization`, …) are redacted wholesale.
ID fields are exempt so hex trace IDs are never corrupted. Uvicorn access logs are disabled and replaced by the
middleware's `http request` line, which logs the **route template** (`/api/v1/leads/{lead_id}`) without the query string.

nginx logs JSON (`zuvo_json` format) with `$uri` only (no query string, so SSE `?token=` never reaches logs),
`request_id`, `status`, `request_time` and upstream timings.

## 2. Liveness vs readiness

| Endpoint | Service | Semantics |
|---|---|---|
| `/livez` | gateway, dashboard-api, zuvo-ai-rag | Process is up. Always 200. Used by Docker healthchecks; never depends on downstreams (avoids restart storms). |
| `/readyz` | gateway (Redis + Kafka), dashboard-api (DB + Kafka), zuvo-ai-rag | 200 when dependencies respond, otherwise **503** with a per-check body. Used by deploy scripts and external checks. |
| `/api/v1/health` | dashboard-api | Legacy; always 200 with a `degraded` flag (the frontend uses it). |

Kafka workers (persister, ai-router, lead-service, agent-worker, dispatch, scheduler) expose no HTTP. Consumer lag and DLQ metrics (below) measure their health.

## 3. Ops metrics

`dashboard-api` serves `GET /internal/metrics` (Prometheus text format). It is **not routed by nginx**; reach it from the host only:

```bash
docker compose exec -T dashboard-api python -m zuvo_common.opsmetrics          # JSON
docker compose exec -T dashboard-api python -m zuvo_common.opsmetrics --prom   # Prometheus text
```

Postgres metrics are summed per tenant through `tenant_session` (so RLS stays enforced). Kafka metrics use the admin
client and consumer-group offsets. Each section reports `zuvo_collect_ok{section}` and `zuvo_collect_seconds{section}`;
a failed section logs the error and still returns the others.

| Metric | Labels | Suggested alert (pilot) |
|---|---|---|
| `zuvo_collect_ok` | section | `== 0` for 2 checks |
| `zuvo_kafka_consumer_lag` | group, topic | `> 100` for 10 min |
| `zuvo_kafka_oldest_unprocessed_seconds` | group | `> 300` |
| `zuvo_kafka_dlq_messages` | topic | `> 0` (any new DLQ message) |
| `zuvo_outbox_pending` | – | informational (normally 0) |
| `zuvo_outbox_oldest_pending_seconds` | – | `> 120` |
| `zuvo_outbox_failing` | – | `> 0` for 5 min |
| `zuvo_outbox_max_attempts` | – | `> 10` |
| `zuvo_processed_events` | – | informational (should plateau; see `docs/kafka-reliability.md`) |
| `zuvo_outbound_unknown` | `> 0` | – (each needs reconciliation; see `docs/outbound-delivery.md`) |
| `zuvo_outbound_unknown_oldest_seconds` | `> 3600` | `> 86400` |
| `zuvo_outbound_pending_retry` | `> 20` | `> 100` (Meta throttling / outage) |
| `zuvo_outbound_sending_stuck` | – | `> 0` (dispatch sweeper not running) |
| `zuvo_outbound_failed_24h` | informational | spike vs. baseline |
| `zuvo_messages_24h` | direction, status | outbound `failed` > 5% of sent |
| `zuvo_inbound_ai_status_24h` | status | `failed` > 10% |
| `zuvo_approvals_pending` / `_oldest_pending_seconds` / `_overdue` | – | overdue `> 0` |
| `zuvo_agent_runs_24h` | decision | informational |
| `zuvo_agent_latency_p95_ms` | – | `> 15000` |
| `zuvo_ai_calls_today`, `zuvo_ai_tokens_today` | – | informational |
| `zuvo_ai_cost_inr_today` / `_month` | – | above an operator-set daily budget (watch-script threshold) |
| `zuvo_rag_documents` | state (indexed/pending/processing/failed) | `failed > 0` |
| `zuvo_rag_processing_stuck` | – | `> 0` |
| `zuvo_rag_failed_max_attempts` | – | `>= 4` (~1 h of failures) |
| `zuvo_agent_outcomes_24h` | outcome | `rag_unavailable` or `draft_failed` > 10% of runs |
| `zuvo-ai-rag` `/metrics` (`zuvo_rag_*_total`, `_seconds`) | outcome / category | see `docs/rag-reliability.md` |
| `zuvo_rag_oldest_pending_seconds` | – | `> 900` |
| `zuvo_followups_stuck` | – | `> 0` |
| `zuvo_pg_connections` vs `zuvo_pg_max_connections` | – | `> 80%` |
| `zuvo_pg_database_bytes` | – | `> 70%` of volume |
| `zuvo_tenants` | – | informational |

A host cron script evaluates these thresholds (`deploy/ops/zuvo-watch.sh`; rules in `deploy/ops/watch_eval.py`) and alerts via SNS and/or a chat webhook; see `docs/runbooks/README.md`.
Host memory, disk and CPU come from the CloudWatch agent (`mem_used_percent`, `disk_used_percent`). The basic EC2 metrics do not include memory.
Measured baselines for these metrics under load (lag, memory peak, latency percentiles, drain times) are in `docs/load-testing.md`.

## 4. CloudWatch Logs Insights queries

Log groups: one per service (`/zuvo/<service>`), created by `deploy/aws/docker-compose.awslogs.yml`. Set retention (e.g. 14–30 days) on `/zuvo/*`; ingest is about $0.50/GB.
Logs Insights auto-discovers fields from the JSON lines. nginx lines are in `/zuvo/frontend`.

```sql
-- HTTP error rate per 5 min (nginx)
fields @timestamp, status
| filter service = "frontend-nginx"
| stats count(*) as total, sum(status >= 500) as errors by bin(5m)

-- p95 latency per route (nginx)
filter service = "frontend-nginx" | stats pct(request_time, 95) as p95, count(*) by uri | sort p95 desc | limit 20

-- Slowest operations (apps)
filter ispresent(duration_ms) | stats pct(duration_ms, 95) as p95, count(*) as n by service, operation | sort p95 desc

-- Gemini failures
filter operation like /gemini/ and outcome != "ok" | stats count(*) by error_code, service

-- Events sent to DLQ
filter msg = "event handled" and outcome = "dlq" | fields @timestamp, service, event_type, event_id, error_code

-- Everything for one conversation flow
filter correlation_id = "<id>" | sort @timestamp asc

-- Everything for one HTTP request (nginx + app)
filter request_id = "<id>" or trace_id = "<trace>" | sort @timestamp asc
```

## 5. Troubleshooting a message

1. Get the `wamid` or the lead/conversation id from the dashboard.
2. Use Logs Insights: `filter wamid = "<wamid>"` and note the `correlation_id`.
3. Use `filter correlation_id = ...` to see gateway → persister → ai-router → agent-worker → dispatch in order.
   Each `event handled` line has `outcome` (`ok`/`dlq`), `attempts` and `duration_ms`.
4. If an event went to DLQ: check `zuvo_kafka_dlq_messages` and the replay procedure (see `docs/kafka-reliability.md`).

Locally, the equivalent is `docker compose logs --no-log-prefix <svc...> | jq 'select(.correlation_id=="<id>")'`.

## 6. Tracing decision

- Trace IDs are OTel-compatible and appear in **logs only**. **No spans are exported.**
- Adding OpenTelemetry + AWS X-Ray (or an OTel collector) would add a sidecar (~100–200 MB RAM on a 4 GB host) and per-trace
  cost. For 2–5 tenants, correlated logs give enough insight. Revisit when there are more than ~20 tenants or multiple hosts.
- Because IDs already follow W3C `traceparent`, enabling the OTel SDK later needs no format change.

## 7. Verification (dev stack)

- `/livez` and `/readyz` return 200 on gateway, dashboard-api and zuvo-ai-rag. Gateway `/readyz` went 200 → `503 {"redis":"down"}` with Redis stopped → 200 after restart; `/livez` stayed 200 throughout.
- `/internal/metrics`: all three sections `ok`. DLQ gauges emit `0` for each of the 13 `.DLQ` topics.
- After the e2e suites, 16 of the 17 traces in the logs spanned ≥3 services (gateway → persister → ai-router → agent-worker),
  sharing `trace_id` and `correlation_id`.
- No `access_token=` appears in any container log. nginx lines are JSON with `request_id`.
- `tests/unit/test_observability.py` covers scrubbing, context binding and traceparent propagation, the request middleware and Prometheus rendering.
