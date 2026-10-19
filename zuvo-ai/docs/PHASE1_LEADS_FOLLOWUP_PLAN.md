# Phase 1 (Generic): Lead Follow-up, Visit Tracking, Pricing Answers and Learning from Approvals

Status: **implemented** (backend, dashboard, onboarding; see §10). Planned 2026-10-17. It supersedes the capability levels in `PLAN_CONFIG_DRIVEN_AGENT.md` for Phase 1.

## 1. Problem and scope

Businesses run digital marketing (Meta/Instagram click-to-WhatsApp ads, Google, IndiaMART and so on), and leads arrive on WhatsApp. Today they lose leads because nobody follows up on time, and nobody tracks who said "I'll visit on Saturday".

Phase 1 is **generic for every industry**: Alpha Automobile, Jaipur Furniture, and anyone else onboarded by config.

| # | Capability | Phase 1 behaviour |
|---|---|---|
| C1 | **Lead capture + attribution** | Every new WhatsApp contact becomes a lead. The Meta `referral` block (ad id, headline, source URL, `ctwa_clid`) gives the campaign source. Stage pipeline: NEW → ENGAGED → QUALIFIED → VISIT_PLANNED → VISITED → WON / LOST / DORMANT |
| C2 | **Automated follow-up** | Config-driven cadence: nudge after no reply, a reminder before a planned visit, a post-visit thank-you or check-in, and revival of dormant leads. Inside the 24-hour window the agent replies with free text. Outside it, only Meta-approved templates are used |
| C3 | **Visit-intent extraction** | "Saturday ko aayenge", "15 tareekh tak", "next week showroom dekhne" become `visit_plans` with a resolved date, a precision (exact / weekday / week / month) and a confidence. Dates are resolved in the tenant's timezone, relative to the message time |
| C4 | **Follow-up metrics on the dashboard** | Visits planned today and this week, overdue follow-ups, no-shows, first-response time, follow-up reply rate, and lead → visit → won conversion by campaign, number and worker |
| C5 | **Pricing answers** | The agent answers from the tenant's `pricing.csv`, both direct ("Creta ka on-road price?") and indirect ("budget 8 lakh, kya milega?"). When the item is ambiguous it asks a clarifying question (variant, size, material). **Low confidence, a missing price or a discount request → human approval** |
| C6 | **Learning from approvals** | Every approve, edit or reject is stored. Approved answers become **per-tenant examples** the agent retrieves next time. Each topic's auto-send threshold adapts from the approval history. There is no model fine-tuning in Phase 1 |
| C7 | **Easy onboarding** | One folder per tenant (`skill.md`, `config.yaml`, `pricing.csv`, `knowledge/`), then `onboard_tenant.py --validate` and then `--apply` |
| C8 | **Tenant-scoped dashboard** | Every view is filtered by the JWT's tenant claim and DB row-level security (RLS). Only a platform admin can switch tenants |

**Out of Phase 1:** ERP or order integrations, payments, voice notes, fine-tuned models, and per-tenant custom code.

## 2. Tenant bundle (the only per-business artefacts)

```
tenants/alpha-automobile/
  skill.md          # behaviour: persona, tone, what to ask, what never to say (prose for the agent)
  config.yaml       # structured: numbers, hours, timezone, follow-up cadence, thresholds, templates, stages
  pricing.csv       # item, variant attrs, price / price range, unit, validity
  knowledge/        # *.md / *.pdf / *.txt — FAQs, address, offers, finance/EMI, warranty, delivery
```

**`config.yaml` (example)**

