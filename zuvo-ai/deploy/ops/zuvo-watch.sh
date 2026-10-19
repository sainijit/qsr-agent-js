#!/usr/bin/env bash
# Host watch for the ZuvoCloud production stack: collects a snapshot and alerts on threshold breaches.
#
#   deploy/ops/zuvo-watch.sh            # evaluate + notify (cron, every 5 min)
#   deploy/ops/zuvo-watch.sh --status   # operator status view: evaluate + print, never notifies or advances state
#
# Checks: disk (docker root, backup dir), memory and swap, every long-running container (running/healthy/OOM/restarts),
# edge /api/v1/health, newest verified backup age, and the dashboard-api ops metrics (Kafka lag/DLQ, outbox, outbound
# UNKNOWN/stuck/retry, RAG indexing, Gemini latency/failure ratios/cost, Postgres connections). Rules and thresholds are
# in deploy/ops/watch_eval.py. Checks that a deploy legitimately disturbs are suppressed while the deploy lock is held.
#
# Alert destinations (both optional, both used if set; with neither the script prints and exits 3 - no pretend alerts):
#   ZUVO_ALERT_SNS_TOPIC_ARN   aws sns publish via the instance role (email/SMS subscriptions on the topic)
#   ZUVO_ALERT_WEBHOOK_URL     Slack / Google Chat incoming webhook (treated as a secret; never printed)
# Other: BACKUP_DIR, BACKUP_MAX_AGE_HOURS (8), ZUVO_AI_DAILY_BUDGET_INR, ZUVO_ALERT_REMIND_HOURS (6),
#        ZUVO_WATCH_STATE (default $ZUVO_STATE_DIR/watch-state.json), plus the scripts/deploy/lib.sh variables.
# Exit codes: 0 ok | 1 warnings | 2 critical | 3 findings but no destination configured | 4 notification failed
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=../../scripts/deploy/lib.sh
. "$HERE/../../scripts/deploy/lib.sh"

STATUS=0
case "${1:-}" in
  --status) STATUS=1 ;;
  "") ;;
  -h|--help) sed -n 2,20p "$0"; exit 0 ;;
  *) die 2 "unknown argument: $1" ;;
esac

export ZUVO_ALERT_SNS_TOPIC_ARN=${ZUVO_ALERT_SNS_TOPIC_ARN:-$(env_get ZUVO_ALERT_SNS_TOPIC_ARN)}
export ZUVO_ALERT_WEBHOOK_URL=${ZUVO_ALERT_WEBHOOK_URL:-$(env_get ZUVO_ALERT_WEBHOOK_URL)}
BDIR=$(env_get BACKUP_DIR); BDIR=${BDIR:-/var/backups/zuvo}
STATE=${ZUVO_WATCH_STATE:-$STATE_DIR/watch-state.json}
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT

# deploy in progress?  (flock -n succeeds only if nobody holds the lock)
DEPLOYING=false
if [[ -f "$STATE_DIR/.deploy.lock" ]] && ! flock -n "$STATE_DIR/.deploy.lock" true 2>/dev/null; then DEPLOYING=true; fi

# host
DOCKER_ROOT=$(docker info -f '{{.DockerRootDir}}' 2>/dev/null || echo /var/lib/docker)
: > "$TMP/disks"
for p in "$DOCKER_ROOT" "$BDIR"; do
  [[ -d "$p" ]] || continue
  df -P "$p" | awk -v p="$p" 'NR==2 {gsub("%","",$5); print p "\t" $5}' >> "$TMP/disks"
done
awk '/^MemTotal:/{t=$2} /^MemAvailable:/{a=$2} /^SwapTotal:/{st=$2} /^SwapFree:/{sf=$2}
     END {printf "%.1f\t%s\n", a*100/t, (st>0 ? sprintf("%.1f", (st-sf)*100/st) : "null")}' /proc/meminfo > "$TMP/mem"

# containers
mapfile -t SERVICES < <(dc config --services 2>/dev/null | grep -vxF -e migrator -e kafka-init)
: > "$TMP/containers"
for svc in "${SERVICES[@]}"; do
  cid=$(dc ps -aq "$svc" 2>/dev/null | head -1)
  [[ -n "$cid" ]] || continue
  docker inspect -f "$svc"'	{{.State.Status}}	{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}	{{.State.OOMKilled}}	{{.RestartCount}}' "$cid" >> "$TMP/containers" 2>/dev/null
