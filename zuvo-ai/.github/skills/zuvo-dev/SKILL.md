---
name: zuvo-dev
description: Principal-engineer context for the ZuvoCloud (zuvo-ai) multi-tenant WhatsApp AI operations platform. Use for any design, implementation, debugging, review, or roadmap work in this repo — webhook ingestion, multi-number worker routing, tenant isolation, dashboard (Gate G6), outbound messaging, Gemini intent/NER extraction (POC-2), MCP deterministic tool bus, approvals, and the React Owner Command Center.
---

# ZuvoCloud (zuvo-ai) — Developer Skill

## 1. Product context

ZuvoCloud is a **multi-tenant WhatsApp AI operations platform for Indian wholesale
distributors** (beachhead: Jaipur — hardware, sanitary, furniture). One distributor
(tenant) runs N worker WhatsApp Business numbers (sales rep, counter clerk, godown
manager). The owner gets a unified "Owner Command Center" across all lines and,
later, AI-extracted orders gated by deterministic credit/stock/discount rules.

Authoritative docs (read before changing behavior):
- `requirement.md` — full PRD: FR-1..6, NFR-1..4, phase matrix, AI tiering, gates G1–G9.
- `simplified_requirements.md` — condensed PRD + 30-day sprint plan.
- `Stage1_POC_Feasibility_and_Architecture.md` — Stage-1 scope, Meta constraints, security guardrails.
- `docs/ARCHITECTURE.md` (links use stale Windows `file:///C:/js/...` paths), other `docs/*.md`.
- `README.md`.
- `requirement-whatsapp.txt` — Detailed Design v2.0: event backbone ("Hermes" = **Allegro Hermes/Kafka**, not the Hermes Agent), `config/tenant-routing.yaml`, per-tenant `qsr/<slug>/skill.yaml`, RLS, topic model.
- `docs/INTEGRATION_PLAN_QSR_AGENT.md` — phased plan integrating zuvo-ai with qsr-agent-js (Hermes Agent runtime + `zuvo-commerce` MCP service) on Gemini. Follow its phase order and contracts. **Decided:** Apache Kafka (aiokafka, KRaft locally) is the event bus from Phase 0; `gemini-3.5-flash-lite` for all AI during testing (model via config, Tier 3 disabled).
- Root `*.docx`, `generate_doc.py`, `generate_simplified_doc.py` are generated docs — not runtime code.

### Roadmap / where code is heading

| Stage | Scope | Status in code |
|---|---|---|
| Stage-1 POC | Meta webhook, HMAC, wamid dedupe, multi-number → worker → tenant routing, persistence, audit, outbound send, dashboard feed + G6 filters | **Implemented** |
| POC-2 / Sprint 2 | Gemini Flash structured JSON intent + NER (`CREATE_ORDER`, `INQUIRY_PRICE_STOCK`, `PAYMENT_COMMITMENT`, `COMPLAINT_RETURN`, `GENERAL_CHAT`), confidence < 0.80 → `NEEDS_REVIEW` | Columns only (`Message.parsed_intent`, `confidence_score`) |
| Sprint 2 | Deterministic **MCP tool bus**: `get_customer_profile`, `search_item_catalog`, `check_stock_availability`, `evaluate_credit_standing`, `create_draft_order`, `trigger_owner_approval_alert` — all take `tenant_id` | Not started |
| Sprint 2–3 | Credit rule: `exposure = outstanding + pending drafts + new order`; `> limit → HELD_FOR_APPROVAL / PENDING_APPROVAL`. Discount > 5% or confidence < 80% → owner approval | Not started (Customer has credit fields) |
| Sprint 3 | SSE live feed, approval queue, 8 PM Daily Pulse | Polling UI only |
| Phase 2+ | Voice-note STT (G9), outbound quotes/templates, Tally sync, edge inference (Qwen + OpenVINO) | Not started |

