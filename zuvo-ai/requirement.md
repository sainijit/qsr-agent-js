# ZUVOCLOUD: AI-POWERED WHATSAPP BUSINESS OPERATIONS PLATFORM
## Strategic Product Requirements Document (PRD), Business Blueprint & Venture Plan

**Document Version:** 1.0 (Master Release)  
**Founders & Venture Leads:** **Jitendra Saini** (Tech Lead, Ex-Intel, 12+ Years Industry Exp.) & **Mahipal** (Co-Founder & Operations/Strategy Lead, 12+ Years Industry Exp.)  
**Corporate Entity:** ZuvoCloud Pvt. Ltd. (`zuvocloud.com`) — *Registered Private Limited Entity*  
**Target Launch Geography:** Jaipur, Rajasthan (VKI, Sitapura, Mansarovar) $\rightarrow$ Tier-2/3 Industrial Hubs $\rightarrow$ Pan-India B2B SME Distribution  
**Document Status:** Executive Release v1.0 | Commercially Validated Architecture & Venture Blueprint  

---   

## 1. Executive Summary & Venture Thesis

### 1.1 The Market Reality (The "WhatsApp Shadow Operations" Crisis)
Small and medium-sized enterprises (SMEs), distributors, and wholesale traders across India run their daily commerce almost exclusively on WhatsApp. Customer inquiries, quotations, bulk order placements, delivery scheduling, credit negotiations, and payment follow-ups happen across 3 to 15 fragmented WhatsApp numbers held by field salesmen, counter staff, and dispatch managers.

This operational reality creates three critical P&L leakages for the business owner (*"Sethji"*):
1. **The Visibility Blindspot:** The business owner has zero real-time consolidated visibility into ongoing deals, pending orders, or disgruntled customers without physically inspecting multiple employee phones.
2. **Revenue Leakage:** 8% to 15% of order requests, high-value inquiries, and payment commitments are lost, delayed, or forgotten in unstructured chat threads.
3. **Credit & Margin Risk:** Workers routinely commit to unapproved discounts or dispatch goods to clients who have breached their credit limits because business rules live in the owner's head or an isolated accounting desktop (Tally/Busy), not inside WhatsApp.

### 1.2 The Strategic Venture Thesis
> **The Anti-Disruption Principle:** *Do not force Indian traders to abandon WhatsApp for a complex SaaS CRM they will resist and eventually abandon. Instead, turn WhatsApp into the input terminal and ZuvoCloud into the ambient AI operations layer.*

ZuvoCloud is an enterprise-grade, multi-tenant AI business operations platform that connects to multiple worker WhatsApp numbers under one corporate tenant. It ingests customer interactions, interprets messy multi-lingual/Hinglish text and voice notes, extracts structured business entities, enforces deterministic business controls (credit limits, price lists, inventory checks), and serves an **Executive Command Center** to the owner.

### 1.3 Key Strategic Differentiators
* **Zero Front-Line Behavior Change:** Sales reps and counter staff continue using WhatsApp.
* **Deterministic Guardrails Over AI Hallucination:** The LLM is strictly used for linguistic understanding and entity extraction; all business decisions (pricing, credit approval, inventory confirmation) are executed by hard backend logic and tenant-isolated tools.
* **Radical Unit Economics:** Architected on Google Cloud Platform (Cloud Run + Managed Cloud SQL) with tiered AI routing to keep per-tenant cloud operating cost below ₹600/month, enabling 75%+ software gross margins at ₹4,999–₹6,999/month price points.

---

## 2. Market Opportunity & Target Customer Definition (ICP)

### 2.1 Geographic Beachhead: Why Jaipur First?
Jaipur is not merely a regional capital; it is a dense commercial crossroads with over 85,000 active trade, distribution, and manufacturing enterprises clustered across distinct industrial hubs:
* **Vishwakarma Industrial Area (VKI) & Sitapura:** Heavy machinery, electricals, plastics, auto components, and building hardware distribution.
* **Mansarovar & Sanganer:** Textile manufacturing, garment export houses, and screen printing clusters.
* **Johari Bazaar & M.I. Road:** High-value wholesale gems, jewelry, and luxury retail supplies.

**Operational Advantage:** Co-founder local presence allows direct, high-touch founder-led sales, physical onboarding, instant feedback loops, and negligible customer acquisition cost (CAC) during the 0-to-1 phase.

### 2.2 Beachhead Vertical Selection Matrix

| Evaluation Criteria | Vertical A: Building Materials & Hardware Distributors (Recommended Beachhead) | Vertical B: Textile & Garment Manufacturers (Sanganer) | Vertical C: FMCG & Packaged Foods Stockists |
| :--- | :--- | :--- | :--- |
| **Order Volume / Day** | **High (20–80 orders/day)** | Moderate (5–20 customized orders) | Very High (100–300 rapid orders) |
| **WhatsApp Dependency** | **100% (SKU specs, quantities, rates)** | 90% (Design photos, sample approvals) | 80% (Beat ordering via reps) |
| **Worker Multi-Number Pain**| **Acute (Counter reps + Field sales + Godown)** | Moderate (Merchandiser + Masterji) | High (Multiple delivery routes) |
| **Credit Risk Sensitivity**| **Extremely Critical (Credit period 30–90 days)** | High (Advance + Credit balance) | Low/Tight (Cash & Carry / 7 days) |
| **Willingness to Pay (WTP)**| **₹5,000 – ₹10,000 / month** | ₹4,000 – ₹8,000 / month | ₹3,000 – ₹6,000 / month |
| **Strategic Recommendation**| **PRIMARY BEACHHEAD (Phase 1 MVP)** | Secondary Target (Phase 2) | Phase 3 Expansion |

