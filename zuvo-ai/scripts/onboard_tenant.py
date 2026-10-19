#!/usr/bin/env python3
"""Validate or apply a tenant bundle (tenants/<slug>/).

  python scripts/onboard_tenant.py tenants/jaipur-furniture --validate
  python scripts/onboard_tenant.py tenants/jaipur-furniture --apply --password owner@x.com=S3cret!
  python scripts/onboard_tenant.py --all --validate

--apply needs DATABASE_URL (asyncpg URL for the zuvo_app role, e.g. via `docker compose exec migrator` or a
port-forwarded postgres). Applying is idempotent; the migrator applies every bundle on each stack start anyway.
"""
import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "libs" / "zuvo_common"))

from zuvo_common.bundle import BundleRegistry, load_bundle  # noqa: E402
from zuvo_common.onboarding import validate_bundle  # noqa: E402
from zuvo_common.pricing import parse_pricing_csv  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("bundle", nargs="?", help="path to tenants/<slug>")
    ap.add_argument("--all", action="store_true", help="every bundle under tenants/")
    ap.add_argument("--validate", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--password", action="append", default=[], metavar="EMAIL=PASSWORD")
    a = ap.parse_args()
    if not (a.bundle or a.all) or not (a.validate or a.apply):
        ap.error("give a bundle path (or --all) and --validate and/or --apply")

    registry = BundleRegistry(str(ROOT / "tenants"))
    if registry.errors:
        for slug, err in registry.errors.items():
            print(f"[{slug}] LOAD ERROR: {err}")
    if a.all:
        bundles = registry.all()
    else:
        try:
            bundles = [load_bundle(Path(a.bundle))]
        except Exception as e:  # noqa: BLE001
            print(f"LOAD ERROR: {e}")
            return 1

    failed = bool(registry.errors) and a.all
    for b in bundles:
        others = [o for o in registry.all() if o.slug != b.slug]
        errs = validate_bundle(b, others)
        rows = parse_pricing_csv(b.pricing_text)[0] if b.pricing_text else []
        warnings = [] if b.pricing_text else ["no pricing.csv (price questions always go to approval)"]
        print(f"[{b.slug}] numbers={len(b.config.numbers)} workers={len(b.config.workers)} "
              f"price_rows={len(rows)} kb_docs={len(b.knowledge)} followups={len(b.config.followups)}")
        for w in warnings:
            print(f"  warn: {w}")
        for e in errs:
            print(f"  ERROR: {e}")
        failed |= bool(errs)

    if failed:
        return 1
    if a.apply:
        from zuvo_common.db import make_sessionmaker
        from zuvo_common.onboarding import apply_bundle

        url = os.environ.get("DATABASE_URL")
        if not url:
            print("DATABASE_URL is not set")
            return 1
        pw = dict(p.split("=", 1) for p in a.password)

        async def run():
            sm = make_sessionmaker(url, pool_size=2)
            for b in bundles:
                print(json.dumps(await apply_bundle(sm, b, actor="cli", passwords=pw), default=str))

        asyncio.run(run())
    return 0


if __name__ == "__main__":
    sys.exit(main())