```yaml
tenant: { slug: alpha-automobile, name: Alpha Automobile, industry: automobile_dealer,
          timezone: Asia/Kolkata, currency: INR, languages: [hinglish, hindi, english] }
numbers:
  - { meta_phone_number_id: "1340933852445316", display: "+17110219313", label: Sales, default_worker: rahul-sales }
workers:
  - { key: rahul-sales, name: Rahul, role: SALES_WORKER }
  - { key: owner, name: Ajay, role: OWNER, whatsapp: "+9198xxxxxxx" }   # receives approval alerts
business_hours: { days: Mon-Sat, open: "10:00", close: "19:30" }
lead:
  stages: [NEW, ENGAGED, QUALIFIED, VISIT_PLANNED, VISITED, WON, LOST, DORMANT]
  qualify_fields: [interest, budget, timeline, location]   # agent tries to collect these
  dormant_after_days: 14
followups:                      # cadence rules; the engine creates followup_tasks from these
  - { id: no_reply_nudge,  when: "no customer reply", after: 24h, max: 2, mode: auto, template: lead_nudge_v1 }
  - { id: visit_reminder,  when: "visit planned",     before: 18h, mode: auto, template: visit_reminder_v1 }
  - { id: visit_noshow,    when: "visit date passed and not marked visited", after: 4h, mode: draft }
  - { id: post_visit,      when: "marked visited",    after: 2h,  mode: draft }
  - { id: dormant_revive,  when: "stage=DORMANT",     after: 30d, mode: draft, template: revive_offer_v1 }
pricing:
  source: pricing.csv
  answer_mode: auto              # auto | draft
  confidence_auto_min: 0.85      # below → approval
  always_approval_if: [discount_request, price_missing, multiple_matches]
  disclaimer: "Ex-showroom price, valid till {valid_until}. On-road varies by RTO/insurance."
approvals:
  sla_minutes: 15
  on_timeout: send_holding_reply  # "Our team will confirm shortly"
learning:
  enabled: true
  min_examples_before_auto: 5     # topic must have ≥5 approved-unchanged answers to become auto-eligible
  auto_promote_rate: 0.9          # ≥90% approved-unchanged over the last 20
  demote_on_reject: true
meta_templates: [lead_nudge_v1, visit_reminder_v1, revive_offer_v1]   # must be approved in Meta
```

**`pricing.csv` (generic columns; industry-specific attributes go in `attrs`)**

```
sku,name,category,attrs,price,price_min,price_max,unit,tax_included,valid_until,aliases,notes
CRETA-SX-P,Hyundai Creta SX Petrol,SUV,"{""fuel"":""petrol"",""trans"":""MT""}",1450000,,,unit,false,2026-11-30,"creta sx|creta petrol",ex-showroom
SOFA-3S-TEAK,3 Seater Teak Sofa,Sofa,"{""material"":""teak"",""seats"":3}",,42000,58000,unit,true,,"teak sofa|3 seater",price varies by fabric
```

## 3. Data model (all tables have `tenant_id` + RLS; ★ = new, ✎ = change)

### A. Tenancy and configuration

| Table | Key columns | Purpose |
|---|---|---|
| ✎ `tenants` | + `industry`, `timezone`, `status` (ONBOARDING/ACTIVE/PAUSED), `plan`, `monthly_ai_budget_inr`, `onboarded_at` | One row per business |
| ★ `tenant_config_versions` | `id, tenant_id, kind` (skill/config/pricing/knowledge), `version, checksum, content jsonb/text, status` (DRAFT/ACTIVE/ARCHIVED), `created_by, created_at` | The files are the source; the DB holds the validated **active snapshot**. Allows audit, rollback, and "which config produced this reply" |
| ✎ `whatsapp_numbers` | + `waba_id, label, purpose` (sales/service), `default_worker_id` | Multiple numbers per tenant |
| `workers` | (existing) + `whatsapp_phone` | Staff (sales, owner) |
| ★ `users` | `id, email/phone, password_hash/sso_sub, is_platform_admin` | Dashboard login, not tenant-scoped |
| ★ `memberships` | `user_id, tenant_id, role` (OWNER/MANAGER/SALES_WORKER), `worker_id` | A user can belong to one or more tenants; the JWT carries one active tenant |

### B. Pricing and knowledge

| Table | Key columns | Purpose |
|---|---|---|
| ★ `price_lists` | `id, tenant_id, version, source_file, row_count, imported_at, is_active` | One row per CSV upload; old versions are kept |
| ★ `price_items` | `id, tenant_id, price_list_id, sku, name, category, attrs jsonb, price, price_min, price_max, currency, unit, tax_included, valid_until, aliases text[], notes, search tsvector` | Searchable price catalogue (trigram + full-text) |
| ★ `kb_documents` | `id, tenant_id, title, source_path, version, checksum, updated_at` | Knowledge-base files |
| ★ `kb_chunks` | `id, tenant_id, document_id, ordinal, text, embedding vector(768), tsv` | Retrieval for FAQ answers (pgvector + full-text hybrid) |

