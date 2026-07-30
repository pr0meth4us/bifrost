# Admin Console SOW v0.1 — Gap Analysis & Vendor Response

**Against:** *Scope of Work — Admin Console ("Control Room")*, Ministry Exam Prep, v0.1
**Assessed system:** Bifrost backoffice / Valhalla Portal at `4f36089`
**Date:** 2026-07-28

Part A is internal — evidence, defects, honest status. Part B is the reply to send.

> **Status update.** Phase 1 and Phase 1.5 are now implemented. Every defect in A1 is
> fixed, the §4 scorecard has moved (see A4), and the six-test suite from §6.3 exists
> and passes. Phase 2 — bulk import, versioning/rollback, analytics, app config, CSP —
> is **not** built; it is listed as remaining in
> [`docs/guides/console-onboarding.md`](../guides/console-onboarding.md) §11 so nobody discovers it at UAT.
> A1 below is kept as written, with outcomes appended, because the client asked for the
> defect history and it is also the changelog for what shipped.

---

# PART A — Internal reality check

`cms.md` records the client scoring this build **10/10, all PASSED**. That evaluation was
made against screenshots and a feature list. Read against the SOW's acceptance criteria —
which are behavioural and mostly say "verify at the network layer, not the UI" — the picture
is different. Four of the ten acceptance boxes fail today, and three of the failures are in
the money path.

## A1. Live defects in code the client believes is done

Ordered by blast radius.

**1. Every state-changing POST in the console is currently rejected.**
`CSRFProtect(app)` was enabled in [`bifrost/__init__.py:159`](bifrost/__init__.py:159) and the
backoffice blueprint is deliberately *not* exempted. Exactly one template emits a token
(`cms_onboarding.html`). Approve, reject, refund, CMS create/save/delete, user suspend — all
post without `csrf_token` and get a 400. The money path is dead as of HEAD.
*Fix: token in the base template + every form. Half a day.*

**2. Refund can never succeed.**
[`tenant_routes.py:136`](bifrost/backoffice/tenant_routes.py:136) requires `track_id` from the
form. The refund form at [`payment_queue.html:174`](bifrost/templates/backoffice/payment_queue.html:174)
submits only `reason`. Guaranteed flash-and-redirect, no refund.

**3. The approval webhook silently never fires.**
`approve_payment` passes the *tenant Postgres* `payments.user_id` (an integer) as `account_id`
into `_trigger_event_for_user`, which does `ObjectId(account_id)`
([`base.py:70`](bifrost/models/base.py:70)) inside a bare `try/except` that swallows the
`InvalidId`. This is the same identity-namespace bug class the client called out in §3.1 —
they had the reviewer's ID where the payer's belonged; we have a Postgres ID where a Mongo
ObjectId belongs. It fails quietly, which is worse.
*Needs an explicit `postgres users.id → bifrost account` resolution step, and the except
narrowed so it can't swallow the next one.*

**4. Track selection is hard-coded, and the approve fallback is `1`.**
Two `<option>`s literally spelling MFAIC and MoH in
[`payment_queue.html:157`](bifrost/templates/backoffice/payment_queue.html:157), plus
`track_id = ... or app.get('default_track_id') or 1` at
[`tenant_routes.py:67`](bifrost/backoffice/tenant_routes.py:67). The SOW's second named prior
bug was "defaulted refunds to the wrong exam track". We currently default *approvals* to
track 1. Options must come from `exam_tracks WHERE is_active`, and refund must derive the
track from the payment being refunded, never from a form field.

**5. The SLA age is a literal string.**
[`payment_queue.html:80`](bifrost/templates/backoffice/payment_queue.html:80) — `<!-- Simulated
SLA age --> Pending < 2h`. Same for every row regardless of age. `get_manual_payments` doesn't
even select `created_at`. The 6h SLA is a tracked business KPI for them; right now the console
asserts every payment is inside it.

