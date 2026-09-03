# Changelog

All notable changes to Bifrost are recorded here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions
follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html). Newest first.
`[Unreleased]` always sits directly below this note.

Section headings, in order: `Added`, `Changed`, `Deprecated`, `Removed`, `Fixed`,
`Security`.

The version banner on `/docs` is parsed from the first `## [X.Y.Z] - YYYY-MM-DD`
header in this file, so a release entered without that exact shape is a release
the product will not admit to shipping.

## [Unreleased]
### Added
- **Externally driven scheduled jobs**: Added `POST /internal/cron/reap` and `POST /internal/cron/payment-sla` (`internal/cron_routes.py`) so Cloud Scheduler can drive the reaper where an in-process thread cannot survive. Both hourly jobs share the `reap` endpoint, so the deployment costs two Cloud Scheduler jobs rather than three — the free tier is three per billing account. The routes verify the OIDC token Cloud Scheduler signs, require it to name the configured `CRON_SERVICE_ACCOUNT`, and fail closed with `503` when that is unset, so an unconfigured deployment refuses to run the jobs instead of exposing them.
- **`BIFROST_SCHEDULER` mode switch**: Selects `thread` (default, unchanged behaviour) or `external`. `external` skips `start_scheduler` so the in-process reaper cannot double-run against Cloud Scheduler, and warns at startup if the cron routes were left unconfigured.
- **Cloud Run deploy scripts**: `scripts/seed_secrets.sh` pushes the sensitive half of `.env` into Secret Manager without printing values (re-running rotates rather than errors), and `scripts/deploy_cloudrun.sh` deploys in two passes — the service URL is not knowable until the first deploy, and `BIFROST_PUBLIC_URL` must then be pinned to exactly that string — before registering both scheduler jobs.
- **Tests**: `tests/test_public_url.py` covers issuer resolution and the OTP logging regression; `tests/test_cron_auth.py` covers the cron authorization matrix, including the audience-scheme regression below.
- **Valhalla Console UI**: Completely redesigned the `content_grid.html` Backoffice template to use the modern, dark-mode premium "Stitch" design system (Valhalla Console). Added dynamic glassmorphic side drawer for entity creation/editing.
- **Security**: Added `Flask-WTF` dependency to properly enforce CSRF protection across all Jinja backoffice forms.
- **Option B Backoffice CMS Console (Phase 1)**: Built manual payment receipt validation queue, dynamic CNAME host header resolution routing, and user entitlement suspension and overrides (commit: payments proxy, user overrides, dynamic template split-screen queue with Alpine.js).
- **Telegram Bot SQL Ingestion**: Integrated the payment bot with multi-tenant SQL databases. The bot now automatically maps Telegram IDs to Postgres users, downloads and stores payment receipts in the tenant database, and routes admin approvals and rejections directly to the custom SQL connection.
- **Developer Guidelines & Verification Script**: Created project-scoped developer instructions (`.agents/AGENTS.md`) and automated initiation shell scripts (`scripts/init_dev.sh`) to support onboarding and pre-release testing.
- **Technical Architecture White Paper**: Authored a detailed technical design blueprint (`docs/WHITE_PAPER.md`) outlining system topologies, database structures, envelope encryption models, and multi-tenant routing parameters.
- **Norse Mythology Mock Client App (Valhalla Portal)**: Built a Norse-themed mock client application (`valhalla_portal/app.py` and templates) running on port `5050` to demonstrate and test secrets injection and centralized SSO authentication redirection callbacks over the Bifrost bridge.
- **Unified Python Client SDK**: Created the canonical `bifrost_client.py` client SDK under `sdk/python/` to allow downstream Python applications to pull secrets, inject environment variables, and manage local config caching dynamically. Enhanced it to be fully object-oriented, documented, and parameterized (supporting custom Client IDs, cache paths, TTL parameters, and process-level injection flags).
- **Auth UI**: Added interactive password show/hide toggle (eye icon) to all client-facing and backoffice login, password reset, and account activation templates.
- **SSO Multi-Provider Integration**: Implemented generic OAuth2 and OpenID Connect (OIDC) SSO routers (`/auth/sso/<provider>/login` & `/callback`) for **Google**, **GitHub**, **Microsoft / Outlook**, **Apple**, and **Facebook**. Integrates nested linked identity schemas (`identities` map) in MongoDB, automatic app redirection, dynamic template rendering based on server configuration, and new user provisioning.
- **Multi-Tenant Telegram Webhooks**: Extended the internal bot builder (`bot/main.py`) to accept dynamic bot tokens. Refactored the webhook receiver `/auth/api/telegram-webhook/<client_id>` to pull unique branded bot tokens dynamically from the respective client application's database vault, allowing a single deployment to host multiple custom branded payment bots. Configured Savvify with the master Telegram Payment Bot token for dynamic payment webhook execution.
- **Phone OTP SMS Authentication**: Built a dedicated SMS dispatch service (`services/sms_service.py`) leveraging the Twilio HTTP API (with local stdout sandbox fallback). Implemented headless REST API endpoints (`/request-phone-otp` & `/verify-phone-otp`) and Flask UI routes (`/request-phone-otp` & `/verify-phone-otp`) with automatic user provisioning and a togglable Phone tab on the login screen.
- **Heimdall Vision OCR Metrics**: The Heimdall AI Metrics dashboard now queries and displays usage and cost metrics for Google Cloud Vision API (`vision.googleapis.com`) under the model name `vision-ocr-pages`.
- **Heimdall Billing Breakdown Table**: Enhanced the dashboard interface (`ai_metrics.html`) to display a detailed tabular breakdown of individual GCP service costs, applied credits, and net costs.
- **Bifrost Console Branding**: Refined backoffice titles and templates to standardize management dashboard naming to "Bifrost Console" for tenant and admin screens.
- **Bifrost Integration Sandbox**: Built an interactive mock client app (`sandbox/app.py`) using Flask to demonstrate integration flows. Showcases session authorization redirection, token callback parsing, local JWT claims decoding, multi-provider SSO/SMS OTP routing, and live SDK vault key loading diagnostics.
- **Client Application RBAC Claims**: Introduced central JWT session token helpers (`utils/token.py`) that map and resolve client-specific user roles (`owner`, `super_admin`, `admin`, `premium_user`, `user`, `guest`) to fine-grained permission arrays (e.g. `premium:access`, `billing:manage`, `read:app`). Embeds resolved roles and permissions directly inside downstream client session tokens.
- **Professional Role-Based Access Control (RBAC)**: Upgraded the numeric permission level checks into a professional explicit role-permissions matrix (`ROLE_PERMISSIONS`). Maps roles (`owner`, `super_admin`, `admin`, `member`, `user`, `viewer`) to granular permissions (such as `read:config`, `write:config`, `manage:users`, `view:secrets`, `manage:secrets`, `transfer:ownership`, `view:metrics`) with backwards-compatible legacy level fallbacks.
- **Service Segmentation & Toggles**: Introduced a comprehensive service toggle configuration system (`enabled_services`) for registered applications. Supports toggling of OAuth SSO, Phone SMS OTP, Email OTP, Secrets Vault, Payment Verification Bot, and Heimdall AI Monitor individually. Dynamically updates UI elements and enforces service-level permission checks across all API and authentication endpoints.