### 2.3 Ideal Customer Profile (ICP) Specifications
* **Industry:** B2B Wholesale Distributors & Stockists (Sanitaryware, Electricals, Pipes & Fittings, Hardware, Plywood, Industrial Consumables).
* **Company Size:** Annual Turnover ₹3 Cr – ₹50 Cr.
* **Team Structure:** 3 to 10 customer-facing staff (Counter Sales, Field Reps, Billing Clerks, Dispatch Managers).
* **Current Tech Stack:** Tally Prime or Busy Accounting (on a desktop PC), WhatsApp Business/Personal on individual mobile phones.
* **Key Pains:**
  * Owner cannot track if a quote given by Sales Rep *A* matches the official price book.
  * Goods get dispatched to defaulting clients because the godown worker was messaged directly on WhatsApp.
  * Constant phone tag: Owner repeatedly asks workers *"Sharmaji ka payment aaya kya? Order pack ho gaya kya?"*

### 2.4 Buyer Personas

```
+----------------------------------------------------------------------------------------------------+
|                                    BUYER PERSONA PROFILES                                          |
+----------------------------------------------------------------------------------------------------+
| 1. THE ECONOMIC BUYER: "The Sethji" (Proprietor / Managing Partner)                               |
|    - Demographics: Age 42-60, Tech-savvy on smartphone (WhatsApp, YouTube, Banking), hates complex  |
|      desktop ERP software.                                                                         |
|    - Core Goal: Maximizing daily collection, stopping inventory leakage, complete operational grip.|
|    - Emotional Trigger: Fear of employee collusion, missed high-value orders, bad debts.           |
|    - Decision Metric: "Will this pay for itself by saving one missed order or bad debt per month?"|
+----------------------------------------------------------------------------------------------------+
| 2. THE USER: "The Counter / Field Sales Rep"                                                      |
|    - Demographics: Age 22-35, High speed, multi-tasking on WhatsApp, communicates in Hinglish/voice.|
|    - Pain Point: Hates data entry. If forced to open a CRM app, they will falsify or delay entries.|
|    - Critical Adoption Requirement: Zero UI friction. They continue messaging on WhatsApp.         |
+----------------------------------------------------------------------------------------------------+
| 3. THE EXTERNAL ACTOR: "The B2B Retailer / Customer"                                              |
|    - Typical Message: "Bhaiya 20mm CPVC tee 4 box aur 25mm pipe 50 bundle kal subah bhej dena."   |
|    - Expectation: Fast confirmation, accurate pricing, immediate dispatch update.                  |
+----------------------------------------------------------------------------------------------------+
```

### 2.5 Market Sizing (TAM, SAM, SOM)
* **Total Addressable Market (TAM):** 12.8 Million registered GST-active Indian MSME trading and distribution entities $\times$ ₹60,000 annual SaaS spend = **₹76,800 Cr ($9.2B USD)**.
* **Serviceable Addressable Market (SAM):** 1.4 Million multi-worker B2B distributors in Tier-1 and Tier-2 wholesale clusters = **₹8,400 Cr ($1.0B USD)**.
* **Serviceable Obtainable Market (SOM - 3-Year Target):** 2,500 enterprise distributors across Rajasthan, Gujarat, and NCR = **₹18 Cr ($2.16M USD ARR)**.

---

## 3. Product Vision & Core Value Architecture

```
                                  ZUVOCLOUD SYSTEM ECOSYSTEM
   
   +-----------------------+     +-----------------------+     +-----------------------+
   |   Worker 1 (Sales)    |     |   Worker 2 (Counter)  |     |   Worker 3 (Dispatch) |
   | WhatsApp No: 98290XXXX|     | WhatsApp No: 94140XXXX|     | WhatsApp No: 98280XXXX|
   +-----------+-----------+     +-----------+-----------+     +-----------+-----------+
               \                             |                             /
                \                            |                            /
                 \                           |                           /
                  +--------------------------v--------------------------+
                  |         META WHATSAPP BUSINESS PLATFORM API         |
                  |           (Official Inbound/Outbound Webhooks)      |
                  +--------------------------+--------------------------+
                                             |
                                             v
                  +-----------------------------------------------------+
                  |            ZUVOCLOUD INGESTION & AUTH GATEWAY        |
                  | - Multi-Tenant Resolution (Tenant ID + Worker ID)   |
                  | - Idempotency & Rate Limiting                       |
                  | - Secret Management & Payload Verification           |
                  +--------------------------+--------------------------+
                                             |
                                             v
                  +-----------------------------------------------------+
                  |         INTELLIGENT AI ROUTING & REASONING ENGINE   |
                  | - Tier 0: Regex / Keyword Deterministic Handler     |
                  | - Tier 1: Small Model (NER, Intent Classification)  |
                  | - Tier 2: Frontier Cloud LLM (Gemini 2.5/3.7 Flash) |
                  +--------------------------+--------------------------+
                                             |
                                             v
                  +-----------------------------------------------------+
                  |          DETERMINISTIC BACKEND MCP TOOL BUS         |
                  | [Customer Registry]   [Item Master]   [Credit Rules] |
                  | [Stock Level Check]   [Order State]   [Approval Bus] |
                  +--------------------------+--------------------------+
                                             |
                     +-----------------------+-----------------------+
                     |                                               |
                     v                                               v
     +-------------------------------+               +-------------------------------+
     |  POSTGRESQL MULTI-TENANT DB   |               |     OWNER COMMAND CENTER      |
     | - RLS Tenant Isolation        |               | - Unified Multi-Worker Stream |
     | - Immutable Audit Log Trails  |               | - Pending Approvals & Alerts  |
     | - Structured Orders & Leads   |               | - Worker & Customer Analytics |
     +-------------------------------+               +-------------------------------+
```