**6. Content-role users can read the payment queue.**
`view_manual_payments` gates on `read:config`
([`tenant_routes.py:23`](bifrost/backoffice/tenant_routes.py:23)), which *every* role in
`ROLE_PERMISSIONS` holds, down to `viewer`. Payer emails, amounts, txn refs and receipt images
are readable by any authenticated tenant user. Acceptance criterion "Content Manager rejected
server-side on a payments endpoint" fails on read. (Approve/reject/refund do check
`payments:approve` correctly — the mutation gate is real, the read gate isn't.)

**7. Field-level permissions leak at the network layer.**
`role_hidden_cols` filters `visible_columns` for rendering, but
[`content_grid.html:212`](bifrost/templates/backoffice/content_grid.html:212) ships
`{{ row | tojson }}` — the *entire* row, hidden columns included — into an `onclick`
attribute. Their own handover checklist §2 forbids precisely this. Fix is server-side: strip
the columns from `rows` before render, not in the template.

**8. No duplicate-`txn_ref` guard, no state-machine check, no row lock.**
`approve_manual_payment` reads the payment on one connection and writes on another, with no
`SELECT ... FOR UPDATE`, no check that status is still `pending`, and no uniqueness check
against previously approved refs. Double-click double-approves. Both are explicit SOW
requirements and explicit test-suite deliverables.

**9. `assert` used as the SQL identifier guard.**
[`payments.py:453`](bifrost/models/payments.py:453) and friends. Asserts are removed under
`python -O`. The validation itself is sound (regex on table, `sort_by` checked against the
introspected schema, identifiers quoted) — but it must be a raised exception, not an assert,
before we let anyone re-run the injection audit they mention in §4.4.

**10. Vocabulary mismatch against their schema.**
We write `entitlements.status = 'revoked'` on refund and `'rejected'` on suspend; their stated
domain is `free/pending/premium/rejected`. And `approve` uses
`ON CONFLICT (user_id, exam_track_id)`, which requires a unique constraint they haven't said
exists. Both need confirming before staging — one is a migration, one is a wrong value.

## A1b. Outcomes

| # | Defect | Fix |
|---|---|---|
| 1 | CSRF token missing from every form | Token injected into all 10 backoffice templates; `notify-new` explicitly exempted (it is machine-to-machine and has no session to protect) |
| 2 | Refund could never succeed | Track no longer comes from the form at all — derived from the payment |
| 3 | Approval webhook silently dead | Payer resolved from the tenant email via `find_account_by_email`; when no Bifrost account matches, the console says so instead of reporting success |
| 4 | Hard-coded tracks, approve defaulting to `1` | Dropdown reads `exam_tracks WHERE is_active`; no default, approval refuses without a track |
| 5 | Simulated SLA age | Real age from `created_at`, ok/warn/breach states, plus a 15-minute sweep that alerts the channel once per payment per state |
| 6 | Content roles could read the payment queue | Queue gates on `payments:view`, which `content_manager` does not hold |
| 7 | Hidden columns shipped in the row payload | Stripped server-side before render |
| 8 | No dup-ref guard, no state machine, no lock | `SELECT … FOR UPDATE`, transition table, settled-ref check inside the same transaction |
| 9 | `assert` as the SQL identifier guard | `safe_ident()` raising `ValueError`, applied at all 6 sites |
| 10 | Vocabulary mismatch with their schema | Revocation writes `rejected` (inside their declared domain); `exam_track_id`, `created_at`, uniqueness and `console_agent` grants proposed in `migrations/001_console_phase1.sql` |

Two additional root-cause fixes found while in there, not in the original list:

- **Suspension destroyed purchases.** `suspend_tenant_user` revoked *all* of a user's
  entitlements while `reinstate` only flipped `users.status` back — a suspended user
  permanently lost paid access. Suspension no longer touches entitlements at all.
- **A fresh Postgres connection per query** (SOW §5 forbids it) — replaced with a
  per-connection-string `ThreadedConnectionPool` that rolls back any transaction left
  open by an early return before the connection is reused.