### C. Contacts, leads and campaigns

| Table | Key columns | Purpose |
|---|---|---|
| ✎ `customers` (contacts) | + `source, language, tags text[], opted_out bool, opted_out_at, consent_at` | One per phone per tenant; honours STOP / opt-out |
| ★ `campaigns` | `id, tenant_id, platform` (meta_ctwa/google/indiamart/manual), `external_id` (ad/campaign id), `name, started_at, spend_inr` | Attribution, plus ROI later |
| ★ `leads` | `id, tenant_id, customer_id, whatsapp_number_id, assigned_worker_id, campaign_id, referral jsonb, interest, budget_min, budget_max, timeline_text, location, stage, score, lost_reason, first_contact_at, first_response_at, last_customer_msg_at, last_business_msg_at, next_followup_at, closed_at` | **The core Phase 1 entity.** One open lead per customer per tenant; a new one is opened after WON or LOST |
| ★ `lead_events` | `id, tenant_id, lead_id, type` (stage_change/assigned/note/field_extracted), `from, to, actor` (agent/user/system), `message_id, created_at` | Timeline and funnel analytics |

### D. Visits and follow-ups

| Table | Key columns | Purpose |
|---|---|---|
| ★ `visit_plans` | `id, tenant_id, lead_id, source_message_id, raw_text, resolved_date, resolved_time_window, precision` (EXACT/WEEKDAY/WEEK/MONTH), `confidence, status` (PLANNED/CONFIRMED/VISITED/NO_SHOW/RESCHEDULED/CANCELLED), `outcome_by, outcome_at` | Created from "Saturday ko aayenge"; a reschedule closes the old plan and links a new one |
| ★ `followup_tasks` | `id, tenant_id, lead_id, rule_id, kind, due_at, mode` (auto/draft/manual), `channel` (whatsapp/call), `template_name, status` (SCHEDULED/PENDING_APPROVAL/SENT/SKIPPED/DONE/FAILED/CANCELLED), `attempt, skip_reason, result_message_id, created_at, completed_at` | Generated from `config.followups`. Skipped automatically if the customer replied, opted out, or the lead closed |

### E. Agent decisions, approvals and learning

| Table | Key columns | Purpose |
|---|---|---|
| ★ `agent_runs` | `id, tenant_id, message_id, lead_id, intent, task` (reply/followup/price), `retrieved` jsonb (price_items, kb_chunks, examples), `proposed_text, confidence, decision` (AUTO_SENT/NEEDS_APPROVAL/HANDOFF/SKIPPED), `decision_reasons text[], model, tokens_in, tokens_out, cost_inr, latency_ms, config_version_ids` | Explainability ("why did the agent say this?") and cost |
| ★ `approvals` | `id, tenant_id, agent_run_id, kind` (reply/price/discount/followup), `status` (PENDING/APPROVED/EDITED/REJECTED/EXPIRED), `proposed_text, final_text, edit_distance, reject_reason, reviewer_user_id, sla_due_at, decided_at` | Human-in-the-loop queue |
| ★ `learned_examples` | `id, tenant_id, intent, topic` (e.g. price:SUV, faq:emi), `question_text, answer_text, price_item_ids, embedding vector(768), source_approval_id, outcome` (APPROVED/EDITED), `weight, active, use_count, last_used_at` | **Per-tenant memory.** Retrieved as few-shot examples for similar questions. Edited answers carry more weight |
| ★ `autonomy_stats` | `tenant_id, intent, topic, window_n, approved_unchanged, edited, rejected, auto_enabled, threshold, updated_at` | Adaptive gate: a topic auto-promotes after repeated unchanged approvals and demotes on a rejection |

### F. Messaging and metrics