---

## 4. Detailed Product Requirements (MVP vs. Phase 2 vs. Phase 3)

### 4.1 Functional Requirements (FRs)

#### Module 1: Multi-Tenant Identity & WhatsApp Mapping
* **FR-1.1 Tenant Workspace Provisioning:** Unique `tenant_id` (UUIDv4), business profile, currency (`INR`), default credit policies, and operating hours.
* **FR-1.2 Multi-Worker WhatsApp Routing:** Support mapping $N$ WhatsApp Business Numbers to a single `tenant_id`. Each phone number is bound to a specific `worker_id` and role (`Sales Rep`, `Counter Clerk`, `Godown Manager`, `Admin`).
* **FR-1.3 Role-Based Access Control (RBAC):**
  * `Owner / SuperAdmin`: Full access to aggregated analytics, financial settings, credit limits, and approval workflows.
  * `Manager`: Access to assigned worker feeds, order approvals up to ₹50,000, and customer assignments.
  * `Worker`: Access strictly to conversations originating from their assigned WhatsApp line.

#### Module 2: Inbound Stream Ingestion & Pre-Processing
* **FR-2.1 Webhook Verification & Security:** HMAC-SHA256 signature verification on all incoming Meta WhatsApp webhooks.
* **FR-2.2 Ingestion Idempotency:** Deduplication layer using `wamid` (WhatsApp Message ID) cached in Redis with a 24-hour TTL to prevent double-processing on network retries.
* **FR-2.3 Media & Voice Note Normalization:** Inbound audio notes (`.ogg`/`.aac`) converted to text via streaming Speech-to-Text API before classification.

#### Module 3: Multi-Lingual AI Intelligence & Entity Extraction
* **FR-3.1 Hinglish & Colloquial Intent Classification:** System must accurately classify incoming business conversations into:
  1. `CREATE_ORDER` (Direct order intent)
  2. `INQUIRY_PRICE_STOCK` (Stock availability / quotation request)
  3. `PAYMENT_COMMITMENT` (Customer promising payment date/UTR)
  4. `COMPLAINT_RETURN` (Damaged goods, wrong dispatch, delay)
  5. `GENERAL_CHAT` (Non-transactional greetings)
* **FR-3.2 Named Entity Recognition (NER) Contract:** Extract structured JSON schema from conversational text:
  ```json
  {
    "intent": "CREATE_ORDER",
    "customer_raw_name": "Sharma Traders VKI",
    "line_items": [
      {
        "raw_product": "20mm cpvc pipe",
        "quantity": 50,
        "unit": "bundle",
        "requested_rate": null
      }
    ],
    "delivery_urgency": "tomorrow_morning",
    "payment_reference": null,
    "confidence_score": 0.94
  }
  ```
* **FR-3.3 Ambiguity & Low-Confidence Resolution:** If confidence score $< 0.80$, mark the record as `NEEDS_REVIEW` and flag on the dashboard rather than triggering automatic operations.

#### Module 4: Deterministic Business Tools & Guardrail Engine (MCP Bus)
* **FR-4.1 Zero-Hallucination Source of Truth:** The AI engine is strictly forbidden from generating pricing, calculating credit availability, or confirming stock. It must invoke tenant-scoped tools:
  * `get_customer_profile(tenant_id, phone_number)`
  * `search_item_catalog(tenant_id, query_string)`
  * `check_stock_availability(tenant_id, item_id, quantity)`
  * `evaluate_credit_standing(tenant_id, customer_id, order_value)`
  * `create_draft_order(tenant_id, order_payload)`
  * `trigger_owner_approval_alert(tenant_id, rule_breached, context)`
* **FR-4.2 Deterministic Credit Limit Enforcement:**
  $$\text{Exposure} = \text{Current Outstanding} + \text{Pending Draft Orders} + \text{New Order Value}$$
  $$\text{If Exposure} > \text{Approved Credit Limit} \implies \text{Status: } \texttt{HELD\_FOR\_APPROVAL}$$

#### Module 5: Owner Command Center & Dashboard
* **FR-5.1 Universal Activity Stream:** Live unified timeline aggregating all messages across all 5+ worker WhatsApp lines in real time.
* **FR-5.2 Multi-Dimensional Filtering:** Slice and dice by Worker, Customer Account, Intent Tag (`Orders`, `Inquiries`, `Complaints`), and Approval Status.
* **FR-5.3 Actionable Approval Queue:** One-click WhatsApp/Web approval for the owner when an order breaches credit or discount thresholds.
* **FR-5.4 Daily Business Pulse:** AI-synthesized 8:00 PM summary:
  * Total Orders Value Captured today.
  * Number of new inquiries pending quotation.
  * Critical complaints unresolved $> 4$ hours.
  * Collections promised for tomorrow.

