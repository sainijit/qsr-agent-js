"""Optional Gemini rephrasing of a grounded proposal into the customer's language/tone.

The model only rewrites facts it is given; any number in the draft that is not present in those facts
(or the customer's message) rejects the draft and the deterministic text is used instead."""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Optional, Protocol, Sequence

from pydantic import BaseModel

from zuvo_common.logging import mask_text
from zuvo_common.pricing import allowed_numbers, unsupported_numbers


class DraftReply(BaseModel):
    reply: str


@dataclass
class DraftResult:
    text: str
    model: str
    latency_ms: int
    tokens_in: int = 0
    tokens_out: int = 0


class Drafter(Protocol):
    async def draft(self, *, persona: str, skill_md: str, customer_text: str, facts: Sequence[str],
                    fallback: str, examples: Sequence[tuple[str, str]], lang: Optional[str]) -> DraftResult: ...


def guard(draft: str, facts: Sequence[str], customer_text: str, price_items: Sequence[dict] = ()) -> list[str]:
    allowed = allowed_numbers(price_items, [*facts, customer_text])
    return unsupported_numbers(draft, allowed)


def build_prompt(persona: str, skill_md: str, examples: Sequence[tuple[str, str]], lang: Optional[str]) -> str:
    shots = "\n".join(f"Customer: {q}\nApproved reply: {a}" for q, a in examples[:3])
    return (
        f"You write WhatsApp replies for a business. Persona: {persona}.\n"
        f"Business instructions:\n{skill_md[:3000]}\n\n"
        "Rewrite the DRAFT into a short, warm WhatsApp reply for the customer's message.\n"
        f"Reply in the customer's language ({lang or 'same as the customer'}); Hinglish customers get Roman-script Hinglish.\n"
        "Use ONLY the FACTS. Never add prices, numbers, dates, discounts, stock or promises that are not in the FACTS.\n"
        "Keep bullet lists of prices as they are. Max 600 characters.\n"
        + (f"\nReplies the owner approved before (match their style):\n{shots}\n" if shots else "")
    )


class GeminiDrafter:
    def __init__(self, api_key: str, model: str, timeout_seconds: float = 20.0):
        from google import genai

        self._genai = genai
        self._client = genai.Client(api_key=api_key)
        self.model = model
        self.timeout = timeout_seconds

    async def draft(self, *, persona, skill_md, customer_text, facts, fallback, examples, lang) -> DraftResult:
        types = self._genai.types
        start = time.perf_counter()
        contents = (f"CUSTOMER MESSAGE:\n{mask_text(customer_text)}\n\nFACTS:\n" + "\n".join(f"- {f}" for f in facts)
                    + f"\n\nDRAFT:\n{fallback}")
        resp = await asyncio.wait_for(self._client.aio.models.generate_content(
            model=self.model, contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=build_prompt(persona, skill_md, examples, lang),
                response_mime_type="application/json", response_schema=DraftReply, temperature=0.3,
            ),
        ), timeout=self.timeout)
        parsed = resp.parsed if isinstance(resp.parsed, DraftReply) else DraftReply.model_validate_json(resp.text)
        usage = getattr(resp, "usage_metadata", None)
        return DraftResult(parsed.reply.strip()[:1000], self.model, int((time.perf_counter() - start) * 1000),
                           getattr(usage, "prompt_token_count", 0) or 0, getattr(usage, "candidates_token_count", 0) or 0)
