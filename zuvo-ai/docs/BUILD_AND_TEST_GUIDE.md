> ⚠️ **Outdated:** this build and test guide describes the removed monolith (`app/`). For the current microservice stack, see `README.md` and `docs/ARCHITECTURE.md`.

# ZuvoCloud Build, Deployment & Testing Runbook
## Institutional Step-by-Step Operations & Quality Assurance Guide

**Document Version:** 1.0  
**Target Audience:** DevOps Engineers, Backend Developers, QA Leads, Field Engineers  
**System:** ZuvoCloud WhatsApp AI Operations Platform  

---

## 1. System Prerequisites

Before starting, ensure your host machine has the following tools installed:

| Tool | Minimum Version | Recommended Purpose |
| :--- | :--- | :--- |
| **Python** | 3.10+ (or 3.9+) | Local development, running seed scripts & test suites |
| **pip** | 22.0+ | Python dependency management |
| **Docker & Docker Compose** | 24.0+ / Compose v2 | Containerized microservice deployment |
| **cURL / Postman** | Any modern version | API endpoint verification |
| **Git** | 2.30+ | Source code version control |

---

## 2. Configuration Setup

### Step 2.1: Clone & Configure Environment Variables
From the project root directory (`C:\js\zuvo-ai`):

```bash
# Copy example environment configuration
cp .env.example .env
```

### Step 2.2: Review & Customize Settings
Inspect `.env` or `config/config.yaml` to configure your environment:

```ini
# Application Environment (development, staging, production)
APP_ENV=development
APP_DEBUG=true
PORT=8000

# Database Configuration
# Local SQLite for immediate zero-dependency testing:
DATABASE_URL=sqlite+aiosqlite:///./zuvo_local.db
# Or PostgreSQL for production / Docker:
# DATABASE_URL=postgresql+asyncpg://zuvo_user:zuvo_password@postgres:5432/zuvo_db

# Redis Cache & Idempotency
REDIS_URL=redis://localhost:6379/0

# Meta WhatsApp Cloud API Credentials
META_VERIFY_TOKEN=zuvo_secret_verify_token_jaipur_2026
META_APP_SECRET=mock_meta_app_secret
META_SYSTEM_USER_TOKEN=mock_system_user_token
META_API_VERSION=v20.0

# Security Guardrails (Set to true in production)
REQUIRE_HMAC_VERIFICATION=false
```

---

## 3. Local Python Build & Execution (Fast Path)

Follow these steps for immediate local development without starting Docker:

### Step 3.1: Install Dependencies
```bash
pip install -r requirements.txt
```

### Step 3.2: Seed Database with Jaipur Wholesale Master Data
Execute the built-in database seeder. This creates:
* **1 Business Tenant:** *"Jaipur Hardware & Sanitary Distributors"*
* **1 Business Owner:** *"Sanjay Agarwal (Managing Partner)"*
* **5 Worker WhatsApp Lines:**
  1. *Ramesh Sharma* (`+91 98290-11111` / `PNID_001` - Counter Sales)
  2. *Suresh Verma* (`+91 98290-22222` / `PNID_002` - Field Sales)
  3. *Mukesh Saini* (`+91 98290-33333` / `PNID_003` - Godown Dispatch)
  4. *Deepak Gupta* (`+91 98290-44444` / `PNID_004` - Accounts & Recovery)
  5. *Amit Joshi* (`+91 98290-55555` / `PNID_005` - Customer Support)
* **3 Initial Customer Accounts:**
  * *Sharma Traders VKI* (`+91 94140-99999`)
  * *Khandelwal Sanitary Mansarovar* (`+91 94140-88888`)
  * *Rajputana Electricals Sitapura* (`+91 94140-77777`)

```bash
python scripts/seed_data.py
```

### Step 3.3: Launch FastAPI Application Server
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

