> ⚠️ **Outdated:** this api reference describes the removed monolith (`app/`). For the current microservice stack, see `README.md` and `docs/ARCHITECTURE.md`.

# ZuvoCloud REST API Reference

This document provides the complete API contracts, request payloads, response schemas, and operational status codes for the ZuvoCloud WhatsApp AI Operations Platform.

**Base URL:** `http://localhost:8000/api/v1` (or `https://api.zuvocloud.com/api/v1`)

---

## 1. System Health & Diagnostics

### `GET /health`
Returns runtime operational status of application components.

* **Query Parameters:** None
* **Response `200 OK`:**
```json
{
  "status": "healthy",
  "app_name": "ZuvoCloud WhatsApp AI Operations Platform",
  "version": "1.0.0",
  "environment": "development",
  "components": {
    "database": "healthy",
    "redis_cache": "connected",
    "meta_cloud_api": "configured"
  }
}
```

---

## 2. Meta WhatsApp Cloud API Webhooks

### `GET /whatsapp/webhook`
Handles Meta WhatsApp Webhook handshake verification challenge.

* **Query Parameters:**
  * `hub.mode` (string, required): Must be `subscribe`
  * `hub.verify_token` (string, required): Verification token configured in Meta Developer Portal
  * `hub.challenge` (string, required): Arbitrary challenge string sent by Meta
* **Response `200 OK`:** Returns plain text containing `hub.challenge`
* **Response `403 Forbidden`:** If `hub.verify_token` does not match configured secret

---

### `POST /whatsapp/webhook`
Receives live message events from Meta WhatsApp Business Platform.

* **Headers:**
  * `X-Hub-Signature-256` (string, optional/enforced): Cryptographic HMAC-SHA256 signature
* **Request Body:** Standard Meta WhatsApp Webhook Payload:
```json
{
  "object": "whatsapp_business_account",
  "entry": [
    {
      "id": "WHATSAPP_BUSINESS_ACCOUNT_ID",
      "changes": [
        {
          "value": {
            "messaging_product": "whatsapp",
            "metadata": {
              "display_phone_number": "919829011111",
              "phone_number_id": "PNID_001"
            },
            "contacts": [
              {
                "profile": { "name": "Sharma Traders VKI" },
                "wa_id": "919414099999"
              }
            ],
            "messages": [
              {
                "from": "919414099999",
                "id": "wamid.HBgLOTE5NDE0MDk5OTk5FQIAEhgg...",
                "timestamp": "1724263200",
                "text": { "body": "50 box pipe kal bhej do" },
                "type": "text"
              }
            ]
          },
          "field": "messages"
        }
      ]
    }
  ]
}
```
* **Response `200 OK`:**
```json
{
  "status": "ok",
  "messages_processed_count": 1,
  "details": [
    {
      "message_id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
      "wamid": "wamid.HBgLOTE5...",
      "tenant_id": "c470e6b1-5931-4cbd-ab8c-bc575c47a86b",
      "worker_id": "3da5cdd4-5f3b-422c-b0e9-702e6e9b1280",
      "worker_name": "Ramesh Sharma",
      "whatsapp_line": "+919829011111",
      "customer_id": "2e46799a-9aaf-41e9-8bc2-39d51387bc84",
      "customer_name": "Sharma Traders VKI",
      "customer_phone": "+919414099999",
      "content": "50 box pipe kal bhej do",
      "created_at": "2026-08-21T18:00:00"
    }
  ]
}
```

---

## 3. Messages & Outbound Dispatch

### `POST /messages/send`
Dispatches an outbound text message to a customer via Meta Cloud API or simulation.

* **Query Parameters:**
  * `tenant_id` (string, required): UUID of the business tenant
* **Request Body:**
```json
{
  "recipient_phone": "+919414099999",
  "message_text": "Namaste! Order confirm ho gaya hai.",
  "sender_number_id": "PNID_001"
}
```
* **Response `200 OK`:**
```json
{
  "success": true,
  "wamid": "wamid.OUT_1724263200",
  "recipient_phone": "+919414099999",
  "meta_response": {
    "messaging_product": "whatsapp",
    "messages": [
      {
        "id": "wamid.OUT_1724263200"
      }
    ]
  }
}
```

