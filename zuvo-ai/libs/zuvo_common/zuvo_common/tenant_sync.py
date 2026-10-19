"""Syncs tenant-routing.yaml into tenants / workers / whatsapp_numbers rows (config is the source of truth)."""
from __future__ import annotations

from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .db import tenant_session
from .models import Tenant, WhatsAppNumber, Worker
from .tenancy import TenantEntry, number_uuid, worker_uuid


async def sync_tenant(session: AsyncSession, t: TenantEntry) -> None:
    tid = str(t.tenant_id)
    await session.execute(
        insert(Tenant)
        .values(id=tid, slug=t.slug, name=t.name, city=t.city, currency=t.currency)
        .on_conflict_do_update(index_elements=[Tenant.id], set_={"name": t.name, "city": t.city, "currency": t.currency})
    )
    seen_workers: set[str] = set()
    for n in t.numbers:
        wid = worker_uuid(tid, n.worker_id)
        if wid not in seen_workers:
            seen_workers.add(wid)
            await session.execute(
                insert(Worker)
                .values(id=wid, tenant_id=tid, worker_key=n.worker_id, display_name=n.worker_name or n.worker_id, role=n.role.value)
                .on_conflict_do_update(
                    index_elements=[Worker.id],
                    set_={"display_name": n.worker_name or n.worker_id, "role": n.role.value},
                )
            )
        await session.execute(
            insert(WhatsAppNumber)
            .values(
                id=number_uuid(tid, n.meta_phone_number_id),
                tenant_id=tid,
                worker_id=wid,
                meta_phone_number_id=n.meta_phone_number_id,
                display_phone_number=n.whatsapp_number,
                status=n.status.value,
            )
            .on_conflict_do_update(
                index_elements=[WhatsAppNumber.id],
                set_={"worker_id": wid, "display_phone_number": n.whatsapp_number, "status": n.status.value},
            )
        )


    keep = [number_uuid(tid, n.meta_phone_number_id) for n in t.numbers]
    # numbers moved to another tenant or removed from config stop resolving
    await session.execute(
        update(WhatsAppNumber).where(WhatsAppNumber.tenant_id == tid, WhatsAppNumber.id.not_in(keep)).values(status="REVOKED")
    )


async def sync_all(sm: async_sessionmaker[AsyncSession], tenants: list[TenantEntry]) -> int:
    for t in tenants:
        async with tenant_session(sm, str(t.tenant_id)) as session:
            await sync_tenant(session, t)
    return len(tenants)