* **Interactive Swagger UI:** Visit [http://localhost:8000/docs](http://localhost:8000/docs) in your browser.
* **Healthcheck:** Visit [http://localhost:8000/api/v1/health](http://localhost:8000/api/v1/health).

---

## 4. Docker Compose Microservice Build & Deployment

For containerized production or staging environments with PostgreSQL 16 and Redis 7:

```
+-------------------------------------------------------------------------+
|                    DOCKER COMPOSE MICROSERVICE STACK                    |
|                                                                         |
|   +-------------------+   +--------------------+   +----------------+   |
|   |  FastAPI Backend  |   |   PostgreSQL 16    |   |    Redis 7     |   |
|   |   (Port: 8000)    |-->|    (Port: 5432)    |   |  (Port: 6379)  |   |
|   +-------------------+   +--------------------+   +----------------+   |
|             |                       |                       |           |
|             +-----------------------+-----------------------+           |
|                                 |                                       |
|                       zuvo_network (Bridge)                             |
+-------------------------------------------------------------------------+
```

### Step 4.1: Build and Launch Containers
```bash
docker compose up -d --build
```

### Step 4.2: Verify Container Status & Health
```bash
docker compose ps
```
*Expected Output:*
```text
NAME                 IMAGE                STATUS                    PORTS
zuvocloud_api        zuvo-ai-api          Up (healthy) 8000->8000/tcp
zuvocloud_postgres   postgres:16-alpine   Up (healthy) 5432->5432/tcp
zuvocloud_redis      redis:7-alpine       Up (healthy) 6379->6379/tcp
```

### Step 4.3: View Application Logs
```bash
docker compose logs -f api
```

### Step 4.4: Stopping the Containers
```bash
docker compose down
```

---

## 5. Running the Automated Test Suite

The project includes an institutional automated test suite covering webhook handshakes, multi-number attribution, idempotency deduplication, and Gate G6 isolation.

```bash
python -m pytest tests/ -v
```

### Test Suite Coverage Matrix:
```text
tests/test_g6_dashboard_filter.py::test_gate_g6_worker_isolation_and_customer_360_timeline PASSED [ 16%]
tests/test_multi_number_routing.py::test_multi_number_to_multi_worker_attribution PASSED           [ 33%]
tests/test_multi_number_routing.py::test_idempotency_deduplication PASSED                          [ 50%]
tests/test_webhook.py::test_webhook_verification_handshake_success                                 [ 66%]
tests/test_webhook.py::test_webhook_verification_handshake_invalid_token                           [ 83%]
tests/test_webhook.py::test_inbound_webhook_event_ingestion                                        [100%]
======================================== 6 passed in 0.82s ========================================
```

---

## 6. Simulating Inbound Webhook Traffic & Testing Gate G6

To test multi-worker routing without configuring live Meta developer tokens, execute the simulation script:

```bash
python scripts/test_webhook_simulation.py
```

### What this script verifies:
1. Simulates Customer A (*Sharma Traders*) messaging Worker 1 (*Ramesh Counter*).
2. Simulates Customer A (*Sharma Traders*) messaging Worker 2 (*Suresh Field Rep*).
3. Simulates Customer B (*Khandelwal Sanitary*) messaging Worker 1 (*Ramesh Counter*).
4. **Validates Gate G6 Worker Isolation:** Queries Ramesh's feed and proves it contains only Ramesh's chats.
5. **Validates Gate G6 Customer 360° Timeline:** Queries Sharma Traders' feed and proves it merges chats across both Ramesh and Suresh into one chronological historical thread.

---

## 7. Manual Testing via cURL Commands

### 7.1 Check System Health
```bash
curl -X GET "http://localhost:8000/api/v1/health"
```
**Response:**
```json
{
  "status": "healthy",
  "app_name": "ZuvoCloud WhatsApp AI Operations Platform",
  "version": "1.0.0",
  "environment": "development",
  "components": {
    "database": "healthy",
    "redis_cache": "in_memory_fallback",
    "meta_cloud_api": "configured"
  }
}
```

---

### 7.2 Meta Webhook Handshake Verification (`GET`)
```bash
curl -X GET "http://localhost:8000/api/v1/whatsapp/webhook?hub.mode=subscribe&hub.verify_token=zuvo_secret_verify_token_jaipur_2026&hub.challenge=1158201244"
```
**Expected Response:** `1158201244` (HTTP 200)

---

### 7.3 Simulate Inbound Customer Message (`POST`)
```bash
curl -X POST "http://localhost:8000/api/v1/messages/simulate-inbound?meta_phone_number_id=PNID_001&customer_phone=%2B919414099999&customer_name=Sharma%20Traders&message_text=50%20box%20pipe%20kal%20subah%20bhej%20do"
```

---

### 7.4 Send Outbound Message (`POST`)
```bash
curl -X POST "http://localhost:8000/api/v1/messages/send?tenant_id=c470e6b1-5931-4cbd-ab8c-bc575c47a86b" \
  -H "Content-Type: application/json" \
  -d '{
    "recipient_phone": "+919414099999",
    "message_text": "Namaste! Aapka 50 box pipe ka order record ho gaya hai.",
    "sender_number_id": "PNID_001"
  }'
```

---

### 7.5 Query Owner Command Center with Gate G6 Filters (`GET`)

* **Query All Messages for Tenant:**
```bash
curl -X GET "http://localhost:8000/api/v1/dashboard/feed?tenant_id=c470e6b1-5931-4cbd-ab8c-bc575c47a86b"
```

* **Gate G6 Filter by Worker Line (Worker Ramesh):**
```bash
curl -X GET "http://localhost:8000/api/v1/dashboard/feed?tenant_id=c470e6b1-5931-4cbd-ab8c-bc575c47a86b&worker_id={worker_ramesh_id}"
```

* **Gate G6 Customer 360° Timeline (Sharma Traders):**
```bash
curl -X GET "http://localhost:8000/api/v1/dashboard/feed?tenant_id=c470e6b1-5931-4cbd-ab8c-bc575c47a86b&customer_phone=%2B919414099999"
```

---

## 8. Meta WhatsApp Cloud API Production Setup

When connecting real WhatsApp Business phone numbers on Meta Developer Portal:

1. **Create Meta Developer App:**
   * Go to [developers.facebook.com](https://developers.facebook.com/) $\to$ Create App $\to$ Select **Other** $\to$ Select **Business**.
   * Add the **WhatsApp** product to your app.
2. **Configure Webhook:**
   * In WhatsApp Settings $\to$ **Configuration** $\to$ Click **Edit Webhook**.
   * **Callback URL:** `https://your-domain.com/api/v1/whatsapp/webhook`
   * **Verify Token:** `zuvo_secret_verify_token_jaipur_2026` (Matches `META_VERIFY_TOKEN`).
   * Subscribe to the **`messages`** webhook field.
3. **Register WhatsApp Numbers:**
   * In WhatsApp $\to$ **API Setup** $\to$ Add your physical SIM / virtual numbers.
   * Copy each number's **Phone Number ID** (e.g. `104928374928194`).
   * Insert into `whatsapp_numbers` table mapping to the appropriate `tenant_id` and `assigned_user_id`.
4. **Generate Permanent System User Access Token:**
   * Go to Meta Business Manager $\to$ System Users $\to$ Generate Token with `whatsapp_business_messaging` and `whatsapp_business_management` permissions.
   * Set in `.env` as `META_SYSTEM_USER_TOKEN`.

---

## 9. Troubleshooting & FAQ

### Q1: `ModuleNotFoundError: No module named 'asyncpg'` during local testing
**Fix:** The local development setup is configured to use `sqlite+aiosqlite:///./zuvo_local.db` by default. When deploying to Docker, `DATABASE_URL` is automatically overridden to PostgreSQL with `asyncpg`.

### Q2: Port 8000 or 5432 already in use on host
**Fix:** Change host port mapping in `.env` (e.g., `PORT=8080`) or in `docker-compose.yml` (e.g., `"5433:5432"`).

### Q3: Redis connection fails on local development
**Fix:** ZuvoCloud has a built-in **automatic in-memory fallback cache** for idempotency deduplication if Redis is not running locally. The system will log a warning and continue operating safely.