- Created `upload_sa_to_bifrost.py` script to seamlessly push Google Service Account credentials directly into the Bifrost MongoDB Vault.
- Added `/heimdall/ai-metrics` routing in `backoffice.py` to query Google Cloud Monitoring.
- Implemented `ai_metrics.html` visual dashboard for Heimdall users to track `aiplatform.googleapis.com` token metrics in real-time.
- Updated `dashboard.html` to integrate AI Metrics button.
- Added `google-cloud-monitoring` dependency to `requirements.txt`.

### Changed
- **Public origin resolution centralized**: `utils/urls.py::public_url()` is now the single answer to "what origin are we reachable at". `auth/oidc.py::issuer()`, `services/email_service.py` (logo, invite and reset links) and `services/payway.py` all route through it instead of reading `BIFROST_PUBLIC_URL` — or a proxy header — for themselves.
- **`BIFROST_PUBLIC_URL` is no longer defaulted**: see Security. Startup now warns when it is unset, and names `BIFROST_API_URL` when *that* is set instead, because confusing the bot's variable for Bifrost's is the specific mistake that shipped the localhost issuer.
- **`google-auth` pinned explicitly** in `requirements.txt`. It already arrived transitively via `google-cloud-*`, but it now backs a security boundary and should not rest on another package's dependency graph.
- **Dead Koyeb hosts removed**: `scripts/bootstrap_prolong.py` wrote three `koyeb.app` literals into the config file Prolong pins; it now reads `BIFROST_PUBLIC_URL` and refuses to run when unset rather than writing a plausible wrong answer. The bot's `BIFROST_API_URL` in `docker-compose.yml` pointed at a remote Koyeb app while `depends_on` already wired up the local service; local dev now talks to the local one, still overridable.

### Fixed
- **Scheduled jobs rejected for a wrong audience**: the cron routes derived the expected OIDC audience from `request.base_url`, which reports `http://` because Cloud Run terminates TLS at the proxy and forwards plain HTTP — while the token Cloud Scheduler signs carries an `https://` audience. Every scheduled call `401`d, which is the failure these endpoints exist to prevent (the reaper silently not running) arriving by another route. The audience is now built from `public_url()`.

### Security
- **OTP codes are no longer written to the log**: `create_otp` logged the code itself, so anyone with log access could spend a live OTP before it expired. It now logs the verification id, which is what actually correlates a send with its verification.
- **The OIDC issuer can no longer be moved by a request header**: `BIFROST_PUBLIC_URL` defaulted to `http://localhost:5000`, and that literal beat the forwarded-header fallback — so a deployment that never set the variable served a discovery document pointing every relying party at the operator's own machine, with a `200` on it. The default is gone: config wins, forwarded headers are the fallback, and outside a request context callers get `''` and omit the link rather than emit a broken one.

## [0.17.0] - 2026-07-29

Bifrost becomes a real OpenID Connect provider with working single sign-on, and
platform staff stop being implicitly omnipotent over customer tenants.

### Added
- **OIDC provider, completed.** Client authentication at the token endpoint
  (`client_secret_basic` / `client_secret_post`), exact-match `redirect_uri`
  allowlisting at both authorize and token exchange, PKCE (`S256` and `plain`,
  mandatory for clients flagged `oidc_public_client`), refresh tokens with
  rotation and no scope widening, RFC 7009 revocation, RP-initiated logout with
  an allowlisted `post_logout_redirect_uri`, and full discovery metadata.
- **Single sign-on.** `bifrost/auth/sso.py` holds a real IdP session. Every
  authentication path — password, email OTP, SMS, social, Telegram, invite
  activation — funnels through one `complete_login()`, so a second application
  redirecting to `/oidc/authorize` receives a code without re-prompting.
  Supports `prompt=none`, `prompt=login`, `max_age`, and emits `amr` /
  `auth_time`.
- **Account directories.** `applications.tenant_id` groups applications that
  share one user pool, defaulting to the application's own `client_id` so a
  standalone tenant is unchanged. This is what lets SSO span more than one app.
- **Internal vs external tenants.** `applications.tenant_type` decides how much
  of a tenant platform staff can see. Internal is unrestricted; external limits
  platform admins to `read:config`, `view:metrics` and `audit:view`.
- **MongoDB backend for the tenant CMS** (`bifrost/models/cms_mongo.py`).
  Selected by connection string, mirrors the seven Postgres CMS operations,
  infers a schema by sampling documents, and coerces form values to the types a
  collection already uses. The payment queue stays PostgreSQL-only.
- Migration scripts `003`–`005` for account directories, tenant classification
  and the legacy index drop.

### Fixed
- **The token endpoint had never worked.** It called `db.get_account()`, which
  does not exist, so every authorization-code exchange raised `AttributeError`.
- **Global unique indexes defeated multi-tenancy at the database layer.**
  `accounts` carried both legacy platform-wide unique indexes (`email_1`,
  `username_1`, `telegram_id_1`, `google_id_1`, `phone_number_1`) and the
  per-tenant compound ones. The legacy indexes are strictly stronger, so two
  tenants could never share a user's email — surfacing as a 500 on registration.
  Dropped by `scripts/005_drop_legacy_global_indexes.py`.
- **Directory scoping broke every caller outside `bifrost/auth/`.** The Telegram
  bot, payment webhooks, internal API and backoffice user search all looked up
  accounts without a scope and silently matched nothing. An unscoped lookup now
  raises instead of quietly returning `None`; platform paths pass `ANY_TENANT`
  explicitly.
- **Console invites created accounts with no directory**, so an invited user
  could never sign in to the tenant application.
- **Platform-locked tables were cosmetic.** The lock list filtered the table
  listing and was never consulted on save, so a hand-made POST wrote to a locked
  ledger. Now enforced in `check_cms_write_permission`, ahead of every role.
