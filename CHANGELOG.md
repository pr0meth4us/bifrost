# Changelog

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

## [Unreleased]

### Added
- Created `upload_sa_to_bifrost.py` script to seamlessly push Google Service Account credentials directly into the Bifrost MongoDB Vault.
- Added `/heimdall/ai-metrics` routing in `backoffice.py` to query Google Cloud Monitoring.
- Implemented `ai_metrics.html` visual dashboard for Heimdall users to track `aiplatform.googleapis.com` token metrics in real-time.
- Updated `dashboard.html` to integrate AI Metrics button.
- Added `google-cloud-monitoring` dependency to `requirements.txt`.

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