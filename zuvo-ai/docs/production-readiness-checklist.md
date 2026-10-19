# Production Readiness Checklist

Legend: `[x]` done and verified · `[~]` partial / documented only · `[ ]` open.
"Evidence" points to the test, command or document that proves the item. Unverified items say **NOT VERIFIED**.
Risks R1–R15 refer to `docs/production-readiness-assessment.md`.

## Phase 1 — Assessment
- [x] Repository assessment — `docs/production-readiness-assessment.md`
- [x] Git history secret scan (names/commits only) — R5

## Phase 2 — Compose hardening
- [x] Production override `docker-compose.prod.yml` — `docs/production-deployment.md` §5 (isolated prodtest run)
- [x] Kafka persistent volume + heap cap (R3, R10) — offsets survive `--force-recreate`; RSS 996→388 MB
- [x] Docker log rotation on every service (R9) — json-file 10m×5
- [x] Memory limits sized from measurements (R10) — table in `docs/production-deployment.md`
- [x] Internal network for data plane; only Caddy publishes ports (R6) — Postgres/Redis egress blocked (tested)
- [x] Health checks for consumer services (R12) — heartbeat + `zuvo_common.health`; gateway health still Redis-only (see Phase 5)
- [x] Graceful SIGTERM shutdown (R11) — `test_consume_finishes_in_flight_message_then_stops`; live stop exit 0
- [x] Config validation `scripts/preflight.py` + runtime guard in `get_settings()` — `tests/unit/test_production.py`

## Phase 3 — HTTPS and application security
- [x] Caddy with automatic HTTPS, HTTP→HTTPS, security headers, 1 MB body limit — tested with internal CA; real ACME NOT VERIFIED
- [x] Production refuses `AUTH_DEV_LOGIN`, `ENABLE_SIMULATOR`, `REQUIRE_HMAC_VERIFICATION=false` (R7) — runtime guard + Caddy 404
- [x] Rate limiting: login 10/min, webhook 50/s, API 30/s (nginx) — verified 6×401 then 429
- [x] Security logging for login + learned-example toggles (audit); no PII (email SHA-256 prefix)
- [x] Token leak fixed: SSE `?access_token=` redacted in Caddy, nginx and uvicorn access logs — canary verified
- [x] Secrets via SSM Parameter Store + `deploy/aws/render-env.sh` (offline-tested with stub `aws`; real SSM NOT VERIFIED) — `docs/security.md` §4
- [x] R5 rotation steps documented — `docs/security.md` §6 (**rotation itself is an owner action, NOT DONE**)

## Phase 4 — Multi-tenant security
- [x] RLS isolation tests against real Postgres — `scripts/test_rls.sh` → 39 passed (read ×13 tables, write/move/delete, no-context, scheduler, RAG indexer + search, pooled-connection reuse, rollback); negative control with a BYPASSRLS role → 38 failed
- [x] Privileges: `zuvo_app` non-super, NOBYPASSRLS, owns no tables, no DELETE on business tables (tested)
- [x] `memberships`/`users` exception + remaining limitations documented — `docs/security.md` §5

## Phase 5 — Observability
- [x] Correlation id across events/logs (W3C trace + correlation/causation in Kafka headers); JSON logs in all services incl. zuvo-ai-rag and nginx
- [x] Operational metrics (consumer lag, DLQ, sends, AI tokens/cost, RAG, approvals, PG) — `/internal/metrics`, `docs/observability.md`
- [x] `/livez` vs `/readyz` (503 verified live with Redis stopped)
- [x] Optional CloudWatch log shipping — `deploy/aws/docker-compose.awslogs.yml` (config validated; real AWS NOT VERIFIED)

## Phase 6 — Kafka reliability
- [x] Transactional outbox for DB-then-publish paths (R2) — `docs/kafka-reliability.md`
- [x] Durable processed-events table (R4)
- [x] Retry jitter; transient/permanent split; DLQ metadata; replay tool (R14)
- [x] Gateway status receipts retried by Meta on publish failure (were silently lost)

