# Backup and restore (Phase 9)

Scripts: `scripts/backup/` — `backup-db.sh`, `verify-backup.sh`, `restore-db.sh` (shared helpers in `lib.sh`).
They work with any compose project and locate Postgres by its compose labels, so they don't need the compose files.

On a first install the database is empty, so `deploy.sh` takes no pre-migration backup; install the cron (§4) right after it
(`deploy/aws/README.md` §0 step 12). Every later release with pending migrations requires a fully verified backup.

## 1. What is backed up and what isn't

| State | Where | Backed up? | Why |
|---|---|---|---|
| Postgres `zuvo_db` (tenants, conversations, leads, approvals, audit, KB + vectors, outbox, ledgers) | `postgres_data` volume | **yes** — `pg_dump -Fc` | the only system of record |
| Redis | `redis_data` | no | dedupe cache only (24 h TTL); the DB ledger `processed_events` is authoritative |
| Kafka | `kafka_data` | no | transport; unpublished events live in `outbox_events` in the DB |
| Caddy certificates | `caddy_data` | no | re-issued automatically by Let's Encrypt |
| Tenant bundles, code | git | git | `tenants/` is re-applied by the migrator |
| Secrets (`.env`) | SSM Parameter Store | SSM | rendered by `deploy/aws/render-env.sh`; never in a backup file |
| Postgres global roles | — | **no** (deliberately) | would contain password hashes; the restore creates `zuvo_app` NOLOGIN and the migrator resets its password from `.env` |

## 2. Policy

| Item | Value |
|---|---|
| Method | `pg_dump --format=custom` via `docker exec` against the live DB: one MVCC snapshot, consistent, no downtime. No password on the command line (the local socket inside the container). |
| Frequency | every 6 h with `--quick-verify` + one daily full verification (cron below) |
| **RPO** | **≤ 6 h** (the data written since the last successful upload is lost) |
| **RTO** | **≤ 30 min** target: S3 download + verify + restore + migrator + start. Measured on the dev host: 47 s from stopping the apps to all services healthy (135 KB DB, file already local). For a larger DB, expect restore time to grow roughly linearly with size. |
| Local retention | newest 14 verified dumps (`--keep`, minimum 2: the only known-good backup is never pruned); `*.unverified` deleted after 7 days |
| Off-host retention | S3 lifecycle: expire `prod/db/` objects after 30 days (≈ 150 dumps at 5/day), noncurrent versions after 7 days; configured on the bucket (`deploy/aws/README.md` §6), not by the script |
| Encryption | in transit: TLS (aws cli). At rest: SSE-KMS (`--kms-key`, customer-managed key); bucket also enforces default encryption. The local copy relies on the EBS volume encryption. |
| Integrity | `.sha256` beside every dump; a `.manifest.json` with the alembic version, pgvector version, RLS/policy counts and row counts of the restored copy |
| Naming | `zuvo_db-<project>-<UTCstamp>.dump` — never overwritten (written as `.partial`, renamed on success) |
| Concurrency | `flock` on `<dir>/.backup.lock` (exit 5 if another backup is running) |

`backup-db.sh` exit codes: 0 ok · 1 dump failed · 2 usage/environment · 3 verification failed · 4 upload failed (local copy kept) · 5 locked.
It always prints `backup complete: file=… verification=full|quick upload=ok|skipped pruned=N`, and a WARNING when there is no
`--s3` (the backup then lives only on this VM).

Verification (`full`, the default) restores the dump into a throw-away Postgres container (`--network none`, tmpfs data, label
`zuvo.purpose=restore-check`, always removed) and requires: the alembic version equals the live one, pgvector is present,
forced-RLS tables > 0, policies > 0 and every expected table present. Tables that were introduced by later migrations are only required once the schema
reaches that revision, so backups of an older schema still verify. `--quick-verify` only checks `pg_restore --list` and is used
for the 6-hourly runs to keep memory free on a small VM.

## 3. S3 and IAM (instance role, bucket/prefix-scoped)

Bucket: private, Block Public Access on, versioning on, default SSE-KMS, lifecycle as above. The instance role (no access keys on the VM):

