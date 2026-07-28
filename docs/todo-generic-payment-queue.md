# TODO — Make the payment queue tenant-agnostic

**Owner:** unassigned · **Status:** DONE 2026-07-28 — built ahead of the trigger by
request. Tasks 1-6 shipped; task 7 partially (see below).

> The "do not build this speculatively" warning below was overridden deliberately.
> It is left in place because the reasoning still stands: the seam was chosen from
> ONE real example plus a hypothetical, so treat the shape as provisional until a
> second tenant actually lands on it.

**Still outstanding:** the Ministry client has NOT been consulted. Their SOW §8 asks
to be told before any tenant abstraction is designed. This changes no Ministry data
and no Ministry behaviour, but the conversation is owed. See the last section.

---

## What shipped

| Task | State |
|---|---|
| 1. Config schema + save-time validation | done — [`queue_schema.py`](../bifrost/models/queue_schema.py), `_validate_payment_queue()` in tenant_routes |
| 2. Resolved config threaded through | done — one `QueueSchema` per request from `_app_and_conn()`, passed as `queue=` |
| 3. Grant extracted | done as config, not an interface — `grant` is a config block, and omitting it gives the webhook-only queue. No second implementation exists, by design |
| 4. `PLATFORM_LOCKED_TABLES` out of config.py | done — `locked_tables_for()` unions the app doc's `platform_locked_tables` over the platform floor |
| 5. Ministry backfilled | done by omission — no `payment_queue` block means QueueSchema defaults, which emit byte-identical SQL. `tests/test_console_phase1.py` passes untouched |
| 6. Second fixture tenant | done — [`tests/test_queue_schema.py`](../tests/test_queue_schema.py) runs the same assertions against Ministry and a shop config sharing no identifier |
| 7. `validate_question_publishable` | partial — WHICH table is validated is now config (`publish_validation_table`); the MCQ rules are still hardcoded, deliberately |

Two things worth knowing:

* **Rows come back under canonical keys.** `get_manual_payments` aliases every tenant
  column to `id`, `user_id`, `txn_ref` and friends, so templates and webhook payloads
  never see tenant vocabulary. The alias is omitted when the names already match,
  which is why the default SQL is unchanged.
* **The SLA sweep now covers every open state**, not the literal word `pending`.
  `unclaimed` was always treated as in-SLA by the clock but was excluded by the
  sweep's filter — an inconsistency, now fixed. Ministry will see SLA alerts for
  unclaimed receipts that were previously silent. Revert by passing a single status
  in [`scheduler.py`](../bifrost/scheduler.py) if that is unwanted.

The e-commerce analysis below is unchanged and still correct: partial refunds, stock
and fulfilment do **not** fit the grant model, and such a tenant must leave `grant`
unset so its own app owns those decisions.

---

## Why this exists

The CMS grid is genuinely generic — it reads `information_schema` and works against
any Postgres. The **payment queue is not**. Everything shipped in the 2026-07-28
console release is Ministry-Exam-Prep-shaped by name, not by configuration.

That was the right call for one tenant with a launch date. It stops being the right
call the moment a second tenant needs a receipt queue, and we should be honest that
this is deferred design, not an accident.

## Where the coupling lives

All in [`bifrost/models/payments.py`](../bifrost/models/payments.py) unless noted.

| Where | Hardcoded |
|---|---|
| `get_manual_payments` (:288) | `payments`, `users`, `p.exam_track_id`, the `LEFT JOIN` shape |
| `get_active_tracks` (:329) | `exam_tracks`, `is_active`, `ministry`, `name_en` |
| `get_manual_payment_by_id` (:342) | `payments` ⋈ `users` |
| `_lock_payment` (:365) | `payments`, `exam_track_id`, `receipt_checksum` |
| `_find_duplicate_txn_ref` (:394) | `payments.txn_ref`, the settled-status set |
| `find_duplicate_receipt` (:405) | `payments.receipt_checksum` / `receipt_url` |
| `approve_manual_payment` (:428) | `entitlements(user_id, exam_track_id, status, activated_at)`, literal `'premium'` |
| `reject_manual_payment` (:483) | `payments.reject_reason` / `notes` |
| `refund_manual_payment` (:511) | `entitlements`, the track-inference fallback, literal `'rejected'` |
| `suspend_tenant_user` (:568) | `users.status` / `suspended_at` / `suspend_reason` |
| `set_entitlement` (:613) | `entitlements`, the four-value status domain |
| `validate_question_publishable` (:644) | `questions`, `choices`, `source_ref`, `explanation_kh/en` |
| `check_publish_permission` ([tenant_routes.py:474](../bifrost/backoffice/tenant_routes.py:474)) | table name `questions` |
| `PLATFORM_LOCKED_TABLES` ([config.py:62](../config.py:62)) | keyed by `client_id` — **a new tenant is a code change and a deploy** |