## Phase 7 — Safe outbound delivery
- [x] Durable outbound ledger `outbound_messages` (PENDING/SENDING/ACCEPTED/FAILED/UNKNOWN), RLS-forced, migration 0005 (R1)
- [x] Error classification (not_sent / rejected / ambiguous / policy); only provably-unsent sends are retried, with backoff
- [x] Timeout / 5xx / crash mid-send → UNKNOWN, never auto-resent; sweeper turns stale SENDING into UNKNOWN
- [x] Status correlation via wamid and `biz_opaque_callback_data`; duplicate and out-of-order webhooks are no-ops
- [x] Manual reconciliation CLI `python -m dispatch.reconcile` + audit; dashboard send-now needs `confirm_unknown`
- [x] Metrics `zuvo_outbound_*`; unit (mock Meta) + integration tests; live UNKNOWN reconcile drill — see `docs/outbound-delivery.md`
- [ ] `biz_opaque_callback_data` echo against the live Meta API — NOT VERIFIED (documented behaviour only)

## Phase 8 — RAG / AI reliability
- [x] Index state machine pending/processing/indexed/failed with claim, backoff, stale-claim recovery (`docs/rag-reliability.md`)
- [x] Atomic version switch; failed re-index keeps previous version serving; deterministic chunk ids
- [x] Embedding validation + DB dimension guard (`/readyz`); categorized, bounded, jittered Gemini retries
- [x] Retrieval 503 with failure category (distinct from "no knowledge"); `document_version` in results
- [x] `agent_runs.outcome` + privacy-conscious `diagnostics` (ids/scores/models/latencies, no text)
- [x] `zuvo-ai-rag /metrics` + opsmetrics index states / agent outcomes
- [x] Golden RAG test set on real pgvector (13) + unit tests; no paid Gemini calls
- [ ] NOT VERIFIED: live Gemini quota exhaustion; live rag_unavailable drill; multi-replica indexer
- [x] ai-router token usage persisted (persister `record_usage`, now ledger-guarded)

## Phase 9 — Backups and DR
- [x] `scripts/backup/{backup-db,verify-backup,restore-db}.sh`: consistent `pg_dump -Fc`, sha256 + manifest, scratch-restore verification, S3 SSE-KMS upload, count retention — `docs/backup-and-restore.md` (R8)
- [x] Policy: 6 h RPO, ≤ 30 min RTO, IAM scoped to bucket/prefix, cron, pg_dump vs PITR limits, monthly restore test
- [x] Restore drills: `--as-new-db` on dev; `--replace` on isolated prodtest (APP_ENV=production): change after backup removed, old DB kept, healthy in 47 s
- [ ] NOT VERIFIED: real S3/KMS/IAM (shim-tested only), cron on the VM, multi-GB restore time

## Phase 10–11 — AWS and deployment
- [x] `deploy/aws/README.md`: measured sizing (≈1.2 GiB idle), EC2 vs Lightsail from AWS Price List API (Mumbai) → **t4g.medium + 2 GB swap ≈ ₹2,670/mo incl. GST**; SG, DNS/HTTPS, instance role, S3, monitoring, resize, teardown
- [x] `deploy/aws/env.production.example` (preflight rejects every `<placeholder>`), `docker-compose.datadir.yml` (tested), immutable image names in `docker-compose.prod.yml`
- [ ] NOT VERIFIED: anything in a real AWS account (no resources created), arm64 build, 4 GB VM memory gate (Phase 14)
- [x] `scripts/deploy/{preflight,deploy,health-check,rollback}.sh` + `docs/deployment.md`: immutable commit tags, flock, mandatory verified backup before pending migrations, advisory-locked migrator, ordered updates, 26 health/smoke checks, release records, schema-guarded rollback, failed-deploy procedure; not zero-downtime (maintenance window stated)
- [x] Rehearsed on prodtest: deploy 0003→0006 (93 s), redeploy, lock (exit 7), rollback (30 s), schema-mismatch refusal, forced health failure + recovery, auto-rollback path
- [ ] NOT VERIFIED: real mid-release migration failure; worktree rebuild of a pruned release; deploy on the AWS VM