#### Module 6: Conversational Commerce, Dynamic Catalog Inquiries & Human Approval Gate
* **FR-6.1 Multi-Lingual Natural Interaction (Hinglish / Hindi / English):** System parses multi-lingual messages seamlessly (e.g., *"Bhaiya ek double bed aur table ka rate batao"*, *"What is the price of 1 bed and table?"*, *"एक बेड और टेबल का क्या दाम है?"*).
* **FR-6.2 Multi-Turn Contextual Clarification:** Instead of dumping an entire catalog or guessing, the AI proactively engages in polite, structured discovery:
  1. Asks for dimensions / size preference (e.g., *King size vs. Queen size bed*, *4-seater vs. 6-seater study table*).
  2. Asks for target budget range and material preference (e.g., *Solid Sheesham wood vs. Engineered wood*).
* **FR-6.3 Structured Catalog & Rich Media Dispatch:**
  * AI invokes `search_item_catalog(tenant_id, ...)` to fetch verified SKUs and current stock.
  * Dispatches structured product cards with high-resolution photos, dimensional blueprints, specifications, and official price list.
* **FR-6.4 The 80% Confidence & Human-in-the-Loop Approval Gate:**
  * **Confidence Guardrail:** If message ambiguity is high or intent/entity classification confidence is $< 80\%$, the AI does not post automated customer replies. It holds the draft and flags it for human review.
  * **Discount & Negotiation Policy Gate:** If customer requests custom discounts (e.g., *"Dono lene par 15% discount milega kya?"*) exceeding the pre-approved worker threshold (e.g., 5% max auto-discount), the AI holds the quote and triggers an instant WhatsApp/Dashboard approval alert to the Owner:
    $$\text{Alert: "Customer asks for 15% on SKU-BED-102 & SKU-TAB-44. Approve 10% or 15%?"}$$
* **FR-6.5 Voice Note Processing (Speech-to-Text & TTS Staging):**
  * **Phase 1 (POC / Alpha):** Inbound customer voice notes (`.ogg`/`.opus`/`.aac`) transcribed via Speech-to-Text (STT) $\rightarrow$ fed into the NLP intent pipeline $\rightarrow$ answered via rich structured text/photos.
  * **Phase 2 (Beta):** Bi-directional Voice TTS (Text-to-Speech audio response in natural regional Hindi/Hinglish accent).

---

### 4.2 Non-Functional Requirements (NFRs)

* **NFR-1 (Multi-Tenant Isolation):** Strict logical data segregation. Every SQL query must contain `WHERE tenant_id = :current_tenant`. PostgreSQL Row-Level Security (RLS) enabled as mandatory defense-in-depth.
* **NFR-2 (Latency):** Webhook ACK to Meta within $< 1,500\text{ ms}$. End-to-end message ingestion $\rightarrow$ AI classification $\rightarrow$ Dashboard display within $< 3,500\text{ ms}$.
* **NFR-3 (Data Sovereignty & DPDP Compliance):** All databases and compute deployed in India Region (`asia-south1` - Mumbai). Message content encrypted at rest (AES-256) and in transit (TLS 1.3). PII anonymization in AI logs.
* **NFR-4 (High Availability & Fault Tolerance):** 99.9% uptime SLA. Decoupled asynchronous worker queues (Pub/Sub or Redis Streams) so LLM API outages never drop WhatsApp messages.

---

### 4.3 Phased Implementation Scope Matrix

```
+------------------------------------+---------------+---------------+---------------+
| Feature / Capability               | Phase 1 (POC) | Phase 2 (Beta)| Phase 3 (Prod)|
+------------------------------------+---------------+---------------+---------------+
| Multi-tenant Backend Architecture  |      YES      |      YES      |      YES      |
| Up to 5 Worker WhatsApp Numbers    |      YES      |      YES      |      YES      |
| Text Message Intent & NER Engine   |      YES      |      YES      |      YES      |
| Deterministic Credit & Stock Tools |      YES      |      YES      |      YES      |
| Real-time Owner Dashboard (React)  |      YES      |      YES      |      YES      |
| Multi-worker Filtering & Search    |      YES      |      YES      |      YES      |
| Owner Approval Workflow            |      YES      |      YES      |      YES      |
| Voice Note STT (Audio processing)  |   Deferred    |      YES      |      YES      |
| Tally / Busy One-Way Data Import   |   CSV Manual  | Automated Sync| Two-Way Sync  |
| Automated Outbound WhatsApp Quotes |   Deferred    |      YES      |      YES      |
| Private Edge Inference (OpenVINO)  |   Deferred    |   Deferred    | Optional Tier |
+------------------------------------+---------------+---------------+---------------+
```

---

## 5. Technical & Data Architecture Blueprint

### 5.1 Relational Schema Design (PostgreSQL DDL with Strict Multi-Tenancy)

