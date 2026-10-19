"""Lead domain operations (stage machine, visits, follow-up scheduling). Shared by lead-service,
followup-scheduler and dashboard-api so every writer applies identical rules. All functions run
inside a `tenant_session` (RLS-scoped)."""
from __future__ import annotations

import re

import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Iterable, Optional

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from .bundle import TenantConfig
from .dates import VisitWhen
from .models import FollowupTask, Lead, LeadEvent, VisitPlan
from .schedule import due_for_rule, visit_window_utc

CLOSED_STAGES = {"WON", "LOST"}
OPEN_TASK_STATUSES = ("SCHEDULED", "QUEUED")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def stage_rank(cfg: TenantConfig, stage: str) -> int:
    stages = cfg.lead.stages
    if stage == "DORMANT":
        return -1
    if stage in CLOSED_STAGES:
        return 1000
    return stages.index(stage) if stage in stages else 0


def compute_score(lead: Lead, cfg: TenantConfig) -> int:
    s = 0
    for f in cfg.lead.qualify_fields:
        v = {"budget": lead.budget_max or lead.budget_min, "timeline": lead.timeline_text}.get(f, getattr(lead, f, None))
        s += 15 if v else 0
    s += {"VISIT_PLANNED": 20, "VISITED": 35, "WON": 50}.get(lead.stage, 0)
    if lead.campaign_id:
        s += 5
    return min(s, 100)


async def add_event(session: AsyncSession, tenant_id: str, lead_id: str, type_: str, actor: str,
                    from_value: Optional[str] = None, to_value: Optional[str] = None,
                    message_id: Optional[str] = None, details: Optional[dict] = None) -> None:
    session.add(LeadEvent(id=str(uuid.uuid4()), tenant_id=tenant_id, lead_id=lead_id, type=type_, actor=actor,
                          from_value=from_value, to_value=to_value, message_id=message_id, details=details or {}))


async def open_lead(session: AsyncSession, customer_id: str, lock: bool = True) -> Optional[Lead]:
    q = select(Lead).where(Lead.customer_id == customer_id, Lead.closed_at.is_(None))
    if lock:
        q = q.with_for_update()
    return (await session.execute(q)).scalars().first()


async def ensure_open_lead(session: AsyncSession, tenant_id: str, customer_id: str, *, whatsapp_number_id: Optional[str],
                           worker_id: Optional[str], at: datetime, source: Optional[str] = None,
                           referral: Optional[dict] = None, campaign_id: Optional[str] = None,
                           message_id: Optional[str] = None) -> tuple[Lead, bool]:
    lead = await open_lead(session, customer_id)
    if lead:
        return lead, False
    lid = str(uuid.uuid4())
    res = await session.execute(
        text(
            "INSERT INTO leads (id, tenant_id, customer_id, whatsapp_number_id, assigned_worker_id, campaign_id, source, referral, "
            "stage, score, first_contact_at, created_at, updated_at) VALUES (:id, :t, :c, :n, :w, :camp, :src, CAST(:ref AS jsonb), "
            "'NEW', 0, :at, now(), now()) ON CONFLICT (tenant_id, customer_id) WHERE closed_at IS NULL DO NOTHING RETURNING id"
        ),
        {"id": lid, "t": tenant_id, "c": customer_id, "n": whatsapp_number_id, "w": worker_id, "camp": campaign_id,
         "src": source or "organic", "ref": _json(referral), "at": at},
    )
    created = res.scalar() is not None
    lead = await open_lead(session, customer_id)
    assert lead is not None
    if created:
        await add_event(session, tenant_id, lead.id, "created", "system", to_value="NEW", message_id=message_id,
                        details={"source": source or "organic", "campaign_id": campaign_id})
    return lead, created


def _json(v) -> Optional[str]:
    import json

    return json.dumps(v) if v is not None else None


