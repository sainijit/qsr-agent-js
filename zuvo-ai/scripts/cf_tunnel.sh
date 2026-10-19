#!/usr/bin/env bash
# Public HTTPS URL for the gateway (Meta webhook) via a free Cloudflare quick tunnel.
#
# Works behind an HTTP CONNECT proxy (e.g. corporate proxy): cloudflared ignores proxies, so
#   1. the quick tunnel is requested with curl (proxy-aware), and
#   2. a socat relay forwards cloudflared's edge connection (TCP 7844, http2) through the proxy.
# Only the gateway is exposed (never the dashboard/UI).
#
# Usage: scripts/cf_tunnel.sh start [--new] | stop | status | url
#   start --new   request a fresh hostname (the URL changes -> update it in Meta)
# Env: CF_PROXY (default $HTTPS_PROXY, empty = direct), GATEWAY_PORT (8000)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STATE="$ROOT/.tunnel"
PORT="${GATEWAY_PORT:-8000}"
PROXY="${CF_PROXY-${HTTPS_PROXY:-${https_proxy:-}}}"
IMAGE="cloudflare/cloudflared:latest"
EDGE="region1.v2.argotunnel.com:7844"

url() { [[ -f "$STATE/host" ]] && echo "https://$(cat "$STATE/host")"; }

request_tunnel() {
  mkdir -p "$STATE" && chmod 700 "$STATE"
  local curl_proxy=() resp
  [[ -n "$PROXY" ]] && curl_proxy=(--proxy "$PROXY")
  resp="$(curl -fsS -m 30 -X POST "${curl_proxy[@]}" -H 'Content-Type: application/json' https://api.trycloudflare.com/tunnel)"
  printf '%s' "$resp" | python3 -c '
import json, os, sys
r, d = json.load(sys.stdin)["result"], sys.argv[1]
json.dump({"AccountTag": r["account_tag"], "TunnelSecret": r["secret"], "TunnelID": r["id"]}, open(d + "/creds.json", "w"))
open(d + "/host", "w").write(r["hostname"])
os.chmod(d + "/creds.json", 0o644)  # read by the non-root cloudflared container; directory stays 700
' "$STATE"
}

stop() { docker rm -f zuvo-cf-tunnel zuvo-cf-relay >/dev/null 2>&1 || true; }

start() {
  [[ "${1:-}" == "--new" || ! -f "$STATE/creds.json" ]] && request_tunnel
  stop
  local edge_args=()
  if [[ -n "$PROXY" ]]; then
    local hp="${PROXY#*://}"; hp="${hp%%/*}"
    docker run -d --name zuvo-cf-relay --restart unless-stopped --network host alpine/socat \
      TCP-LISTEN:7844,bind=127.0.0.1,fork,reuseaddr "PROXY:${hp%:*}:${EDGE},proxyport=${hp##*:}" >/dev/null
    edge_args=(--edge 127.0.0.1:7844)
  fi
  docker run -d --name zuvo-cf-tunnel --restart unless-stopped --network host \
    -v "$STATE/creds.json:/etc/cf/creds.json:ro" "$IMAGE" tunnel --no-autoupdate --protocol http2 "${edge_args[@]}" \
    --credentials-file /etc/cf/creds.json --url "http://localhost:$PORT" run "$(python3 -c "import json;print(json.load(open('$STATE/creds.json'))['TunnelID'])")" >/dev/null
  for _ in $(seq 1 20); do
    docker logs zuvo-cf-tunnel 2>&1 | grep -q "Registered tunnel connection" && break
    sleep 1
  done
  status
}

status() {
  if docker logs zuvo-cf-tunnel 2>&1 | grep -q "Registered tunnel connection"; then
    echo "tunnel: up"
  else
    echo "tunnel: down (docker logs zuvo-cf-tunnel)"; return 1
  fi
  local curl_proxy=()
  [[ -n "$PROXY" ]] && curl_proxy=(--proxy "$PROXY")
  echo "health: $(curl -s -m 20 "${curl_proxy[@]}" -o /dev/null -w '%{http_code}' "$(url)/api/v1/health")"
  echo "Meta callback URL: $(url)/api/v1/whatsapp/webhook"
}

case "${1:-}" in
  start) start "${2:-}" ;;
  stop) stop; echo "tunnel: stopped" ;;
  status) status ;;
  url) url ;;
  *) sed -n '2,12p' "$0"; exit 2 ;;
esac
