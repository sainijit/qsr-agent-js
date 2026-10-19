# Runbook: Application unavailable

> Conventions: run on the VM from `/opt/zuvo` as the deploy user. `D=scripts/deploy/dcp` (compose pinned to the current
> release). `M="$D exec -T dashboard-api python -m zuvo_common.opsmetrics"`. Never use `down -v`. Never paste secrets,
> payloads or phone numbers into tickets or chat.

## Symptoms
- `edge:health` / `container:<svc>` / `metrics:collect` alert; `https://<domain>/api/v1/health` not 200; tenants cannot log in; webhooks failing in Meta.

## First diagnostics
```bash
scripts/deploy/health-check.sh            # which layer fails: container / readyz / edge / smoke
$D ps -a                                  # state, health, restarts
deploy/ops/zuvo-watch.sh --status
df -h / /var/lib/docker; free -m          # disk or memory exhaustion are the usual root causes
```
## Logs and metrics
- `$D logs --tail 200 caddy frontend gateway dashboard-api`
- Caddy certificate problems: `$D logs caddy | grep -i -E 'acme|certificate|tls'`
- Security group / DNS: `dig +short <domain>` must equal the Elastic IP.

## Safe recovery
1. Only one service down: `$D up -d --no-deps <svc>` and watch `$D logs -f <svc>`. An `UnsafeProductionConfig` line names the bad `.env` variable.
2. A data service is down: postgres → `postgres-down-or-disk-full.md`; redis → `redis-unavailable.md`; kafka → `kafka-consumer-lag.md`.
3. Disk full or OOM: `postgres-down-or-disk-full.md` / `container-crash-oom.md`.
4. Started right after a deploy: `failed-deployment.md`.
5. Whole stack stopped (e.g. after a reboot with Docker not enabled): `sudo systemctl enable --now docker`, then `$D up -d`.

## Requires approval
- Rolling back a release, restoring a backup, resizing the instance, changing DNS or the security group.

## Verify
`scripts/deploy/health-check.sh` exits 0, `zuvo-watch.sh --status` shows OK, and a test WhatsApp message gets a reply or an approval card.
