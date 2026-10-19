# ZuvoCloud — Production Readiness Assessment

Scope: single-VM Docker Compose pilot for 2–5 tenants (see `docs/HLD.md`).
Method: read the code, compose file, migrations and tests, and inspected the running dev stack
(`docker compose config`, `docker stats`, `pg_class`/`pg_policy`, git history scan).
Every finding is tagged **[CONFIRMED]** (verified in code or the live stack) or **[ASSUMED]** (inferred, not yet verified).

Progress against each item is tracked in `docs/production-readiness-checklist.md`.

## 0. Final status (after phases 2–15, 2026-10-19)

The baseline below is kept unchanged as the record of where we started. This table gives the state of each risk now.
"Verified" means it was run on the dev host or the isolated production-mode project (`zuvo-prodtest`, APP_ENV=production).
Nothing has run in AWS yet.

| # | Fix | Evidence | Residual risk |
|---|---|---|---|
| R1 | Durable outbound ledger `outbound_messages`; Meta timeout/5xx/crash mid-send → `UNKNOWN`, never auto-resent; reconcile CLI | `docs/outbound-delivery.md`; unit + integration tests; live UNKNOWN drill | `biz_opaque_callback_data` echo not tested against live Meta |
| R2 | Transactional outbox on every DB-then-publish path | `docs/kafka-reliability.md`; integration tests; outage load test (no loss) | none known |
| R3 | `kafka_data` volume; heap capped | offsets + topics survive `--force-recreate` and restart (persistence drill) | single broker, no replication (pilot decision) |
| R4 | Durable processed-events table (Redis no longer the only guard) | unit/integration tests | — |
| R5 | Rotation procedure documented (`docs/security.md` §6); release gate scans for the token pattern | secret-scan gate PASS on the current tree | **Open: rotating the exposed Meta tokens is an owner action and has NOT been done.** History still contains them |
| R6 | Caddy is the only published service (HTTPS, headers, body limit); data services on an `internal` network | `smoke-test.sh` exposure checks; Postgres/Redis egress blocked | real Let's Encrypt issuance NOT VERIFIED |
| R7 | Production refuses dev login, simulator, HMAC off; preflight rejects weak default password; per-user passwords via `set_password.py` | `tests/unit/test_production.py`, `test_set_password.py`; fresh-install rehearsal | `META_DRY_RUN` flip is a manual go-live step (walkthrough step 15) |
| R8 | Backup/verify/restore scripts, S3 SSE-KMS, cron, 6 h RPO; deploy refuses migrations without a verified backup | restore drills (`--as-new-db`, `--replace`) | real S3/KMS/IAM and cron on the VM NOT VERIFIED; no PITR |
| R9 | json-file log rotation (10 MB × 5) on every service; disk rules in `zuvo-watch.sh`; image pruning | compose config; watch unit tests | Kafka retention is time-based only |
| R10 | Measured `mem_limit`s; Kafka heap cap; 2 GB swap | load test peak 1.38 GiB, no OOM kills | not yet measured on a real 4 GB / 2 vCPU VM |
| R11 | SIGTERM drains the in-flight message | unit test; live stop exit 0 in < 1 s | — |
| R12 | Heartbeat health for consumers; `/livez` vs `/readyz` | 503 verified with Redis stopped; `health-check.sh` | — |
| R13 | Correlation ids, JSON logs everywhere, `/internal/metrics`, `zuvo-watch.sh` alerting | `docs/observability.md`, `docs/runbooks/` | no tracing backend; real SNS delivery and HTTP 5xx alarm NOT VERIFIED |
| R14 | Retry jitter, transient/permanent split, DLQ metadata, replay tool | unit tests; 10 % Gemini-error load test (89/89 extracted) | — |
| R15 | **Not fixed.** Redis has no password. It is on the `internal` data network with no published port and no internet route | compose config; egress test | any compromised app container can read/write Redis. Durable guards (R1/R4) no longer depend on it. Add `requirepass` + ACL if that changes |

