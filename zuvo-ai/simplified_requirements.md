# ZUVOCLOUD AI SOLUTION
## Simplified Product Requirements Document (PRD) — Point-by-Point Specification

**Document Version:** 1.0 (Simplified Executive Edition)  
**Author:** Senior Venture Strategist & Serial Entrepreneur (*IIM Ahmedabad Alumnus*)  
**Company:** ZuvoCloud Pvt. Ltd. (`zuvocloud.com`)  
**Target Market:** Jaipur B2B Trading Hubs (Sitapura / VKI / Mansarovar) $\rightarrow$ Pan-India SME Distribution  

---

## 1. Executive Summary & Core Principle

* **1.1 The Core Problem:** Indian wholesale distributors and traders manage high-volume commerce across 3 to 15 fragmented WhatsApp numbers held by staff. Owners face zero real-time visibility, 8–15% missed/unattended orders, and unmonitored credit/discount risks.
* **1.2 The Core Principle (Anti-Disruption):** Employees continue using WhatsApp without learning any new software. ZuvoCloud acts as the ambient AI operations layer, capturing structured data, running deterministic rules, and equipping the owner with a centralized Command Center.
* **1.3 Zero-Hallucination Mandate:** The AI (Gemini Flash) is strictly used for linguistic understanding (Hinglish/Hindi/English) and entity extraction. Pricing, inventory, and credit rules are 100% deterministic and enforced by backend databases and tools.

---

## 2. Point-by-Point Functional Requirements

### REQ-1.0: Multi-Tenant Workspace & Role Management
* **REQ-1.1 Unique Tenant Isolation:** Every business is provisioned as an independent tenant with a unique `tenant_id` (UUIDv4). Cross-tenant data access is strictly blocked at the database and API layer.
* **REQ-1.2 Multi-Worker User Directory:** Each tenant supports adding $N$ employees with specific roles:
  * `Owner / SuperAdmin`: Unrestricted access to all worker feeds, financial reports, credit rules, and approval queues.
  * `Manager`: Access to assigned worker teams and order approvals up to specified limits (e.g., ₹50,000).
  * `Worker (Sales / Counter / Godown)`: Access restricted strictly to conversations originating on their assigned WhatsApp line.
* **REQ-1.3 Business Profile Configuration:** Tenant profile stores official business name, GSTIN, currency (`INR`), operating hours, payment terms, and default credit limits.

---

### REQ-2.0: WhatsApp Ingestion & Multi-Worker Line Management
* **REQ-2.1 Official Meta Cloud API Webhook:** Ingest messages in real time using the official WhatsApp Business Platform API.
* **REQ-2.2 Webhook Security & Verification:** Every inbound payload is cryptographically verified using HMAC-SHA256 signature checking before processing.
* **REQ-2.3 Ingestion Deduplication (Idempotency):** Incoming messages are deduplicated using Meta's `wamid` (WhatsApp Message ID) cached in Redis for 24 hours to prevent duplicate orders on network retries.
* **REQ-2.4 Multi-Number Binding:** Support connecting multiple physical WhatsApp Business numbers to a single business tenant workspace.
* **REQ-2.5 Worker Attribution:** Inbound messages automatically map to both the specific `tenant_id` and the assigned `worker_id` based on the receiving WhatsApp line.

---

### REQ-3.0: Multi-Lingual AI Intelligence & Entity Extraction
* **REQ-3.1 Multi-Lingual NLP Engine:** Native understanding of colloquial Hinglish, Hindi (Devanagari), and Indian English:
  * Example Hinglish: *"Sharma Traders ko 50 box 20mm pipe kal subah bhej dena."*
  * Example Hindi: *"अग्रवाल हार्डवेयर को १०० बैग सीमेंट कल भेजना है।"*
  * Example English: *"Send 50 boxes of 20mm pipe to Sharma Traders by tomorrow."*
