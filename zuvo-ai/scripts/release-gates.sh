#!/usr/bin/env bash
# Release gates: everything that must pass before a commit is tagged for production. Run from a dev machine/CI,
# never on the production VM (the integration suite needs the *development* compose Postgres).
#
#   scripts/release-gates.sh [--fast] [--no-integration]
#
#   unit          tests/unit (security, readiness, auth, outbox, ledger, dispatch state machine, watch evaluator, ...)
#   rag           zuvo-ai-rag/tests (chunking, API, reliability)
#   integration   tests/integration via scripts/test_rls.sh (RLS, cross-tenant RAG, outbound idempotency, golden RAG)
#                 - requires the dev stack's postgres (COMPOSE_PROJECT_NAME, default zuvo-ai); refuses APP_ENV=production
#   compose       merged prod compose renders with deploy/aws/env.production.example; only caddy publishes ports
#   secrets       no credentials in files that would be committed (tracked + untracked-not-ignored); .env is ignored
#   lint          shellcheck over scripts/deploy, scripts/backup, deploy/ops, deploy/aws
#
# --fast skips integration (and is what a pre-commit hook should run). --no-integration skips only integration.
# Env: PYTHON (default python3; must have the dev requirements), SHELLCHECK (default: shellcheck on PATH).
# Exit: 0 all gates passed | 1 a gate failed | 2 usage. A skipped gate is reported as SKIPPED, never as passed.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; cd "$ROOT" || exit 2
PY=${PYTHON:-python3}
SC=${SHELLCHECK:-$(command -v shellcheck || true)}
RUN_INT=1
for a in "$@"; do
  case "$a" in
    --fast|--no-integration) RUN_INT=0 ;;
    -h|--help) sed -n 2,17p "$0"; exit 0 ;;
    *) echo "unknown argument: $a" >&2; exit 2 ;;
  esac
done

declare -a NAMES RESULTS DETAILS
LOG=$(mktemp -d); trap 'rm -rf "$LOG"' EXIT
gate() {  # gate <name> <command...>
  local name=$1; shift
  local t0=$SECONDS
  printf '==> %-12s ' "$name"
  if "$@" >"$LOG/$name.log" 2>&1; then
    NAMES+=("$name"); RESULTS+=(PASS); DETAILS+=("$(tail -1 "$LOG/$name.log" | cut -c1-90) ($((SECONDS - t0))s)")
    echo PASS
  else
    NAMES+=("$name"); RESULTS+=(FAIL); DETAILS+=("see output above ($((SECONDS - t0))s)")
    echo FAIL; sed 's/^/    /' "$LOG/$name.log" | tail -40
  fi
}
skip() { NAMES+=("$1"); RESULTS+=(SKIPPED); DETAILS+=("$2"); printf '==> %-12s SKIPPED (%s)\n' "$1" "$2"; }

export NO_PROXY="${NO_PROXY:-},127.0.0.1,localhost" no_proxy="${no_proxy:-},127.0.0.1,localhost"

gate unit "$PY" -m pytest -q -p no:cacheprovider tests/unit
gate rag  bash -c "cd zuvo-ai-rag && '$PY' -m pytest -q -p no:cacheprovider tests"

if (( RUN_INT )); then
  if [[ -n "$(docker compose -p "${COMPOSE_PROJECT_NAME:-zuvo-ai}" ps -q postgres 2>/dev/null)" ]]; then
    gate integration env PYTHON="$PY" scripts/test_rls.sh
  else
    skip integration "dev postgres not running (docker compose -p ${COMPOSE_PROJECT_NAME:-zuvo-ai} up -d postgres)"
  fi
else
  skip integration "--fast/--no-integration"
fi

compose_gate() {
  local dc=(docker compose -p zuvo-gates --env-file deploy/aws/env.production.example -f docker-compose.yml -f docker-compose.prod.yml)
  "${dc[@]}" config -q || return 1
  local published
  published=$("${dc[@]}" config --format json | "$PY" -c 'import json,sys
c=json.load(sys.stdin); print(" ".join(sorted(n for n,s in c["services"].items() if s.get("ports"))))') || return 1
  [[ "$published" == "caddy" ]] || { echo "services publishing host ports: $published (only caddy may)"; return 1; }
  echo "prod compose renders; only caddy publishes ports"
}
gate compose compose_gate

secrets_gate() {
  local rc=0 f
  git check-ignore -q .env || { echo ".env is NOT gitignored"; rc=1; }
  git ls-files --error-unmatch .env >/dev/null 2>&1 && { echo ".env is tracked by git"; rc=1; }
  # Gemini/Google, AWS access key, Meta long-lived token, GitHub/Slack tokens, private keys, assigned DB/JWT secrets
  local pat='AIza[0-9A-Za-z_-]{35}|AKIA[0-9A-Z]{16}|EAA[A-Za-z0-9]{80,}|gh[pousr]_[A-Za-z0-9]{36}|xox[baprs]-[A-Za-z0-9-]{10,}|-----BEGIN [A-Z ]*PRIVATE KEY-----'
  local pat2='^[[:space:]]*(GEMINI_API_KEY|META_ACCESS_TOKEN|META_APP_SECRET|JWT_SECRET|POSTGRES_PASSWORD|ZUVO_APP_DB_PASSWORD|AWS_SECRET_ACCESS_KEY)[[:space:]]*[=:][[:space:]]*["'"'"']?[A-Za-z0-9+/_-]{20,}'
  local hits=()
  while IFS= read -r -d '' f; do
    [[ -f "$f" ]] || continue
    case "$f" in *.png|*.jpg|*.jpeg|*.gif|*.ico|*.woff*|*.pdf|*.dump|*.lock|*package-lock.json) continue ;; esac
    if grep -IqE "$pat" "$f" 2>/dev/null; then hits+=("$f (credential pattern)"); fi
    if grep -IE "$pat2" "$f" 2>/dev/null | grep -qviE 'change[-_]?me|example|placeholder|generate|mock|dummy|fake|replace|x{8,}|dev[-_]only|not[-_]a[-_]real|\$\{'; then
      hits+=("$f (secret assigned a literal value)")
    fi
  done < <(git ls-files -z --cached --others --exclude-standard)
  if (( ${#hits[@]} )); then printf 'possible secret in: %s\n' "${hits[@]}"; rc=1; fi   # file names only, never the value
  (( rc == 0 )) && echo "no credential patterns in $(git ls-files --cached --others --exclude-standard | wc -l) committable files"
  return $rc
}
gate secrets secrets_gate

if [[ -n "$SC" ]]; then
  gate shellcheck "$SC" -x -S warning scripts/deploy/*.sh scripts/deploy/dcp scripts/backup/*.sh deploy/ops/*.sh deploy/aws/*.sh scripts/release-gates.sh scripts/test_rls.sh
else
  skip shellcheck "shellcheck not installed (set SHELLCHECK=/path/to/shellcheck)"
fi

echo; echo "Release gates"; FAILED=0
for i in "${!NAMES[@]}"; do
  printf '  %-12s %-8s %s\n' "${NAMES[$i]}" "${RESULTS[$i]}" "${DETAILS[$i]}"
  [[ "${RESULTS[$i]}" == FAIL ]] && FAILED=1
done
if (( FAILED )); then echo "RESULT: FAIL - do not release"; exit 1; fi
if [[ " ${RESULTS[*]} " == *" SKIPPED "* ]]; then echo "RESULT: PASS with skipped gates - run the skipped gates before a production release"; else echo "RESULT: PASS"; fi
exit 0
