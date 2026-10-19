"""Container liveness probe for consumer services: `python -m zuvo_common.health [--max-age SECONDS]`.

Exit 0 when the heartbeat written by the consume/tick loop is fresh, 1 otherwise.
The default 180 s covers the longest legitimate handler (Gemini timeout x attempts).
"""
from __future__ import annotations

import argparse
import sys

from .runtime import heartbeat_age


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-age", type=float, default=180.0)
    args = ap.parse_args(argv)
    age = heartbeat_age()
    if age is None:
        print("unhealthy: no heartbeat yet")
        return 1
    if age > args.max_age:
        print(f"unhealthy: heartbeat {age:.0f}s old")
        return 1
    print(f"ok: heartbeat {age:.0f}s old")
    return 0


if __name__ == "__main__":
    sys.exit(main())
