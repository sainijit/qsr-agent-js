# Runbook: Backup failure

> Conventions: run on the VM from `/opt/zuvo` as the deploy user. `D=scripts/deploy/dcp` (compose pinned to the current
> release). `M="$D exec -T dashboard-api python -m zuvo_common.opsmetrics"`. Never use `down -v`. Never paste secrets,
> payloads or phone numbers into tickets or chat.

## Symptoms
- `backup:age` (no verified backup newer than `BACKUP_MAX_AGE_HOURS`, default 8 h) or `backup:unverified` (a backup failed restore verification).

## First diagnostics
```bash
ls -lt /var/backups/zuvo | head
grep -E 'ERROR|backup complete' /var/log/zuvo/backup.log | tail -20     # cron output (docs/backup-and-restore.md)
crontab -l | grep backup-db
```
## Logs and metrics
- `backup-db.sh` exit codes: 1 dump failed, 2 usage/environment, 3 verification failed, 4 upload failed, 5 another backup running.

## Safe recovery
1. Run it by hand: `scripts/backup/backup-db.sh --s3 "$BACKUP_S3_URI" --kms-key "$BACKUP_KMS_KEY_ID"` and read the error.
2. Exit 4 (S3): check the instance role (`aws sts get-caller-identity`), bucket policy and KMS key permissions. The local copy is kept.
3. Exit 3 (verification): keep `*.unverified` for analysis. Check disk space and that Postgres is healthy, then re-run.
4. Disk full: `postgres-down-or-disk-full.md`.
5. Do not deploy migrations until a verified backup exists (`deploy.sh` enforces this).

## Requires approval
- Deleting backups, changing retention or the S3 lifecycle, IAM/KMS changes.

## Verify
A new `zuvo_db-*.dump` with `.sha256` and manifest, `verify-backup.sh --file <it>` passes, the S3 object exists (`aws s3 ls`), and the `backup:age` alert resolves.
