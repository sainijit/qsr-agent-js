# Config-Driven Multi-Tenant Reply Agent: Plan

Status: proposal (2026-10-17). Extends `INTEGRATION_PLAN_QSR_AGENT.md` Phases 3–5.

## 1. Problem

ZuvoCloud is one app serving many businesses. Most of these businesses have **no order system, price API or ERP**. Some have only an Excel price list, and some have nothing beyond a phone number and opening hours.

The reply agent must therefore:
- **work with whatever data a business has**, from nothing up to a live ERP;
- **never invent** a price, stock level, credit figure or promise that a business has not supplied;
- **onboard by configuration**: a folder, optionally a CSV, and no code per tenant;
- start as a **POC in under a day per business**, then upgrade as the business shares more data.

## 2. Core idea: capability levels and a generic agent

There is one agent for every tenant. What it may say and do depends on **which capabilities that tenant has configured**. The agent's tool list is built per tenant, so it cannot call a tool for data the business doesn't have.

| Level | The business provides | The agent can | The agent never |
|---|---|---|---|
| **L0 Concierge** | Profile: hours, address, delivery areas, payment modes, FAQs | Greet; answer FAQs; **capture the request** (items, qty, location) as a structured lead or order request; hand off to staff | Quote prices or stock |
| **L1 Catalogue** | Plus a CSV or Google Sheet. `price`, `stock` and `moq` are optional columns; blank means "on request" | Search the catalogue, clarify size/brand, share the **list price with its "as of" date**, and create an **order request** in the Zuvo dashboard | Promise stock that isn't in the sheet; give a discount above the configured limit |
| **L2 Connected** | Plus an HTTP connector (or Tally later) for live stock, price and credit, and optionally an order push | Live stock and price checks, credit checks, **draft orders with approvals**, and pushing orders to the business system | Bypass the credit, discount or confidence gates |

Upgrading from one level to the next means adding a file or a connector; there is no code change.

**Orders without a backend:** every level can capture orders. When a business has no order system, the order lives in **Zuvo's own `orders` table**, and the owner sees it in the dashboard and gets a WhatsApp alert. "Order backend: Zuvo" is the default.

## 3. Tenant config bundle (one folder per business)

```
tenants/<slug>/
  tenant.yaml        # profile, persona, languages, hours, numbers (replaces the routing entry + skill.yaml)
  policy.yaml        # what the agent may do per intent (autonomy matrix), thresholds, hand-off rules
  knowledge/faq.md   # free-text FAQs and policies (returns, delivery, payment terms)
  catalog.csv        # OPTIONAL: sku, name, category, unit, price, stock, moq, aliases, image_url
  templates.yaml     # canned replies (greeting, after-hours, hand-off) + approved Meta templates
  connectors.yaml    # OPTIONAL: L2 providers (http/tally) with secrets referenced from env/secret store
```

**`policy.yaml`: the autonomy matrix is the heart of the design**

```yaml
autonomy_default: draft            # off | draft | auto  (draft = AI suggests, a person sends)
intents:
  GENERAL_CHAT:        { mode: auto,  reply: llm_faq }
  INQUIRY_PRICE_STOCK: { mode: draft, requires: [catalog], fallback: handoff }
  CREATE_ORDER:        { mode: draft, requires: [], action: order_request }
  PAYMENT_COMMITMENT:  { mode: draft, reply: template:payment_ack }
  COMPLAINT_RETURN:    { mode: off,   fallback: handoff }      # always a human
gates:
  confidence_min: 0.80
  max_auto_discount_pct: 5
  credit_check: when_available   # only applies at L2
handoff:
  quiet_if_staff_replied_within_min: 30
  business_hours: "Mon-Sat 10:00-20:00 Asia/Kolkata"
  after_hours: template:after_hours
limits:
  max_auto_replies_per_customer_per_hour: 6
  monthly_ai_budget_inr: 500
```

- **`requires`:** if the tenant lacks a capability (for example, there is no `catalog.csv`), the intent automatically uses its `fallback` (usually `handoff`, which means capturing the request and alerting staff). The agent never improvises.
- **Validation:** every file is validated with Pydantic at load time and hot-reloaded, the same mechanism `tenant-routing.yaml` uses today. A bad file keeps the last good config and shows an error on the dashboard.

## 4. Provider (connector) layer

The agent's tools call **providers**, never tenant systems directly.

| Interface | `none` | `csv` / `sheet` (L1) | `http` (L2) | `tally` (later) |
|---|---|---|---|---|
| `CatalogProvider.search(q)` | – | ✔ imported to Postgres (RLS) and searched | ✔ | ✔ |
| `PriceProvider.price(sku, qty)` | – | ✔ list price + `as_of` | ✔ live | ✔ |
| `StockProvider.stock(sku)` | – | ✔ snapshot + `as_of` | ✔ live | ✔ |
| `CustomerProvider.credit(phone)` | – | optional `customers.csv` | ✔ | ✔ |
| `OrderSink.submit(order)` | **Zuvo DB** (default) | Zuvo DB | Zuvo DB **+** POST to the business | Zuvo DB + Tally voucher |