async def set_stage(session: AsyncSession, cfg: TenantConfig, lead: Lead, stage: str, actor: str, *,
                    force: bool = False, message_id: Optional[str] = None, reason: Optional[str] = None) -> bool:
    if stage == lead.stage:
        return False
    if stage not in cfg.lead.stages and stage not in CLOSED_STAGES and stage != "DORMANT":
        raise ValueError(f"unknown stage {stage}")
    if not force and lead.stage in CLOSED_STAGES:
        return False
    if not force and stage != "DORMANT" and lead.stage != "DORMANT" and stage_rank(cfg, stage) <= stage_rank(cfg, lead.stage):
        return False
    old = lead.stage
    lead.stage = stage
    lead.updated_at = _now()
    if stage in CLOSED_STAGES:
        lead.closed_at = _now()
        if stage == "LOST":
            lead.lost_reason = reason
        await cancel_tasks(session, lead.id, None, f"lead_{stage.lower()}")
    elif old in CLOSED_STAGES:
        lead.closed_at = None
    lead.score = compute_score(lead, cfg)
    await add_event(session, lead.tenant_id, lead.id, "stage_change", actor, from_value=old, to_value=stage,
                    message_id=message_id, details={"reason": reason} if reason else None)
    return True


async def cancel_tasks(session: AsyncSession, lead_id: str, kinds: Optional[Iterable[str]], reason: str,
                       visit_plan_id: Optional[str] = None) -> int:
    q = update(FollowupTask).where(FollowupTask.lead_id == lead_id, FollowupTask.status.in_(OPEN_TASK_STATUSES))
    if kinds is not None:
        q = q.where(FollowupTask.kind.in_(list(kinds)))
    if visit_plan_id is not None:
        q = q.where(FollowupTask.visit_plan_id == visit_plan_id)
    res = await session.execute(q.values(status="CANCELLED", skip_reason=reason, completed_at=_now()))
    return res.rowcount or 0


async def refresh_next_followup(session: AsyncSession, lead: Lead) -> None:
    lead.next_followup_at = await session.scalar(
        select(FollowupTask.due_at).where(FollowupTask.lead_id == lead.id, FollowupTask.status.in_(OPEN_TASK_STATUSES))
        .order_by(FollowupTask.due_at).limit(1)
    )


async def schedule_rules(session: AsyncSession, cfg: TenantConfig, lead: Lead, when: str, anchor: datetime, *,
                         attempt: int = 1, visit_plan_id: Optional[str] = None, now: Optional[datetime] = None) -> list[FollowupTask]:
    now = now or _now()
    created = []
    for rule in cfg.followups:
        if not rule.enabled or rule.when != when or attempt > rule.max:
            continue
        due = due_for_rule(rule, anchor, cfg.business_hours, cfg.tenant.timezone)
        if rule.before and due <= now:
            continue  # the reminder moment has already passed
        task = FollowupTask(
            id=str(uuid.uuid4()), tenant_id=lead.tenant_id, lead_id=lead.id, visit_plan_id=visit_plan_id, rule_id=rule.id,
            kind=when, due_at=due, mode=rule.mode, template_name=rule.template, status="SCHEDULED", attempt=attempt,
        )
        session.add(task)
        created.append(task)
    if created:
        await session.flush()
        await refresh_next_followup(session, lead)
    return created


# ---------------------------------------------------------------- inbound / outbound hooks


async def on_customer_message(session: AsyncSession, cfg: TenantConfig, lead: Lead, at: datetime, message_id: Optional[str]) -> None:
    prior = lead.last_customer_msg_at
    lead.last_customer_msg_at = at
    lead.updated_at = _now()
    n = await cancel_tasks(session, lead.id, ["no_reply", "dormant"], "customer_replied")
    if lead.stage == "DORMANT":
        await set_stage(session, cfg, lead, "ENGAGED", "system", message_id=message_id, reason="customer returned")
    elif lead.stage == "NEW" and prior is not None:
        await set_stage(session, cfg, lead, "ENGAGED", "system", message_id=message_id)
    if n:
        await refresh_next_followup(session, lead)