- **Role-hidden columns were cosmetic on write** for the same reason. Stripped
  from the payload in `save_cms_row` and `create_cms_row`.
- **The OIDC signing key was generated per worker process**, so one worker signed
  id_tokens another worker's JWKS could not verify. Persisted in MongoDB and
  shared, with `OIDC_PRIVATE_KEY_PEM` to pin it explicitly.
- **The issuer was taken from `X-Forwarded-Host`**, which is attacker-controlled.
  Read from `BIFROST_PUBLIC_URL`, with the header only as a fallback.
- **Signing up mid-OIDC-flow dropped the relying party** — the branch checked a
  session key nothing ever set.
- `expires_in` advertised 3600 seconds while issuing a seven-day token.

### Changed
- Platform staff (`heimdall`, `pr0meth4us`) no longer short-circuit
  `check_permission` to `True`. Access is resolved against the tenant's type.
- Heimdall's cross-tenant views (`/heimdall/users`, `/heimdall/api-keys`, global
  user delete) are scoped to internal directories. `global_user_details` returns
  an identical 404 for absent and external accounts, so it cannot enumerate a
  customer's users.
- Deleting an account now revokes its refresh tokens and authorization codes.
- Uniqueness checks in `link_sso`, `link_telegram`, `update_account_profile` and
  `link_email_credentials` take their scope from the account itself rather than
  applying globally.

### Removed
- `Config.PLATFORM_LOCKED_TABLES`. Locks live on the application document and
  are editable in the console. The hardcoded keys (`finance-bot`, `savvify`)
  matched no application in the database and had never taken effect.

## [0.16.0] - 2026-07-28
Admin Console ("Control Room") Phase 1 + 1.5, against the Ministry Exam Prep scope of
work. Gap analysis and vendor reply in `docs/scope-response-admin-console.md`;
operator runbook in `docs/console-onboarding.md`.

### Fixed
- **App Configuration form unlocked for authorized roles**: Removed artificial Alpine.js `:disabled` blocking and hidden submit button in `bifrost/templates/backoffice/app_users.html`. Owners, Admins, Super Admins, and Heimdall can now directly edit configuration fields and click "Save Changes".
- **Docs formatting and button URL fixes**: Replaced raw Markdown bold tags with `<strong>` HTML tags, increased base typography size (15.2px, line-height 1.7), added high-contrast glassmorphism panels, and fixed Changelog & Console Login link routes in `bifrost/templates/docs.html`.
- **Koyeb build failure due to submodule gitlink**: Removed `prolong` submodule gitlink and `.gitmodules` so Koyeb and automated CI/CD runners build the `bifrost` Docker container cleanly without git credential errors.
- **CSRF tokens were missing from every backoffice form.** `CSRFProtect` had been enabled without them, so approve, reject, refund and all CMS writes were returning 400. Tokens added to all 10 templates; `/api/tenant/<app_id>/payments/notify-new` explicitly exempted (machine-to-machine, secret-authenticated) and its secret comparison made constant-time.
- **Refund could never succeed** — the route required a `track_id` the form never sent. The track is now derived from the payment itself, never from a form field.
- **The approval webhook silently never fired** — a tenant Postgres `user_id` was passed where a Bifrost `ObjectId` was expected and the `InvalidId` was swallowed. The payer is now resolved from their email, and a failure to resolve is surfaced instead of reported as success.
- **Exam tracks were hard-coded in the template** and approval fell back to track `1`. Options now come from `exam_tracks WHERE is_active`; approval refuses without an explicit track.
- **The SLA age badge was a literal string** (`Pending < 2h` on every row). Real age from `created_at`, with ok/warn/breach states.
- **Any role with `read:config` could read the payment queue**, including payer emails and receipts. The queue now requires `payments:view`.
- **Role-hidden columns were serialised into the page** by the drawer's `row | tojson`. Hidden columns are stripped server-side before render.
- **Suspending a user destroyed their purchase** — all entitlements were revoked while reinstate only restored `users.status`. Suspension no longer touches entitlements.
- `assert`-based SQL identifier guards replaced with `safe_ident()` raising `ValueError` (asserts vanish under `python -O`).

### Added
- **Raw JSON Editor Modal (`{ } Raw JSON Editor`)**: Added a syntax-validated, formatted Raw JSON editor modal to both Schema Config (`cms_config.html`) and Onboarding Smart Detection (`cms_onboarding.html`). Enables owners/admins to view, edit, prettify, copy, and paste raw `cms_config` JSON configurations directly with instant live validation.
- **Ultra-Clean Top-Level Navigation URLs**: Completely removed `/app/<app_id>` and `/app/<slug>` from standard console navigation. All main backoffice tabs now operate on ultra-clean top-level URLs: `/backoffice` (Dashboard), `/backoffice/users` (App Settings & Staff), `/backoffice/cms` (Content Editor Grid), `/backoffice/onboarding` (3-Step Setup Wizard), and `/backoffice/payments` (Payment Verification Queue).
- **Database Hosting Architecture Selector (Managed Bifrost DB vs Custom External DB)**: Added `db_mode` selection (`managed` vs `custom`) in App Configuration (`app_users.html`) and backend database proxy routing (`get_tenant_db_conn_str`). Allows tenants to choose between auto-hosted Managed Bifrost Database (zero setup) or connecting their own PostgreSQL / Supabase connection string (BYODB).
- **Zero-Tables Starter Schema Bootstrapper**: Added 1-click database schema creation panel to Step 2 of the Onboarding Wizard (`cms_onboarding.html` and `POST /backoffice/app/<app_id>/cms/bootstrap-schema`) when a connected database returns **0 tables**. Supports applying *Ministry Exam Prep Schema* (`questions`, `choices`, `tracks`, `users`, `payments`, `entitlements`) or *E-Commerce Schema* (`customers`, `products`, `orders`, `payments`, `access_grants`) directly to the PostgreSQL database.
- **Prolong Application Bootstrap Script (`scripts/bootstrap_prolong.py`)**: Created automated script to register Ministry Exam Prep (`ministry_exam_prep`) in Bifrost Control Plane MongoDB, configure default `cms_config.payment_queue` schemas, and generate `bifrost_bootstrap.json` credentials for client adoption.
- **Unified Claude Artifact-styled Documentation Portal (`/docs`)**: Redesigned `bifrost/templates/docs.html` into a single, sectionized, external-facing documentation hub styled with Claude Artifact aesthetics (dark mode, glassmorphism panels, `Plus Jakarta Sans` typography, and JetBrains Mono code blocks). Covers Part I: Getting Started & SDK, Part II: Secrets Vault & Services API, Part III: Webhook HMAC Signatures, Part IV: 3-Step CMS Onboarding Wizard, and Part V: Console Operator's Manual & Money Path rules.
- **Payment state machine** enforced server-side: `FREE → PENDING → PREMIUM | REJECTED`, `PREMIUM → REFUNDED`, with `SELECT … FOR UPDATE`, mandatory reason codes, and actor/timestamp/reason on every transition.
- **Duplicate `txn_ref` rejection** inside the approving transaction, surfacing the earlier payment; duplicate-receipt detection by checksum (or URL where the column is absent).
- **MFA on all console accounts** — emailed 6-digit second factor, no session issued until it verifies. Plus admin session policy (30 min idle / 8 h max, HttpOnly/SameSite/Secure) and Redis-backed login rate limiting.
- **Audit log for every mutation** — content, payments, users, entitlements — with before/after JSON, and a filterable timeline UI at `/backoffice/app/<app_id>/audit`. No TTL: retention is a minimum of one year.
- **Publish workflow**: only `content:publish` holders may publish, and publishing is blocked unless the question has exactly 4 choices, exactly 1 correct, bilingual explanations on the correct choice, and a non-empty `source_ref`.
- **Console roles** `content_manager` and `operations` alongside admin/owner, enforced by a single `@requires(permission)` decorator that returns 403 on mutations rather than redirecting.
- **SLA sweep** every 15 minutes alerting on payments approaching or past the 6h threshold, once per payment per state.
- **Pluggable notification channels** — telegram | email | webhook, selected by tenant configuration rather than code.
- **Khmer typography** (`Kantumruy Pro`, `Noto Sans Khmer`, line-height 1.8) applied to grid cells, drawer fields and form controls.
- **Connection pooling** for tenant Postgres (`ThreadedConnectionPool` per connection string), replacing a fresh connect per query.
- **Manual entitlement override** endpoint for support cases, audit-logged.
- `migrations/001_console_phase1.sql` — proposed schema additions (`payments.exam_track_id`, unique `txn_ref`, `receipt_checksum`, `created_at`, entitlement uniqueness, `users.status`, `questions.question_source`) plus the restricted `console_agent` role grants.
- `tests/test_console_phase1.py` — 27 tests covering approve→entitlement, refund→revocation, duplicate `txn_ref`, the state machine, publish validation, identifier guards, and server-side role enforcement.