AI routing target (requirement.md §5.2): Tier 0 regex → Tier 1 small local model →
**Tier 2 Gemini Flash** (Hinglish NER, JSON schema, tool-call formulation) → Tier 3
frontier reasoning fallback. **The LLM must never invent price, stock, or credit —
only deterministic tools produce those values.**

Acceptance gates: G1 tenant isolation (403 cross-tenant), G2 5-number attribution,
G3 Hinglish extraction, G4 credit enforcement, G5 dashboard < 3.5 s, G6 worker-line
isolation + Customer 360°, G7 immutable audit of every AI tool call, G8 catalog
clarification + human gate, G9 voice STT.

### Phase 1 (implemented): leads, follow-ups, pricing, learning
Spec and status: `docs/PHASE1_LEADS_FOLLOWUP_PLAN.md` (§10). Every business is configured only by a **tenant bundle**, `tenants/<slug>/{config.yaml, skill.md, pricing.csv, knowledge/*.md}`; there is no per-tenant code. `tenants/_template` is the starting point.

**Services**
- `lead_service` consumes `ai.intent.extracted`. It runs the stage machine (NEW→ENGAGED→QUALIFIED→VISIT_PLANNED→VISITED→WON/LOST/DORMANT), creates `visit_plans` via `dates.resolve_visit`, and schedules `followup_tasks` from `config.followups` rules. It also handles opt-out (`leads.is_opt_out`, config keywords, whole-message match).
- `agent_worker` turns an extraction into a proposal (`logic.py`):
  - prices come from `pricing.match_prices` (deterministic);
  - KB and learned-example reuse use lexical `similarity`;
  - Gemini (`drafter.py`) only words the reply around those facts.
  
  The confidence gate, mode (auto/draft/off) and learning gate then give either a send or an `approvals` row with a PENDING_APPROVAL agent_run. It also consumes `followup.due` and `approval.decided`, writing `learned_examples` and `autonomy_stats` (reply kind only). The month budget cap is `tenants.monthly_ai_budget_inr`.
- `followup_scheduler` runs a tick loop rather than consuming Kafka:
  - claims due tasks (`FOR UPDATE SKIP LOCKED`) and publishes `followup.due` (uuid5 event id);
  - sends holding replies for approvals past their SLA;
  - runs a dormant sweep every 600 s;
  - runs `metrics.compute_day` → `metrics_daily` rollup every 300 s.
- `dispatch` additions:
  - template sends (`TemplateSpec`), which bypass the 24h window;
  - retry on 429/5xx with no retry on timeout;
  - refuses non-human sends to opted-out customers;
  - non-numeric PNIDs (`PNID_*` simulator lines) produce a simulated `wamid.SIM.*`.
- `dashboard_api/phase1.py` (`/api/v1`) provides:
  - `auth/login|tenants|switch-tenant`, `dashboard/today`;
  - `leads` (+PATCH), `visits` (+outcome), `followups` (skip/send-now);
  - `approvals/{id}/decide` (approve/edit/reject; FOR UPDATE, 409 if already decided, template outside 24h);
  - `insights`, `learning/examples` (toggle), `settings/config|pricing` (dry-run diff → import).
  
  The SSE events are `message`, `lead`, `visit` and `approval`.
- The shared libs are in `zuvo_common/{bundle,onboarding,leads,dates,pricing,learning,metrics,outbound}.py`. `outbound_envelope` is the single way to build an outbound request.

**Data (migration 0002)**
- Tables: tenant_config_versions, users/memberships (no RLS; password login JWT sub `user:<email>`), campaigns, leads, lead_events, visit_plans, followup_tasks, price_lists/price_items, kb_documents/kb_chunks, agent_runs, approvals (+`template_params`), learned_examples, autonomy_stats, metrics_daily, ai_usage_daily.
- Every other table has FORCE RLS.
- **Never edit 0002**; it is applied. Use 0003+.
- Watch the CHECK constraints: followup status includes AWAITING_APPROVAL/NEEDS_ACTION; the agent_run decision is PENDING_APPROVAL; approvals include SUPERSEDED.