Also found and fixed during the work: Kafka healthcheck CPU cost (prod probe every 30 s), a stale one-shot migrator being
re-run as a dependency, SSE token leak into access logs, and `deploy.sh` failing on an empty database (first install).
Known limits that are by design: per-tenant throughput ≈ 1 / Gemini latency (`docs/load-testing.md` §3.3); `AI_PENDING`
messages are not reprocessed automatically after an outage (`docs/runbooks/gemini-errors.md`); deploys are not zero-downtime.

**Go-live blockers:** R5 rotation; the AWS walkthrough (`deploy/aws/README.md` §0) executed with its NOT VERIFIED items
(Let's Encrypt, SSM, S3/KMS, SNS, 4 GB memory gate) checked on the real VM.

## 1. Highest-risk findings (ordered, baseline)

| # | Risk | Finding | Status |
|---|------|---------|--------|
| R1 | **Duplicate / lost customer messages** | Dispatch's "never double-send" guard is a Redis key `zuvo:send:{tenant}:{request_id}` (7 d TTL) claimed *before* the Meta call. Redis data loss (AOF corruption, volume loss, `FLUSHALL`) allows a resend on redelivery. There is no durable outbound record; a Meta **timeout** is reported as a plain failure (`transport: timeout`) although Meta may have delivered it. | CONFIRMED |
| R2 | **Lost events between DB commit and Kafka publish** | Persister, agent-worker (`_finalise` → `_emit`), dashboard-api approvals and `messages/send`, followup-scheduler and lead-service commit the DB transaction and *then* call `bus.publish`. A crash or Kafka outage in between loses the event (e.g. an approved reply is never sent). The follow-up scheduler alone compensates (`QUEUED` → `SCHEDULED` on publish failure). No transactional outbox. | CONFIRMED |
| R3 | **Kafka data is ephemeral** | `kafka` has no volume (`# dev only` in compose). Recreating the container drops every un-consumed event and every consumer offset; `auto_offset_reset=earliest` on new topics is then harmless only because topics are empty. | CONFIRMED |
| R4 | **Consumer idempotency is Redis-only** | `zuvo_common.contract` claims `zuvo:evt:{group}:{event_id}` (TTL 24 h) *after* the handler succeeds. Redis loss or a TTL expiry → reprocessing. Most handlers are DB-idempotent anyway (`ON CONFLICT` on `messages(tenant_id,wamid)`, `agent_runs(tenant_id,message_id,task)`, uuid5 event ids), so the residual risk concentrates in dispatch (R1). | CONFIRMED |
| R5 | **Exposed credentials in git history** | Meta-access-token-shaped strings (`EAA…`, 2 distinct values) exist in commit `df2f519`. No such strings in the current tracked tree; `.env` is untracked. Deleting a file does **not** remove it from history — rotation is required. | CONFIRMED (values not printed) |
| R6 | **Public attack surface** | `gateway` published on `0.0.0.0:8000` and `frontend` on `0.0.0.0:3000` over plain HTTP; no TLS, no security headers, no request-size limit or rate limiting at the edge. | CONFIRMED |
| R7 | **Insecure dev conveniences reachable via config** | `AUTH_DEV_LOGIN` (token for any seeded identity without a password) and `ENABLE_SIMULATOR` default to `false` but are enabled in the local `.env`. Nothing *prevents* them in production. `ZUVO_ONBOARD_DEFAULT_PASSWORD` sets the initial password of every new bundle user to the same value (README and `e2e_phase1.py` reference `Zuvo@2026`). `META_DRY_RUN` defaults to `true` — safe, but must be consciously flipped. | CONFIRMED |
| R8 | **No backups / DR** | Postgres is a named volume only; no dump schedule, no off-host copy, no restore drill. A manual pre-RAG dump exists outside the repo. | CONFIRMED |
| R9 | **Unbounded disk growth** | No Docker log rotation configured; Kafka retention 168 h with no size cap; no disk monitoring. | CONFIRMED |
| R10 | **No resource limits** | No `mem_limit`/`cpus` on any service; Kafka JVM heap uncapped (measured ~1.0 GB RSS). On a 4 GB VM one runaway container can OOM the host. | CONFIRMED |
| R11 | **Ungraceful shutdown** | Consumers stop only via `finally` when the task is cancelled; no SIGTERM handler draining the in-flight message. Commit-after-handle means at-least-once redelivery, not loss — acceptable but noisy. | CONFIRMED |
| R12 | **Health checks incomplete** | Only gateway, dashboard-api, zuvo-ai-rag (+ infra) have health checks; the six consumer services have none, and gateway `/api/v1/health` checks Redis only. A wedged consumer is invisible. | CONFIRMED |
| R13 | **No metrics / alerts / tracing** | JSON logs only (`zuvo_common.logging`, phones masked); zuvo-ai-rag logs plain text. No consumer-lag, DLQ, send-failure or token-cost metrics. ai-router token usage is not persisted. | CONFIRMED |
| R14 | **Retry without jitter** | Contract retries 3× with exponential backoff, no jitter; PermanentError → DLQ. Minor for a single replica. | CONFIRMED |
| R15 | **Redis has no auth** | `redis-server --appendonly yes` with no password; reachable from every container on the single `zuvo` network. Acceptable only while the network is internal-only. | CONFIRMED |

## 2. Detailed baseline

### 2.1 Compose topology (`docker-compose.yml`, compose v2.29.7)
- 15 services: 13 long-running + one-shot `migrator`, `kafka-init`.
- One bridge network `zuvo`; volumes `postgres_data`, `redis_data` (Kafka none).
- Published ports: gateway `:8000` (all interfaces), frontend `:3000` (all interfaces), dashboard-api `127.0.0.1:8001`.
- Restart `unless-stopped` on long-running services; ordering via `depends_on` (migrator/kafka-init completed, redis healthy).
- App containers run as uid 10001 `zuvo` (Python Dockerfiles); frontend is stock `nginx:alpine` (root master process).
- Measured idle RSS (dev, 2 tenants): Kafka ~1.0 GB; Python services 52–95 MB each; Postgres ~40 MB; Redis ~6 MB; nginx ~14 MB; total ≈ 1.7 GB, CPU < 2 %.

### 2.2 Configuration and secrets
- `zuvo_common.settings.Settings` (pydantic-settings, env only). Compose uses `${VAR:?}` for `POSTGRES_PASSWORD`, `ZUVO_APP_DB_PASSWORD`, `JWT_SECRET`; everything else silently defaults to empty.
- **No startup validation**: empty `JWT_SECRET` is impossible (compose `:?`), but empty `META_APP_SECRET` with HMAC on would reject all webhooks; empty `GEMINI_API_KEY` degrades AI silently; `RAG_SERVICE_TOKEN` empty → zuvo-ai-rag fails closed (503 on all `/v1` routes) and agent-worker falls back to lexical — safe but silent [CONFIRMED].
- `.env.example` contains placeholders only (one weak placeholder `change-me-now`).

### 2.3 Migrations
- `migrator` (one-shot): ensure role `zuvo_app LOGIN NOSUPERUSER NOBYPASSRLS` → `alembic upgrade head` (0001–0003) → sync legacy routing → validate + apply tenant bundles. Runs as `zuvo_owner` (Postgres superuser) via `MIGRATION_DATABASE_URL`. Idempotent; re-run on every `up`.

### 2.4 Kafka
- KRaft single broker, `auto.create.topics=false`, 13 topics + `.DLQ`, 12 partitions, RF 1. `kafka-init` uses `--create --if-not-exists` → **repeatable and safe**.
- Producer: `acks=all`, idempotent. Events are JSON `Envelope` (event_id, event_type, tenant_id, …, payload) with tenant headers; partition key = `{tenant_id}:{worker_id}` (`events.Envelope.key`) → per-tenant-worker ordering [CONFIRMED].
- Consumer: manual commit after each handled message; contract validates tenant header vs body vs authorised set, else DLQ with `ERROR` + `ORIGINAL_TOPIC` headers.

### 2.5 Idempotency and Redis
| Key | Owner | Loss impact |
|---|---|---|
| `zuvo:wamid:{tenant}:{wamid}` | gateway inbound dedupe | duplicate inbound event → absorbed by `messages(tenant_id,wamid)` unique (safe) |
| `zuvo:status:{tenant}:{wamid}:{status}` | gateway status dedupe | duplicate status → persister ranks statuses, monotonic (safe) |
| `zuvo:evt:{group}:{event_id}` | consumer contract | reprocessing → DB upserts absorb, except dispatch |
| `zuvo:send:{tenant}:{request_id}` | dispatch | **possible duplicate WhatsApp send (R1)** |

### 2.6 Tenant isolation (RLS)
- `tenant_session` → `set_config('app.current_tenant', tid, true)` inside `session.begin()`: transaction-local, therefore **cannot leak across pooled connections** [CONFIRMED by code]. Tenant id validated as UUID.
- Live DB: 24/26 public tables have `ENABLE` + `FORCE ROW LEVEL SECURITY` with policy `tenant_isolation` (`tenant_id = NULLIF(current_setting('app.current_tenant', true),'')::uuid`, USING + WITH CHECK). Services connect as `zuvo_app` (not owner, NOBYPASSRLS).
- Exceptions: `users` (no tenant_id, platform table) and `memberships` (has tenant_id, **no RLS**) — read by the login flow before a tenant is known. Any code path running as `zuvo_app` can read all memberships. [CONFIRMED] Acceptable by design but needs a test and documentation (Phase 4).
- No automated cross-tenant isolation test against real Postgres exists. [CONFIRMED]

### 2.7 Meta webhooks
- HMAC `X-Hub-Signature-256` verified with `hmac.compare_digest` when `REQUIRE_HMAC_VERIFICATION=true` (default); GET verify-token challenge implemented.
- Inbound attribution: phone_number_id → tenant/line via `tenant-routing.yaml`; unrouted numbers are not published.
- Publish failure → dedupe key released and **503** so Meta retries. [CONFIRMED]
- Delivery status webhooks: implemented (`message.status` → persister, monotonic rank `sent<delivered<read`, `failed`). [CONFIRMED]
- Outbound correlation: dispatch publishes `outbound.message.sent` with the Meta `wamid`; statuses match on `(tenant_id, wamid)`. A send that timed out has no wamid → its later status cannot be correlated. [CONFIRMED]

### 2.8 RAG (zuvo-ai-rag)
- Documents stored in `kb_documents.body`; indexer polls `pending` docs (checksum ≠ indexed_checksum), re-chunks, embeds, replaces chunks for that doc in one transaction (no duplicate chunks), records errors and retries on next update. All queries filter `tenant_id` and run under RLS. Agent-worker falls back to lexical retrieval when RAG is down (`e2e_rag --rag-down` PASS). [CONFIRMED]
- Gaps: no explicit `indexing/failed` state surfaced to operators; no metrics; similarity floor 0.60 calibrated on 25+9 golden questions only.

### 2.9 Auth
- JWT HS256, `iss=zuvocloud`, requires `exp`, `tenant_id`, `role`; TTL 8 h. Password login with scrypt hashes; `enforce_tenant` → 403 on cross-tenant. Token accepted from `?access_token=` for SSE (appears in proxy access logs — note for Phase 3). CORS allow-list from `ALLOWED_ORIGINS`.
- No login rate limiting / lockout. [CONFIRMED]

### 2.10 Tests
- Unit: 46 (`tests/unit`: common 7, services 8, phase1 15, rag 6 … + zuvo-ai-rag 16 in-image).
- E2E: `tests/e2e/test_stack.py` (6), `scripts/e2e_phase1.py`, `scripts/e2e_rag.py` — need the running stack.
- Not covered: RLS against real Postgres, Redis-loss behaviour, outbox, outbound timeout/UNKNOWN, backup/restore, compose prod config.

## 3. Assumptions to verify later
- A1: Meta retries webhooks on non-2xx for up to ~7 days (Meta documentation; drives R2's inbound safety).
- A2: A single t4g.medium (2 vCPU / 4 GB) is sufficient with Kafka heap capped at 512 MB (Phase 14).
- A3: Meta Cloud API does not offer an idempotency key for sends; duplicate suppression must be on our side.

## 4. Plan (phases 2–15)
See `docs/production-readiness-checklist.md`. Priority order follows the risk table: R3/R6/R7/R9/R10 (Phase 2–3), R5 (Phase 3 doc), RLS tests (4), R13 (5), R2/R4 (6), R1 (7), R8 (9).
