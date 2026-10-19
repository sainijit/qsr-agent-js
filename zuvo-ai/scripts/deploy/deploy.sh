#!/usr/bin/env bash
# Repeatable manual release of the checked-out git commit to the production compose project.
#
#   git fetch && git checkout --detach <release-commit-or-tag>
#   scripts/deploy/deploy.sh [--rebuild] [--pull] [--skip-backup] [--auto-rollback] [--allow-dirty] [--local-rehearsal]
#
# Steps (each failure stops the release and says what state the system is in):
#   1 lock (one deploy/rollback at a time)      5 backup (mandatory, full verification, when migrations are pending)
#   2 preflight.sh                              6 migrator: advisory-locked, runs once (alembic upgrade + bundles)
#   3 build images zuvo/<svc>:<commit>          7 update services in order (UPDATE_GROUPS in lib.sh), wait healthy
#   4 data services up (never recreated)        8 health-check.sh; record the release; prune old images
#
# Not zero-downtime: each recreated container is briefly unavailable (seconds). Meta retries webhooks, and the consumers
# resume from committed offsets. Deploy in a quiet window; see docs/deployment.md.
# Volumes are never removed. The database is never rolled back automatically.
# --auto-rollback: if step 7/8 fails AND the schema did not change, restart the previous release's images.
# Exit codes: 0 deployed | 2 usage/preflight | 3 build | 4 backup | 5 migration | 6 update/health (see output) | 7 locked
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib.sh
. "$HERE/lib.sh"

REBUILD=0; PULL=(); SKIP_BACKUP=0; AUTO_RB=0; ALLOW_DIRTY=0; PF_ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --rebuild) REBUILD=1; shift ;;
    --pull) PULL=(--pull); shift ;;
    --skip-backup) SKIP_BACKUP=1; shift ;;
    --auto-rollback) AUTO_RB=1; shift ;;
    --allow-dirty) ALLOW_DIRTY=1; shift ;;
    --local-rehearsal) PF_ARGS+=(--local-rehearsal); shift ;;
    -h|--help) sed -n 2,18p "$0"; exit 0 ;;
    *) die 2 "unknown argument: $1" ;;
  esac
done

with_lock
STARTED=$(date -u +%Y-%m-%dT%H:%M:%SZ)
COMMIT=$(git -C "$ROOT" rev-parse HEAD)
TAG=$(git -C "$ROOT" rev-parse --short=12 HEAD)
if [[ -n "$(git -C "$ROOT" status --porcelain --untracked-files=no)" ]]; then
  [[ $ALLOW_DIRTY -eq 1 ]] || die 2 "uncommitted changes: a release must be a commit (commit/stash, or --allow-dirty for a rehearsal)"
  TAG="$TAG-dirty$(date +%s)"
  log "WARNING: dirty tree, tag $TAG is not reproducible from git"
fi
PREV=$(cat "$STATE_DIR/current" 2>/dev/null || true)
export IMAGE_TAG=$TAG ZUVO_VERSION=$TAG ZUVO_IMAGE_PREFIX=$IMAGE_PREFIX
log "release $TAG (commit $COMMIT) to project $PROJECT; previous: ${PREV:-none}"

fail_record() {  # fail_record <step> <message>
  append_history "$(json_obj tag "$TAG" commit "$COMMIT" result failed step "$1" message "$2" previous "$PREV" \
                    alembic_before "${REV_BEFORE:-}" alembic_after "$(db_alembic)" \
                    started_at "$STARTED" finished_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" by "$(id -un)")"
}

# ---- 2 preflight
IMAGE_TAG=$TAG "$HERE/preflight.sh" "${PF_ARGS[@]}" || { fail_record preflight "preflight failed"; die 2 "preflight failed; nothing was changed"; }

# ---- 3 build
if [[ $REBUILD -eq 0 ]] && images_present "$TAG"; then
  log "images for $TAG already present (use --rebuild to force)"
else
  log "building images for $TAG"
  dc build "${PULL[@]}" "${BUILT_SERVICES[@]}" >&2 || { fail_record build "image build failed"; die 3 "build failed; running services are unchanged"; }
fi
images_present "$TAG" || { fail_record build "images missing after build"; die 3 "images for $TAG missing after build"; }

# ---- 4 data services (start if stopped; never recreate a running database during a release)
dc up -d --no-recreate --wait "${INFRA_SERVICES[@]}" >&2 || { fail_record infra "data services not healthy"; die 6 "postgres/redis/kafka not healthy; application unchanged"; }
dc up --no-recreate --no-deps kafka-init >&2 2>/dev/null || dc run --rm --no-deps kafka-init >&2 || { fail_record infra "kafka-init failed"; die 6 "topic creation failed"; }

# ---- 5 backup before risky migrations
STATUS=$(dc run --rm --no-deps -T migrator python run.py --status 2>/dev/null | tail -1) ||
  { fail_record migrate "migrator --status failed"; die 5 "cannot read migration status; nothing changed"; }