- Every provider result carries `{source, as_of, placeholder}`, which keeps the qsr tool contract. When `as_of` is old, the agent says "as per our list dated …".
- **`http` connector contract:** we publish a small OpenAPI spec (`GET /items?q=`, `GET /stock/{sku}`, `POST /orders`). A business with a developer implements it; for businesses without one, we build adapters once per popular system.
- **Mock HTTP connector** (a container) for demos of L2.

## 5. Runtime flow

```
WhatsApp → gateway → persister → ai-router (intent, Gemini) ──► ai.intent.extracted
                                                                  │
                                       agent-worker (qsr-agent-js, per-tenant toolset)
                                       1. load tenant bundle + capabilities
                                       2. policy matrix → mode (off/draft/auto) or fallback
                                       3. Gemini Flash-Lite tool loop (≤4 tool calls, ≤10 s)
                                          tools = zuvo-commerce MCP (backed by providers)
                                       4. output: agent.reply.proposed {text, sources, mode, order_request?}
                                                                  │
          mode=draft → dashboard "AI suggestion" card (Send / Edit / Discard)
          mode=auto & gates pass → outbound.reply.requested → dispatch → WhatsApp
          gates fail / fallback → handoff: owner alert + holding reply template
```

**Safety rules in the worker, regardless of config:**
- Never reply to the business's own numbers or to status events.
- Stay quiet if staff replied recently.
- Apply a per-customer rate limit and a per-tenant **kill switch**.
- Apply a per-tenant **budget cap**.
- Keep the 24-hour WhatsApp window check (already in dispatch).

## 6. Showing it to prospects: the POC kit

1. **Three demo tenants** in the repo, one per level:
   - *Sharma Salon* (L0): FAQ, appointment request capture, after-hours reply.
   - *Jaipur Hardware* (L1): `catalog.csv` with 50 SKUs, a mix of blank and filled prices; clarify, then list price, then order request.
   - *Delhi Electricals* (L2): mock ERP connector; live stock, a credit breach goes to approval, and a 15% discount goes to an owner alert.
2. **Sandbox chat** in the dashboard, which reuses the existing simulator: try the agent without WhatsApp, and see a "why" panel with the intent, tools called, sources and gate decisions.
3. **Onboarding a prospect in about 30 minutes:**
   1. Copy a demo folder, then edit `tenant.yaml` and `faq.md`.
   2. Optionally drop in their Excel as `catalog.csv`.
   3. Map the WhatsApp number and run in **draft** mode for a week.
   4. Show the report: suggested-vs-sent edits, response time saved, and orders captured.
4. **Later:** a dashboard onboarding wizard (upload CSV, edit FAQs, toggle autonomy per intent) that writes the same bundle, stored in the DB rather than files when multi-tenant SaaS needs it.

## 7. Phases

| Phase | Scope | Exit / demo |
|---|---|---|
| **A: Config bundle and L0** (≈1 wk) | `tenants/<slug>/` schema + loader (backward-compatible with `tenant-routing.yaml` + `skill.yaml`). Policy matrix. `agent-worker` container (Gemini tool calling) with FAQ + capture-request + hand-off. `agent.reply.proposed` topic. Dashboard suggestion card (Send/Edit/Discard). Kill switch, quiet-if-staff, rate limit | Salon demo answers FAQs and captures a request in draft mode over real WhatsApp |
| **B: L1 catalogue and order requests** (≈1 wk) | CSV/Sheet import → `items` (RLS), alias search. `orders`/`order_lines` with "order backend: Zuvo". Owner WhatsApp alert. Order-request panel in the dashboard. `as_of` wording | Hardware demo clarifies a size, quotes the list price and creates an order request the owner sees |
| **C: L2 connectors and approvals** (≈1–1.5 wk) | Provider interfaces + `http` connector + mock ERP container. `zuvo-commerce` MCP in qsr-agent-js backed by providers. Credit/discount gates → approval queue (1-click). Auto mode per intent | Electricals demo: credit breach → PENDING_APPROVAL; 15% discount → owner alert |
| **D: POC kit and onboarding** (≈1 wk) | Sandbox "why" panel. Per-tenant budget + cost meter. Golden test chats per demo tenant. Draft-vs-sent metrics report. Onboarding wizard (MVP) | 30-minute prospect onboarding run end to end |

## 8. Decisions needed

| # | Question | Recommendation |
|---|---|---|
| Q1 | Agent runtime | Gemini function calling in a qsr-agent-js `agent-worker` container, with tools via the `zuvo-commerce` MCP. The Hermes CLI path needs proxy, turn and concurrency fixes first |
| Q2 | Default autonomy for new tenants | `draft` for everything; enable `auto` per intent after a week of reviewing drafts |
| Q3 | Config storage | Files in git for the POC; move to the DB plus wizard once there are more than about 10 tenants |
| Q4 | Catalogue search | Postgres trigram + aliases first; embeddings only if recall is poor |