## [0.15.0] - 2026-07-24
### Added
- `sdk/python/bifrost_ai.py` — Google AI client factories (`get_genai_client` for Vertex Gemini, `get_vision_client` for Cloud Vision) built on bifrost's own credential resolution. Bifrost now owns "talk to Google AI + handle credentials" for all consumers; downstream repos import these instead of hand-rolling `genai.Client(vertexai=True, …)`. google libs are imported lazily so `bifrost_client` (secrets-only) stays dependency-light.

## [0.14.0] - 2026-07-09
### Added
- Multi-Tenant UI Configuration: App Owners can now input and update their `db_connection` (PostgreSQL/Supabase) string directly from the Configuration tab in the Bifrost Backoffice.
- Inference Engine: Automatically detects and renders monetary columns, avatar columns, and status pills (active/pending) in the Content Editor grid.
- Platform Lock Badges: Locked tables (like 'transactions' or 'payments') now display a "Protected by Bifrost data policy" badge to indicate they are read-only for tenant operators.

### Security
- UI input elements are correctly disabled when the user lacks the required `write` permissions for specific tables or columns, reinforcing the Layer B RBAC on the frontend.

All notable changes to the `bifrost` project will be documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.13.0] - 2026-06-15
### Added
- **Config Webhooks (Secret Zero)**: Bifrost now emits a `config_updated` webhook event to an application's `app_api_url` whenever its API keys, details, or Client Secret are rotated. This allows client applications to seamlessly refresh their cached secrets without latency overhead or restarts.
- **Client SecretManager**: Added a reusable `bifrost_secret_manager.py` Python snippet to easily integrate the webhook auto-refresh mechanism into downstream client applications.
- **Vault UI Sync Status**: Updated the Global API Vault frontend to display the Webhook Sync status for applications, allowing Heimdall administrators to see at a glance if an application is hooked up to real-time configuration updates.

## [0.12.2] - 2026-06-15
### Added
- **Global API Vault**: Added a centralized, read-only dashboard (`/heimdall/api-keys`) for Heimdall super-admins to monitor configured API keys across all registered applications.
- **API Vault Navigation**: Integrated the "API VAULT" quick-link into the main Backoffice Dashboard and the Global Users List navigation bars.

### Fixed
- **API Keys Manager UI**: Moved the API Keys Manager section outside of the strict Owner block in `app_users.html` to ensure Super Admins can configure keys properly.
- **Key Migration**: Automated the parsing of local `.env` keys (such as `EXCHANGERATE_API_KEY`, `TELEGRAM_TOKEN`, `MONGODB_URI`, `PAYWAY_API_KEY`) and securely encrypted them directly into the Bifrost database. Added placeholders for `GEMINI_API_KEY` for the TikTok streak features.

## [0.12.1] - 2026-06-15
### Fixed
- **Config API Import**: Fixed an `ImportError: attempted relative import beyond top-level package` in `config_api.py` caused by an incorrect `get_db` relative import, which prevented Gunicorn workers from booting.

## [0.12.0] - 2026-06-15
### Added
- **Remote Config Server**: Upgraded Bifrost to act as an HTTP-based centralized Remote Config Server utilizing End-to-End Encryption at Rest.
- **Server-Side Decryption**: Added `GET /api/v1/config` endpoint. Bifrost decrypts stored API keys and Configs using the specific app's `webhook_secret` and returns them securely over HTTPS to authenticated clients.
- **API Key Management UI**: Added dynamic API key management panel in the Backoffice configuration tab.

## [0.11.0] - 2026-06-14
- **Heimdall Monitor**: Implemented a cinematic, live-scrolling terminal UI for Heimdall users to monitor all Helm ecosystem apps centrally.
- **Centralized Logging API**: Added `POST /internal/logs` endpoint (protected by `require_service_auth`) for tenant apps to securely stream their logs into Bifrost.
- **Redis Integration**: Replaced MongoDB with an ultra-fast, in-memory Redis database specifically for streaming logs. Included auto-trimming (1000 items max) and a 7-day TTL to optimize storage usage.

### Fixed
- **Logging API Bug**: Resolved a `NameError` causing HTTP 500 responses in `/internal/logs` by importing the missing `datetime` module.

