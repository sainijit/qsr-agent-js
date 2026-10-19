# Outbound delivery (Phase 7)

How ZuvoCloud sends WhatsApp messages without double-sending, and what to do when a send's outcome is unknown.

**ZuvoCloud does not, and cannot, guarantee exactly-once delivery.** The Meta Cloud API has no idempotency key.
If a request leaves our process and the answer is lost, the message may or may not have been sent. The design:
1. never sends the same request twice automatically;
2. retries only when the send provably did not happen;
3. surfaces every ambiguous outcome as `UNKNOWN` for a webhook or a person to resolve.

## Ledger: `outbound_messages`

Dispatch owns one row per outbound request (migration `0005`, FORCE RLS, no DELETE grant).

| Column | Meaning |
|---|---|
| `id` | `uuid5(tenant, request_id)`: stable; also sent to Meta as `biz_opaque_callback_data` |
| `request_id` | from `outbound.reply.requested` (idempotency key; `UNIQUE (tenant_id, request_id)`) |
| `status` | `PENDING`, `SENDING`, `ACCEPTED`, `FAILED`, `UNKNOWN` |
| `attempts`, `next_attempt_at` | dispatch-level attempts; backoff for provably-unsent failures |
| `last_error_category`, `last_error` | `not_sent`, `retry_exhausted`, `rejected`, `ambiguous`, `policy`, `meta_failed`, `manual` |
| `wamid`, `delivery_status`, `delivered_at`, `read_at` | Meta id and receipt progress (`sent` < `delivered` < `read`; `failed`) |
| `request` | original request JSON, kept only while non-final (needed to re-emit/reconcile), then cleared |
| `reconciled_at`, `reconciled_by` | set when a webhook (`meta_webhook`) or an operator resolved a non-final outcome |

## State machine

```
              policy check fails (opted out / outside 24h without template)
   PENDING ───────────────────────────────────────────────────────────────▶ FAILED (policy)
      │  ▲
      │  │ not_sent, attempts < 5  (next_attempt_at = backoff 30 s … 10 min; sweeper re-attempts)
      ▼  │
   SENDING ──── 2xx ─────────────────────────────────────────────────────▶ ACCEPTED
      │   ───── rejected (4xx) / not_sent at attempt 5 ──────────────────▶ FAILED
      │   ───── ambiguous (read timeout, reset, 5xx, client crash) ─────┐
      │   ───── stale > 120 s (process died mid-send; sweeper) ─────────┤
      ▼                                                                  ▼
                                                                      UNKNOWN ── webhook sent/delivered/read ──▶ ACCEPTED
                                                                              ── webhook failed ──────────────▶ FAILED
                                                                              ── operator (dispatch.reconcile) ▶ ACCEPTED | FAILED
```

* `PENDING → SENDING` is committed **before** Meta is called. It is the only way to reach Meta. A Kafka redelivery,
  a second replica or the sweeper therefore sees `SENDING`/a final state and does nothing.
