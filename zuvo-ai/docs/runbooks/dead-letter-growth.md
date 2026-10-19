# Runbook: Dead-letter queue growth

> Conventions: run on the VM from `/opt/zuvo` as the deploy user. `D=scripts/deploy/dcp` (compose pinned to the current
> release). `M="$D exec -T dashboard-api python -m zuvo_common.opsmetrics"`. Never use `down -v`. Never paste secrets,
> payloads or phone numbers into tickets or chat.

## Symptoms
- `dlq:<topic>.DLQ` alert ("N new DLQ record(s)"). A message was not processed after retries, or failed permanently.

## First diagnostics
```bash
DLQ="$D exec -T dashboard-api python -m zuvo_common.dlq"
$DLQ summary                                   # counts by topic / group / error class
$DLQ show --topic <topic>.DLQ --max 20         # metadata + scrubbed error only, never payloads
```
## Logs and metrics
- Use the event id from `show` with `$D logs <service> | grep <event_id>`, and follow the `correlation_id` (`docs/observability.md` §5).
- `dlq_error_class` / `error` prefix: `permanent:` = bad data or a bug; `transient:` = retries exhausted during an outage.

## Safe recovery
1. Fix the cause first (deploy the bug fix, or wait for Gemini/DB to recover).
2. Dry-run the replay: `$DLQ replay --topic <topic>.DLQ --event-id <id>`.
3. Replay a single event: `... --execute`. Replays target only the failed consumer group, and the ledger prevents double side effects (`docs/kafka-reliability.md` §5).
4. DLQ records expire after 7 days (topic retention). Investigate within the week.

## Requires approval
- **Replaying `outbound.reply.requested.DLQ`** (sends a WhatsApp message to a customer). First confirm with the tenant that nobody already answered.
- Bulk replays (more than 10 events).

## Verify
The replayed event shows `outcome=ok` in logs, no new DLQ growth for 2 watch runs, and the business effect is visible (lead, reply) in the dashboard.