## [0.10.0] - 2026-06-14
### Changed
- **Architectural Reversion**: Removed the Bifrost Control Room UI and the `pr0meth4us` role. 
- **Decoupling**: Removed all dynamic TikTok configuration saving logic, as the configuration has been moved back into a static file inside the Finance service.

## [0.9.0] - 2026-06-14
### Added
- **Centralized Control Room**: Added a new protected view in `backoffice.py` styled with Tailwind CSS, offering a terminal viewer for ecosystem logs and an interactive panel to manage the AI Keeper.
- **pr0meth4us Role**: Added support for the `pr0meth4us` (Bot Master) role. `pr0meth4us` has Level 3 (Owner) privileges across all connected applications and exclusive access to the Control Room, but cannot register new apps or permanently delete users like `heimdall`.
- **Dynamic Configuration Updates**: Implemented forms in the Control Room to push settings (`FORCE_DICE_ROLL`, `BEHAVIORAL_CONFIG`) directly to MongoDB, eliminating the need for hardcoded scripts.

## [0.8.9] - 2026-06-10
### Added
- **Secret Management**: Added "Rotate Secret" action to the `ApplicationsView` in `admin_panel.py`. Administrators can now securely invalidate and regenerate Client Secrets directly from the Bifrost Admin Dashboard.

### Security
- **Production Safety**: Removed hardcoded `debug=True` in `run.py`, ensuring interactive debuggers are disabled in production.

## [0.8.8] - 2026-06-10
### Added
- **Free Trial API**: Implemented a new internal endpoint (`POST /internal/payments/free-trial/activate`) to seamlessly upgrade users to the `premium_user` role for a 14-day duration without requiring a Stripe or ABA payment intent.
- **Trial Fraud Prevention**: Added a `trial_used` boolean flag to the `app_links` collection in MongoDB. The free trial endpoint validates this flag before granting access, effectively preventing users from claiming multiple trials across different applications within the Bifrost ecosystem.
- **Bulk Role API**: Built a new internal endpoint (`POST /internal/get-roles-bulk`) that accepts an array of `account_ids` and securely returns their real-time application-specific roles directly from the identity database. This enables client applications to synchronize user privileges instantly without maintaining complex caching logic or violating microservice isolation principles.
- **Role Assignment Controls**: Upgraded the internal data structures to support manual role overrides by system administrators via the Client Apps, bypassing automated billing flows when necessary.

## [0.8.7] - 2026-06-09
### Added
- **Bank API Configuration (System-Level)**: Integrated the `ABA_RECURRING_API_TOKEN` environment variable in the Bifrost configuration (`config.py`) as a system-level placeholder/sandbox key to support future recurring payments, keeping it securely out of the user-facing backoffice settings.

## [0.8.6] - 2026-06-09
### Fixed
- **App Link Role Preservation on Login**: Resolved premium user demotions by ensuring `link_user_to_app` preserves the existing `app_specific_role` and `role` when not explicitly specified (such as during login calls).
- **Expiration Sync in Reaper**: Updated the scheduler's expiration logic to verify both `app_specific_role` and legacy `role` fields, ensuring a robust downgrade sync across both fields.

### Added
- **Subscription Expiration Warnings**: Implemented `run_expiration_warning_check` in the background scheduler to run hourly. It queries active premium subscriptions expiring within 3 days that have not been warned yet, flags them, and fires the `subscription_warning` webhook.
- **Legacy Role Compatibility**: Added automatic synchronization between legacy `role` and strict `app_specific_role` across models.
- **Warning Flag Clearance**: Clears the `warning_sent` subscription expiration warning flag on database role upgrades, renewals, and downgrades to prevent stale states.

## [0.8.5] - 2026-06-08
### Added
- **Explicit Expiration Output**: Modified the `/internal/validate-token` endpoint in `bifrost/internal/routes.py` to output the `exp` timestamp parsed directly from the JWT payload. This empowers relying services (like the Finance Core) to accurately cap their cache TTL without violating domain isolation by decoding the token themselves.

## [0.8.4] - 2026-06-08
### Fixed
- **Validation Debugging**: Improved `validate_token` endpoint to return specific JWT exceptions (e.g., ExpiredSignatureError, DecodeError) instead of a generic "Invalid Token" 401 error. This will assist client applications in diagnosing token rejections after webhook events.

## [0.8.3] - 2026-06-08
### Fixed
- **Bifrost API**: Fixed a 404 error affecting `/internal/payments/secure-intent` and other payment endpoints by correcting the blueprint import in `bifrost/__init__.py`. The import was changed from `from .internal.routes import internal_bp` to `from .internal import internal_bp`, ensuring that both general routes and payment routes are properly registered with the Flask application.
- **Webhook Processing**: Fixed an issue in `bot/main.py` where the bot would quietly drop messages during webhook execution. Added missing `await app.start()` and `await app.stop()` calls to ensure the `python-telegram-bot` application correctly initializes its internal task queues before processing updates.

## [0.8.2] - 2026-03-25
### Fixed
- **Webhook Reliability**: Fixed a bug where the `subscription_success` webhook was not being dispatched to client applications when a pending transaction was completed via Bot admin approval, PayWay callback, or Gumroad callback. 
- The `_trigger_event_for_user` call is now safely executed directly within `bifrost.models.payments.PaymentMixin.complete_transaction`, guaranteeing that role updates consistently flush local application caches.

## [0.8.1] - 2026-03-25
### Fixed
- **Bifrost API**: Fixed `TypeError` in `/internal/payments/secure-intent` route caused by an incorrect keyword argument. Changed `ref_id` to `client_ref_id` when calling `db.create_transaction()` in `bifrost/internal/payment_routes.py` to match the `PaymentMixin` method signature.

## [0.8.0] - 2026-01-30
### Fixed
- **Role Corruption Recovery**: Modified `get_user_role_for_app` in `bifrost/models/apps.py` to handle cases where the database record incorrectly stores the role in `app_specific_role` instead of `role`.
  - This fixes the "Premium users reported as Guest" bug caused by manual database edits or legacy scripts using the wrong field name.
  - Added warning logs when this corruption is detected.

## [0.7.9] - 2026-01-30
### Fixed
- **Role Sync Debugging**: Added verbose logging to `get_user_role_for_app` in `models/apps.py` and internal API routes `get-role` and `validate-token`.
  - Now logs resolving of Telegram ID to User ID.
  - Logs the raw `app_link` document found (if any).
  - Logs the specific expiration timestamp comparison (UTC vs UTC).
  - This is to diagnose why Premium users are being reported as Guest/User.

