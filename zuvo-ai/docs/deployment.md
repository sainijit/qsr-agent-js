# Deployment and rollback (manual, single VM)

Release workflow for the Docker Compose production stack (`docker-compose.yml` + `docker-compose.prod.yml`).
For first-time server setup see `production-deployment.md` and `deploy/aws/README.md`. For backups see `backup-and-restore.md`.

```bash
cd /opt/zuvo
git fetch --tags && git checkout --detach <release-tag-or-commit>
scripts/deploy/deploy.sh                 # preflight → build → backup → migrate → update → health → record
scripts/deploy/rollback.sh --list        # releases on this host
scripts/deploy/rollback.sh               # back to the previous release (application only)
scripts/deploy/health-check.sh           # at any time; read-only
scripts/deploy/smoke-test.sh             # health-check + TLS/headers, auth, exposure, Kafka topics, DB role/RLS, metrics
scripts/deploy/dcp ps                    # docker compose pinned to the current release (use instead of plain compose)
```

For manual operations always use `scripts/deploy/dcp`. Plain `docker compose` without `IMAGE_TAG` would use the `local` tag.
For a single service use `up -d --no-deps <svc>`. The one-shot `migrator` container is re-created by every deploy/rollback, so a
dependency re-run never executes an older release's migrations (found in rehearsal and fixed).

| Script | Purpose | Exit codes |
|---|---|---|
| `preflight.sh [--local-rehearsal]` | tools, Compose ≥ 2.24, `.env` mode 600 and production rules (`scripts/preflight.py`, names only, never values), `compose config`, only Caddy publishes ports, git state, disk ≥ 5 GB, memory/swap, backup destination. No changes. | 0 ok, 2 errors |
| `deploy.sh` | the release (below) | 0, 2 preflight/usage, 3 build, 4 backup, 5 migration, 6 update/health, 7 locked |
| `health-check.sh [--expect-tag T] [--expect-alembic R]` | container state/health/OOM/image tag, `/readyz` of gateway, dashboard-api and zuvo-ai-rag, alembic revision, edge HTTPS, smoke tests | 0, 1 failed |
| `smoke-test.sh [--expect-tag T] [--expect-alembic R] [--skip-health]` | `health-check.sh` plus TLS cert ≥ 14 days, security headers, no `Server` header, wrong-password login / missing / forged JWT → 401, `/internal/metrics` not public, all Kafka topics + DLQs exist, app connects as `zuvo_app` (no superuser/BYPASSRLS), RLS enabled+forced on every `tenant_id` table (except `memberships`, read at login), opsmetrics all sections ok. Read-only. | 0, 1 failed |
| `rollback.sh [--to previous\|TAG] [--accept-schema] [--list]` | restart a previously deployed release's images; never touches the schema | 0, 2 refused, 3 images, 6 unhealthy, 7 locked |

Configuration (environment): `COMPOSE_PROJECT_NAME` (default `zuvo-ai`), `ZUVO_ENV_FILE` (default `.env`),
`ZUVO_COMPOSE_EXTRA` (e.g. `deploy/aws/docker-compose.datadir.yml`), `ZUVO_STATE_DIR` (default `.deploy/`),
`ZUVO_KEEP_RELEASES` (default 3), `ZUVO_WAIT_TIMEOUT` (per group, default 240 s). `HEALTH_URLS` and backup
settings (`BACKUP_DIR`, `BACKUP_S3_URI`, `BACKUP_KMS_KEY_ID`) are read from the environment or `.env`.

## What `deploy.sh` does

1. **Lock**: `flock` on `.deploy/.deploy.lock`. A second deploy or rollback exits 7 immediately.
2. **Release identity**: tag = `git rev-parse --short=12 HEAD`. Images are `zuvo/<service>:<tag>` (`IMAGE_TAG`), and the
   running version (`ZUVO_VERSION`) is the same tag. A dirty tree is refused. `--allow-dirty` is for rehearsals only
   (tag `<sha>-dirty<epoch>`, cannot be rebuilt after it is pruned).
