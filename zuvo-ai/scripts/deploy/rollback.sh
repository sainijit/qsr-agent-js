#!/usr/bin/env bash
# Roll the APPLICATION back to a previously deployed release. The database is never downgraded and the migrator never runs.
#
#   scripts/deploy/rollback.sh [--to previous|<tag>] [--accept-schema] [--reason TEXT] [--list]
#
# - The target must have a release record (.deploy/releases/<tag>.json) from a successful deploy.
# - If the live schema differs from the schema that release was deployed with (alembic_after), the old code may not
#   understand the new schema. The rollback is refused unless --accept-schema is given, which states that the
#   migrations in between were additive/backward compatible (our convention: expand first, contract later).
#   For a schema that the old code cannot use, restore the pre-release backup instead (docs/backup-and-restore.md).
# - Compose files are taken from the target commit (git worktree in $STATE_DIR/worktrees), so the services match the images.
# Exit codes: 0 rolled back | 2 usage/refused | 3 images missing and rebuild failed | 6 unhealthy after rollback | 7 locked
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib.sh
. "$HERE/lib.sh"

TO=previous; ACCEPT=0; REASON="manual"; LOCK=1; LIST=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --to) TO=$2; shift 2 ;;
    --accept-schema) ACCEPT=1; shift ;;
    --reason) REASON=$2; shift 2 ;;
    --no-lock) LOCK=0; shift ;;   # internal: called by deploy.sh, which already holds the lock
    --list) LIST=1; shift ;;
    -h|--help) sed -n 2,13p "$0"; exit 0 ;;
    *) die 2 "unknown argument: $1" ;;
  esac
done

if [[ $LIST -eq 1 ]]; then
  printf 'current:  %s\nprevious: %s\n\n' "$(cat "$STATE_DIR/current" 2>/dev/null || echo -)" "$(cat "$STATE_DIR/previous" 2>/dev/null || echo -)"
  [[ -f "$STATE_DIR/history.jsonl" ]] && tail -n 20 "$STATE_DIR/history.jsonl" | python3 -c '
import json, sys
for line in sys.stdin:
    r = json.loads(line)
    print("{:<22} {:<12} {:<10} schema {}->{}  {}".format(r.get("finished_at", ""), r.get("result", ""), r.get("tag", ""),
          r.get("alembic_before", "-"), r.get("alembic_after", "-"), r.get("message", r.get("reason", ""))))'
  exit 0
fi

[[ $LOCK -eq 1 ]] && with_lock
STARTED=$(date -u +%Y-%m-%dT%H:%M:%SZ)
CUR=$(cat "$STATE_DIR/current" 2>/dev/null || true)
if [[ "$TO" == previous ]]; then
  TO=$(cat "$STATE_DIR/previous" 2>/dev/null || true)
  [[ -n "$TO" ]] || die 2 "no previous release recorded in $STATE_DIR/previous; use --to <tag> (see --list)"
fi
[[ -f "$STATE_DIR/releases/$TO.json" ]] || die 2 "no release record for $TO (only successfully deployed releases can be targets; see --list)"
[[ "$TO" != "$CUR" ]] || log "note: $TO is already the current release; re-applying it"

COMMIT=$(release_field "$TO" commit)
TARGET_REV=$(release_field "$TO" alembic_after)
LIVE_REV=$(db_alembic)
[[ -n "$LIVE_REV" ]] || die 2 "cannot read the live schema version (is postgres running?)"
if [[ "$LIVE_REV" != "$TARGET_REV" ]]; then
  if [[ $ACCEPT -eq 0 ]]; then
    log "live schema is $LIVE_REV but $TO was deployed with $TARGET_REV."
    log "Rolling back code does not roll back the schema. Only continue if every migration after $TARGET_REV is backward"
    log "compatible (additive). Otherwise restore the pre-release backup (docs/backup-and-restore.md) or fix forward."
    die 2 "refused: re-run with --accept-schema to roll back the application only"
  fi
  log "WARNING: --accept-schema: running $TO (built for $TARGET_REV) against schema $LIVE_REV"
fi

# Use the target commit's compose files so service definitions match the images.
COMPOSE_ROOT=$ROOT
if git -C "$ROOT" cat-file -e "$COMMIT^{commit}" 2>/dev/null; then
  if [[ "$(git -C "$ROOT" rev-parse HEAD)" != "$COMMIT" ]]; then
    WT="$STATE_DIR/worktrees/$TO"
    [[ -d "$WT" ]] || git -C "$ROOT" worktree add --detach "$WT" "$COMMIT" >/dev/null 2>&1 || die 2 "cannot create worktree for $COMMIT"
    COMPOSE_ROOT=$WT
  fi
else
  log "WARNING: commit $COMMIT not in this clone; using the current compose files"
fi
if [[ "$COMPOSE_ROOT" != "$ROOT" ]]; then
  FILE_ARGS=(-f "$COMPOSE_ROOT/docker-compose.yml" -f "$COMPOSE_ROOT/docker-compose.prod.yml")
  for _f in ${ZUVO_COMPOSE_EXTRA:-}; do [[ "$_f" == /* ]] || _f="$ROOT/$_f"; FILE_ARGS+=(-f "$_f"); done
  dc() { docker compose -p "$PROJECT" --project-directory "$COMPOSE_ROOT" --env-file "$ENV_FILE" "${FILE_ARGS[@]}" "$@"; }
fi

export IMAGE_TAG=$TO ZUVO_VERSION=$TO ZUVO_IMAGE_PREFIX=$IMAGE_PREFIX
log "rolling back application ${CUR:-?} -> $TO (commit $COMMIT, schema $LIVE_REV)"
if ! images_present "$TO"; then
  [[ "$TO" == *-dirty* ]] && die 3 "images for $TO were pruned and a -dirty tag cannot be rebuilt from git"
  log "images for $TO not present locally; rebuilding from commit $COMMIT"
  dc build "${BUILT_SERVICES[@]}" >&2 || die 3 "rebuild of $TO failed; current release left running"
fi

record() {
  append_history "$(json_obj tag "$TO" commit "$COMMIT" result "$1" from "$CUR" reason "$REASON" alembic_before "$LIVE_REV" \
                    alembic_after "$LIVE_REV" started_at "$STARTED" finished_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" by "$(id -un)")"
}
# Keep the one-shot migrator container on the target release too (created, not run: the schema is never touched here).
dc create --no-deps --force-recreate migrator >&2 2>/dev/null || log "WARNING: could not recreate the migrator container definition"
if ! update_services || ! "$HERE/health-check.sh" --expect-tag "$TO" >&2; then
  record rollback_failed
  die 6 "rollback to $TO is NOT healthy; see docs/runbooks/failed-deployment.md"
fi
record rolled_back
set_current "$TO"
log "rolled back to $TO. The database schema is still $LIVE_REV."
echo "$TO"