* **REQ-3.2 Intent Classification:** Classify every inbound message into 5 primary operational categories:
  1. `CREATE_ORDER`: Direct purchase order placement.
  2. `INQUIRY_PRICE_STOCK`: Pricing, quotation, or stock availability request.
  3. `PAYMENT_COMMITMENT`: Promise of payment date, cheque, or UTR reference.
  4. `COMPLAINT_RETURN`: Damaged goods, wrong dispatch, or delivery delay.
  5. `GENERAL_CHAT`: Non-commercial greetings (*"Hi"*, *"Namaste"*, *"Thanks"*).
* **REQ-3.3 Named Entity Recognition (NER):** Extract structured JSON fields containing:
  * `customer_name`: Identified or raw account name.
  * `line_items`: Array of items containing `product_name`, `quantity`, `unit` (bags, boxes, bundles, pcs), and `requested_rate`.
  * `delivery_date`: Urgency and delivery timeline (*"kal subah"*, *"by Friday"*).
  * `confidence_score`: Metric from 0.00 to 1.00 indicating parsing certainty.

---

### REQ-4.0: Conversational Commerce, Inquiry Clarification & Catalog Quoting
* **REQ-4.1 Multi-Turn Conversational Clarification:** When a customer asks an open-ended inquiry (e.g., *"What is the price of a bed and a table?"*), the AI does not guess or dump the entire catalog. It proactively asks structured clarifying questions:
  * **Size / Dimensions:** King size vs. Queen size bed; 4-seater vs. 6-seater table.
  * **Budget & Material:** Target price range; Solid Sheesham wood vs. Engineered wood.
* **REQ-4.2 Automated Catalog Search & Media Dispatch:** Once parameters are clarified, the AI executes `search_item_catalog()` and sends rich WhatsApp product cards containing:
  * High-resolution product images.
  * Exact dimensions, material specifications, and stock status.
  * Official MRP and standard selling rates.
* **REQ-4.3 Voice Note Processing (STT / TTS Roadmap):**
  * **Phase 1 (POC / Alpha):** Inbound customer voice notes (`.ogg`/`.opus`/`.aac`) are transcribed to text via Speech-to-Text (STT) $\rightarrow$ fed into the NLP pipeline $\rightarrow$ replied to via structured text/photos.
  * **Phase 2 (Beta):** Bi-directional Voice TTS (Text-to-Speech audio response in natural Hindi/Hinglish accent).

---

### REQ-5.0: Deterministic Business Tools & Guardrail Engine
* **REQ-5.1 Zero-Hallucination Architecture:** The AI is strictly barred from inventing prices, creating credit, or confirming unverified inventory. It must invoke backend Model Context Protocol (MCP) tools:
  * `get_customer_profile(tenant_id, phone_number)`: Retrieves credit limit, balance, and ledger.
  * `search_item_catalog(tenant_id, search_term)`: Queries verified SKU master and price book.
  * `check_stock_availability(tenant_id, item_id, quantity)`: Confirms warehouse inventory.
  * `evaluate_credit_standing(tenant_id, customer_id, order_value)`: Calculates real-time credit exposure.
  * `create_draft_order(tenant_id, order_payload)`: Persists structured order object in database.
* **REQ-5.2 Deterministic Credit Limit Formula:**
  $$\text{Total Exposure} = \text{Current Outstanding} + \text{Pending Drafts} + \text{New Order Value}$$
  $$\text{If Total Exposure} > \text{Approved Credit Limit} \implies \text{Status: } \texttt{HELD\_FOR\_APPROVAL}$$

---

### REQ-6.0: Human-in-the-Loop & Approval System
* **REQ-6.1 The 80% Confidence Guardrail:** If message ambiguity is high or AI classification confidence is $< 80\%$, the AI will not send an automated customer response. It drafts the suggested response and flags the message on the dashboard as `NEEDS_HUMAN_REVIEW`.
* **REQ-6.2 Discount & Negotiation Policy Gate:**
  * Counter workers have a maximum pre-approved automated discount limit (e.g., 5%).
  * If a customer asks for a higher discount (e.g., *"Dono saath lene par 15% discount do"*), the AI holds the quote and triggers an Owner Approval Alert:
    $$\text{Alert: "Customer asks for 15% discount on Bed + Table. Approve 10% or 15%?"}$$
