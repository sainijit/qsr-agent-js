"""In-process SSE fan-out fed by a broadcast Kafka reader (messages, leads, visits, approvals)."""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field

from zuvo_common.bus import Record
from zuvo_common.events import Envelope, Headers, Topics
from zuvo_common.security import Principal


@dataclass(eq=False)
class Subscriber:
    principal: Principal
    queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=200))


class FeedBroker:
    def __init__(self) -> None:
        self._subs: set[Subscriber] = set()

    def subscribe(self, principal: Principal) -> Subscriber:
        sub = Subscriber(principal)
        self._subs.add(sub)
        return sub

    def unsubscribe(self, sub: Subscriber) -> None:
        self._subs.discard(sub)

    @property
    def size(self) -> int:
        return len(self._subs)

    async def on_record(self, record: Record) -> None:
        tenant_id = record.headers.get(Headers.TENANT_ID)
        if not tenant_id:
            return
        try:
            env = Envelope.model_validate_json(record.value)
        except Exception:
            return
        if env.tenant_id != tenant_id:
            return
        kind = {Topics.LEAD_UPDATED: "lead", Topics.VISIT_PLANNED: "visit", Topics.APPROVAL_REQUESTED: "approval",
                Topics.APPROVAL_DECIDED: "approval"}.get(record.topic, "message")
        if kind != "message":
            data = json.dumps({"change": env.payload.get("change") or record.topic, "lead_id": env.payload.get("lead_id"),
                               "approval_id": env.payload.get("approval_id"), "customer_id": env.customer_id,
                               "occurred_at": env.occurred_at.isoformat()})
            self._fan_out(tenant_id, env.worker_id, (kind, data), worker_scoped=False)
            return
        event = json.dumps(
            {
                "change": env.payload.get("change", "").split(":")[0],
                "message_id": env.payload.get("message_id"),
                "direction": env.payload.get("direction"),
                "ai_status": env.payload.get("ai_status"),
                "worker_id": env.worker_id,
                "customer_id": env.customer_id,
                "occurred_at": env.occurred_at.isoformat(),
            }
        )
        self._fan_out(tenant_id, env.worker_id, ("message", event), worker_scoped=True)

    def _fan_out(self, tenant_id: str, worker_id, item: tuple[str, str], worker_scoped: bool) -> None:
        for sub in list(self._subs):
            p = sub.principal
            if p.tenant_id != tenant_id:
                continue
            if worker_scoped and p.is_worker and worker_id != p.worker_id:
                continue
            try:
                sub.queue.put_nowait(item)
            except asyncio.QueueFull:
                pass  # slow client; it will catch up on the next refetch
