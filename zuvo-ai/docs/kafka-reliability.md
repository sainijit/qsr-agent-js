# Kafka reliability and event processing

Status: implemented and verified on the dev stack (Phase 6). Delivery is **at-least-once**. Nothing here is
exactly-once: PostgreSQL, Kafka and the Meta Graph API are separate transactional boundaries, so every
handler is written to tolerate duplicates.

## 1. Building blocks

| Concern | Where | Behaviour |
|---|---|---|
| Envelope + schema | `zuvo_common/events.py` | Pydantic `Envelope` (`extra="forbid"`), `schema_version` (=1) in body and header, `tenant_id`/`event_id` headers. Invalid JSON, a missing tenant or a header/body tenant mismatch go straight to the DLQ. |
| Stable event ids | gateway, persister, ai-router, scheduler | Derived with `uuid5` from business keys (`inbound:{tenant}:{wamid}`, `status:{tenant}:{wamid}:{status}`, `due:{task}:{attempt}` …), so a webhook retry, a redelivery or a re-run produces the same id. |
| Correlation / causation | `zuvo_common/tracing.py` | `traceparent`, `correlation_id` (first event of the chain), `causation_id` (the event whose handler emitted this one) in Kafka headers. The outbox stores these at enqueue time, so relayed events keep them. |
| Offset commits | `bus.py` | `enable_auto_commit=False`; one record per poll; offset committed **after** the handler returns (or after the DLQ publish). |
| Graceful shutdown | `runtime.py` | SIGTERM → `request_stop()`; the in-flight record finishes and its offset is committed, then the consumer leaves the group. |
| Producer | `bus.py` | `acks=all`, `enable_idempotence=True` (removes broker-level duplicates on producer retry within one session only). |
| Retry | `contract.py` | Transient errors: up to 3 attempts, exponential backoff with equal jitter (`d/2 + U(0, d/2)`, base 0.5 s, cap 30 s), no sleep after the last attempt. |
| Permanent errors | `contract.py` `PERMANENT_ERRORS` | `PermanentError`, `ValidationError`, `KeyError`, `TypeError`, `ValueError`, `LookupError`, SQLAlchemy `IntegrityError`/`DataError`/`ProgrammingError` → DLQ on the first failure. `ValueError` is treated as permanent by judgement (bad data rarely heals on retry); raise a different type if a handler needs to retry it. |
| Fast dedupe | Redis (`idempotency.py`) | `claim(group:event_id)` after success; TTL 24 h. A DLQ'd event is **not** claimed, so it can be replayed. |
| Durable dedupe | `processed_events` + `zuvo_common/processed.py` | `first_delivery(s, group, env)` inside the handler's own transaction; see §3. |
| Transactional outbox | `outbox_events` + `zuvo_common/outbox.py` | Business rows + outgoing events commit together; see §2. |

## 2. Transactional outbox

```
handler / HTTP request                    followup-scheduler (relay, every 2 s)
  BEGIN (tenant_session, RLS)               for each tenant in bundles:
    business writes                           SELECT … WHERE published_at IS NULL
    INSERT outbox_events (id = event_id)        AND next_attempt_at <= now() AND created_at <= now()-5s
  COMMIT                                      ORDER BY created_at FOR UPDATE SKIP LOCKED
  fast path: publish those ids (timeout)      publish → published_at = now()
    ok   → published_at = now()               fail  → attempts+1, last_error, next_attempt_at = now()+backoff
    fail → attempts+1, backoff, stop batch            and stop this tenant's batch (keeps order)
```

- **Same transaction:** `Outbox.session(tenant_id)` wraps `tenant_session`; `add()`/`add_all()` insert into the
  outbox with the caller's session. A rollback discards the events with the data.
- **Marked published only after Kafka acks** (`acks=all`). A timeout can still deliver later (aiokafka keeps
  the buffered record), so the relay may publish the same event again. That is expected: consumers dedupe on
  `event_id` (verified live, see §8).
- **Retry:** backoff with equal jitter, 1 s doubling to a 300 s cap. Rows are never given up on; alert on age.
- **Tenant safety:** the relay runs inside `tenant_session` (RLS: a tenant only sees its own rows) and
  additionally re-checks that the body `tenant_id` and the `tenant_id` header match the tenant being relayed.
  A mismatch is quarantined (`next_attempt_at='infinity'`, `last_error='tenant_mismatch'`) and never published.
- **Timeouts:** Kafka workers use a 10 s publish timeout; dashboard HTTP paths use 2 s so a broker outage
  costs a user ~2 s while the relay catches up.
- **Ordering:** per tenant, oldest first; a failure stops the batch so later events do not overtake it.
  Cross-tenant ordering is not guaranteed (and is not needed).

