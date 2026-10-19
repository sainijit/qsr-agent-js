#!/usr/bin/env bash
# Restore a verified backup. Two explicit modes; nothing is ever dropped.
#
#   As a NEW database next to the live one (non-destructive; inspect / extract / compare):
#     scripts/backup/restore-db.sh --file F.dump --project zuvo-ai --as-new-db zuvo_db_restore --confirm zuvo-ai/zuvo_db_restore
#
#   REPLACE the application database (disaster recovery). Requires every application service in the project to be
#   stopped first, takes a safety backup, then RENAMES the current database to zuvo_db_pre_restore_<ts> (kept) and
#   restores into a fresh zuvo_db:
#     docker compose stop gateway dashboard-api ai-router lead-service agent-worker dispatch persister followup-scheduler zuvo-ai-rag frontend
#     scripts/backup/restore-db.sh --file F.dump --project zuvo-ai --replace --confirm zuvo-ai/zuvo_db
#
# The safety backup goes to --safety-dir (default: <directory of --file>/pre-restore).
# APP_ENV=production targets additionally require --production (and the same confirmation).
# Exit codes: 0 ok | 2 usage / refused | 3 restore or post-restore checks failed
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib.sh
. "$HERE/lib.sh"

FILE=""; PROJECT=""; NEWDB=""; REPLACE=0; CONFIRM=""; PROD_OK=0; SAFETY_DIR=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --file) FILE=$2; shift 2 ;;
    --project) PROJECT=$2; shift 2 ;;
    --as-new-db) NEWDB=$2; shift 2 ;;
    --replace) REPLACE=1; shift ;;
    --confirm) CONFIRM=$2; shift 2 ;;
    --production) PROD_OK=1; shift ;;
    --safety-dir) SAFETY_DIR=$2; shift 2 ;;
    -h|--help) sed -n 2,16p "$0"; exit 0 ;;
    *) die 2 "unknown argument: $1" ;;
  esac
done
[[ -n "$FILE" && -f "$FILE" ]] || die 2 "--file <backup.dump> is required"
SAFETY_DIR=${SAFETY_DIR:-"$(cd "$(dirname "$FILE")" && pwd)/pre-restore"}
[[ -n "$PROJECT" ]] || die 2 "--project is required (no default: the target must be explicit)"
if [[ -n "$NEWDB" && $REPLACE -eq 1 ]] || [[ -z "$NEWDB" && $REPLACE -eq 0 ]]; then
  die 2 "choose exactly one of --as-new-db NAME or --replace"
fi
TARGET_DB=${NEWDB:-$ZUVO_DB}
[[ "$TARGET_DB" =~ ^[a-z_][a-z0-9_]{0,62}$ ]] || die 2 "invalid database name: $TARGET_DB"
[[ -z "$NEWDB" || "$NEWDB" != "$ZUVO_DB" ]] || die 2 "--as-new-db cannot be $ZUVO_DB; use --replace"
[[ "$CONFIRM" == "$PROJECT/$TARGET_DB" ]] || die 2 "refusing: pass --confirm $PROJECT/$TARGET_DB to acknowledge the target"

CID=$(pg_container "$PROJECT") || die 2 "postgres container for compose project '$PROJECT' not running"
APP_CID=$(docker ps -aq --filter "label=com.docker.compose.project=$PROJECT" --filter "label=com.docker.compose.service=dashboard-api" | head -1)
APP_ENV=""
if [[ -n "$APP_CID" ]]; then
  APP_ENV=$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$APP_CID" | sed -n 's/^APP_ENV=//p' | head -1 || true)
fi
if [[ "${APP_ENV,,}" == "production" && $PROD_OK -ne 1 ]]; then
  die 2 "refusing: project $PROJECT runs APP_ENV=production; add --production after reading docs/backup-and-restore.md"
fi

if [[ $REPLACE -eq 1 ]]; then
  INFRA="^(postgres|redis|kafka|kafka-init|migrator|caddy)$"
  RUNNING=$(docker ps --filter "label=com.docker.compose.project=$PROJECT" --format '{{.Label "com.docker.compose.service"}}' |
            grep -Ev "$INFRA" || true)
  [[ -z "$RUNNING" ]] || die 2 "refusing: stop the application services first (running: $(tr '\n' ' ' <<<"$RUNNING"))"
  OTHERS=$(psql_c "$CID" postgres "SELECT count(*) FROM pg_stat_activity WHERE datname = '$ZUVO_DB' AND pid <> pg_backend_pid()")
  [[ "$OTHERS" == "0" ]] || die 2 "refusing: $OTHERS session(s) still connected to $ZUVO_DB"

fi

log "verifying the backup before touching $PROJECT"
"$HERE/verify-backup.sh" "$FILE" >/dev/null || die 3 "backup failed verification; nothing was changed"

if [[ -n "$NEWDB" ]]; then
  [[ -z "$(psql_c "$CID" postgres "SELECT 1 FROM pg_database WHERE datname = '$NEWDB'")" ]] ||
    die 2 "database $NEWDB already exists; pick another name (nothing is overwritten)"
  log "restoring into new database $NEWDB"
  restore_into "$CID" "$NEWDB" "$FILE" >/dev/null || die 3 "restore failed; drop the partial database with: DROP DATABASE $NEWDB"
  RESULT=$(check_restored "$CID" "$NEWDB") || die 3 "post-restore checks failed: $RESULT"
  echo "$RESULT"
  log "done. $NEWDB is NOT used by the application. Drop it when finished: DROP DATABASE $NEWDB;"
  exit 0
fi

# ---- replace ----
log "safety backup of the current database (kept even if the restore succeeds)"
SAFETY=$("$HERE/backup-db.sh" --project "$PROJECT" --dir "$SAFETY_DIR" --quick-verify) ||
  die 3 "safety backup failed; nothing was changed"
TS=$(date -u +%Y%m%d%H%M%S)
OLD="${ZUVO_DB}_pre_restore_$TS"
psql_c "$CID" postgres "ALTER DATABASE $ZUVO_DB RENAME TO $OLD" || die 3 "could not rename current database"
log "current database kept as $OLD; safety backup: $SAFETY"
if ! restore_into "$CID" "$ZUVO_DB" "$FILE" >/dev/null || ! RESULT=$(check_restored "$CID" "$ZUVO_DB"); then
  log "restore failed - putting the previous database back"
  psql_c "$CID" postgres "DROP DATABASE IF EXISTS $ZUVO_DB" >/dev/null || true
  psql_c "$CID" postgres "ALTER DATABASE $OLD RENAME TO $ZUVO_DB" || die 3 "ROLLBACK FAILED: rename $OLD back to $ZUVO_DB by hand"
  die 3 "restore failed; previous database restored unchanged"
fi
echo "$RESULT"
cat >&2 <<EOF
Restore complete. Next (see docs/backup-and-restore.md section 5):
  (use the same -f / --env-file flags this project is normally run with)
  1. docker compose -p $PROJECT run --rm migrator   # re-applies the app-role password + any newer migrations
  2. docker compose -p $PROJECT up -d --wait        # start services
  3. scripts/deploy/health-check.sh                 # verify
  4. after acceptance: DROP DATABASE $OLD;          # manual, never automatic
EOF