**Onboarding**
- `python3 scripts/onboard_tenant.py tenants/<slug> --validate`, then `--apply [--password EMAIL=PW]` (idempotent).
- The migrator also applies `--all` on every `docker compose up`, so it re-imports `pricing.csv` and overrides Settings uploads if they differ.

**Demo tenants**
- jaipur-hardware, delhi-electricals, alpha-automobile (real Meta pnid `1340933852445316` + `PNID_ALPHA_01`), jaipur-furniture (`PNID_JF_01/02`).
- Logins `owner@<slug>.demo` / `Zuvo@2026`.

## 2. High-level design (event-driven microservices on Kafka, since 2026-10-17)

No monolith: the old `app/` was removed. Each container has one responsibility, and services communicate only through Kafka.

```
Meta ─▶ gateway ─▶ [inbound.message.received] ─▶ persister ─▶ Postgres (RLS, role zuvo_app)
          (HMAC, routing YAML, policy check,              │
           Redis wamid dedupe, 503 if publish fails)  [message.persisted] ─▶ dashboard-api ─SSE─▶ frontend
                                                          │                (broadcast reader)
                                                          ▼
                                                      ai-router ── Tier0 keywords / Gemini Flash-Lite NER
                                                          │
                          [ai.intent.extracted | ai.review.flagged] ─▶ persister (ai_status, extraction)
frontend ─▶ dashboard-api ─▶ [outbound.reply.requested] ─▶ dispatch ─▶ Meta (META_DRY_RUN default true)
                                           [outbound.message.sent] ─▶ persister
```

**Kafka**
- apache/kafka 3.8 in KRaft mode, a single node for development.
- `kafka-init` creates 6 topics plus their `.DLQ` topics, with 12 partitions each. Auto-create is off.
- Key is `tenant_id:worker_id`. The `tenant_id` travels in both a header and the body.

**Stack**
- Python 3.12, FastAPI, aiokafka, SQLAlchemy 2 async (asyncpg), Alembic, redis-py, PyJWT, google-genai.
- Frontend: React 18, Vite, TypeScript, Tailwind.
  - UI design system: light theme only, no dark mode. The page uses the `canvas` background; panels use white `.card`.
  - Primary colour is the `brand` (blue) palette; `emerald` is reserved for success and WhatsApp states.
  - Shared component classes in `src/index.css`: `.card`, `.btn-primary`, `.btn-secondary`, `.btn-ghost`, `.btn-icon`, `.field`, `.label`, `.eyebrow`.
  - Font: Inter.
  - Helpers exported from `ConversationsList.tsx`: `avatarTone`, `initialsOf`, `prettyRole`.
  - Layout: 3 panes of equal height. The chat and the composer share one card.

## 3. Repo map (low-level)

