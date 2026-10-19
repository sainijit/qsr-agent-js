#!/usr/bin/env bash
# Shared helpers for backup-db.sh / verify-backup.sh / restore-db.sh. Source only.
# Talks to Postgres with `docker exec` over the container's local socket: no password ever appears in arguments,
# environment listings or logs.

ZUVO_DB=${ZUVO_DB:-zuvo_db}
ZUVO_DB_OWNER=${ZUVO_DB_OWNER:-zuvo_owner}
ZUVO_APP_ROLE=${ZUVO_APP_ROLE:-zuvo_app}
PG_IMAGE=${PG_IMAGE:-zuvo-postgres:16-pgvector}
# tables whose presence (and row counts) prove a usable restore
CRITICAL_TABLES=(tenants users memberships whatsapp_numbers customers conversations messages leads followup_tasks
                 agent_runs approvals audit_logs kb_documents kb_chunks outbox_events processed_events outbound_messages)
# tables added after the baseline, keyed by the alembic revision that created them. A backup of an older
# schema is still a valid backup: such a table is only required once the restored schema reaches its revision.
declare -A TABLE_SINCE=([outbox_events]=0004 [processed_events]=0004 [outbound_messages]=0005)

log()  { printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >&2; }
die()  { local code=$1; shift; log "ERROR: $*"; exit "$code"; }

# container id of the compose service `postgres` in project $1 (works without the compose files)
pg_container() {
  local project=$1 cid
  cid=$(docker ps -q --filter "label=com.docker.compose.project=$project" --filter "label=com.docker.compose.service=postgres")
  [[ -n "$cid" ]] || return 1
  [[ $(wc -l <<<"$cid") -eq 1 ]] || return 1
  echo "$cid"
}

# psql in a container: psql_c <cid> <db> <sql>  -> unaligned, tuples only
psql_c() {
  docker exec -i "$1" psql -X -v ON_ERROR_STOP=1 -U "$ZUVO_DB_OWNER" -d "$2" -tAq -c "$3"
}

sha256_of() { sha256sum "$1" | awk '{print $1}'; }

# Start an isolated throw-away Postgres (no network, no volume, trust auth on the local socket only).
# Prints the container id. The caller must `scratch_stop` it.
scratch_start() {
  local name cid
  name="zuvo-restore-check-$(date -u +%Y%m%d%H%M%S)-$$"
  docker image inspect "$PG_IMAGE" >/dev/null 2>&1 || die 2 "image $PG_IMAGE not found (build it: docker compose build postgres)"
  cid=$(docker run -d --rm --name "$name" --network none --label zuvo.purpose=restore-check \
        -e POSTGRES_USER="$ZUVO_DB_OWNER" -e POSTGRES_DB=postgres -e POSTGRES_HOST_AUTH_METHOD=trust \
        --tmpfs "/var/lib/postgresql/data:rw,size=${SCRATCH_TMPFS:-2g}" "$PG_IMAGE") || die 2 "could not start scratch postgres"
  for _ in $(seq 1 90); do
    # the image's init runs a temporary server first; wait for the real one
    if docker logs "$cid" 2>&1 | grep -q "PostgreSQL init process complete" &&
       docker exec "$cid" pg_isready -q -U "$ZUVO_DB_OWNER" -d postgres 2>/dev/null; then
      echo "$cid"; return 0
    fi
    sleep 1
  done
  docker rm -f "$cid" >/dev/null 2>&1
  die 2 "scratch postgres did not become ready"
}

scratch_stop() { if [[ -n "${1:-}" ]]; then docker rm -f "$1" >/dev/null 2>&1 || true; fi; }

# restore_into <cid> <db> <dumpfile>: db must not exist; app role is created NOLOGIN for GRANTs if missing
restore_into() {
  local cid=$1 db=$2 file=$3
  psql_c "$cid" postgres "DO \$\$BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '$ZUVO_APP_ROLE') THEN
                             CREATE ROLE $ZUVO_APP_ROLE NOLOGIN NOSUPERUSER NOBYPASSRLS; END IF; END\$\$" >/dev/null
  psql_c "$cid" postgres "CREATE DATABASE \"$db\" OWNER $ZUVO_DB_OWNER" >/dev/null
  docker exec -i "$cid" pg_restore -U "$ZUVO_DB_OWNER" -d "$db" --exit-on-error --no-password < "$file"
}

# check_restored <cid> <db>: prints a JSON object with schema facts + row counts; exit 1 if anything is missing
check_restored() {
  local cid=$1 db=$2 missing=() t ver ext rls pol counts=""
  ver=$(psql_c "$cid" "$db" "SELECT version_num FROM alembic_version") || return 1
  ext=$(psql_c "$cid" "$db" "SELECT extversion FROM pg_extension WHERE extname = 'vector'")
  rls=$(psql_c "$cid" "$db" "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                              WHERE n.nspname = 'public' AND c.relkind = 'r' AND c.relrowsecurity AND c.relforcerowsecurity")
  pol=$(psql_c "$cid" "$db" "SELECT count(*) FROM pg_policies WHERE schemaname = 'public'")
  for t in "${CRITICAL_TABLES[@]}"; do
    # revisions are zero-padded numbers, so string comparison orders them
    if [[ -n "${TABLE_SINCE[$t]:-}" && "$ver" < "${TABLE_SINCE[$t]}" ]]; then continue; fi
    if [[ $(psql_c "$cid" "$db" "SELECT to_regclass('public.$t') IS NOT NULL") != "t" ]]; then
      missing+=("$t"); continue
    fi
    counts+="\"$t\": $(psql_c "$cid" "$db" "SET row_security = off; SELECT count(*) FROM public.$t" | tail -1), "
  done
  local miss_json=""
  [[ ${#missing[@]} -gt 0 ]] && miss_json=$(printf '"%s",' "${missing[@]}") && miss_json=${miss_json%,}
  printf '{"alembic_version": "%s", "pgvector": "%s", "rls_forced_tables": %s, "policies": %s, "missing_tables": [%s], "row_counts": {%s}}\n' \
    "$ver" "$ext" "$rls" "$pol" "$miss_json" "${counts%, }"
  [[ -n "$ver" && -n "$ext" && ${#missing[@]} -eq 0 && "$rls" -gt 0 && "$pol" -gt 0 ]]
}

# rows of the critical tables in a live database (same JSON shape as check_restored's row_counts)
live_counts() {
  local cid=$1 db=$2 t out=""
  for t in "${CRITICAL_TABLES[@]}"; do
    [[ $(psql_c "$cid" "$db" "SELECT to_regclass('public.$t') IS NOT NULL") == "t" ]] || continue
    out+="\"$t\": $(psql_c "$cid" "$db" "SET row_security = off; SELECT count(*) FROM public.$t" 2>/dev/null | tail -1 || echo null), "
  done
  echo "{${out%, }}"
}
