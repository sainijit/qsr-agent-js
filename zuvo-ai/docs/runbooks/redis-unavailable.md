# Runbook: Redis unavailable

> Conventions: run on the VM from `/opt/zuvo` as the deploy user. `D=scripts/deploy/dcp` (compose pinned to the current
> release). `M="$D exec -T dashboard-api python -m zuvo_common.opsmetrics"`. Never use `down -v`. Never paste secrets,
> payloads or phone numbers into tickets or chat.

## Symptoms
- `container:redis` alert. Gateway `/readyz` → 503 `{"redis": false}`. Webhook POSTs return 5xx (the idempotency claim fails), so Meta retries and **no message is lost** while Meta keeps retrying.

## First diagnostics
```bash
$D ps redis; $D logs --tail 100 redis
$D exec -T redis redis-cli ping; $D exec -T redis redis-cli info memory | grep -E 'used_memory_human|maxmemory_human'
```
## Logs and metrics
- `OOM command not allowed`: maxmemory (96 MB, `noeviction`) reached. Keys are idempotency claims with a TTL. Check `redis-cli dbsize`.
- AOF errors at startup: a truncated AOF after a crash or a full disk.

## Safe recovery
1. `$D up -d --no-deps redis`. When healthy, the gateway readiness recovers by itself (`$D up -d --no-deps --force-recreate gateway` if not).
2. Memory full: raise `maxmemory` in `docker-compose.prod.yml` in a release (it is not dangerous to wait: Meta retries). Do **not** switch to an eviction policy, because it silently drops idempotency keys.
3. AOF corrupt: `$D run --rm --no-deps redis redis-check-aof --fix /data/appendonlydir/<file>` (approval).
4. Losing Redis data is tolerable: dedupe is also enforced downstream by deterministic event ids and the processed-event ledger.

## Requires approval
- `redis-check-aof --fix`, `FLUSHALL`, changing memory limits.

## Verify
`redis-cli ping` → PONG, `readyz gateway` ok, the webhook verify smoke test passes, and new messages arrive in the dashboard.