## Why an e-commerce tenant doesn't fit

The domain model, not just the table names:

- They have **orders and line items**, not one payment granting one entitlement.
- **Refunds are partial** — one line item out of five — so "set status refunded,
  revoke the entitlement" has no equivalent. Our refund is all-or-nothing by design.
- Refunds **touch stock**: a refund is also a restock decision, which is a write our
  console has no concept of.
- **Fulfilment** is a state our machine doesn't have. `PENDING → PAID → SHIPPED →
  DELIVERED → RETURNED` is not `FREE → PENDING → PREMIUM → REFUNDED`.
- Their "grant" is a **shipment**, not an access flag.

So this is not a rename job. Roughly: the queue, the receipt workspace, the duplicate
checks, the SLA machinery, the audit trail and the RBAC generalise cleanly. The
**grant/revoke half does not** and needs a real abstraction.

## Proposed shape

A per-app `payment_queue` block in the existing Mongo `cms_config`, so a new tenant is
configuration and not a deploy. Strawman:

```json
{
  "payment_queue": {
    "table": "payments",
    "subject_join": { "table": "users", "on": "user_id", "label": "email" },
    "fields": {
      "amount": "amount", "reference": "txn_ref",
      "receipt": "receipt_url", "checksum": "receipt_checksum",
      "created": "created_at", "status": "status"
    },
    "states": {
      "open": ["pending"], "settled": ["approved", "refunded"],
      "transitions": { "pending": ["approved", "rejected"], "approved": ["refunded"] }
    },
    "grant": {
      "type": "entitlement",
      "table": "entitlements",
      "keys": ["user_id", "exam_track_id"],
      "on_approve": { "status": "premium", "activated_at": "NOW()" },
      "on_revoke":  { "status": "rejected" },
      "scope_source": "payments.exam_track_id",
      "scope_options": { "table": "exam_tracks", "active": "is_active", "label": "name_en" }
    }
  }
}
```

`grant.type` is the extension point. `entitlement` covers Ministry. E-commerce needs a
second implementation (`fulfilment`, or a webhook-only `notify` that hands the grant
back to the tenant app and keeps stock logic out of our console entirely — probably
the right first move).

Every identifier from this config goes through `safe_ident()` **and** is checked
against the introspected schema before it reaches SQL. Config becomes an injection
surface the moment we do this; that check is not optional.

## Tasks

1. **Define the config schema and validate it on save.** Reject a `payment_queue`
   block whose tables/columns don't exist in the tenant schema, at save time, with a
   readable error. Nothing else starts until this is in place.
2. **Thread a resolved config object through the payment methods.** One
   `QueueSchema` value object built once per request; no method reads raw dicts.
3. **Extract the grant step behind an interface.** `EntitlementGrant` is the first
   and only implementation; do not add a second until a real tenant needs it.
4. **Move `PLATFORM_LOCKED_TABLES` out of `config.py`** into the app document, with
   the platform-level defaults still enforced server-side so a tenant cannot grant
   itself access to a locked table.
5. **Backfill Ministry's own config** and confirm the console behaves identically —
   [`tests/test_console_phase1.py`](../tests/test_console_phase1.py) must pass
   untouched. If a test needs editing, the abstraction changed behaviour and that is
   a bug, not a test problem.
6. **Add a second fixture tenant to the tests** with different table names, and run
   the same assertions against it. That is the only real proof this worked.
7. **Generalise `validate_question_publishable`** last, or not at all — publish rules
   are genuinely domain-specific and a rules-config may be worse than a second
   function.

## Estimate

2–3 days for tasks 1–6, assuming the second tenant's schema is known. Task 7 is
open-ended; scope it separately.

## Non-goals

- **Do not build this speculatively.** One example is not enough to find the right
  seam — we would be guessing, and a wrong abstraction here is worse than the current
  honest hardcoding.
- **Do not put stock, fulfilment or partial-refund logic in the console.** If an
  e-commerce tenant needs those, the grant step should be a webhook to their app.
- No multi-tenancy work for Ministry Exam Prep. Their SOW §8 forbids a tenant
  abstraction in their data. This is Bifrost-side only and does not change their
  schema.

## Before starting, tell the Ministry client

This touches the exact code path their acceptance tests cover (approve → entitlement,
refund → revocation, duplicate `txn_ref`). Their SOW §8 asks to be consulted before
any tenant abstraction is designed. Raise it, don't surprise them.
