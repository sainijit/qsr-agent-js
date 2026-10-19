#!/usr/bin/env bash
# Pre-deployment checks. Changes nothing. Prints variable NAMES only, never values.
#
#   scripts/deploy/preflight.sh [--local-rehearsal]
#
# Checks: tools and versions, env file (scripts/preflight.py production rules), merged compose config (with
# ZUVO_COMPOSE_EXTRA), git state, disk and memory headroom, backup destination.
# --local-rehearsal: only for a production-mode rehearsal on a dev host with PUBLIC_DOMAIN=localhost. It tolerates the
# PUBLIC_DOMAIN/ALLOWED_ORIGINS localhost errors and nothing else.
# Exit codes: 0 ok (warnings allowed) | 1 errors found | 2 usage
set -euo pipefail
# shellcheck source=lib.sh
. "$(dirname "$0")/lib.sh"

REHEARSAL=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --local-rehearsal) REHEARSAL=1; shift ;;
    -h|--help) sed -n 2,11p "$0"; exit 0 ;;
    *) die 2 "unknown argument: $1" ;;
  esac
done

ERRORS=0
ok()   { printf 'ok    %s\n' "$*"; }
warn() { printf 'WARN  %s\n' "$*"; }
err()  { printf 'ERROR %s\n' "$*"; ERRORS=$((ERRORS + 1)); }

# ---- tools
for t in docker python3 git flock curl; do command -v "$t" >/dev/null && ok "tool $t" || err "tool $t not installed"; done
CV=$(docker compose version --short 2>/dev/null || echo 0)
if python3 -c 'import sys; v=[int(x) for x in sys.argv[1].lstrip("v").split("-")[0].split(".")[:2]]; sys.exit(0 if v >= [2, 24] else 1)' "$CV" 2>/dev/null; then
  ok "docker compose $CV (>= 2.24)"
else
  err "docker compose $CV: need >= 2.24 (!reset / !override in docker-compose.prod.yml)"
fi
docker info >/dev/null 2>&1 && ok "docker daemon reachable" || err "docker daemon not reachable (is this user in the docker group?)"

# ---- env file
if [[ ! -f "$ENV_FILE" ]]; then
  err "env file $ENV_FILE not found (deploy/aws/render-env.sh writes it)"
else
  MODE=$(stat -c %a "$ENV_FILE")
  [[ "$MODE" =~ ^[0-7]00$ ]] && ok "env file mode $MODE" || warn "env file mode $MODE: run chmod 600 $ENV_FILE"
  OUT=$(python3 "$ROOT/scripts/preflight.py" --env-file "$ENV_FILE" 2>&1) || true
  DOMAIN=$(env_get PUBLIC_DOMAIN)
  while IFS= read -r line; do
    case "$line" in
      ERROR*)
        if [[ $REHEARSAL -eq 1 && "$DOMAIN" == "localhost" && "$line" =~ ^ERROR\ (PUBLIC_DOMAIN|ALLOWED_ORIGINS): ]]; then
          warn "${line#ERROR } (tolerated: --local-rehearsal)"
        else
          err "${line#ERROR }"
        fi ;;
      WARN*) warn "${line#WARN  }" ;;
    esac
  done <<<"$OUT"
  [[ $REHEARSAL -eq 1 && "$DOMAIN" != "localhost" ]] && err "--local-rehearsal is only allowed with PUBLIC_DOMAIN=localhost"
  [[ "$(env_get APP_ENV)" == "production" ]] && ok "APP_ENV=production" || err "APP_ENV must be production for this deploy path"
fi

# ---- compose
if CERR=$(dc config -q 2>&1); then
  ok "compose config valid ($(printf '%s ' "${FILE_ARGS[@]}" | sed "s#$ROOT/##g; s#-f ##g"))"
else
  err "compose config invalid: $(head -3 <<<"$CERR")"
fi
PUBLISHED=$(dc config --format json 2>/dev/null | python3 -c '
import json,sys
cfg=json.load(sys.stdin)
print(" ".join(sorted(n for n,s in cfg.get("services",{}).items() if s.get("ports"))))' 2>/dev/null || echo "?")
[[ "$PUBLISHED" == "caddy" ]] && ok "only caddy publishes ports" || err "services publishing host ports: $PUBLISHED (only caddy may)"

# ---- git
if git -C "$ROOT" rev-parse --git-dir >/dev/null 2>&1; then
  ok "git commit $(git -C "$ROOT" rev-parse --short=12 HEAD)"
  [[ -z "$(git -C "$ROOT" status --porcelain --untracked-files=no)" ]] && ok "git tree clean" ||
    warn "git tree has uncommitted changes: deploy.sh refuses unless --allow-dirty"
else
  err "$ROOT is not a git checkout (release records need a commit)"
fi

# ---- capacity
DROOT=$(docker info -f '{{.DockerRootDir}}' 2>/dev/null || echo /var/lib/docker)
FREE_GB=$(df -Pk "$DROOT" 2>/dev/null | awk 'NR==2 {printf "%d", $4/1048576}')
[[ -n "$FREE_GB" ]] || FREE_GB=$(df -Pk / | awk 'NR==2 {printf "%d", $4/1048576}')
if (( FREE_GB >= ${ZUVO_MIN_FREE_GB:-5} )); then ok "disk free ${FREE_GB} GB on docker root"; else err "disk free ${FREE_GB} GB on docker root (< ${ZUVO_MIN_FREE_GB:-5} GB): prune old images / grow the volume"; fi
AVAIL_MB=$(awk '/MemAvailable/ {printf "%d", $2/1024}' /proc/meminfo)
SWAP_MB=$(awk '/SwapTotal/ {printf "%d", $2/1024}' /proc/meminfo)
(( AVAIL_MB >= 600 )) && ok "memory available ${AVAIL_MB} MB" || warn "memory available ${AVAIL_MB} MB: image builds may swap or OOM"
(( SWAP_MB > 0 )) && ok "swap ${SWAP_MB} MB" || warn "no swap configured (deploy/aws/README.md section 5)"

# ---- backups
if [[ -n "$(env_get BACKUP_S3_URI)" ]]; then
  ok "backup destination configured (BACKUP_S3_URI)"
  command -v aws >/dev/null && ok "aws cli present" || err "BACKUP_S3_URI set but aws cli missing"
else
  warn "BACKUP_S3_URI not set: pre-migration backups stay on this host only"
fi

echo
echo "preflight: $ERRORS error(s)"
(( ERRORS == 0 ))
