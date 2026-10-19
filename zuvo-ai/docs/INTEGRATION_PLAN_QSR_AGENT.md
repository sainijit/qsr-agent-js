# ZuvoCloud × qsr-agent-js Integration Plan (Gemini)

Status: v1.1 (Phases 0–2 text implemented) · 2026-10-17 · Source spec: `requirement-whatsapp.txt` (Detailed Design v2.0, G1–G9)

## 0. Implementation status (2026-10-17)

Phases 0, 1 and 2 (text only) are implemented in zuvo-ai as separate containers on Kafka. The monolith `app/` has been removed.

| Container | Path | Consumes → Produces |
|---|---|---|
| `gateway` | `services/gateway` | Meta webhook / simulator → `inbound.message.received` |
| `persister` | `services/persister` | inbound, `ai.*`, `outbound.message.sent` → DB (RLS) + `message.persisted` |
| `ai-router` | `services/ai_router` | `message.persisted` (created, inbound) → `ai.intent.extracted` / `ai.review.flagged` |
| `dispatch` | `services/dispatch` | `outbound.reply.requested` → Meta (dry run by default) → `outbound.message.sent` |
| `dashboard-api` | `services/dashboard_api` | HTTP + SSE; publishes `outbound.reply.requested` |
| `migrator` | `services/migrator` | one-shot: role `zuvo_app` (NOBYPASSRLS), Alembic, routing sync |
| `frontend` | `frontend` | nginx; routes to gateway / dashboard-api |

Verified: 18 unit tests and 6 e2e tests pass against the live stack. The e2e tests cover:
- G1: cross-tenant requests get 403, and RLS returns 0 rows for another tenant.
- G2: 5 lines are each attributed to the right worker.
- G6: the worker and customer filters work, and a sales worker can see only their own line.
- Gemini Flash-Lite extraction lands on the message (about 1.3 s per call).
- An outbound dry run is persisted.
- A duplicate wamid is ignored.

**Deviation from spec:**
- The ai-router consumes `message.persisted` (extension topic) instead of `inbound.message.normalized`, so the row exists before the AI update.
- The Normalizer (voice, G9) will slot in between the gateway and the persister.

**Next:**
1. Phase 2b: Normalizer (voice) and the golden evaluation set.
2. Phase 3: the `zuvo-commerce` MCP tool bus in qsr-agent-js.
3. Phase 4: Hermes Agent on Gemini.
4. Phase 5: approval workflow.

## 1. What we are building

ZuvoCloud is a multi-tenant WhatsApp operations copilot for Indian distributors.

- Staff keep chatting on their own WhatsApp numbers; the system listens silently.
- AI extracts intents and orders from Hinglish text and voice notes.
- Deterministic tools enforce credit, stock and discount rules. The AI never invents a price, a stock level or a credit figure.
- The owner gets one live dashboard with a worker view, a Customer 360° view and 1-click approvals.
- A new tenant is onboarded with config files only: a `tenant-routing.yaml` entry, a skill file and a DB tenant row.

## 2. Terminology conflict (resolve first)