```
libs/zuvo_common/zuvo_common/      shared library (pip-installed into every image)
  settings.py   env-only Settings (get_settings); no secrets in files
  events.py     Topics, Headers, Envelope(event_id,tenant_id,worker_id,customer_id,wamid,payload),
                InboundMessagePayload, Intent enum, Extraction (= Gemini response_schema), ExtractionPayload,
                OutboundRequestPayload, OutboundSentPayload
  bus.py        EventBus ABC; KafkaEventBus (acks=all, idempotent; manual commit after handler;
                group_id=None ⇒ broadcast reader); InMemoryEventBus for tests (.on(topic))
  contract.py   ConsumerContract: tenant header required/authorised/matches body → else DLQ; event_id
                idempotency (Redis); retries w/ backoff; PermanentError ⇒ straight to DLQ
  tenancy.py    TenantRouting (YAML, mtime hot-reload, keeps last valid), worker_uuid/number_uuid (uuid5),
                Role, TenantPolicy + TenantPolicyLoader (path-traversal guard, tenant_id must match)
  db.py         make_sessionmaker, tenant_session(sm, tid) ⇒ set_config('app.current_tenant') per txn
  models.py     Tenant, Worker, WhatsAppNumber, Customer, Conversation, Message(ai_status, extraction), AuditLog
  security.py   Meta HMAC verify/sign, Principal, issue/decode JWT (HS256, iss=zuvocloud)
  web.py        current_principal (Bearer or ?access_token for SSE), enforce_tenant (403 = G1)
  audit.py      write_audit with mask_details (phones masked, text ⇒ "<n chars>")
  idempotency.py, phone.py (E.164 digits, no '+'), logging.py (JSON, mask_phone), tenant_sync.py
services/<svc>/{Dockerfile,requirements.txt,<pkg>/main.py}   build context = repo root
  gateway/        create_app factory; ingest.py Ingestor; webhook GET/POST; simulate-inbound (dev)
  persister/      the only writer for messages; publishes message.persisted (change=created|ai:*|outbound)
  ai_router/      extractor.py GeminiExtractor + build_system_prompt(policy); main.py AiRouter tiers
  dispatch/       MetaWhatsAppClient (dry-run), Dispatcher (tenant line check, at-most-once, 24h window)
  dashboard_api/  main.py routes; queries.py RLS read models; broker.py SSE fan-out
  migrator/       run.py: create/alter role zuvo_app NOBYPASSRLS → alembic upgrade → sync routing
                  migrations/versions/0001_initial.py: DDL, FORCE RLS policies, grants
                  0002 phase-1 leads; 0003_rag_pgvector: vector ext, kb_documents.body/indexed_*, kb_chunks.embedding(768)/tsv
  agent_worker/   logic.faq_proposal(rag=…) — tenant `knowledge.retrieval: rag` calls zuvo-ai-rag (2 s) else lexical;
                  RagUnavailable ⇒ strategy=lexical_fallback; learned example always wins over retrieval
zuvo-ai-rag/      FastAPI :8020 (internal). Gemini gemini-embedding-001@768 + pgvector hybrid (HNSW + tsv, RRF), RLS per tenant.
                  main.py (RagState/create_app) · api/routes.py (v1 tenant API, Bearer token) · services/store.py (SQL)
                  services/indexer.py (10 s reconcile: checksum≠indexed_checksum) · services/retrieval_service.py (Redis query cache)
                  components/gemini.py · components/chunking/* (from smart-kiosk rag-service) · config.yaml (min_similarity 0.60)
infra/postgres/   Dockerfile: postgres:16-alpine + pgvector v0.8.0
config/tenant-routing.yaml   2 tenants: Jaipur Hardware (owner + 5 lines incl. real Meta pnid
                             1312713255253790) and Delhi Electricals (for G1 tests)
qsr/<slug>/skill.yaml        per-tenant policy: intents.enabled, confidence_threshold, guardrails, tier0_keywords
frontend/                    nginx routes /api/v1/whatsapp + simulate → gateway, rest → dashboard-api;
                             LoginScreen (dev identities), SSE-triggered refetch, JWT in sessionStorage
tests/unit (in-memory, no Docker)   tests/e2e (against running stack via :3000)
docs/ARCHITECTURE.md, docs/INTEGRATION_PLAN_QSR_AGENT.md (phase status)
```

### Data model
- Every table has `tenant_id`, plus RLS `tenant_isolation` (FORCE). The tenants table uses `id`.
- Unset tenant ⇒ 0 rows, via `NULLIF(current_setting('app.current_tenant', true), '')::uuid`.
- **Worker**: `id` = uuid5(tenant, worker_key). `worker_key` comes from the routing YAML. `role` is OWNER, MANAGER or SALES_WORKER.
- **WhatsAppNumber**: `id` = uuid5(tenant, pnid); `meta_phone_number_id` is unique.
- **Customer**: unique on (tenant, phone). Has credit_limit and outstanding.
- **Conversation**: one per (line, customer). Has `last_inbound_at`, which drives the 24h window.
- **Message**: unique on (tenant, wamid). Fields: `parsed_intent`, `confidence`, `extraction` (JSONB), `ai_status`, `status`.
  - `ai_status` values: PENDING, EXTRACTED, NEEDS_REVIEW, AI_PENDING, SKIPPED.
