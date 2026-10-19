"""EventBus abstraction with a Kafka (aiokafka) adapter and an in-memory fake for tests."""
from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer

from .tracing import outgoing_headers
from .events import Envelope
from .runtime import heartbeat


@dataclass
class Record:
    topic: str
    key: Optional[str]
    value: bytes
    headers: dict[str, str] = field(default_factory=dict)
    partition: Optional[int] = None
    offset: Optional[int] = None


RecordHandler = Callable[[Record], Awaitable[None]]


class EventBus(ABC):
    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    async def stop(self) -> None: ...

    @abstractmethod
    async def publish_raw(
        self, topic: str, value: bytes, key: Optional[str], headers: list[tuple[str, bytes]]
    ) -> None: ...

    @abstractmethod
    async def consume(
        self, topics: list[str], group_id: Optional[str], handler: RecordHandler, from_latest: bool = False
    ) -> None:
        """Runs until cancelled or `request_stop()`. Commits only after `handler` returns (at-least-once).
        `group_id=None` is a broadcast reader (no group, no commits) used for SSE fan-out."""

    async def ping(self, timeout: float = 3.0) -> bool:
        """Readiness: can we reach the broker right now?"""
        return True

    def request_stop(self) -> None:
        """Ask consume loops to return after the in-flight message (graceful shutdown)."""

    async def publish(self, topic: str, envelope: Envelope) -> None:
        await self.publish_raw(
            topic, envelope.model_dump_json().encode(), envelope.key,
            envelope.headers() + outgoing_headers(envelope.event_id),
        )


class KafkaEventBus(EventBus):
    def __init__(self, bootstrap_servers: str, client_id: str):
        self._bootstrap = bootstrap_servers
        self._client_id = client_id
        self._producer: Optional[AIOKafkaProducer] = None
        self._stopping = asyncio.Event()

    def request_stop(self) -> None:
        self._stopping.set()

    async def start(self) -> None:
        self._producer = AIOKafkaProducer(
            bootstrap_servers=self._bootstrap,
            client_id=self._client_id,
            acks="all",
            enable_idempotence=True,
            linger_ms=5,
        )
        for attempt in range(30):
            try:
                await self._producer.start()
                return
            except Exception:
                if attempt == 29:
                    raise
                await asyncio.sleep(2)

    async def stop(self) -> None:
        if self._producer:
            await self._producer.stop()

    async def ping(self, timeout: float = 3.0) -> bool:
        if not self._producer:
            return False
        try:
            md = await asyncio.wait_for(self._producer.client.fetch_all_metadata(), timeout)
            return bool(md.brokers())
        except Exception:  # noqa: BLE001
            return False

    async def publish_raw(self, topic, value, key, headers) -> None:
        assert self._producer, "bus not started"
        await self._producer.send_and_wait(
            topic, value=value, key=key.encode() if key else None, headers=headers
        )

    async def consume(self, topics, group_id, handler, from_latest=False) -> None:
        consumer = AIOKafkaConsumer(
            *topics,
            bootstrap_servers=self._bootstrap,
            client_id=f"{self._client_id}-{group_id or 'broadcast'}",
            group_id=group_id,
            enable_auto_commit=False,
            auto_offset_reset="latest" if from_latest else "earliest",
        )
        for attempt in range(30):
            try:
                await consumer.start()
                break
            except Exception:
                if attempt == 29:
                    raise
                await asyncio.sleep(2)
        heartbeat(force=True)
        try:
            # one record per poll keeps the commit-after-handle semantics of the old `async for`,
            # while the 1 s timeout lets the loop observe request_stop() and refresh the heartbeat
            while not self._stopping.is_set():
                batch = await consumer.getmany(timeout_ms=1000, max_records=1)
                heartbeat()
                for msgs in batch.values():
                    for msg in msgs:
                        record = Record(
                            topic=msg.topic,
                            key=msg.key.decode() if msg.key else None,
                            value=msg.value,
                            headers={k: v.decode() for k, v in (msg.headers or [])},
                            partition=getattr(msg, "partition", None),
                            offset=getattr(msg, "offset", None),
                        )
                        await handler(record)
                        if group_id:
                            await consumer.commit()
        finally:
            await consumer.stop()


class InMemoryEventBus(EventBus):
    """Synchronous fan-out for unit tests."""

    def __init__(self) -> None:
        self.published: list[Record] = []
        self._subs: list[tuple[set[str], RecordHandler]] = []

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def publish_raw(self, topic, value, key, headers) -> None:
        record = Record(topic, key, value, {k: v.decode() for k, v in headers})
        self.published.append(record)
        for topics, handler in list(self._subs):
            if topic in topics:
                await handler(record)

    def subscribe(self, topics: list[str], handler: RecordHandler) -> None:
        self._subs.append((set(topics), handler))

    async def consume(self, topics, group_id, handler, from_latest=False) -> None:
        self.subscribe(topics, handler)

    def on(self, topic: str) -> list[Envelope]:
        return [Envelope.model_validate_json(r.value) for r in self.published if r.topic == topic]
