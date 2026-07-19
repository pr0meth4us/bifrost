# Bifrost Workspace Rules & Developer Guidelines

This document outlines developer guidelines, onboarding manuals, client integration procedures, and pre-release testing pipelines for the Bifrost system.

---

## 🚀 1. Developer Onboarding & Principles

### Architectural Alignment
Bifrost acts as a central **control plane** (managing MongoDB credentials, metadata, global roles, and application secrets) while **proxying writes directly** to independent tenant databases (Postgres/Supabase) to keep tenant data isolated.

```
                  +-----------------------------------+
                  |      Bifrost Control Plane        |
                  |  - App Registry (Client IDs)      |
                  |  - Webhook Secrets (HMAC Keys)    |
                  |  - Vault Secrets (Encrypted API)  |
                  |  - MongoDB (Central Storage)      |
                  +-----------------+-----------------+
                                    |
                                    | Proxies Writes
                                    v
                  +-----------------------------------+
                  |       Tenant Data Plane           |
                  |  - PostgreSQL / Supabase Tables   |
                  |  - users, payments, entitlements  |
                  +-----------------------------------+
```

### Code Style & Scalability
1. **Database Proxying**: Do not import MongoDB models directly into tenant controllers. Instead, keep SQL and NoSQL operations separated. Use context managers (`get_tenant_db`) to prevent database pool starvation.
2. **Defensive SQL**: Always query column schemas dynamically (`information_schema.columns`) before inserting reject/refund comments to remain resilient to minor tenant-specific database deviations.
3. **Connection Pool Integrity**: Always wrap PostgreSQL cursor operations in the `get_tenant_db` context manager:
  ```python
  from bifrost.utils.tenant_db import get_tenant_db
  
  with get_tenant_db(db_connection_string) as conn:
      with conn.cursor() as cur:
          cur.execute("SELECT ...")
  ```

---

## 🔌 2. Client Application Adoption Guide (Adopting Bifrost)

To integrate a new client application (tenant) with Bifrost, follow these steps:

### Step 1: App Registration in Bifrost Control Plane
Create a document in the MongoDB `applications` collection containing:
* `client_id`: Unique slug identifier (e.g. `ministry_exam_prep`).
* `webhook_secret`: Decryption key and webhook validation token.
* `db_connection`: Scoped administrative database connection string (e.g., using a restricted user role like `bifrost_cms_agent`, *never* the master `service_role`).
* `custom_domain`: Custom DNS CNAME target mapping (e.g., `backoffice.wkc.kh`).

### Step 2: Secret Fetching & Injection
Client applications should use the Bifrost Python SDK or query the config endpoint directly at boot time:

```python
from bifrost_client import BifrostClient

# Bootstrapping credentials in the client application:
BifrostClient(
    client_id="ministry_exam_prep",
    webhook_secret="your_configured_webhook_secret",
    bifrost_url="https://bifrost.wkc.kh"
)
# The SDK automatically decrypts and injects all keys (e.g., DB_CONNECTION) into os.environ.
```

Or query the config REST endpoint directly via HTTP:
* **Endpoint**: `GET /api/v1/config`
* **Headers**: `X-Client-ID` & `X-Webhook-Secret`.

### Step 3: Webhook Listening & Signature Verification
Bifrost triggers a POST webhook payload to the client app's endpoint (configured under `webhook_url`) whenever a user subscription changes. You **must** verify the signature before processing entitlements.

#### Python Signature Verification:
```python
import hmac
import hashlib
from flask import Flask, request, abort

app = Flask(__name__)

WEBHOOK_SECRET = b"your_configured_webhook_secret"

@app.route('/bifrost-webhook', methods=['POST'])
def handle_webhook():
    signature = request.headers.get('X-Bifrost-Signature')
    if not signature or not hmac.compare_digest(
        hmac.new(WEBHOOK_SECRET, request.data, hashlib.sha256).hexdigest(),
        signature
    ):
        abort(403, "Invalid signature")
    # Process subscription update here
    return "OK", 200
```

### Step 4: Custom Feature Mix (Service Segmentation)
Downstream applications can toggle individual services on or off in their MongoDB application document using the `enabled_services` key:
* `secrets_vault`: Toggle vault API credentials retrieval.
* `payment_bot`: Toggle Telegram manual validation bots.
* `oauth_sso`: Toggle OAuth SSO endpoints.
* `sms_otp`: Toggle Phone SMS OTP validations.
* `email_otp`: Toggle email-based verification codes.
* `heimdall_monitor`: Toggle AI monitor metrics reporting.

If a service is marked `false`, the Bifrost gateway automatically rejects calls to those endpoints for that `client_id` with a `403 Forbidden` response.

---

## 🧪 3. Tester Verification Pipeline (Pre-Release Checklist)

Before merging new features or shipping changes, developers must execute this standard tester pipeline:

| Phase | Target Verification | Verification Command / Step |
| :--- | :--- | :--- |
| **1. Compile** | Syntax & Import Integrity | `python3 -m py_compile run.py config.py bifrost/__init__.py bot/main.py` |
| **2. DB Suite** | Postgres Model & Entitlements | Run `python3 scratch/test_tenant_db.py` to verify connection lifecycles and dynamic update logic. |
| **3. Bot Suite** | Telegram Bot SQL Ingest | Run `python3 scratch/test_bot_integration.py` to verify bot photo-to-Postgres insertion and callback payloads. |
| **4. Middleware** | Custom Host Header Redirection | Run `curl -H "Host: <custom_domain>" http://localhost:5000/` and check for auto-redirects. |
| **5. SLA Suite** | Telegram Webhook Dispatch | Call `/api/tenant/<app_id>/payments/notify-new` and verify that the target Telegram channel receives the SLA alert. |

---

## 🔄 4. Session Initiation Workflow (Memory Tracking)

To ensure seamless handoffs and continuous memory tracking across coding sessions:
1. **Context Extraction**: At the start of a session, boot memory using:
   ```bash
   ANTIGRAVITY_MEM_DB="/Users/nicksng/.antigravity-mem/memory.db" /opt/homebrew/bin/antigravity-mem context -p "/Users/nicksng/code/bifrost" -q "init"
   ```
2. **Annotation Logging**: During development, insert milestones directly into the SQLite notes table.
3. **Session Summary**: Before closing, run:
   ```bash
   GEMINI_API_KEY="<api_key>" GEMINI_MODEL="gemini-2.5-flash-lite" ANTIGRAVITY_MEM_DB="/Users/nicksng/.antigravity-mem/memory.db" /opt/homebrew/bin/antigravity-mem summarize -s "<session_id>"
   ```
4. **Obsidian Sync**: Export all session notes to the vault:
   ```bash
   python3 "/Users/nicksng/code/egd platform/scripts/python/export_to_obsidian.py"
   ```