```json
{
  "Version": "2012-10-17",
  "Statement": [
    { "Sid": "WriteReadBackups", "Effect": "Allow",
      "Action": ["s3:PutObject", "s3:GetObject"],
      "Resource": "arn:aws:s3:::zuvo-backups-<account>/prod/db/*" },
    { "Sid": "ListPrefix", "Effect": "Allow", "Action": "s3:ListBucket",
      "Resource": "arn:aws:s3:::zuvo-backups-<account>",
      "Condition": { "StringLike": { "s3:prefix": ["prod/db/*"] } } },
    { "Sid": "UseBackupKey", "Effect": "Allow",
      "Action": ["kms:GenerateDataKey", "kms:Decrypt"],
      "Resource": "arn:aws:kms:<region>:<account>:key/<backup-key-id>" }
  ]
}
```

No `s3:DeleteObject`: a compromised VM cannot remove off-host history (lifecycle expires objects). Objects are written
to `prod/db/YYYY/MM/` and the upload is confirmed with `head-object` (size must match).

## 4. Scheduling

`/etc/cron.d/zuvo-backup` (the user must be in the `docker` group; paths per `deploy/aws/README.md`):

```cron
SHELL=/bin/bash
BACKUP_S3_URI=s3://zuvo-backups-<account>/prod/db
BACKUP_KMS_KEY_ID=alias/zuvo-backup
15 0,6,12,18 * * * zuvo /opt/zuvo/scripts/backup/backup-db.sh --project zuvo-ai --quick-verify >>/var/log/zuvo/backup.log 2>&1
45 3 * * *         zuvo /opt/zuvo/scripts/backup/backup-db.sh --project zuvo-ai              >>/var/log/zuvo/backup.log 2>&1
```

A failure must alert. The age of the newest file in `/var/backups/zuvo` and the last `backup complete` line are
checked by the Phase 12 watch script (`deploy/ops/zuvo-watch.sh`).

## 5. Restore procedures

Always start from a **verified** file. `restore-db.sh` re-runs `verify-backup.sh` before it touches anything.
It never drops a database. Every target must be named explicitly with `--confirm <project>/<db>`, and
`APP_ENV=production` targets also need `--production`.

### 5a. Get the file (if the VM is lost)

```bash
aws s3 ls s3://zuvo-backups-<account>/prod/db/2026/10/
for x in "" .sha256 .manifest.json; do aws s3 cp "s3://zuvo-backups-<account>/prod/db/2026/10/<file>.dump$x" /var/backups/zuvo/; done
scripts/backup/verify-backup.sh /var/backups/zuvo/<file>.dump        # checksum + full scratch restore + manifest match
```

### 5b. Non-destructive: restore next to the live DB (inspect, extract rows, compare)

```bash
scripts/backup/restore-db.sh --file F.dump --project zuvo-ai --as-new-db zuvo_db_restore --confirm zuvo-ai/zuvo_db_restore
# ... investigate with psql -d zuvo_db_restore ...
docker compose exec -T postgres psql -U zuvo_owner -d postgres -c "DROP DATABASE zuvo_db_restore"
```

The application never uses that database. The command refuses if the name already exists.

### 5c. Disaster recovery: replace the application database

Use this on a fresh VM after a full loss, or when the data is unrecoverable. It is never automatic.

```bash
dcp() { docker compose -p zuvo-ai --env-file .env -f docker-compose.yml -f docker-compose.prod.yml "$@"; }
dcp up -d postgres redis kafka                                 # infra only (fresh VM: also run the migrator once later)
dcp stop frontend gateway dashboard-api zuvo-ai-rag ai-router agent-worker lead-service dispatch persister followup-scheduler
scripts/backup/restore-db.sh --file F.dump --project zuvo-ai --replace --confirm zuvo-ai/zuvo_db --production
dcp run --rm migrator                                          # app-role password, newer migrations, bundles
dcp up -d --wait $(dcp config --services | grep -v '^dispatch$')   # everything EXCEPT dispatch
```

