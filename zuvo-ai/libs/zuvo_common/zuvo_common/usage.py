"""AI cost accounting per tenant (ai_usage_daily) and the monthly budget guard."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Gemini Flash-Lite list price (USD per 1M tokens) converted at a fixed rate; override via env if needed.
USD_INR = 85.0
PRICE_PER_M = {"input": 0.10, "output": 0.40}


def cost_inr(tokens_in: int, tokens_out: int) -> Decimal:
    usd = tokens_in / 1e6 * PRICE_PER_M["input"] + tokens_out / 1e6 * PRICE_PER_M["output"]
    return Decimal(str(round(usd * USD_INR, 4)))


async def record_usage(session: AsyncSession, tenant_id: str, day: date, service: str, model: str, tokens_in: int, tokens_out: int) -> Decimal:
    c = cost_inr(tokens_in or 0, tokens_out or 0)
    await session.execute(
        text(
            "INSERT INTO ai_usage_daily (tenant_id, date, service, model, calls, tokens_in, tokens_out, cost_inr) "
            "VALUES (:t, :d, :s, :m, 1, :ti, :to, :c) "
            "ON CONFLICT (tenant_id, date, service, model) DO UPDATE SET calls = ai_usage_daily.calls + 1, "
            "tokens_in = ai_usage_daily.tokens_in + EXCLUDED.tokens_in, tokens_out = ai_usage_daily.tokens_out + EXCLUDED.tokens_out, "
            "cost_inr = ai_usage_daily.cost_inr + EXCLUDED.cost_inr"
        ),
        {"t": tenant_id, "d": day, "s": service, "m": model or "unknown", "ti": tokens_in or 0, "to": tokens_out or 0, "c": c},
    )
    return c


async def month_spend_inr(session: AsyncSession, day: date) -> float:
    v = await session.scalar(
        text("SELECT COALESCE(SUM(cost_inr), 0) FROM ai_usage_daily WHERE date >= :start AND date <= :d"),
        {"start": day.replace(day=1), "d": day},
    )
    return float(v or 0)