- **AuditLog**: zuvo_app may only SELECT and INSERT.

G6 filters:
- **Worker filter**: shows only that worker's line.
- **Customer filter**: by `customer_id` or `customer_phone` (normalised); shows the customer across all lines.
- A SALES_WORKER is always pinned to their own `worker_id`.

### Key flows
- **Inbound:**
  - The gateway publishes and ACKs. It returns 503 if Kafka publish fails, so Meta retries.
  - The persister upserts the customer and conversation and inserts the message. On a duplicate wamid it does nothing.
  - It then publishes `message.persisted` with change `created`, and the ai-router picks it up.
- **AI:**
  - Non-text messages → flagged `unsupported_media` (the Normalizer for voice is pending).
  - Tier 0 keyword → GENERAL_CHAT, confidence 1.0.
  - Otherwise Gemini; then `intent_disabled` / `low_confidence` → `ai.review.flagged`.
  - A Gemini error → AI_PENDING.
  - Output event_ids are deterministic: uuid5(tenant, message_id).
- **Outbound:**
  - `POST /api/v1/messages/send` resolves the sender line, which must belong to the tenant; a worker may use only their own line. It returns 202 with a request_id.
  - dispatch claims `zuvo:send:{tenant}:{request_id}`, checks the 24h window, sends (or dry-runs), and publishes `outbound.message.sent`.
  - The persister then writes the OUTBOUND row.

### Extension points
- **New consumer service:**
  - Wrap its handler with `ConsumerContract`; never parse records by hand.
  - Do all DB access through `tenant_session`.
  - Add its own Dockerfile, requirements and compose service, plus any topics in `kafka-init`.
- **New topic:** add it to `Topics` and `Topics.ALL`, to the `kafka-init` loop, and to the ARCHITECTURE table.
- **New tenant or line:** edit the routing YAML and the qsr policy only, with no code change; the migrator and persister sync it.
- **MCP tools (Phase 3):** deterministic and tenant-scoped. The `tenant_id` comes from the envelope or principal, never from model output.

## 4. Run / test

```bash
cp .env.example .env            # secrets; GEMINI_API_KEY; proxy vars on Intel network
docker compose up -d --build    # 15 containers (incl. zuvo-ai-rag); migrator & kafka-init are one-shot
docker compose ps; docker compose logs -f ai-router persister
# UI http://localhost:3000 (dev login) · webhook :8000 · dashboard-api 127.0.0.1:8001
python -m venv /tmp/zuvo-dev && /tmp/zuvo-dev/bin/pip install -e libs/zuvo_common fastapi httpx pytest pytest-asyncio
NO_PROXY=127.0.0.1 /tmp/zuvo-dev/bin/python -m pytest -q -p no:cacheprovider tests/unit   # unit: 185 pass
set -a; . ./.env; set +a; ZUVO_E2E_URL=http://localhost:3000 /tmp/zuvo-dev/bin/python -m pytest tests/e2e   # 6 pass
set -a; . ./.env; set +a; /tmp/zuvo-dev/bin/python scripts/e2e_phase1.py   # Phase 1 flow (--tenant-slug, --line); prints PASS
set -a; . ./.env; set +a; /tmp/zuvo-dev/bin/python scripts/e2e_rag.py [--rag-down]   # RAG reply path on Alpha (PNID_ALPHA_01)
docker compose exec zuvo-ai-rag python scripts/evaluate_rag.py [--rerank]   # golden-set eval: RAG vs lexical, floor sweep
python3 scripts/e2e_local.py   # stdlib-only local trace: verify GET, HMAC, dedupe, persist, Gemini, SSE, reply, DLQ
scripts/cf_tunnel.sh start [--new]|status|stop   # Cloudflare quick tunnel -> gateway :8000 only. cloudflared ignores proxies: script requests tunnel via curl+proxy and relays edge TCP 7844 (http2) through socat. Creds in gitignored .tunnel/; --new changes URL (update Meta callback).
# ai-router retries Gemini timeouts/429/5xx GEMINI_MAX_ATTEMPTS (3) times with 1s,2s backoff before flagging AI_PENDING
docker compose up -d --scale ai-router=3   # spreads tenants only: key tenant:worker → one tenant stays on one partition (docs/load-testing.md §3.3)
```

