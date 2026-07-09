# Bifrost Developer Onboarding & Architecture Guide

This guide provides developers with the knowledge required to onboard, configure, and maintain the Bifrost system.

---

## 1. System Philosophy & Plane Isolation

Bifrost separates the management of secret configurations (Control Plane) from operational tenant data (Data Plane) to prevent cross-tenant security vulnerabilities and database resource bottlenecks.

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

### 1.1 The Control Plane (MongoDB)
* **Branding & Metadata**: Contains application slugs, CNAME/custom domains, and config profiles.
* **Secrets Vault**: Holds encrypted client configurations (like payment gateway keys and LLM tokens). These are decrypted only on demand using the application's `webhook_secret` as the decryption key.

### 1.2 The Data Plane (PostgreSQL)
* Bifrost **never** caches or stores customer transaction records internally for SQL tenants. 
* All manual payments, subscriptions, and override operations are executed directly against the tenant's PostgreSQL database using a thread-local connection pool wrapper.

---

## 2. Directory Map & Component Roles

Here is a map of the Bifrost codebase layout:

```
bifrost/
├── __init__.py                # Flask app initialization, middleware, Host routing
├── config.py                  # Global application configuration settings
├── config_api.py              # Vault secret fetching and bulk upload REST APIs
├── backoffice/                # Flask Administration Console blueprint
│   ├── __init__.py            # Access decorators, RBAC checks, blueprint definition
│   ├── app_routes.py          # App settings, configuration panels, dashboard
│   ├── auth_routes.py         # Login, MFA, password reset routes
│   └── tenant_routes.py       # SQL Payment queue and user overrides controllers
├── models/                    # Core database interface layers
│   ├── __init__.py            # Main BifrostDB class wrapper (MongoDB)
│   ├── apps.py                # MongoDB App register and update methods
│   └── payments.py            # Postgres/MongoDB transaction & entitlement models
├── services/                  # Integrations and notification wrappers
│   ├── email_service.py       # Transactional verification mailings
│   ├── notification_service.py# Custom Telegram channel alerting (SLA checks)
│   └── webhook_service.py     # HMAC signed updates to client endpoints
├── templates/                 # Consolidated console dashboard views
│   └── backoffice/
│       ├── app_users.html     # User details configuration tab
│       └── payment_queue.html # Receipt verification split-screen layout
└── utils/                     # Helpers and cryptography
    ├── encryption.py          # AES-256-CBC Vault encryption helper
    └── tenant_db.py           # Context-bound PostgreSQL connection manager
bot/
├── main.py                    # Multi-tenant Telegram Bot entry point
├── services.py                # Bot SQL database transaction delegation
└── handlers/
    ├── admin.py               # inline keyboard click verification handlers
    ├── commands.py            # /start context invoice and QR builder
    └── payment.py             # User proof image uploader and SQL logger
```

---

## 3. Developer Standards & Guidelines

To maintain code scalability and security, all developers must adhere to the following coding principles:

### 3.1 Scoped SQL Access
* **Never** use the database master `service_role` or `postgres` user in `applications.db_connection` configs.
* Always enforce a restricted database profile (e.g., `bifrost_cms_agent`) with access restricted to the `payments`, `entitlements`, and `users` tables.

### 3.2 Defensive Schema Mapping
* Downstream PostgreSQL tables can differ slightly between tenants (e.g., some use a `notes` column while others use a `reject_reason` column).
* Always query the table schema dynamically via `information_schema.columns` before performing updates to ensure SQL operations do not crash due to minor schema deviations.

### 3.3 Connection Pool Integrity
* Always wrap PostgreSQL cursor operations in the `get_tenant_db` context manager:
  ```python
  from bifrost.utils.tenant_db import get_tenant_db
  
  with get_tenant_db(db_connection_string) as conn:
      with conn.cursor() as cur:
          cur.execute("SELECT ...")
  ```
* This guarantees that the connection is cleanly returned to the pool, preventing database pool starvation.