3. **Preflight**: any error stops before anything changes.
4. **Build** the 11 application images (skipped if all exist for the tag; `--rebuild` forces it, `--pull` refreshes base images).
   Images are built on the VM; there is no registry yet (ECR is an option, not configured).
5. **Data services**: `up --no-recreate` for postgres/redis/kafka, so a running database is never recreated by a release. kafka-init is idempotent.
6. **Backup**: `migrator --status` compares the DB revision with the code's head.
   - Migrations pending → a backup with **full restore verification is mandatory**. `--skip-backup` is ignored, and a failure exits 4 before any change.
   - Nothing pending → quick-verified backup (`--skip-backup` allowed).
   - First install (no `alembic_version` **and** zero tables) → no backup (there is no data). A database without
     `alembic_version` that has tables is unexpected and exits 4.
7. **Migrate**: `docker compose run --rm migrator` (alembic upgrade + tenant bundles). The migrator holds a Postgres
   advisory lock, so concurrent migrators wait (default 300 s) or exit 3. They never run twice in parallel. Each migration is its own transaction.
8. **Update** in order, each group waited until healthy (`up -d --no-deps --wait`):
   `zuvo-ai-rag` → Kafka consumers (persister, ai-router, lead-service, agent-worker, followup-scheduler, dispatch) →
   `dashboard-api gateway` → `frontend caddy`.
9. **Verify**: `health-check.sh --expect-tag <tag> --expect-alembic <rev>`. Any failure → exit 6, and the release is **not** recorded as current.
10. **Record**: `.deploy/releases/<tag>.json` (tag, commit, alembic before/after, backup file, times, user),
    `.deploy/history.jsonl` (every success and failure), `.deploy/current`, `.deploy/previous`. Then prune old
    `zuvo/*` images, keeping current, previous and the newest 3 releases.

Volumes are never removed. `down -v` is never used. The database is never rolled back automatically.

### Downtime: not zero-downtime

Each service runs as one container, and an update recreates it. Expect a few seconds of unavailability per group
(measured: about 6–10 s per group, about 30 s for a full update). What happens during that window:
- **Webhooks**: Meta retries non-2xx deliveries, and the gateway idempotency key deduplicates the retries.
- **Kafka consumers**: they stop gracefully (SIGTERM → finish and commit) and resume from committed offsets.
- **Outbound**: the dispatch ledger prevents duplicate sends.
- **Dashboard and frontend**: they show errors briefly.

**Deploy in a quiet window** (for example 23:00–06:00 IST) and tell the pilot tenants in advance. True zero-downtime
would need two replicas behind Caddy plus backward-compatible migrations, which is not implemented.

### Smoke tests in `health-check.sh`
- Edge `GET /api/v1/health` → 200 with `status=ok`, and `GET /` → 200 (or `HEALTH_URLS`).
- `http://` → 308 to https.
- WhatsApp webhook verify handshake echoes the challenge. The token is sent via stdin, never in argv or logs.
- Unsigned webhook POST → 401/403.
- `/api/v1/auth/dev-login`, `/api/v1/messages/simulate-inbound` and `/docs` → 404.

Run `smoke-test.sh` after every deploy or rollback, and after any manual infrastructure change.

## Release gates (before tagging a release)
`scripts/release-gates.sh` runs on a dev machine or CI, never on the production VM:

| Gate | What | Evidence 2026-10-19 |
|---|---|---|
| unit | `tests/unit`: webhook signature tampering, JWT forgery/expiry/alg=none, dashboard route inventory (every route needs a JWT except an explicit allowlist), tenant/role guards, `/readyz` 503 when Redis/Kafka down with `/livez` 200, Kafka-down webhook → 503 and Meta retry not swallowed, duplicate/out-of-order status receipts, outbox, ledger, dispatch state machine, watch evaluator | 176 passed |
| rag | `zuvo-ai-rag/tests` | 45 passed |
| integration | `scripts/test_rls.sh` against the dev Postgres: RLS, cross-tenant RAG, outbound idempotency and monotonic status (`apply_status`), golden RAG | 78 passed |
| compose | merged prod compose renders with `deploy/aws/env.production.example`; only Caddy publishes ports | pass |
| secrets | credential patterns and literal secret assignments in every committable file (names only, values never printed); `.env` ignored and untracked | pass (canary key detected in negative test) |
| shellcheck | all deploy/backup/ops/aws scripts | pass |

