"""Manual reconciliation of outbound sends whose outcome is UNKNOWN (Phase 7).

An UNKNOWN send may or may not have reached the customer (timeout after the request left, 5xx, crash mid-send).
ZuvoCloud never resends it automatically. A status webhook carrying its wamid / callback id resolves it on its own;
otherwise an operator checks WhatsApp Manager or asks the customer, then records the outcome here.

    docker compose exec -T dispatch python -m dispatch.reconcile list [--tenant UUID] [--status UNKNOWN]
    docker compose exec -T dispatch python -m dispatch.reconcile resolve --tenant UUID --id OUTBOUND_ID \\
        --as accepted|failed --actor ops@example.com [--wamid WAMID] [--note TEXT]

`list` prints metadata only (phone masked, no message text). `resolve` updates the row, writes an audit entry and
emits `outbound.message.sent` (state ACCEPTED/FAILED) through the outbox in the same transaction, so the message
history and follow-up task are updated. Marking `failed` does not resend: resend from the dashboard deliberately.
"""
from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import text

from zuvo_common import outbound_state as ost
from zuvo_common.audit import write_audit
from zuvo_common.bus import KafkaEventBus
from zuvo_common.db import make_sessionmaker, tenant_session
from zuvo_common.events import OutboundRequestPayload, Topics
from zuvo_common.outbox import Outbox
from zuvo_common.settings import get_settings
from zuvo_common.tenancy import TenantRouting


def _mask(phone: str | None) -> str:
    return f"***{phone[-4:]}" if phone and len(phone) > 4 else "***"


async def _list(sm, tenants: list[str], state: str) -> int:
    n = 0
    for tid in tenants:
        async with tenant_session(sm, tid) as s:
            rows = (await s.execute(text(
                f"SELECT {ost.COLUMNS} FROM outbound_messages WHERE status = :st ORDER BY updated_at LIMIT 200"),
                {"st": state})).all()
        for r in rows:
            n += 1
            print(f"{r.id}  tenant={tid}  kind={r.kind}  to={_mask(r.recipient_phone)}  attempts={r.attempts}  "
                  f"since={r.updated_at:%Y-%m-%dT%H:%M:%SZ}  error={(r.last_error or '')[:80]}"
                  + (f"  followup={r.followup_task_id}" if r.followup_task_id else ""))
    print(f"{n} {state} outbound message(s)")
    return 0


async def _resolve(sm, a) -> int:
    bus = KafkaEventBus(get_settings().kafka_bootstrap_servers, "dispatch-reconcile")
    try:
        await bus.start()
    except Exception as exc:  # noqa: BLE001 - the outbox relay publishes later
        print(f"kafka unavailable ({exc.__class__.__name__}); result event stays in the outbox for the relay")
    outbox = Outbox(sm, bus, flush_timeout_s=5.0)
    try:
        async with outbox.session(a.tenant) as s:
            before = await ost.resolve_unknown(s, a.id, a.outcome, a.actor, a.wamid, a.note)
            after = await ost.get(s, a.id)
            if before.request:
                req = OutboundRequestPayload.model_validate(before.request)
                await outbox.add(s, Topics.OUTBOUND_SENT, ost.sent_envelope(a.tenant, after, req))
            await write_audit(s, a.tenant, "OUTBOUND_RECONCILED", a.actor, "outbound_message", a.id,
                              {"outcome": a.outcome, "wamid": a.wamid, "note": (a.note or "")[:200]})
    except (LookupError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    finally:
        await bus.stop()
    print(f"{a.id} -> {after.status} (reconciled_by={a.actor})")
    return 0


async def _main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="dispatch.reconcile")
    sub = ap.add_subparsers(dest="cmd", required=True)
    ls = sub.add_parser("list")
    ls.add_argument("--tenant")
    ls.add_argument("--status", default="UNKNOWN", choices=["UNKNOWN", "PENDING", "SENDING", "FAILED"])
    rs = sub.add_parser("resolve")
    rs.add_argument("--tenant", required=True)
    rs.add_argument("--id", required=True)
    rs.add_argument("--as", dest="outcome", required=True, choices=["accepted", "failed"])
    rs.add_argument("--actor", required=True)
    rs.add_argument("--wamid")
    rs.add_argument("--note")
    a = ap.parse_args(argv)
    settings = get_settings()
    sm = make_sessionmaker(settings.database_url, pool_size=2)
    if a.cmd == "list":
        tenants = [a.tenant] if a.tenant else [str(t.tenant_id) for t in TenantRouting(settings.tenant_routing_path).config.tenants]
        return await _list(sm, tenants, a.status)
    return await _resolve(sm, a)


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