What `--replace` does: it refuses while any application service is running or any session is connected. It then takes a
safety backup of the current DB (`<dir of F>/pre-restore/` or `--safety-dir`). The current DB is **renamed** to
`zuvo_db_pre_restore_<ts>` (kept), and the file is restored into a fresh `zuvo_db` and checked. If the restore fails, the old DB is renamed
back.

**Before starting `dispatch`, review outbound messages.** The restore rewinds `outbound_messages`/`outbox_events` to the
backup time. Messages the customer already received after that point may now look `PENDING`/`SENDING` again:

```bash
for s in PENDING SENDING UNKNOWN; do dcp run --rm --no-deps dispatch python -m dispatch.reconcile list --status $s; done
# resolve rows you know were delivered (Meta Business Manager / WhatsApp history), then:
dcp up -d --wait dispatch
scripts/deploy/health-check.sh
```

After acceptance (e.g. 7 days): `DROP DATABASE zuvo_db_pre_restore_<ts>;` (manual).

Data written between the backup and the incident (≤ RPO) is lost. Customer messages from that window can be partly
re-obtained from WhatsApp itself, but not re-ingested automatically.

## 6. Restore test (do this monthly and after any schema migration)

1. Pick the newest S3 copy, not the local one, and download it to a scratch directory (5a).
2. `scripts/backup/verify-backup.sh <file>`: it must print `manifest match`.
3. Optionally, run 5c on an isolated project (a separate `-p`, different ports, `META_DRY_RUN=true`) and run the health check.
4. Record the date, file, duration and result in the ops log. If it fails, treat that as a P1: there is no proven backup.

## 7. `pg_dump` limitations vs point-in-time recovery

- RPO equals the backup interval (6 h). PITR (WAL archiving, e.g. pgBackRest/wal-g to S3, or RDS) gives an RPO of seconds to minutes and can
  restore to "just before the bad `DELETE`".
- A dump is a logical snapshot: restore time grows with data and index size (HNSW/ivfflat vector indexes are rebuilt).
  Beyond roughly 10–20 GB, move to physical backups/PITR or RDS.
- Global objects (roles) are not included (this is by design here; see §1).
- The dump holds a snapshot for its duration; long dumps delay vacuum. This is negligible at the current size.
- Migration path when RPO must be < 1 h: managed RDS PostgreSQL with automated backups/PITR, or wal-g on the VM.

## 8. Verification evidence (dev host, 2026-10-19)

| Drill | Result |
|---|---|
| Full backup `zuvo-ai` (alembic 0006, 17 tables) | exit 0, 304 KB, ~4 s; scratch restore row counts identical to live |
| `verify-backup.sh` standalone | checksum ok, archive readable, restore ok, `manifest match` |
| Corrupted byte in dump | `sha256 mismatch`, exit 3 |
| Truncated dump without checksum | `archive unreadable`, exit 3 |
| S3 path (fake `aws` shim recording calls) | `s3 cp --sse aws:kms --sse-kms-key-id …` for dump, `.sha256`, manifest under `YYYY/MM/`; `head-object` size check; upload failure → exit 4, local copy kept |
| Retention `--keep 3` over 4 runs | 3 newest kept; `--keep 1` refused |
| `--as-new-db` | restored + checked; same name again refused; dropped manually |
| Refusals | missing `--confirm`; apps running; `APP_ENV=production` without `--production` — each exit 2 with no change |
| **Replace drill** on isolated prodtest project (`APP_ENV=production`, Caddy, alembic 0003) | backup → change made after backup (`drill_marker` table) → apps stopped → `--replace`: safety backup written, old DB kept as `zuvo_db_pre_restore_*`, change gone → migrator → all services healthy, `https://localhost:18443/api/v1/health` ok; **47 s** stop→healthy |

Issues found and fixed by the drills: the verification wrongly required tables from newer migrations, so backups of an
older schema failed; the safety-backup directory defaulted to an unwritable path. Refusal checks now run before the slow verification.

**NOT VERIFIED:** real S3/KMS/IAM (shim only), cron on a VM, restore of a multi-GB database, the `dispatch` outbound review
after a real rewind with live Meta traffic, and RTO on the target AWS instance (Phase 14).