* **REQ-6.3 One-Click Owner Approval Interface:**
  * **Via WhatsApp:** Interactive button sent to owner: `[Approve 10%]` | `[Approve 15%]` | `[Reject]`.
  * **Via Web Dashboard:** One-click modal on the Owner Command Center to approve or counter-offer held orders.

---

### REQ-7.0: Owner Command Center & Unified Dashboard
* **REQ-7.1 Universal Real-Time Feed:** Live chronological stream aggregating all messages, orders, and inquiries across all 5+ worker WhatsApp lines in $< 3.5\text{s}$ latency.
* **REQ-7.2 Gate G6: Multi-Worker Line Segmentation & Customer 360° Thread Isolation:**
  * **Worker-Level Isolation:** Owner clicks `[Worker: Ramesh (Counter)]` $\rightarrow$ Displays *only* messages, quotes, and orders handled on Ramesh's phone (`+91 98290-XXXXX`). Worker Ramesh can only see his own assigned chats (preventing client poaching).
  * **Customer 360° Unified Timeline:** Owner searches `[Customer: Sharma Traders]` $\rightarrow$ Aggregates all interactions Sharma Traders had across all 5 worker numbers into one single timeline (e.g., 10:15 AM Suresh quote $\rightarrow$ 02:30 PM Ramesh order $\rightarrow$ 05:00 PM Mukesh dispatch).
  * **Zero Crosstalk Security:** Messages from Worker A's phone never leak into Worker B's feed, while Owner retains 100% centralized visibility.
* **REQ-7.3 8:00 PM Daily Business Pulse:** AI-synthesized evening summary sent to the owner detailing:
  * Total order value captured today.
  * Pending quotation requests $> 2$ hours old.
  * Overdue payment promises and credit limit breaches.
  * Customer complaints requiring owner intervention.

---

### REQ-8.0: Data Security, Multi-Tenant Isolation & Compliance
* **REQ-8.1 Tenant Isolation at Database Layer:** PostgreSQL Row-Level Security (RLS) enabled on all tables (`tenants`, `users`, `customers`, `products`, `orders`, `messages`). Every query enforces `WHERE tenant_id = :current_tenant`.
* **REQ-8.2 India DPDP Act 2023 Compliance:** 100% data localization with infrastructure deployed in Google Cloud Platform Mumbai region (`asia-south1`).
* **REQ-8.3 Data Encryption:** AES-256 encryption for data at rest; TLS 1.3 for data in transit. PII masking in debug logs.
* **REQ-8.4 Immutable Audit Logging:** Every AI tool invocation, discount approval, and order status transition is recorded in `audit_logs` with actor identity, timestamp, and JSON parameter snapshot.

---

## 3. Minimum Viable POC Acceptance Gates (G1 to G9)

* **Gate G1 (Tenant Isolation):** API calls from Tenant B attempting to query Tenant A records return `HTTP 403 Forbidden`.
* **Gate G2 (5-Worker Number Ingestion):** 5 distinct WhatsApp numbers simultaneously ingest messages into 1 tenant workspace with accurate worker attribution.
* **Gate G3 (Hinglish Order Extraction):** Correctly parses *"Sharma Traders ko 50 box pipe kal bhej do"* into structured JSON order payload.
* **Gate G4 (Deterministic Credit Enforcement):** Orders exceeding customer credit limits transition to `PENDING_APPROVAL` status; automatic creation is blocked.
* **Gate G5 (Owner Dashboard Latency):** Aggregated multi-worker feed renders on web dashboard in $< 3.5\text{ seconds}$ from webhook arrival.
* **Gate G6 (Multi-Worker & Customer 360° Filtering):** Filtering by Worker isolates employee line; filtering by Customer aggregates all 5 worker chats with that party into 1 chronological thread without data loss.
* **Gate G7 (Immutable Audit Log):** All AI tool executions create verifiable records in `audit_logs` table.
* **Gate G8 (Conversational Catalog & Human Gate):** Inquiry for *"bed and table"* triggers clarifying questions for size/budget, dispatches photo cards, and routes discount requests $> 5\%$ or confidence $< 80\%$ to Owner Approval.
* **Gate G9 (Inbound Voice STT):** Inbound voice notes (`.ogg`/`.opus`) are transcribed to text and processed through NLP pipeline.