Gotchas:
- **Corporate proxy:** the build args are passed via a compose anchor. The ai-router and dispatch services need `HTTPS_PROXY` set, with `NO_PROXY` covering the internal hosts. Use `curl --noproxy '*'` for localhost.
- **asyncpg** cannot run multi-statement SQL. In migrations, use `_execute_script()`.
- **Startup noise:** `GroupCoordinatorNotAvailableError` in the first few seconds is normal; Kafka is still creating its offsets topic.
- **nginx 502 after rebuilding dashboard-api:** the frontend's nginx caches the old upstream IP. Run `docker compose restart frontend`.
- **psql:** RLS is forced even for the owner, so use `docker compose exec -T postgres psql -U zuvo_owner -d zuvo_db -c "set app.current_tenant='<uuid>'" -c "..."`.
- **Kafka storage** is ephemeral in development; Postgres and Redis use named volumes.
- **Postgres image** is built from `infra/postgres/Dockerfile` (postgres:16-alpine + pgvector). Keep the alpine base: switching to Debian changes libc collation on the existing volume.
- **zuvo-ai-rag** has no published port. Probe it with `docker compose exec zuvo-ai-rag python -c ...` (Bearer `RAG_SERVICE_TOKEN`). Its tests run inside the image (see `zuvo-ai-rag/README.md`), not under the root `pytest.ini`.
- **Knowledge edits:** editing `tenants/<slug>/knowledge/*.md` and then running `docker compose run --rm migrator` updates `kb_documents.body`/`checksum`; the RAG indexer re-embeds within about 10 s. `sync_knowledge` only manages `knowledge/…` paths, so `uploads/…` documents (RAG API) survive.

### Production operations (docs/deployment.md, deploy/aws/README.md §0)
```bash
PYTHON=/tmp/zuvo-dev/bin/python SHELLCHECK=/tmp/zuvo-dev/bin/shellcheck scripts/release-gates.sh [--fast]  # unit, rag, integration, compose, secrets, shellcheck
scripts/deploy/preflight.sh && scripts/deploy/deploy.sh   # first install AND every release; exit 2/3/4/5/6/7 = preflight/build/backup/migrate/health/lock
scripts/deploy/smoke-test.sh                              # read-only: health, TLS/headers, 401s, exposure, topics, zuvo_app role, RLS
scripts/deploy/rollback.sh [--list|--to TAG]              # app images only; refused if schema differs
scripts/deploy/dcp <compose args>                         # compose pinned to the current release (never plain compose on the server)
scripts/deploy/dcp run --rm --no-deps migrator python set_password.py <email>   # per-user password: prompt/stdin, never argv
scripts/backup/backup-db.sh --project zuvo-ai [--quick-verify] [--s3 URI]       # restore: docs/backup-and-restore.md §5
deploy/ops/zuvo-watch.sh [--status]                       # cron alerting; runbooks in docs/runbooks/
```
- Rehearse in an isolated project: `COMPOSE_PROJECT_NAME=zuvo-prodtest ZUVO_ENV_FILE=/tmp/x.env ZUVO_STATE_DIR=/tmp/state scripts/deploy/deploy.sh --allow-dirty --local-rehearsal` (other ports). Clean up with `down` then explicit `docker volume rm` of that project's volumes — never `down -v` on a real stack.
- Load test without paid calls: `deploy/loadtest/` Gemini mock (`ZUVO_COMPOSE_EXTRA=deploy/loadtest/docker-compose.loadtest.yml`) + `scripts/loadtest/zuvo_load.py` (refuses unless localhost, `META_DRY_RUN`, mock). `--approve` graduates the learning gate and changes later runs. Results: `docs/load-testing.md`.
- Status of every production risk: `docs/production-readiness-assessment.md` §0.

