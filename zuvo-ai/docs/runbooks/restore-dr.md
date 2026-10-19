# Runbook: Restore and disaster recovery

> Conventions: run on the VM from `/opt/zuvo` as the deploy user. `D=scripts/deploy/dcp` (compose pinned to the current
> release). `M="$D exec -T dashboard-api python -m zuvo_common.opsmetrics"`. Never use `down -v`. Never paste secrets,
> payloads or phone numbers into tickets or chat.

## When
- Data corruption or accidental deletion, Postgres will not start, or loss of the VM or volume.

## First diagnostics
```bash
scripts/deploy/rollback.sh --list                  # what release and schema were running
ls -lt /var/backups/zuvo | head; aws s3 ls "$BACKUP_S3_URI/" --recursive | tail
```
## Procedure
Follow `docs/backup-and-restore.md` (§5 restore, §6 full VM rebuild). Summary:
1. Pick the backup: the newest verified one before the incident. Run `verify-backup.sh --file <f>`.
2. Prefer a side-by-side check first: `restore-db.sh --file <f> --as-new-db zuvo_check --confirm` and inspect.
3. Replace: stop the apps, then `restore-db.sh --file <f> --replace --confirm --production`. The old DB is kept as `zuvo_db_pre_restore_<ts>`, and a safety backup is written.
4. Before starting dispatch, review outbound messages created after the backup (`dispatch.reconcile list --status PENDING`). Messages may be resent or lost.
5. Deploy the release that matches the backup's alembic revision (`rollback.sh --to <tag>` or `deploy.sh`), then run `health-check.sh`.
6. VM lost: new instance (`deploy/aws/README.md`), render `.env` from SSM, copy the backup from S3, `restore-db.sh`, `deploy.sh`.

## Requires approval
- **Every restore** (it discards data written after the backup: RPO up to 6 h), and telling tenants what was lost.

## Verify
`health-check.sh` exit 0, row counts match the backup manifest, tenants confirm recent data, and the next scheduled backup succeeds.
