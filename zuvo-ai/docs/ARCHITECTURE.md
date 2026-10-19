# ZuvoCloud Architecture

## Principles
1. **One responsibility per container.** Services talk only through Kafka topics; the sole exception is `dashboard-api` reading the DB.
2. **Tenant on every event.** Every envelope carries `tenant_id` in both the body and a Kafka header, and the key is `tenant_id:worker_id`, so per-line ordering holds. The `ConsumerContract` rejects a missing or mismatched tenant to `<topic>.DLQ`.
3. **At-least-once with idempotency.**
   - Producers use `acks=all` and idempotence.
   - Consumers commit after the handler and dedupe on `event_id` in Redis.
   - The gateway dedupes on `wamid`.
   - Dispatch claims `request_id` at most once, so a send is never repeated.
4. **RLS everywhere.**
   - Services use the `zuvo_app` role (NOBYPASSRLS).
   - `tenant_session()` sets `app.current_tenant` per transaction.
   - Policies are FORCEd, and an unset tenant sees 0 rows.
5. **Config as data.** `config/tenant-routing.yaml` and `qsr/<slug>/skill.yaml` hot-reload; onboarding a tenant needs no code change.

## Topics (12 partitions each, plus `.DLQ`)
| Topic | Producer | Consumers |
|---|---|---|
| `inbound.message.received` | gateway | persister |
| `message.persisted` *(extension)* | persister | ai-router, dashboard-api (broadcast/SSE) |
| `ai.intent.extracted` / `ai.review.flagged` | ai-router | persister |
| `outbound.reply.requested` | dashboard-api | dispatch |
| `outbound.message.sent` *(extension)* | dispatch | persister |

## AI routing (testing configuration)

| Tier | Handles | Confidence / flagging |
|---|---|---|
| Tier 0 | Exact matches against the policy's `tier0_keywords` → `GENERAL_CHAT` | Confidence 1.0 |
| Tier 2 | `gemini-3.5-flash-lite` with `response_schema = Extraction`, temperature 0, phone numbers masked | Below threshold or intent disabled → `ai.review.flagged` (`NEEDS_REVIEW`). Gemini error → `AI_PENDING`; the message is never lost. |
| Tier 3 | Disabled (D2) | — |

Change the model with `ZUVO_GEMINI_MODEL`.

## Auth
- `dashboard-api` issues HS256 JWTs carrying `tenant_id`, `role` (OWNER / MANAGER / SALES_WORKER) and `worker_id`.
- In development, identities come from the routing YAML (`AUTH_DEV_LOGIN=true`).
- A `tenant_id` param that doesn't match the token → 403 (G1). A SALES_WORKER is pinned to their own line (G6).
- SSE takes `?access_token=`, because EventSource cannot send headers.

## HTTP surface (via nginx :3000)

**gateway**
- `GET|POST /api/v1/whatsapp/webhook`
- `POST /api/v1/messages/simulate-inbound` (dev only)

**dashboard-api**
- `GET /api/v1/auth/dev-identities`
- `POST /api/v1/auth/dev-login`
- `GET /api/v1/auth/me`
- `GET /api/v1/dashboard/{tenants,customers,feed,stream}`
- `POST /api/v1/messages/send` → 202 `{status: queued, request_id}`
- `GET /api/v1/health`

## Scaling
- Workers are stateless. Run `docker compose up --scale ai-router=4` to spread partitions; the Gemini call (about 1.3 s) dominates per-message latency.
- dashboard-api replicas each run a broadcast reader, so SSE fan-out is per instance.

## Not yet built
- Normalizer (voice, G9)
- MCP tool bus (`zuvo-commerce` in qsr-agent-js, G4/G7)
- Hermes conversational agent (G8)
- Approvals UI
- Daily pulse

See `INTEGRATION_PLAN_QSR_AGENT.md`.
