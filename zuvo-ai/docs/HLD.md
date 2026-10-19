# ZuvoCloud — High-Level Design (HLD)

Status: current as of 2026-10-19 (Phase 1 + zuvo-ai-rag). Owner: ZuvoCloud platform.
Related: [ARCHITECTURE.md](ARCHITECTURE.md) · [PHASE1_LEADS_FOLLOWUP_PLAN.md](PHASE1_LEADS_FOLLOWUP_PLAN.md) ·
[ZUVO_AI_RAG_PLAN.md](ZUVO_AI_RAG_PLAN.md) · [`zuvo-ai-rag/README.md`](../zuvo-ai-rag/README.md)

## 1. Purpose and scope

ZuvoCloud is a **multi-tenant WhatsApp AI assistant for small businesses**, such as a car service centre or a furniture showroom.
These businesses get leads from digital marketing, for example Click-to-WhatsApp ads. For each business, the platform:

- receives customer WhatsApp messages on one or more business numbers;
- understands intent with Gemini, including Hinglish (price question, FAQ, visit plan, opt-out, …);
- answers pricing and FAQ questions from that business's own price list and knowledge base;
- asks for **human approval** when unsure, and **learns** from approvals so it can gradually auto-reply;
- tracks **leads**, planned **visits** and **follow-ups**, and shows tenant-scoped metrics on a dashboard.

**Onboarding is configuration only.** A new business is a folder `tenants/<slug>/` containing `config.yaml`, `skill.md`, `pricing.csv` and `knowledge/*.md`; no code changes are needed.

Out of scope today: order and payment backends, voice notes, multiple regions, and a high-availability deployment.

## 2. System context

```
              Customers (WhatsApp)                         Business owners / staff (browser)
                     │                                                   │
                     ▼                                                   ▼
        Meta WhatsApp Cloud API ──webhook (HMAC)──►  ┌──────────────────────────────┐
                     ▲                               │          ZuvoCloud           │
                     └────────── send (Graph API) ───│   (Docker Compose, 1 VM)     │
                                                     └──────────────┬───────────────┘
                                                                    │ HTTPS
                                          Google Gemini API (Flash-Lite: NER + drafting,
                                          gemini-embedding-001: knowledge embeddings)
```

| External system | Used for | Failure behaviour |
|---|---|---|
| Meta WhatsApp Cloud API | Inbound webhooks and outbound sends | Inbound: Meta retries. Outbound: at-most-once send; on failure, the error is recorded and the message appears in the dashboard. `META_DRY_RUN=true` by default. |
| Google Gemini | Intent extraction, reply wording, embeddings, `/answer` playground | Retries with backoff. Extraction fails as `AI_PENDING`. Drafting falls back to the deterministic grounded text. RAG falls back to lexical retrieval. |
| Cloudflare tunnel (dev) | Public HTTPS URL for the gateway webhook | Quick-tunnel URLs change; production should use a named tunnel or a domain with TLS. |

## 3. Architecture style

- **Event-driven microservices on Kafka.** Each container has one responsibility, and services communicate only through topics. The exceptions are the RAG HTTP call and dashboard reads from Postgres.
- **Shared library** `libs/zuvo_common`. It provides settings, events and envelopes, the Kafka bus, the consumer contract, tenancy, database/RLS sessions, security, bundles, pricing, leads, learning and the RAG client.
- **Tenant isolation in depth:**
  - every event carries `tenant_id` in both a header and the body;
  - consumers reject events for unknown or mismatched tenants, sending them to the DLQ;
  - Postgres **Row-Level Security** (`app.current_tenant`) applies to every tenant table, with the app role set to `NOBYPASSRLS`;
  - dashboard JWTs are bound to tenant memberships.
- **Configuration-driven behaviour.** Per-tenant bundles, plus a hot-reloaded number→tenant→worker routing YAML.
- **Human in the loop by default.** Auto-send only happens when confidence, mode and the learning gate all allow it.

## 4. Logical architecture

