"""Per-tenant daily funnel / follow-up metrics. Used live by dashboard-api and rolled up into metrics_daily
by the followup-scheduler. Must run inside a tenant_session (RLS scopes every query)."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


def day_bounds(day: date, tz: str) -> tuple[datetime, datetime]:
    z = ZoneInfo(tz)
    start = datetime.combine(day, time(0, 0), z).astimezone(timezone.utc)
    return start, start + timedelta(days=1)


async def _one(s: AsyncSession, sql: str, **params) -> Any:
    return (await s.execute(text(sql), params)).scalar()


async def compute_day(s: AsyncSession, day: date, tz: str) -> dict[str, Any]:
    a, b = day_bounds(day, tz)
    p = {"a": a, "b": b, "d": day}
    stages = {r[0]: r[1] for r in (await s.execute(text(
        "SELECT stage, count(*) FROM leads WHERE closed_at IS NULL OR stage IN ('WON','LOST') GROUP BY stage"))).all()}
    sources = {r[0] or "unknown": r[1] for r in (await s.execute(text(
        "SELECT source, count(*) FROM leads WHERE created_at >= :a AND created_at < :b GROUP BY source"), p)).all()}
    frt = await _one(s, "SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM first_response_at - first_contact_at)) "
                        "FROM leads WHERE first_response_at IS NOT NULL AND first_contact_at >= :a AND first_contact_at < :b", **p)
    return {
        "leads_new": await _one(s, "SELECT count(*) FROM leads WHERE created_at >= :a AND created_at < :b", **p) or 0,
        "leads_new_by_source": sources,
        "leads_open_by_stage": stages,
        "leads_won": await _one(s, "SELECT count(*) FROM leads WHERE stage = 'WON' AND closed_at >= :a AND closed_at < :b", **p) or 0,
        "leads_lost": await _one(s, "SELECT count(*) FROM leads WHERE stage = 'LOST' AND closed_at >= :a AND closed_at < :b", **p) or 0,
        "first_response_median_min": round(float(frt) / 60, 1) if frt is not None else None,
        "visits_planned_for_day": await _one(s, "SELECT count(*) FROM visit_plans WHERE resolved_date = :d AND status IN ('PLANNED','CONFIRMED','VISITED','NO_SHOW')", **p) or 0,
        "visits_created": await _one(s, "SELECT count(*) FROM visit_plans WHERE created_at >= :a AND created_at < :b AND status <> 'RESCHEDULED'", **p) or 0,
        "visits_done": await _one(s, "SELECT count(*) FROM visit_plans WHERE status = 'VISITED' AND outcome_at >= :a AND outcome_at < :b", **p) or 0,
        "visits_no_show": await _one(s, "SELECT count(*) FROM visit_plans WHERE status = 'NO_SHOW' AND outcome_at >= :a AND outcome_at < :b", **p) or 0,
        "visits_next_7d": await _one(s, "SELECT count(*) FROM visit_plans WHERE status IN ('PLANNED','CONFIRMED') AND resolved_date >= :d AND resolved_date < :d + 7", **p) or 0,
        "followups_due": await _one(s, "SELECT count(*) FROM followup_tasks WHERE due_at >= :a AND due_at < :b AND status <> 'CANCELLED'", **p) or 0,
        "followups_sent": await _one(s, "SELECT count(*) FROM followup_tasks WHERE status = 'SENT' AND completed_at >= :a AND completed_at < :b", **p) or 0,
        "followups_overdue": await _one(s, "SELECT count(*) FROM followup_tasks WHERE status IN ('SCHEDULED','QUEUED','AWAITING_APPROVAL','NEEDS_ACTION') AND due_at < now()", **p) or 0,
        "followups_needs_action": await _one(s, "SELECT count(*) FROM followup_tasks WHERE status = 'NEEDS_ACTION'", **p) or 0,
        "agent_auto_sent": await _one(s, "SELECT count(*) FROM agent_runs WHERE decision = 'AUTO_SENT' AND created_at >= :a AND created_at < :b", **p) or 0,
        "agent_drafted": await _one(s, "SELECT count(*) FROM agent_runs WHERE decision = 'PENDING_APPROVAL' AND created_at >= :a AND created_at < :b", **p) or 0,
        "approvals_pending": await _one(s, "SELECT count(*) FROM approvals WHERE status = 'PENDING'", **p) or 0,
        "approvals_decided": await _one(s, "SELECT count(*) FROM approvals WHERE decided_at >= :a AND decided_at < :b AND status IN ('APPROVED','EDITED','REJECTED')", **p) or 0,
        "messages_in": await _one(s, "SELECT count(*) FROM messages WHERE direction = 'INBOUND' AND created_at >= :a AND created_at < :b", **p) or 0,
        "messages_out": await _one(s, "SELECT count(*) FROM messages WHERE direction = 'OUTBOUND' AND created_at >= :a AND created_at < :b", **p) or 0,
        "ai_cost_inr": float(await _one(s, "SELECT COALESCE(sum(cost_inr), 0) FROM ai_usage_daily WHERE date = :d", **p) or 0),
    }


async def upsert_day(s: AsyncSession, tenant_id: str, day: date, data: dict) -> None:
    import json

    await s.execute(text(
        "INSERT INTO metrics_daily (tenant_id, date, data, updated_at) VALUES (:t, :d, CAST(:j AS jsonb), now()) "
        "ON CONFLICT (tenant_id, date) DO UPDATE SET data = EXCLUDED.data, updated_at = now()"),
        {"t": tenant_id, "d": day, "j": json.dumps(data)})