async def on_business_message(session: AsyncSession, cfg: TenantConfig, lead: Lead, at: datetime, sent_by: str,
                              followup_attempt: Optional[int] = None) -> None:
    if lead.first_response_at is None and lead.last_customer_msg_at is not None:
        lead.first_response_at = at
    lead.last_business_msg_at = at
    lead.updated_at = _now()
    if lead.closed_at is not None:
        return
    # (re)arm the no-reply nudge chain from this outbound message
    await cancel_tasks(session, lead.id, ["no_reply"], "superseded")
    attempt = (followup_attempt + 1) if (sent_by == "followup" and followup_attempt) else 1
    await schedule_rules(session, cfg, lead, "no_reply", at, attempt=attempt)
    await refresh_next_followup(session, lead)


async def apply_lead_fields(session: AsyncSession, cfg: TenantConfig, lead: Lead, fields: dict, actor: str,
                            message_id: Optional[str]) -> list[str]:
    changed = []
    mapping = {"interest": "interest", "budget_min": "budget_min", "budget_max": "budget_max",
               "timeline": "timeline_text", "location": "location"}
    for src, col in mapping.items():
        v = fields.get(src)
        if v in (None, "", 0):
            continue
        if col.startswith("budget"):
            v = Decimal(str(round(float(v), 2)))
        else:
            v = str(v)[:255]
        if getattr(lead, col) != v:
            old = getattr(lead, col)
            setattr(lead, col, v)
            changed.append(col)
            await add_event(session, lead.tenant_id, lead.id, "field_extracted", actor, from_value=str(old) if old is not None else None,
                            to_value=str(v), message_id=message_id, details={"field": col})
    if changed:
        known = sum(1 for f in cfg.lead.qualify_fields
                    if {"budget": lead.budget_max or lead.budget_min, "timeline": lead.timeline_text}.get(f, getattr(lead, f, None)))
        if known >= 2:
            await set_stage(session, cfg, lead, "QUALIFIED", actor, message_id=message_id)
        lead.score = compute_score(lead, cfg)
        lead.updated_at = _now()
    return changed


# ---------------------------------------------------------------- visits


async def plan_visit(session: AsyncSession, cfg: TenantConfig, lead: Lead, when: VisitWhen, *, raw_text: str,
                     confidence: float, actor: str, source_message_id: Optional[str] = None,
                     now: Optional[datetime] = None) -> VisitPlan:
    now = now or _now()
    previous = (await session.execute(
        select(VisitPlan).where(VisitPlan.lead_id == lead.id, VisitPlan.status.in_(("PLANNED", "CONFIRMED")))
    )).scalars().all()
    for p in previous:
        if p.resolved_date == when.date and (p.resolved_time_window or None) == (when.time_window or None):
            return p  # same plan restated: idempotent
    rescheduled_from = None
    for p in previous:
        p.status = "RESCHEDULED"
        p.outcome_by, p.outcome_at = actor, now
        rescheduled_from = p.id
        await cancel_tasks(session, lead.id, ["visit_planned", "visit_passed"], "rescheduled", visit_plan_id=p.id)
    plan = VisitPlan(
        id=str(uuid.uuid4()), tenant_id=lead.tenant_id, lead_id=lead.id, source_message_id=source_message_id,
        raw_text=raw_text[:2000], resolved_date=when.date, resolved_time_window=when.time_window, precision=when.precision,
        confidence=confidence, status="PLANNED", rescheduled_from_id=rescheduled_from,
    )
    session.add(plan)
    await session.flush()
    start, end = visit_window_utc(when.date, when.time_window, when.precision, cfg.business_hours, cfg.tenant.timezone)
    if when.precision in ("EXACT", "WEEKDAY"):
        await schedule_rules(session, cfg, lead, "visit_planned", start, visit_plan_id=plan.id, now=now)
    await schedule_rules(session, cfg, lead, "visit_passed", end, visit_plan_id=plan.id, now=now)
    await set_stage(session, cfg, lead, "VISIT_PLANNED", actor, force=lead.stage in ("VISITED", "DORMANT"), message_id=source_message_id)
    await add_event(session, lead.tenant_id, lead.id, "visit_rescheduled" if rescheduled_from else "visit_planned", actor,
                    to_value=when.date.isoformat(), message_id=source_message_id,
                    details={"precision": when.precision, "time_window": when.time_window, "visit_plan_id": plan.id})
    lead.score = compute_score(lead, cfg)
    return plan