read -r REV_BEFORE REV_HEAD PENDING < <(python3 -c 'import json,sys; d=json.loads(sys.argv[1]); print(d["current"] or "none", d["head"], "yes" if d["pending"] else "no")' "$STATUS")
log "schema: database=$REV_BEFORE code=$REV_HEAD pending=$PENDING"
BACKUP_FILE=""
BDIR=$(env_get BACKUP_DIR); BDIR=${BDIR:-/var/backups/zuvo}
FRESH_DB=0
if [[ "$REV_BEFORE" == "none" ]]; then
  # First install: no schema means no data to protect. Any table without alembic_version is unexpected: stop.
  TABLES=$(db_table_count)
  [[ "$TABLES" == "0" ]] || { fail_record backup "no alembic_version but ${TABLES:-unknown} tables"; die 4 "database has no migration record but is not empty (${TABLES:-unknown} tables); inspect before deploying"; }
  FRESH_DB=1; log "empty database (first install): no pre-migration backup needed"
fi
if [[ $FRESH_DB -eq 0 && ( "$PENDING" == "yes" || $SKIP_BACKUP -eq 0 ) ]]; then
  [[ "$PENDING" == "yes" && $SKIP_BACKUP -eq 1 ]] && log "--skip-backup ignored: migrations are pending, a verified backup is mandatory"
  VERIFY=(); [[ "$PENDING" == "no" ]] && VERIFY=(--quick-verify)
  log "backup before release (verification: ${VERIFY[*]:-full})"
  BACKUP_FILE=$(BACKUP_DIR="$BDIR" BACKUP_S3_URI="$(env_get BACKUP_S3_URI)" BACKUP_KMS_KEY_ID="$(env_get BACKUP_KMS_KEY_ID)" \
                "$ROOT/scripts/backup/backup-db.sh" --project "$PROJECT" "${VERIFY[@]}") ||
    { fail_record backup "backup failed"; die 4 "backup failed; release stopped before any change"; }
  log "backup: $BACKUP_FILE"
fi

# ---- 6 migrations (advisory lock inside the migrator; one run)
log "running migrator"
# `up --force-recreate` (not `run --rm`) so the one-shot migrator container that `docker compose start/up <svc>` re-runs
# as a dependency always belongs to the current release; a stale one would run old migrations against a newer schema.
if ! dc up --no-deps --force-recreate --exit-code-from migrator migrator >&2; then
  REV_NOW=$(db_alembic)
  fail_record migrate "migrator failed (schema now ${REV_NOW:-unknown})"
  log "Each migration runs in its own transaction: the schema is at ${REV_NOW:-unknown}. Application containers still run ${PREV:-the old release}."
  log "Fix forward and re-run deploy.sh. If the data is damaged, restore $BACKUP_FILE (docs/backup-and-restore.md section 5c)."
  die 5 "migration failed"
fi
REV_AFTER=$(db_alembic)

# ---- 7 + 8 update and verify
rollback_or_die() {  # rollback_or_die <step> <message>
  fail_record "$1" "$2"
  log "ERROR: $2"
  if [[ $AUTO_RB -eq 1 && -n "$PREV" && "$REV_AFTER" == "$REV_BEFORE" ]]; then
    log "auto-rollback: schema unchanged ($REV_AFTER), restarting $PREV"
    "$HERE/rollback.sh" --to "$PREV" --no-lock --reason "auto: $2" && die 6 "release $TAG failed; rolled back to $PREV"
    die 6 "release $TAG failed AND rollback to $PREV failed: follow docs/runbooks/failed-deployment.md"
  fi
  [[ -n "$PREV" ]] && log "to restore the previous application images: scripts/deploy/rollback.sh --to $PREV" \
                   || log "no previous release recorded; fix forward"
  [[ "$REV_AFTER" != "$REV_BEFORE" ]] && log "schema changed $REV_BEFORE -> $REV_AFTER: rollback.sh will ask you to confirm the old code is compatible"
  die 6 "release $TAG is NOT healthy"
}
update_services || rollback_or_die update "services did not become healthy"
"$HERE/health-check.sh" --expect-tag "$TAG" --expect-alembic "$REV_AFTER" >&2 || rollback_or_die health "health/smoke checks failed"

record_release "$(json_obj tag "$TAG" commit "$COMMIT" result deployed previous "$PREV" alembic_before "$REV_BEFORE" \
                  alembic_after "$REV_AFTER" backup "$BACKUP_FILE" started_at "$STARTED" \
                  finished_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" by "$(id -un)" compose_extra "${ZUVO_COMPOSE_EXTRA:-}")"
set_current "$TAG"
prune_images
log "deployed $TAG (schema $REV_AFTER). Previous release: ${PREV:-none}"
echo "$TAG"