## [0.7.8] - 2026-01-30
### Added
- **App Super Admin Role**: Introduced an intermediate role (`super_admin`) between App Admin and Owner.
  - Can manage Users AND App Admins.
  - Can manage App Configuration (Name, URLs, Bot Token).
  - Cannot access Secrets.

### Changed
- **Permission Hierarchy**: Enforced strict vertical permission logic in Backoffice.
  - **Heimdall/Owner**: Full Access (Level 3/4).
  - **Super Admin**: Config + User Management (Level 2).
  - **App Admin**: User Management Only (Level 1).
- **UI Logic**: Updated `app_users.html` to conditionally hide the "Secrets" pane and disable Config forms based on the logged-in user's role rank.

## [0.7.7] - 2026-01-30
### Added
- **Password Recovery**: Implemented a full Forgot Password flow for the Backoffice.
  - `/backoffice/forgot-password`: Email entry form.
  - `/backoffice/reset-password`: OTP verification and new password setting.
- `send_reset_email` service to deliver OTPs via SMTPLIB.
- **Support**: Works for both **Heimdall** (Admins) and **App Owners** (Accounts) automatically.

## [0.7.6] - 2026-01-30
### Security
- **Strict Role Enforcement**: Updated Backoffice login to strictly require `role: "heimdall"` for global access.
  - Users with the legacy `super_admin` role will now be blocked and prompted to update.
- **Access Control**: Hardened the check for Tenant Dashboard access.
  - Users with no managed apps (zero ownership/admin links) will now be explicitly denied access with a clear error message.

## [0.7.5] - 2026-01-30
### Added
- **Heimdall Vision**: Added a direct shortcut in the Backoffice Dashboard for God Admins to access the Global User Database.
- **App Owner Display**: Added a read-only field in the App Configuration tab that displays the current App Owner's name and email, with a prompt to change it via the Users tab.

### Changed
- **Rebranding**: Renamed "Super Admin" role to **Heimdall** throughout the codebase (Database `admins` collection now uses `role: "heimdall"`).
- **Admin Script**: Updated `create_super_admin.py` to assign the `heimdall` role by default.
- **Permissions**: Updated `backoffice.py` decorators to check for `is_heimdall` session flags.

## [0.7.4] - 2026-01-30
### Fixed
- **Bot Token UI**: Restored the ability to view and edit the `telegram_bot_token` in the App Configuration settings within the Backoffice.
- **Data Model**: Updated `update_app_details` in `bifrost/models/apps.py` to allow `telegram_bot_token` updates.

## [0.7.3] - 2026-01-29
### Added
- **Owner Role**: New top-level role for App Creators.
  - Apps can now have only **one** Owner.
  - Transferring ownership automatically demotes the previous owner to Admin.
- **UI Enhancements**:
  - Added "Show/Hide Password" (Eye Icon) to Backoffice Login.
  - Added "Show/Hide Secrets" (Eye Icon) to App Configuration.

### Changed
- **Permissions**: App Creators are now assigned `owner` role instead of `admin` upon creation.
- **Backoffice**: Updated User Management modals to include `owner` in role selection.
- **Admin Panel**: Added `Owner` to the Flask-Admin role dropdowns.

## [0.7.2] - 2026-01-29
### Security
- **Dynamic CORS Middleware**: Implemented a custom middleware that validates the `Origin` header against the database in real-time.
- **Zero-Downtime Updates**: New client applications are automatically whitelisted within 60 seconds of registration without requiring a server restart.
- **Smart Caching**: Implemented a TTL-based cache for allowed origins to maintain high performance while ensuring security.

## [0.7.1] - 2026-01-29
### Security
- **CORS Hardening**: Replaced the insecure wildcard CORS configuration (`*`) with a strict dynamic whitelist.
- **Dynamic Origin Loading**: The application now queries the database at startup to fetch `app_web_url` and `app_callback_url` for all registered clients, parsing them to allow only authorized origins.
- **Dev Fallback**: `localhost` ports are automatically added to the whitelist when the app is running in debug mode.

## [0.7.0] 2026-01-29
### Added
- "Get Started" section in bifrost_docs.html outlining the 4-step integration process.
- Sidebar link for the "Get Started" section for improved navigation.

## [0.6.0] - 2026-01-29
### Added
- **Payment Status Polling**: Added `GET /internal/payments/status/<transaction_id>`.
- Client frontends can now poll this endpoint (via their backend) to confirm payment success in real-time without relying solely on webhooks.
- **Custom App QR Codes**: Added `app_qr_url` to Application model and Bot logic.
- **Security Check**: Added explicit blocklist (`FORBIDDEN_ROLES`) to payment routes to prevent unauthorized promotion to Admin/Super Admin via the payment API.

## [0.5.0] - 2026-01-29
### Added
- **Custom App QR Codes**: Added `app_qr_url` to the Application model.
- Client Apps can now upload/set their own custom Payment QR code via the Backoffice configuration tab.
- The Bifrost Bot (`/pay` command) now dynamically loads this custom QR instead of the default system image if it exists.
- **Enhanced Role Permissions**:
  - Implemented `check_admin_permission` in `bot/services.py`.
- **Client App Admin Approval**: The Telegram Bot now allows users with the `admin` role for a specific app to approve/reject payments for *that app*, even if they are not in the main Payment Group.
- Updated `_verify_admin` in `bot/handlers/admin.py` to support this dual-verification strategy (Global Admin Group OR Client App Admin).

### Changed
- **Bot Logic**: The `/pay` command now prioritizes the App's custom QR URL over the local `assets/qr.jpg`.
- **Payment Approval**: The `admin_approve` handler now dynamically checks the clicker's role against the target application of the transaction.

## [0.4.3] - 2026-01-29
### Documentation
- **Integration Guide**: Major overhaul of `bifrost/templates/docs.html`.
- Added comprehensive "Registration Flow" section covering OTP generation and verification.
  - Added "Account Linking" section detailing the `generate-link-token` flow.
- Added "Payment Proofs" section for the `submit-proof` API.
  - Added concrete Python code examples for HMAC Webhook verification.
- Added specific details on User Roles (`guest`, `user`, `premium_user`, `admin`).
- **Structure**: Organized docs with a sticky sidebar for easier navigation.

## [0.4.2] - 2026-01-29
### Added
- **Guest Role**: Explicitly added `guest` as a selectable role in the Backoffice "Add User" and "Manage User" forms.
- **Documentation**: Added `docs/` folder with `README.md` and `API_REFERENCE.md` detailing compliance rules.
- **Testing**: Added `tests.http` for internal API testing.

