#!/usr/bin/env bash
# Post-deploy health and smoke checks for the production stack. Read-only, except for one unsigned webhook POST, which
# must be rejected.
#
#   scripts/deploy/health-check.sh [--expect-tag TAG] [--expect-alembic REV] [--insecure]
#
# Checks:
#   containers   every long-running service is running, healthy (if it has a healthcheck), not OOM-killed, on TAG
#   readiness    gateway / dashboard-api / zuvo-ai-rag /readyz from inside their containers (DB, Kafka, Redis, schema)
#   database     alembic revision (= REV if given)
#   edge         HEALTH_URLS (default https://$PUBLIC_DOMAIN[:$HTTPS_PORT]/api/v1/health and /) return 200
#   smoke        http->https redirect; webhook verify handshake (META_VERIFY_TOKEN; sent via stdin, never in argv);
#                unsigned webhook POST rejected (401); dev-only endpoints 404
# --insecure skips TLS verification. It is applied automatically when PUBLIC_DOMAIN=localhost (Caddy's internal CA).
# Exit codes: 0 healthy | 1 one or more checks failed | 2 usage
set -uo pipefail
# shellcheck source=lib.sh
. "$(dirname "$0")/lib.sh"

EXPECT_TAG=""; EXPECT_REV=""; INSECURE=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --expect-tag) EXPECT_TAG=$2; shift 2 ;;
    --expect-alembic) EXPECT_REV=$2; shift 2 ;;
    --insecure) INSECURE=1; shift ;;
    -h|--help) sed -n 2,15p "$0"; exit 0 ;;
    *) die 2 "unknown argument: $1" ;;
  esac
done

FAILS=0
pass() { printf 'ok    %-28s %s\n' "$1" "${2:-}"; }
fail() { printf 'FAIL  %-28s %s\n' "$1" "${2:-}"; FAILS=$((FAILS + 1)); }

