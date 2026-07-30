# Admin Console — Onboarding & Runbook

**Ministry Exam Prep · Bifrost "Valhalla" Console**
For the operations team, the content team, and whoever runs the staging cutover.

Screenshots get added during the handover session, against real staging data — they
are not useful until the tracks and content are loaded.

---

## 1. What this console is

One console, three jobs: approve the money, manage the content, see who did what.
It talks directly to your Supabase Postgres — there is no second copy of your data.

| I need to… | Go to |
|---|---|
| Approve / reject / refund a receipt | **Payments** → `/backoffice/app/<app_id>/payments` |
| Edit questions, choices, glossary | **Content** → `/backoffice/app/<app_id>/cms` |
| See who changed what | **Audit** → `/backoffice/app/<app_id>/audit` |
| Configure the grid (labels, hidden columns) | **CMS Settings** |
| Configure who can do what | **CMS RBAC** |
| Run 3-step setup wizard | **Onboarding Wizard** → `/backoffice/app/<app_id>/onboarding` |

### First-Run Setup (The 3-Step Onboarding Wizard)
When initializing a new app, the backoffice launches a 3-step wizard (`/backoffice/app/<app_id>/onboarding`):
1. **Connect Database**: Save encrypted PostgreSQL connection string.
2. **Smart Schema Detection**: Introspect schema, auto-hide system plumbing tables (`migrations`, `tokens`, `logs`), and propose friendly labels (`users` → `Customers`).
3. **Invite Team**: Assign initial staff roles by email before launching.

For the full interactive documentation, see the unified portal at `/docs`.

---

## 2. Roles — pick one per person

| Role | Payments | Content | Publish | Config | Analytics |
|---|:--:|:--:|:--:|:--:|:--:|
| `owner` / `admin` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `content_manager` | ❌ | ✅ | ❌ | ❌ | ✅ |
| `operations` (= billing) | ✅ | ❌ | ❌ | ❌ | ❌ |

Two things worth being explicit about, because they are business rules and not
preferences:

- **A Content Manager cannot see payments at all** — not the amounts, not the payer
  emails, not the receipt images. The server rejects the request; there is no
  "hidden button" involved.
- **A Content Manager cannot publish.** They move content `draft → review`. Only an
  Admin or Owner moves `review → published`.

### Who assigns roles

Backoffice → App → **Users** → add by email, pick a role. Nobody self-serves into the
console; a person with a higher rank has to put them there.

| I am… | I can create | I cannot |
|---|---|---|
| `owner` | anyone, including transferring ownership | — |
| `super_admin` | admin, content_manager, operations, and end-user roles | owner |
| `admin` | content_manager, operations, and end-user roles | admin or above |
| `content_manager` / `operations` | nobody | — |

You also cannot modify someone at your own rank or higher — an admin cannot demote
another admin. There is exactly one `owner` per app; granting `owner` to a second
person automatically demotes the first to `super_admin` and logs it.

Adding an email that has no Bifrost account sends an invite; the person sets their own
password from that email.

### Who approves a sign-in

**Nobody — and that is deliberate.** There is no human in the sign-in loop. The second
factor is a code emailed to the address on the account, so the approval is "you control
that mailbox", not "someone was awake to click yes". A human gate would break the 6-hour
payment SLA the first time it fired at 2am.

The human decision happens once, earlier: when someone grants the role. Revoking it
(Users → Remove) cuts access at the next request.

---

## 3. Signing in

1. Email + password.
2. **A 6-digit code is emailed to you.** Every account, every role, no exceptions.
   The code expires in 10 minutes.
3. Sessions end after **30 minutes idle** or **8 hours total**, whichever comes
   first — shorter than the student app on purpose.

Ten failed sign-in attempts from one IP inside 5 minutes triggers a cool-off.

---

## 4. Approving a payment

The queue is the left column; the receipt and actions are the right.

1. Click a payment. The receipt enlarges — click it again to zoom.
2. Check the amount against the receipt, and the `txn_ref` against the bank
   statement.
3. **Select the exam track** the user is paying for. There is no default, on
   purpose — the list comes from your `exam_tracks` table, so a new ministry shows
   up here as soon as you add the row.