## Phase 12–15
- [x] `docs/runbooks/` — 15 runbooks (symptoms, first commands, logs/metrics, safe recovery, approval steps, verification); every command executed on prodtest
- [x] `deploy/ops/zuvo-watch.sh` + `watch_eval.py` (16 unit tests): host, containers, edge, backup age, ops-metrics rules; consecutive-check gating, de-dup, escalation, RESOLVED; SNS and/or chat webhook only, exits 3 if no destination (no pretend alerts); fault-injection tested with a capture webhook
- [x] Operator view = `zuvo-watch.sh --status` over SSH/SSM (IAM-authorized), separate from the tenant dashboard; `scripts/deploy/dcp` pinned compose wrapper
- [ ] NOT VERIFIED: real SNS/email delivery, cron on the VM, external uptime check, CloudWatch metric-filter alarm for HTTP 5xx
- [x] Release gates: `scripts/release-gates.sh` (unit 176, RAG 45, integration 78, compose/ports, secret scan, shellcheck — all PASS); `scripts/deploy/smoke-test.sh` (health-check + TLS/headers, auth 401s, `/internal` not public, Kafka topics, `zuvo_app` role, RLS forced, opsmetrics) PASS on prodtest; negative tests: un-forced RLS on `leads` and a canary key are both detected
- [x] New tests (`tests/unit/test_release_gates.py`, 23): readiness under Redis/Kafka failure, Kafka-down webhook retry, signature tampering, JWT forgery/expiry/alg=none, dashboard route-auth inventory, tenant/role guards, duplicate status receipts with stable event ids
- [x] Persistence: `restart postgres kafka redis` on prodtest → table counts, alembic, Kafka log-end and committed offsets, Redis keys identical; signed inbound persisted 2 s later
- [ ] NOT VERIFIED: release gates in CI (no CI configured), frontend build/test gate, TLS-expiry check against a real certificate
- [x] Load test (`docs/load-testing.md`): Gemini mock (`deploy/loadtest/`) + driver `scripts/loadtest/zuvo_load.py` with safety interlocks (localhost, `META_DRY_RUN`, mock required); idle, startup, steady 1 and 5 msg/s, burst 200, Gemini outage, 10 % Gemini errors, API, RAG, approve → outbound
- [x] Results: peak 1.38 GiB, no OOM kills or restarts, ≤ 20 pg connections; 1 msg/s → first agent run p50 1.8 s; no message lost in the burst or outage; t4g.medium + 2 GB swap confirmed for the pilot
- [x] Findings: per-tenant throughput ≈ 1 / Gemini latency (sequential consumers, `tenant:worker` key; ~1.4 msg/s with the mock, 8× pilot peak) — documented, not changed; Kafka JVM healthcheck every 10 s burned CPU → prod probe every 30 s with `start_interval` (idle 12 % → 7 % of a core); `AI_PENDING` is not reprocessed after an outage → runbook step added
- [ ] NOT VERIFIED: load test on a real 4 GB / 2 vCPU VM, real Gemini latency/quotas, dmesg OOM log, multi-IP traffic
- [x] First install on empty volumes with `deploy.sh` (bug fixed: backup step failed on an empty database); `smoke-test.sh` PASS — `docs/deployment.md` rehearsal table
- [x] `services/migrator/set_password.py` (+9 unit tests): per-user password from a prompt or stdin, never argv; audited; owner login verified on the fresh install
- [x] Final documentation: assessment §0 final status (R1–R15 → fix, evidence, residual risk), `deploy/aws/README.md` §0 clean-VM walkthrough, `production-deployment.md` first install via `deploy.sh`, security (password tool), observability/backup/runbook cross-links
- [ ] Owner actions before go-live: R5 token rotation; run the AWS walkthrough and close its NOT VERIFIED items
