"""Owner Command Center API: JWT auth, tenant-scoped reads (RLS), SSE, outbound requests."""
from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field

from zuvo_common.audit import write_audit
from zuvo_common.bus import EventBus, KafkaEventBus
from zuvo_common import opsmetrics
from zuvo_common.httpobs import install_request_observability
from zuvo_common.db import db_ping, make_sessionmaker, tenant_session
from zuvo_common.events import Topics
from zuvo_common.outbound import outbound_envelope
from zuvo_common.outbox import Outbox
from zuvo_common.logging import get_logger, install_access_log_redaction
from zuvo_common.phone import normalize_phone
from zuvo_common.security import Principal, issue_token
from zuvo_common.settings import Settings, get_settings
from zuvo_common.tenancy import TenantRouting, worker_uuid
from zuvo_common.web import current_principal, enforce_tenant

from . import queries
from .broker import FeedBroker
from .phase1 import build_router

log = get_logger("dashboard-api")


class DevLogin(BaseModel):
    tenant_id: str
    worker_key: str


class SendMessage(BaseModel):
    recipient_phone: str
    message_text: str = Field(min_length=1, max_length=4096)
    sender_number_id: Optional[str] = None


def create_app(settings: Optional[Settings] = None, bus: Optional[EventBus] = None, sessionmaker=None) -> FastAPI:
    settings = settings or get_settings()
    install_access_log_redaction()
    bus = bus or KafkaEventBus(settings.kafka_bootstrap_servers, "dashboard-api")
    sm = sessionmaker or make_sessionmaker(settings.database_url)
    outbox = Outbox(sm, bus, flush_timeout_s=2.0)
    routing = TenantRouting(settings.tenant_routing_path)
    broker = FeedBroker()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await bus.start()
        reader = asyncio.create_task(bus.consume([Topics.MESSAGE_PERSISTED, Topics.LEAD_UPDATED, Topics.VISIT_PLANNED, Topics.APPROVAL_REQUESTED,
                                                   Topics.APPROVAL_DECIDED], None, broker.on_record, from_latest=True))
        yield
        reader.cancel()
        await bus.stop()

    app = FastAPI(title="ZuvoCloud Dashboard API", lifespan=lifespan)
    install_request_observability(app, "dashboard-api")
    app.add_middleware(
        CORSMiddleware, allow_origins=settings.origins, allow_methods=["GET", "POST", "PATCH"], allow_headers=["Authorization", "Content-Type"]
    )

    # ---------------------------------------------------------------- auth
    if settings.auth_dev_login:

        @app.get("/api/v1/auth/dev-identities")
        async def dev_identities():
            out = []
            for t in routing.config.tenants:
                seen = set()
                for n in t.numbers:
                    if n.worker_id in seen:
                        continue
                    seen.add(n.worker_id)
                    out.append({
                        "tenant_id": str(t.tenant_id), "tenant_name": t.name, "worker_key": n.worker_id,
                        "worker_name": n.worker_name or n.worker_id, "role": n.role.value,
                    })
            return out

        @app.post("/api/v1/auth/dev-login")
        async def dev_login(body: DevLogin):
            tenant = routing.tenant(body.tenant_id)
            entry = next((n for n in (tenant.numbers if tenant else []) if n.worker_id == body.worker_key), None)
            if not entry:
                raise HTTPException(status.HTTP_401_UNAUTHORIZED, "unknown identity")
            principal = Principal(
                subject=f"{body.tenant_id}:{body.worker_key}", tenant_id=body.tenant_id, role=entry.role,
                worker_id=worker_uuid(body.tenant_id, body.worker_key),
            )
            token = issue_token(settings.jwt_secret, principal, settings.jwt_ttl_seconds)
            return {"access_token": token, "token_type": "bearer", "tenant_id": principal.tenant_id, "role": principal.role.value, "worker_id": principal.worker_id}

    @app.get("/api/v1/auth/me")
    async def me(principal: Principal = Depends(current_principal)):
        return {"tenant_id": principal.tenant_id, "role": principal.role.value, "worker_id": principal.worker_id, "subject": principal.subject}

    # ----------------------------------------------------------- dashboard
    @app.get("/api/v1/dashboard/tenants")
    async def tenants(principal: Principal = Depends(current_principal)):
        async with tenant_session(sm, principal.tenant_id) as s:
            info = await queries.tenant_info(s, principal.tenant_id)
        return [info] if info else []

    @app.get("/api/v1/dashboard/customers")
    async def list_customers(tenant_id: Optional[str] = None, principal: Principal = Depends(current_principal)):
        tid = enforce_tenant(principal, tenant_id)
        async with tenant_session(sm, tid) as s:
            return await queries.customers(s, principal)

    @app.get("/api/v1/dashboard/feed")
    async def feed(
        tenant_id: Optional[str] = None,
        worker_id: Optional[str] = None,
        customer_id: Optional[str] = None,
        customer_phone: Optional[str] = None,
        limit: int = Query(50, ge=1, le=500),
        offset: int = Query(0, ge=0),
        principal: Principal = Depends(current_principal),
    ):
        tid = enforce_tenant(principal, tenant_id)
        if principal.is_worker and worker_id and worker_id != principal.worker_id:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "workers may only view their own line")
        for value in (worker_id, customer_id):
            if value:
                try:
                    uuid.UUID(value)
                except ValueError:
                    raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid id")
        phone = None
        if customer_phone:
            try:
                phone = normalize_phone(customer_phone)
            except ValueError:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid phone")
        async with tenant_session(sm, tid) as s:
            return await queries.feed(s, principal, worker_id, customer_id, phone, limit, offset)

    @app.get("/api/v1/dashboard/stream")
    async def stream(request: Request, principal: Principal = Depends(current_principal)):
        sub = broker.subscribe(principal)

        async def events():
            try:
                yield "retry: 3000\n\n"
                while not await request.is_disconnected():
                    try:
                        kind, data = await asyncio.wait_for(sub.queue.get(), timeout=15)
                        yield f"event: {kind}\ndata: {data}\n\n"
                    except asyncio.TimeoutError:
                        yield ": keep-alive\n\n"
            finally:
                broker.unsubscribe(sub)

        return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    app.include_router(build_router(settings, sm, bus, routing))

    # ------------------------------------------------------------ outbound
    @app.post("/api/v1/messages/send", status_code=status.HTTP_202_ACCEPTED)
    async def send(body: SendMessage, principal: Principal = Depends(current_principal)):
        try:
            recipient = normalize_phone(body.recipient_phone)
        except ValueError:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid recipient phone")
        lines = routing.lines_for_tenant(principal.tenant_id)
        if body.sender_number_id:
            line = next((l for l in lines if body.sender_number_id in (l.whatsapp_number_id, l.meta_phone_number_id)), None)
            if not line:
                raise HTTPException(status.HTTP_403_FORBIDDEN, "sender line does not belong to tenant")
        else:
            line = next((l for l in lines if l.worker_id == principal.worker_id), None) or (lines[0] if lines else None)
        if not line:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "tenant has no active line")
        if principal.is_worker and line.worker_id != principal.worker_id:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "workers may only send from their own line")

        env = outbound_envelope(principal.tenant_id, line, recipient, body.message_text, requested_by=principal.subject, sent_by="human")
        request_id = env.payload["request_id"]
        async with outbox.session(principal.tenant_id) as s:
            await write_audit(
                s, principal.tenant_id, "OUTBOUND_REQUESTED", principal.subject, "outbound_request", request_id,
                {"recipient_phone": recipient, "text": body.message_text, "line": line.worker_key},
            )
            await outbox.add(s, Topics.OUTBOUND_REQUESTED, env)
        return {"status": "queued", "request_id": request_id}

    @app.get("/internal/metrics", include_in_schema=False)
    async def internal_metrics():
        # not routed by nginx/Caddy: reachable only on the compose network (deploy/ops/zuvo-watch.sh)
        metrics = await opsmetrics.collect(sm, [str(t.tenant_id) for t in routing.config.tenants],
                                           settings.kafka_bootstrap_servers)
        return PlainTextResponse(opsmetrics.render_prometheus(metrics), media_type="text/plain; version=0.0.4")

    @app.get("/livez")
    async def livez():
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz():
        comps = {"database": await db_ping(sm), "kafka": await bus.ping()}
        body = {"status": "ready" if all(comps.values()) else "not_ready",
                "components": {k: "ok" if v else "down" for k, v in comps.items()}}
        return JSONResponse(body, status_code=200 if all(comps.values()) else 503)

    @app.get("/api/v1/health")
    async def health():
        db_ok = await db_ping(sm)
        return {
            "status": "ok" if db_ok else "degraded",
            "app_name": "ZuvoCloud Dashboard API",
            "version": "2.0.0",
            "environment": settings.app_env,
            "components": {"database": "ok" if db_ok else "down", "redis_cache": "n/a", "meta_cloud_api": "via dispatch", "sse_clients": broker.size},
        }

    return app