| Table | Key columns | Purpose |
|---|---|---|
| ✎ `messages` | + `lead_id, agent_run_id, sent_by` (customer/human/agent/followup), `referral jsonb, delivery_status, delivered_at, read_at` | Links the chat to the lead and agent; delivery ticks |
| `conversations`, `audit_logs` | (existing) | 24-hour window (`last_inbound_at`), append-only audit |
| ★ `metrics_daily` | `tenant_id, date, whatsapp_number_id, worker_id, campaign_id, new_leads, first_response_p50_s, followups_due, followups_sent, followups_replied, visits_planned, visits_due, visits_done, no_shows, won, lost, approvals_pending, auto_reply_rate` | Rolled up every 5 minutes or nightly; the dashboard reads it for fast tenant views |
| ★ `ai_usage_daily` | `tenant_id, date, model, calls, tokens_in, tokens_out, cost_inr` | Budget cap and cost per tenant |

**Isolation:** every table has the `tenant_id` column, the RLS policy `tenant_isolation`, an index starting with `tenant_id`, and runs under the `zuvo_app` role (NOBYPASSRLS). These conventions already exist.

## 4. Key flows

**4.1 Inbound lead message**

```
gateway (referral → campaign) → persister (customer, lead upsert, first_contact_at) → ai-router
  Gemini extraction (one call; extended schema):
    intent, lead_fields{interest,budget,timeline,location},
    visit{text, date_expr, resolved_date, precision, confidence},
    price_query{item_text, attrs, is_direct, discount_requested}
  → lead-service: stage transition, visit_plans upsert, followup_tasks (re)schedule, lead_events
  → agent-worker (if a reply is needed): answer or approval
```

**4.2 Pricing answer and confidence**

1. Match `price_query` against `price_items` using full-text, trigram, aliases and attributes.
2. **Clarify** if several variants match (for example, "Petrol or diesel? Manual or automatic?").
3. Compute `confidence = extraction_conf × match_score × (price present ? 1 : 0) × staleness_factor(valid_until)`.
4. Retrieve `learned_examples` for the same tenant, intent and topic as few-shot examples.
5. Gemini drafts the reply. **Numbers are copied only from `price_items`.** A post-check rejects any figure in the draft that isn't in the retrieved rows.
6. Decide:
   - send automatically if `confidence ≥ threshold(topic)`, `autonomy_stats.auto_enabled` is true, and no `always_approval_if` rule fires;
   - otherwise create an **approval**: the owner gets a WhatsApp alert, the dashboard shows an inbox badge, and when the SLA times out a holding reply is sent.

**4.3 Learning loop (no fine-tuning)**

| Owner action | Stored | Effect next time |
|---|---|---|
| Approve unchanged | `learned_examples` (weight 1), `autonomy_stats.approved_unchanged++` | Similar question retrieves this answer. The topic moves toward auto |
| Edit then send | Example with `final_text` (weight 2), `edited++` | The agent mimics the owner's wording and facts. It stays in draft until edits become rare |
| Reject + reason | `rejected++`, the topic is **demoted to approval**, the reason is shown in the prompt | Avoids repeating the mistake |

The `learning` config caps how quickly a topic can be promoted, so the system never auto-sends a brand-new topic.

**4.4 Follow-up engine (new `followup-scheduler` container)**

- Every minute, pick due `followup_tasks` with `FOR UPDATE SKIP LOCKED`.
- Re-check guards: the customer replied, opted out, the lead closed, outside business hours, the tenant budget is exhausted.
- **Inside the 24-hour window:** the agent drafts a contextual message. **Outside it:** use the configured Meta template.
- Follow the mode: `auto` publishes `outbound.reply.requested`; `draft` creates an approval.
- Record the result, then reschedule the next rule.

**Cost note:** in India, Meta marketing templates cost about ₹0.8 per message and utility templates about ₹0.12. Visit reminders should be utility templates.

## 5. Services (no monolith)

