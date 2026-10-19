> **Update:** secrets now go only in `.env`, and line mapping lives in `config/tenant-routing.yaml`. `config/config.yaml` and `scripts/seed_data.py` were removed. Set `META_DRY_RUN=false` in `.env` to send real messages.

# Live Meta WhatsApp Cloud API Integration Guide
## Connecting Real WhatsApp Business Phone Numbers to ZuvoCloud

This operational runbook explains **where WhatsApp numbers are registered** and **how to integrate live WhatsApp numbers** with your ZuvoCloud application.

---

## 1. Where do you configure WhatsApp Numbers?

There are **three specific layers** where numbers and credentials live:

```
+-----------------------------------------------------------------------------------------+
|                                THREE CONFIGURATION LAYERS                                |
+-----------------------------------------------------------------------------------------+
| LAYER 1: META DEVELOPER PORTAL (Cloud API Provider)                                     |
| • Meta assigns a unique `phone_number_id` (e.g. `104928374928194`) to each SIM/number.  |
| • You configure your Webhook URL (`https://your-domain.com/api/v1/whatsapp/webhook`).    |
| • You configure the Webhook `Verify Token` (e.g. `zuvo_secret_verify_token_jaipur_2026`).|
+-----------------------------------------------------------------------------------------+
| LAYER 2: APPLICATION ENVIRONMENT (`.env` / `config/config.yaml`)                       |
| • Holds your Meta App credentials:                                                      |
|     META_VERIFY_TOKEN=zuvo_secret_verify_token_jaipur_2026                              |
|     META_APP_SECRET=your_meta_app_secret                                                |
|     META_SYSTEM_USER_TOKEN=EAAG... (Permanent Long-Lived Bearer Token)                  |
+-----------------------------------------------------------------------------------------+
| LAYER 3: ZUVOCLOUD DATABASE (`whatsapp_numbers` table)                                  |
| • Maps each Meta `phone_number_id` to a specific Employee/Worker and Business Tenant.    |
|   Example:                                                                              |
|     meta_phone_number_id : "104928374928194"                                            |
|     display_phone_number : "+919829011111"                                              |
|     assigned_user_id     : "3da5cdd4-..." (Ramesh - Counter Sales)                      |
|     tenant_id            : "c470e6b1-..." (Jaipur Hardware Distributors)                |
+-----------------------------------------------------------------------------------------+
```

---

## 2. End-to-End Live Integration Runbook (Step-by-Step)

Follow these **6 practical steps** to connect a live WhatsApp number:

---

### STEP 1: Expose ZuvoCloud to the Internet (Public HTTPS Webhook URL)
Meta’s Cloud API servers cannot send webhooks to `localhost:8000`. You need a public HTTPS endpoint.

* **For Production / Staging:** Deploy on GCP Cloud Run, AWS, or your VPS:
  $$\text{Webhook URL: } \texttt{https://api.yourdomain.com/api/v1/whatsapp/webhook}$$
* **For Local Testing (via ngrok):**
  ```bash
  # In a separate terminal, expose port 8000
  ngrok http 8000
  ```
  ngrok will give you an HTTPS URL such as: `https://a1b2-your-subdomain.ngrok-free.app`  
  Your Webhook URL will be:  
  $$\texttt{https://a1b2-your-subdomain.ngrok-free.app/api/v1/whatsapp/webhook}$$

---

### STEP 2: Create a Meta Developer App & WhatsApp Business Account (WABA)

1. Open [developers.facebook.com](https://developers.facebook.com/) and log in with your Facebook account.
2. Click **My Apps** $\to$ **Create App**.
3. Select **Other** $\to$ Select **Business** as the app type.
4. Name your app (e.g. `ZuvoCloud Operations Platform`) and link your Meta Business Account.
5. In the App Dashboard, scroll to **Add Products to Your App** and click **Set up** on the **WhatsApp** tile.

---

### STEP 3: Register Your Real WhatsApp Phone Number in Meta

1. In the left sidebar, navigate to **WhatsApp** $\to$ **API Setup**.
2. Under **Step 5: Add a phone number**:
   * Click **Add phone number**.
   * Enter your Business Display Name, Category, and Business Description.
   * Enter the physical SIM or virtual phone number (e.g. `+91 98290 11111`).
   * Verify the number via SMS OTP or Voice call.
3. Once verified, Meta will display:
   * **Phone number ID:** (e.g. `104928374928194`) $\leftarrow$ **SAVE THIS ID!**
   * **WhatsApp Business Account ID:** (e.g. `109283746192834`)

> [!IMPORTANT]
> **Phone Co-existence Rule:** A phone number linked to the WhatsApp Cloud API **cannot** be simultaneously logged into the standard consumer WhatsApp mobile app on a phone. Use a dedicated business SIM.

---

### STEP 4: Configure the Webhook in Meta Developer Portal

1. In the left sidebar, navigate to **WhatsApp** $\to$ **Configuration**.
2. Next to **Webhook**, click **Edit**.
3. Enter your details:
   * **Callback URL:** `https://api.yourdomain.com/api/v1/whatsapp/webhook` (or your ngrok URL)
   * **Verify Token:** `zuvo_secret_verify_token_jaipur_2026` (Must match `META_VERIFY_TOKEN` in `.env`)
4. Click **Verify and Save**. Meta will send a `GET` request to your server to validate the handshake.
5. Under **Webhook fields**, click **Manage** $\to$ Find **`messages`** $\to$ Click **Subscribe**.

---

### STEP 5: Generate a Permanent System User Access Token

To send outbound replies without tokens expiring every 24 hours:

1. Open [business.facebook.com/settings](https://business.facebook.com/settings) (Meta Business Suite Settings).
2. Go to **Users** $\to$ **System Users** $\to$ Click **Add**.
3. Name the system user (e.g. `zuvo-api-service`) and set role to **Admin**.
4. Click **Assign Assets** $\to$ Select **Apps** $\to$ Select your ZuvoCloud app $\to$ Grant **Full Control**.
5. Click **Generate New Token** on the system user:
   * Select your App.
   * Check permissions: `whatsapp_business_messaging` and `whatsapp_business_management`.
   * Set Token Expiration to **Never** (Permanent Token).
6. Copy the token and paste it into your `.env` file:
   ```ini
   META_SYSTEM_USER_TOKEN=EAAG...your_permanent_token_here...
   ```

---

### STEP 6: Register the Number in the ZuvoCloud Database

Now link Meta's `Phone Number ID` to your employee in ZuvoCloud using SQL or Python:

#### Option A: Via SQL Insert (PostgreSQL / SQLite)
```sql
INSERT INTO whatsapp_numbers (
    id, 
    tenant_id, 
    assigned_user_id, 
    meta_phone_number_id, 
    display_phone_number, 
    display_name, 
    status, 
    created_at
) VALUES (
    'wn-001-uuid',
    'c470e6b1-5931-4cbd-ab8c-bc575c47a86b', -- Tenant ID (Jaipur Hardware)
    '3da5cdd4-5f3b-422c-b0e9-702e6e9b1280', -- Worker ID (Ramesh Counter)
    '104928374928194',                      -- Meta's Phone Number ID from Step 3
    '+919829011111',                        -- Display Number
    'Ramesh Sharma (Counter Sales Line)',
    'ACTIVE',
    CURRENT_TIMESTAMP
);
```

#### Option B: Via Python Helper Script
You can modify `scripts/seed_data.py` with your real `meta_phone_number_id` values:

```python
# scripts/seed_data.py
workers_data = [
    # Full Name,      Phone Number,     Real Meta Phone Number ID, Designation
    ("Ramesh Sharma", "+919829011111", "104928374928194",        "Counter Sales & Billing"),
    ("Suresh Verma",  "+919829022222", "104928374928195",        "Field Sales Rep")
]
```
Run:
```bash
python scripts/seed_data.py
```

---

## 3. Live Testing Verification Procedure

1. **Send Inbound Message:**  
   Take your personal smartphone and send a WhatsApp message (*"50 box 20mm pipe kal bhej do"*) to the registered business number (`+91 98290 11111`).
2. **Verify Server Logs:**  
   ZuvoCloud logs will show:
   ```text
   [INFO] Ingesting Inbound Message: From=+9194****9999 To=+919829011111 (Worker=Ramesh Sharma)
   [INFO] Resolved routing: phone_number_id=104928374928194 -> Tenant='Jaipur Hardware Distributors', Worker='Ramesh Sharma'
   ```
3. **Verify Owner Command Center:**  
   Open `http://localhost:8000/api/v1/dashboard/feed?tenant_id={your_tenant_id}`.  
   The message will be visible with attribution to Ramesh Sharma!
4. **Send Outbound WhatsApp Reply:**  
   Call `POST /api/v1/messages/send`:
   ```bash
   curl -X POST "http://localhost:8000/api/v1/messages/send?tenant_id={tenant_id}" \
     -H "Content-Type: application/json" \
     -d '{
       "recipient_phone": "+919414099999",
       "message_text": "Namaste! Aapka 50 box pipe ka order record kar liya hai.",
       "sender_number_id": "104928374928194"
     }'
   ```
   The customer will receive the live message directly on their personal WhatsApp!