```
Meta ─► gateway ─► [inbound.message.received] ─► persister ─► Postgres (RLS)
                                                    │
                                          [message.persisted] ─► dashboard-api ─SSE─► frontend
                                                    ▼
                                                ai-router  (Tier0 keywords → Gemini structured NER)
                                                    │
                              [ai.intent.extracted | ai.review.flagged] ─► persister (extraction, ai_status)
                                    │                         │
                                    ▼                         ▼
                              lead-service               agent-worker ──HTTP──► zuvo-ai-rag ─► pgvector
                     (lead stage, visit plans,      (price / FAQ / greeting proposal,
                      follow-up tasks, opt-out)      Gemini wording, confidence gate)
                                    │                         │
                  [lead.updated | visit.planned]   auto-send ─┼─► [outbound.reply.requested] ─► dispatch ─► Meta
                                                              └─► [approval.requested] ─► dashboard (owner)
 owner approve/edit/reject ─► dashboard-api ─► [outbound.reply.requested] + [approval.decided] ─► agent-worker (learning)
 followup-scheduler ─(due tasks, SLA holding replies, dormant sweep, daily metrics)─► [followup.due] ─► agent-worker
 dispatch ─► [outbound.message.sent] ─► persister / lead-service
```

### 4.1 Containers (15)

| Container | Type | Responsibility | Exposed |
|---|---|---|---|
| `gateway` | FastAPI | Meta webhook GET verify / POST, HMAC check, number→tenant routing, Redis `wamid` dedupe, publish inbound; dev simulator | `:8000` (webhook) |
| `persister` | consumer | **Only writer** of customers, conversations and messages; applies AI results and outbound status | – |
| `ai-router` | consumer | Tier 0 keyword rules, then Gemini Flash-Lite structured extraction (intent, item, price query, visit date, confidence, language) | – |
| `lead-service` | consumer | Lead state machine, CTWA ad attribution, visit plans, follow-up scheduling, opt-out | – |
| `agent-worker` | consumer | Builds the reply proposal (price, FAQ, greeting, visit ack), polishes it with Gemini under grounding guards, decides `AUTO_SENT`, `PENDING_APPROVAL` or `SKIPPED`, records `agent_runs`, learns from decisions | – |
| `zuvo-ai-rag` | FastAPI | Per-tenant knowledge indexing (Gemini embeddings) and hybrid retrieval (pgvector + full text, RRF) | internal `:8020` |
| `followup-scheduler` | loop | Due follow-ups, approval SLA holding replies, dormant-lead sweep, `metrics_daily` rollup | – |
| `dispatch` | consumer | Meta Graph API sender: tenant line check, 24-hour window and template rules, at-most-once | – |
| `dashboard-api` | FastAPI | Login (JWT), tenant-scoped reads, approvals, leads, insights, SSE, outbound requests | `127.0.0.1:8001` |
| `frontend` | nginx + React | Owner Command Center; the only browser entry point; proxies `/api` | `:3000` |
| `kafka` | infra | Event backbone (KRaft, single node in dev) | internal |
| `postgres` | infra | System of record + pgvector, with RLS | internal |
| `redis` | infra | Webhook dedupe, consumer idempotency, RAG query-embedding cache | internal |
| `migrator` | one-shot | DB role, Alembic migrations, routing sync, tenant bundle apply (config, skill, pricing, knowledge, users) | – |
| `kafka-init` | one-shot | Creates topics and their DLQs (12 partitions each) | – |

### 4.2 Event catalogue (Kafka)

| Topic | Producer → consumers |
|---|---|
| `inbound.message.received` | gateway → persister |
| `message.persisted` | persister → ai-router, dashboard-api (broadcast) |
| `ai.intent.extracted` / `ai.review.flagged` | ai-router → persister, lead-service, agent-worker |
| `lead.updated`, `visit.planned` | lead-service → agent-worker, dashboard |
| `followup.due` | followup-scheduler → agent-worker |
| `agent.reply.proposed`, `approval.requested` | agent-worker → dashboard |
| `approval.decided` | dashboard-api → agent-worker (learning) |
| `outbound.reply.requested` | agent-worker / dashboard-api → dispatch |
| `outbound.message.sent`, `message.status` | dispatch → persister, lead-service |

Each topic has a `.DLQ` sibling. The **partition key** is `tenant_id:worker_id`, which keeps per-conversation ordering.
The **consumer contract** applies to every consumer:
- the tenant header is required, must be authorised, and must match the body;
- `event_id` idempotency is checked in Redis;
- failures are retried with backoff, then sent to the DLQ;
- offsets are committed manually after the handler succeeds.

## 5. Key flows

