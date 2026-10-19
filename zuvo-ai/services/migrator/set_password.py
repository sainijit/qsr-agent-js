"""Set or reset a dashboard user's password (operator tool, runs in the migrator image as the DB owner).

The password is never taken from argv (visible in `ps` and shell history):

    scripts/deploy/dcp run --rm --no-deps migrator python set_password.py owner@example.com      # prompts twice (TTY)
    printf '%s\\n' "$PW" | scripts/deploy/dcp run --rm --no-deps -T migrator python set_password.py owner@example.com

The user must already exist (created by its tenant bundle, `tenants/<slug>/tenant.yaml`). One audit row
`user.password_set` is written per tenant membership with a hashed email; neither the email nor the password is logged.
Exit codes: 0 updated, 1 user not found / inactive, 2 rejected password or usage.
"""
from __future__ import annotations

import asyncio
import getpass
import hashlib
import json
import os
import sys
import uuid
from typing import Optional

MIN_LENGTH = 12


def check_password(email: str, password: str) -> Optional[str]:
    """Return the reason a password is rejected, or None if acceptable."""
    if len(password) < MIN_LENGTH:
        return f"must be at least {MIN_LENGTH} characters"
    if password != password.strip():
        return "must not start or end with whitespace"
    low = password.lower()
    if low == email.strip().lower() or low == email.split("@")[0].strip().lower():
        return "must not be the email address"
    if len(set(password)) < 5:
        return "too few distinct characters"
    return None


def email_sha(email: str) -> str:
    return hashlib.sha256(email.strip().lower().encode()).hexdigest()[:12]


def read_password() -> str:
    if sys.stdin.isatty():
        first = getpass.getpass("New password: ")
        if first != getpass.getpass("Repeat password: "):
            raise ValueError("passwords do not match")
        return first
    return sys.stdin.readline().rstrip("\r\n")


async def apply(email: str, password: str) -> int:
    import asyncpg

    from zuvo_common.passwords import hash_password

    conn = await asyncpg.connect(os.environ["MIGRATION_DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://"))
    try:
        async with conn.transaction():
            user = await conn.fetchrow("SELECT id, is_active FROM users WHERE lower(email) = lower($1) FOR UPDATE", email.strip())
            if not user or not user["is_active"]:
                print(json.dumps({"updated": False, "email_sha": email_sha(email), "reason": "no active user with that email"}))
                return 1
            await conn.execute("UPDATE users SET password_hash = $1 WHERE id = $2", hash_password(password), user["id"])
            tenants = [r["tenant_id"] for r in await conn.fetch("SELECT tenant_id FROM memberships WHERE user_id = $1", user["id"])]
            for tid in tenants:
                await conn.execute("SELECT set_config('app.current_tenant', $1, true)", str(tid))
                await conn.execute(
                    "INSERT INTO audit_logs (id, tenant_id, event_type, actor, entity_type, entity_id, details, created_at) "
                    "VALUES ($1, $2, 'user.password_set', 'operator-cli', 'user', $3, $4::jsonb, now())",
                    uuid.uuid4(), tid, str(user["id"]), json.dumps({"email_sha": email_sha(email)}))
        print(json.dumps({"updated": True, "email_sha": email_sha(email), "memberships": len(tenants)}))
        return 0
    finally:
        await conn.close()


def main(argv: list[str]) -> int:
    if len(argv) != 2 or "@" not in argv[1] or "=" in argv[1]:
        print("usage: set_password.py <email>   (password from TTY prompt or one line on stdin)", file=sys.stderr)
        return 2
    email = argv[1]
    try:
        password = read_password()
    except (ValueError, EOFError, KeyboardInterrupt) as e:
        print(f"rejected: {e or 'no input'}", file=sys.stderr)
        return 2
    reason = check_password(email, password)
    if reason:
        print(f"rejected: password {reason}", file=sys.stderr)
        return 2
    return asyncio.run(apply(email, password))


if __name__ == "__main__":
    sys.exit(main(sys.argv))