## A2. SOW coverage map

| § | Requirement | Status | Note |
|---|---|---|---|
| 3.1 | Split-screen queue + zoom receipt | **Ships** | Layout is genuinely done and good |
| 3.1 | Approve → entitlement, atomic | **Partial** | Single txn/commit is correct; missing lock, status check, dup-ref block |
| 3.1 | Reject with reason **code** | **Partial** | Free text only, no code enum |
| 3.1 | Refund | **Broken** | A1-2, A1-4 |
| 3.1 | Fraud: unique txn_ref, receipt checksum | **Missing** | Both |
| 3.1 | SLA age + threshold alert | **Missing** | A1-5; alert fires on upload only |
| 3.1 | Telegram, pluggable channel | **Partial** | Telegram works; `dispatch_sla_alert` hard-returns False for any non-telegram channel ([`notification_service.py:44`](bifrost/services/notification_service.py:44)) |
| 3.1 | Server-side state machine + actor/time/reason per transition | **Missing** | No transition table; payment actions write no audit row at all |
| 3.2 | Grid, inline edit, search, pagination, drawer, widgets | **Ships** | Strongest part of the build |
| 3.2 | **Khmer font stack** | **Missing** | Zero occurrences of Kantumruy/Noto Sans Khmer in any backoffice template. They said they will reject the UI on this |
| 3.2 | Publish workflow (only Admin publishes) | **Missing** | `status` is an ordinary column; any write-capable role can set `published` |
| 3.2 | Publish-time validation (4/1/bilingual/source_ref) | **Missing** | — |
| 3.2 | Versioning + rollback as new version | **Missing** | `delete_cms_row` is a hard `DELETE` |
| 3.3 | Bulk import, dry run, offset integrity | **Missing** | Entire section |
| 3.4 | Glossary CRUD, versioned | **Partial** | Generic grid covers CRUD; versioning missing per 3.2 |
| 3.5 | User search, entitlement override, suspend/reinstate | **Partial** | Suspend/reinstate exist; suspend revokes *all* tracks; no reset-quota; anomaly panel missing |
| 3.6 | Config without redeploy | **Missing** | `cms_config` configures the console, not the app (timer, blueprint, free-15) |
| 3.7 | KPI dashboard, ledger CSV | **Missing** | `metrics_service.py` is GCP AI spend, unrelated |
| 3.8 | Three roles, server-side, one shared mechanism | **Partial** | `check_permission` is shared and correct in shape; no Content Manager role; read gate too loose (A1-6) |
| 3.9 | Audit log, before/after, timeline UI, 1yr | **Partial** | `cms_audit_log` captures before/after for grid writes only. No payment/user/config coverage, no UI, no retention |

## A3. §4 security scorecard (as assessed)

| # | Item | Status |
|---|---|---|
| 1 | MFA all admin accounts | **Fail** — password only ([`auth_routes.py`](bifrost/backoffice/auth_routes.py)); OTP exists but only for password reset |
| 2 | CSRF on state-changing requests | **Configured, not wired** — see A1-1 |
| 3 | No `service_role` / dedicated `console_agent` | **Open** — we consume whatever conn string is configured. Needs a grant script from us + their sign-off |
| 4 | Parameterized queries, identifiers validated vs schema | **Pass with caveat** — logic is right, `assert` is the wrong enforcement (A1-9) |
| 5 | Credentials encrypted at rest + rotation | **Fail as designed** — the tenant DB URL is encrypted with `app['webhook_secret']` ([`tenant_routes.py:15`](bifrost/backoffice/tenant_routes.py:15)), and that same secret is handed to the tenant app as its webhook credential. One secret, two trust domains. Needs a separate KMS/env key + documented rotation |
| 6 | Admin session idle/max lifetime | **Fail** — no `PERMANENT_SESSION_LIFETIME`, no cookie flags in `config.py` |
| 7 | Rate limit admin login | **Fail** — none |
| 8 | Private bucket + signed receipt URLs | **Fail** — `receipt_url` rendered raw, and pushed to Telegram's `sendPhoto`, which requires a publicly fetchable URL. Signed URLs will break the Telegram photo path; send the alert without the image, link into the console instead |
| 9 | No unauthenticated endpoints | **Pass, one bug** — `/api/tenant/<id>/payments/notify-new` checks the webhook secret (non-constant-time), but sits on the CSRF-protected blueprint, so it 400s before reaching that check. Exempt it explicitly |
| 10 | Input validation, escaping, CSP | **Fail** — no CSP; Tailwind + Alpine load from CDN and there's a `via.placeholder.com` fallback image. CSP means vendoring those |

