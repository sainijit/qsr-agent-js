#!/usr/bin/env bash
# Shared helpers for scripts/deploy/*. Sourced, not executed.
#
# Configuration (environment):
#   COMPOSE_PROJECT_NAME  compose project                         (default zuvo-ai)
#   ZUVO_ENV_FILE         env file passed to compose              (default <repo>/.env)
#   ZUVO_COMPOSE_EXTRA    extra compose files, space separated    (e.g. "deploy/aws/docker-compose.awslogs.yml")
#   ZUVO_STATE_DIR        release records + lock                  (default <repo>/.deploy)
#   ZUVO_IMAGE_PREFIX     image name prefix                       (default zuvo)
#   ZUVO_KEEP_RELEASES    image tags kept on disk                 (default 3, never fewer than current + previous)
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PROJECT=${COMPOSE_PROJECT_NAME:-zuvo-ai}
ENV_FILE=${ZUVO_ENV_FILE:-$ROOT/.env}
STATE_DIR=${ZUVO_STATE_DIR:-$ROOT/.deploy}
IMAGE_PREFIX=${ZUVO_IMAGE_PREFIX:-zuvo}
KEEP_RELEASES=${ZUVO_KEEP_RELEASES:-3}

FILE_ARGS=(-f "$ROOT/docker-compose.yml" -f "$ROOT/docker-compose.prod.yml")
for _f in ${ZUVO_COMPOSE_EXTRA:-}; do
  [[ "$_f" == /* ]] || _f="$ROOT/$_f"
  FILE_ARGS+=(-f "$_f")
done

# images we build
BUILT_SERVICES=(migrator zuvo-ai-rag persister ai-router lead-service agent-worker followup-scheduler dispatch dashboard-api gateway frontend)
# controlled update order: dependencies first, ingress last. Each group is started and waited for (healthy) before the next.
#   1 retrieval (agent-worker calls it)  2 Kafka consumers (at-least-once, idempotent)  3 HTTP APIs (webhook ingress)  4 edge
UPDATE_GROUPS=("zuvo-ai-rag"
               "persister ai-router lead-service agent-worker followup-scheduler dispatch"
               "dashboard-api gateway"
               "frontend caddy")
# shellcheck disable=SC2034  # used by the sourcing scripts
INFRA_SERVICES=(postgres redis kafka)
# shellcheck disable=SC2034
ONE_SHOT_SERVICES=(migrator kafka-init)

log()  { printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >&2; }
die()  { local code=$1; shift; log "ERROR: $*"; exit "$code"; }

dc() { docker compose -p "$PROJECT" --project-directory "$ROOT" --env-file "$ENV_FILE" "${FILE_ARGS[@]}" "$@"; }

# env_get NAME: value from the process environment, else from $ENV_FILE (compose precedence). Never log the result.
env_get() {
  if [[ -n "${!1+x}" ]]; then printf '%s' "${!1}"; return; fi
  python3 - "$ENV_FILE" "$1" <<'PY'
import sys
from pathlib import Path
path, name = Path(sys.argv[1]), sys.argv[2]
val = ""
if path.exists():
    for line in path.read_text().splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        if k.strip() != name:
            continue
        v = v.strip()
        if v[:1] in ("'", '"') and v[-1:] == v[:1]:
            v = v[1:-1]
        elif " #" in v:
            v = v.split(" #", 1)[0].rstrip()
        val = v
print(val, end="")
PY
}

with_lock() {
  mkdir -p "$STATE_DIR"
  exec 8>"$STATE_DIR/.deploy.lock"
  flock -n 8 || die 7 "another deploy/rollback is running (lock $STATE_DIR/.deploy.lock)"
}

image_of() { printf '%s/%s:%s' "$IMAGE_PREFIX" "$1" "$2"; }

images_present() {  # images_present TAG -> 0 if every built service has a local image for TAG
  local s
  for s in "${BUILT_SERVICES[@]}"; do docker image inspect "$(image_of "$s" "$1")" >/dev/null 2>&1 || return 1; done
}

db_table_count() {  # user tables in zuvo_db ("" if unknown)
  dc exec -T postgres psql -X -U zuvo_owner -d zuvo_db -tAq -c "SELECT count(*) FROM pg_tables WHERE schemaname NOT IN ('pg_catalog','information_schema')" 2>/dev/null | tr -d '[:space:]' || true
}

db_alembic() {  # current alembic revision of the live DB ("" if unknown)
  dc exec -T postgres psql -X -U zuvo_owner -d zuvo_db -tAq -c "SELECT version_num FROM alembic_version" 2>/dev/null | tr -d '[:space:]' || true
}

# update_services: recreate application services in UPDATE_GROUPS order using IMAGE_TAG from the environment
update_services() {
  local group svcs
  for group in "${UPDATE_GROUPS[@]}"; do
    read -r -a svcs <<<"$group"
    log "updating: ${svcs[*]}"
    dc up -d --no-deps --wait --wait-timeout "${ZUVO_WAIT_TIMEOUT:-240}" "${svcs[@]}" >&2 || return 1
  done
}

# ---- release records: $STATE_DIR/releases/<tag>.json, current, previous, history.jsonl
record_release() {  # record_release <json>: writes releases/<tag>.json and appends to history.jsonl
  mkdir -p "$STATE_DIR/releases"
  local tag
  tag=$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["tag"])' "$1")
  printf '%s\n' "$1" > "$STATE_DIR/releases/$tag.json.tmp" && mv -f "$STATE_DIR/releases/$tag.json.tmp" "$STATE_DIR/releases/$tag.json"
  printf '%s\n' "$1" >> "$STATE_DIR/history.jsonl"
}

append_history() { mkdir -p "$STATE_DIR"; printf '%s\n' "$1" >> "$STATE_DIR/history.jsonl"; }

set_current() {  # set_current <tag>: old current -> previous (if different), <tag> -> current
  local cur=""
  [[ -f "$STATE_DIR/current" ]] && cur=$(cat "$STATE_DIR/current")
  if [[ -n "$cur" && "$cur" != "$1" ]]; then printf '%s\n' "$cur" > "$STATE_DIR/previous"; fi
  printf '%s\n' "$1" > "$STATE_DIR/current"
}

release_field() {  # release_field <tag> <field>
  [[ -f "$STATE_DIR/releases/$1.json" ]] || return 1
  python3 -c 'import json,sys; v=json.load(open(sys.argv[1])).get(sys.argv[2]); print("" if v is None else v)' "$STATE_DIR/releases/$1.json" "$2"
}

json_obj() {  # json_obj k1 v1 k2 v2 ... -> one-line JSON object (string values)
  python3 -c 'import json,sys; a=sys.argv[1:]; print(json.dumps(dict(zip(a[::2], a[1::2]))))' "$@"
}

prune_images() {  # keep current, previous and the newest KEEP_RELEASES deployed tags; only touches $IMAGE_PREFIX/* release tags
  local keep tag ref
  keep=$(python3 - "$STATE_DIR" "$KEEP_RELEASES" <<'PY'
import json, sys, pathlib
d, n = pathlib.Path(sys.argv[1]), int(sys.argv[2])
keep = []
for f in ("current", "previous"):
    p = d / f
    if p.exists() and p.read_text().strip():
        keep.append(p.read_text().strip())
hist = []
if (d / "history.jsonl").exists():
    for line in (d / "history.jsonl").read_text().splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("result") in ("deployed", "rolled_back"):
            hist.append(r["tag"])
for t in reversed(hist):
    if t not in keep and len(keep) < max(n, 2):
        keep.append(t)
print(" ".join(keep))
PY
)
  while read -r ref; do
    [[ -n "$ref" ]] || continue
    tag=${ref##*:}
    [[ "$tag" =~ ^[0-9a-f]{12}(-dirty[0-9]+)?$ ]] || continue
    [[ " $keep " == *" $tag "* ]] && continue
    docker image rm "$ref" >/dev/null 2>&1 && log "pruned image $ref"
  done < <(docker image ls --format '{{.Repository}}:{{.Tag}}' | grep "^$IMAGE_PREFIX/" || true)
}
