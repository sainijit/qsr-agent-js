"""One-shot migrator: ensure app role -> alembic upgrade -> sync legacy tenant-routing.yaml -> apply tenant bundles.

The whole run holds a Postgres advisory lock, so two migrators (e.g. a deploy and a manual `run --rm migrator`)
never apply migrations or bundles concurrently: the second one waits up to MIGRATOR_LOCK_WAIT_SECONDS, then exits 3.

    python run.py            # migrate
    python run.py --status   # print {"current": ..., "head": ..., "pending": bool} and exit (no changes)
"""
import asyncio
import json
import os
import re
import subprocess
import sys

import asyncpg

from zuvo_common.db import make_sessionmaker
from zuvo_common.logging import get_logger
from zuvo_common.onboarding import apply_bundle, validate_bundle
from zuvo_common.settings import get_settings
from zuvo_common.tenancy import TenantRouting
from zuvo_common.tenant_sync import sync_all

log = get_logger("migrator")

# arbitrary constant shared by every migrator instance ("zuvo" + "mig")
LOCK_KEY = 0x7A75766F6D6967


async def owner_connect() -> asyncpg.Connection:
    owner_url = os.environ["MIGRATION_DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    for attempt in range(30):
        try:
            return await asyncpg.connect(owner_url)
        except Exception:
            if attempt == 29:
                raise
            await asyncio.sleep(2)
    raise RuntimeError("unreachable")


async def acquire_lock(conn: asyncpg.Connection, wait_seconds: float) -> bool:
    deadline = asyncio.get_running_loop().time() + wait_seconds
    while True:
        if await conn.fetchval("SELECT pg_try_advisory_lock($1)", LOCK_KEY):
            return True
        if asyncio.get_running_loop().time() >= deadline:
            return False
        await asyncio.sleep(2)


async def ensure_app_role(conn: asyncpg.Connection) -> None:
    password = os.environ["ZUVO_APP_DB_PASSWORD"]
    if not re.fullmatch(r"[A-Za-z0-9_\-]{8,128}", password):
        raise SystemExit("ZUVO_APP_DB_PASSWORD must be 8-128 chars of [A-Za-z0-9_-]")
    exists = await conn.fetchval("SELECT 1 FROM pg_roles WHERE rolname = 'zuvo_app'")
    verb = "ALTER" if exists else "CREATE"
    # NOBYPASSRLS + non-owner => RLS policies always apply to services
    await conn.execute(f"{verb} ROLE zuvo_app LOGIN NOSUPERUSER NOBYPASSRLS PASSWORD '{password}'")


def script_head() -> str:
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    return ScriptDirectory.from_config(Config(os.path.join(os.path.dirname(os.path.abspath(__file__)), "alembic.ini"))).get_current_head()


async def status() -> dict:
    conn = await owner_connect()
    try:
        has_table = await conn.fetchval("SELECT to_regclass('public.alembic_version') IS NOT NULL")
        current = await conn.fetchval("SELECT version_num FROM alembic_version") if has_table else None
    finally:
        await conn.close()
    head = script_head()
    return {"current": current, "head": head, "pending": current != head}


async def main() -> None:
    conn = await owner_connect()
    try:
        wait = float(os.environ.get("MIGRATOR_LOCK_WAIT_SECONDS", "300"))
        if not await acquire_lock(conn, wait):
            log.error(f"another migrator holds the migration lock (waited {wait:.0f}s); not running")
            raise SystemExit(3)
        log.info("migration lock acquired")
        await ensure_app_role(conn)
        log.info("app role ensured")
        await migrate()
    finally:
        await conn.close()  # releases the session-level advisory lock


async def migrate() -> None:
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], check=True)
    log.info("migrations applied")
    settings = get_settings()
    routing = TenantRouting(settings.tenant_routing_path)
    sm = make_sessionmaker(settings.database_url, pool_size=2)
    bundles = routing.bundles.all()
    bundle_ids = {b.tenant_id for b in bundles}
    legacy = [t for t in routing.config.tenants if str(t.tenant_id) not in bundle_ids]
    count = await sync_all(sm, legacy)
    log.info(f"synced {count} legacy tenants from routing config")
    for slug, err in routing.bundles.errors.items():
        log.error(f"bundle {slug} not loaded: {err}")
    for b in bundles:
        errs = validate_bundle(b, [o for o in bundles if o.slug != b.slug])
        if errs:
            log.error(f"bundle {b.slug} invalid, skipped: {errs}")
            continue
        log.info(f"applied bundle {await apply_bundle(sm, b, actor='migrator')}")


if __name__ == "__main__":
    if sys.argv[1:] == ["--status"]:
        print(json.dumps(asyncio.run(status())))
    else:
        asyncio.run(main())