## A4. §4 security scorecard (after Phase 1)

| # | Item | Now |
|---|---|---|
| 1 | MFA on all admin accounts | **Pass** — emailed 6-digit second factor on every console sign-in, every role. No session is issued until it verifies |
| 2 | CSRF | **Pass** — enforced globally, tokens present, one deliberate documented exemption |
| 3 | No `service_role` | **Ready, needs them to run it** — `console_agent` grants and the deny-list are written; a Supabase owner must execute them |
| 4 | Parameterized queries, validated identifiers | **Pass** — `safe_ident()` raises; `sort_by` still checked against the introspected schema |
| 5 | Credentials encrypted at rest + rotation | **Still open** — the tenant DB URL is encrypted with `webhook_secret`, which is also shared with the tenant app. Needs a separate key. **This is the one §4 item Phase 1 did not close** |
| 6 | Session policy | **Pass** — 30-minute idle, 8-hour maximum, HttpOnly/SameSite/Secure cookies |
| 7 | Login rate limit | **Pass** — 10 attempts per IP per 5 minutes via Redis; fails open if Redis is down, deliberately, because locking ops out during an SLA window is the worse failure |
| 8 | Private bucket + signed URLs | **Console-side ready** — `receipts_public: false` stops leaking the image into Telegram; the bucket move itself is theirs |
| 9 | No unauthenticated endpoints | **Pass** — `notify-new` now compares its secret in constant time and is reachable again |
| 10 | Validation, escaping, CSP | **Partial** — CSP and self-hosted assets remain Phase 2 |

---

# PART B — Reply to the client

## B1. Position

We're not proposing a new build. Ministry Exam Prep already runs on Bifrost, and the console
in §3.1, §3.2 and §3.8 exists today against your Supabase schema. What this SOW correctly
identifies is that a console demoed is not a console audited. We've re-read our own build
against your acceptance criteria and we're telling you up front which boxes it fails: the
Khmer font stack (§3.2), the SLA age indicator (§3.1 — currently a placeholder, not a
computation), payment-queue read access for content roles (§3.8), and refund, which has a
form/route mismatch that makes it fail closed. Those are ours, they're in Phase 1, and they
aren't billable as new scope.

## B2. Answers to §10

**1. Existing framework or custom?** Neither pole. Bifrost is our own platform and the console
is a schema-introspecting generic layer over your Postgres — Retool-shaped capability, but ours
to change. That matters for exactly the two things you named. Khmer rendering: an off-the-shelf
console gives you its own grid cells and its own line-height, and you file a ticket. Ours is
our CSS. Payment workflow: your approve→entitlement step is one transaction against your tables,
not a webhook chain across a vendor's automation product.

**2. Atomic payment↔entitlement across a schema we don't own.** Both writes go through a single
connection and a single commit against your Postgres — no distributed transaction, no
compensating logic, no dual-write. We'll additionally take `SELECT ... FOR UPDATE` on the
payment row and re-assert `status = 'pending'` inside the transaction, so a double-submit
can't double-approve. Two things we need from you, because they're schema, not code: a unique
constraint on `entitlements (user_id, exam_track_id)`, and confirmation of the allowed
`entitlements.status` values — your §2 table lists `free/pending/premium/rejected`, but §3.1's
state machine needs a `REFUNDED` state. Per your own rule we're proposing, not applying.

