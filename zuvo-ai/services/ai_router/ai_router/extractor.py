"""Gemini structured-output extractor (Tier 2). Model is config-driven (ZUVO_GEMINI_MODEL)."""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Protocol
from zoneinfo import ZoneInfo

from zuvo_common.events import Extraction
from zuvo_common.logging import mask_text
from zuvo_common.tenancy import TenantPolicy


@dataclass
class ExtractionResult:
    extraction: Extraction
    model: str
    latency_ms: int
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None


class Extractor(Protocol):
    async def extract(self, text: str, policy: TenantPolicy) -> ExtractionResult: ...


def _today(policy: TenantPolicy) -> str:
    try:
        tz = ZoneInfo(policy.daily_pulse.timezone)
    except Exception:  # noqa: BLE001
        tz = timezone.utc
    now = datetime.now(tz)
    return f"{now.date().isoformat()} ({now.strftime('%A')})"


def build_system_prompt(policy: TenantPolicy) -> str:
    business = policy.business_name or "the business"
    kind = (policy.industry or "retail").replace("_", " ")
    interests = f"Typical customer interests: {', '.join(policy.interests)}.\n" if policy.interests else ""
    return (
        f"You classify WhatsApp messages sent by customers to {business} ({kind}). Persona: {policy.persona}.\n"
        f"Today is {_today(policy)} in the business's timezone.\n"
        f"Customers write in {', '.join(policy.language)} (often Hinglish, Roman-script Hindi).\n"
        f"Classify the message into exactly one intent from: {', '.join(policy.intents.enabled)}.\n"
        "Intent guide: INQUIRY_PRICE_STOCK = asks price/rate/cost/availability; VISIT_PLAN = says when they will come to "
        "the showroom/shop or asks to book a visit/test drive; FAQ = asks about the business (timings, address, delivery, "
        "warranty, finance, process); CREATE_ORDER = places an order with quantities; GENERAL_CHAT = greetings and small talk.\n"
        + interests +
        "Extract only what is literally present in the message (null when absent):\n"
        "- lead: interest (product/category they want), budget_min/budget_max (rupees as numbers, '8 lakh' = 800000), "
        "timeline (e.g. 'this month'), location (their city/area).\n"
        "- visit: when the customer says they will visit. text = the customer's words about time; date_expr = the time "
        "phrase as written (e.g. 'shanivar ko', 'kal shaam'); resolved_date = ISO date computed from today, or null if "
        "unclear; time_window = morning/afternoon/evening or HH:MM; is_reschedule / is_cancel when they move or cancel a "
        "planned visit; confidence 0..1.\n"
        "- price_query: item_text = the product they ask the price of, attributes = variant words (size, colour, fuel, "
        "model), is_direct = true if they ask the price explicitly, discount_requested = true if they ask for discount/best price.\n"
        "- question: the customer's question rephrased in short English (for FAQ lookup).\n"
        "- opt_out: true only if they ask to stop messages (stop, unsubscribe, mat bhejo).\n"
        "- line_items, customer_account, urgency, payment_reference, requested_discount_pct: for orders/payments.\n"
        "- lang: one of hinglish, hindi, english.\n"
        "Never invent prices, stock or dates. "
        "confidence is your probability (0..1) that the intent AND entities are correct; lower it for ambiguous messages."
    )


class GeminiExtractor:
    def __init__(self, api_key: str, model: str, timeout_seconds: float = 20.0):
        from google import genai  # imported lazily so tests do not need the SDK

        self._genai = genai
        self._client = genai.Client(api_key=api_key)
        self.model = model
        self.timeout = timeout_seconds

    async def extract(self, text: str, policy: TenantPolicy) -> ExtractionResult:
        types = self._genai.types
        start = time.perf_counter()
        response = await asyncio.wait_for(
            self._client.aio.models.generate_content(
                model=self.model,
                contents=mask_text(text),
                config=types.GenerateContentConfig(
                    system_instruction=build_system_prompt(policy),
                    response_mime_type="application/json",
                    response_schema=Extraction,
                    temperature=0,
                ),
            ),
            timeout=self.timeout,
        )
        parsed = response.parsed
        extraction = parsed if isinstance(parsed, Extraction) else Extraction.model_validate_json(response.text)
        usage = getattr(response, "usage_metadata", None)
        return ExtractionResult(
            extraction=extraction,
            model=self.model,
            latency_ms=int((time.perf_counter() - start) * 1000),
            input_tokens=getattr(usage, "prompt_token_count", None),
            output_tokens=getattr(usage, "candidates_token_count", None),
        )