## 5. Hard rules

1. **Tenant scoping:**
   - Every DB access goes through `tenant_session`, connected as `zuvo_app`. Never use the owner role in a service.
   - Every event carries a tenant header and a tenant field in the body, and they must match.
2. **Secrets come only from `.env` or a secret manager.** Compose has no secret defaults; tests use dummy values.
3. **The gateway never does slow work.** No DB writes and no LLM calls before the ACK.
4. **Idempotency at every hop:** wamid at the gateway, `event_id` per consumer group, `request_id` in dispatch.
5. **Audit state changes and AI calls** using `write_audit`, which masks PII. Logs use `mask_phone` / `mask_text`.
6. **The LLM never produces price, stock or credit values.** Those come from deterministic tools. Low confidence or a policy breach → human review.
7. **Respect Meta's 24h window.** META_DRY_RUN stays true unless sends are explicitly wanted.
8. **Schema changes go through new Alembic revisions only.** Keep RLS enabled and FORCEd on new tables, and add grants for them.
9. **Every change ships with tests:** a unit test (in-memory bus or idempotency store) and, where relevant, an e2e test.
10. **Never push to `github.com/zuvocloud-admin/*`** (the origin remote) unless the user explicitly asks.

## 6. Adding a feature — checklist
- [ ] Contract first: payload model in `events.py`, plus the topic in `Topics.ALL` and in `kafka-init`.
- [ ] Service logic is in its own container; consumers are wrapped with `ConsumerContract`.
- [ ] Model or migration change, with RLS policy and grants.
- [ ] Any new endpoint uses `current_principal` and `enforce_tenant`, and checks the worker scope.
- [ ] Audit with masked details; no PII in logs.
- [ ] Any new env var is added to Settings, `.env.example` and the compose environment.
- [ ] Frontend: update `services/api.ts`, which must go through `authFetch`.
- [ ] Unit and e2e tests are green; update docs and the plan status.

## 7. Review checklist
- Cross-tenant leak? Look for a client-supplied tenant, a missing `tenant_session`, a missing header check, or the SSE filter.
- Is a secret in a tracked file or a compose default?
- Is a consumer non-idempotent, or does it commit before the handler finishes? Can a handler exception lose a message?
- Could an outbound message be sent twice? Is the 24h window bypassed?
- Is PII in logs, audit records or error bodies?
- Are datetimes timezone-aware?

## 8. Known issues / pending
- 🔴 Meta secrets from earlier commits are still in git history. The owner must rotate them in Meta Business Manager.
- Dev login (`AUTH_DEV_LOGIN`) is not production auth (production refuses it); production uses email + password set with `set_password.py`. No MFA yet.
- Redis has no password (R15); it relies on the internal data network.
- `AI_PENDING` messages are not reprocessed automatically after a Gemini outage (`docs/runbooks/gemini-errors.md`).
- **Pending phases:**
  - Normalizer (voice, G9)
  - golden evaluation set for intents (RAG has one: `zuvo-ai-rag/eval/golden.yaml`)
  - Gemini circuit breaker
  - MCP tool bus in qsr-agent-js (G4/G7)
  - Hermes conversational agent (G8)
  - daily pulse
  - Phase 1 gaps: owner WhatsApp approval alert (`notify_owner_whatsapp` is unused), `message.status` delivery ticks
  - testcontainers integration tests
- The ai-router consumes `message.persisted` instead of the spec's `inbound.message.normalized`; this is a documented deviation.
- The old docs `API_REFERENCE.md` and `BUILD_AND_TEST_GUIDE.md` are marked outdated.
