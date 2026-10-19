# Production deployment (single VM, Docker Compose)

Applies to the 2–5 tenant pilot (`docs/HLD.md` §deployment). AWS specifics are in `deploy/aws/README.md`.

```bash
cp .env.example .env && chmod 600 .env       # fill real values (see "Required configuration")
python3 scripts/preflight.py --compose        # must report 0 errors; prints variable names only
scripts/deploy/deploy.sh                       # first install and every release (empty DB: no backup needed)
scripts/deploy/smoke-test.sh
scripts/deploy/dcp run --rm --no-deps migrator python set_password.py owner@<tenant-domain>
```

Use `scripts/deploy/deploy.sh` for the first install too (verified on empty volumes, `docs/deployment.md` "Rehearsal evidence"):
it builds tagged images and records the release, so `dcp`, `rollback.sh` and the backup cron work from day one.
The ordered clean-VM walkthrough is `deploy/aws/README.md` §0. Releases: `deploy.sh` / `rollback.sh` / `health-check.sh` (`docs/deployment.md`).

Always pass **both** files (`alias dcp='docker compose -f docker-compose.yml -f docker-compose.prod.yml'`).
Requires Docker Compose ≥ 2.24 (uses `!reset` / `!override`). Never use `down -v` — it deletes Postgres, Kafka and certificate volumes.

## 1. What the production override changes

