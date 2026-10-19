#!/usr/bin/env bash
# Run the tenant-isolation suite against the local compose Postgres (synthetic data, self-cleaning).
# Never point this at production. Usage: scripts/test_rls.sh [pytest args]
set -euo pipefail
cd "$(dirname "$0")/.."
PROJECT=${COMPOSE_PROJECT_NAME:-zuvo-ai}
cid=$(docker compose -p "$PROJECT" ps -q postgres)
[[ -n "$cid" ]] || { echo "postgres container not running in project $PROJECT" >&2; exit 2; }
ZUVO_RLS_PG_HOST=$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}' "$cid" | awk '{print $1}')
export ZUVO_RLS_PG_HOST
export ZUVO_RLS_ENV_FILE=${ENV_FILE:-.env}
exec "${PYTHON:-python3}" - "$@" <<'PY'
import importlib.util, os, sys
from urllib.parse import quote
spec = importlib.util.spec_from_file_location("preflight", "scripts/preflight.py")
pf = importlib.util.module_from_spec(spec); spec.loader.exec_module(pf)
env = pf.parse_env_file(__import__("pathlib").Path(os.environ["ZUVO_RLS_ENV_FILE"]))
if env.get("APP_ENV", "").lower() == "production":
    sys.exit("refusing: env file has APP_ENV=production")
host = os.environ["ZUVO_RLS_PG_HOST"]
url = lambda user, key: f"postgresql+asyncpg://{user}:{quote(env[key], safe='')}@{host}:5432/zuvo_db"
os.environ.update(ZUVO_RLS_APP_URL=url("zuvo_app", "ZUVO_APP_DB_PASSWORD"),
                  ZUVO_RLS_OWNER_URL=url("zuvo_owner", "POSTGRES_PASSWORD"),
                  NO_PROXY=f"{os.environ.get('NO_PROXY', '')},{host}", no_proxy=f"{os.environ.get('no_proxy', '')},{host}")
os.execvp(sys.executable, [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                           *(sys.argv[1:] or ["tests/integration"])])
PY