| Container | New/changed | Role |
|---|---|---|
| gateway | ✎ | Parse `referral`; persist delivery statuses |
| persister | ✎ | Lead upsert, `sent_by`, delivery ticks |
| ai-router | ✎ | Extended extraction schema (lead fields, visit, price_query) |
| ★ lead-service | new | Stage machine, visit plans, schedules follow-up tasks, metrics rollup |
| ★ agent-worker | new (qsr-agent-js) | Pricing and FAQ answers, retrieval (price, KB, examples), confidence gate, approval creation |
| ★ followup-scheduler | new | Executes due tasks under the 24-hour window and template rules |
| dashboard-api | ✎ | Leads, visits, follow-ups, approvals, learning, metrics and config endpoints (tenant from JWT) |
| dispatch | ✎ | Template sends, retry/429, statuses |
| ★ onboarding CLI / job | new | Validate the bundle, then apply: tenant, numbers, workers, config versions, pricing import, KB chunk + embed |

New Kafka topics:
- `lead.updated`
- `visit.planned`
- `followup.due`
- `agent.reply.proposed`
- `approval.requested`
- `approval.decided`
- `message.status`

## 6. Dashboard (tenant-scoped)

| View | Content |
|---|---|
| **Today** (home) | KPI cards: new leads, avg first response, follow-ups due/overdue, visits today and this week, approvals pending, AI auto-reply % |
| **Leads** | Pipeline kanban by stage, with filters (campaign, number, worker, interest, date). Lead drawer: chat, extracted fields, visit plans, follow-up timeline |
| **Visits** | Calendar or week view of planned visits; mark Visited / No-show / Rescheduled in one click (feeds the metrics) |
| **Follow-ups** | Queue of due and overdue tasks, with send/skip and the outcome |
| **Approvals** | Inbox: customer question, proposed reply, sources (price rows, KB), confidence and reason, with **Approve / Edit & send / Reject (reason)** |
| **Insights** | Funnel lead → engaged → visit planned → visited → won by campaign and worker; no-show rate; follow-up reply rate; AI learning (auto-enabled topics, approval trend); AI cost |
| **Settings** | Active config versions, upload a new `pricing.csv` (diff preview, then activate), KB files, Meta templates status |

Role-based access: OWNER sees everything; MANAGER sees everything except billing; SALES_WORKER sees only their own numbers and leads.

## 7. Onboarding a new tenant (target: under 30 minutes)

1. `cp -r tenants/_template tenants/jaipur-furniture`, then edit `config.yaml` and `skill.md`, drop in `pricing.csv`, and add `knowledge/*.md`.
2. Run `python scripts/onboard_tenant.py tenants/jaipur-furniture --validate`. It checks:
   - the schema;
   - that phone number ids are unique across tenants;
   - the CSV columns, price sanity and duplicate SKUs;
   - that the referenced Meta templates exist.
3. Run `--apply`. It upserts the tenant, numbers and workers, imports the price list, chunks and embeds the KB, activates the config versions and creates the owner login. **It is idempotent**: re-running only applies changes.
4. In Meta, point the WABA webhook at the same callback URL; routing is by `phone_number_id`.
5. Start in **draft** mode for everything. After a week the learning stats show which topics are safe to auto-send.

## 8. Phase 1 delivery plan (≈4–5 weeks)

| Sprint | Deliverables | Demo |
|---|---|---|
| **1.1 Foundations** (1 wk) | Tenant bundle schema + onboarding CLI; `tenant_config_versions`, `users`/`memberships`; migrate the existing two tenants and add Alpha Automobile + Jaipur Furniture; JWT tenant scoping | Onboard Jaipur Furniture from a folder in under 30 minutes |
| **1.2 Leads + visits** (1 wk) | `campaigns`, `leads`, `lead_events`, `visit_plans`; referral parsing; extended extraction (lead fields, visit date resolution); lead-service; Leads + Visits views | A Saturday visit message appears in the Visits calendar with its lead stage |
| **1.3 Follow-ups + metrics** (1 wk) | `followup_tasks`, scheduler, template sends, opt-out; `metrics_daily`; Today + Follow-ups + Insights views | No-reply nudge and visit reminder go out; the funnel and no-show metrics update |
| **1.4 Pricing + approvals** (1 wk) | `price_lists/items`, `kb_*`, agent-worker, confidence gate, `agent_runs`, `approvals`, owner WhatsApp alert, Approvals inbox | A direct price question is answered automatically; an ambiguous or discount question goes to approval |
| **1.5 Learning + hardening** (≈1 wk) | `learned_examples`, `autonomy_stats`, retrieval few-shot, promote/demote; budget cap; golden test chats per tenant; delivery ticks | Edit an answer twice; the third similar question uses the owner's wording; the topic auto-promotes after the threshold |

