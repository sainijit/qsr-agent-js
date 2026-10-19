# Runbook: Failed deployment and rollback

> Conventions: run on the VM from `/opt/zuvo` as the deploy user. `D=scripts/deploy/dcp` (compose pinned to the current
> release). `M="$D exec -T dashboard-api python -m zuvo_common.opsmetrics"`. Never use `down -v`. Never paste secrets,
> payloads or phone numbers into tickets or chat.

## Symptoms
- `deploy.sh` exited non-zero, or alerts started right after a release.

## First diagnostics
```bash
scripts/deploy/rollback.sh --list        # current / previous / failed entries with step and message
scripts/deploy/health-check.sh
$D ps -a; $D logs --tail 200 <failing service>
```
## Safe recovery
Use the exit code table in `docs/deployment.md` "Failed deployment: recovery procedure":
- 2/3/4: nothing was changed. Fix the cause and re-run.
- 5 (migration): old application still running. Read the migrator output. Fix forward with a corrected migration; restoring needs approval.
- 6 (update/health): `scripts/deploy/rollback.sh` (to previous). If it refuses because of a schema difference, check that the new migrations are additive before using `--accept-schema`; otherwise fix forward.
- 7: another deploy is running. Wait.

## Requires approval
- `--accept-schema` rollbacks, restoring the pre-release backup (`restore-dr.md`), deploying outside the maintenance window.

## Verify
`health-check.sh` exit 0, and `rollback.sh --list` shows the expected current release. Record the incident (the `history.jsonl` entry plus the cause).