done

# backup: newest verified dump (failed verifications are kept as *.dump.unverified)
BACKUP_AGE=null; UNVERIFIED=0
if [[ -d "$BDIR" ]]; then
  newest=$(find "$BDIR" -maxdepth 1 -name 'zuvo_db-*.dump' -printf '%T@\n' 2>/dev/null | sort -n | tail -1)
  [[ -n "$newest" ]] && BACKUP_AGE=$(awk -v n="$newest" -v now="$(date +%s)" 'BEGIN {printf "%.2f", (now-n)/3600}')
  UNVERIFIED=$(find "$BDIR" -maxdepth 1 -name '*.unverified' 2>/dev/null | wc -l)
fi

# edge
DOMAIN=$(env_get PUBLIC_DOMAIN); HTTPS_PORT=$(env_get HTTPS_PORT); HTTPS_PORT=${HTTPS_PORT:-443}
EDGE_URL="https://$DOMAIN"; [[ "$HTTPS_PORT" != 443 ]] && EDGE_URL="$EDGE_URL:$HTTPS_PORT"; EDGE_URL="$EDGE_URL/api/v1/health"
K=(); [[ "$DOMAIN" == localhost ]] && K=(-k)
EDGE_CODE=$(curl -sS "${K[@]}" --noproxy '*' --max-time 10 -o /dev/null -w '%{http_code}' "$EDGE_URL" 2>/dev/null || echo 0)

# application metrics
METRICS_ERR=""
if ! dc exec -T dashboard-api python -m zuvo_common.opsmetrics > "$TMP/metrics" 2> "$TMP/metrics.err"; then
  METRICS_ERR=$(tail -1 "$TMP/metrics.err" | cut -c1-200); : > "$TMP/metrics"
fi

python3 - "$TMP" "$DEPLOYING" "$BDIR" "$BACKUP_AGE" "$UNVERIFIED" "$EDGE_URL" "$EDGE_CODE" "$METRICS_ERR" \
         "$(env_get ZUVO_AI_DAILY_BUDGET_INR)" "$(env_get BACKUP_MAX_AGE_HOURS)" "${SERVICES[@]}" > "$TMP/snapshot.json" <<'PY'
import json, sys, time
from pathlib import Path
tmp, deploying, bdir, bage, unverified, edge_url, edge_code, merr, budget, maxage, *services = sys.argv[1:]
t = Path(tmp)
disks = [{"path": p, "used_pct": float(u)} for p, u in (l.split("\t") for l in t.joinpath("disks").read_text().splitlines() if l)]
mem, swap = t.joinpath("mem").read_text().split()
containers = []
for line in t.joinpath("containers").read_text().splitlines():
    s, state, health, oom, restarts = line.split("\t")
    containers.append({"service": s, "state": state, "health": health, "oom": oom == "true", "restarts": int(restarts)})
metrics = None
raw = t.joinpath("metrics").read_text().strip()
if raw:
    try:
        metrics = json.loads(raw)
    except ValueError:
        merr = merr or "opsmetrics returned invalid JSON"
print(json.dumps({
    "collected_at": time.time(), "deploy_in_progress": deploying == "true",
    "host": {"disks": disks, "mem_available_pct": float(mem), "swap_used_pct": None if swap == "null" else float(swap)},
    "containers": containers, "expected_services": services,
    "backup": {"dir": bdir, "newest_age_hours": None if bage == "null" else float(bage), "unverified": int(unverified)},
    "edge": {"url": edge_url, "code": int(edge_code)},
    "metrics": metrics, "metrics_error": merr or None,
    "thresholds": {"ai_daily_budget_inr": float(budget) if budget else None,
                   "backup_max_age_hours": float(maxage) if maxage else 8.0},
}))
PY

ARGS=(--state "$STATE"); [[ $STATUS -eq 1 ]] && ARGS+=(--no-notify)
mkdir -p "$(dirname "$STATE")"
python3 "$HERE/watch_eval.py" "${ARGS[@]}" < "$TMP/snapshot.json"
