# Runbook: Meta webhook failures / outbound throttling

> Conventions: run on the VM from `/opt/zuvo` as the deploy user. `D=scripts/deploy/dcp` (compose pinned to the current
> release). `M="$D exec -T dashboard-api python -m zuvo_common.opsmetrics"`. Never use `down -v`. Never paste secrets,
> payloads or phone numbers into tickets or chat.

## Symptoms
- No inbound messages arrive while customers are writing. Meta App Dashboard → WhatsApp → Configuration shows webhook delivery errors.
  Or the `metric:zuvo_outbound_pending_retry` / `ratio:outbound_failed` alerts.

## First diagnostics
```bash
scripts/deploy/health-check.sh       # "smoke webhook verify" and "unsigned webhook" lines
$D logs --since 30m gateway | grep -E 'webhook|invalid signature|publish failed' | tail -20
$D logs --since 30m caddy | grep -c '/api/v1/whatsapp/webhook'      # are requests reaching us at all?
```
## Logs and metrics
- `invalid signature` (401) on real traffic = `META_APP_SECRET` does not match the app (it was reset in Meta, or a wrong value is in SSM).
- `webhook verification failed` (403) = `META_VERIFY_TOKEN` mismatch while (re)subscribing.
- `publish failed; asking Meta to retry` (503) = Kafka down (`kafka-consumer-lag.md`).
- No requests at all = DNS/TLS/security group, or the subscription was removed in Meta.
- Outbound: 429 / `131056` = throttling (pair rate limit). 401/190 = an expired or revoked system-user token.

## Safe recovery
1. Secret mismatch: update the value in SSM, `deploy/aws/render-env.sh`, then `$D up -d --no-deps --force-recreate gateway` (approval: secret change).
2. Expired token for outbound sends: generate a new system-user token (`security.md` §6 steps 1, 4), then recreate `dispatch`.
3. Subscription missing: Meta App Dashboard → re-verify the callback URL `https://<domain>/api/v1/whatsapp/webhook`.
4. Throttling: nothing to do. Sends back off (up to 10 min) and stop after 5 attempts as `FAILED/retry_exhausted`.

## Requires approval
- Changing any Meta credential or the webhook subscription.

## Verify
A test message from a phone appears in the dashboard, `pending_retry` decreases, and the Meta dashboard shows successful deliveries.
