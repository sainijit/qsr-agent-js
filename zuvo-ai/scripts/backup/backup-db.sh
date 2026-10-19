#!/usr/bin/env bash
# Consistent logical backup of the ZuvoCloud Postgres database, verified by a full restore into an isolated
# throw-away Postgres, optionally uploaded to S3 (SSE-KMS) and pruned locally by count.
#
#   scripts/backup/backup-db.sh [--project zuvo-ai] [--dir /var/backups/zuvo] [--keep 14]
#                               [--s3 s3://bucket/prefix] [--kms-key alias/zuvo-backup] [--quick-verify]
#
# Env equivalents: COMPOSE_PROJECT_NAME, BACKUP_DIR, BACKUP_KEEP, BACKUP_S3_URI, BACKUP_KMS_KEY_ID.
# S3 credentials come from the instance role / standard AWS credential chain - never from this script.
#
# Exit codes: 0 ok | 1 dump failed | 2 usage/environment | 3 verification failed | 4 upload failed | 5 locked
# A backup that failed verification is kept as *.dump.unverified for inspection and is never counted as good.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib.sh
. "$HERE/lib.sh"

PROJECT=${COMPOSE_PROJECT_NAME:-zuvo-ai}
DIR=${BACKUP_DIR:-/var/backups/zuvo}
KEEP=${BACKUP_KEEP:-14}
S3=${BACKUP_S3_URI:-}
KMS=${BACKUP_KMS_KEY_ID:-}
QUICK=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --project) PROJECT=$2; shift 2 ;;
    --dir) DIR=$2; shift 2 ;;
    --keep) KEEP=$2; shift 2 ;;
    --s3) S3=$2; shift 2 ;;
    --kms-key) KMS=$2; shift 2 ;;
    --quick-verify) QUICK=1; shift ;;
    -h|--help) sed -n 2,13p "$0"; exit 0 ;;
    *) die 2 "unknown argument: $1" ;;
  esac
