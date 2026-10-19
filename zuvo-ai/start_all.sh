#!/usr/bin/env bash
# ZuvoCloud launcher: builds and starts every microservice container via docker compose.
set -euo pipefail
cd "$(dirname "$0")"
[ -f .env ] || { echo "Missing .env — copy .env.example to .env and fill it in."; exit 1; }
docker compose up -d --build
docker compose ps
echo
echo "Owner Command Center : http://localhost:${FRONTEND_PORT:-3000}"
echo "Meta webhook gateway : http://localhost:${GATEWAY_PORT:-8000}/api/v1/whatsapp/webhook"
