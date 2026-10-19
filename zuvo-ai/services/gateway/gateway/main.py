"""Ingestion Gateway: Meta webhook -> Kafka. Stateless; scale horizontally."""
from __future__ import annotations

import json
import time
import uuid
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

from zuvo_common.bus import EventBus, KafkaEventBus
from zuvo_common.httpobs import install_request_observability
from zuvo_common.idempotency import IdempotencyStore, RedisIdempotencyStore
from zuvo_common.logging import get_logger, install_access_log_redaction
from zuvo_common.security import Principal, verify_meta_signature
from zuvo_common.settings import Settings, get_settings
from zuvo_common.tenancy import TenantPolicyLoader, TenantRouting
from zuvo_common.web import current_principal

from .ingest import Ingestor, PublishError

log = get_logger("gateway")


class SimulateInbound(BaseModel):
    phone_number_id: str
    sender_phone: str
    sender_name: Optional[str] = None
    message_text: str = Field(min_length=1, max_length=4096)


def create_app(
    settings: Optional[Settings] = None,
    bus: Optional[EventBus] = None,
    idempotency: Optional[IdempotencyStore] = None,
) -> FastAPI:
    settings = settings or get_settings()
    install_access_log_redaction()
    bus = bus or KafkaEventBus(settings.kafka_bootstrap_servers, "gateway")
    idempotency = idempotency or RedisIdempotencyStore(settings.redis_url)
    routing = TenantRouting(settings.tenant_routing_path)
    policies = TenantPolicyLoader(settings.tenant_policy_root)
    ingestor = Ingestor(routing, policies, bus, idempotency, settings.idempotency_ttl_seconds)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await bus.start()
        yield
        await bus.stop()
        await idempotency.close()

    app = FastAPI(title="ZuvoCloud Ingestion Gateway", lifespan=lifespan)
    install_request_observability(app, "gateway")

    @app.get("/api/v1/whatsapp/webhook")
    async def verify(
        hub_mode: Optional[str] = Query(None, alias="hub.mode"),
        hub_verify_token: Optional[str] = Query(None, alias="hub.verify_token"),
        hub_challenge: Optional[str] = Query(None, alias="hub.challenge"),
    ):
        if (
            hub_mode == "subscribe"
            and settings.meta_verify_token
            and hub_verify_token == settings.meta_verify_token
            and hub_challenge
        ):
            return PlainTextResponse(hub_challenge)
        log.warning("webhook verification failed")
        raise HTTPException(status.HTTP_403_FORBIDDEN, "verification failed")

    @app.post("/api/v1/whatsapp/webhook")
    async def receive(request: Request):
        raw = await request.body()
        if settings.require_hmac_verification and not verify_meta_signature(
            settings.meta_app_secret, raw, request.headers.get("x-hub-signature-256")
        ):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid signature")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid json")
        start = time.perf_counter()
        try:
            stats = await ingestor.ingest(payload)
        except PublishError:
            log.exception("publish failed; asking Meta to retry")
            return JSONResponse({"status": "retry"}, status_code=status.HTTP_503_SERVICE_UNAVAILABLE)
        log.info("webhook processed", extra={"ctx": {**stats, "ms": round((time.perf_counter() - start) * 1000, 1)}})
        return {"status": "ok", **stats}

    if settings.enable_simulator:

        @app.post("/api/v1/messages/simulate-inbound")
        async def simulate(body: SimulateInbound, principal: Principal = Depends(current_principal)):
            line = routing.resolve(body.phone_number_id)
            if not line:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown or inactive line")
            if line.tenant_id != principal.tenant_id:
                raise HTTPException(status.HTTP_403_FORBIDDEN, "cross-tenant access denied")
            wamid = f"wamid.SIM.{uuid.uuid4().hex}"
            payload = {
                "object": "whatsapp_business_account",
                "entry": [{
                    "id": "simulator",
                    "changes": [{
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {"phone_number_id": line.meta_phone_number_id, "display_phone_number": line.whatsapp_number},
                            "contacts": [{"wa_id": body.sender_phone, "profile": {"name": body.sender_name}}],
                            "messages": [{
                                "from": body.sender_phone,
                                "id": wamid,
                                "timestamp": str(int(time.time())),
                                "type": "text",
                                "text": {"body": body.message_text},
                            }],
                        },
                    }],
                }],
            }
            try:
                stats = await ingestor.ingest(payload)
            except PublishError:
                raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "event bus unavailable")
            if not stats.get("published"):
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "message rejected")
            return {"status": "accepted", "wamid": wamid}

    @app.get("/api/v1/health")
    async def health():
        redis_ok = await idempotency.ping()
        return {"service": "gateway", "status": "ok" if redis_ok else "degraded", "components": {"redis": "ok" if redis_ok else "down"}}

    @app.get("/livez")
    async def livez():
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz():
        # webhooks are only acknowledged once published, so Kafka is a hard dependency for ingest
        comps = {"redis": await idempotency.ping(), "kafka": await bus.ping()}
        body = {"status": "ready" if all(comps.values()) else "not_ready",
                "components": {k: "ok" if v else "down" for k, v in comps.items()}}
        return JSONResponse(body, status_code=200 if all(comps.values()) else 503)

    return app