done
[[ "$KEEP" =~ ^[0-9]+$ && "$KEEP" -ge 2 ]] || die 2 "--keep must be >= 2 (never keep only one backup)"
[[ -z "$S3" || "$S3" =~ ^s3://[a-z0-9.-]+(/.*)?$ ]] || die 2 "--s3 must look like s3://bucket/prefix"
if [[ -n "$S3" ]]; then command -v aws >/dev/null || die 2 "--s3 given but aws CLI is not installed"; fi

umask 077
mkdir -p "$DIR"
chmod 700 "$DIR"
exec 9>"$DIR/.backup.lock"
flock -n 9 || die 5 "another backup is running ($DIR/.backup.lock)"

CID=$(pg_container "$PROJECT") || die 2 "postgres container for compose project '$PROJECT' not running"
TS=$(date -u +%Y%m%dT%H%M%SZ)
BASE="$DIR/${ZUVO_DB}-${PROJECT}-${TS}"
DUMP="$BASE.dump"
[[ ! -e "$DUMP" ]] || die 2 "$DUMP already exists; refusing to overwrite"
SCRATCH=""
trap 'scratch_stop "$SCRATCH"; rm -f "$DUMP.partial"' EXIT

# ---- 1. dump (pg_dump runs in one REPEATABLE READ snapshot: consistent across tables) ----
log "dumping $ZUVO_DB from project $PROJECT"
T0=$(date +%s)
if ! docker exec "$CID" pg_dump -U "$ZUVO_DB_OWNER" -d "$ZUVO_DB" --format=custom --compress=6 --no-password \
     > "$DUMP.partial"; then
  die 1 "pg_dump failed"
fi
[[ -s "$DUMP.partial" ]] || die 1 "pg_dump produced an empty file"
mv "$DUMP.partial" "$DUMP"
SHA=$(sha256_of "$DUMP")
echo "$SHA  $(basename "$DUMP")" > "$DUMP.sha256"
SIZE=$(stat -c %s "$DUMP")
LIVE_VER=$(psql_c "$CID" "$ZUVO_DB" "SELECT version_num FROM alembic_version")
log "dump ok: $(basename "$DUMP") ${SIZE} bytes sha256=${SHA:0:16}... in $(( $(date +%s) - T0 ))s"

# ---- 2. verify ----
VERIFY_JSON='{}'
VERIFIED=quick
if ! docker exec -i "$CID" pg_restore --list < "$DUMP" > "$BASE.toc"; then
  mv "$DUMP" "$DUMP.unverified"; die 3 "pg_restore --list failed: archive unreadable"
fi
if [[ $QUICK -eq 0 ]]; then
  log "restoring into an isolated scratch postgres for verification"
  SCRATCH=$(scratch_start)
  if ! restore_into "$SCRATCH" "$ZUVO_DB" "$DUMP" >"$BASE.restore.log" 2>&1; then
    mv "$DUMP" "$DUMP.unverified"; die 3 "verification restore failed (see $BASE.restore.log)"
  fi
  if ! VERIFY_JSON=$(check_restored "$SCRATCH" "$ZUVO_DB"); then
    mv "$DUMP" "$DUMP.unverified"; die 3 "restored database failed checks: $VERIFY_JSON"
  fi
  scratch_stop "$SCRATCH"; SCRATCH=""
  [[ "$(sed -n 's/.*"alembic_version": "\([^"]*\)".*/\1/p' <<<"$VERIFY_JSON")" == "$LIVE_VER" ]] ||
    { mv "$DUMP" "$DUMP.unverified"; die 3 "restored schema version differs from live ($LIVE_VER)"; }
  VERIFIED=full
  rm -f "$BASE.restore.log"
fi
log "verification ($VERIFIED) ok"

GIT=$(git -C "$HERE/../.." rev-parse --short HEAD 2>/dev/null || echo unknown)
cat > "$BASE.manifest.json" <<EOF
{"file": "$(basename "$DUMP")", "created_at": "$TS", "project": "$PROJECT", "database": "$ZUVO_DB",
 "host": "$(hostname -s)", "git_commit": "$GIT", "size_bytes": $SIZE, "sha256": "$SHA",
 "format": "pg_dump custom", "pg_server": "$(psql_c "$CID" "$ZUVO_DB" "SHOW server_version")",
 "alembic_version": "$LIVE_VER", "verification": "$VERIFIED", "restored": $VERIFY_JSON}
EOF
rm -f "$BASE.toc"

# ---- 3. off-host copy ----
UPLOAD=skipped
if [[ -n "$S3" ]]; then
  SSE=(--sse aws:kms); [[ -n "$KMS" ]] && SSE+=(--sse-kms-key-id "$KMS")
  DEST="${S3%/}/$(date -u +%Y/%m)"
  log "uploading to $DEST/"
  for f in "$DUMP" "$DUMP.sha256" "$BASE.manifest.json"; do
    aws s3 cp --only-show-errors "${SSE[@]}" "$f" "$DEST/$(basename "$f")" || die 4 "upload failed: $(basename "$f") (local backup kept)"
  done
  REMOTE_SIZE=$(aws s3api head-object --bucket "$(cut -d/ -f3 <<<"$DEST")" \
                --key "$(cut -d/ -f4- <<<"$DEST")/$(basename "$DUMP")" --query ContentLength --output text) ||
    die 4 "uploaded object not found (local backup kept)"
  [[ "$REMOTE_SIZE" == "$SIZE" ]] || die 4 "uploaded size $REMOTE_SIZE != local $SIZE (local backup kept)"
  UPLOAD=ok
fi

# ---- 4. local retention: keep the newest $KEEP verified backups (never the one just made) ----
mapfile -t GOOD < <(ls -1 "$DIR"/${ZUVO_DB}-${PROJECT}-*.dump 2>/dev/null | sort -r)
PRUNED=0
for old in "${GOOD[@]:$KEEP}"; do
  [[ "$old" == "$DUMP" ]] && continue
  rm -f "$old" "$old.sha256" "${old%.dump}.manifest.json"; PRUNED=$((PRUNED + 1))
done
find "$DIR" -maxdepth 1 -name "${ZUVO_DB}-${PROJECT}-*.dump.unverified" -mtime +7 -delete

log "backup complete: file=$(basename "$DUMP") verification=$VERIFIED upload=$UPLOAD pruned=$PRUNED"
[[ -z "$S3" ]] && log "WARNING: no --s3 destination: this backup exists only on this host"
echo "$DUMP"