### Flow inventory (what uses the outbox and why)

| Flow | Producer | Mechanism | Reason |
|---|---|---|---|
| inbound webhook → `inbound.message.received` | gateway | direct publish, non-200 on failure (Meta retries), stable id | No DB write in the gateway; Meta is the durable retry source. Redis claim released on failure. |
| status webhook → `message.status` | gateway | same as above (changed in Phase 6: was best-effort and lost receipts on failure) | Receipts drive delivery state (Phase 7). |
| `message.persisted` | persister | direct publish after commit, stable id | Consumer redelivery re-runs the idempotent upsert and re-publishes the same id. |
| `ai.intent.extracted` / `ai.review.flagged` | ai-router | direct publish, stable id | No DB write; offset committed only after publish. On Redis loss Gemini is re-called (cost only). |
| `lead.updated`, `visit.planned` (+ follow-up task rows) | lead-service | **outbox** + `first_delivery` | Events depend on state transitions computed in the transaction; a redelivery would otherwise see the new state and not re-emit. |
| `approval.requested`, `outbound.reply.requested`, `lead.updated` | agent-worker | **outbox** + `first_delivery` | Same reason; previously a publish failure after commit followed by a retry hit `ON CONFLICT` and lost the event. |
| `followup.due`, holding replies | followup-scheduler | **outbox** in the claim transaction | Previously a publish failure left a task QUEUED forever. |
| approve/reject, send-now, manual send | dashboard-api | **outbox** | HTTP request: no redelivery source at all. |
| `outbound.message.sent` | dispatch, persister (webhook reconciliation), `dispatch.reconcile` | outbox | Written in the same transaction as the `outbound_messages` state change; one event id per (request, state). See `docs/outbound-delivery.md`. |
| `lead.updated` from dashboard edits | dashboard-api | best-effort direct publish | UI refresh hint only; the DB row is the source of truth. |

## 3. Durable idempotency (processed-event ledger)

`processed_events (tenant_id, consumer_group, event_id)` primary key, RLS-forced.

`first_delivery()` is the **first statement inside the handler's transaction**:

- true → do the work and enqueue any events in the same transaction; all of it commits (or rolls back) together.
- false → this exact event was already fully processed by this group: return without side effects. Nothing
  is lost because the earlier run's events are already in the outbox.

Used by: agent-worker (`on_extraction`, `on_visit_planned`, `on_followup_due`, `on_approval_decided` — the
last prevents double-counting autonomy counters), lead-service (`_inbound`, `_outbound`, `on_extraction`),
persister (`record_usage` + audit row only; the message upsert is already naturally idempotent).

Covered failure windows:

| Window | Result |
|---|---|
| crash before DB commit | everything rolled back, ledger row included → redelivery processes normally |
| crash after DB commit, before offset commit | redelivery: `first_delivery` false → skip; outbox relay publishes the pending events |
| Redis flushed / TTL expired | ledger still rejects the duplicate |
| duplicate publication (outbox fast path + relay) | consumer dedupes on `event_id` (Redis, then ledger) |

### Retention

| Table | Kept | Why |
|---|---|---|
| `outbox_events` published | 7 days | diagnosis only |
| `outbox_events` unpublished | forever (never deleted automatically) | they are undelivered business events; alert instead |
| `processed_events` | 14 days | must outlive Kafka retention (168 h) so any redeliverable offset is still recognised |

Cleanup runs hourly inside followup-scheduler (`outbox.cleanup`), per tenant under RLS.

## 4. Dead-letter queues

Every topic has `<topic>.DLQ`. A DLQ record keeps the **original key, value bytes and headers** (tenant,
event id, schema version, traceparent, correlation/causation) plus:

| Header | Meaning |
|---|---|
| `error` | `permanent:` / `transient:` prefix + scrubbed message |
| `original_topic` | where it came from |
| `dlq_group` | consumer group that failed (replays target only this group) |
| `dlq_error_class` | exception class |
| `dlq_attempts` | attempts made (1 for permanent errors) |
| `dlq_failed_at` | ISO timestamp |
| `dlq_partition`, `dlq_offset` | source position |

## 5. Replay procedure

All commands run inside dashboard-api (it has Kafka access); no consumer group is used, so reading never
moves offsets.

```bash
D="docker compose exec -T dashboard-api python -m zuvo_common.dlq"
$D summary                                                        # counts by DLQ topic / group / error class
$D show   --topic outbound.reply.requested.DLQ --max 20           # metadata only
# filters for show/replay: --group, --event-id, --tenant, --error-class, --max (default 50)
$D replay --topic outbound.reply.requested.DLQ --event-id <id>     # dry run (default)
$D replay --topic outbound.reply.requested.DLQ --event-id <id> --execute   # after fixing the cause
```