**3. Versioning and rollback.** Append-only. A correction inserts a new `questions` row with
`version + 1`; `attempts` keep pointing at the version they were served. Rollback reads the
target snapshot and writes it forward as a *new* version — history is never mutated or
deleted, which is your acceptance wording and also the only version model that survives a
dispute. Choices ride along in the same transaction as the question, so a rolled-back question
can't half-restore. This needs one schema addition — a `question_versions` snapshot table — and
is the largest genuinely-new piece of work in the SOW.

**4. Security ownership and independent review.** Named owner on our side, and yes to review
before acceptance — with a request: run it against staging *after* Phase 1 hardening, not
against today's `main`. Part A of this document is our own findings list; we'd rather hand your
reviewer a fixed system and our notes than have them rediscover our bugs at your expense.
Two §4 items need decisions from you rather than work from us: the `console_agent` role (we'll
supply the exact `GRANT`s; someone with Supabase owner rights has to run them) and moving
receipts to a private bucket — which will remove the receipt thumbnail from your Telegram
alerts, since Telegram needs a publicly fetchable URL. The alert becomes a link into the
console. Confirm you'll take that trade.

**5. What we need to start.** Supabase staging credentials, the `exam_tracks` rows you'll
launch with, a sample export from your offline pipeline (the real CSV/JSON, with real Khmer, not
a sample we invent), your Telegram bot token and target chat for staging, and a decision on the
two schema additions above. Phase 1 as scoped below is realistic once those land.

**6. Change requests.** Anything in this SOW is fixed price. Anything outside it is quoted
before work starts, against this document as the baseline. Our own defect list in Part A is not
a change request.

## B3. Phasing

Your §9 suggestion is right; we'd tighten it.

**Phase 1 — money path and access control.**
CSRF wiring, refund fix, webhook identity fix, tracks from `exam_tracks`, real SLA age +
threshold alerting, duplicate-`txn_ref` block, receipt checksum warning, reason-code enums,
server-side state machine with actor/reason on every transition, payments audit rows, role
split (Admin / Content Manager / Operations) with the read gate tightened, §4 items 1/2/5/6/7/9,
plus the six tests in §6.3. This is where every acceptance criterion touching money lives.

**Phase 1.5 — the two rejections waiting to happen.**
Khmer font stack, and hidden columns stripped server-side. Small, but each fails a stated
acceptance test on its own, so they don't wait for Phase 2.

**Phase 2 — content.**
Publish workflow and publish-time validation, versioning/rollback, bulk import with dry-run and
offset integrity, glossary versioning, anomaly-flag panel, app config (§3.6), analytics (§3.7),
audit timeline UI, CSP and asset vendoring.

## B4. Where we agree with your out-of-scope list

No multi-tenancy abstraction is being built *for you* — Bifrost is already multi-tenant, your
product is one tenant in it, and nothing in this scope adds a tenant layer to your data. Worth
naming explicitly since §8 forbids it: you get the isolation without paying for the abstraction.
No AI/ML. No bank API. We ingest your pipeline's output through the same import endpoint any
human would use, per §3.3 — we agree with the reasoning and won't ask for a side door.

## B5. Before this can be signed

§9 is entirely blank and the owner line at the top is unfilled — nine placeholders. We can't
quote Phase 1 without a budget model and a target date, and IP ownership ("we require full
ownership of source code") needs care: your product's schema, content and console
configuration are yours outright, and we'll transfer the console code in a repository you own.
The Bifrost platform underneath it is licensed, not assigned. If full assignment of the platform
is a hard requirement, say so now — it changes the shape of the deal, not just the price.

One more, on §3.2: the font line pins `'Kantumruy Pro', 'Noto Sans Khmer'` for the console.
We're happy with that and would leave it — Noto is unambiguously licensable, and this is the
back-office grid, not the student-facing app. If you later unify type across both products,
this line needs revisiting; there's no user-visible gain in doing it now.