---

## 4. 30-Day Accelerated POC Execution Plan

```
+----------------------------------------------------------------------------------------------------+
|                                30-DAY POC SPRINT ROADMAP & MILESTONES                              |
+----------------------------------------------------------------------------------------------------+
| SPRINT 1 (DAYS 01 - 07): Cloud Infrastructure, PostgreSQL Schema & Meta Ingestion                  |
| • Day 01-02: Setup GCP Cloud Run, Managed PostgreSQL (Cloud SQL asia-south1), and Redis cache.    |
| • Day 03-04: Execute multi-tenant DDL schema with Row-Level Security (RLS) & isolation tests.     |
| • Day 05-07: Meta Cloud API webhook handler with HMAC-SHA256 & deduplication; map 5 test SIMs.    |
| • MILESTONE 1 GATE: Inbound messages from 5 worker lines stored with accurate worker attribution.  |
+----------------------------------------------------------------------------------------------------+
| SPRINT 2 (DAYS 08 - 15): AI Intelligence, MCP Tool Bus & Conversational Guardrails                 |
| • Day 08-10: Gemini Flash structured JSON output for Hinglish/Hindi Intent & Entity extraction.    |
| • Day 11-12: Deterministic MCP Tool Bus (search_catalog, check_stock, evaluate_credit, order_api). |
| • Day 13-15: Conversational discovery flow (bed/table size/budget) + 80% confidence & >5% discount |
|              Human Approval Gate + Inbound Voice Note STT transcription pipeline.                  |
| • MILESTONE 2 GATE: Orders & quotes parsed; deterministic credit & discount rules enforce approval.|
+----------------------------------------------------------------------------------------------------+
| SPRINT 3 (DAYS 16 - 22): React Owner Command Center & Gate G6 Multi-Worker Isolation                |
| • Day 16-18: Real-time React dashboard with Server-Sent Events (SSE) live feed across 5 lines.     |
| • Day 19-20: Implement Gate G6 (Worker-level isolation view vs Customer 360° unified timeline).   |
| • Day 21-22: 1-Click WhatsApp & Web Owner Approval interface + 8:00 PM Daily Business Pulse.      |
| • MILESTONE 3 GATE: Full Owner Command Center functional with live filtering & approval controls.  |
+----------------------------------------------------------------------------------------------------+
| SPRINT 4 (DAYS 23 - 30): Stress Testing, Production Hardening & Jaipur Pilot Go-Live               |
| • Day 23-25: End-to-end automated testing across 500 real Jaipur distributor chat samples (G1-G9).|
| • Day 26-27: Production deployment hardening on GCP Cloud Run, automated backups & audit logging.  |
| • Day 28-30: On-ground onboarding of First 3 Live Jaipur Distributors in Sitapura/VKI with Tally   |
|              catalog CSV imports, 30-minute staff training, and live commercial pilot kickoff.     |
| • MILESTONE 4 GATE: 3 Live Jaipur Distributors actively running daily operations on ZuvoCloud.     |
+----------------------------------------------------------------------------------------------------+
```

---

## 5. Commercial Pricing & Unit Economics Summary

* **5.1 Starter Growth Plan:** ₹2,999 / month (Up to 3 WhatsApp lines, 2,500 AI messages/mo, basic order extraction).
* **5.2 Business Pro Plan (Core):** ₹6,999 / month (Up to 8 WhatsApp lines, 10,000 AI messages/mo, full MCP tool bus, Owner Command Center, Tally 1-way sync).
* **5.3 Enterprise Distributor Plan:** ₹14,999 / month (Up to 20 WhatsApp lines, 30,000 AI messages/mo, Voice STT, priority custom rules, dedicated manager).
* **5.4 Unit Economics per Tenant:**
  * Monthly Subscription Revenue: ₹6,999.00
  * Direct COGS (Hosting + Gemini Flash AI + Storage): ₹715.00
  * **Net Contribution Margin per Tenant: ₹6,284.00 (89.8% Gross Margin)**