- `show` prints metadata only (ids, error class, scrubbed error) — never the payload (PII).
- Replay publishes to `original_topic` with the original key/value, strips the DLQ headers, and adds
  `replay_for_group=<dlq_group>` and `replayed_from=<topic>:<partition>:<offset>`. Other consumer groups on
  that topic skip the record, so a replay never re-runs groups that already succeeded.
- The same `event_id` is reused. The failed group never claimed it, so it is processed; the durable ledger
  still prevents double side effects if it had partially succeeded. Replaying the same record twice is
  harmless: the second copy is skipped as a duplicate once the first succeeded.
- DLQ records written before Phase 6 have no `dlq_group`; `replay` refuses them. Inspect them with `show` and
  re-drive the business action manually (e.g. resend from the dashboard).
- **Before replaying `outbound.reply.requested`**, check the conversation: the customer may already have
  been answered by a human. Replaying it sends a WhatsApp message.
- DLQ topics follow Kafka retention (168 h). Investigate within a week or export with `show`.

## 6. Topology

- Single KRaft broker (`apache/kafka:3.8.0`), `KAFKA_AUTO_CREATE_TOPICS_ENABLE=false`, retention 168 h.
- `kafka-init` creates every topic and its `.DLQ` with `--if-not-exists --partitions 12 --replication-factor 1`.
  It is repeatable and never alters an existing topic. Changing partitions of a live topic is a manual,
  deliberate operation (it remaps keys → breaks per-key ordering for in-flight data).
- Replication factor is 1 because there is one broker. **12 partitions on one broker give consumer
  parallelism only, not high availability**: if the broker or its volume is lost, unconsumed events are lost
  (the outbox and Meta retries narrow, but do not close, that window). Durability of business state comes from
  PostgreSQL backups (Phase 9).
- Key = `{tenant_id}:{worker_id|-}` → per tenant-line ordering. No change was needed.

## 7. Monitoring

From `/internal/metrics` (see `docs/observability.md`):

| Metric | Alert |
|---|---|
| `zuvo_outbox_pending` | informational (normally 0 within seconds) |
| `zuvo_outbox_oldest_pending_seconds` | `> 120` (relay or Kafka is down) |
| `zuvo_outbox_failing` | `> 0` for 5 min |
| `zuvo_outbox_max_attempts` | `> 10` (retry storm / quarantined row) |
| `zuvo_processed_events` | informational; should plateau (cleanup working) |
| `zuvo_kafka_consumer_lag`, `zuvo_kafka_oldest_unprocessed_seconds` | see observability doc |
| `zuvo_kafka_dlq_messages` | any increase |

Retry storms also show in logs: `outcome="retry"` lines per `service`/`error_code` (Logs Insights query in
`docs/observability.md`).

Quarantined outbox rows: `SELECT id, topic, last_error FROM outbox_events WHERE next_attempt_at = 'infinity'`
(as `zuvo_owner` with `SET row_security = off`). They indicate a producer bug; fix and delete or re-tenant
them deliberately.

## 8. Verification (dev stack)

- Unit: `tests/unit/test_reliability.py` (retry/permanent classification, DLQ metadata, replay targeting,
  jitter bounds, outbox tenant guard) and gateway publish-failure / stable-id tests in `test_services.py`.
- Integration (real PostgreSQL, `scripts/test_rls.sh`): `tests/integration/test_outbox.py` — rollback,
  duplicate enqueue, Kafka-down → pending → relay publishes, batch stops on failure, ledger once-only and
  rollback, crash-after-commit redelivery emits no duplicate and loses nothing, tenant isolation, quarantine,
  retention. 49 integration tests pass (39 RLS + 10 outbox).
- Live drill: Kafka stopped, `POST /api/v1/messages/send` returned 202 in 2.0 s; row pending with
  `TimeoutError`, `zuvo_outbox_pending=1`, `zuvo_outbox_failing=1`. Kafka started → relay published within
  ~6 s. aiokafka also delivered the buffered first attempt, so dispatch received the event twice and logged
  the second as `outcome=duplicate` — one send.
- e2e suites (pytest e2e, `e2e_phase1.py`, `e2e_rag.py`) pass after the change.

- `dlq summary` ran against the live broker (all 13 DLQ topics empty).

NOT VERIFIED: `dlq replay --execute` against a live broker (the DLQs were empty; header rewriting is unit-tested),
broker volume loss, multi-hour Kafka outage, behaviour under load (Phase 14).
