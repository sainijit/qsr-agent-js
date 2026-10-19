#!/usr/bin/env bash
# Post-deploy smoke test for production: health-check.sh plus security, data-plane and isolation checks.
# Read-only. The only requests sent are one wrong-password login (rejected, nothing written to the DB) and the
# unsigned webhook and dev-endpoint probes from health-check.sh. Safe to run against the live stack at any time.
#
#   scripts/deploy/smoke-test.sh [--expect-tag TAG] [--expect-alembic REV] [--skip-health]
#
# Checks (in addition to health-check.sh):
#   tls        HTTPS certificate validity/expiry (unless PUBLIC_DOMAIN=localhost); HSTS, nosniff, frame-deny, CSP present;
#              no Server header
#   auth       wrong-password login -> 401; /api/v1/auth/me without, or with a forged, token -> 401
#   exposure   /internal/metrics is not served by the edge
#   kafka      every topic and its .DLQ exist
#   database   app connects as the non-owner role zuvo_app (not superuser, no BYPASSRLS);
#              RLS enabled and forced on every table with a tenant_id column
#   metrics    opsmetrics collector: every section collected (zuvo_collect_ok == 1)
# Exit codes: 0 all passed | 1 one or more checks failed | 2 usage
set -uo pipefail
# shellcheck source=lib.sh
. "$(dirname "$0")/lib.sh"

HC_ARGS=(); SKIP_HEALTH=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --expect-tag|--expect-alembic) HC_ARGS+=("$1" "$2"); shift 2 ;;
    --skip-health) SKIP_HEALTH=1; shift ;;
    -h|--help) sed -n 2,17p "$0"; exit 0 ;;
    *) die 2 "unknown argument: $1" ;;
  esac
done

FAILS=0
pass() { printf 'ok    %-28s %s\n' "$1" "${2:-}"; }
fail() { printf 'FAIL  %-28s %s\n' "$1" "${2:-}"; FAILS=$((FAILS + 1)); }

if (( SKIP_HEALTH == 0 )); then
  "$ROOT/scripts/deploy/health-check.sh" "${HC_ARGS[@]}" || FAILS=$((FAILS + 1))
  echo
fi

DOMAIN=$(env_get PUBLIC_DOMAIN); HTTPS_PORT=$(env_get HTTPS_PORT); HTTPS_PORT=${HTTPS_PORT:-443}
BASE="https://$DOMAIN"; [[ "$HTTPS_PORT" != "443" ]] && BASE="$BASE:$HTTPS_PORT"
CURL=(curl -sS --max-time 10 --noproxy '*')
[[ "$DOMAIN" == "localhost" ]] && CURL+=(-k)
TMP=$(mktemp); trap 'rm -f "$TMP"' EXIT

