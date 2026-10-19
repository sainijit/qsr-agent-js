# ZuvoCloud: WhatsApp AI Operations Platform

ZuvoCloud is a multi-tenant WhatsApp operations hub for Indian distributors. Each business connects several worker WhatsApp lines. Inbound messages are attributed to the right tenant, worker and customer. Gemini Flash-Lite classifies Hinglish, Hindi and English orders. Owners watch everything live in the Owner Command Center.

The system is event-driven: Kafka connects small single-purpose containers, and Postgres Row-Level Security enforces tenant isolation.

```
Meta ──▶ gateway ──▶ [inbound.message.received] ──▶ persister ──▶ Postgres (RLS)
                                                        │
                                       [message.persisted] ──▶ dashboard-api ──SSE──▶ frontend
                                                        │
                                                        ▼
                                                    ai-router ──(Gemini Flash-Lite)
                                                        │
                       [ai.intent.extracted | ai.review.flagged] ──▶ persister
frontend ──▶ dashboard-api ──▶ [outbound.reply.requested] ──▶ dispatch ──▶ Meta
                                         [outbound.message.sent] ──▶ persister
```

**Phase 1: leads, follow-ups, pricing, learning** (`docs/PHASE1_LEADS_FOLLOWUP_PLAN.md`)

```
[ai.intent.extracted] ─▶ lead-service ─▶ leads / visit_plans / followup_tasks ─▶ [lead.updated | visit.planned]
                      └▶ agent-worker ─▶ price/KB (zuvo-ai-rag or lexical)/example retrieval + Gemini wording ─▶ auto-send or [approval.requested]
followup-scheduler ─(due tasks, holding replies, dormant sweep, metrics rollup)─▶ [followup.due] ─▶ agent-worker ─▶ dispatch
dashboard approve/edit/reject ─▶ [outbound.reply.requested] + [approval.decided] ─▶ learned_examples / autonomy_stats
```

## Layout

| Path | What |
|---|---|
| `libs/zuvo_common/` | Shared library used by all services: events/envelope, Kafka bus, consumer contract (tenant header, idempotency, DLQ), tenancy, RLS sessions, JWT/HMAC, models |
| `services/gateway/` | Meta webhook ingress and dev simulator |
| `services/persister/` | The only writer of messages/customers/conversations |
| `services/ai_router/` | Tier 0 keywords, then Gemini structured NER |
| `services/dispatch/` | Meta Cloud API sender (24 h window, at-most-once, dry-run default) |
| `services/dashboard_api/` | Auth, tenant-scoped reads, SSE, outbound requests |
| `services/lead_service/` | Lead stage machine, visit plans, follow-up scheduling, opt-out |
| `services/agent_worker/` | Pricing/FAQ answers, confidence gate, approvals, learning from decisions |
| `services/followup_scheduler/` | Executes due follow-ups, SLA holding replies, dormant sweep, `metrics_daily` rollup |
| `zuvo-ai-rag/` | Internal multi-tenant knowledge retrieval: Gemini embeddings + pgvector hybrid search ([README](zuvo-ai-rag/README.md)) |
| `services/migrator/` | One-shot: DB role, Alembic migrations, routing sync, tenant bundle apply |
| `tenants/<slug>/` | Per-business bundle: `config.yaml`, `skill.md`, `pricing.csv`, `knowledge/*.md` (copy `tenants/_template`) |
| `frontend/` | React Owner Command Center served by nginx |
| `config/tenant-routing.yaml` | Number → tenant → worker map (hot-reloaded) |
| `qsr/<slug>/skill.yaml` | Per-tenant policy (intents, thresholds, guardrails, tier-0 keywords) |
| `tests/unit`, `tests/e2e` | In-memory unit tests, and e2e tests against the running stack |

## Run

```bash
cp .env.example .env        # fill in secrets; set GEMINI_API_KEY
./start_all.sh              # = docker compose up -d --build
open http://localhost:3000  # owner@<slug>.demo / Zuvo@2026, or a dev identity (AUTH_DEV_LOGIN=true)
```

### Onboard a new business

```bash
cp -r tenants/_template tenants/my-shop      # edit config.yaml (tenant id/slug, numbers, workers, follow-up rules), skill.md, pricing.csv, knowledge/
python3 scripts/onboard_tenant.py tenants/my-shop --validate
python3 scripts/onboard_tenant.py tenants/my-shop --apply --password owner@my-shop.demo=ChangeMe1   # idempotent
# or simply: docker compose up migrator   (applies every bundle under tenants/)
```

Every reply starts as a **draft for approval**. A topic auto-sends only after enough owner approvals (`learning.min_examples_before_auto`).
To change prices, edit the bundle's `pricing.csv`; the migrator re-applies it on each run.

Host ports:
- `3000`: UI
- `8000`: gateway, the Meta webhook / ngrok target
- `127.0.0.1:8001`: dashboard-api, for `npm run dev`

Postgres, Redis and Kafka are internal only.

## Test

```bash
pip install -e libs/zuvo_common fastapi httpx pytest pytest-asyncio
pytest                                          # unit tests (no Docker needed)
set -a; . ./.env; set +a
ZUVO_E2E_URL=http://localhost:3000 pytest tests/e2e   # needs the stack running
python3 scripts/e2e_phase1.py                   # Phase 1 flow: login, ad lead, price approval, learning, visit, follow-ups, opt-out, isolation
python3 scripts/e2e_rag.py [--rag-down]         # FAQ via zuvo-ai-rag on Alpha; --rag-down checks lexical fallback
python3 scripts/e2e_local.py                    # hop-by-hop trace of one Meta-signed message (no public URL needed)
python3 scripts/e2e_local.py --text "Payment RTGS kar diya" --line PNID_003
scripts/cf_tunnel.sh start [--new]                # public HTTPS URL for the gateway only (Cloudflare quick tunnel; works behind HTTPS_PROXY)
scripts/cf_tunnel.sh status | stop
```

## Security notes

- Secrets live only in `.env`, which is gitignored. Compose has no secret defaults.
- Services connect as `zuvo_app` (NOBYPASSRLS), and every query runs with `app.current_tenant` set.
- Webhook HMAC verification is on by default. The simulator and dev login are dev-only flags.
- `META_DRY_RUN=true` by default, so no real WhatsApp sends happen until you turn it off.
- Earlier commits contained Meta secrets. Rotate them in Meta Business Manager.

See `docs/ARCHITECTURE.md` and `docs/INTEGRATION_PLAN_QSR_AGENT.md`.
