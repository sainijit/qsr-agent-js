"""Operational state metrics for a single-VM pilot (no Prometheus server required).

Computed on demand from PostgreSQL and Kafka, aggregated across tenants, and labelled only with
low-cardinality values (consumer group, topic, status). Tenant/conversation/phone ids never appear as labels.

    python -m zuvo_common.opsmetrics            # JSON (used by deploy/ops/zuvo-watch.sh)
    python -m zuvo_common.opsmetrics --prom     # Prometheus text exposition
    GET http://dashboard-api:8001/internal/metrics   (internal network only; not routed by nginx/Caddy)

Each section is collected independently; a failing dependency yields `zuvo_collect_ok{section="..."} 0`
instead of an exception, so the collector itself is a reliable health probe.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Iterable, Optional

from sqlalchemy import text

from .db import tenant_session
from .logging import get_logger

log = get_logger("opsmetrics")

STALE_FOLLOWUP_MINUTES = 10


@dataclass
class Metric:
    name: str
    value: float
    labels: dict = field(default_factory=dict)
    help: str = ""


class _Sink:
    def __init__(self) -> None:
        self.items: list[Metric] = []

    def add(self, name: str, value, help: str = "", **labels) -> None:
        self.items.append(Metric(name, float(value or 0), {k: str(v) for k, v in labels.items()}, help))


# ---------------------------------------------------------------- PostgreSQL (per tenant, summed)
_TENANT_SQL = {
    "approvals": """SELECT count(*) FILTER (WHERE status = 'PENDING'),
                           COALESCE(EXTRACT(EPOCH FROM now() - min(created_at) FILTER (WHERE status = 'PENDING')), 0),
                           count(*) FILTER (WHERE status = 'PENDING' AND sla_due_at < now())
                    FROM approvals""",
    "messages": """SELECT direction, COALESCE(delivery_status, status, 'UNKNOWN'), count(*)
                   FROM messages WHERE created_at > now() - interval '24 hours' GROUP BY 1, 2""",
    "ai_status": """SELECT COALESCE(ai_status, 'NONE'), count(*) FROM messages
                    WHERE direction = 'INBOUND' AND created_at > now() - interval '24 hours' GROUP BY 1""",
    "agent": """SELECT decision, count(*), COALESCE(percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms), 0)
                FROM agent_runs WHERE created_at > now() - interval '24 hours' GROUP BY 1""",
    "ai_today": """SELECT COALESCE(sum(calls), 0), COALESCE(sum(tokens_in), 0), COALESCE(sum(tokens_out), 0),
                          COALESCE(sum(cost_inr), 0) FROM ai_usage_daily WHERE date = (now() AT TIME ZONE 'UTC')::date""",
    "ai_month": """SELECT COALESCE(sum(cost_inr), 0) FROM ai_usage_daily
                   WHERE date >= date_trunc('month', now() AT TIME ZONE 'UTC')::date""",
    "rag": """SELECT count(*) FILTER (WHERE index_state = 'indexed' AND indexed_checksum = checksum),
                     count(*) FILTER (WHERE index_state = 'pending' OR (index_state = 'indexed' AND indexed_checksum IS DISTINCT FROM checksum)),
                     count(*) FILTER (WHERE index_state = 'processing'),
                     count(*) FILTER (WHERE index_state = 'failed'),
                     COALESCE(EXTRACT(EPOCH FROM now() - min(updated_at) FILTER (WHERE indexed_checksum IS DISTINCT FROM checksum)), 0),
                     count(*) FILTER (WHERE index_state = 'processing' AND index_started_at < now() - interval '10 minutes'),
                     COALESCE(max(index_attempts) FILTER (WHERE index_state = 'failed'), 0)
              FROM kb_documents WHERE is_active""",
    "agent_outcome": """SELECT COALESCE(outcome, 'unrecorded'), count(*) FROM agent_runs
                        WHERE created_at > now() - interval '24 hours' GROUP BY 1""",
    "followups": f"""SELECT count(*) FILTER (WHERE status = 'SCHEDULED' AND due_at < now() - interval '{STALE_FOLLOWUP_MINUTES} minutes'),
                            count(*) FILTER (WHERE status = 'QUEUED' AND due_at < now() - interval '{STALE_FOLLOWUP_MINUTES} minutes')
                     FROM followup_tasks""",
    "outbox": """SELECT count(*) FILTER (WHERE published_at IS NULL),
                         count(*) FILTER (WHERE published_at IS NULL AND attempts > 0),
                         COALESCE(EXTRACT(EPOCH FROM now() - min(created_at) FILTER (WHERE published_at IS NULL)), 0),
                         COALESCE(max(attempts) FILTER (WHERE published_at IS NULL), 0)
                  FROM outbox_events""",
    "processed": "SELECT count(*) FROM processed_events",
    "outbound": """SELECT count(*) FILTER (WHERE status = 'UNKNOWN'),
                          COALESCE(EXTRACT(EPOCH FROM now() - min(updated_at) FILTER (WHERE status = 'UNKNOWN')), 0),
                          count(*) FILTER (WHERE status = 'PENDING' AND attempts > 0),
                          count(*) FILTER (WHERE status = 'SENDING' AND sending_at < now() - interval '5 minutes'),
                          count(*) FILTER (WHERE status = 'FAILED' AND failed_at > now() - interval '24 hours')
                   FROM outbound_messages""",
}


async def collect_db(sm, tenant_ids: Iterable[str], sink: _Sink) -> None:
    acc: dict = {"appr": [0, 0.0, 0], "msg": {}, "ai_status": {}, "agent": {}, "agent_p95": 0.0,
                 "ai": [0, 0, 0, 0.0], "ai_month": 0.0, "rag": [0, 0, 0, 0, 0.0, 0, 0], "outcome": {}, "fu": [0, 0],
                 "outbox": [0, 0, 0.0, 0], "processed": 0, "outbound": [0, 0.0, 0, 0, 0]}
    tenants = list(tenant_ids)
    for tid in tenants:
        async with tenant_session(sm, tid) as s:
            q = lambda k: s.execute(text(_TENANT_SQL[k]))  # noqa: E731
            n, oldest, overdue = (await q("approvals")).one()
            acc["appr"][0] += n; acc["appr"][1] = max(acc["appr"][1], float(oldest)); acc["appr"][2] += overdue
            for d, st, c in (await q("messages")).all():
                acc["msg"][(d, st)] = acc["msg"].get((d, st), 0) + c
            for st, c in (await q("ai_status")).all():
                acc["ai_status"][st] = acc["ai_status"].get(st, 0) + c
            for dec, c, p95 in (await q("agent")).all():
                acc["agent"][dec] = acc["agent"].get(dec, 0) + c
                acc["agent_p95"] = max(acc["agent_p95"], float(p95))
            row = (await q("ai_today")).one()
            acc["ai"] = [a + float(b) for a, b in zip(acc["ai"], row)]
            acc["ai_month"] += float((await q("ai_month")).scalar() or 0)
            ok, pend, proc, failed, age, stuck, attempts = (await q("rag")).one()
            r = acc["rag"]
            acc["rag"] = [r[0] + ok, r[1] + pend, r[2] + proc, r[3] + failed, max(r[4], float(age)), r[5] + stuck,
                          max(r[6], attempts)]
            for oc, c in (await q("agent_outcome")).all():
                acc["outcome"][oc] = acc["outcome"].get(oc, 0) + c
            a, b = (await q("followups")).one()
            acc["fu"] = [acc["fu"][0] + a, acc["fu"][1] + b]
            pend, failing, age, attempts = (await q("outbox")).one()
            o = acc["outbox"]
            acc["outbox"] = [o[0] + pend, o[1] + failing, max(o[2], float(age)), max(o[3], attempts)]
            acc["processed"] += (await q("processed")).scalar() or 0
            unk, unk_age, retry, stuck, failed = (await q("outbound")).one()
            ob = acc["outbound"]
            acc["outbound"] = [ob[0] + unk, max(ob[1], float(unk_age)), ob[2] + retry, ob[3] + stuck, ob[4] + failed]

    sink.add("zuvo_tenants", len(tenants), "tenants in routing config")
    sink.add("zuvo_approvals_pending", acc["appr"][0], "approvals waiting for a human")
    sink.add("zuvo_approvals_oldest_pending_seconds", acc["appr"][1], "age of the oldest pending approval")
    sink.add("zuvo_approvals_overdue", acc["appr"][2], "pending approvals past their SLA")
    for (d, st), c in sorted(acc["msg"].items()):
        sink.add("zuvo_messages_24h", c, "messages in the last 24h", direction=d, status=st)
    for st, c in sorted(acc["ai_status"].items()):
        sink.add("zuvo_inbound_ai_status_24h", c, "inbound messages by AI processing status (24h)", ai_status=st)
    for dec, c in sorted(acc["agent"].items()):
        sink.add("zuvo_agent_runs_24h", c, "agent decisions (24h)", decision=dec)
    sink.add("zuvo_agent_latency_p95_ms", acc["agent_p95"], "worst per-tenant p95 agent latency (24h)")
    sink.add("zuvo_ai_calls_today", acc["ai"][0], "Gemini calls today (UTC)")
    sink.add("zuvo_ai_tokens_today", acc["ai"][1], "Gemini tokens today", kind="input")
    sink.add("zuvo_ai_tokens_today", acc["ai"][2], "Gemini tokens today", kind="output")
    sink.add("zuvo_ai_cost_inr_today", acc["ai"][3], "estimated Gemini cost today (INR, list price)")
    sink.add("zuvo_ai_cost_inr_month", acc["ai_month"], "estimated Gemini cost month-to-date (INR)")
    for i, st in enumerate(("indexed", "pending", "processing", "failed")):
        sink.add("zuvo_rag_documents", acc["rag"][i], "active knowledge documents by index state", state=st)
    sink.add("zuvo_rag_oldest_pending_seconds", acc["rag"][4], "age of the oldest unindexed document change")
    sink.add("zuvo_rag_processing_stuck", acc["rag"][5], "documents in 'processing' for >10 min (indexer crashed or hung)")
    sink.add("zuvo_rag_failed_max_attempts", acc["rag"][6], "highest consecutive failed-index attempts among failed documents")
    for oc, c in sorted(acc["outcome"].items()):
        sink.add("zuvo_agent_outcomes_24h", c, "agent runs by outcome (24h)", outcome=oc)
    sink.add("zuvo_followups_stuck", acc["fu"][0], "SCHEDULED follow-ups overdue by >10 min (scheduler not claiming)", state="scheduled")
    sink.add("zuvo_followups_stuck", acc["fu"][1], "QUEUED follow-ups overdue by >10 min (consumer not processing)", state="queued")
    sink.add("zuvo_outbox_pending", acc["outbox"][0], "outbox events not yet published to Kafka")
    sink.add("zuvo_outbox_failing", acc["outbox"][1], "unpublished outbox events with >=1 failed publish attempt")
    sink.add("zuvo_outbox_oldest_pending_seconds", acc["outbox"][2], "age of the oldest unpublished outbox event")
    sink.add("zuvo_outbox_max_attempts", acc["outbox"][3], "highest publish-attempt count among unpublished events")
    sink.add("zuvo_outbound_unknown", acc["outbound"][0], "sends with UNKNOWN outcome awaiting webhook or manual reconciliation")
    sink.add("zuvo_outbound_unknown_oldest_seconds", acc["outbound"][1], "age of the oldest UNKNOWN send")
    sink.add("zuvo_outbound_pending_retry", acc["outbound"][2], "sends waiting for a retry after a not-sent failure")
    sink.add("zuvo_outbound_sending_stuck", acc["outbound"][3], "sends in SENDING for >5 min (sweeper not running)")
    sink.add("zuvo_outbound_failed_24h", acc["outbound"][4], "sends that ended FAILED in the last 24h")
    sink.add("zuvo_processed_events", acc["processed"], "rows in the durable processed-event ledger")


async def collect_pg(sm, sink: _Sink) -> None:
    async with sm() as s:
        conns, max_conns, size = (await s.execute(text(
            """SELECT (SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()),
                      current_setting('max_connections')::int, pg_database_size(current_database())"""))).one()
    sink.add("zuvo_pg_connections", conns, "connections to this database")
    sink.add("zuvo_pg_max_connections", max_conns, "server max_connections")
    sink.add("zuvo_pg_database_bytes", size, "database size on disk")


# ---------------------------------------------------------------- Kafka
async def collect_kafka(bootstrap: str, sink: _Sink, timeout: float = 5.0) -> None:
    from aiokafka import AIOKafkaConsumer, TopicPartition
    from aiokafka.admin import AIOKafkaAdminClient

    admin = AIOKafkaAdminClient(bootstrap_servers=bootstrap, client_id="zuvo-opsmetrics")
    consumer = AIOKafkaConsumer(bootstrap_servers=bootstrap, client_id="zuvo-opsmetrics", enable_auto_commit=False,
                                group_id=None)
    await asyncio.wait_for(admin.start(), timeout)
    try:
        await asyncio.wait_for(consumer.start(), timeout)
        try:
            dlq_topics = sorted(t for t in await admin.list_topics() if t.endswith(".DLQ"))
            described = await admin.describe_topics(dlq_topics) if dlq_topics else []
            dlq_tps = [TopicPartition(d["topic"], p["partition"]) for d in described for p in d.get("partitions", [])]
            if dlq_tps:
                end, begin = await consumer.end_offsets(dlq_tps), await consumer.beginning_offsets(dlq_tps)
                per_topic: dict[str, int] = {}
                for tp in dlq_tps:
                    per_topic[tp.topic] = per_topic.get(tp.topic, 0) + end[tp] - begin[tp]
                for t, n in per_topic.items():
                    sink.add("zuvo_kafka_dlq_messages", n, "messages retained in a dead-letter topic", topic=t)

            groups = sorted(g[0] for g in await admin.list_consumer_groups() if not str(g[0]).startswith("zuvo-opsmetrics"))
            for g in groups:
                committed = {tp: om.offset for tp, om in (await admin.list_consumer_group_offsets(g)).items() if om.offset >= 0}
                if not committed:
                    continue
                end = await consumer.end_offsets(list(committed))
                lag_by_topic: dict[str, int] = {}
                oldest = 0.0
                for tp, off in committed.items():
                    lag = max(0, end[tp] - off)
                    lag_by_topic[tp.topic] = lag_by_topic.get(tp.topic, 0) + lag
                    if lag:
                        oldest = max(oldest, await _record_age(consumer, tp, off, timeout))
                for t, lag in sorted(lag_by_topic.items()):
                    sink.add("zuvo_kafka_consumer_lag", lag, "messages not yet committed by the group", group=g, topic=t)
                sink.add("zuvo_kafka_oldest_unprocessed_seconds", oldest, "age of the oldest unconsumed message", group=g)
        finally:
            await consumer.stop()
    finally:
        await admin.close()


async def _record_age(consumer, tp, offset: int, timeout: float) -> float:
    try:
        consumer.assign([tp])
        consumer.seek(tp, offset)
        msg = await asyncio.wait_for(consumer.getone(tp), timeout)
        return max(0.0, time.time() - msg.timestamp / 1000.0)
    except Exception:  # noqa: BLE001
        return 0.0


# ---------------------------------------------------------------- orchestration / rendering
async def collect(sm, tenant_ids: Iterable[str], bootstrap: Optional[str]) -> list[Metric]:
    sink = _Sink()
    sections = [("tenants", collect_db(sm, tenant_ids, sink)), ("postgres", collect_pg(sm, sink))]
    if bootstrap:
        sections.append(("kafka", collect_kafka(bootstrap, sink)))
    for name, coro in sections:
        started = time.perf_counter()
        try:
            await asyncio.wait_for(coro, 30)
            ok = 1
        except Exception as exc:  # noqa: BLE001
            ok = 0
            log.warning("metrics section failed", extra={"ctx": {"section": name, "error_code": exc.__class__.__name__}})
        sink.add("zuvo_collect_ok", ok, "1 if the section was collected", section=name)
        sink.add("zuvo_collect_seconds", round(time.perf_counter() - started, 3), "collection time", section=name)
    return sink.items


def render_prometheus(metrics: list[Metric]) -> str:
    families: dict[str, list[Metric]] = {}
    for m in metrics:  # exposition format requires each family's samples to be contiguous
        families.setdefault(m.name, []).append(m)
    out = []
    for name, ms in families.items():
        out.append(f"# HELP {name} {ms[0].help}")
        out.append(f"# TYPE {name} gauge")
        for m in ms:
            out.append(_sample(m))
    return "\n".join(out) + "\n"


def _sample(m: Metric) -> str:
    lbl = ",".join(f'{k}="{v}"' for k, v in sorted(m.labels.items()))
    return f"{m.name}{{{lbl}}} {m.value:g}" if lbl else f"{m.name} {m.value:g}"


def render_json(metrics: list[Metric]) -> str:
    return json.dumps([{"name": m.name, "labels": m.labels, "value": m.value} for m in metrics], indent=None)


async def _main(prom: bool) -> int:
    from .db import make_sessionmaker
    from .settings import get_settings
    from .tenancy import TenantRouting

    settings = get_settings()
    routing = TenantRouting(settings.tenant_routing_path)
    sm = make_sessionmaker(settings.database_url, pool_size=2)
    try:
        metrics = await collect(sm, [str(t.tenant_id) for t in routing.config.tenants], settings.kafka_bootstrap_servers)
    finally:
        await sm.kw["bind"].dispose()
    print(render_prometheus(metrics) if prom else render_json(metrics))
    return 0 if all(m.value for m in metrics if m.name == "zuvo_collect_ok") else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--prom", action="store_true", help="Prometheus text instead of JSON")
    raise SystemExit(asyncio.run(_main(ap.parse_args().prom)))