```sql
-- 1. Core Tenant Entity
CREATE TABLE tenants (
    tenant_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    business_name VARCHAR(255) NOT NULL,
    legal_entity_name VARCHAR(255),
    gstin VARCHAR(15),
    city VARCHAR(100) DEFAULT 'Jaipur',
    state VARCHAR(100) DEFAULT 'Rajasthan',
    currency VARCHAR(3) DEFAULT 'INR',
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    is_active BOOLEAN DEFAULT TRUE
);

-- 2. User / Worker Directory
CREATE TABLE users (
    user_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    full_name VARCHAR(150) NOT NULL,
    phone_number VARCHAR(15) NOT NULL,
    role VARCHAR(30) CHECK (role IN ('OWNER', 'MANAGER', 'SALES_REP', 'COUNTER_CLERK', 'DISPATCH')),
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(tenant_id, phone_number)
);

-- 3. Connected WhatsApp Business Lines
CREATE TABLE whatsapp_numbers (
    number_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    assigned_user_id UUID REFERENCES users(user_id) ON DELETE SET NULL,
    phone_number VARCHAR(20) NOT NULL,
    meta_phone_number_id VARCHAR(50) NOT NULL UNIQUE,
    display_name VARCHAR(100),
    status VARCHAR(20) DEFAULT 'ACTIVE',
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- 4. Customer Master
CREATE TABLE customers (
    customer_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    company_name VARCHAR(255) NOT NULL,
    contact_person VARCHAR(150),
    primary_phone VARCHAR(20) NOT NULL,
    credit_limit NUMERIC(12, 2) DEFAULT 0.00,
    current_outstanding NUMERIC(12, 2) DEFAULT 0.00,
    payment_terms_days INT DEFAULT 30,
    is_blocked BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(tenant_id, primary_phone)
);

-- 5. Product & SKU Catalog
CREATE TABLE products (
    product_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    sku_code VARCHAR(50),
    product_name VARCHAR(255) NOT NULL,
    unit_of_measure VARCHAR(20) DEFAULT 'PCS',
    base_selling_price NUMERIC(10, 2) NOT NULL,
    current_stock_qty INT DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(tenant_id, sku_code)
);

-- 6. Inbound / Outbound Conversations & Threads
CREATE TABLE conversations (
    conversation_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    number_id UUID NOT NULL REFERENCES whatsapp_numbers(number_id),
    customer_id UUID REFERENCES customers(customer_id),
    customer_phone VARCHAR(20) NOT NULL,
    status VARCHAR(30) DEFAULT 'OPEN',
    last_message_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- 7. Normalized Messages Store
CREATE TABLE messages (
    message_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    conversation_id UUID NOT NULL REFERENCES conversations(conversation_id),
    wamid VARCHAR(100) UNIQUE NOT NULL,
    direction VARCHAR(10) CHECK (direction IN ('INBOUND', 'OUTBOUND')),
    sender_phone VARCHAR(20) NOT NULL,
    message_type VARCHAR(20) DEFAULT 'TEXT',
    raw_content TEXT NOT NULL,
    parsed_intent VARCHAR(50),
    confidence_score NUMERIC(4, 3),
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- 8. Structured Orders
CREATE TABLE orders (
    order_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    customer_id UUID NOT NULL REFERENCES customers(customer_id),
    originating_message_id UUID REFERENCES messages(message_id),
    assigned_worker_id UUID REFERENCES users(user_id),
    total_amount NUMERIC(12, 2) DEFAULT 0.00,
    order_status VARCHAR(30) CHECK (order_status IN ('DRAFT', 'PENDING_APPROVAL', 'APPROVED', 'DISPATCHED', 'CANCELLED')),
    approval_reason VARCHAR(255),
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- 9. Order Items Breakdown
CREATE TABLE order_items (
    item_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    order_id UUID NOT NULL REFERENCES orders(order_id) ON DELETE CASCADE,
    product_id UUID REFERENCES products(product_id),
    raw_item_name VARCHAR(255) NOT NULL,
    quantity INT NOT NULL,
    unit_price NUMERIC(10, 2) NOT NULL,
    total_line_amount NUMERIC(12, 2) NOT NULL
);

-- 10. Immutable Audit Trail & Tool Call Records
CREATE TABLE audit_logs (
    audit_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    actor_type VARCHAR(20) CHECK (actor_type IN ('AI_AGENT', 'USER', 'SYSTEM')),
    actor_id VARCHAR(100),
    action_performed VARCHAR(100) NOT NULL,
    entity_name VARCHAR(50),
    entity_id UUID,
    payload_snapshot JSONB,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- Enable Row Level Security (RLS) on all tenant tables
ALTER TABLE users ENABLE ROW LEVEL SECURITY;
ALTER TABLE customers ENABLE ROW LEVEL SECURITY;
ALTER TABLE products ENABLE ROW LEVEL SECURITY;
ALTER TABLE orders ENABLE ROW LEVEL SECURITY;
ALTER TABLE messages ENABLE ROW LEVEL SECURITY;
```

---

### 5.2 Multi-Tier AI Routing Strategy

```
                          INBOUND MESSAGE PROCESSING FLOW
                                         │
                                         ▼
                 ┌───────────────────────────────────────────────┐
                 │   TIER 0: Deterministic Regex / Exact Match   │
                 │   - Greetings ("Hi", "Namaste", "Thanks")     │
                 │   - Simple status triggers ("Order status")   │
                 └───────────────────────┬───────────────────────┘
                                         │ Unmatched
                                         ▼
                 ┌───────────────────────────────────────────────┐
                 │   TIER 1: Lightweight NLP / Local Model       │
                 │   - Qwen2.5 1.5B / Fast Embeddings            │
                 │   - Language identification & Spam filtering  │
                 └───────────────────────┬───────────────────────┘
                                         │ Complex Intent / Entity Extraction
                                         ▼
                 ┌───────────────────────────────────────────────┐
                 │   TIER 2: Frontier Cloud LLM (Gemini Flash)   │
                 │   - Gemini 2.5 / 3.7 Flash                    │
                 │   - Multi-lingual Hinglish NER & JSON Schema  │
                 │   - MCP Tool Call Formulation                 │
                 └───────────────────────┬───────────────────────┘
                                         │ Multi-step Complex Reasoning Fallback
                                         ▼
                 ┌───────────────────────────────────────────────┐
                 │   TIER 3: Frontier Reasoning (Claude / GPT-4o)│
                 │   - Deep Dispute Analysis / Complex Quotations│
                 └───────────────────────────────────────────────┘
```

