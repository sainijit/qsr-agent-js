# Runbook: PostgreSQL unavailable or disk nearly full

> Conventions: run on the VM from `/opt/zuvo` as the deploy user. `D=scripts/deploy/dcp` (compose pinned to the current
> release). `M="$D exec -T dashboard-api python -m zuvo_common.opsmetrics"`. Never use `down -v`. Never paste secrets,
> payloads or phone numbers into tickets or chat.

## Symptoms
- `disk:<path>` (≥ 80% warning, ≥ 90% critical), `collect:postgres`, `metric:pg_connections`, `container:postgres` alerts.
- dashboard-api `/readyz` → 503 `database`. Consumers crash-loop with connection errors.

## First diagnostics
```bash
df -h /var/lib/docker /var/backups/zuvo; docker system df
$D ps postgres; $D logs --tail 100 postgres
$D exec -T postgres psql -U zuvo_owner -d zuvo_db -c "select count(*), state from pg_stat_activity group by 2"
$D exec -T postgres psql -U zuvo_owner -d zuvo_db -c "select pg_size_pretty(pg_database_size('zuvo_db'))"
```
## Safe recovery (disk)
1. Remove only disposable data: `docker image prune -f` (dangling images), `docker builder prune -f` (build cache), and old local backups beyond retention (`ls -lt /var/backups/zuvo`; keep at least 2 verified ones, and S3 must hold copies).
2. Container logs are capped at 50 MB each. Do not delete files under the docker root by hand.
3. Grow the EBS volume (`deploy/aws/README.md` resize section): this is online and needs no downtime.

## Safe recovery (Postgres down)
1. `$D up -d --no-deps postgres` and wait healthy. The consumers reconnect on their own; if not, `$D up -d --no-deps --force-recreate <svc>`.
2. "No space left on device" in the logs: free space first (above), then start Postgres. **Never delete files in the Postgres volume.**
3. Corruption / will not start after space is freed: `restore-dr.md`.
4. Connection saturation: find the service holding the connections (`pg_stat_activity` → `application_name`/`client_addr`), then `$D up -d --no-deps --force-recreate <svc>`.

## Requires approval
- Volume resize (cost), deleting any backup, `VACUUM FULL`, restore.

## Verify
Disk < 75%, `zuvo-watch.sh --status` OK, `health-check.sh` exit 0, `readyz dashboard-api` shows `database: ok`.