async def cancel_visit(session: AsyncSession, lead: Lead, actor: str, message_id: Optional[str] = None) -> int:
    plans = (await session.execute(
        select(VisitPlan).where(VisitPlan.lead_id == lead.id, VisitPlan.status.in_(("PLANNED", "CONFIRMED")))
    )).scalars().all()
    for p in plans:
        p.status, p.outcome_by, p.outcome_at = "CANCELLED", actor, _now()
        await cancel_tasks(session, lead.id, ["visit_planned", "visit_passed"], "visit_cancelled", visit_plan_id=p.id)
        await add_event(session, lead.tenant_id, lead.id, "visit_cancelled", actor, from_value=p.resolved_date.isoformat(), message_id=message_id)
    return len(plans)


async def set_visit_outcome(session: AsyncSession, cfg: TenantConfig, plan: VisitPlan, status: str, actor: str) -> Lead:
    if status not in ("CONFIRMED", "VISITED", "NO_SHOW", "CANCELLED"):
        raise ValueError("status must be CONFIRMED, VISITED, NO_SHOW or CANCELLED")
    lead = (await session.execute(select(Lead).where(Lead.id == plan.lead_id).with_for_update())).scalar_one()
    old = plan.status
    plan.status, plan.outcome_by, plan.outcome_at = status, actor, _now()
    if status == "VISITED":
        await cancel_tasks(session, lead.id, ["visit_planned", "visit_passed"], "visited", visit_plan_id=plan.id)
        await set_stage(session, cfg, lead, "VISITED", actor)
        await schedule_rules(session, cfg, lead, "visited", _now())
    elif status == "NO_SHOW":
        await cancel_tasks(session, lead.id, ["visit_planned"], "no_show", visit_plan_id=plan.id)
    elif status == "CANCELLED":
        await cancel_tasks(session, lead.id, ["visit_planned", "visit_passed"], "visit_cancelled", visit_plan_id=plan.id)
    await add_event(session, lead.tenant_id, lead.id, f"visit_{status.lower()}", actor, from_value=old, to_value=status,
                    details={"visit_plan_id": plan.id})
    lead.score = compute_score(lead, cfg)
    await refresh_next_followup(session, lead)
    return lead


# ---------------------------------------------------------------- sweeps


async def dormant_sweep(session: AsyncSession, cfg: TenantConfig, now: Optional[datetime] = None) -> int:
    now = now or _now()
    cutoff = now - timedelta(days=cfg.lead.dormant_after_days)
    leads = (await session.execute(
        select(Lead).where(
            Lead.closed_at.is_(None), Lead.stage.in_(("NEW", "ENGAGED", "QUALIFIED")),
            Lead.last_customer_msg_at.is_not(None), Lead.last_customer_msg_at < cutoff,
        ).with_for_update(skip_locked=True).limit(200)
    )).scalars().all()
    for lead in leads:
        await set_stage(session, cfg, lead, "DORMANT", "system", reason=f"no reply for {cfg.lead.dormant_after_days}d")
        await cancel_tasks(session, lead.id, ["no_reply"], "dormant")
        await schedule_rules(session, cfg, lead, "dormant", now)
    return len(leads)


async def opt_out(session: AsyncSession, customer_id: str, lead: Optional[Lead], message_id: Optional[str]) -> None:
    await session.execute(text("UPDATE customers SET opted_out = true, opted_out_at = now() WHERE id = :c"), {"c": customer_id})
    if lead:
        await cancel_tasks(session, lead.id, None, "opted_out")
        await add_event(session, lead.tenant_id, lead.id, "opted_out", "customer", message_id=message_id)
        lead.next_followup_at = None


def today_local(cfg: TenantConfig, now: Optional[datetime] = None) -> date:
    from zoneinfo import ZoneInfo

    return (now or _now()).astimezone(ZoneInfo(cfg.tenant.timezone)).date()


def is_opt_out(text: str, keywords: list[str]) -> bool:
    """Whole-message match only, so "stop by tomorrow" is not an opt-out."""
    norm = re.sub(r"[^\w\s]", " ", (text or "").lower())
    norm = " ".join(norm.split())
    return bool(norm) and norm in {" ".join(k.lower().split()) for k in keywords}