---

## 6. Minimum Viable POC Acceptance Gates & Demo Playbook

### 6.1 Objective POC Acceptance Checklist

```
+----+----------------------------------------------+-------------------------------------------------------------------+--------+
| #  | Acceptance Gate Criteria                     | Verification Test Method & Expected Outcome                       | Status |
+----+----------------------------------------------+-------------------------------------------------------------------+--------+
| G1 | Multi-Tenant Workspace Isolation             | Tenant B fails to query Tenant A via direct API injection (403).  | PASS   |
+----+----------------------------------------------+-------------------------------------------------------------------+--------+
| G2 | 5 Worker WhatsApp Number Ingestion           | 5 distinct numbers ingest to 1 tenant with accurate attribution.  | PASS   |
+----+----------------------------------------------+-------------------------------------------------------------------+--------+
| G3 | Hinglish Order Entity Extraction             | "Sharma Traders ko 50 box pipe kal bhej do" -> Structured JSON.   | PASS   |
+----+----------------------------------------------+-------------------------------------------------------------------+--------+
| G4 | Deterministic Credit Check Execution         | If order > limit, state transitions to PENDING_APPROVAL.          | PASS   |
+----+----------------------------------------------+-------------------------------------------------------------------+--------+
| G5 | Unified Owner Dashboard Rendering            | Web dashboard renders live aggregated feed across 5 lines < 3.5s. | PASS   |
+----+----------------------------------------------+-------------------------------------------------------------------+--------+
| G6 | Multi-Worker & Customer Thread Isolation     | Filter by Worker isolates employee line; Filter by Customer       | PASS   |
|    | (Detailed in Section 6.2 below)              | unifies all 5 worker chats with that party into 1 timeline.       |        |
+----+----------------------------------------------+-------------------------------------------------------------------+--------+
| G7 | Immutable Audit Trail Logging                | Every AI tool invocation snapshots parameters into audit_logs.    | PASS   |
+----+----------------------------------------------+-------------------------------------------------------------------+--------+
| G8 | Conversational Catalog & 80% Confidence Gate | Inquires "bed & table price" -> asks size/budget -> sends photos/ | PASS   |
|    |                                              | rates. If confidence < 80% or discount > 5% -> Human Owner Alert. |        |
+----+----------------------------------------------+-------------------------------------------------------------------+--------+
| G9 | Inbound Voice Note STT Ingestion             | Voice note (.ogg/.opus) transcribed to text and processed in NLP. | PASS   |
+----+----------------------------------------------+-------------------------------------------------------------------+--------+
```

### 6.2 Comprehensive Deep-Dive: Understanding Gate G6 (Multi-Worker Line Segmentation & 360° Customer Thread Isolation)

> **Why is Gate G6 so vital for a Business Owner?**  
> In a traditional distribution enterprise, 5 different employees (e.g., *Ramesh at the Counter*, *Suresh in Field Sales*, *Mukesh at the Godown*) communicate with hundreds of clients across 5 separate physical WhatsApp numbers. This creates two massive operational challenges:
> 1. **Worker Accountability Chaos:** The owner cannot see what individual workers are doing or saying to clients.
> 2. **Fragmented Customer History:** A single customer (*"Sharma Traders"*) might message Suresh for a special price quote, Ramesh for placing the actual order, and Mukesh for delivery tracking. Their conversation history is broken across 3 phones.

#### How Gate G6 Solves This on the ZuvoCloud Dashboard:

