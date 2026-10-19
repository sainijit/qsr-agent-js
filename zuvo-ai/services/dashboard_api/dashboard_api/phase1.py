"""Phase 1 API: password login, leads, visits, follow-ups, approvals (human-in-the-loop), insights and settings.

Every tenant read/write runs inside tenant_session (RLS). Users/memberships are platform tables and are read
without a tenant context only to authenticate."""
from __future__ import annotations

import hashlib
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import and_, desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from zuvo_common import leads as L
from zuvo_common.audit import write_audit
from zuvo_common.bundle import TenantBundle
from zuvo_common.bus import EventBus
from zuvo_common.db import tenant_session
from zuvo_common.events import ApprovalDecidedPayload, Envelope, TemplateSpec, Topics
from zuvo_common.learning import edit_distance
from zuvo_common.logging import get_logger
from zuvo_common.metrics import compute_day
from zuvo_common.models import (AgentRun, Approval, AutonomyStat, Campaign, Conversation, Customer, FollowupTask, LearnedExample,
                                Lead, LeadEvent, Membership, Message, MetricsDaily, PriceItem, PriceList, Tenant, User, VisitPlan,
                                Worker)
from zuvo_common.onboarding import import_pricing
from zuvo_common.outbound import outbound_envelope
from zuvo_common.outbox import Outbox
from zuvo_common.passwords import verify_password
from zuvo_common.pricing import blocking_errors, diff_price_rows, parse_pricing_csv
from zuvo_common.security import Principal, issue_token
from zuvo_common.settings import Settings
from zuvo_common.tenancy import Role, TenantRouting
from zuvo_common.web import current_principal

log = get_logger("dashboard-api")
_NS = uuid.UUID("5b7d9f10-2c4e-4a6b-8d0f-1e3a5c7e9b2d")
SERVICE_WINDOW = timedelta(hours=24)


# ------------------------------------------------------------------ schemas
class Login(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=1, max_length=255)
    tenant_id: Optional[str] = None


class SwitchTenant(BaseModel):
    tenant_id: str


class LeadPatch(BaseModel):
    stage: Optional[str] = None
    assigned_worker_id: Optional[str] = None
    lost_reason: Optional[str] = Field(None, max_length=255)
    interest: Optional[str] = Field(None, max_length=255)


class VisitOutcome(BaseModel):
    status: Literal["CONFIRMED", "VISITED", "NO_SHOW", "CANCELLED"]


class FollowupSendNow(BaseModel):
    text: Optional[str] = Field(None, min_length=1, max_length=4096)
    # the previous send's outcome is unknown (it may have reached the customer); resending needs explicit confirmation
    confirm_unknown: bool = False


class Decide(BaseModel):
    decision: Literal["approve", "edit", "reject"]
    text: Optional[str] = Field(None, max_length=4096)
    reason: Optional[str] = Field(None, max_length=500)


class PricingUpload(BaseModel):
    csv_text: str = Field(min_length=1, max_length=2_000_000)
    source_file: str = Field("dashboard-upload.csv", max_length=255)
    dry_run: bool = True


# ------------------------------------------------------------------ helpers
def _iso(v) -> Optional[str]:
    return v.isoformat() if v is not None else None


def _num(v) -> Optional[float]:
    return float(v) if v is not None else None


def _uuid(v: str, what: str = "id") -> str:
    try:
        return str(uuid.UUID(v))
    except (ValueError, TypeError):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"invalid {what}")


def _require(principal: Principal, *roles: Role) -> None:
    if principal.role not in roles:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "insufficient role")


def _lead_dict(lead: Lead, cust: Optional[Customer], worker: Optional[Worker] = None) -> dict:
    return {
        "id": lead.id, "customer_id": lead.customer_id, "stage": lead.stage, "score": lead.score, "source": lead.source,
        "interest": lead.interest, "budget_min": _num(lead.budget_min), "budget_max": _num(lead.budget_max),
        "timeline_text": lead.timeline_text, "location": lead.location, "lost_reason": lead.lost_reason,
        "campaign_id": lead.campaign_id, "whatsapp_number_id": lead.whatsapp_number_id,
        "assigned_worker_id": lead.assigned_worker_id, "assigned_worker_name": worker.display_name if worker else None,
        "customer_name": (cust.display_name or cust.account_name) if cust else None, "customer_phone": cust.phone if cust else None,
        "opted_out": bool(cust and cust.opted_out),
        "first_contact_at": _iso(lead.first_contact_at), "first_response_at": _iso(lead.first_response_at),
        "last_customer_msg_at": _iso(lead.last_customer_msg_at), "last_business_msg_at": _iso(lead.last_business_msg_at),
        "next_followup_at": _iso(lead.next_followup_at), "closed_at": _iso(lead.closed_at),
        "created_at": _iso(lead.created_at), "updated_at": _iso(lead.updated_at),
    }