| Concern | Dev (`docker-compose.yml`) | Production (`+ docker-compose.prod.yml`) |
|---|---|---|
| Public ports | gateway `:8000`, frontend `:3000`, dashboard-api `127.0.0.1:8001` | **only Caddy `:80`/`:443`** (HTTP→HTTPS redirect, Let's Encrypt) |
| Networks | one bridge `zuvo` | `edge` (caddy↔frontend), `zuvo` (apps, internet egress for Gemini/Meta), `data` (**internal**, no internet: Postgres/Redis/Kafka) |
| Kafka | ephemeral, default 1 GB heap | volume `kafka_data`, heap `-Xmx512m`, 16 MB segments, 32 MB/partition + 7-day retention |
| Redis | AOF | AOF `everysec`, `maxmemory 96mb`, `noeviction` (idempotency keys are never silently evicted) |
| Safety guard | `APP_ENV=development` | `APP_ENV=production` forced → `get_settings()` refuses dev login, simulator, `REQUIRE_HMAC_VERIFICATION=false`, JWT secret < 32 chars, missing per-service secrets |
| Logs | unbounded json-file | json-file `10m × 5` per container (≤ 50 MB each) |
| Liveness | HTTP services only | + consumers: `python -m zuvo_common.health` (heartbeat file from the poll loop, stale > 180 s = unhealthy) |
| Shutdown | task cancellation | SIGTERM → finish + commit the in-flight message → exit 0; `stop_grace_period` 30 s (Kafka/Postgres 60 s) |
| Container hardening | non-root uid 10001 | + read-only root FS, `/tmp` tmpfs, `cap_drop: ALL`, `no-new-privileges` |
| Edge security | none | HSTS, CSP, `X-Frame-Options DENY`, nosniff, 1 MB body limit, dev endpoints → 404, `access_token` stripped from access logs |

### Memory limits (measured, not uniform)
Measured with `docker stats` on the running prod-override stack (2 tenants, idle + smoke traffic):

| Service | Measured RSS | Limit | Rationale |
|---|---|---|---|
| kafka | 388 MB (996 MB before heap cap) | 900m | 512 MB heap + metaspace/page cache headroom |
| postgres | 51 MB | 512m | shared_buffers 128 MB default + work_mem bursts (RAG vector search) |
| dashboard-api, zuvo-ai-rag | 71 / 95 MB | 384m | SSE fan-out; embedding responses |
| other Python services | 50–86 MB | 256m | ~3× headroom |
| redis / caddy / frontend | 5 / 14 / 13 MB | 128m / 128m / 64m | |
| **total** | **≈ 1.1 GB** | ceilings sum ≈ 4.3 GB | limits are ceilings, not reservations; 4 GB VM leaves ~2.5 GB free at steady state |

A container exceeding its limit is OOM-killed and restarted (`unless-stopped`); check `docker inspect -f '{{.State.OOMKilled}}'`.

### Deliberate exceptions
- `frontend` (stock `nginx:alpine`) keeps a root master process: it binds port 80 inside the container and is reachable only from Caddy on `edge`/`zuvo`. Switching to `nginx-unprivileged` is a possible follow-up.
- `postgres`, `redis`, `kafka` are not `read_only`/`cap_drop`: their entrypoints `chown` data directories at start.
- `caddy` has no health check (admin API disabled); its availability is covered by the external HTTPS check (`docs/runbooks`).

## 2. Required configuration
`scripts/preflight.py` is the source of truth. Production needs (names only):
`APP_ENV=production`, `POSTGRES_PASSWORD`, `ZUVO_APP_DB_PASSWORD` (≥16), `JWT_SECRET`, `RAG_SERVICE_TOKEN` (≥32),
`META_VERIFY_TOKEN`, `META_APP_SECRET`, `META_SYSTEM_USER_TOKEN`, `GEMINI_API_KEY`, `PUBLIC_DOMAIN`, `ACME_EMAIL`,
`ALLOWED_ORIGINS=https://<PUBLIC_DOMAIN>`, `AUTH_DEV_LOGIN=false`, `ENABLE_SIMULATOR=false`, `REQUIRE_HMAC_VERIFICATION=true`.
`META_DRY_RUN=true` is allowed (warning) for a soft launch. `ZUVO_ONBOARD_DEFAULT_PASSWORD` should be unset (weak values are errors).
Generate secrets with `openssl rand -hex 32`. How they reach the VM: `docs/security.md`.

## 3. Startup sequence
Compose enforces it through `depends_on` conditions:
1. `postgres`, `redis`, `kafka` → healthy.
2. `migrator` (role `zuvo_app` → `alembic upgrade head` → tenant bundles) and `kafka-init` (`--if-not-exists`, idempotent) → exit 0.
3. Application services start; `gateway`, `dashboard-api` HTTP health → healthy; consumers write their first heartbeat once subscribed.
4. `frontend` → healthy, then `caddy` (obtains/renews the certificate on first start; DNS must already point at the VM and ports 80/443 must be open).

Verify: `dcp ps` shows every long-running service `healthy` (caddy `Up`), and `curl -fsS https://$PUBLIC_DOMAIN/api/v1/health`.

## 4. Recovery
| Situation | Action |
|---|---|
| VM reboot | Docker restarts everything (`unless-stopped`); ordering re-applies. Consumers resume from committed offsets. |
| One service crash-looping | `dcp logs --tail 100 <svc>`; an `UnsafeProductionConfig` line names the bad variable. Fix `.env`, `dcp up -d <svc>`. |
| Consumer `unhealthy` | wedged handler or lost broker: `dcp restart <svc>` (graceful; at-least-once redelivery is safe). |
| Kafka recreated / restarted | data and offsets persist in `kafka_data` (verified: offsets and 27 topics survive `--force-recreate`); consumers reconnect automatically. |
| Disk filling | log rotation caps containers at 50 MB each; check `docker system df`, Kafka volume, Postgres volume. |
| Database restore | `docs/backup-and-restore.md` §5 (`scripts/backup/restore-db.sh`; never automatic). |

## 5. Verification evidence (this change)
Run on the dev host as an isolated project (`-p zuvo-prodtest`, ports 18080/18443, `PUBLIC_DOMAIN=localhost` → Caddy internal CA):
- all 13 long-running services healthy under read-only root FS;
- HTTP → 308 HTTPS; HSTS/CSP/XFO/nosniff present, no `Server` header; simulator & dev-login → 404; 2 MB body → 413; unsigned webhook → 401;
- signed webhook through Caddy → persisted once (duplicate absorbed) → Gemini extraction → RAG retrieval → approval;
- `docker compose stop persister|followup-scheduler` → `SIGTERM received` log, exit 0 in < 1 s;
- `AUTH_DEV_LOGIN=true` with `APP_ENV=production` → `UnsafeProductionConfig` (service refuses to start);
- Kafka `--force-recreate` → committed offsets and topics preserved; new webhook processed afterwards;
- Postgres/Redis containers cannot reach the internet.
NOT VERIFIED: real Let's Encrypt issuance (needs a public DNS name), behaviour on an actual 4 GB ARM VM (Phase 14).
