"""AI Router: Tier 0 keywords -> Tier 2 Gemini. Thresholds/intents come from the tenant policy file."""
from __future__ import annotations

import asyncio
import re
import time
import uuid
from typing import Optional

from zuvo_common.bus import EventBus, KafkaEventBus, Record
from zuvo_common.contract import ConsumerContract
from zuvo_common.events import Envelope, Extraction, ExtractionPayload, Intent, Topics
from zuvo_common.idempotency import RedisIdempotencyStore
from zuvo_common.logging import get_logger
from zuvo_common.runtime import install_shutdown
from zuvo_common.settings import get_settings
from zuvo_common.tenancy import TenantPolicy, TenantPolicyLoader, TenantRouting

from .extractor import Extractor

log = get_logger("ai-router")
_NS = uuid.UUID("9d3c3b7a-1f0e-4a56-8c3e-5b2f6a7d8e90")
_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)


def _is_transient(exc: Exception) -> bool:
    """Timeouts, network errors, 429 and 5xx are retried; other 4xx (bad key, unknown model) are not."""
    code = getattr(exc, "code", None)
    if isinstance(code, int) and 400 <= code < 500:
        return code in (408, 429)
    return True


def _tier0(text: str, policy: TenantPolicy) -> bool:
    normalized = _PUNCT.sub("", text).strip().lower()
    return bool(normalized) and normalized in {k.lower() for k in policy.ai_routing.tier0_keywords}


class AiRouter:
    def __init__(
        self,
        bus: EventBus,
        routing: TenantRouting,
        policies: TenantPolicyLoader,
        extractor: Optional[Extractor],
        max_attempts: int = 3,
        backoff_seconds: float = 1.0,
    ):
        self.bus = bus
        self.max_attempts = max(1, max_attempts)
        self.backoff = backoff_seconds
        self.routing = routing
        self.policies = policies
        self.extractor = extractor

    async def _emit(self, env: Envelope, flagged: bool, payload: ExtractionPayload) -> None:
        topic = Topics.REVIEW_FLAGGED if flagged else Topics.INTENT_EXTRACTED
        await self.bus.publish(
            topic,
            Envelope(
                event_id=str(uuid.uuid5(_NS, f"{env.tenant_id}:{payload.message_id}")),
                event_type=topic,
                tenant_id=env.tenant_id,
                worker_id=env.worker_id,
                customer_id=env.customer_id,
                wamid=env.wamid,
                payload=payload.model_dump(mode="json"),
            ),
        )

    async def handle(self, env: Envelope, _record: Record) -> None:
        p = env.payload
        if p.get("change") != "created" or p.get("direction") != "INBOUND":
            return
        message_id = p["message_id"]
        text = (p.get("text") or "").strip()

        if p.get("message_type") != "text" or not text:
            # Voice/media go through the Normalizer (Phase 2b) before classification.
            await self._emit(env, True, ExtractionPayload(message_id=message_id, tier="none", reason="unsupported_media"))
            return

        tenant = self.routing.tenant(env.tenant_id)
        try:
            policy = self.policies.load(tenant.skill_file, env.tenant_id) if tenant else None
        except Exception as exc:  # noqa: BLE001
            log.error("policy load failed", extra={"ctx": {"tenant_id": env.tenant_id, "error": str(exc)[:200]}})
            policy = None
        if not policy:
            await self._emit(env, True, ExtractionPayload(message_id=message_id, tier="none", reason="ai_unavailable"))
            return

        if _tier0(text, policy):
            await self._emit(env, False, ExtractionPayload(
                message_id=message_id, tier="tier0", latency_ms=0,
                extraction=Extraction(intent=Intent.GENERAL_CHAT, confidence=1.0),
            ))
            return

        if not self.extractor:
            await self._emit(env, True, ExtractionPayload(message_id=message_id, tier="none", reason="ai_unavailable"))
            return

        start = time.perf_counter()
        result = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                result = await self.extractor.extract(text, policy)
                break
            except Exception as exc:  # noqa: BLE001 - keep the message, mark AI_PENDING (NFR-4)
                retry = attempt < self.max_attempts and _is_transient(exc)
                log.warning("gemini extraction failed", extra={"ctx": {
                    "operation": "gemini.extract", "outcome": "retry" if retry else "error",
                    "error_code": exc.__class__.__name__, "error": f"{exc.__class__.__name__}: {str(exc)[:200]}", "attempt": attempt, "will_retry": retry}})
                if not retry:
                    break
                await asyncio.sleep(self.backoff * (2 ** (attempt - 1)))
        if result is None:
            await self._emit(env, True, ExtractionPayload(
                message_id=message_id, tier="tier2", reason="ai_unavailable",
                latency_ms=int((time.perf_counter() - start) * 1000),
            ))
            return

        x = result.extraction
        reason = None
        if x.intent.value not in policy.intents.enabled:
            reason = "intent_disabled"
        elif x.confidence < policy.intents.confidence_threshold:
            reason = "low_confidence"
        await self._emit(env, reason is not None, ExtractionPayload(
            message_id=message_id, extraction=x, tier="tier2", model=result.model, reason=reason,
            latency_ms=result.latency_ms, input_tokens=result.input_tokens, output_tokens=result.output_tokens,
        ))
        log.info("extracted", extra={"ctx": {"tenant_id": env.tenant_id, "intent": x.intent.value, "confidence": x.confidence,
                                            "operation": "gemini.extract", "outcome": "flagged" if reason else "ok",
                                            "duration_ms": result.latency_ms, "model": result.model, "flagged": reason,
                                            "tokens_in": result.input_tokens, "tokens_out": result.output_tokens}})


async def main() -> None:
    settings = get_settings()
    bus = KafkaEventBus(settings.kafka_bootstrap_servers, "ai-router")
    routing = TenantRouting(settings.tenant_routing_path)
    policies = TenantPolicyLoader(settings.tenant_policy_root)
    extractor: Optional[Extractor] = None
    if settings.gemini_api_key:
        from .extractor import GeminiExtractor

        extractor = GeminiExtractor(settings.gemini_api_key, settings.gemini_model, settings.gemini_timeout_seconds)
        log.info(f"tier2 model: {settings.gemini_model}")
    else:
        log.warning("GEMINI_API_KEY not set: tier2 disabled, messages flagged AI_PENDING")
    router = AiRouter(bus, routing, policies, extractor, settings.gemini_max_attempts)
    await bus.start()
    install_shutdown(bus.request_stop, log)
    contract = ConsumerContract(
        bus, RedisIdempotencyStore(settings.redis_url), "ai-router", router.handle,
        authorized_tenants=lambda tid: routing.tenant(tid) is not None,
    )
    try:
        await bus.consume([Topics.MESSAGE_PERSISTED], "ai-router", contract)
    finally:
        await bus.stop()


if __name__ == "__main__":
    asyncio.run(main())