`--fast` skips integration (use as a pre-commit hook). A skipped gate is reported as SKIPPED, never as passed.

## Rollback

`rollback.sh` restarts the application images of a release that was **successfully deployed** on this host. It uses that
commit's compose files (via a git worktree under `.deploy/worktrees/`) and rebuilds the images if they were pruned
(not possible for `-dirty` tags). It then runs the same ordered update and health check, and records `rolled_back`.

**Schema rule**: the rollback is refused (exit 2) when the live alembic revision differs from the one the target release
ran with. Code rollback does not undo migrations. Use `--accept-schema` only when every migration in between is
additive and old code tolerates it (our convention: expand → deploy → contract in a later release). Otherwise:
- fix forward (preferred), or
- restore the pre-release backup recorded in `.deploy/releases/<tag>.json` (`backup-and-restore.md` §5c). This loses
  data written after the backup, so it **requires approval**.

`deploy.sh --auto-rollback` restarts the previous release automatically when update or health fails **and** the schema did
not change. With a schema change it never auto-rolls back. It prints the command and leaves the decision to the operator.

## Failed deployment: recovery procedure

| Exit | State of the system | Action |
|---|---|---|
| 2 preflight | nothing changed | fix the reported variable or file, re-run |
| 3 build | nothing changed (old containers running) | read the build error; `docker system df` (disk); re-run |
| 4 backup | nothing changed | fix backup (disk, S3 permissions; `backup-and-restore.md`); never deploy migrations without it |
| 5 migration | old app containers still running; schema may be at an intermediate revision (printed) | read `docker compose run --rm migrator` output; fix forward and re-run deploy. If data is damaged, restore the backup (approval). Old code against a partially migrated schema: check that it is additive before leaving it running |
| 6 update/health | some services on the new tag, possibly unhealthy | read the `FAIL` lines; `dcp logs --tail 200 <svc>`; then `rollback.sh` (application) or fix forward. If the schema changed, see the schema rule above |
| 7 locked | another deploy/rollback is running | wait. If no deploy process is running (`pgrep -f deploy.sh`), the lock is released automatically (flock) |

Always finish with `scripts/deploy/health-check.sh` (exit 0) and `rollback.sh --list` to confirm what is current.
After an incident, write the cause in the history: the failed entry already contains step, message and time.

## Rehearsal evidence (isolated `zuvo-prodtest` project, APP_ENV=production, 2026-10-19)

| Drill | Result |
|---|---|
| **First install on empty volumes** (separate project `zuvo-fresh`, new secrets) | first attempt exit 4: the backup queried `alembic_version` on an empty database (bug, fixed above). After the fix: 65 s, schema none → 0006, all groups healthy; `smoke-test.sh` all passed; `set_password.py` → owner login 200, wrong password 401. Project, volumes and images removed afterwards |
| Preflight `--local-rehearsal` | 0 errors (expected warnings: localhost domain, dirty tree, proxy, no S3) |
| Deploy from schema 0003 (old images) | 93 s: build, **full-verified** backup, migrate 0003 → 0006, 4 groups healthy, 26/26 health checks |
| Second deploy (no pending migrations) | quick-verified backup, update, all checks passed; previous recorded |
| Concurrent deploy | exit 7 (locked), no changes |
| `rollback.sh` to previous | 30 s, all containers on the previous tag, health passed, `rolled_back` recorded |
| Rollback to a release with another schema | refused, exit 2 |
| Rollback to an unknown tag | refused, exit 2 |
| Forced health failure (bad `HEALTH_URLS`) | exit 6, `failed` recorded, rollback command printed; manual rollback healthy |
| `--auto-rollback` with forced failure | rollback executed automatically; its health check failed too because the injected fault persists → exit 6 with the runbook pointer (expected) |

NOT VERIFIED: a real migration failure mid-release, image rebuild of a pruned release from a git worktree, deploy on the
AWS VM (Let's Encrypt, real domain, arm64), and behavior under live webhook traffic during the update window.