4. **Verify & Approve.**

What the console guarantees:

- The payment status and the entitlement are written in **one transaction**. There
  is no state where a payment reads "approved" but the student has no access.
- Approving a `txn_ref` that was already approved is **blocked**, and the message
  names the earlier payment number so you can compare them.
- Clicking Approve twice does nothing the second time.

**The age badge is real.** It shows hours waiting against the 6-hour SLA: grey under
4.5h, amber approaching, red past. Amber and red also fire a Telegram alert to your
channel, once each, so nothing rots in the queue unnoticed.

### Rejecting

Pick a reason code — `wrong amount`, `unreadable`, `duplicate reference`, `other` —
and add a note. The user can upload a new receipt afterwards.

### Refunding

Only available on an already-approved payment. It sets the payment to `refunded`
**and** revokes the entitlement for **the track that payment unlocked** — derived
from the payment record, not from a dropdown. Access stops immediately.

If a payment predates the migration and the console can't tell which track it paid
for, it refuses and says so rather than guessing. Fix it by setting
`payments.exam_track_id` on that row, then refund.

### If your schema isn't Ministry's

The queue no longer assumes the `payments` / `entitlements` / `exam_track_id`
names — they come from the `payment_queue` block in **CMS Settings**
(`cms_config.payment_queue`): table, column names, status values, and the grant
step. An app with **no block gets Ministry's shape as the default**, which is why
this manual and its fallback SQL (§10) just work for Ministry. A tenant with a
different schema configures the block instead of waiting for a deploy; the console
validates the names against the live database schema on save. Omitting the `grant`
step gives a queue that settles the payment and leaves fulfilment to the tenant's
own app. The money-path tests for both shapes live in
[`tests/test_queue_schema.py`](../tests/test_queue_schema.py).

---

## 5. Publishing content

`draft → review` — Content Manager or Admin.
`review → published` — **Admin/Owner only**, and only if the question passes:

- exactly **4** choices
- exactly **1** marked correct
- the correct choice has an explanation in **both** Khmer and English
- `source_ref` is not empty

If it fails, the console tells you which rule broke and the row stays where it was.
The check runs on the server, so it applies to the grid, the drawer, and the
status-pill quick menu alike.

> The bilingual-explanation rule is applied to the **correct** choice. If you want
> all four choices to require explanations, that's one line in
> `validate_question_publishable` — say the word.

Khmer renders in Kantumruy Pro with Noto Sans Khmer as fallback, at line-height 1.8,
in every grid cell, drawer field and preview. If you ever see a clipped subscript,
that is a bug — report it with a screenshot.

---

## 6. Support actions

- **Suspend / reinstate** an account: reason mandatory, audit-logged.
  **Suspension does not touch entitlements** — a reinstated user gets their paid
  access back. Access is gated on `users.status`, so the app must check it.
- **Entitlement override**: force-grant or revoke premium for a track. For seeded
  reviewers and support cases. Audit-logged with before/after.

---

## 7. Audit log

Every mutation the console makes — content, payments, users, entitlements — writes
actor, table, row, action, timestamp and a before/after snapshot. Filter by table or
by actor; expand a row to see the diff.

Retention is a minimum of one year for dispute resolution. There is deliberately no
TTL on the collection: nothing expires it automatically, and nobody should add one
without a decision from you.

---

## 8. Staging cutover — do this in order

1. **Create the restricted database role.** Run section 7 of
   [`migrations/001_console_phase1.sql`](../migrations/001_console_phase1.sql) as a
   Supabase owner. The console must never hold `service_role`.
   Verify: `SET ROLE console_agent; CREATE TABLE t(i int);` must **fail**.
2. **Run the migration** (sections 1–6 of the same file) against staging. It is
   idempotent and safe to re-run.
3. **Register the app** in Bifrost and paste the `console_agent` connection string
   into the app's `db_connection`. It is encrypted at rest.