---

### `POST /messages/simulate-inbound`
Simulates an incoming WhatsApp message payload for rapid local testing.

* **Query Parameters:**
  * `meta_phone_number_id` (string, required): e.g. `PNID_001`
  * `customer_phone` (string, required): e.g. `+919414099999`
  * `customer_name` (string, required): e.g. `Sharma Traders`
  * `message_text` (string, required): e.g. `50 box pipe kal bhej do`
* **Response `200 OK`:**
```json
{
  "status": "ok",
  "simulated_wamid": "wamid.SIM_+919414099999_1724263200000",
  "details": [...]
}
```

---

## 4. Owner Dashboard & Gate G6 Filtering

### `GET /dashboard/feed`
Retrieves the real-time aggregated message timeline with Gate G6 filtering options.

* **Query Parameters:**
  * `tenant_id` (string, required): UUID of the business tenant
  * `worker_id` (string, optional): **Gate G6 Filter 1:** Isolates a specific worker's line
  * `customer_phone` (string, optional): **Gate G6 Filter 2:** Unifies all staff chats with customer
  * `customer_id` (string, optional): Filter by internal customer ID
  * `limit` (integer, default `50`): Number of messages to return (1–200)
  * `offset` (integer, default `0`): Pagination offset
* **Response `200 OK`:**
```json
{
  "metrics": {
    "tenant_id": "c470e6b1-5931-4cbd-ab8c-bc575c47a86b",
    "business_name": "Jaipur Hardware & Sanitary Distributors",
    "total_messages_today": 4,
    "total_inbound": 4,
    "total_outbound": 0,
    "active_workers_count": 5,
    "active_customers_count": 3,
    "pending_approvals_count": 0
  },
  "workers": [
    {
      "worker_id": "3da5cdd4-5f3b-422c-b0e9-702e6e9b1280",
      "worker_name": "Ramesh Sharma",
      "designation": "Counter Sales & Billing",
      "phone_number": "+919829011111",
      "meta_phone_number_id": "PNID_001",
      "total_messages_today": 0,
      "active_conversations_count": 0
    }
  ],
  "messages": [
    {
      "id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
      "wamid": "wamid.SIM_...",
      "direction": "INBOUND",
      "sender_phone": "+919414099999",
      "recipient_phone": "+919829011111",
      "message_type": "text",
      "content": "Ramesh ji, 20mm CPVC pipe ke 50 box kal bhej do urgent.",
      "parsed_intent": null,
      "confidence_score": null,
      "worker_id": "3da5cdd4-5f3b-422c-b0e9-702e6e9b1280",
      "worker_name": "Ramesh Sharma",
      "worker_phone": "+919829011111",
      "customer_id": "2e46799a-9aaf-41e9-8bc2-39d51387bc84",
      "customer_name": "Sharma Traders VKI",
      "customer_phone": "+919414099999",
      "created_at": "2026-08-21T18:00:00"
    }
  ],
  "filter_applied": {
    "worker_id": null,
    "customer_id": null,
    "customer_phone": "+919414099999",
    "limit": 50,
    "offset": 0
  }
}
```

---

### `GET /dashboard/tenants`
Lists all registered business tenants, active workers, and connected WhatsApp lines.

* **Query Parameters:** None
* **Response `200 OK`:**
```json
[
  {
    "tenant_id": "c470e6b1-5931-4cbd-ab8c-bc575c47a86b",
    "business_name": "Jaipur Hardware & Sanitary Distributors",
    "city": "Jaipur",
    "connected_lines_count": 5,
    "whatsapp_lines": [
      {
        "number_id": "546067ec-e03b-42aa-8c27-0e0b4c0eccf9",
        "meta_phone_number_id": "PNID_001",
        "display_phone_number": "+919829011111",
        "assigned_worker": "Ramesh Sharma",
        "worker_id": "3da5cdd4-5f3b-422c-b0e9-702e6e9b1280",
        "status": "ACTIVE"
      }
    ]
  }
]
```