**5.1 Inbound customer question → reply**
1. Meta POSTs a webhook. The gateway verifies the HMAC, resolves the tenant and worker from `phone_number_id`, dedupes the `wamid`, and publishes the event.
2. The persister upserts the customer, conversation and message, then emits `message.persisted`. The dashboard updates live over SSE.
3. The ai-router classifies the message. Tier 0 keywords handle cheap cases; otherwise Gemini returns structured JSON. Low confidence becomes `ai.review.flagged`.
4. The lead-service opens or updates the lead, using the ad referral as the source, and plans a visit if a date is mentioned (for example, "Saturday shaam").
5. The agent-worker picks a task:
   - **price:** match against the tenant's `pricing.csv`, with a disclaimer and a staleness factor;
   - **FAQ:** a learned example if one matches; otherwise zuvo-ai-rag when the tenant uses `knowledge.retrieval: rag`; otherwise lexical matching.
6. Gemini rewrites the grounded facts in the customer's language. A **number guard** rejects any price or figure that isn't in the facts.
7. The worker decides:

   | Condition | Result |
   |---|---|
   | confidence below the tenant threshold, mode `draft`, learning gate closed, or message flagged | `PENDING_APPROVAL` |
   | otherwise | `AUTO_SENT` → dispatch → Meta |

**5.2 Approval and learning:** the owner approves, edits or rejects the draft in the dashboard. The reply is sent, and `approval.decided` records a `learned_example` and updates `autonomy_stats`. After N approvals at a high rate, a topic is promoted to auto-reply; a rejection demotes it.

**5.3 Follow-ups:** visit plans create `visit_reminder` and `visit_noshow` tasks. The scheduler emits `followup.due`, and the agent-worker sends a template, respecting the 24-hour window, opt-out and quiet rules. Leads with no activity are swept to dormant and `revive_offer` follow-ups are offered.

**5.4 Knowledge update:**
1. Edit `tenants/<slug>/knowledge/*.md` and run the migrator; this updates `kb_documents.body` and `checksum`.
2. Within about 10 seconds the RAG indexer chunks the text, embeds it with Gemini, and writes `kb_chunks.embedding`.
3. Dashboard uploads (`uploads/…`) go through the RAG API instead.

**5.5 Tenant onboarding:** copy `tenants/_template`, fill in the config, skill, pricing and knowledge, and add the numbers to `config/tenant-routing.yaml`. Running the migrator then validates and applies the bundle and creates the users.

## 6. Data architecture (PostgreSQL 16 + pgvector)

| Domain | Tables |
|---|---|
| Tenancy & identity | `tenants`, `workers`, `whatsapp_numbers`, `users`, `memberships`, `tenant_config_versions` |
| Messaging | `customers`, `conversations`, `messages` (AI status, extraction, delivery), `audit_logs` |
| Commerce knowledge | `price_lists`, `price_items`, `kb_documents` (body, index state), `kb_chunks` (text, `embedding vector(768)`, `tsv`) |
| Leads | `campaigns`, `leads`, `lead_events`, `visit_plans`, `followup_tasks` |
| Agent & learning | `agent_runs` (retrieved context, confidence, decision, tokens, cost), `approvals`, `learned_examples`, `autonomy_stats` |
| Metrics | `metrics_daily`, `ai_usage_daily` |

- Every tenant table has a `tenant_id` column with a forced RLS policy `tenant_isolation`. Services connect as `zuvo_app` (`NOBYPASSRLS`); only the migrator uses the owner role.
- Schema is managed by Alembic: `0001` (core), `0002` (Phase 1 leads), `0003` (RAG / pgvector).
- Indexes for knowledge search: HNSW cosine on `kb_chunks.embedding`, GIN on `kb_chunks.tsv`.
- Redis holds only short-lived state: dedupe and idempotency keys, and the query-vector cache.
- Kafka is the transport, not a store (7-day retention).

## 7. AI design

| Use | Model | Grounding and safety |
|---|---|---|
| Intent / NER | `ZUVO_GEMINI_MODEL` (Flash-Lite), JSON `response_schema` | Tier 0 keywords first; confidence threshold per tenant policy; review flag |
| Reply wording | Same model | Sees only FACTS (price rows or retrieved chunks); number guard; falls back to deterministic text |
| Knowledge retrieval | `gemini-embedding-001` @ 768, pgvector hybrid + RRF | Similarity floor 0.60 (calibrated on the golden set); below the floor → holding reply; optional LLM rerank (off) |
| Learning | No model training | Owner-approved examples are reused verbatim; per-topic autonomy promotion and demotion |