| Term in spec | Meaning in spec | Same word in qsr-agent-js |
|---|---|---|
| **Hermes** | Allegro Hermes: a REST pub/sub broker on Apache Kafka (the event backbone) | NousResearch **Hermes Agent**: the LLM agent runtime (`hermes -z`, skills, MCP client) |
| **qsr/** skill file | `qsr/<slug>/skill.yaml`: per-tenant *policy data* (thresholds, intents, persona) | `qsr-skills/<name>/SKILL.md`: *behaviour/routing prose* loaded by Hermes Agent |
| **MCP Tool Bus** | Deterministic catalog, stock, credit and order tools | `QsrService` + `PolicyGate` + `GateLevel` MCP services; the same concept, already built |

This document uses the following names:
- **Event Bus** for Allegro Hermes / Kafka.
- **Agent Runtime** for Hermes Agent.
- **Tenant Policy** for `skill.yaml`.
- **Agent Skill** for `SKILL.md`.

## 3. Target architecture: who owns what

```
                      ┌──────────────────────── zuvo-ai (Data + Experience plane) ─────────────────────────┐
 WhatsApp ─► Meta ─►  │ Ingestion Gateway ─► Tenant Resolver (tenant-routing.yaml→Redis) ─► Tenant Policy  │
                      │   HMAC · dedupe wamid · ACK<1.5s                     (qsr/<slug>/skill.yaml)        │
                      │        │ publish inbound.message.received                                         │
                      └────────┼──────────────────────────────────────────────────────────────────────────┘
                               ▼
                 ┌──────── Event Bus: Apache Kafka (EventBus interface; Allegro Hermes REST layer optional) ┐
                               │                    │                       │                     │
            ┌──────────────────▼───┐   ┌────────────▼───────────┐  ┌────────▼─────────┐  ┌────────▼────────┐
            │ Persister + Normalizer│   │ AI Router (zuvo-ai)    │  │ Agent Worker     │  │ Owner Center /  │
            │ (zuvo-ai)             │   │ Tier0 keywords         │  │ (qsr-agent-js)   │  │ Dispatch        │
            │ DB write, voice→text  │   │ Tier2 Gemini Flash-Lite│  │ Hermes Agent +   │  │ (zuvo-ai)       │
            │ via Gemini audio      │   │ structured NER JSON    │  │ Gemini Flash-Lite│  │ SSE, approvals, │
            └───────────┬───────────┘   └────────────┬───────────┘  │ Agent Skills     │  │ Meta send, 24h  │
                        │                            │              └────────┬─────────┘  └────────┬────────┘
                        │                            │       MCP (HTTP, tenant-bound token)│            │
                        │                            │              ┌────────▼─────────┐           │
                        │                            └─────────────►│ zuvo-commerce MCP│◄──────────┘
                        │                                           │ (qsr-agent-js    │
                        │                                           │ QsrService +     │
                        │                                           │ PolicyGate)      │
                        ▼                                           └────────┬─────────┘
                   PostgreSQL 16 + RLS (app.current_tenant) · append-only audit_logs ◄───┘
```

| Concern | Owner | Reuses |
|---|---|---|
| Webhook, tenant resolve, dedupe, persistence, dashboard, approvals UI, dispatch | **zuvo-ai** | Existing `IngestionService`, `RouterService`, `MetaWhatsAppClient`, React app |
| Single-shot intent + NER extraction (high volume, cheap) | **zuvo-ai AI Router** using the `google-genai` SDK directly | Gemini structured output (`response_schema`) |
| Deterministic tools and gates (catalog, stock, credit, draft order) | **New `zuvo-commerce` MCP service** in qsr-agent-js | `QsrService`, `act_tool`, `GateLevel`, `PolicyGate`, JSONL tool log |
| Multi-turn conversation (catalog clarification, quotes, G8) | **qsr-agent-js Agent Runtime** (Hermes Agent + Gemini) | `autonomy/advisor.py` bounded runs, `qsr-skills/*` pattern, AGENTS.md rules |
| Approval workflow semantics | Designed in qsr-agent-js; **UI and state live in zuvo-ai** | Ports `ProposalStore` semantics: dedupe, expiry, re-validate on approve |

**Principle:** use the LLM only where it adds value.
- Extraction is one structured Gemini call; it does not go through an agent loop.
- The Agent Runtime runs only for `INQUIRY_PRICE_STOCK` and `CREATE_ORDER` conversations.
- These figures are estimates and must be measured in Phase 2:
  - Flash-Lite extraction costs about ₹0.1 per message.
  - An agent turn costs about ₹2–4.

## 4. Gemini model plan

| Tier | Spec | Proposed | Why |
|---|---|---|---|
| 0 | keywords | Keywords from `tier0_keywords` | Zero cost |
| 1 | Qwen 1.5B | **Defer.** Use a lightweight language/spam heuristic for now. Optional later via the existing OVMS path in qsr-agent-js | Flash-Lite is cheap enough for POC volumes (20–80 customers/day) |
| 2 | Gemini Flash | **`gemini-3.5-flash-lite` for everything during testing**: extraction, Agent Runtime tool calling, STT and Daily Pulse | ✅ Decided 2026-10-17: costs $0.30 in / $2.50 out per 1M tokens and fits the ₹500 test budget |
| 3 | Claude/GPT-4o | **Disabled during testing.** Escalations go to the human approval queue instead. Revisit after the Phase 2 evaluation (candidate: `gemini-3.8-flash` for the agent if Flash-Lite's tool-use accuracy is too low) | Keeps one model and one key |

The model name is configured, never hardcoded:
- `ZUVO_GEMINI_MODEL` in zuvo-ai.
- `model.default` in the Hermes config in qsr-agent-js.

Moving up a tier should therefore be a config change only.
| STT (G9) | separate STT | **Gemini audio input** (`.ogg/.opus` sent directly) | One vendor; transcript plus intent in one call |

**Data residency (NFR-3, DPDP):** the Gemini Developer API (AI Studio key) does not guarantee processing in India. For the pilot, move to **Vertex AI in `asia-south1`**, after confirming the models are available in that region. Before any Gemini call, mask customer phone numbers and send only the message text plus a minimal context window.

## 5. Contracts (freeze before parallel work)

Create a versioned `contracts/` package in zuvo-ai. Both repos import it or copy it at a pinned version.

1. **Event envelope** as in spec §4: `event_id, tenant_id, worker_id, customer_id, wamid, occurred_at, payload`, plus `schema_version`. `tenant_id` is a required header, and the key is `tenant_id:worker_id`.
2. **NER schema (G3):** `{intent, customer_account, line_items[{sku_name, quantity, unit, requested_rate}], urgency, payment_reference, requested_discount_pct, confidence, lang}`.
   - The Pydantic model is also the Gemini `response_schema`.
3. **Tenant Policy schema:** a Pydantic model for `skill.yaml` that is validated on load and hot-reloaded.
   - Add `meta_phone_number_id` beside `whatsapp_number`. Meta webhooks identify the receiving line by `metadata.phone_number_id`; the display number is not a stable key.
4. **MCP tool contract** for `zuvo-commerce`, following the qsr rules:
   - Every result carries `schema_version`, `source`, `observed_at`, units and `placeholder`.
   - **`tenant_id` is bound from a signed per-run token in the MCP transport header, not trusted from model arguments.** If the argument differs from the token, the call is denied with 403 and audited.
5. **Consumer contract:**
   - Missing or unauthorised `tenant_id` goes to the DLQ and is audited.
   - Run `SET LOCAL app.current_tenant` before any DB access.
   - Skip events whose `event_id` was already processed.
   - Unrecoverable errors go to `<topic>.DLQ`.

## 6. Phase-by-phase plan

Each phase ends at a demoable gate. Phases 2 and 3 can run in parallel once Phase 0 contracts are frozen.

### Phase 0: Foundations and hardening (≈1 week) · prerequisite for every gate

**zuvo-ai**
- [ ] ⚠️ **Owner action:** rotate the committed Meta `app_secret` and system token. Move all secrets to `.env` or a secret manager, and remove them from `config/config.yaml` and the compose defaults.
- [x] Fix the `dashboard.list_customers` NameError, the port mismatch (8000 vs 8010), and add `greenlet` to requirements. (Resolved by replacing the monolith.)
- [x] Postgres only for runtime: introduce **Alembic** and migrate ID columns to native `UUID`. Keep SQLite only if RLS tests can run against Postgres (testcontainers).
- [x] **JWT auth** carrying `tenant_id` and `role`. Middleware sets `app.current_tenant`. **RLS policies on all tenant tables.** Cross-tenant access returns 403. **Proves G1.**
- [x] `config/tenant-routing.yaml` loader with mtime hot-reload and a sync into the `whatsapp_numbers` table. (Redis cache not needed yet: each service holds it in memory.)
- [x] `qsr/<slug>/skill.yaml` Tenant Policy loader with schema validation.
- [x] **Kafka from day one** (✅ decided 2026-10-17). Run a single-node KRaft Kafka in docker-compose for local development; no ZooKeeper.
  - Create the topics from spec §4 and their DLQ topics with a fixed partition count (for example 12) and key `tenant_id:worker_id`.
- [x] `EventBus` interface with an **aiokafka** adapter:
  - Producer settings: `acks=all` and `enable_idempotence=true`.
  - Consumers commit manually after the DB commit (at-least-once delivery plus `event_id` dedupe).
  - Keep the interface so the Allegro Hermes REST layer can be added later without touching services.
- [x] Testing: an in-memory `EventBus` fake for unit tests (`tests/unit`); e2e tests run against the real compose stack (`tests/e2e`). testcontainers still to do.

**qsr-agent-js**
- [ ] Land the Gemini migration from the earlier scope analysis:
  - `config.example.yaml` uses `provider: gemini`.
  - Relax `entrypoint.py` so it no longer forces `custom`.
  - Remove OVMS from compose or make it optional.
  - Add a `GEMINI_API_KEY` placeholder to `.env.example`.
  - Update the advisor defaults.
- [ ] Create a `contracts/` package (envelope, NER, Tenant Policy, MCP tool schemas).

**Exit:** a JWT for tenant B gets 403 when reading tenant A's data; two tenants load from YAML with no code change.

### Phase 1: Event-driven ingestion (≈1 week) · G2, G5 groundwork

- [x] Change the Gateway to: HMAC (on by default) → resolve → policy load → dedupe → **publish `inbound.message.received`** → ACK.
  - The DB write moves out of the webhook request.
- [x] Persister consumer that writes Message, Conversation and Customer under RLS, makes the `wamid` duplicate check safe, and normalises phones to E.164.
- [x] Use the same consumer helper (tenant header, RLS, idempotency, DLQ) in every later service.
- [x] SSE `/dashboard/stream`, tenant-filtered from the JWT. The React feed switches from polling to SSE.
- [ ] Load test: 50 concurrent messages across 5 lines must give 0 duplicates and 100% correct attribution.

**Exit:** G2 passes; a message appears on the dashboard in under 3.5 s (G5, without AI).

### Phase 2: Gemini intelligence (≈1–1.5 weeks) · G3, G9

- [x] **AI Router** consumer in zuvo-ai:
  - Tier 0 keywords from the Tenant Policy.
  - Tier 2 is a Gemini Flash-Lite structured-output call using the NER `response_schema`, with the persona and enabled intents from the Tenant Policy.
- [x] Write `Message.parsed_intent` and `confidence`, then publish `ai.intent.extracted`.
- [x] If confidence is below the policy threshold, publish `ai.review.flagged` with status `NEEDS_REVIEW` instead.
- [ ] **Voice (G9):** the Normalizer downloads the Meta media, sends the audio to Gemini, and publishes `inbound.message.normalized` with the transcript and `lang`.
- [ ] **Golden evaluation set:** 100, then 500, real or synthetic Hinglish, Hindi and English messages with expected JSON. Track per-intent precision and recall, field accuracy, p95 latency and ₹ per message. Run it in CI against a fixed model version.
- [ ] Record every Gemini call in audit with the model, a prompt hash, token counts and latency. Do not store the raw PII body.
- [~] Add retry, timeout and circuit breaker (timeout and contract retries done; circuit breaker pending). If Gemini is down, the message is kept and marked `AI_PENDING` (NFR-4: never lost).

**Exit:** "Sharma Traders ko 50 box pipe kal bhej do" produces correct JSON (G3); a voice note is transcribed and classified (G9).

### Phase 3: Deterministic tool bus as a qsr MCP service (≈1–1.5 weeks) · G4, G7

- [ ] New zuvo-ai tables: `items`, `stock`, `orders`, `order_lines`, `approvals`, all with `tenant_id` and RLS. Add a Tally/CSV catalog import.
- [ ] **`services/zuvo-commerce/`** in qsr-agent-js, built on `QsrService`:
  - Read tools: `get_customer_profile`, `search_item_catalog`, `check_stock`, `evaluate_credit_standing`, `get_conversation_history`.
  - Action tool: `create_draft_order` with `GateLevel` and `PolicyGate`.
  - **Gate thresholds come from the Tenant Policy:**
    - credit formula breach → `PENDING_APPROVAL` (reason `credit`)
    - discount above `max_auto_discount_pct` → reason `discount`
    - low confidence → reason `low_conf`
- [ ] Streamable-HTTP transport behind TLS and a tenant-bound bearer token. Every tool call writes to append-only `audit_logs` with `{tenant_id, tool, params_snapshot, result_hash, actor, ts}` (G7). The DB role has no UPDATE or DELETE grant.
- [ ] Publish `order.draft.created` and `order.pending.approval`.
- [ ] Tests follow the qsr pattern: FastMCP in-process client, gate allow/deny, credit edge cases, and a tenant-mismatch denial.

**Exit:** an order over the credit limit lands in `PENDING_APPROVAL` (G4); every tool call has an audit row (G7).

### Phase 4: Conversational agent with qsr Agent Runtime and Gemini Flash-Lite (≈1.5 weeks) · G8

- [ ] **Agent Worker** in qsr-agent-js consumes `ai.intent.extracted` for `INQUIRY_PRICE_STOCK` and `CREATE_ORDER`. It runs a bounded Hermes Agent session, reusing the `advisor.py` limits on rounds, reads and timeout.
  - Each run uses a **per-run config and HERMES_HOME** containing only the `zuvo-commerce` MCP server with that tenant's token.
- [ ] New Agent Skills:
  - `qsr-skills/wholesale-order-intake/SKILL.md`
  - `qsr-skills/catalog-inquiry/SKILL.md`, which asks clarifying questions about dimension, material and budget before quoting.
  - Both are tenant-agnostic. The persona and thresholds are injected from the Tenant Policy.
- [ ] Add zuvo rules to AGENTS.md: never state a price, stock or credit figure unless it came from a tool; use only the tenant from context.
- [ ] Output is a **proposed reply or order**, never a direct send:
  - If confidence is at or above the threshold and the gates pass, publish `outbound.reply.requested`.
  - Otherwise publish `order.pending.approval`, which reuses the ProposalStore semantics: expiry, and re-validating fresh context when the owner approves.
- [ ] Load multi-turn state from zuvo's DB via `get_conversation_history`. The Agent Runtime stays stateless.

**Exit:** the "bed and table price" inquiry asks about size and budget, then quotes from the catalog. A 15% discount request becomes an owner alert (G8).

### Phase 5: Owner Command Center and Dispatch (≈1 week) · G5, G6 complete

- [ ] Approval queue UI with 1-click Approve and Counter, publishing `order.approved` and `order.countered`. Optionally, the owner can approve by WhatsApp reply to the OWNER number.
- [ ] **Dispatch Service:**
  - Meta send that respects the 24h window (templates outside it).
  - Catalog cards and photos.
  - Delivery statuses written back to the DB.
- [ ] RBAC from the JWT: OWNER sees everything; MANAGER has approval limits; SALES_WORKER sees only their own lines (G6 at the auth layer, not only as a filter).
- [ ] 8 PM Daily Pulse per tenant timezone. Deterministic SQL aggregates are summarised by Gemini Flash-Lite, and the numbers come only from SQL.

**Exit:** G5 under 3.5 s with AI tags arriving asynchronously; G6 worker and 360° views are enforced by RBAC.

### Phase 6: Scale, backbone and pilot (≈1–2 weeks) · all gates, Definition of Done

- [ ] Production Kafka: managed Kafka in `asia-south1` (for example Confluent Cloud or GCP Managed Service for Apache Kafka), replication factor 3, ACLs, and consumer-lag alerts.
  - Add the Allegro Hermes REST gateway only if external producers need HTTP publish.
- [ ] Hash-partition `messages`, `orders` and `audit_logs` by `tenant_id`.
- [ ] Deploy to GCP `asia-south1` (Cloud Run, Cloud SQL, Memorystore, Vertex AI) with Secret Manager.
- [ ] Observability: per-stage latency, Gemini tokens and ₹ per tenant, DLQ depth, gate-trigger rates.
- [ ] Replay a load test of 500 transcripts; onboard 3 pilot tenants **by config only** (Definition of Done).

## 7. Risks and decisions needed

| # | Item | Recommendation |
|---|---|---|
| D1 | Event backbone | ✅ **Kafka from Phase 0** (KRaft locally, managed Kafka in production). Allegro Hermes REST layer optional later |
| D2 | Model | ✅ **`gemini-3.5-flash-lite` for all AI during testing**; Tier 3 disabled, escalations go to a human |
| D3 | Tier 1 Qwen | Defer; revisit when on-prem or edge is needed (the OVMS path already exists in qsr-agent-js) |
| D4 | Gemini endpoint | Vertex AI `asia-south1` before real tenant data (DPDP) |
| D5 | Where approvals live | zuvo-ai Owner Center only. Retire the qsr Operator UI for this product to avoid two approval UIs |
| R1 | Model hallucinating `tenant_id` or values | Tenant bound by transport token; values only from MCP tools; tool results carry `source` and `observed_at` |
| R2 | Agent latency or cost per turn | Agent only for inquiry and order intents; bounded rounds; measure ₹ per turn in the Phase 2 evaluation |
| R3 | Hermes Agent is single-context per process | Per-run HERMES_HOME and config; never share an MCP session across tenants |
| R4 | Committed secrets already in git history | Rotate now; optionally rewrite history before sharing the repo |

## 8. Gate → phase map

| Gate | Phase | Owning component |
|---|---|---|
| G1 isolation / 403 | 0 | zuvo-ai JWT + RLS, MCP tenant token |
| G2 5-worker ingestion | 1 | Gateway + Tenant Resolver |
| G3 Hinglish NER | 2 | AI Router (Gemini Flash-Lite) |
| G4 credit rule | 3 | zuvo-commerce MCP + PolicyGate |
| G5 < 3.5 s dashboard | 1 → 5 | Persister + SSE |
| G6 worker / 360° | 1 → 5 | Dashboard + RBAC |
| G7 audit | 2 → 3 | Append-only `audit_logs` |
| G8 catalog + human gate | 4 → 5 | Agent Runtime + Agent Skills + approvals |
| G9 voice STT | 2 | Normalizer (Gemini audio) |