### Security & Compliance
- **Immutable Verified Users**: Updated `remove_user_from_app` in `bifrost/models/apps.py`.
- Administrators can no longer remove users whose role is anything other than `guest`.
- This ensures verified users own their data and cannot be forcibly unlinked by a tenant admin.
- **Backoffice UI**: Added logic to `bifrost/backoffice.py` to catch compliance errors and flash a descriptive warning ("Verified users cannot be removed...").
- Updated the UI button to label removal as "(Guest Only)".

## [0.4.1] - 2026-01-26
### Fixed
- **OTP Race Condition**: Updated `create_otp` in `bifrost/models/auth.py` to delete any existing codes for the same identifier/channel before creating a new one.
- This resolves issues where users try to use an "old" code after requesting a new one.
- **OTP Validation**: Added stricter whitespace cleaning to `verify_otp` to handle copy-paste errors better.
- **UI UX**: Added double-submit protection (JavaScript disable button) to `verify_otp.html` to prevent the "Invalid/Expired" error that occurs when a user double-clicks the verify button.

### Changed
- **Auth UI**: Overhauled `forgot_password.html`, `verify_otp.html`, and `reset_password.html` to use the modern "Glassmorphism" design system (Tailwind CSS) consistent with the Login page.

## [0.4.0] - 2026-01-23
### Added
- **Web Payment Proofs**: Added `POST /internal/payments/submit-proof` allowing client applications to upload payment screenshots directly via API.
- **Admin Forwarding**: Implemented `send_payment_proof_to_admin` in `bifrost/utils/telegram.py` to bridge the gap between the Web API and the Telegram Admin Group.
- **Bot Logic Update**: Updated `call_grant_premium` in `bot/services.py` to support `ObjectId` (Bifrost Account IDs) for manual approvals, enabling the bot to verify users who are not on Telegram.

### Changed
- **Admin Handler**: Refactored `admin_approve` in the Bot to gracefully skip sending Telegram DMs if the user identifier is not a valid Telegram ID (Web upload flow).

## [0.3.3] - 2026-01-23
### Changed
- **Webhooks**: The `subscription_success` webhook event now includes `duration` (e.g., '1m') and `expires_at` (ISO timestamp) in the `extra_data` payload.
- **Internal Logic**: Updated `complete_transaction` in `PaymentMixin` to calculate the expiration date immediately for the webhook payload, ensuring client apps receive the exact validity period of the new subscription.

## [0.3.2] - 2026-01-23
### Added
- **Global User Database**: Implemented a "God View" for Super Admins (`/backoffice/users`) to search and manage all accounts across the entire ecosystem.
- **Global Deletion**: Added functionality to permanently delete a user account (`accounts` collection) and all their associated app links.
- **UI Interaction**: Added Alpine.js to handle dynamic Modals, Tabs, and Secret masking without page reloads.

### Changed
- **Security Hardening**:
  - **Masked Credentials**: Client IDs and Webhook Secrets are now hidden by default (`•••••`) and require a click to reveal.
- **Read-Only Config**: Application settings (URLs, Name) are locked by default to prevent accidental edits.
- **UX Overhaul**:
  - **App Management**: Split "Users" and "Configuration" into separate tabs.
- **User Actions**: Replaced inline table forms with a single "Manage" button that opens a detailed Modal.
- **Logic Fixes**:
  - **Default Duration**: The "Add User" and "Manual Bot Approval" flows now default to **1 Month** access instead of **Lifetime** if no duration is specified.
- **User Feedback**: Clarified success messages to distinguish between "Inviting a new user" and "Linking an existing global user".

## [0.3.1] - 2026-01-23
### Added
- **User Removal**: Added `remove_user_from_app` method to `BifrostDB` and corresponding UI in the Backoffice.
- **Admin Control**: App Admins and Super Admins can now permanently unlink a user from an application via the Backoffice "Actions" column (Red "X" button).

## [0.3.0] - 2026-01-23
### Added
- **API**: Added `update_app_details` method to `BifrostDB` and a corresponding `POST /backoffice/app/<id>/update` route.

### Changed
- **Backoffice Permissions**: Restored App Management capabilities for App Admins (Tenants).
- They can now view and edit their own application details.
- **App Management**: Added a "General Settings" form to the App Details view allowing updates to App Name, URLs (Callback/Web/API), and Logo.
- **Technical Details**: Exposed `client_id`, `webhook_secret`, and `rotate_secret` functionality to App Admins for their owned applications.

## [0.2.1] - 2026-01-23
### Fixed
- **API Response**: The `validate_token` endpoint in `bifrost/internal/routes.py` now explicitly returns the `telegram_id` in its JSON response.
- **Webhooks**: Enhanced `account_update` webhooks in `bifrost/models/auth.py` to include changed identity fields (`telegram_id`, `email`, `username`) in the `extra_data` payload.

## [0.2.0] - 2026-01-22
### Added
- **Branding**: Implemented global support for `logo.png` and `favicon.ico` across the entire platform.
- **Email Branding**: Updated `bifrost/services/email_service.py` to inject the specific App's logo (or the Bifrost system logo) into invitation and OTP emails.
- **Dynamic Assets**: Added `get_default_logo_url` helper to resolve static assets via the `BIFROST_PUBLIC_URL`.

### Changed
- **UI Design**: Completely redesigned `backoffice/login.html` with a modern Tailwind glassmorphism aesthetic.
- **Backoffice**: Updated `create_app` and `add_user_to_app` logic to pass the specific `logo_url` to the email service during user invites.
- **Auth UI**: Updated `forgot_password` route to include branding in password reset emails.
- **Templates**: Updated `dashboard.html` and `index.html` to display the custom logo and favicon.

## [0.1.1] - 2026-01-22
### Added
- **User Invite Flow**: Implemented a system to invite new users via email when adding them to an App or assigning them as an App Admin during creation.
- **Email Service**: Added `send_invite_email` to `bifrost/services/email_service.py`.
- **UI**: Added "Initial Administrator" field to the Create App form in the Backoffice.
- **Developer Documentation**: Added a comprehensive documentation portal at `/docs`.
- **Docs Template**: Created `docs.html` with integration guides for Authentication, Payments, and Webhooks.

### Changed
- **Email Templates**: Refactored `verification_email.html` into a universal template supporting dynamic Titles, Subtitles, and Call-to-Action buttons.
- **Backoffice Logic**: Updated `create_app` and `add_user_to_app` to detect non-existent users, create placeholder accounts, and trigger invitation emails automatically.