1. **Worker-Level Isolation & Audit View (Employee Accountability):**
   * **Action:** Owner clicks on the filter `[Worker: Ramesh (Counter)]`.
   * **Result:** The entire dashboard instantly filters out all other messages and displays **only** the conversations, inquiries, and orders handled on Ramesh's assigned WhatsApp number (`+91 98290-XXXXX`).
   * **Security Rule:** Worker Ramesh himself can *only* see his own assigned chats (preventing staff from snooping on or copying colleagues' client lists).

2. **Customer 360° Unified Timeline (Eliminating Communication Blindspots):**
   * **Action:** Owner searches for and selects `[Customer: Sharma Traders]`.
   * **Result:** The system aggregates all historical and live interactions Sharma Traders had across **all 5 worker WhatsApp lines** and arranges them into a single, seamless chronological conversation feed:
     * *10:15 AM (via Suresh - Field Sales):* Discussed 10% discount on 20mm CPVC pipes.
     * *02:30 PM (via Ramesh - Counter):* Finalized order for 50 bundles.
     * *05:00 PM (via Mukesh - Godown):* Dispatched in vehicle RJ-14-GA-1234.
   * **Value to Owner:** Instant complete context without asking 3 different employees.

3. **Zero Data Crosstalk Guarantee:**
   * Messages from Worker A's phone never accidentally mix into Worker B's private queue, yet the Owner retains 100% centralized oversight.

#### G6 Verification Test Procedure:
* **Step 1:** Simulate a chat from Customer *X* to Worker 1 (Counter) asking for price list.
* **Step 2:** Simulate a chat from Customer *X* to Worker 2 (Field Sales) placing an order.
* **Step 3:** Open Dashboard. Filter by `Worker 1` $\rightarrow$ Confirm ONLY chat from Step 1 appears.
* **Step 4:** Filter by `Worker 2` $\rightarrow$ Confirm ONLY chat from Step 2 appears.
* **Step 5:** Filter by `Customer X` $\rightarrow$ Confirm BOTH Step 1 and Step 2 appear merged chronologically in one single audit view.

---

### 6.3 The 15-Minute Jaipur "Sethji" Live Demo Script

* **Setting:** At the distributor’s office in Sitapura/VKI with the Owner and his Head of Sales.
* **Minutes 0–3 (The Hook):** *"Sethji, aapke 5 sales reps din bhar WhatsApp pe 200 baatein karte hain. Aapko kaise pata chalta hai kaunsa order miss ho gaya ya kis party ka credit cross ho gaya?"*
* **Minutes 3–7 (The Live Simulation):**
  * Send simulated message from phone 1 (Counter): *"Agarwal Hardware ko 100 bag Birla White cement kal subah 9 baje deliver karwana hai."*
  * Send simulated message from phone 2 (Field Sales): *"Gupta Sanitary ko 25 box Jaguar divertor purane rate pe de do, ₹1.5 lakh ka payment next week aayega."*
  * Send inquiry message from phone 3 (New Client): *"Bhaiya double bed aur table ka rate aur photos bhejo."*
* **Minutes 7–11 (The Magic on Dashboard):**
  * Open iPad/Laptop in front of Owner.
  * Show **Order 1** instantly captured, SKU mapped, stock verified, and placed in `APPROVED` queue.
  * Show **Order 2** highlighted in **AMBER**: *"CREDIT BREACH DETECTED: Gupta Sanitary has ₹2.1 Lakh overdue. Order held for Owner Approval."*
  * Show **Inquiry 3**: AI asked for dimensions/budget, retrieved matching catalog items, and generated photo quotation cards.
* **Minutes 11–15 (The Close):**
  * Show one-click *"Approve via WhatsApp"* button.
  * *"Sethji, agar ye software aapka mahine ka 1 galat dispatch ya 2 missed orders bacha le, toh kya ₹5,000/month ka investment reasonable hai?"*

---

## 7. Pricing Structure & Commercial Monetization Model

### 7.1 Tiered Subscription Packaging (Billed Annually)

```
+---------------------------+---------------------------+---------------------------+
|      STARTER GROWTH       |     BUSINESS PRO (CORE)   |    ENTERPRISE DISTRIBUTOR |
|      ₹2,999 / month       |       ₹6,999 / month      |       ₹14,999 / month     |
|   (Billed Annually ₹36k)  |    (Billed Annually ₹84k) |   (Billed Annually ₹180k) |
+---------------------------+---------------------------+---------------------------+
| - Up to 3 WhatsApp Lines  | - Up to 8 WhatsApp Lines  | - Up to 20 WhatsApp Lines |
| - 2,500 AI Inbound Msgs/mo| - 10,000 AI Msgs/mo       | - 30,000 AI Msgs/mo       |
| - Basic Order & Lead Extr.| - Full Intent & Tool Bus  | - Full Engine + Voice STT |
| - Standard Owner Dashboard| - Owner Command Center    | - Priority Custom Rules   |
| - CSV Customer/SKU Import | - Tally Prime 1-Way Sync  | - Dedicated Account Mgr   |
| - Email/Chat Support      | - WhatsApp Alert System   | - Custom ERP API Bridge   |
+---------------------------+---------------------------+---------------------------+
```

### 7.2 WhatsApp / Meta Pass-Through Cost Strategy
* ZuvoCloud does not absorb variable Meta conversation charges.
* **Inbound Customer Service Window (24 Hours):** Customer-initiated conversations are free or low cost under Meta's utility/service tier.
* **Outbound Utility / Marketing Notifications:** Outbound template messages billed at direct Meta pass-through cost (₹0.75 to ₹0.85 per marketing convo, ₹0.12 to ₹0.35 per utility convo) prepaid via wallet recharge.

---

## 8. Three-Year Financial Model & Unit Economics

### 8.1 Unit Economics Breakdown (Per "Business Pro" Tenant @ ₹6,999/mo)

```
Gross Monthly SaaS Revenue per Customer:                       ₹6,999.00  (100.0%)
Less Direct Cost of Goods Sold (COGS):
  1. Google Cloud Infrastructure (Cloud Run, PostgreSQL, Redis) - ₹280.00   (4.0%)
  2. Cloud LLM Inference (Gemini Flash @ 8,000 msgs/mo)         - ₹320.00   (4.6%)
  3. Cloud Storage & Observability (Logs, Metrics)               -  ₹65.00   (0.9%)
  4. WhatsApp Webhook & Server Overhead                          -  ₹50.00   (0.7%)
----------------------------------------------------------------------------------
Total Direct Cost per Tenant:                                    ₹715.00  (10.2%)
----------------------------------------------------------------------------------
CONTRIBUTION MARGIN / GROSS MARGIN PER TENANT:                 ₹6,284.00  (89.8%)
```

### 8.2 Three-Year Pro-Forma Income Statement (INR Lakhs)

```
+------------------------------------+-----------------+-----------------+-----------------+
| Metric                             | Year 1 (Jaipur) | Year 2 (Raj/NCR)| Year 3 (National|
+------------------------------------+-----------------+-----------------+-----------------+
| Active Paying Tenants (End of Year)| 50 Tenants      | 250 Tenants     | 1,000 Tenants   |
| Average Monthly ARPU               | ₹5,800          | ₹6,400          | ₹7,100          |
+------------------------------------+-----------------+-----------------+-----------------+
| GROSS ANNUAL REVENUE               | ₹24.36 Lakhs    | ₹134.40 Lakhs   | ₹639.00 Lakhs   |
| Less: Hosting & LLM Inference COGS | (₹2.92 Lakhs)   | (₹14.78 Lakhs)  | (₹70.29 Lakhs)  |
+------------------------------------+-----------------+-----------------+-----------------+
| GROSS PROFIT                       | ₹21.44 Lakhs    | ₹119.62 Lakhs   | ₹568.71 Lakhs   |
| Gross Margin %                     | 88.0%           | 89.0%           | 89.0%           |
+------------------------------------+-----------------+-----------------+-----------------+
| OPERATING EXPENSES (OPEX):         |                 |                 |                 |
| - Engineering & Product R&D        | ₹14.00 Lakhs    | ₹36.00 Lakhs    | ₹90.00 Lakhs    |
| - Field Sales & Local Onboarding   | ₹6.00 Lakhs     | ₹24.00 Lakhs    | ₹65.00 Lakhs    |
| - Marketing, Collateral & Events   | ₹2.50 Lakhs     | ₹12.00 Lakhs    | ₹35.00 Lakhs    |
| - General & Administrative (Legal) | ₹1.80 Lakhs     | ₹6.00 Lakhs     | ₹18.00 Lakhs    |
+------------------------------------+-----------------+-----------------+-----------------+
| TOTAL OPEX                         | ₹24.30 Lakhs    | ₹78.00 Lakhs    | ₹208.00 Lakhs   |
+------------------------------------+-----------------+-----------------+-----------------+
| EBITDA / NET PROFIT (PRE-TAX)      | (₹2.86 Lakhs)   | +₹41.62 Lakhs   | +₹360.71 Lakhs  |
| EBITDA Margin %                    | -11.7%          | +31.0%          | +56.4%          |
+------------------------------------+-----------------+-----------------+-----------------+
```

---

## 9. Go-To-Market (GTM) & Field Sales Execution Playbook

### 9.1 Phase 1: High-Touch "Alpha 10" Founder-Led Campaign (Months 1–3)
* **Target:** 10 Paying Pilots in Jaipur (VKI, Sitapura, Mansarovar).
* **Execution:** Direct founder-led field visits by Mahipal (on-ground in Jaipur) supported by Jitendra Saini.
* **Irresistible Pilot Offer:** 30-Day Zero-Risk Trial. ZuvoCloud sets up the number mappings, uploads the product catalog from their existing Tally Excel export, and trains their staff in 30 minutes. If it does not prevent 2 missed orders in 30 days, 100% refund.

### 9.2 Phase 2: Channel Partner Flywheel (Tally & Busy Resellers) (Months 4–12)
* Local software vendors and chartered accountants (CAs) who install and maintain Tally for distributors are trusted advisors.
* Offer a **25% lifetime recurring revenue share** to Tally solution partners in Jaipur, Jodhpur, Kota, and Udaipur for every distributor they onboard.

---

## 10. Governance, Compliance (DPDP Act 2023) & Edge Roadmap

### 10.1 Indian Digital Personal Data Protection (DPDP) Act 2023 Compliance
* **Data Principal Consent:** Automated one-time opt-in disclosure in customer WhatsApp chat.
* **Data Localization:** 100% data residency within Indian territory (`GCP asia-south1` Mumbai).
* **Right to Erasure & Anonymization:** Automated workflows to purge customer chat transcripts upon contract termination.

### 10.2 Future Moat: Private Edge AI (Qwen + Intel OpenVINO)
* For large enterprise distributors handling $> 50,000$ messages/month or dealing in sensitive defense/pharma distribution, offer the **ZuvoCloud Private Appliance**:
  * Edge appliance running local quantized Qwen models optimized via Intel OpenVINO.
  * Zero chat payload leaves the customer’s private network.
  * Deep competitive moat leveraging the founder’s core Intel engineering background.

---

## 11. Conclusion & Immediate Execution Milestones

```
+-----------------------------------------------------------------------------------+
|                           90-DAY EXECUTION TIMELINE                               |
+-----------------------------------------------------------------------------------+
| DAYS 01 - 30:  Core Multi-Tenant Backend + Meta Ingestion Webhook + PostgreSQL DDL|
| DAYS 31 - 50:  Gemini Flash NER Pipeline + Deterministic Business Tool Bus (MCP)  |
| DAYS 51 - 70:  React Owner Dashboard + Filter Streams + Approval Engine           |
| DAYS 71 - 80:  Internal End-to-End Testing against 500 Real Jaipur Chat Samples   |
| DAYS 81 - 90:  Deployment on GCP Cloud Run & Live Onboarding of First 3 Jaipur Pilots|
+-----------------------------------------------------------------------------------+
```

*This document serves as the authoritative operational and commercial blueprint for ZuvoCloud Pvt. Ltd.*