def _visit_dict(v: VisitPlan, lead: Optional[Lead] = None, cust: Optional[Customer] = None) -> dict:
    return {
        "id": v.id, "lead_id": v.lead_id, "date": _iso(v.resolved_date), "time_window": v.resolved_time_window,
        "precision": v.precision, "confidence": v.confidence, "status": v.status, "raw_text": v.raw_text,
        "outcome_by": v.outcome_by, "outcome_at": _iso(v.outcome_at), "created_at": _iso(v.created_at),
        "lead_stage": lead.stage if lead else None, "interest": lead.interest if lead else None,
        "customer_name": (cust.display_name or cust.account_name) if cust else None, "customer_phone": cust.phone if cust else None,
    }


def _task_dict(t: FollowupTask, lead: Optional[Lead] = None, cust: Optional[Customer] = None) -> dict:
    return {
        "id": t.id, "lead_id": t.lead_id, "visit_plan_id": t.visit_plan_id, "rule_id": t.rule_id, "kind": t.kind,
        "due_at": _iso(t.due_at), "mode": t.mode, "status": t.status, "attempt": t.attempt, "skip_reason": t.skip_reason,
        "template_name": t.template_name, "completed_at": _iso(t.completed_at), "created_at": _iso(t.created_at),
        "lead_stage": lead.stage if lead else None, "interest": lead.interest if lead else None,
        "customer_name": (cust.display_name or cust.account_name) if cust else None, "customer_phone": cust.phone if cust else None,
    }


def _approval_dict(a: Approval, run: Optional[AgentRun] = None, cust: Optional[Customer] = None) -> dict:
    return {
        "id": a.id, "kind": a.kind, "topic": a.topic, "status": a.status, "lead_id": a.lead_id, "customer_id": a.customer_id,
        "followup_task_id": a.followup_task_id, "question_text": a.question_text, "proposed_text": a.proposed_text,
        "final_text": a.final_text, "template_name": a.template_name, "edit_distance": a.edit_distance,
        "reject_reason": a.reject_reason, "reviewer": a.reviewer, "sla_due_at": _iso(a.sla_due_at),
        "holding_sent_at": _iso(a.holding_sent_at), "decided_at": _iso(a.decided_at), "created_at": _iso(a.created_at),
        "confidence": run.confidence if run else None, "reasons": list(run.decision_reasons or []) if run else [],
        "retrieved": run.retrieved if run else None, "task": run.task if run else a.kind,
        "customer_name": (cust.display_name or cust.account_name) if cust else None, "customer_phone": cust.phone if cust else None,
    }


async def _lead_or_404(s: AsyncSession, principal: Principal, lead_id: str, lock: bool = False) -> Lead:
    stmt = select(Lead).where(Lead.id == _uuid(lead_id, "lead id"))
    if lock:
        stmt = stmt.with_for_update()
    lead = (await s.execute(stmt)).scalar_one_or_none()
    if not lead:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "lead not found")
    if principal.is_worker and lead.assigned_worker_id != principal.worker_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "lead is assigned to another worker")
    return lead


def _worker_scope(principal: Principal):
    return [Lead.assigned_worker_id == principal.worker_id] if principal.is_worker else []


