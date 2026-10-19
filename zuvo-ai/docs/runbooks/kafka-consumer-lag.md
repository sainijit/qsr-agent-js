# Runbook: Kafka consumer lag / outbox / stuck follow-ups

> Conventions: run on the VM from `/opt/zuvo` as the deploy user. `D=scripts/deploy/dcp` (compose pinned to the current
> release). `M="$D exec -T dashboard-api python -m zuvo_common.opsmetrics"`. Never use `down -v`. Never paste secrets,
> payloads or phone numbers into tickets or chat.

## Symptoms
- `lag:<group>:<topic>`, `lagage:<group>`, `metric:zuvo_outbox_*`, `metric:zuvo_followups_stuck` or `collect:kafka` alerts. Replies or approval cards are delayed.

## First diagnostics
```bash
$M | python3 -c 'import json,sys; [print(m["name"], m["labels"], m["value"]) for m in json.load(sys.stdin) if "kafka" in m["name"] or "outbox" in m["name"]]'
$D ps -a        # is the consumer for that group running/healthy?
$D exec -T kafka /opt/kafka/bin/kafka-consumer-groups.sh --bootstrap-server localhost:9092 --describe --group <group>
```
## Logs and metrics
- `$D logs --tail 200 <consumer>`: look for repeated `event handled` with `outcome != ok`, Gemini errors, or DB errors.
- The group → service map is in `docs/kafka-reliability.md` §6. Outbox publishing runs inside the producing service (`docs/kafka-reliability.md` §2).

## Safe recovery
1. Consumer unhealthy or stalled (heartbeat stale > 180 s): `$D up -d --no-deps --force-recreate <svc>`. Offsets are committed and handling is idempotent (ledger), so a restart is safe.
2. Lag with a healthy consumer: a downstream is slow (Gemini → `gemini-errors.md`; DB → `postgres-down-or-disk-full.md`). Lag drains by itself once that is fixed.
3. Kafka down: `$D up -d --no-deps kafka`; check disk (`docker system df`, volume `kafka_data`).
4. Outbox failing: the cause is Kafka unavailability. Rows are retried automatically after Kafka returns.

## Requires approval
- Resetting consumer group offsets (skips or replays events), deleting topics, increasing partitions.

## Verify
Lag < 100 and `zuvo_outbox_pending` back to 0 for two watch runs. A test message flows end to end.
