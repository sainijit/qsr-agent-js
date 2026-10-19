"""Agent worker: decides how to answer customers and runs follow-ups.

  ai.intent.extracted / ai.review.flagged -> price | faq | greeting proposal -> auto-send or approval
  visit.planned                            -> visit acknowledgement
  followup.due                             -> render rule -> send / approval / needs-action
  approval.decided                         -> learned_examples + autonomy_stats (learning from approvals)
  outbound.message.sent                    -> follow-up task SENT/FAILED
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from zuvo_common import leads as L
from zuvo_common.bus import EventBus, KafkaEventBus, Record
from zuvo_common.bundle import TenantBundle, TenantConfig
from zuvo_common.contract import ConsumerContract
from zuvo_common.db import make_sessionmaker, tenant_session
from zuvo_common.events import (ApprovalDecidedPayload, Envelope, ExtractionPayload, FollowupDuePayload, OutboundSentPayload,
                                TemplateSpec, Topics)
from zuvo_common.idempotency import RedisIdempotencyStore
from zuvo_common.learning import APPROVED, EDITED, REJECTED, AutonomyState, record_outcome
from zuvo_common.logging import get_logger
from zuvo_common.runtime import install_shutdown
from zuvo_common.models import (AgentRun, Approval, AutonomyStat, Conversation, Customer, FollowupTask, KbChunk, KbDocument,
                                LearnedExample, Lead, Message, PriceItem, PriceList, VisitPlan)
from zuvo_common.outbound import outbound_envelope
from zuvo_common.outbox import Outbox
from zuvo_common.processed import first_delivery
from zuvo_common.rag_client import RagClient, RagUnavailable
from zuvo_common.settings import get_settings
from zuvo_common.tenancy import TenantRouting
from zuvo_common.dates import VisitWhen
from zuvo_common.usage import cost_inr, month_spend_inr, record_usage

from .drafter import Drafter, guard
from .logic import (Proposal, choose_task, decide, faq_proposal, mode_for, price_proposal, render, run_diagnostics,
                    run_outcome)

log = get_logger("agent-worker")
ACTOR = "agent"
GROUP = "agent-worker"
SERVICE_WINDOW = timedelta(hours=24)
_NS = uuid.UUID("7f3e2d1c-0b9a-4876-9543-210fedcba987")


def _f(v) -> Optional[float]:
    return float(v) if isinstance(v, Decimal) else v


class AgentWorker:
    def __init__(self, sm: async_sessionmaker[AsyncSession], bus: EventBus, routing: TenantRouting,
                 drafter: Optional[Drafter] = None, rag: Optional[RagClient] = None):
        self.sm, self.bus, self.routing, self.drafter, self.rag = sm, bus, routing, drafter, rag
        self.outbox = Outbox(sm, bus)

    # ------------------------------------------------------------ data access

    async def _price_items(self, s: AsyncSession) -> list[dict]:
        rows = (await s.execute(
            select(PriceItem).join(PriceList, PriceList.id == PriceItem.price_list_id).where(PriceList.is_active.is_(True))
        )).scalars().all()
        return [{"sku": r.sku, "name": r.name, "category": r.category or "", "attrs": r.attrs or {}, "price": _f(r.price),
                 "price_min": _f(r.price_min), "price_max": _f(r.price_max), "unit": r.unit, "aliases": r.aliases or [],
                 "valid_until": r.valid_until.isoformat() if r.valid_until else None, "notes": r.notes or ""} for r in rows]

    async def _chunks(self, s: AsyncSession) -> list[tuple[dict, str]]:
        # a bundle document has lexical chunks (current version) and embedded ones (last indexed version): prefer lexical;
        # an uploaded document only has embedded chunks
        lexical = select(KbChunk.document_id).where(KbChunk.embed_model.is_(None))
        rows = (await s.execute(
            select(KbChunk.id, KbChunk.text, KbDocument.title).join(KbDocument, KbDocument.id == KbChunk.document_id)
            .where(KbDocument.is_active.is_(True),
                   KbChunk.embed_model.is_(None) | KbChunk.document_id.not_in(lexical))
        )).all()
        return [({"id": r.id, "title": r.title}, r.text) for r in rows]

    async def _rag_retrieve(self, tenant_id: str, cfg: TenantConfig, question: str) -> tuple[Optional[dict], Optional[str]]:
        """(zuvo-ai-rag result, error category) for tenants in rag mode; a None result means use lexical retrieval."""
        if cfg.knowledge.retrieval != "rag" or not question:
            return None, None
        if self.rag is None:
            return None, "not_configured"
        try:
            return await self.rag.retrieve(tenant_id, question, cfg.knowledge.top_k, cfg.knowledge.min_similarity), None
        except RagUnavailable as exc:
            log.warning("zuvo-ai-rag unavailable; lexical fallback", extra={"ctx": {
                "tenant_id": tenant_id, "operation": "rag.retrieve", "outcome": "fallback", "error_code": exc.category}})
            return None, exc.category

    async def _examples(self, s: AsyncSession, task: str) -> list[dict]:
        rows = (await s.execute(
            select(LearnedExample).where(LearnedExample.task == task, LearnedExample.active.is_(True),
                                         LearnedExample.outcome.in_(("APPROVED", "EDITED")))
            .order_by(LearnedExample.created_at.desc()).limit(200)
        )).scalars().all()
        return [{"id": r.id, "topic": r.topic, "question_text": r.question_text, "answer_text": r.answer_text,
                 "price_item_skus": r.price_item_skus or [], "weight": r.weight} for r in rows]

    async def _auto_enabled(self, s: AsyncSession, tenant_id: str, task: str, topic: str) -> bool:
        return bool(await s.scalar(select(AutonomyStat.auto_enabled).where(
            AutonomyStat.tenant_id == tenant_id, AutonomyStat.task == task, AutonomyStat.topic == topic)))

    async def _within_window(self, s: AsyncSession, number_id: str, customer_id: str) -> bool:
        last = await s.scalar(select(Conversation.last_inbound_at).where(
            Conversation.whatsapp_number_id == number_id, Conversation.customer_id == customer_id))
        return bool(last and datetime.now(timezone.utc) - last <= SERVICE_WINDOW)

    # ------------------------------------------------------------ drafting

    async def _polish(self, s: AsyncSession, env: Envelope, bundle: TenantBundle, p: Proposal, customer_text: str,
                      examples: list[dict], lang: Optional[str], items: list[dict]) -> dict:
        """Optionally rephrase with Gemini; returns usage info for the agent run."""
        cfg = bundle.config
        info = {"model": None, "tokens_in": 0, "tokens_out": 0, "latency_ms": 0, "cost": Decimal("0")}
        if not (self.drafter and cfg.agent.use_llm_drafting and p.facts and p.task in ("price", "faq")):
            return info
        if p.retrieved.get("example_id"):
            return info  # reusing an owner-approved answer verbatim
        if await month_spend_inr(s, datetime.now(timezone.utc).date()) >= cfg.tenant.monthly_ai_budget_inr:
            p.reasons.append("ai_budget_exhausted")
            return info
        shots = [(e["question_text"], e["answer_text"]) for e in examples if e["topic"] == p.topic][:3]
        try:
            r = await self.drafter.draft(persona=cfg.ai.persona, skill_md=bundle.skill_md, customer_text=customer_text,
                                         facts=p.facts, fallback=p.text, examples=shots, lang=lang)
        except Exception as exc:  # noqa: BLE001 - deterministic text is always available
            log.warning("draft failed", extra={"ctx": {"operation": "gemini.draft", "outcome": "error",
                                                       "error_code": exc.__class__.__name__, "error": f"{exc.__class__.__name__}: {str(exc)[:200]}"}})
            info["error"] = exc.__class__.__name__
            return info
        info.update(model=r.model, tokens_in=r.tokens_in, tokens_out=r.tokens_out, latency_ms=r.latency_ms,
                    cost=cost_inr(r.tokens_in, r.tokens_out))
        await record_usage(s, env.tenant_id, datetime.now(timezone.utc).date(), "agent-worker", r.model, r.tokens_in, r.tokens_out)
        bad = guard(r.text, p.facts + p.allowed_texts, customer_text, items if p.task == "price" else ())
        if bad or not r.text:
            p.reasons.append("draft_guard")
            p.retrieved["rejected_draft"] = r.text[:500]
            p.retrieved["unsupported_numbers"] = bad
        else:
            p.retrieved["deterministic_text"] = p.text
            p.text = r.text
        return info

    # ------------------------------------------------------------ shared finalisation

    async def _finalise(self, s: AsyncSession, env: Envelope, cfg: TenantConfig, p: Proposal, mode: str, *, flagged: bool,
                        message_id: Optional[str], lead: Optional[Lead], customer: Customer, number_id: str,
                        intent: Optional[str], question: Optional[str], usage: Optional[dict] = None,
                        diag: Optional[dict] = None) -> list[tuple[str, Envelope]]:
        auto_ok = await self._auto_enabled(s, env.tenant_id, p.task, p.topic)
        decision, reasons = decide(cfg, p, mode, flagged=flagged, topic_auto_enabled=auto_ok)
        line = self.routing.line_by_id(number_id)
        if line is None:
            decision, reasons = "SKIPPED", reasons + ["line_not_routable"]
        if customer.opted_out:
            decision, reasons = "SKIPPED", reasons + ["opted_out"]
        usage = usage or {}
        diag = diag or {}
        outcome = run_outcome(decision, reasons, rag_error=diag.get("rag_error"), draft_error=usage.get("error"))
        diagnostics = run_diagnostics(p, extraction=diag.get("extraction"), rag=diag.get("rag"),
                                      rag_error=diag.get("rag_error"), usage=usage)
        run_id = str(uuid.uuid4())
        res = await s.execute(
            insert(AgentRun).values(
                id=run_id, tenant_id=env.tenant_id, message_id=message_id, lead_id=lead.id if lead else None,
                intent=intent, task=p.task, topic=p.topic, question_text=question, retrieved=p.retrieved, proposed_text=p.text,
                confidence=p.confidence, decision=decision, decision_reasons=reasons, model=usage.get("model"),
                tokens_in=usage.get("tokens_in", 0), tokens_out=usage.get("tokens_out", 0),
                cost_inr=usage.get("cost", Decimal("0")), latency_ms=usage.get("latency_ms", 0),
                outcome=outcome, diagnostics=diagnostics,
            ).on_conflict_do_nothing(index_elements=["tenant_id", "message_id", "task"], index_where=text("message_id IS NOT NULL"))
            .returning(AgentRun.id)
        )
        if res.scalar_one_or_none() is None:
            return []  # this message/task was already handled (redelivery)
        out: list[tuple[str, Envelope]] = []
        if decision == "AUTO_SENT":
            out.append((Topics.OUTBOUND_REQUESTED, outbound_envelope(
                env.tenant_id, line, customer.phone, p.text, requested_by=ACTOR, sent_by="agent", request_key=f"run:{run_id}",
                lead_id=lead.id if lead else None, agent_run_id=run_id, customer_id=customer.id)))
        elif decision == "PENDING_APPROVAL":
            if lead:
                await s.execute(update(Approval).where(
                    Approval.lead_id == lead.id, Approval.kind == "reply", Approval.topic == p.topic, Approval.status == "PENDING",
                ).values(status="SUPERSEDED", decided_at=datetime.now(timezone.utc)))
            appr_id = str(uuid.uuid4())
            s.add(Approval(
                id=appr_id, tenant_id=env.tenant_id, agent_run_id=run_id, lead_id=lead.id if lead else None, customer_id=customer.id,
                whatsapp_number_id=number_id, kind="reply", topic=p.topic, status="PENDING", question_text=question,
                proposed_text=p.text, sla_due_at=datetime.now(timezone.utc) + timedelta(minutes=cfg.approvals.sla_minutes),
            ))
            out.append((Topics.APPROVAL_REQUESTED, Envelope(
                event_id=str(uuid.uuid5(_NS, f"appr:{appr_id}")), event_type=Topics.APPROVAL_REQUESTED, tenant_id=env.tenant_id,
                customer_id=customer.id, payload={"approval_id": appr_id, "task": p.task, "topic": p.topic, "reasons": reasons,
                                                  "lead_id": lead.id if lead else None})))
        log.info("agent decision", extra={"ctx": {"tenant_id": env.tenant_id, "task": p.task, "topic": p.topic,
                                                  "decision": decision, "confidence": p.confidence, "reasons": reasons,
                                                  "run_outcome": outcome, "agent_run_id": run_id, "operation": "agent.decide", "outcome": decision}})
        return out

    # ------------------------------------------------------------ inbound replies

    async def on_extraction(self, env: Envelope, bundle: TenantBundle, flagged: bool) -> None:
        cfg = bundle.config
        p = ExtractionPayload.model_validate(env.payload)
        x = p.extraction
        task = choose_task(x, p.tier)
        if task is None or mode_for(cfg, task) == "off":
            return
        events: list[tuple[str, Envelope]] = []
        async with self.outbox.session(env.tenant_id) as s:
            if not await first_delivery(s, GROUP, env):
                return  # durable dedupe: already decided (and its events are in the outbox)
            msg = await s.get(Message, p.message_id)
            if not msg or msg.direction != "INBOUND":
                return
            customer = await s.get(Customer, msg.customer_id)
            if (x and x.opt_out) or L.is_opt_out(msg.body or "", cfg.opt_out_keywords) or (customer and customer.opted_out):
                return  # lead-service records the opt-out; never answer it
            lead, _ = await L.ensure_open_lead(s, env.tenant_id, msg.customer_id, whatsapp_number_id=msg.whatsapp_number_id,
                                               worker_id=msg.worker_id, at=msg.created_at, referral=msg.referral,
                                               source="ctwa_ad" if msg.referral else "whatsapp", message_id=msg.id)
            if task == "greeting" and lead.last_business_msg_at is not None:
                return  # only greet a brand-new conversation
            items: list[dict] = []
            examples: list[dict] = []
            question = (x.question if x else None) or msg.body
            diag: dict = {"extraction": {"tier": p.tier, "model": p.model, "latency_ms": p.latency_ms,
                                         "input_tokens": p.input_tokens, "output_tokens": p.output_tokens,
                                         "reason": p.reason}}
            if task == "price":
                items = await self._price_items(s)
                examples = await self._examples(s, "price")
                prop = price_proposal(cfg, items, x, msg.body, L.today_local(cfg), examples)
            elif task == "faq":
                examples = await self._examples(s, "faq")
                rag, rag_error = await self._rag_retrieve(env.tenant_id, cfg, (x.question if x else None) or msg.body or "")
                prop = faq_proposal(cfg, [] if rag is not None else await self._chunks(s), x, msg.body, examples, rag)
                if rag is None and cfg.knowledge.retrieval == "rag":
                    prop.retrieved["strategy"] = "lexical_fallback"
                    prop.retrieved["rag_error"] = rag_error
                diag.update(rag=rag, rag_error=rag_error)
            elif task == "greeting":
                prop = Proposal("greeting", "greeting", render(cfg.agent.greeting_text, {"business": cfg.tenant.name}),
                                1.0 if p.tier == "tier0" else x.confidence)
            else:
                prop = Proposal("general", "general", cfg.agent.holding_reply, x.confidence, ["general_question"])
            usage = await self._polish(s, env, bundle, prop, msg.body, examples, x.lang if x else None, items)
            events = await self._finalise(s, env, cfg, prop, mode_for(cfg, task), flagged=flagged, message_id=msg.id, lead=lead,
                                          customer=customer, number_id=msg.whatsapp_number_id, intent=x.intent.value if x else None,
                                          question=question, usage=usage, diag=diag)
            await self.outbox.add_all(s, events)

    async def on_visit_planned(self, env: Envelope, bundle: TenantBundle) -> None:
        cfg = bundle.config
        if mode_for(cfg, "visit_ack") == "off":
            return
        d = env.payload
        async with self.outbox.session(env.tenant_id) as s:
            if not await first_delivery(s, GROUP, env):
                return
            lead = await s.get(Lead, d["lead_id"])
            if not lead:
                return
            customer = await s.get(Customer, lead.customer_id)
            msg = await s.get(Message, d.get("message_id")) if d.get("message_id") else None
            number_id = (msg.whatsapp_number_id if msg else None) or lead.whatsapp_number_id
            ctx = {"visit_when": d.get("describe") or d.get("date"), "address": cfg.agent.address, "business": cfg.tenant.name,
                   "customer_name": customer.display_name or ""}
            prop = Proposal("visit_ack", "visit", render(cfg.agent.visit_ack_text, ctx), float(d.get("confidence") or 0.8),
                            retrieved={"visit_plan_id": d.get("visit_plan_id")})
            events = await self._finalise(s, env, cfg, prop, mode_for(cfg, "visit_ack"), flagged=False,
                                          message_id=d.get("message_id"), lead=lead, customer=customer, number_id=number_id,
                                          intent="VISIT_PLAN", question=msg.body if msg else None)
            await self.outbox.add_all(s, events)

    # ------------------------------------------------------------ follow-ups

    async def on_followup_due(self, env: Envelope, bundle: TenantBundle) -> None:
        cfg = bundle.config
        d = FollowupDuePayload.model_validate(env.payload)
        events: list[tuple[str, Envelope]] = []
        async with self.outbox.session(env.tenant_id) as s:
            if not await first_delivery(s, GROUP, env):
                return
            task = (await s.execute(select(FollowupTask).where(FollowupTask.id == d.task_id).with_for_update())).scalar_one_or_none()
            if not task or task.status != "QUEUED":
                return
            lead = await s.get(Lead, task.lead_id)
            rule = cfg.rule(task.rule_id)
            customer = await s.get(Customer, lead.customer_id) if lead else None
            now = datetime.now(timezone.utc)

            def finish(status: str, reason: Optional[str] = None) -> None:
                task.status, task.skip_reason = status, reason
                if status in ("SKIPPED", "CANCELLED"):
                    task.completed_at = now

            if not lead or not customer or not rule or not rule.enabled:
                finish("SKIPPED", "rule_removed" if lead else "lead_missing")
                return
            if lead.closed_at is not None or customer.opted_out:
                finish("SKIPPED", "opted_out" if customer.opted_out else "lead_closed")
                return
            visit = None
            if task.visit_plan_id:
                visit = await s.get(VisitPlan, task.visit_plan_id)
                if visit and visit.status not in ("PLANNED", "CONFIRMED") and task.kind in ("visit_planned", "visit_passed"):
                    finish("SKIPPED", f"visit_{visit.status.lower()}")
                    return
            visit_when = VisitWhen(visit.resolved_date, visit.precision, visit.resolved_time_window, "").describe() if visit else ""
            ctx = {"customer_name": customer.display_name or "", "interest": lead.interest or "our products",
                   "visit_when": visit_when, "address": cfg.agent.address, "business": cfg.tenant.name}
            body = render(rule.text, ctx)
            number_id = lead.whatsapp_number_id
            line = self.routing.line_by_id(number_id) if number_id else None
            if line is None:
                finish("SKIPPED", "line_not_routable")
                return
            in_window = await self._within_window(s, number_id, customer.id)
            tpl = cfg.template(rule.template)
            template = None
            if not in_window:
                if tpl and tpl.approved:
                    template = TemplateSpec(name=tpl.name, language=tpl.language,
                                            body_params=[str(ctx.get(k.split(".")[-1], "")) or "-" for k in tpl.params])
                else:
                    finish("NEEDS_ACTION", "outside_24h_no_approved_template")
                    events.append(self._lead_event(env, lead, "followup_needs_action"))
            if task.status == "QUEUED":
                if rule.mode == "manual":
                    finish("NEEDS_ACTION", "manual_rule")
                elif rule.mode == "draft":
                    appr_id = str(uuid.uuid4())
                    s.add(Approval(
                        id=appr_id, tenant_id=env.tenant_id, lead_id=lead.id, customer_id=customer.id, whatsapp_number_id=number_id,
                        followup_task_id=task.id, kind="followup", topic=f"followup:{rule.id}", status="PENDING",
                        question_text=None, proposed_text=body, template_name=template.name if template else None,
                        template_params=template.body_params if template else None,
                        sla_due_at=None,
                    ))
                    finish("AWAITING_APPROVAL")
                    events.append((Topics.APPROVAL_REQUESTED, Envelope(
                        event_id=str(uuid.uuid5(_NS, f"appr:{appr_id}")), event_type=Topics.APPROVAL_REQUESTED,
                        tenant_id=env.tenant_id, customer_id=customer.id,
                        payload={"approval_id": appr_id, "task": "followup", "topic": f"followup:{rule.id}", "lead_id": lead.id})))
                else:
                    events.append((Topics.OUTBOUND_REQUESTED, outbound_envelope(
                        env.tenant_id, line, customer.phone, body, requested_by=f"followup:{rule.id}", sent_by="followup",
                        request_key=f"task:{task.id}", template=template, lead_id=lead.id, followup_task_id=task.id,
                        customer_id=customer.id)))
            await L.refresh_next_followup(s, lead)
            await self.outbox.add_all(s, events)

    def _lead_event(self, env: Envelope, lead: Lead, change: str) -> tuple[str, Envelope]:
        return (Topics.LEAD_UPDATED, Envelope(
            event_id=str(uuid.uuid5(_NS, f"{env.event_id}:{change}")), event_type=Topics.LEAD_UPDATED, tenant_id=env.tenant_id,
            payload={"lead_id": lead.id, "stage": lead.stage, "change": change}))

    async def on_outbound_sent(self, env: Envelope, bundle: TenantBundle) -> None:
        p = OutboundSentPayload.model_validate(env.payload)
        if not p.followup_task_id:
            return
        async with tenant_session(self.sm, env.tenant_id) as s:
            task = await s.get(FollowupTask, p.followup_task_id)
            # an UNKNOWN send parks the task as FAILED/delivery_unknown; a later reconciliation may still resolve it
            unresolved = task is not None and task.status == "FAILED" and (task.skip_reason or "").startswith("delivery_unknown")
            if not task or (task.status in ("SENT", "FAILED", "CANCELLED", "SKIPPED") and not (unresolved and p.state != "UNKNOWN")):
                return
            task.status = "SENT" if p.success else "FAILED"
            err = p.error or "send_failed"
            if p.state == "UNKNOWN" and not err.startswith("delivery_unknown"):
                err = f"delivery_unknown: {err}"
            task.skip_reason = None if p.success else err[:128]
            task.result_message_id = env.wamid
            task.completed_at = datetime.now(timezone.utc)
            lead = await s.get(Lead, task.lead_id)
            if lead:
                await L.refresh_next_followup(s, lead)

    # ------------------------------------------------------------ learning

    async def on_approval_decided(self, env: Envelope, bundle: TenantBundle) -> None:
        cfg = bundle.config
        d = ApprovalDecidedPayload.model_validate(env.payload)
        async with tenant_session(self.sm, env.tenant_id) as s:
            if not await first_delivery(s, GROUP, env):
                return  # autonomy counters are not idempotent
            appr = await s.get(Approval, d.approval_id)
            if not appr or appr.status not in ("APPROVED", "EDITED", "REJECTED"):
                return
            run = await s.get(AgentRun, appr.agent_run_id) if appr.agent_run_id else None
            task = run.task if run else (appr.kind if appr.kind != "reply" else "general")
            topic = appr.topic or (run.topic if run else "general")
            outcome = {"APPROVED": APPROVED, "EDITED": EDITED, "REJECTED": REJECTED}[appr.status]
            question = appr.question_text or (run.question_text if run else None)
            if question and appr.kind == "reply":
                await s.execute(insert(LearnedExample).values(
                    id=str(uuid.uuid5(_NS, f"ex:{appr.id}")), tenant_id=env.tenant_id, task=task, topic=topic,
                    question_text=question, answer_text=appr.final_text if outcome != REJECTED else None,
                    reject_reason=appr.reject_reason, price_item_skus=list((run.retrieved or {}).get("skus") or []) if run else [],
                    source_approval_id=appr.id, outcome=appr.status, weight=2.0 if outcome == EDITED else 1.0,
                    active=outcome != REJECTED, use_count=0,
                ).on_conflict_do_nothing(index_elements=["id"]))
            row = (await s.execute(select(AutonomyStat).where(
                AutonomyStat.tenant_id == env.tenant_id, AutonomyStat.task == task, AutonomyStat.topic == topic).with_for_update()
            )).scalar_one_or_none()
            state = AutonomyState(list(row.recent or []), row.approved_unchanged, row.edited, row.rejected, row.auto_enabled) if row \
                else AutonomyState([], 0, 0, 0, False)
            new = record_outcome(state, outcome, cfg.learning)
            vals = dict(recent=new.recent, approved_unchanged=new.approved_unchanged, edited=new.edited, rejected=new.rejected,
                        auto_enabled=new.auto_enabled, updated_at=datetime.now(timezone.utc))
            await s.execute(insert(AutonomyStat).values(tenant_id=env.tenant_id, task=task, topic=topic, **vals)
                            .on_conflict_do_update(index_elements=["tenant_id", "task", "topic"], set_=vals))
            if new.auto_enabled != state.auto_enabled:
                log.info("autonomy changed", extra={"ctx": {"tenant_id": env.tenant_id, "task": task, "topic": topic,
                                                            "auto_enabled": new.auto_enabled}})

    # ------------------------------------------------------------ dispatch

    async def handle(self, env: Envelope, _record: Record) -> None:
        bundle = self.routing.bundles.get(env.tenant_id)
        if bundle is None:
            return
        t = env.event_type
        if t == Topics.INTENT_EXTRACTED:
            await self.on_extraction(env, bundle, flagged=False)
        elif t == Topics.REVIEW_FLAGGED:
            await self.on_extraction(env, bundle, flagged=True)
        elif t == Topics.VISIT_PLANNED:
            await self.on_visit_planned(env, bundle)
        elif t == Topics.FOLLOWUP_DUE:
            await self.on_followup_due(env, bundle)
        elif t == Topics.APPROVAL_DECIDED:
            await self.on_approval_decided(env, bundle)
        elif t == Topics.OUTBOUND_SENT:
            await self.on_outbound_sent(env, bundle)


async def main() -> None:
    settings = get_settings()
    bus = KafkaEventBus(settings.kafka_bootstrap_servers, "agent-worker")
    routing = TenantRouting(settings.tenant_routing_path)
    drafter = None
    if settings.gemini_api_key:
        from .drafter import GeminiDrafter

        drafter = GeminiDrafter(settings.gemini_api_key, settings.gemini_model, settings.gemini_timeout_seconds)
    else:
        log.warning("GEMINI_API_KEY not set: replies use deterministic grounded text only")
    rag = RagClient(settings.rag_url, settings.rag_service_token, settings.rag_timeout_seconds) if settings.rag_url else None
    worker = AgentWorker(make_sessionmaker(settings.database_url, pool_size=5), bus, routing, drafter, rag)
    await bus.start()
    install_shutdown(bus.request_stop, log)
    contract = ConsumerContract(bus, RedisIdempotencyStore(settings.redis_url), "agent-worker", worker.handle,
                                authorized_tenants=lambda tid: routing.tenant(tid) is not None)
    log.info("agent-worker consuming")
    try:
        await bus.consume([Topics.INTENT_EXTRACTED, Topics.REVIEW_FLAGGED, Topics.VISIT_PLANNED, Topics.FOLLOWUP_DUE,
                           Topics.APPROVAL_DECIDED, Topics.OUTBOUND_SENT], "agent-worker", contract)
    finally:
        await bus.stop()


if __name__ == "__main__":
    asyncio.run(main())
