"""Read models for the Owner Command Center. Every query runs inside a tenant_session (RLS)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from sqlalchemy import and_, desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from zuvo_common.models import Conversation, Customer, Message, Tenant, WhatsAppNumber, Worker
from zuvo_common.security import Principal

IST = ZoneInfo("Asia/Kolkata")


def _today_start() -> datetime:
    now = datetime.now(IST)
    return now.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)


async def tenant_info(s: AsyncSession, tenant_id: str) -> Optional[dict]:
    tenant = await s.get(Tenant, tenant_id)
    if not tenant:
        return None
    rows = (
        await s.execute(
            select(WhatsAppNumber, Worker).join(Worker, Worker.id == WhatsAppNumber.worker_id).order_by(Worker.display_name)
        )
    ).all()
    lines = [
        {
            "number_id": n.id,
            "meta_phone_number_id": n.meta_phone_number_id,
            "display_phone_number": n.display_phone_number,
            "assigned_worker": w.display_name,
            "worker_id": w.id,
            "status": n.status,
        }
        for n, w in rows
    ]
    return {
        "tenant_id": tenant.id,
        "business_name": tenant.name,
        "city": tenant.city or "",
        "connected_lines_count": sum(1 for l in lines if l["status"] == "ACTIVE"),
        "whatsapp_lines": lines,
    }


async def customers(s: AsyncSession, principal: Principal) -> list[dict]:
    stmt = select(Customer).order_by(Customer.display_name.nulls_last(), Customer.phone)
    if principal.is_worker:
        stmt = stmt.where(Customer.id.in_(select(Conversation.customer_id).where(Conversation.worker_id == principal.worker_id)))
    rows = (await s.execute(stmt)).scalars().all()
    return [
        {
            "id": c.id,
            "company_name": c.account_name or c.display_name or c.phone,
            "contact_person": c.display_name,
            "primary_phone": c.phone,
            "credit_limit": float(c.credit_limit),
            "current_outstanding": float(c.outstanding),
            "is_blocked": c.is_blocked,
            "created_at": c.created_at.isoformat() if c.created_at else None,
        }
        for c in rows
    ]


async def feed(
    s: AsyncSession,
    principal: Principal,
    worker_id: Optional[str],
    customer_id: Optional[str],
    customer_phone: Optional[str],
    limit: int,
    offset: int,
) -> dict:
    tenant = await s.get(Tenant, principal.tenant_id)
    today = _today_start()

    workers_stmt = (
        select(Worker, WhatsAppNumber)
        .join(WhatsAppNumber, WhatsAppNumber.worker_id == Worker.id, isouter=True)
        .where(Worker.is_active.is_(True))
        .order_by(Worker.display_name)
    )
    if principal.is_worker:
        workers_stmt = workers_stmt.where(Worker.id == principal.worker_id)
    worker_rows = (await s.execute(workers_stmt)).all()

    today_counts = dict(
        (await s.execute(select(Message.worker_id, func.count()).where(Message.created_at >= today).group_by(Message.worker_id))).all()
    )
    active_convs = dict(
        (
            await s.execute(
                select(Conversation.worker_id, func.count())
                .where(Conversation.last_message_at >= datetime.now(timezone.utc) - timedelta(hours=24))
                .group_by(Conversation.worker_id)
            )
        ).all()
    )
    workers, seen = [], set()
    for w, n in worker_rows:
        if w.id in seen:
            continue
        seen.add(w.id)
        workers.append(
            {
                "worker_id": w.id,
                "worker_name": w.display_name,
                "designation": w.role,
                "phone_number": n.display_phone_number if n else "",
                "meta_phone_number_id": n.meta_phone_number_id if n else "N/A",
                "total_messages_today": today_counts.get(w.id, 0),
                "active_conversations_count": active_convs.get(w.id, 0),
            }
        )

    msg_stmt = (
        select(Message, Worker, WhatsAppNumber, Customer)
        .join(Worker, Worker.id == Message.worker_id)
        .join(WhatsAppNumber, WhatsAppNumber.id == Message.whatsapp_number_id)
        .join(Customer, Customer.id == Message.customer_id)
        .order_by(desc(Message.created_at))
        .limit(limit)
        .offset(offset)
    )
    scope = []
    if principal.is_worker:
        scope.append(Message.worker_id == principal.worker_id)
    if worker_id:  # G6 worker isolation
        scope.append(Message.worker_id == worker_id)
    if customer_id:  # G6 customer 360
        scope.append(Message.customer_id == customer_id)
    elif customer_phone:
        scope.append(Customer.phone == customer_phone)
    if scope:
        msg_stmt = msg_stmt.where(and_(*scope))

    items = []
    for m, w, n, c in (await s.execute(msg_stmt)).all():
        inbound = m.direction == "INBOUND"
        items.append(
            {
                "id": m.id,
                "wamid": m.wamid,
                "direction": m.direction,
                "sender_phone": c.phone if inbound else n.display_phone_number,
                "recipient_phone": n.display_phone_number if inbound else c.phone,
                "message_type": m.message_type,
                "content": m.body,
                "parsed_intent": m.parsed_intent,
                "confidence_score": m.confidence,
                "ai_status": m.ai_status,
                "status": m.status,
                "extraction": m.extraction,
                "worker_id": w.id,
                "worker_name": w.display_name,
                "worker_phone": n.display_phone_number,
                "customer_id": c.id,
                "customer_name": c.account_name or c.display_name or c.phone,
                "customer_phone": c.phone,
                "created_at": m.created_at.isoformat(),
            }
        )

    metric_scope = [Message.worker_id == principal.worker_id] if principal.is_worker else []

    async def _count(*conds) -> int:
        return (await s.scalar(select(func.count(Message.id)).where(*metric_scope, *conds))) or 0

    metrics = {
        "tenant_id": principal.tenant_id,
        "business_name": tenant.name if tenant else "",
        "total_messages_today": await _count(Message.created_at >= today),
        "total_inbound": await _count(Message.direction == "INBOUND", Message.created_at >= today),
        "total_outbound": await _count(Message.direction == "OUTBOUND", Message.created_at >= today),
        "active_workers_count": len(workers),
        "active_customers_count": (await s.scalar(select(func.count(Customer.id)))) or 0,
        "pending_approvals_count": await _count(or_(Message.ai_status == "NEEDS_REVIEW", Message.ai_status == "AI_PENDING")),
    }
    return {
        "metrics": metrics,
        "workers": workers,
        "messages": items,
        "filter_applied": {
            "worker_id": worker_id,
            "customer_id": customer_id,
            "customer_phone": customer_phone,
            "limit": limit,
            "offset": offset,
        },
    }
