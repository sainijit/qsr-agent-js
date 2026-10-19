"""Dead-letter inspection and controlled replay (Phase 6).

Run inside any container on the Kafka network, e.g.:

    docker compose exec -T dashboard-api python -m zuvo_common.dlq summary
    docker compose exec -T dashboard-api python -m zuvo_common.dlq show --topic ai.intent.extracted.DLQ
    docker compose exec -T dashboard-api python -m zuvo_common.dlq replay --topic ai.intent.extracted.DLQ \\
        --group agent-worker [--event-id ID] [--tenant UUID] [--error-class X] [--max 50]          # dry run
    ... replay ... --execute                                                                        # really republish

Output is metadata only (ids, error class, scrubbed error); payloads may contain customer data and are never printed.

Replay republishes the original bytes to the original topic, with the original headers and these additions:
* `replay_for_group=<dlq_group>`: other consumer groups of that topic ignore the copy;
* `replayed_from=<dlq topic>:<partition>:<offset>`.
The event keeps its `event_id`. Replaying the same record twice is safe: after the first successful run the
target group has it in Redis (24 h) and, for ledger-backed handlers, in `processed_events`.
Nothing is deleted from the DLQ topic; Kafka retention (168 h) expires it.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections import Counter
from typing import AsyncIterator, Optional

from aiokafka import AIOKafkaConsumer, TopicPartition
from aiokafka.admin import AIOKafkaAdminClient

from .bus import KafkaEventBus
from .events import Headers
from .logging import scrub


async def _partitions(bootstrap: str, topics: list[str]) -> list[TopicPartition]:
    admin = AIOKafkaAdminClient(bootstrap_servers=bootstrap, client_id="zuvo-dlq-admin")
    await admin.start()
    try:
        described = await admin.describe_topics(topics)
    finally:
        await admin.close()
    return [TopicPartition(d["topic"], p["partition"]) for d in described for p in d.get("partitions", [])]


async def dlq_topics(bootstrap: str) -> list[str]:
    admin = AIOKafkaAdminClient(bootstrap_servers=bootstrap, client_id="zuvo-dlq-admin")
    await admin.start()
    try:
        return sorted(t for t in await admin.list_topics() if t.endswith(".DLQ"))
    finally:
        await admin.close()


async def read_all(bootstrap: str, topics: list[str]) -> AsyncIterator:
    """Every record currently in `topics`, without a consumer group (no offsets are committed)."""
    tps = await _partitions(bootstrap, topics)
    if not tps:
        return
    consumer = AIOKafkaConsumer(bootstrap_servers=bootstrap, group_id=None, enable_auto_commit=False,
                                client_id="zuvo-dlq-reader")
    await consumer.start()
    try:
        consumer.assign(tps)
        end = await consumer.end_offsets(tps)
        await consumer.seek_to_beginning(*tps)
        remaining = {tp for tp in tps if end[tp] > await consumer.position(tp)}
        while remaining:
            batch = await consumer.getmany(*remaining, timeout_ms=2000, max_records=500)
            if not batch:
                break
            for tp, msgs in batch.items():
                for m in msgs:
                    if m.offset < end[tp]:
                        yield m
                if await consumer.position(tp) >= end[tp]:
                    remaining.discard(tp)
    finally:
        await consumer.stop()


def meta(m) -> dict:
    h = {k: v.decode(errors="replace") for k, v in (m.headers or [])}
    return {
        "dlq_topic": m.topic, "partition": m.partition, "offset": m.offset,
        "original_topic": h.get(Headers.ORIGINAL_TOPIC), "group": h.get(Headers.DLQ_GROUP),
        "event_id": h.get(Headers.EVENT_ID), "event_type": h.get(Headers.EVENT_TYPE), "tenant_id": h.get(Headers.TENANT_ID),
        "error_class": h.get(Headers.DLQ_ERROR_CLASS), "attempts": h.get(Headers.DLQ_ATTEMPTS),
        "failed_at": h.get(Headers.DLQ_FAILED_AT), "error": scrub(h.get(Headers.ERROR) or "")[:300],
        "correlation_id": h.get("correlation_id"),
    }


def matches(md: dict, a) -> bool:
    return all([
        not a.group or md["group"] == a.group,
        not a.event_id or md["event_id"] == a.event_id,
        not a.tenant or md["tenant_id"] == a.tenant,
        not a.error_class or md["error_class"] == a.error_class,
    ])


def replay_headers(m) -> tuple[str, list[tuple[str, bytes]]]:
    h = [(k, v) for k, v in (m.headers or [])]
    d = {k: v.decode(errors="replace") for k, v in h}
    original, group = d.get(Headers.ORIGINAL_TOPIC), d.get(Headers.DLQ_GROUP)
    if not original or not group:
        raise ValueError("record lacks original_topic/dlq_group metadata (written before Phase 6?) - replay manually")
    out = [(k, v) for k, v in h if k not in Headers.DLQ_HEADERS and k not in (Headers.REPLAY_FOR_GROUP, Headers.REPLAYED_FROM)]
    out += [(Headers.REPLAY_FOR_GROUP, group.encode()), (Headers.REPLAYED_FROM, f"{m.topic}:{m.partition}:{m.offset}".encode())]
    return original, out


async def cmd_summary(bootstrap: str) -> int:
    topics = await dlq_topics(bootstrap)
    counts: Counter = Counter()
    async for m in read_all(bootstrap, topics):
        md = meta(m)
        counts[(md["dlq_topic"], md["group"] or "?", md["error_class"] or "?")] += 1
    if not counts:
        print("all DLQ topics empty")
    for (t, g, e), n in sorted(counts.items()):
        print(f"{n:6d}  {t}  group={g}  error_class={e}")
    return 0


async def cmd_show(bootstrap: str, a) -> int:
    n = 0
    async for m in read_all(bootstrap, [a.topic]):
        md = meta(m)
        if matches(md, a):
            print(json.dumps(md))
            n += 1
            if n >= a.max:
                break
    return 0


async def cmd_replay(bootstrap: str, a) -> int:
    todo = []
    async for m in read_all(bootstrap, [a.topic]):
        md = meta(m)
        if matches(md, a):
            todo.append((m, md))
            if len(todo) >= a.max:
                break
    print(f"{len(todo)} record(s) match{' (dry run; add --execute to republish)' if not a.execute else ''}")
    for _, md in todo:
        print(json.dumps({k: md[k] for k in ("partition", "offset", "original_topic", "group", "event_id", "tenant_id", "error_class")}))
    if not a.execute or not todo:
        return 0
    bus = KafkaEventBus(bootstrap, "zuvo-dlq-replay")
    await bus.start()
    failed = 0
    try:
        for m, md in todo:
            try:
                topic, headers = replay_headers(m)
                await bus.publish_raw(topic, m.value, m.key.decode() if m.key else None, headers)
                print(f"replayed {md['event_id']} -> {topic} for group {md['group']}")
            except Exception as exc:  # noqa: BLE001
                failed += 1
                print(f"FAILED {md['event_id']}: {exc.__class__.__name__}: {exc}", file=sys.stderr)
    finally:
        await bus.stop()
    return 1 if failed else 0


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m zuvo_common.dlq", description=__doc__.split("\n\n")[0])
    ap.add_argument("--bootstrap", default=os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "kafka:9092"))
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("summary", help="count DLQ records by topic / group / error class")
    for name in ("show", "replay"):
        p = sub.add_parser(name)
        p.add_argument("--topic", required=True, help="DLQ topic, e.g. ai.intent.extracted.DLQ")
        p.add_argument("--group", help="only records that failed in this consumer group")
        p.add_argument("--event-id")
        p.add_argument("--tenant")
        p.add_argument("--error-class")
        p.add_argument("--max", type=int, default=50)
        if name == "replay":
            p.add_argument("--execute", action="store_true", help="actually republish (default: dry run)")
    a = ap.parse_args(argv)
    if a.cmd != "summary" and not a.topic.endswith(".DLQ"):
        ap.error("--topic must be a .DLQ topic")
    if a.cmd == "summary":
        return asyncio.run(cmd_summary(a.bootstrap))
    if a.cmd == "show":
        return asyncio.run(cmd_show(a.bootstrap, a))
    return asyncio.run(cmd_replay(a.bootstrap, a))


if __name__ == "__main__":
    sys.exit(main())