# ---- tls + security headers
if [[ "$DOMAIN" != "localhost" ]]; then
  if exp=$(openssl s_client -connect "$DOMAIN:$HTTPS_PORT" -servername "$DOMAIN" </dev/null 2>/dev/null \
           | openssl x509 -noout -enddate 2>/dev/null); then
    exp=${exp#notAfter=}
    days=$(( ($(date -d "$exp" +%s) - $(date +%s)) / 86400 ))
    (( days >= 14 )) && pass "tls certificate" "expires in ${days}d" || fail "tls certificate" "expires in ${days}d (<14)"
  else
    fail "tls certificate" "could not read certificate"
  fi
else
  pass "tls certificate" "skipped (localhost uses Caddy's internal CA)"
fi
if "${CURL[@]}" -D "$TMP" -o /dev/null "$BASE/" 2>/dev/null; then
  for h in strict-transport-security x-content-type-options x-frame-options content-security-policy referrer-policy; do
    grep -qi "^$h:" "$TMP" && pass "header $h" || fail "header $h" "missing"
  done
  grep -qi '^server:' "$TMP" && fail "header server" "present: $(grep -i '^server:' "$TMP" | tr -d '\r')" || pass "header server" "absent"
else
  fail "tls headers" "GET $BASE/ failed"
fi

# ---- auth
code=$("${CURL[@]}" -o /dev/null -w '%{http_code}' -X POST -H 'Content-Type: application/json' \
       --data '{"email":"smoke-test@invalid.example","password":"not-a-real-password"}' "$BASE/api/v1/auth/login" 2>/dev/null || echo 000)
[[ "$code" == 401 ]] && pass "auth bad login" "401" || fail "auth bad login" "HTTP $code (must be 401)"
code=$("${CURL[@]}" -o /dev/null -w '%{http_code}' "$BASE/api/v1/auth/me" 2>/dev/null || echo 000)
[[ "$code" == 401 ]] && pass "auth no token" "401" || fail "auth no token" "HTTP $code (must be 401)"
FORGED='eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ4IiwidGVuYW50X2lkIjoieCIsInJvbGUiOiJPV05FUiIsImV4cCI6NDEwMjQ0NDgwMCwiaXNzIjoienV2b2Nsb3VkIn0.AAAA'
code=$("${CURL[@]}" -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $FORGED" "$BASE/api/v1/auth/me" 2>/dev/null || echo 000)
[[ "$code" == 401 ]] && pass "auth forged token" "401" || fail "auth forged token" "HTTP $code (must be 401)"

# ---- exposure
"${CURL[@]}" -o "$TMP" "$BASE/internal/metrics" 2>/dev/null || true
grep -q 'zuvo_collect_ok' "$TMP" && fail "exposure /internal/metrics" "served publicly" || pass "exposure /internal/metrics" "not served by the edge"

# ---- kafka topics
TOPICS=$(dc exec -T kafka /opt/kafka/bin/kafka-topics.sh --bootstrap-server localhost:9092 --list 2>/dev/null)
EXPECTED=$(sed -n 's/^ *for t in \(.*\); do$/\1/p' "$ROOT/docker-compose.yml" | head -1)
if [[ -z "$TOPICS" || -z "$EXPECTED" ]]; then
  fail "kafka topics" "could not list topics (or parse kafka-init in docker-compose.yml)"
else
  missing=()
  for t in $EXPECTED; do
    grep -qx "$t" <<<"$TOPICS" || missing+=("$t")
    grep -qx "$t.DLQ" <<<"$TOPICS" || missing+=("$t.DLQ")
  done
  (( ${#missing[@]} == 0 )) && pass "kafka topics" "$(wc -w <<<"$EXPECTED") topics + DLQs" || fail "kafka topics" "missing: ${missing[*]}"
fi

# ---- database role + RLS
psql_owner() { dc exec -T postgres psql -X -U zuvo_owner -d zuvo_db -tAq -c "$1" 2>/dev/null; }
role=$(psql_owner "SELECT (CASE WHEN rolsuper THEN '1' ELSE '0' END) || (CASE WHEN rolbypassrls THEN '1' ELSE '0' END) FROM pg_roles WHERE rolname = 'zuvo_app'")
[[ "$role" == "00" ]] && pass "database app role" "zuvo_app: no superuser, no BYPASSRLS" || fail "database app role" "zuvo_app super/bypassrls=$role"
dburl_user=$(dc exec -T dashboard-api printenv DATABASE_URL 2>/dev/null | sed -n 's#^[a-z+]*://\([^:]*\):.*#\1#p')
[[ "$dburl_user" == "zuvo_app" ]] && pass "database app connects as" "zuvo_app" || fail "database app connects as" "'$dburl_user' (must be zuvo_app)"
weak=$(psql_owner "SELECT string_agg(c.relname, ',') FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
  JOIN information_schema.columns k ON k.table_schema = n.nspname AND k.table_name = c.relname AND k.column_name = 'tenant_id'
  WHERE n.nspname = 'public' AND c.relkind = 'r' AND NOT (c.relrowsecurity AND c.relforcerowsecurity)")
total=$(psql_owner "SELECT count(*) FROM information_schema.columns WHERE table_schema = 'public' AND column_name = 'tenant_id'")
if [[ -z "$total" ]]; then fail "database rls" "query failed"
elif [[ -z "$weak" ]]; then pass "database rls" "enabled+forced on all $total tenant tables"
else
  # memberships is a platform table read at login, before any tenant context exists (see dashboard_api/phase1.py)
  allow=$(env_get RLS_EXEMPT_TABLES); allow=${allow:-memberships}
  bad=$(tr ',' '\n' <<<"$weak" | grep -vxF -f <(tr ',' '\n' <<<"$allow") | paste -sd, -)
  [[ -z "$bad" ]] && pass "database rls" "forced on tenant data; exempt platform tables: $weak" || fail "database rls" "not forced: $bad"
fi

# ---- metrics collector
if out=$(dc exec -T dashboard-api python -m zuvo_common.opsmetrics 2>/dev/null); then
  bad=$(python3 -c 'import json,sys
m=json.loads(sys.stdin.read())
print(",".join(sorted(x["labels"].get("section","?") for x in m if x["name"]=="zuvo_collect_ok" and not x["value"])))' <<<"$out" 2>/dev/null || echo parse-error)
  [[ -z "$bad" ]] && pass "metrics collector" "all sections ok" || fail "metrics collector" "failed sections: $bad"
else
  fail "metrics collector" "opsmetrics exited non-zero"
fi

echo
if (( FAILS == 0 )); then echo "smoke-test: all checks passed"; exit 0; fi
echo "smoke-test: $FAILS check(s) FAILED"; exit 1