4. **Configure notifications** on the app document:
   ```json
   { "channel": "telegram", "bot_token": "…", "chat_id": "-100…" }
   ```
   `channel` may also be `email` (add `"email": "ops@…"`) or `webhook` (add
   `"url": "https://…"`). Changing channel is configuration, not a deploy.
5. **Point the student app** at `POST /backoffice/api/tenant/<app_id>/payments/notify-new`
   with header `X-Webhook-Secret: <app webhook_secret>` when a receipt is uploaded.
6. **Onboard the CMS** — first Admin to open Content walks the onboarding screen
   once, which hides system tables and sets friendly labels.
7. **Create staff accounts** and assign roles per §2.
8. **Walk §9 below** before touching production.

---

## 9. Acceptance verification — how to prove each box

Run these on staging, ideally with someone who didn't build it.

| SOW acceptance criterion | How to verify |
|---|---|
| Receipt approved end-to-end, entitlement flips within 5s | Approve a real receipt; query `entitlements` for the payer; the app unlocks |
| Duplicate `txn_ref` blocked, prior payment surfaced | Create two payments with the same ref, approve both — the second is refused and names the first |
| Refund revokes access, audit-logged with reason | Refund an approved payment; check `entitlements.status`, then open Audit |
| Content Manager rejected server-side on payments | `curl -X POST .../payments/1/approve` with a Content Manager session cookie → **403**, not a redirect |
| Bad MCQ cannot be published | Try to publish a question with 3 choices / 2 correct / missing English explanation / empty `source_ref` |
| Content Manager cannot publish; Admin can | Same question, two accounts |
| Khmer renders without clipped subscripts | Open a real Khmer question in the grid, the drawer, and the preview |
| Audit shows before/after for every UAT mutation | Filter the audit log by your own actor id |

The automated half of this lives in
[`tests/test_console_phase1.py`](../tests/test_console_phase1.py) — 27 tests:

```bash
python -m unittest discover -s tests -v
```

---

## 10. If the console is down during an SLA window

The fallback is manual and deliberately simple. Two people, one SQL statement, one
row in a log.

1. Verify the receipt against the bank statement as usual.
2. Have a second person confirm the amount and `txn_ref` out loud. There is no
   duplicate check in this path — you are the duplicate check.
3. In the Supabase SQL editor, as an owner:
   ```sql
   BEGIN;
   UPDATE payments SET status='approved', reviewed_by='<your name>', reviewed_at=NOW(),
          exam_track_id=<track>
   WHERE id=<payment id> AND status='pending';
   INSERT INTO entitlements (user_id, exam_track_id, status, activated_at)
   VALUES (<user id>, <track>, 'premium', NOW())
   ON CONFLICT (user_id, exam_track_id)
   DO UPDATE SET status='premium', activated_at=NOW();
   COMMIT;
   ```
   Both statements or neither — do not run them separately.
4. **Write it down**: payment id, track, who approved, when, why the console was
   unavailable. These rows will not be in the audit log, and that gap needs to be
   reconstructable later.
5. When the console is back, reconcile: every manually-approved payment should have
   a matching entitlement and no duplicate `txn_ref`.

---

## 11. Not built yet (Phase 2)

Being explicit so nobody discovers these at UAT:

- **Bulk import** (CSV/JSON, dry-run, offset integrity). Content is entered through
  the grid until this ships. This is the biggest remaining gap.
- **Versioning and rollback** of questions and glossary terms. Edits currently
  overwrite; the audit log holds the before-state, so nothing is unrecoverable, but
  there is no version history table and no rollback button yet.
- **Analytics dashboard** (§3.7) and the transaction ledger CSV export.
- **App configuration without redeploy** (§3.6) — timer, blueprint weights, the
  fixed free-diagnostic 15.
- **Anomaly flag panel** on the user detail view.
- **CSP headers and self-hosted fonts.** Tailwind, Alpine and the Khmer fonts load
  from CDNs today; a strict CSP means vendoring all three.
- **Receipt images in a private bucket with signed URLs.** Note the trade-off: once
  receipts are private, Telegram cannot render the thumbnail in the alert, and the
  alert becomes a link into the console. The console already has the switch —
  set `receipts_public: false` on the app document.
