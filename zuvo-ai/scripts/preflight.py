#!/usr/bin/env python3
"""Deploy-time configuration check. Prints variable NAMES only, never values.

    python3 scripts/preflight.py                    # production profile, reads ./.env + process env
    python3 scripts/preflight.py --env-file /opt/zuvo/.env
    python3 scripts/preflight.py --profile development
    python3 scripts/preflight.py --compose          # also validates the merged prod compose file

Exit code: 0 = no errors (warnings allowed), 1 = errors, 2 = usage problem.
Needs only the Python standard library (runs on a bare VM before any image is built).
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_preflight():
    # import the module file directly so the CLI does not need zuvo_common's dependencies installed
    path = ROOT / "libs/zuvo_common/zuvo_common/preflight.py"
    spec = importlib.util.spec_from_file_location("zuvo_preflight", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["zuvo_preflight"] = mod
    spec.loader.exec_module(mod)
    return mod


def parse_env_file(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:]
        k, v = line.split("=", 1)
        v = v.strip()
        if len(v) >= 2 and v[0] in "'\"" and v[0] in v[1:]:
            v = v[1:v.index(v[0], 1)]
        else:
            # like docker compose: an unquoted value ends at whitespace + '#'
            v = re.split(r"\s+#", v, maxsplit=1)[0].strip()
        env[k.strip()] = v
    return env


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--env-file", default=str(ROOT / ".env"))
    ap.add_argument("--profile", choices=["production", "development"], default="production")
    ap.add_argument("--compose", action="store_true", help="also run `docker compose ... config -q` on the prod files")
    args = ap.parse_args()

    pf = _load_preflight()
    env_path = Path(args.env_file)
    env: dict[str, str] = {}
    if env_path.exists():
        mode = env_path.stat().st_mode & 0o777
        env.update(parse_env_file(env_path))
        if args.profile == "production" and mode & 0o077:
            print(f"WARN  {env_path.name}: file mode {oct(mode)} is readable by group/others; run chmod 600")
    elif args.profile == "production":
        print(f"note: {env_path} not found; checking process environment only")
    env.update({k: v for k, v in os.environ.items() if k in env or k in pf.REQUIRED_PRODUCTION or k == "IMAGE_TAG"})

    issues = pf.check_env(env, args.profile)
    for i in issues:
        print(i)

    errors = [i for i in issues if i.level == pf.ERROR]
    if args.compose:
        cmd = ["docker", "compose", "--env-file", str(env_path), "-f", str(ROOT / "docker-compose.yml"),
               "-f", str(ROOT / "docker-compose.prod.yml"), "config", "-q"]
        r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
        if r.returncode != 0:
            # compose errors name variables, not values
            print("ERROR compose: merged production compose config is invalid:\n" + r.stderr.strip())
            errors.append(None)
        else:
            print("ok    compose: docker-compose.yml + docker-compose.prod.yml are valid")

    print(f"\npreflight ({args.profile}): {len(errors)} error(s), {len(issues) - len([i for i in issues if i.level == pf.ERROR])} warning(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