## 8. Security

- **Ingress:**
  - only `gateway` (webhook) and `frontend` are published; `dashboard-api` binds to localhost and `zuvo-ai-rag` is internal;
  - Meta webhooks require a valid `X-Hub-Signature-256` HMAC;
  - dashboard calls use HS256 JWTs bound to tenant memberships;
  - zuvo-ai-rag requires a Bearer service token, checked in constant time.
- **Isolation:** tenant enforced at the event contract, at the database (RLS) and at the API (membership check).
- **Secrets** come only from `.env` (gitignored) or a secret manager; there are no defaults in compose.
- **Privacy:** phone numbers are masked in logs, and audit details are masked. Knowledge-base text and customer messages are sent to Google Gemini, which must be covered in the data-processing agreement.
- **Known item:** Meta secrets from early commits must be rotated.

## 9. Reliability and observability

- **Delivery semantics:** at-least-once consumption with idempotent handlers, an at-most-once WhatsApp send, and DLQs for poison messages.
- **Degraded modes:**

  | Failure | Behaviour |
  |---|---|
  | Gemini down | `AI_PENDING` or deterministic replies |
  | RAG down | lexical fallback |
  | Meta down | send error recorded and shown in the dashboard |

- **Health:** HTTP health checks on `gateway`, `dashboard-api` and `zuvo-ai-rag`; compose restarts failed services with `restart: unless-stopped`.
- **Observability:**
  - structured JSON logs;
  - `agent_runs` holds the per-decision trace (retrieved context, scores, latency, tokens, cost);
  - `metrics_daily` and `ai_usage_daily` feed the dashboard insights.

## 10. Deployment

| Stage | Model |
|---|---|
| **Now (a couple of tenants)** | Docker Compose on one VM (2 vCPU / 4 GB, 30 GB disk; AWS `t4g.medium` or Lightsail 4 GB is about $20–24 a month). Images come from a registry; HTTPS via Caddy or a named Cloudflare tunnel; nightly `pg_dump` to object storage; Kafka heap capped at 512 MB. |
| **Growth (about 20+ tenants, HA needed)** | Kubernetes (EKS or GKE) with Helm. Managed Postgres with pgvector (RDS or Cloud SQL), managed Kafka or Redpanda, horizontal scaling of consumers by partition. |

Measured footprint: about 1.7 GB RAM in total (Kafka about 1 GB, each Python service 50–95 MB), with CPU under 2% idle.
Variable cost is about $0.00025 of Gemini per inbound message. WhatsApp template messages are billed separately by Meta.

## 11. Scalability path

- **Consumers:** scale out via Kafka consumer groups, up to 12 partitions per topic; for example `docker compose up --scale ai-router=3`.
- **Retrieval:** stays per tenant. Moving zuvo-ai-rag to a dedicated vector store only matters at millions of chunks.
- **Single points of failure today:** Kafka, Postgres and the VM. Fix these with managed services before onboarding SLA-bound customers.

## 12. Key decisions

| Decision | Rationale |
|---|---|
| Kafka microservices instead of a monolith | Isolated failure domains, independent scaling, replayable events, DLQs |
| Postgres RLS for multi-tenancy | Isolation enforced by the database, not only by application code |
| Configuration bundles per tenant | Onboarding without code; versioned in git (`tenant_config_versions`) |
| Gemini Flash-Lite instead of self-hosted OVMS/Qwen | No GPU or ops cost, Hinglish quality, pay per use |
| pgvector instead of Chroma | One datastore, RLS isolation, transactional with documents |
| Learning by approved examples, not fine-tuning | Explainable, immediate, reversible, and the owner stays in control |
| Docker Compose now, Kubernetes later | Cost and simplicity at current scale; services are already stateless and Kafka-decoupled |

## 13. Open items

- Confirm the licence of the code imported from the smart-kiosk-assistant `rag-service`.
- Persist ai-router token usage so per-tenant monthly AI cost can be shown.
- Owner WhatsApp approval alerts (`notify_owner_whatsapp`); delivery ticks (`message.status`).
- Production auth (OTP or SSO) to replace dev login; rotate leaked Meta secrets.
- Production compose overlay, backups, TLS ingress, monitoring.