* A crash after that commit leaves `SENDING`. The sweeper (`DISPATCH_SWEEP_SECONDS`, default 10) turns it into
  `UNKNOWN` after `SENDING_STALE_S` (120 s, more than the client's worst-case time). It is never resent.
* A receipt that arrives while the row is still `SENDING` (Meta can be faster than our commit) sets `ACCEPTED`
  quietly; dispatch's own result transaction then sees the final state and emits it.

## Error classification (`MetaWhatsAppClient`)

| Observation | Category | Action |
|---|---|---|
| 200/201 | accepted | `ACCEPTED` with wamid |
| `ConnectError`, `ConnectTimeout`, `PoolTimeout` | not_sent | 3 quick in-call tries, then dispatch backoff |
| HTTP 429/503; Meta codes 2, 4, 80007, 130429, 131056 | not_sent | same as above (Meta says not processed) |
| Read/write timeout, connection reset, protocol error | **ambiguous** | `UNKNOWN`, no retry |
| Other 5xx | **ambiguous** | `UNKNOWN`, no retry |
| Other 4xx (bad number, template error, auth) | rejected | `FAILED` |
| Opted out / outside 24 h window without template | policy | `FAILED`, Meta not called |

Simulator lines (`PNID_*`) and dry-run mode never call Meta. They return `wamid.SIM.*` / `wamid.DRYRUN.*`.

## Events

Every final state is announced as `outbound.message.sent` through the transactional outbox, in the same transaction
as the state change. The payload adds `state`, `error_category` and `outbound_id`. The event id is
`uuid5(sent:{tenant}:{request_id}:{state})`:
* redeliveries of the same state are deduplicated;
* `UNKNOWN → ACCEPTED/FAILED` is a new event.

Consumers:
* **persister** writes the message history. `UNKNOWN` creates a placeholder message `unknown.{request_id}` with
  status `UNKNOWN`. A later `ACCEPTED`/`FAILED` updates that row (real wamid, `SENT`/`FAILED`) instead of adding
  a second message.
* **agent-worker** marks the follow-up task `FAILED` with `skip_reason = "delivery_unknown: …"`. A later resolution
  moves it to `SENT` or `FAILED`.
* **lead-service** reacts only to successful sends.

## Status webhooks

`gateway` forwards Meta receipts as `message.status`, including `callback_data` (the echoed
`biz_opaque_callback_data`). Inside one tenant transaction the persister does two things:
1. updates `outbound_messages`, matched by wamid, else by callback id (RLS: a receipt can only touch its own tenant).
   If that resolved an `UNKNOWN` (or retry-pending) row, it emits the final `outbound.message.sent`.
2. updates the `messages` row.

Receipts are monotonic (`sent` < `delivered` < `read`, `failed` final), so duplicates and out-of-order receipts
are no-ops.

## UNKNOWN runbook

Alert: `zuvo_outbound_unknown > 0`, page if `zuvo_outbound_unknown_oldest_seconds > 86400`.

1. Most `UNKNOWN` rows resolve themselves within seconds to minutes, when Meta's `sent`/`delivered` webhook arrives. Wait ~15 min.
2. List what is left (metadata only, phone masked):
   ```bash
   docker compose exec -T dispatch python -m dispatch.reconcile list [--tenant UUID]
   ```
3. For each row, check WhatsApp Manager (message history for the business number) or ask the tenant to check the
   customer chat.
4. Record the outcome. This audits it (`OUTBOUND_RECONCILED`) and updates history and follow-ups through the outbox:
   ```bash
   docker compose exec -T dispatch python -m dispatch.reconcile resolve --tenant UUID --id OUTBOUND_ID \
       --as accepted --actor you@company [--wamid wamid.XXX]
   docker compose exec -T dispatch python -m dispatch.reconcile resolve --tenant UUID --id OUTBOUND_ID \
       --as failed --actor you@company --note "not in WhatsApp Manager"
   ```
5. `--as failed` does **not** resend. To send again, a person uses the dashboard. For a follow-up whose last send was
   `delivery_unknown`, "Run now"/"Send now" first answers HTTP 409 and the UI asks for confirmation
   (`confirm_unknown: true`). That is a deliberate human decision to risk a duplicate.

Other checks:
* `zuvo_outbound_sending_stuck > 0`: the dispatch sweeper is not running. Check `docker compose logs dispatch`.
* `zuvo_outbound_pending_retry` rising: Meta is throttling (429/131056) or unavailable (503). Retries back off
  up to 10 min; after 5 attempts the send becomes `FAILED/retry_exhausted`.

## Verification

* Unit, `tests/unit/test_dispatch_client.py` (mocked Graph API via `httpx.MockTransport`): every classification row
  above, retry counts, callback in the body, simulator lines never call HTTP.
* Integration, `tests/integration/test_outbound.py` (real PostgreSQL, scripted Meta client):
  * accepted plus redelivery (one Meta call);
  * ambiguous → UNKNOWN (never resent by redelivery or sweeper);
  * not_sent backoff and sweeper retry, then exhaustion;
  * rejected; policy;
  * stale SENDING → UNKNOWN;
  * webhook reconciliation by callback and by wamid;
  * duplicate/out-of-order receipts; failed receipt;
  * webhook-before-commit race; manual resolve;
  * tenant isolation;
  * persister placeholder → resolved message.
* Live (local stack): simulator sends recorded `ACCEPTED`; an outside-window send recorded `FAILED/policy` without
  calling Meta. A synthetic `UNKNOWN` row resolved with `dispatch.reconcile` flowed through outbox → Kafka →
  persister (message `SENT`) with an audit entry.
* **NOT VERIFIED:** Meta echoing `biz_opaque_callback_data` on a live number (taken from Meta's webhook reference);
  real ambiguous failures against Meta.