### Fixed
- **Missing Webhook**: Fixed an issue where Admin Approval via the Bot triggered `account_role_change` instead of `subscription_success`.
- **Transaction Completion**: The `call_grant_premium` service now attempts to find and complete a pending transaction record before falling back to a manual role grant.
- This ensures the client app receives the transaction ID and amount in the webhook payload.

## [0.1.0] - 2026-01-22
### Added
- **Unified Portal**: The `/backoffice` now serves as the single portal for both Super Admins and App Admins.
- **App Management**: Super Admins can now **Create Applications** via the UI.
- **Secret Management**: Added "Regenerate Secret" functionality in the App Details view.
- **Passkey Prep**: Database models now support `webauthn_credentials` field (placeholder for future implementation).
- **Rich Webhooks**: The webhook system now supports arbitrary data payloads via `extra_data`.
- **Subscription Events**:
  - `subscription_success`: Fired when a payment completes. Payload includes `transaction_id`, `amount`, `currency`, and `role`.
- `subscription_expired`: Fired by the Reaper when a subscription expires.
- **Subscription Reaper**: Implemented `bifrost/scheduler.py`, a background cron job that runs every 60 minutes to automatically downgrade expired subscriptions.
- **Enterprise Payment Flow**: Implemented "Intent-Based" payments to prevent parameter tampering.
- New API: `POST /internal/payments/secure-intent` allows client apps to create a transaction record before generating a link.
- Bot Update: `/pay` and `/start` commands now accept a `transaction_id` (e.g., `tx-a1b2c3...`).
- **Tenant Dashboard**: Created `bifrost/backoffice.py` to allow App Admins to manage their specific users.
- **Role Hierarchy**:
  - **Super Admin**: Full access to all apps via Backoffice login.
- **App Admin**: Access restricted to apps where they hold the `admin` or `owner` role.
- **User Management UI**: Added `app_users.html` allowing Admins to manually change user roles (e.g., grant Premium) and extend subscription duration.
- **Subscription Expiration**: Updated `BifrostDB` models to support `expires_at` for app links.
- **Dynamic Pricing**: Bifrost Bot now parses `duration` (e.g., '1m', '1y') and `client_ref_id` from the payment payload.
- **Improved Parsing**: Added support for `/pay` command and complex deep-link payloads (format: `client_id__price__duration__role__ref`).
- **App Branding**: The Payment Bot now looks up and displays the actual "App Name" (e.g., "Finance Bot") during the payment flow instead of the raw client ID.

### Changed
- **UI Overhaul**: Migrated all Admin views to **Tailwind CSS**.
- **Authentication**: Login endpoints now explicitly check `username` OR `email` for all users.
- **Scheduler**: The subscription reaper now sends `subscription_expired` instead of the generic `account_role_change` event for better clarity in client apps.
- **Bot Architecture**: Migrated from Long Polling to Webhooks for cost efficiency and scalability on serverless platforms (Koyeb).
- **Process Management**: Updated `run.sh` to exclusively run the Gunicorn Web Server.
- The Bot process is now triggered internally via the `/telegram-webhook` route.
- **State Management**: Replaced local file storage (`PicklePersistence`) with `MongoPersistence` (`bot/persistence.py`) to store conversation states in MongoDB.
- **Models**: Updated `create_transaction` in `BifrostDB` to accept `None` for `account_id`, allowing transactions to be created before a user is identified.
- **Database Models**: Modularized the monolithic `BifrostDB` class into a `models` package containing `BaseMixin`, `AuthMixin`, `AppMixin`, and `PaymentMixin` for better maintainability.
- **Internal API**: Split `routes.py` into `routes.py` (Auth/User) and `payment_routes.py` (Transactions/Claims).
- **Bot Structure**: Modularized the Telegram Bot into a package structure with separate handlers for commands, payments, and admin functions.

### Removed
- **Legacy Admin**: Removed `bifrost/admin_panel.py` and the `flask-admin` dependency.
  All administration is now handled via the custom `backoffice` blueprint.

### Fixed
- **UX**: The final "Payment Accepted" message now displays the human-readable App Name (e.g., "Savvify") instead of the internal `client_id`.
- **Admin Approval**: Fixed a bug where the Admin Approve button failed with "App lookup_skipped not found".
- The bot now correctly fetches the `client_id` from the database during the `/start` command instead of relying on a placeholder.
- **Critical Deadlock**: Replaced the HTTP call in `call_grant_premium` with a direct database operation to prevent server worker freezing.
- **Persistence**: Fixed a critical bug where `user_data` was lost upon bot restart.
- The `MongoPersistence` class now correctly reads/writes user data.
- **Payment Flow**: Added a fallback in `receive_proof` for cases where payment details are forgotten.
- **Webhook Crash**: Resolved `RuntimeError: Install Flask with the 'async' extra` by converting the `/telegram-webhook` route to a synchronous wrapper.
- **Worker Compatibility**: Implemented a manual `asyncio` event loop within the webhook route.
- **Webhook Implementation**: Resolved `NameError: name 'Config' is not defined` by switching to `current_app.config`.
- **Concurrency Crash**: Resolved `RuntimeError: Event loop is closed` by creating an ephemeral `Application` instance for each incoming webhook request.
- **Configuration**: `config.py` now uses `pathlib` to find the `.env` file.

### Security
- **Tamper-Proofing**: Users can no longer modify the payment amount or duration by editing the Telegram deep link, as the link now only contains a reference ID.

## [0.0.1] - 2026-01-19
### Added
- **Centralized Auth**: Initial release as Global Identity Provider (IdP) for the ecosystem.
- **User Model**: Implemented comprehensive `User` model supporting password hashing, account management, and application linking.
- **Authentication API**: Headless API endpoints for Login, Registration, and Telegram Authentication with JWT issuance.
- **Service-to-Service Validation**: Internal routes for client services to validate User JWTs via Basic Auth.
- **Deep Linking Support**: Added `create_deep_link_token` and `POST /internal/generate-link-token` for secure "Web -> Telegram" account linking.
- **Unified Account Linking**: Added `POST /internal/link-account` supporting Email/Password and Telegram linking.
- **Payment Hooks**: Integrated Webhooks for Gumroad and ABA Payway.
- **Username Authentication**: Users can now set a unique username during registration.
- **Flexible Login**: Login endpoint supports both `email` and `username` as valid identifiers.
- **Bifrost Bot**: Introduced a dedicated Telegram Bot to handle centralized authentication and "Proof of Payment" flows.
- **Internal API**: Added `POST /internal/grant-premium` for manual user upgrades and `GET /auth/me` for token introspection.
