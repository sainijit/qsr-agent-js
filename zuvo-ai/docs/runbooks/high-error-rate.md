# Runbook: High HTTP error rate

> Conventions: run on the VM from `/opt/zuvo` as the deploy user. `D=scripts/deploy/dcp` (compose pinned to the current
> release). `M="$D exec -T dashboard-api python -m zuvo_common.opsmetrics"`. Never use `down -v`. Never paste secrets,
> payloads or phone numbers into tickets or chat.

## Symptoms
- Tenants report errors in the dashboard. CloudWatch Logs Insights (`docs/observability.md` §4) shows nginx 5xx > 2% for 10 min, or 4xx spikes.
- Note: there is no automatic HTTP error-rate alarm (no metrics backend). It is found through tenant reports or a scheduled Logs Insights query.

## First diagnostics
```bash
$D logs --since 15m frontend | grep -c '"status": 5'               # nginx JSON lines
$D logs --since 15m dashboard-api gateway | grep '"level": "ERROR"' | tail -20
scripts/deploy/health-check.sh
```
## Logs and metrics
- Use the `request_id` from the response header `x-request-id`, then `$D logs dashboard-api | grep <request_id>`.
- 502/504 from nginx = upstream (dashboard-api) restarting or slow. Check `$D ps dashboard-api` restarts and the DB (`zuvo_pg_connections`).
- 401 spike = JWT secret changed or tokens expired. 403 = tenant RBAC. 429 = rate limiting (expected under abuse).

## Safe recovery
1. One endpoint failing after a deploy: `failed-deployment.md` (roll back the application).
2. DB saturation (`pg_connections` alert): restart the noisy consumer `$D up -d --no-deps --force-recreate <svc>`.
3. Abuse or scanning from one IP: block it in the AWS security group / NACL (approval).

## Requires approval
- Rollback, security group changes, rotating `JWT_SECRET` (logs every user out).

## Verify
The 5xx count returns to baseline for 15 min, and `health-check.sh` exits 0.