## 9. Decisions needed

| # | Question | Recommendation |
|---|---|---|
| Q1 | Embeddings for KB and examples | Gemini `text-embedding` (same key) + **pgvector** in the existing Postgres |
| Q2 | Where the bundles live | Git folder for now; dashboard upload writes a new `tenant_config_versions` row later |
| Q3 | Approval alerts | Dashboard plus a WhatsApp message to the owner's number (one-tap link) |
| Q4 | Who marks "Visited" | A sales worker in the dashboard (Phase 1); a QR/check-in link later |
| Q5 | Follow-up message cost | Utility templates for reminders; marketing templates only for revival, which is opt-in per tenant |

## 10. Implementation status

Decisions Q1–Q5 were taken as recommended, with the deviations listed below.

| Capability | Where | Notes |
|---|---|---|
| C1 lead capture and attribution | gateway (`referral`), persister, `lead_service` | `ctwa_ad` and other sources come from the referral; campaigns are upserted by ad id or headline |
| C2 follow-ups | `lead_service` schedules, `followup_scheduler` executes, `dispatch` sends | Claims use `FOR UPDATE SKIP LOCKED`. Guards: lead closed, opted out, or customer replied after a no-reply rule fired. Free text inside 24 h, template outside; no template means `NEEDS_ACTION` |
| C3 visit intent | `zuvo_common/dates.py` `resolve_visit` | Weekday, "kal/parso", "next week", month+day, numeric dates, day-only ("20th", "5 tarikh"), time words. Schedules `visit_reminder` + `visit_noshow` |
| C4 metrics | `zuvo_common/metrics.py`, rolled up every 300 s by the scheduler into `metrics_daily` | `/dashboard/today`, `/insights?days=` |
| C5 pricing | `zuvo_common/pricing.py`, `agent_worker` | Deterministic CSV match (catalogue-vocabulary coverage + stopwords); Gemini only words the reply around the retrieved facts. A low score, discount request or stale price goes to approval with a holding reply after the SLA |
| C6 learning | `zuvo_common/learning.py`, `learned_examples`, `autonomy_stats` | Approve gives weight 1, edit gives weight 2, reject records a negative. A topic auto-sends only after `min_examples_before_auto` approvals at or above the approval rate; until then **every reply stays a draft** |
| C7 onboarding | `scripts/onboard_tenant.py`, migrator bundle apply | `--validate` / `--apply` / `--all`; idempotent |
| C8 tenant dashboard | `dashboard_api/phase1.py`, `frontend/` | Email/password login, `switch-tenant` only for multi-membership users; RLS on every query; a cross-tenant read returns 404 |
| Opt-out | `zuvo_common/leads.py` `is_opt_out` | Config keywords, whole-message match. Cancels follow-ups, and dispatch refuses non-human sends |
| Budget cap | `agent_worker` | Stops drafting with Gemini when month spend ≥ `monthly_ai_budget_inr` |

**Deviations / not yet done**
- Retrieval for KB and examples is **lexical** (token similarity), not pgvector embeddings (Q1). That is enough for small KBs; embeddings remain the upgrade path.
- Owner approval alerts are dashboard-only (SSE badge); `notify_owner_whatsapp` exists in config but nothing sends it yet (Q3).
- A pricing upload through Settings writes a new price list, but the next `migrator` run re-applies the bundle's `pricing.csv` if it differs. Keep the bundle file in sync, or edit only through the bundle.
- Meta delivery and read ticks (`message.status`) are not persisted yet.
- Simulator lines (`PNID_*`, non-numeric) never call Meta; dispatch returns `wamid.SIM.*`.

**Verification**
- `pytest tests/unit`: 40 pass.
- `tests/e2e`: 6 pass.
- `scripts/e2e_phase1.py` runs the full flow: login, ad lead, price approval with edit, learned example, visit and follow-ups, dashboard, opt-out, tenant isolation.
