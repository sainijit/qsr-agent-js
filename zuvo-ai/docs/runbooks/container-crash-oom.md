# Runbook: Container crash or out-of-memory kill

> Conventions: run on the VM from `/opt/zuvo` as the deploy user. `D=scripts/deploy/dcp` (compose pinned to the current
> release). `M="$D exec -T dashboard-api python -m zuvo_common.opsmetrics"`. Never use `down -v`. Never paste secrets,
> payloads or phone numbers into tickets or chat.

## Symptoms
- `oom:<svc>`, `restart:<svc>`, `host:memory`, `host:swap` alerts. The service restarts repeatedly.

## First diagnostics
```bash
$D ps -a
docker inspect -f '{{.Name}} oom={{.State.OOMKilled}} exit={{.State.ExitCode}} restarts={{.RestartCount}}' $($D ps -aq)
docker stats --no-stream --format 'table {{.Name}}\t{{.MemUsage}}\t{{.MemPerc}}'
free -m; sudo dmesg -T | grep -i -E 'killed process|out of memory' | tail
```
## Logs and metrics
- `$D logs --tail 200 <svc>`: the last lines before the exit. Exit 137 = SIGKILL (OOM or the limit), 1 = application error, 0 = graceful stop.
- Limits per service are in `docker-compose.prod.yml` (measured values: `production-deployment.md`).

## Safe recovery
1. Application error: read the traceback. A config error names the variable. Fix `.env`, then `$D up -d --no-deps <svc>`.
2. Container OOM (limit reached): raise that service's `mem_limit` in `docker-compose.prod.yml` by about 25% and deploy. Check for a leak if memory grows steadily (`docker stats` over hours).
3. Host OOM (swap > 50%, low available memory): stop non-essential work (builds) and recreate the largest consumer. Plan an instance resize (`deploy/aws/README.md`).

## Requires approval
- Instance resize (cost, downtime of a few minutes), limit changes that exceed the host budget.

## Verify
Restart count stable across 3 watch runs, no new OOM, `free -m` available > 20%.
