# Runbook: Outbound WhatsApp message stuck in UNKNOWN / SENDING

> Conventions: run on the VM from `/opt/zuvo` as the deploy user. `D=scripts/deploy/dcp` (compose pinned to the current
> release). `M="$D exec -T dashboard-api python -m zuvo_common.opsmetrics"`. Never use `down -v`. Never paste secrets,
> payloads or phone numbers into tickets or chat.

## Symptoms
- `metric:zuvo_outbound_unknown` (after 3 runs ≈ 15 min), `metric:zuvo_outbound_unknown_oldest_seconds` (> 24 h, critical), `metric:zuvo_outbound_sending_stuck`.
- UNKNOWN means the Meta send call's outcome is unknown (timeout or connection reset after the request was sent). The system never resends automatically, because that could duplicate the message.

## First diagnostics
```bash
$D exec -T dispatch python -m dispatch.reconcile list            # metadata only, phone masked
$D logs --since 1h dispatch | grep -E '"operation": "meta.send"' | grep -v '"outcome": "ok"' | tail
```
## Safe recovery
Follow `docs/outbound-delivery.md` "UNKNOWN runbook":
1. Wait about 15 min: most rows resolve through Meta's status webhook.
2. For each remaining row, check WhatsApp Manager message history (or ask the tenant).
3. Record the result: `reconcile resolve --tenant <uuid> --id <id> --as accepted|failed --actor <you> [--wamid ...]`.
4. `sending_stuck > 0`: the dispatch sweeper is not running. `$D up -d --no-deps --force-recreate dispatch`.

## Requires approval
- Any resend of a message whose delivery is unknown (a person decides in the dashboard, which asks for `confirm_unknown`).

## Verify
`reconcile list` is empty, `zuvo_outbound_unknown` is 0, and the audit log has `OUTBOUND_RECONCILED` entries.