# ---- containers
mapfile -t SERVICES < <(dc config --services 2>/dev/null)
[[ ${#SERVICES[@]} -gt 0 ]] || die 2 "could not read compose services (check ZUVO_ENV_FILE / ZUVO_COMPOSE_EXTRA)"
for svc in "${SERVICES[@]}"; do
  [[ " ${ONE_SHOT_SERVICES[*]} " == *" $svc "* ]] && continue
  cid=$(dc ps -q "$svc" 2>/dev/null | head -1)
  if [[ -z "$cid" ]]; then fail "container $svc" "not created"; continue; fi
  IFS='|' read -r state health oom restarts image < <(docker inspect -f \
    '{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}|{{.State.OOMKilled}}|{{.RestartCount}}|{{.Config.Image}}' "$cid")
  detail="state=$state health=$health restarts=$restarts"
  if [[ "$state" != "running" ]]; then fail "container $svc" "$detail"; continue; fi
  if [[ "$health" != "healthy" && "$health" != "none" ]]; then fail "container $svc" "$detail"; continue; fi
  if [[ "$oom" == "true" ]]; then fail "container $svc" "OOM-killed ($detail)"; continue; fi
  if [[ -n "$EXPECT_TAG" && " ${BUILT_SERVICES[*]} " == *" $svc "* && "$image" != "$(image_of "$svc" "$EXPECT_TAG")" ]]; then
    fail "container $svc" "image $image, expected tag $EXPECT_TAG"; continue
  fi
  pass "container $svc" "$detail"
done

# ---- readiness from inside the containers (no published ports needed)
readyz() {  # readyz <service> <port>
  local body
  body=$(dc exec -T "$1" python -c "import urllib.request,sys
try:
    r = urllib.request.urlopen('http://127.0.0.1:$2/readyz', timeout=5); print(r.status, r.read().decode()[:300])
except urllib.error.HTTPError as e:
    print(e.code, e.read().decode()[:300]); sys.exit(1)
except Exception as e:
    print('error', type(e).__name__); sys.exit(1)" 2>&1)
  if [[ $? -eq 0 && "$body" == 200* ]]; then pass "readyz $1" "$(cut -c1-120 <<<"${body#200 }")"; else fail "readyz $1" "$(cut -c1-160 <<<"$body")"; fi
}
readyz gateway 8000
readyz dashboard-api 8001
readyz zuvo-ai-rag 8020

# ---- database schema
REV=$(db_alembic)
if [[ -z "$REV" ]]; then fail "database alembic" "could not read alembic_version"
elif [[ -n "$EXPECT_REV" && "$REV" != "$EXPECT_REV" ]]; then fail "database alembic" "$REV, expected $EXPECT_REV"
else pass "database alembic" "$REV"; fi

# ---- edge
DOMAIN=$(env_get PUBLIC_DOMAIN); HTTPS_PORT=$(env_get HTTPS_PORT); HTTP_PORT=$(env_get HTTP_PORT)
HTTPS_PORT=${HTTPS_PORT:-443}; HTTP_PORT=${HTTP_PORT:-80}
[[ "$DOMAIN" == "localhost" ]] && INSECURE=1
BASE="https://$DOMAIN"; [[ "$HTTPS_PORT" != "443" ]] && BASE="$BASE:$HTTPS_PORT"
CURL=(curl -sS --max-time 10 --noproxy '*')
[[ $INSECURE -eq 1 ]] && CURL+=(-k)
read -r -a URLS <<<"$(env_get HEALTH_URLS)"
[[ ${#URLS[@]} -gt 0 ]] || URLS=("$BASE/api/v1/health" "$BASE/")
for u in "${URLS[@]}"; do
  code=$("${CURL[@]}" -o /tmp/.zuvo-hc.$$ -w '%{http_code}' "$u" 2>/dev/null || echo 000)
  if [[ "$code" == 200 ]]; then
    if [[ "$u" == */api/v1/health ]] && ! python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); sys.exit(0 if d.get("status")=="ok" else 1)' /tmp/.zuvo-hc.$$ 2>/dev/null; then
      fail "edge $u" "200 but status != ok: $(head -c 200 /tmp/.zuvo-hc.$$)"
    else
      pass "edge $u" "200"
    fi
  else
    fail "edge $u" "HTTP $code"
  fi
done
rm -f /tmp/.zuvo-hc.$$

# ---- smoke
HBASE="http://$DOMAIN"; [[ "$HTTP_PORT" != "80" ]] && HBASE="$HBASE:$HTTP_PORT"
code=$("${CURL[@]}" -o /dev/null -w '%{http_code}' "$HBASE/" 2>/dev/null || echo 000)
[[ "$code" =~ ^30[178]$ ]] && pass "smoke http->https" "$code" || fail "smoke http->https" "HTTP $code"

TOKEN=$(env_get META_VERIFY_TOKEN)
if [[ -n "$TOKEN" ]]; then
  CH="hc$RANDOM$RANDOM"
  body=$(printf '%s' "$TOKEN" | "${CURL[@]}" -G "$BASE/api/v1/whatsapp/webhook" --data-urlencode "hub.mode=subscribe" \
         --data-urlencode "hub.challenge=$CH" --data-urlencode "hub.verify_token@-" 2>/dev/null || true)
  [[ "$body" == "$CH" ]] && pass "smoke webhook verify" "challenge echoed" || fail "smoke webhook verify" "challenge not echoed"
else
  fail "smoke webhook verify" "META_VERIFY_TOKEN not set"
fi
code=$("${CURL[@]}" -o /dev/null -w '%{http_code}' -X POST -H 'Content-Type: application/json' --data '{"object":"whatsapp_business_account","entry":[]}' "$BASE/api/v1/whatsapp/webhook" 2>/dev/null || echo 000)
[[ "$code" == 401 || "$code" == 403 ]] && pass "smoke unsigned webhook" "rejected $code" || fail "smoke unsigned webhook" "HTTP $code (must be 401/403)"
for p in /api/v1/auth/dev-login /api/v1/messages/simulate-inbound /docs; do
  code=$("${CURL[@]}" -o /dev/null -w '%{http_code}' "$BASE$p" 2>/dev/null || echo 000)
  [[ "$code" == 404 ]] && pass "smoke blocked $p" "404" || fail "smoke blocked $p" "HTTP $code (must be 404)"
done

echo
if (( FAILS == 0 )); then echo "health-check: all checks passed"; exit 0; fi
echo "health-check: $FAILS check(s) FAILED"; exit 1
