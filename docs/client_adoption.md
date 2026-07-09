# Bifrost Client Application Adoption Guide

This guide describes how to connect downstream client applications (tenants) to Bifrost for dynamic secrets management and subscription entitlement syncing.

---

## 1. Secrets Vault Integration

Bifrost acts as a Secret Vault. When your application boots, it should pull its configurations dynamically from Bifrost, keeping API keys out of repository source files.

### 1.1 Method A: Python SDK (Recommended)
Add [bifrost_client.py](file:///Users/nicksng/code/bifrost/sdk/python/bifrost_client.py) to your application codebase and run the bootstrapper:

```python
from bifrost_client import BifrostClient
import os

# 1. Initialize client (downloads, decrypts, and caches vault keys)
BifrostClient(
    client_id=os.getenv("BIFROST_CLIENT_ID"),
    webhook_secret=os.getenv("BIFROST_WEBHOOK_SECRET"),
    bifrost_url=os.getenv("BIFROST_URL", "https://bifrost.wkc.kh")
)

# 2. Retrieve variables directly from environment memory
# (BifrostClient automatically injected these keys into os.environ)
db_url = os.environ.get("DB_CONNECTION")
stripe_key = os.environ.get("STRIPE_API_KEY")
```

### 1.2 Method B: Raw HTTP config loading (Node.js/Go/etc.)
Query the configuration REST endpoint:
* **Endpoint**: `GET /api/v1/config`
* **Headers Required**:
  * `X-Client-ID`: Your registered client ID slug.
  * `X-Webhook-Secret`: Your decryption secret.

#### Node.js Bootstrap Example:
```javascript
const axios = require('axios');
const crypto = require('crypto');

async function bootstrapConfig() {
  const endpoint = `${process.env.BIFROST_URL}/api/v1/config`;
  const headers = {
    'X-Client-ID': process.env.BIFROST_CLIENT_ID,
    'X-Webhook-Secret': process.env.BIFROST_WEBHOOK_SECRET
  };

  try {
    const response = await axios.get(endpoint, { headers });
    const keys = response.data.data.api_keys;
    
    // Inject into environment memory
    for (const [key, val] of Object.entries(keys)) {
      process.env[key] = val;
    }
    console.log("Config loaded successfully from Bifrost.");
  } catch (err) {
    console.error("Bifrost config bootstrap failed:", err.message);
  }
}
```

---

## 2. Webhook Entitlement Syncing

Whenever a payment is validated or a subscription is altered, Bifrost triggers a webhook POST payload to your client application's configured `webhook_url`.

### Payload Structure
```json
{
  "event_type": "subscription_success",
  "account_id": "user_mongo_or_telegram_id",
  "specific_app_id": "app_object_id",
  "timestamp": 1783519579457,
  "extra_data": {
    "payment_id": "42",
    "txn_ref": "tx-12345",
    "amount": "10.00",
    "role": "premium_user",
    "method": "manual_approval"
  }
}
```

### Security: Verifying Signatures
Bifrost signs webhook payloads using HMAC SHA-256 with the application's `webhook_secret`. You **must** verify the signature before processing entitlements.

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
    if not signature:
        abort(400, "Missing signature")
        
    payload = request.data
    expected_signature = hmac.new(
        WEBHOOK_SECRET,
        payload,
        hashlib.sha256
    ).hexdigest()
    
    if not hmac.compare_digest(expected_signature, signature):
        abort(403, "Invalid signature")
        
    data = request.json
    # Process subscription update here
    print(f"Subscription event: {data['event_type']} for user {data['account_id']}")
    return "OK", 200
```

#### Node.js Signature Verification:
```javascript
const express = require('express');
const crypto = require('crypto');
const app = express();

const WEBHOOK_SECRET = 'your_configured_webhook_secret';

app.post('/bifrost-webhook', express.raw({ type: 'application/json' }), (req, res) => {
  const signature = req.headers['x-bifrost-signature'];
  if (!signature) {
    return res.status(400).send('Missing signature');
  }

  const hash = crypto
    .createHmac('sha256', WEBHOOK_SECRET)
    .update(req.body)
    .digest('hex');

  if (hash !== signature) {
    return res.status(403).send('Invalid signature');
  }

  const payload = JSON.parse(req.body.toString());
  // Process subscription update here
  console.log(`Verified webhook event: ${payload.event_type}`);
  res.status(200).send('OK');
});
```

---

## 3. Custom Domain Setup (Whitelabeling)

To configure a custom dashboard mapping (e.g. `backoffice.yourcompany.com`) to route directly to your console:
1. Log in to your DNS provider (Cloudflare, AWS Route53, GoDaddy, etc.).
2. Add a `CNAME` record:
   * **Host/Name**: `backoffice.yourcompany.com`
   * **Value/Target**: `cname.bifrost.io` (or your central server domain).
3. Set the CNAME record to proxy (e.g., DNS-only for Caddy or proxy-enabled for Cloudflare SSL for SaaS).
4. Register the domain in the Bifrost CMS under application settings. Bifrost will automatically handle TLS certificate issuance and routing.

---

## 4. Service Segmentation & Feature Toggles

Client applications do not need to adopt all of Bifrost's capabilities. You can toggle individual services on or off in the application's configuration document (`enabled_services` map).

### 4.1 Available Toggles
* **`secrets_vault`** (default: `true`): Enables secrets retrieval via the SDK or config endpoints. If disabled, calls to `/api/v1/config` return `403 Forbidden`.
* **`payment_bot`** (default: `true`): Activates Telegram bot verification hooks. If disabled, incoming bot messages/callback events return `403 Forbidden`.
* **`oauth_sso`** (default: `true`): Enables SSO OAuth2 login routers (Google, GitHub, Apple, etc.).
* **`sms_otp`** (default: `true`): Enables SMS OTP logging and authentication via Twilio.
* **`email_otp`** (default: `true`): Enables transactional email OTP verification.
* **`heimdall_monitor`** (default: `true`): Gathers AI model token usage and cost metrics.

### 4.2 Configuration Example (MongoDB Application Registry)
To configure your app to only adopt the Secrets Vault and Payment Bot while disabling OAuth and SMS logins:
```json
{
  "client_id": "ministry_exam_prep",
  "enabled_services": {
    "secrets_vault": true,
    "payment_bot": true,
    "oauth_sso": false,
    "sms_otp": false,
    "email_otp": false,
    "heimdall_monitor": false
  }
}
```
If a service is marked `false`, the Bifrost gateway instantly blocks requests to that service's endpoints for your client application.