# ------------------------------------------------------------------ router
def build_router(settings: Settings, sm: async_sessionmaker[AsyncSession], bus: EventBus, routing: TenantRouting) -> APIRouter:
    r = APIRouter(prefix="/api/v1")
    outbox = Outbox(sm, bus, flush_timeout_s=2.0)

    def bundle_for(principal: Principal) -> TenantBundle:
        b = routing.bundles.get(principal.tenant_id)
        if b is None:
            raise HTTPException(status.HTTP_409_CONFLICT, "tenant has no bundle (legacy tenant); Phase 1 features are disabled")
        return b

    async def memberships(email: str) -> list[tuple[User, Membership, Tenant]]:
        async with sm() as s:
            rows = (await s.execute(
                select(User, Membership).join(Membership, Membership.user_id == User.id)
                .where(func.lower(User.email) == email.lower().strip(), User.is_active.is_(True))
            )).all()
            out = []
            for u, m in rows:
                async with tenant_session(sm, str(m.tenant_id)) as ts:
                    t = await ts.get(Tenant, str(m.tenant_id))
                if t is not None:
                    out.append((u, m, t))
            return out

    def token_for(u: User, m: Membership) -> dict:
        principal = Principal(subject=f"user:{u.email}", tenant_id=str(m.tenant_id), role=Role(m.role),
                              worker_id=str(m.worker_id) if m.worker_id else None)
        return {"access_token": issue_token(settings.jwt_secret, principal, settings.jwt_ttl_seconds), "token_type": "bearer",
                "tenant_id": principal.tenant_id, "role": principal.role.value, "worker_id": principal.worker_id,
                "email": u.email, "display_name": u.display_name}

    # ------------------------------------------------------------- auth
    @r.post("/auth/login")
    async def login(body: Login):
        rows = await memberships(body.email)
        # security log: hashed email only (no PII); the client IP is in the edge access log
        who = hashlib.sha256(body.email.strip().lower().encode()).hexdigest()[:12]
        if not rows or not verify_password(body.password, rows[0][0].password_hash):
            log.warning("login failed", extra={"ctx": {"event": "auth.login_failed", "email_sha": who, "known_user": bool(rows)}})
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid email or password")
        chosen = next((x for x in rows if str(x[1].tenant_id) == body.tenant_id), None) if body.tenant_id else rows[0]
        if chosen is None:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "no membership in that tenant")
        u, m, _ = chosen
        log.info("login ok", extra={"ctx": {"event": "auth.login_ok", "email_sha": who, "tenant_id": str(m.tenant_id), "role": m.role}})
        return {**token_for(u, m), "tenants": [{"tenant_id": str(x[1].tenant_id), "name": x[2].name, "role": x[1].role} for x in rows]}

    @r.get("/auth/tenants")
    async def my_tenants(principal: Principal = Depends(current_principal)):
        if not principal.subject.startswith("user:"):
            return [{"tenant_id": principal.tenant_id, "role": principal.role.value}]
        rows = await memberships(principal.subject[5:])
        return [{"tenant_id": str(m.tenant_id), "name": t.name, "role": m.role} for _, m, t in rows]

    @r.post("/auth/switch-tenant")
    async def switch_tenant(body: SwitchTenant, principal: Principal = Depends(current_principal)):
        if not principal.subject.startswith("user:"):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "only password users can switch tenant")
        rows = await memberships(principal.subject[5:])
        chosen = next((x for x in rows if str(x[1].tenant_id) == body.tenant_id), None)
        if chosen is None:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "no membership in that tenant")
        return token_for(chosen[0], chosen[1])

    # ------------------------------------------------------------- today
    @r.get("/dashboard/today")
    async def today(principal: Principal = Depends(current_principal)):
        b = bundle_for(principal)
        cfg = b.config
        day = L.today_local(cfg)
        scope = _worker_scope(principal)
        async with tenant_session(sm, principal.tenant_id) as s:
            metrics = await compute_day(s, day, cfg.tenant.timezone)
            visits = (await s.execute(
                select(VisitPlan, Lead, Customer).join(Lead, Lead.id == VisitPlan.lead_id).join(Customer, Customer.id == Lead.customer_id)
                .where(VisitPlan.resolved_date >= day, VisitPlan.resolved_date < day + timedelta(days=7),
                       VisitPlan.status.in_(["PLANNED", "CONFIRMED"]), *scope)
                .order_by(VisitPlan.resolved_date, VisitPlan.resolved_time_window).limit(50))).all()
            due = (await s.execute(
                select(FollowupTask, Lead, Customer).join(Lead, Lead.id == FollowupTask.lead_id).join(Customer, Customer.id == Lead.customer_id)
                .where(or_(FollowupTask.status.in_(["NEEDS_ACTION", "AWAITING_APPROVAL", "FAILED"]),
                           and_(FollowupTask.status.in_(["SCHEDULED", "QUEUED"]),
                                FollowupTask.due_at < datetime.now(timezone.utc) + timedelta(hours=24))), *scope)
                .order_by(FollowupTask.due_at).limit(50))).all()
            hot = (await s.execute(
                select(Lead, Customer).join(Customer, Customer.id == Lead.customer_id)
                .where(Lead.closed_at.is_(None), *scope).order_by(desc(Lead.score), desc(Lead.updated_at)).limit(10))).all()
            tenant = await s.get(Tenant, principal.tenant_id)
        return {
            "tenant": {"id": principal.tenant_id, "name": tenant.name if tenant else cfg.tenant.name, "slug": b.slug,
                       "industry": cfg.tenant.industry, "timezone": cfg.tenant.timezone},
            "date": day.isoformat(), "metrics": metrics,
            "visits_upcoming": [_visit_dict(v, l, c) for v, l, c in visits],
            "followups_attention": [_task_dict(t, l, c) for t, l, c in due],
            "hot_leads": [_lead_dict(l, c) for l, c in hot],
        }

    # ------------------------------------------------------------- leads
    @r.get("/leads")
    async def list_leads(stage: Optional[str] = None, q: Optional[str] = Query(None, max_length=100), source: Optional[str] = None,
                         open_only: bool = False, limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0),
                         principal: Principal = Depends(current_principal)):
        b = bundle_for(principal)
        async with tenant_session(sm, principal.tenant_id) as s:
            stmt = (select(Lead, Customer, Worker).join(Customer, Customer.id == Lead.customer_id)
                    .join(Worker, Worker.id == Lead.assigned_worker_id, isouter=True).where(*_worker_scope(principal)))
            if stage:
                stmt = stmt.where(Lead.stage == stage)
            if source:
                stmt = stmt.where(Lead.source == source)
            if open_only:
                stmt = stmt.where(Lead.closed_at.is_(None))
            if q:
                like = f"%{q.strip()}%"
                stmt = stmt.where(or_(Customer.phone.ilike(like), Customer.display_name.ilike(like), Lead.interest.ilike(like)))
            total = await s.scalar(select(func.count()).select_from(stmt.subquery()))
            rows = (await s.execute(stmt.order_by(desc(Lead.updated_at)).limit(limit).offset(offset))).all()
        return {"total": total or 0, "stages": list(dict.fromkeys(b.config.lead.stages + ["DORMANT"])),
                "items": [_lead_dict(l, c, w) for l, c, w in rows]}

    @r.get("/leads/{lead_id}")
    async def lead_detail(lead_id: str, principal: Principal = Depends(current_principal)):
        bundle_for(principal)
        async with tenant_session(sm, principal.tenant_id) as s:
            lead = await _lead_or_404(s, principal, lead_id)
            cust = await s.get(Customer, lead.customer_id)
            worker = await s.get(Worker, lead.assigned_worker_id) if lead.assigned_worker_id else None
            campaign = await s.get(Campaign, lead.campaign_id) if lead.campaign_id else None
            events = (await s.execute(select(LeadEvent).where(LeadEvent.lead_id == lead.id).order_by(desc(LeadEvent.created_at)).limit(100))).scalars().all()
            visits = (await s.execute(select(VisitPlan).where(VisitPlan.lead_id == lead.id).order_by(desc(VisitPlan.created_at)))).scalars().all()
            tasks = (await s.execute(select(FollowupTask).where(FollowupTask.lead_id == lead.id).order_by(FollowupTask.due_at))).scalars().all()
            approvals = (await s.execute(select(Approval).where(Approval.lead_id == lead.id).order_by(desc(Approval.created_at)).limit(20))).scalars().all()
            msgs = (await s.execute(select(Message).where(Message.customer_id == lead.customer_id)
                                    .order_by(desc(Message.created_at)).limit(50))).scalars().all()
        return {
            "lead": _lead_dict(lead, cust, worker),
            "campaign": {"id": campaign.id, "platform": campaign.platform, "name": campaign.name, "external_id": campaign.external_id} if campaign else None,
            "referral": lead.referral,
            "events": [{"id": e.id, "type": e.type, "from": e.from_value, "to": e.to_value, "actor": e.actor,
                        "details": e.details, "created_at": _iso(e.created_at)} for e in events],
            "visits": [_visit_dict(v) for v in visits],
            "followups": [_task_dict(t) for t in tasks],
            "approvals": [_approval_dict(a) for a in approvals],
            "messages": [{"id": m.id, "direction": m.direction, "body": m.body, "intent": m.parsed_intent, "status": m.status,
                          "sent_by": m.sent_by, "created_at": _iso(m.created_at)} for m in reversed(msgs)],
        }

    @r.patch("/leads/{lead_id}")
    async def patch_lead(lead_id: str, body: LeadPatch, principal: Principal = Depends(current_principal)):
        cfg = bundle_for(principal).config
        actor = principal.subject
        async with tenant_session(sm, principal.tenant_id) as s:
            lead = await _lead_or_404(s, principal, lead_id, lock=True)
            if body.assigned_worker_id is not None:
                _require(principal, Role.OWNER, Role.MANAGER)
                wid = _uuid(body.assigned_worker_id, "worker id")
                if not await s.get(Worker, wid):
                    raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "unknown worker")
                if wid != lead.assigned_worker_id:
                    await L.add_event(s, lead.tenant_id, lead.id, "assigned", actor, from_value=lead.assigned_worker_id, to_value=wid)
                    lead.assigned_worker_id = wid
            if body.interest is not None and body.interest != lead.interest:
                await L.add_event(s, lead.tenant_id, lead.id, "field", actor, from_value=lead.interest, to_value=body.interest,
                                  details={"field": "interest"})
                lead.interest = body.interest or None
            if body.stage:
                if body.stage == "LOST" and not (body.lost_reason or "").strip():
                    raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "lost_reason is required")
                try:
                    await L.set_stage(s, cfg, lead, body.stage, actor, force=True, reason=body.lost_reason)
                except ValueError as exc:
                    raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
            lead.score = L.compute_score(lead, cfg)
            lead.updated_at = datetime.now(timezone.utc)
            await L.refresh_next_followup(s, lead)
            cust = await s.get(Customer, lead.customer_id)
            out = _lead_dict(lead, cust)
        await _publish_lead(lead.id, lead.stage, "manual_update", principal, lead.customer_id)
        return out

    async def _publish_lead(lead_id: str, stage: str, change: str, principal: Principal, customer_id: Optional[str] = None):
        try:
            await bus.publish(Topics.LEAD_UPDATED, Envelope(event_type=Topics.LEAD_UPDATED, tenant_id=principal.tenant_id,
                                                            customer_id=customer_id,
                                                            payload={"lead_id": lead_id, "stage": stage, "change": change}))
        except Exception as exc:  # noqa: BLE001 - UI notification only
            log.warning("lead.updated publish failed", extra={"ctx": {"error": str(exc)[:200]}})

    # ------------------------------------------------------------- visits
    @r.get("/visits")
    async def list_visits(date_from: Optional[date] = Query(None, alias="from"), date_to: Optional[date] = Query(None, alias="to"),
                          status_: Optional[str] = Query(None, alias="status"), principal: Principal = Depends(current_principal)):
        cfg = bundle_for(principal).config
        today_ = L.today_local(cfg)
        a = date_from or today_ - timedelta(days=7)
        b_ = date_to or today_ + timedelta(days=30)
        async with tenant_session(sm, principal.tenant_id) as s:
            stmt = (select(VisitPlan, Lead, Customer).join(Lead, Lead.id == VisitPlan.lead_id).join(Customer, Customer.id == Lead.customer_id)
                    .where(VisitPlan.resolved_date >= a, VisitPlan.resolved_date <= b_, *_worker_scope(principal)))
            if status_:
                stmt = stmt.where(VisitPlan.status == status_)
            else:
                stmt = stmt.where(VisitPlan.status != "RESCHEDULED")
            rows = (await s.execute(stmt.order_by(VisitPlan.resolved_date, VisitPlan.resolved_time_window).limit(500))).all()
        items = [_visit_dict(v, l, c) for v, l, c in rows]
        by_day: dict[str, int] = {}
        for it in items:
            by_day[it["date"]] = by_day.get(it["date"], 0) + 1
        return {"from": a.isoformat(), "to": b_.isoformat(), "today": today_.isoformat(), "by_day": by_day, "items": items}

    @r.post("/visits/{visit_id}/outcome")
    async def visit_outcome(visit_id: str, body: VisitOutcome, principal: Principal = Depends(current_principal)):
        cfg = bundle_for(principal).config
        async with tenant_session(sm, principal.tenant_id) as s:
            plan = (await s.execute(select(VisitPlan).where(VisitPlan.id == _uuid(visit_id)).with_for_update())).scalar_one_or_none()
            if not plan:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "visit not found")
            await _lead_or_404(s, principal, plan.lead_id)
            if plan.status in ("RESCHEDULED", "CANCELLED", "VISITED") and plan.status != body.status:
                raise HTTPException(status.HTTP_409_CONFLICT, f"visit is already {plan.status}")
            lead = await L.set_visit_outcome(s, cfg, plan, body.status, principal.subject)
            out = _visit_dict(plan, lead)
        await _publish_lead(lead.id, lead.stage, f"visit_{body.status.lower()}", principal, lead.customer_id)
        return out

    # ------------------------------------------------------------- follow-ups
    @r.get("/followups")
    async def list_followups(status_: Optional[str] = Query(None, alias="status"), limit: int = Query(200, ge=1, le=500),
                             principal: Principal = Depends(current_principal)):
        bundle_for(principal)
        async with tenant_session(sm, principal.tenant_id) as s:
            stmt = (select(FollowupTask, Lead, Customer).join(Lead, Lead.id == FollowupTask.lead_id)
                    .join(Customer, Customer.id == Lead.customer_id).where(*_worker_scope(principal)))
            if status_:
                stmt = stmt.where(FollowupTask.status.in_(status_.split(",")))
            rows = (await s.execute(stmt.order_by(FollowupTask.due_at).limit(limit))).all()
            counts = dict((await s.execute(select(FollowupTask.status, func.count()).group_by(FollowupTask.status))).all())
        return {"counts": counts, "items": [_task_dict(t, l, c) for t, l, c in rows]}

    @r.post("/followups/{task_id}/skip")
    async def skip_followup(task_id: str, principal: Principal = Depends(current_principal)):
        bundle_for(principal)
        async with tenant_session(sm, principal.tenant_id) as s:
            t = (await s.execute(select(FollowupTask).where(FollowupTask.id == _uuid(task_id)).with_for_update())).scalar_one_or_none()
            if not t:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "follow-up not found")
            lead = await _lead_or_404(s, principal, t.lead_id)
            if t.status not in ("SCHEDULED", "NEEDS_ACTION", "AWAITING_APPROVAL", "FAILED"):
                raise HTTPException(status.HTTP_409_CONFLICT, f"follow-up is {t.status}")
            t.status, t.skip_reason, t.completed_at = "SKIPPED", f"skipped_by:{principal.subject}"[:128], datetime.now(timezone.utc)
            pend = (await s.execute(select(Approval).where(Approval.followup_task_id == t.id, Approval.status == "PENDING"))).scalars().all()
            for a in pend:
                a.status, a.decided_at, a.reviewer = "SUPERSEDED", datetime.now(timezone.utc), principal.subject
            await L.add_event(s, lead.tenant_id, lead.id, "followup_skipped", principal.subject, details={"task_id": t.id})
            await L.refresh_next_followup(s, lead)
            out = _task_dict(t)
        return out

    @r.post("/followups/{task_id}/send-now")
    async def send_followup_now(task_id: str, body: FollowupSendNow, principal: Principal = Depends(current_principal)):
        bundle_for(principal)
        async with outbox.session(principal.tenant_id) as s:
            t = (await s.execute(select(FollowupTask).where(FollowupTask.id == _uuid(task_id)).with_for_update())).scalar_one_or_none()
            if not t:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "follow-up not found")
            lead = await _lead_or_404(s, principal, t.lead_id)
            if t.status == "FAILED" and (t.skip_reason or "").startswith("delivery_unknown") and not body.confirm_unknown:
                raise HTTPException(status.HTTP_409_CONFLICT,
                                    "previous send may have been delivered (outcome unknown); check WhatsApp, then confirm_unknown")
            if body.text:
                # a human writes the follow-up themselves (manual rules / NEEDS_ACTION)
                if t.status not in ("SCHEDULED", "NEEDS_ACTION", "FAILED", "AWAITING_APPROVAL"):
                    raise HTTPException(status.HTTP_409_CONFLICT, f"follow-up is {t.status}")
                cust = await s.get(Customer, lead.customer_id)
                line = routing.line_by_id(lead.whatsapp_number_id) if lead.whatsapp_number_id else None
                if not line or not cust:
                    raise HTTPException(status.HTTP_409_CONFLICT, "lead has no sending line")
                if principal.is_worker and line.worker_id != principal.worker_id:
                    raise HTTPException(status.HTTP_403_FORBIDDEN, "workers may only send from their own line")
                t.status, t.attempt = "QUEUED", t.attempt + (1 if t.status == "FAILED" else 0)
                for a in (await s.execute(select(Approval).where(Approval.followup_task_id == t.id, Approval.status == "PENDING"))).scalars().all():
                    a.status, a.decided_at, a.reviewer = "SUPERSEDED", datetime.now(timezone.utc), principal.subject
                await outbox.add(s, Topics.OUTBOUND_REQUESTED, outbound_envelope(
                    principal.tenant_id, line, cust.phone, body.text, requested_by=principal.subject, sent_by="human",
                    request_key=f"task-manual:{t.id}:{t.attempt}", lead_id=lead.id, followup_task_id=t.id, customer_id=cust.id))
                await write_audit(s, principal.tenant_id, "OUTBOUND_REQUESTED", principal.subject, "followup_task", t.id,
                                  {"text": body.text, "line": line.worker_key})
            else:
                if t.status not in ("SCHEDULED", "FAILED"):
                    raise HTTPException(status.HTTP_409_CONFLICT, "only scheduled or failed follow-ups can be re-run; send text instead")
                if t.status == "FAILED":
                    t.attempt += 1
                t.status, t.due_at, t.skip_reason = "SCHEDULED", datetime.now(timezone.utc), None
            out = _task_dict(t)
        return out

    # ------------------------------------------------------------- approvals
    @r.get("/approvals")
    async def list_approvals(status_: str = Query("PENDING", alias="status"), limit: int = Query(100, ge=1, le=500),
                             principal: Principal = Depends(current_principal)):
        bundle_for(principal)
        async with tenant_session(sm, principal.tenant_id) as s:
            stmt = (select(Approval, AgentRun, Customer).join(AgentRun, AgentRun.id == Approval.agent_run_id, isouter=True)
                    .join(Customer, Customer.id == Approval.customer_id, isouter=True)
                    .where(Approval.status.in_(status_.split(","))))
            if principal.is_worker:
                stmt = stmt.join(Lead, Lead.id == Approval.lead_id).where(Lead.assigned_worker_id == principal.worker_id)
            rows = (await s.execute(stmt.order_by(Approval.created_at if status_ == "PENDING" else desc(Approval.decided_at)).limit(limit))).all()
            pq = select(func.count()).select_from(Approval).where(Approval.status == "PENDING")
            if principal.is_worker:
                pq = pq.join(Lead, Lead.id == Approval.lead_id).where(Lead.assigned_worker_id == principal.worker_id)
            pending = await s.scalar(pq)
        return {"pending": pending or 0, "items": [_approval_dict(a, run, c) for a, run, c in rows]}

    @r.post("/approvals/{approval_id}/decide")
    async def decide(approval_id: str, body: Decide, principal: Principal = Depends(current_principal)):
        cfg = bundle_for(principal).config
        now = datetime.now(timezone.utc)
        async with outbox.session(principal.tenant_id) as s:
            a = (await s.execute(select(Approval).where(Approval.id == _uuid(approval_id)).with_for_update())).scalar_one_or_none()
            if not a:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "approval not found")
            if a.lead_id:
                await _lead_or_404(s, principal, a.lead_id)
            elif principal.is_worker:
                raise HTTPException(status.HTTP_403_FORBIDDEN, "insufficient role")
            if a.status != "PENDING":
                raise HTTPException(status.HTTP_409_CONFLICT, f"approval already {a.status}")
            a.reviewer, a.decided_at = principal.subject, now
            task = await s.get(FollowupTask, a.followup_task_id) if a.followup_task_id else None
            if body.decision == "reject":
                if not (body.reason or "").strip():
                    raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "reason is required to reject")
                a.status, a.reject_reason = "REJECTED", body.reason.strip()
                if task and task.status in ("AWAITING_APPROVAL", "QUEUED"):
                    task.status, task.skip_reason, task.completed_at = "SKIPPED", "approval_rejected", now
            else:
                text_ = (body.text or "").strip() if body.decision == "edit" else a.proposed_text
                if not text_:
                    raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "text is required to edit")
                changed = text_.strip() != (a.proposed_text or "").strip()
                a.status = "EDITED" if changed else "APPROVED"
                a.final_text = text_
                a.edit_distance = edit_distance(a.proposed_text or "", text_) if changed else 0
                cust = await s.get(Customer, a.customer_id) if a.customer_id else None
                line = routing.line_by_id(a.whatsapp_number_id) if a.whatsapp_number_id else None
                if not cust or not line:
                    raise HTTPException(status.HTTP_409_CONFLICT, "approval has no recipient/line")
                last_in = await s.scalar(select(Conversation.last_inbound_at).where(
                    Conversation.whatsapp_number_id == a.whatsapp_number_id, Conversation.customer_id == cust.id))
                within = bool(last_in and now - last_in <= SERVICE_WINDOW)
                template = None
                if not within and a.template_name:
                    tpl = next((x for x in cfg.meta_templates if x.name == a.template_name), None)
                    template = TemplateSpec(name=a.template_name, language=tpl.language if tpl else "en_US",
                                            body_params=list(a.template_params or []))
                elif not within:
                    raise HTTPException(status.HTTP_409_CONFLICT,
                                        "customer is outside the 24h WhatsApp window and no approved template exists; reply from WhatsApp or reject")
                if task and task.status == "AWAITING_APPROVAL":
                    task.status = "QUEUED"
                await outbox.add(s, Topics.OUTBOUND_REQUESTED, outbound_envelope(
                    principal.tenant_id, line, cust.phone, text_, requested_by=principal.subject,
                    sent_by="followup" if a.kind == "followup" else "agent", request_key=f"approval:{a.id}", template=template,
                    lead_id=a.lead_id, agent_run_id=a.agent_run_id, approval_id=a.id, followup_task_id=a.followup_task_id,
                    customer_id=cust.id))
            await write_audit(s, principal.tenant_id, f"APPROVAL_{a.status}", principal.subject, "approval", a.id,
                              {"edit_distance": a.edit_distance, "reason": a.reject_reason})
            out = _approval_dict(a)
            await outbox.add(s, Topics.APPROVAL_DECIDED, Envelope(
                event_id=str(uuid.uuid5(_NS, f"decided:{a.id}")), event_type=Topics.APPROVAL_DECIDED, tenant_id=principal.tenant_id,
                payload={**ApprovalDecidedPayload(approval_id=out["id"], decision=a.status, reviewer=principal.subject).model_dump(),
                         "lead_id": out["lead_id"]}))
        return out

    # ------------------------------------------------------------- insights / learning
    @r.get("/insights")
    async def insights(days: int = Query(30, ge=1, le=365), principal: Principal = Depends(current_principal)):
        _require(principal, Role.OWNER, Role.MANAGER)
        cfg = bundle_for(principal).config
        today_ = L.today_local(cfg)
        start = today_ - timedelta(days=days - 1)
        async with tenant_session(sm, principal.tenant_id) as s:
            series = (await s.execute(select(MetricsDaily).where(MetricsDaily.date >= start).order_by(MetricsDaily.date))).scalars().all()
            funnel = dict((await s.execute(select(Lead.stage, func.count()).where(Lead.created_at >= datetime.now(timezone.utc) - timedelta(days=days))
                                           .group_by(Lead.stage))).all())
            by_source = dict((await s.execute(select(func.coalesce(Lead.source, "unknown"), func.count())
                                              .where(Lead.created_at >= datetime.now(timezone.utc) - timedelta(days=days))
                                              .group_by(Lead.source))).all())
            camp = (await s.execute(
                select(Campaign.id, Campaign.platform, Campaign.name, Campaign.external_id, func.count(Lead.id),
                       func.count(Lead.id).filter(Lead.stage.in_(["VISIT_PLANNED", "VISITED", "WON"])),
                       func.count(Lead.id).filter(Lead.stage == "WON"))
                .join(Lead, Lead.campaign_id == Campaign.id, isouter=True).group_by(Campaign.id).order_by(desc(func.count(Lead.id))).limit(20))).all()
            autonomy = (await s.execute(select(AutonomyStat).order_by(AutonomyStat.task, AutonomyStat.topic))).scalars().all()
            ex_counts = dict((await s.execute(select(LearnedExample.outcome, func.count()).group_by(LearnedExample.outcome))).all())
            decided = (await s.execute(select(Approval.status, func.count()).where(
                Approval.decided_at >= datetime.now(timezone.utc) - timedelta(days=days)).group_by(Approval.status))).all()
        return {
            "days": days, "series": [{"date": m.date.isoformat(), **(m.data or {})} for m in series],
            "funnel": funnel, "by_source": by_source,
            "campaigns": [{"id": c[0], "platform": c[1], "name": c[2] or c[3], "leads": c[4], "visits": c[5], "won": c[6]} for c in camp],
            "approvals": dict(decided),
            "learning": {"examples": ex_counts, "autonomy": [
                {"task": x.task, "topic": x.topic, "approved": x.approved_unchanged, "edited": x.edited, "rejected": x.rejected,
                 "auto_enabled": x.auto_enabled, "recent": len(x.recent or [])} for x in autonomy]},
        }

    @r.get("/learning/examples")
    async def learned_examples(principal: Principal = Depends(current_principal)):
        _require(principal, Role.OWNER, Role.MANAGER)
        bundle_for(principal)
        async with tenant_session(sm, principal.tenant_id) as s:
            rows = (await s.execute(select(LearnedExample).order_by(desc(LearnedExample.created_at)).limit(200))).scalars().all()
        return [{"id": e.id, "task": e.task, "topic": e.topic, "question": e.question_text, "answer": e.answer_text,
                 "reject_reason": e.reject_reason, "outcome": e.outcome, "weight": e.weight, "active": e.active,
                 "use_count": e.use_count, "created_at": _iso(e.created_at)} for e in rows]

    @r.post("/learning/examples/{example_id}/toggle")
    async def toggle_example(example_id: str, principal: Principal = Depends(current_principal)):
        _require(principal, Role.OWNER, Role.MANAGER)
        async with tenant_session(sm, principal.tenant_id) as s:
            e = await s.get(LearnedExample, _uuid(example_id))
            if not e:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "example not found")
            if e.outcome == "REJECTED":
                raise HTTPException(status.HTTP_409_CONFLICT, "rejected examples are kept as negative examples")
            e.active = not e.active
            # active examples are reused verbatim and feed the auto-send gate: record who changed it
            await write_audit(s, principal.tenant_id, "LEARNED_EXAMPLE_TOGGLED", principal.subject, "learned_example", e.id,
                              {"active": e.active, "task": e.task, "topic": e.topic})
            return {"id": e.id, "active": e.active}

    # ------------------------------------------------------------- settings
    @r.get("/settings/config")
    async def settings_config(principal: Principal = Depends(current_principal)):
        _require(principal, Role.OWNER, Role.MANAGER)
        b = bundle_for(principal)
        async with tenant_session(sm, principal.tenant_id) as s:
            pl = (await s.execute(select(PriceList).where(PriceList.is_active.is_(True)))).scalars().first()
            items = (await s.execute(select(PriceItem).where(PriceItem.price_list_id == pl.id).order_by(PriceItem.category, PriceItem.name))
                     ).scalars().all() if pl else []
            workers = (await s.execute(select(Worker).where(Worker.is_active.is_(True)).order_by(Worker.display_name))).scalars().all()
        return {
            "slug": b.slug, "checksums": b.checksums(), "config_yaml": b.config_text, "skill_md": b.skill_md,
            "config": b.config.model_dump(mode="json"),
            "knowledge": [{"path": d.source_path, "title": d.title, "chars": len(d.text)} for d in b.knowledge],
            "workers": [{"id": w.id, "name": w.display_name, "role": w.role} for w in workers],
            "price_list": {"version": pl.version, "rows": pl.row_count, "source_file": pl.source_file, "imported_by": pl.imported_by,
                           "imported_at": _iso(pl.imported_at)} if pl else None,
            "price_items": [{"sku": i.sku, "name": i.name, "category": i.category, "price": _num(i.price), "price_min": _num(i.price_min),
                             "price_max": _num(i.price_max), "unit": i.unit, "valid_until": _iso(i.valid_until), "aliases": i.aliases}
                            for i in items],
        }

    @r.post("/settings/pricing")
    async def upload_pricing(body: PricingUpload, principal: Principal = Depends(current_principal)):
        _require(principal, Role.OWNER, Role.MANAGER)
        b = bundle_for(principal)
        rows, errors = parse_pricing_csv(body.csv_text)
        blocking = blocking_errors(errors)
        async with tenant_session(sm, principal.tenant_id) as s:
            pl = (await s.execute(select(PriceList).where(PriceList.is_active.is_(True)))).scalars().first()
            old = [] if not pl else [
                {"sku": i.sku, "name": i.name, "price": _num(i.price), "price_min": _num(i.price_min), "price_max": _num(i.price_max)}
                for i in (await s.execute(select(PriceItem).where(PriceItem.price_list_id == pl.id))).scalars().all()]
            diff = diff_price_rows(old, rows)
            result = {"dry_run": body.dry_run, "rows": len(rows), "errors": blocking, "warnings": [e for e in errors if e not in blocking],
                      "diff": diff}
            if body.dry_run or blocking:
                return result
            try:
                result["import"] = await import_pricing(s, principal.tenant_id, body.csv_text, body.source_file[:255], principal.subject,
                                                        currency=b.config.tenant.currency)
            except ValueError as exc:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
            await write_audit(s, principal.tenant_id, "PRICING_IMPORTED", principal.subject, "price_list",
                              str(result["import"].get("version")), {"rows": len(rows)})
        return result

    return r
