# bifrost/models/payments.py
import secrets
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from bson import ObjectId
import logging

from . import cms_mongo
from .queue_schema import (  # noqa: F401 — safe_ident is re-exported for callers
    DEFAULT as DEFAULT_QUEUE, IDENT_RE, QueueSchema, _table_columns, safe_ident,
)

def _request_cache():
    """Per-request memo dict, or None outside a request context.

    Introspecting a tenant's schema is the most expensive thing the CMS does —
    a three-way join across information_schema — and one grid load asked for it
    twice: get_tenant_table_data validates sort_by against it, and the route
    then fetches it again for the drawer. The tenant's schema cannot change
    inside one request, so the second call is free.

    ponytail: request-scoped on purpose. A longer-lived cache would need
    invalidation on every migration a developer runs from devtools, and a stale
    column list is a silently dropped write.
    """
    try:
        from flask import g, has_request_context
        if not has_request_context():
            return None
        cache = getattr(g, '_bifrost_schema_cache', None)
        if cache is None:
            cache = {}
            g._bifrost_schema_cache = cache
        return cache
    except Exception:
        return None


def _memoized(key, produce):
    cache = _request_cache()
    if cache is None:
        return produce()
    if key not in cache:
        cache[key] = produce()
    return cache[key]


log = logging.getLogger(__name__)
UTC = ZoneInfo("UTC")

# Columns the CMS fills in itself on update when the tenant table has them.
REVIEW_STAMP = ('reviewed_by', 'reviewed_at')

# --- Manual payment state machine (SOW 3.1) -------------------------------
# FREE -> PENDING -> PREMIUM | REJECTED, plus PREMIUM -> REFUNDED.
# Enforced server-side; the UI is not the gate. This is the default vocabulary;
# a tenant may declare its own in cms_config.payment_queue.states.
PAYMENT_TRANSITIONS = DEFAULT_QUEUE.transitions

REJECT_REASON_CODES = ('wrong_amount', 'unreadable', 'duplicate_reference', 'other')
REFUND_REASON_CODES = ('duplicate_payment', 'user_request', 'chargeback', 'other')

SLA_HOURS = 6            # approval SLA the client manages against
SLA_WARN_HOURS = 4.5     # "approaching" threshold that fires the alert


def _as(expr, canonical):
    """Aliases a tenant column to the canonical key callers expect.

    Emits the bare expression when the tenant already uses that name, which keeps
    the default (Ministry) SQL identical to the pre-config build.
    """
    return expr if expr.split('.')[-1] == canonical else f'{expr} AS {canonical}'


def _sla_age(created_at, status, queue=DEFAULT_QUEUE):
    """Returns (age_hours, state) where state is ok | warn | breach | done."""
    if not isinstance(created_at, datetime):
        return None, 'unknown'
    if not queue.is_open(status):
        return None, 'done'
    now = datetime.now(created_at.tzinfo or UTC)
    hours = (now - created_at).total_seconds() / 3600.0
    if hours >= SLA_HOURS:
        state = 'breach'
    elif hours >= SLA_WARN_HOURS:
        state = 'warn'
    else:
        state = 'ok'
    return round(hours, 1), state


class PaymentMixin:
    # ---------------------------------------------------------
    # TRANSACTION MANAGEMENT
    # ---------------------------------------------------------
    def create_transaction(self, account_id, app_id, amount, currency, description, target_role="premium_user",
                           duration="1m", client_ref_id=None, app_name=None):
        """Creates a pending transaction record."""
        tx_id = f"tx-{secrets.token_hex(8)}"

        # Handle account_id being None (for pre-login intents)
        acc_oid = ObjectId(account_id) if account_id else None

        tx_doc = {
            "transaction_id": tx_id,
            "account_id": acc_oid,
            "app_id": ObjectId(app_id),
            "app_name": app_name,
            "amount": amount,
            "currency": currency,
            "description": description,
            "status": "pending",
            "target_role": target_role,
            "duration": duration,
            "client_ref_id": client_ref_id,
            "created_at": datetime.now(UTC),
            "updated_at": datetime.now(UTC),
            "provider_ref": None
        }
        self.db.transactions.insert_one(tx_doc)
        return tx_id

    def get_transaction(self, transaction_id):
        return self.db.transactions.find_one({"transaction_id": transaction_id})

    def complete_transaction(self, transaction_id, provider_ref=None):
        """
        Marks a transaction as completed and grants the role.
        STRICT MODE: Writes ONLY to 'app_specific_role'.
        Legacy 'role' field is completely deprecated.
        """
        tx = self.db.transactions.find_one({"transaction_id": transaction_id})
        if not tx:
            return False, "Transaction not found"

        if tx['status'] == 'completed':
            return True, "Already completed"

        # 1. Update Transaction Status
        self.db.transactions.update_one(
            {"_id": tx['_id']},
            {
                "$set": {
                    "status": "completed",
                    "provider_ref": provider_ref,
                    "updated_at": datetime.now(UTC)
                }
            }
        )

        # 2. Calculate Expiration
        duration = tx.get('duration')
        expires_at = None
        if duration:
            now = datetime.now(UTC)
            if duration == '1m':
                expires_at = now + timedelta(days=30)
            elif duration == '3m':
                expires_at = now + timedelta(days=90)
            elif duration == '6m':
                expires_at = now + timedelta(days=180)
            elif duration == '1y':
                expires_at = now + timedelta(days=365)
            elif duration == 'lifetime':
                expires_at = None

        # 3. Grant Role (STRICT)
        # We exclusively use 'app_specific_role' for ALL apps.
        target_role = tx.get('target_role', 'premium_user')

        update_doc = {
            "app_specific_role": target_role,
            "role": target_role,  # Legacy support
            "last_login": datetime.now(UTC)
        }

        if expires_at:
            update_doc["expires_at"] = expires_at
        elif duration == 'lifetime':
            # Explicitly clear expiration for lifetime
            update_doc["expires_at"] = None

        # 4. Perform Update
        self.db.app_links.update_one(
            {
                "account_id": tx['account_id'],
                "app_id": tx['app_id']
            },
            {
                "$set": update_doc,
                "$setOnInsert": {"linked_at": datetime.now(UTC)},
                "$unset": {"warning_sent": ""}  # Clear warning flag on upgrade
            },
            upsert=True
        )

        log.info(f"✅ Transaction {transaction_id} completed. Granted '{target_role}' to {tx['account_id']} in app_specific_role.")

        # --- FIX: Trigger Webhook automatically on completion ---
        self._trigger_event_for_user(
            account_id=tx['account_id'],
            event_type="subscription_success",
            specific_app_id=tx['app_id'],
            extra_data={
                "transaction_id": transaction_id,
                "amount": tx.get('amount'),
                "currency": tx.get('currency', 'USD'),
                "role": target_role,
                "duration": duration,
                "expires_at": expires_at.isoformat() if expires_at else None
            }
        )

        # 5. Return Data for general processing
        return True, {
            "account_id": str(tx['account_id']),
            "app_id": str(tx['app_id']),
            "role": target_role,
            "expires_at": expires_at.isoformat() if expires_at else None,
            "duration": duration
        }

    def save_pending_payment(self, trx_id, amount, currency, raw_text, payer_name):
        try:
            if self.db.payment_logs.find_one({"trx_id": trx_id}):
                return False

            self.db.payment_logs.insert_one({
                "trx_id": trx_id,
                "amount": float(amount),
                "currency": currency,
                "payer_name": payer_name,
                "raw_text": raw_text,
                "status": "unclaimed",
                "claimed_by_account_id": None,
                "created_at": datetime.now(UTC)
            })
            return True
        except Exception as e:
            log.error(f"Error saving payment log: {e}")
            return False

    def claim_payment(self, trx_input, app_id, user_identity):
        # 1. Resolve User, within the claiming app's own directory — a claim must
        #    never land on a same-email account belonging to another tenant.
        app_doc = self.db.applications.find_one({"_id": ObjectId(app_id)}) or {}
        directory = self.directory_scope(app_doc) if app_doc else None

        user = None
        if 'account_id' in user_identity:
            user = self.find_account_by_id(user_identity['account_id'])
        elif 'telegram_id' in user_identity:
            user = self.find_account_by_telegram(user_identity['telegram_id'], directory)
        elif 'email' in user_identity:
            user = self.find_account_by_email(user_identity['email'], directory)

        if not user:
            return False, "User account not found."

        # 2. Fuzzy Match Payment
        safe_input = str(trx_input).strip()
        regex_pattern = f"{safe_input}$"

        payment = self.db.payment_logs.find_one({
            "status": "unclaimed",
            "trx_id": {"$regex": regex_pattern}
        })

        if not payment:
            return False, "Transaction ID not found or already claimed."

        # 3. Atomic Claim
        result = self.db.payment_logs.update_one(
            {"_id": payment['_id'], "status": "unclaimed"},
            {
                "$set": {
                    "status": "claimed",
                    "claimed_by_account_id": user['_id'],
                    "claimed_for_app_id": ObjectId(app_id),
                    "claimed_method": list(user_identity.keys())[0],
                    "claimed_at": datetime.now(UTC)
                }
            }
        )

        if result.modified_count == 0:
            return False, "Error: Payment claimed by someone else."

        # 4. Grant Premium Role (Claims currently default to 1 Month if not specified)
        self.link_user_to_app(user['_id'], app_id, role="premium_user", suppress_webhook=True)

        # 5. Send Success Webhook for Claims
        self._trigger_event_for_user(
            account_id=user['_id'],
            event_type="subscription_success",
            specific_app_id=app_id,
            extra_data={
                "transaction_id": payment['trx_id'],
                "amount": payment['amount'],
                "currency": payment['currency'],
                "role": "premium_user",
                "method": "claim"
            }
        )

        return True, f"Success! ${payment['amount']} claimed."

    # ---------------------------------------------------------
    # MULTI-TENANT POSTGRESQL PROXIED MANUAL PAYMENTS
    #
    # Table and column names come from the app's cms_config.payment_queue block
    # (see queue_schema.py). An app with no block gets QueueSchema defaults, which
    # are Ministry Exam Prep's shape and emit byte-identical SQL to the build that
    # hardcoded them.
    #
    # Every method takes `queue` last so existing callers keep working; routes build
    # one schema per request and pass it down. Rows come back keyed by CANONICAL
    # names (id, user_id, txn_ref, ...) whatever the tenant calls its columns, so
    # templates and webhook payloads never see tenant vocabulary.
    # ---------------------------------------------------------
    def get_manual_payments(self, db_conn_str, status_filter=None, queue=DEFAULT_QUEUE):
        """Fetches manual payments from the tenant's PostgreSQL database.

        Adds derived SLA fields (age_hours, sla_state) so the queue shows the real
        age of a receipt against the 6h approval SLA.
        """
        from bifrost.utils.tenant_db import get_tenant_db
        from decimal import Decimal
        q = queue
        with get_tenant_db(db_conn_str) as conn:
            with conn.cursor() as cur:
                cols = _table_columns(cur, q.table)
                created = (_as(f'p.{q.created}', 'created_at') if q.created in cols
                           else 'NULL::timestamptz AS created_at')
                track = (_as(f'p.{q.scope}', 'exam_track_id') if q.scope in cols
                         else 'NULL::int AS exam_track_id')
                selected = ', '.join([
                    _as(f'p.{q.id}', 'id'), _as(f'p.{q.subject_key}', 'user_id'),
                    _as(f'p.{q.amount}', 'amount'), _as(f'p.{q.reference}', 'txn_ref'),
                    _as(f'p.{q.receipt}', 'receipt_url'), _as(f'p.{q.status}', 'status'),
                    _as(f'p.{q.reviewed_at}', 'reviewed_at'), _as(f'p.{q.reviewed_by}', 'reviewed_by'),
                    created, track, _as(f'u.{q.subject.label}', 'email'),
                ])
                sql = f"""
                SELECT {selected}
                FROM {q.table} p
                LEFT JOIN {q.subject.table} u ON p.{q.subject_key} = u.{q.subject.id}
                """
                params = []
                if isinstance(status_filter, (list, tuple, set)):
                    statuses = list(status_filter)
                    sql += f" WHERE p.{q.status} IN ({', '.join(['%s'] * len(statuses))})"
                    params.extend(statuses)
                elif status_filter:
                    sql += f" WHERE p.{q.status} = %s"
                    params.append(status_filter)
                sql += f" ORDER BY p.{q.id} DESC"

                cur.execute(sql, params)
                columns = [desc[0] for desc in cur.description]
                results = []
                for row in cur.fetchall():
                    d = dict(zip(columns, row))
                    for k, v in d.items():
                        if isinstance(v, Decimal):
                            d[k] = float(v)
                    d['age_hours'], d['sla_state'] = _sla_age(d.get('created_at'), d.get('status'), q)
                    if isinstance(d.get('created_at'), datetime):
                        d['created_at'] = d['created_at'].isoformat()
                    if isinstance(d.get('reviewed_at'), datetime):
                        d['reviewed_at'] = d['reviewed_at'].isoformat()
                    results.append(d)
                return results

    def get_active_tracks(self, db_conn_str, queue=DEFAULT_QUEUE):
        """Options for the approve dropdown. Rows, never hard-coded options.

        A queue with no scope_options (nothing to pick when approving) returns [].
        """
        from bifrost.utils.tenant_db import get_tenant_db
        opts = queue.scope_options
        if not opts:
            return []
        with get_tenant_db(db_conn_str) as conn:
            with conn.cursor() as cur:
                cols = _table_columns(cur, opts.table)
                if not cols:
                    return []
                name = opts.label if opts.label in cols else 'name'
                group = f'{opts.group}' if opts.group in cols else "NULL"
                where = f" WHERE {opts.active}" if opts.active in cols else ""
                cur.execute(
                    f'SELECT {opts.id}, {group}, "{name}" FROM {opts.table}{where} ORDER BY {opts.id}'
                )
                return [{"id": r[0], "ministry": r[1], "name": r[2]} for r in cur.fetchall()]

    def get_manual_payment_by_id(self, db_conn_str, payment_id, queue=DEFAULT_QUEUE):
        """Fetches a single manual payment from the tenant's PostgreSQL database."""
        from bifrost.utils.tenant_db import get_tenant_db
        from decimal import Decimal
        q = queue
        selected = ', '.join([
            _as(f'p.{q.id}', 'id'), _as(f'p.{q.subject_key}', 'user_id'),
            _as(f'p.{q.amount}', 'amount'), _as(f'p.{q.reference}', 'txn_ref'),
            _as(f'p.{q.receipt}', 'receipt_url'), _as(f'p.{q.status}', 'status'),
            _as(f'p.{q.reviewed_at}', 'reviewed_at'), _as(f'u.{q.subject.label}', 'email'),
        ])
        sql = f"""
        SELECT {selected}
        FROM {q.table} p
        LEFT JOIN {q.subject.table} u ON p.{q.subject_key} = u.{q.subject.id}
        WHERE p.{q.id} = %s
        """
        with get_tenant_db(db_conn_str) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, [payment_id])
                row = cur.fetchone()
                if row:
                    columns = [desc[0] for desc in cur.description]
                    d = dict(zip(columns, row))
                    for k, v in d.items():
                        if isinstance(v, Decimal):
                            d[k] = float(v)
                    return d
                return None

    def _lock_payment(self, cur, payment_id, target_status, queue=DEFAULT_QUEUE):
        """SELECT ... FOR UPDATE + state-machine check. Returns (payment_dict, error_dict)."""
        q = queue
        cols = _table_columns(cur, q.table)
        track = (_as(q.scope, 'exam_track_id') if q.scope in cols
                 else 'NULL::int AS exam_track_id')
        checksum = (_as(q.checksum, 'receipt_checksum') if q.checksum in cols
                    else 'NULL::text AS receipt_checksum')
        selected = ', '.join([
            _as(q.id, 'id'), _as(q.subject_key, 'user_id'), _as(q.amount, 'amount'),
            _as(q.reference, 'txn_ref'), _as(q.receipt, 'receipt_url'), _as(q.status, 'status'),
            track, checksum,
        ])
        cur.execute(
            f"SELECT {selected} "
            f"FROM {q.table} WHERE {q.id} = %s FOR UPDATE",
            [payment_id]
        )
        row = cur.fetchone()
        if not row:
            return None, {"error": "not_found", "message": "Payment record not found."}
        payment = dict(zip([d[0] for d in cur.description], row))
        current = (payment.get('status') or '').lower()
        if target_status not in q.transitions.get(current, set()):
            return payment, {
                "error": "invalid_transition",
                "message": f"Cannot move payment from '{current}' to '{target_status}'.",
            }
        return payment, None

    def find_duplicate_txn_ref(self, db_conn_str, txn_ref, exclude_payment_id=None, queue=DEFAULT_QUEUE):
        """Returns the id of an already-settled payment sharing this txn_ref, else None."""
        from bifrost.utils.tenant_db import get_tenant_db
        with get_tenant_db(db_conn_str) as conn:
            with conn.cursor() as cur:
                return self._find_duplicate_txn_ref(cur, txn_ref, exclude_payment_id, queue)

    def _find_duplicate_txn_ref(self, cur, txn_ref, exclude_payment_id=None, queue=DEFAULT_QUEUE):
        if not txn_ref:
            return None
        q = queue
        cur.execute(
            f"SELECT {q.id} FROM {q.table} WHERE {q.reference} = %s AND {q.id} <> %s "
            f"AND {q.status} IN ({q.settled_sql()}) LIMIT 1",
            [txn_ref, exclude_payment_id or -1]
        )
        row = cur.fetchone()
        return row[0] if row else None

    def find_duplicate_receipt(self, db_conn_str, payment, queue=DEFAULT_QUEUE):
        """Warn-only: another payment carrying the same receipt image.

        Matches on the checksum column when the tenant schema has it (their uploader
        computes it), otherwise falls back to an exact receipt-URL match.
        """
        from bifrost.utils.tenant_db import get_tenant_db
        q = queue
        with get_tenant_db(db_conn_str) as conn:
            with conn.cursor() as cur:
                cols = _table_columns(cur, q.table)
                if q.checksum in cols and payment.get('receipt_checksum'):
                    field, value = q.checksum, payment['receipt_checksum']
                elif payment.get('receipt_url'):
                    field, value = q.receipt, payment['receipt_url']
                else:
                    return None
                cur.execute(
                    f'SELECT {q.id} FROM {q.table} WHERE "{field}" = %s AND {q.id} <> %s LIMIT 1',
                    [value, payment.get('id') or -1]
                )
                row = cur.fetchone()
                return row[0] if row else None

    def approve_manual_payment(self, db_conn_str, payment_id, track_id, reviewer_id, app_id=None,
                               queue=DEFAULT_QUEUE):
        """Approves a payment and activates the grant in ONE transaction.

        Returns (True, payment) or (False, error_dict). A payment is never marked
        approved unless the matching grant write commits with it.

        A queue with no `grant` configured settles the payment and stops there — the
        tenant's app is told over the webhook and owns whatever "granting" means for
        it (stock, fulfilment, partial refunds). That logic does not belong here.
        """
        from bifrost.utils.tenant_db import get_tenant_db
        q = queue
        if q.grant and not track_id:
            return False, {"error": "track_required", "message": "An exam track must be selected."}

        with get_tenant_db(db_conn_str) as conn:
            with conn.cursor() as cur:
                payment, err = self._lock_payment(cur, payment_id, q.status_for('approve'), q)
                if err:
                    return False, err

                dup = self._find_duplicate_txn_ref(cur, payment.get('txn_ref'), payment_id, q)
                if dup:
                    return False, {
                        "error": "duplicate_txn_ref",
                        "duplicate_of": dup,
                        "message": f"Transaction reference already settled on payment #{dup}.",
                    }

                cols = _table_columns(cur, q.table)
                sets = [f"{q.status} = '{q.status_for('approve')}'",
                        f"{q.reviewed_by} = %s", f"{q.reviewed_at} = NOW()"]
                params = [reviewer_id]
                if q.scope in cols and track_id is not None:
                    sets.append(f"{q.scope} = %s")
                    params.append(track_id)
                cur.execute(
                    f"UPDATE {q.table} SET {', '.join(sets)} WHERE {q.id} = %s",
                    params + [payment_id]
                )

                if q.grant:
                    g = q.grant
                    # Upsert without depending on a unique constraint we don't own yet.
                    cur.execute(
                        f"UPDATE {g.table} SET {g.status} = '{g.on_approve}', {g.activated_at} = NOW() "
                        f"WHERE {g.subject_key} = %s AND {g.scope_key} = %s",
                        [payment['user_id'], track_id]
                    )
                    if cur.rowcount == 0:
                        cur.execute(
                            f"INSERT INTO {g.table} "
                            f"({g.subject_key}, {g.scope_key}, {g.status}, {g.activated_at}) "
                            f"VALUES (%s, %s, '{g.on_approve}', NOW())",
                            [payment['user_id'], track_id]
                        )
                conn.commit()

        payment['exam_track_id'] = track_id
        self.log_audit(app_id, q.table, 'APPROVE', payment_id, reviewer_id,
                       before={"status": payment.get('status')},
                       after={"status": q.status_for('approve'), "exam_track_id": track_id,
                              "user_id": payment.get('user_id')})
        return True, payment

    def reject_manual_payment(self, db_conn_str, payment_id, reviewer_id, reason_code, reason_text=None,
                              app_id=None, queue=DEFAULT_QUEUE):
        """Rejects a pending payment. Reason code is mandatory and validated."""
        from bifrost.utils.tenant_db import get_tenant_db
        if reason_code not in REJECT_REASON_CODES:
            return False, {"error": "bad_reason", "message": "A valid rejection reason code is required."}
        reason = reason_code if not reason_text else f"{reason_code}: {reason_text}"
        q = queue
        rejected = q.status_for('reject')

        with get_tenant_db(db_conn_str) as conn:
            with conn.cursor() as cur:
                payment, err = self._lock_payment(cur, payment_id, rejected, q)
                if err:
                    return False, err

                cols = _table_columns(cur, q.table)
                sets = [f"{q.status} = '{rejected}'", f"{q.reviewed_by} = %s", f"{q.reviewed_at} = NOW()"]
                params = [reviewer_id]
                for candidate in q.reject_reason:
                    if candidate in cols:
                        sets.append(f'"{candidate}" = %s')
                        params.append(reason)
                        break
                cur.execute(f"UPDATE {q.table} SET {', '.join(sets)} WHERE {q.id} = %s",
                            params + [payment_id])
                conn.commit()

        self.log_audit(app_id, q.table, 'REJECT', payment_id, reviewer_id,
                       before={"status": payment.get('status')},
                       after={"status": rejected, "reason": reason})
        return True, payment

    def refund_manual_payment(self, db_conn_str, payment_id, reviewer_id, reason_code, reason_text=None,
                              app_id=None, queue=DEFAULT_QUEUE):
        """Refunds an approved payment and revokes THAT payment's grant.

        The scope is derived from the payment itself, never from the form — refunding
        the wrong track was a named defect in the prior build.

        Refunds are all-or-nothing by design. A tenant whose refunds are partial (one
        line item of five) must not configure a `grant`: settle the payment here and
        let the webhook hand the decision back to their app.
        """
        from bifrost.utils.tenant_db import get_tenant_db
        if reason_code not in REFUND_REASON_CODES:
            return False, {"error": "bad_reason", "message": "A valid refund reason code is required."}
        reason = reason_code if not reason_text else f"{reason_code}: {reason_text}"
        q = queue
        refunded = q.status_for('refund')

        with get_tenant_db(db_conn_str) as conn:
            with conn.cursor() as cur:
                payment, err = self._lock_payment(cur, payment_id, refunded, q)
                if err:
                    return False, err

                track_id = payment.get('exam_track_id')
                if q.grant and not track_id:
                    g = q.grant
                    # Legacy rows predate the scope column: fall back to the payer's
                    # single active grant, and refuse to guess when ambiguous.
                    cur.execute(
                        f"SELECT {g.scope_key} FROM {g.table} "
                        f"WHERE {g.subject_key} = %s AND {g.status} = '{g.on_approve}'",
                        [payment['user_id']]
                    )
                    active = [r[0] for r in cur.fetchall()]
                    if len(active) != 1:
                        return False, {
                            "error": "ambiguous_track",
                            "message": ("Cannot determine which exam track this payment unlocked "
                                        f"({len(active)} active entitlements). Resolve manually."),
                        }
                    track_id = active[0]

                cols = _table_columns(cur, q.table)
                sets = [f"{q.status} = '{refunded}'", f"{q.reviewed_by} = %s", f"{q.reviewed_at} = NOW()"]
                params = [reviewer_id]
                for candidate in q.refund_reason:
                    if candidate in cols:
                        sets.append(f'"{candidate}" = %s')
                        params.append(reason)
                        break
                cur.execute(f"UPDATE {q.table} SET {', '.join(sets)} WHERE {q.id} = %s",
                            params + [payment_id])

                if q.grant:
                    g = q.grant
                    cur.execute(
                        f"UPDATE {g.table} SET {g.status} = '{g.on_revoke}' "
                        f"WHERE {g.subject_key} = %s AND {g.scope_key} = %s",
                        [payment['user_id'], track_id]
                    )
                conn.commit()

        payment['exam_track_id'] = track_id
        self.log_audit(app_id, q.table, 'REFUND', payment_id, reviewer_id,
                       before={"status": payment.get('status'), "exam_track_id": track_id},
                       after={"status": refunded, "exam_track_id": track_id, "reason": reason})
        return True, payment

    def suspend_tenant_user(self, db_conn_str, user_id, reason, actor=None, app_id=None,
                            queue=DEFAULT_QUEUE):
        """Suspends a user in the tenant DB.

        Deliberately does NOT touch grants: suspension is reversible and a reinstated
        user must get their paid access back. Access is gated on the subject's status
        column, so revoking here would silently destroy a purchase that reinstate
        cannot restore.
        """
        from bifrost.utils.tenant_db import get_tenant_db
        s = queue.subject
        with get_tenant_db(db_conn_str) as conn:
            with conn.cursor() as cur:
                cols = _table_columns(cur, s.table)
                if s.status not in cols:
                    raise ValueError(f"{s.table}.{s.status} column is required to suspend accounts.")
                sets, params = [f"{s.status} = 'suspended'"], []
                if s.suspended_at in cols:
                    sets.append(f"{s.suspended_at} = NOW()")
                if s.suspend_reason in cols:
                    sets.append(f"{s.suspend_reason} = %s")
                    params.append(reason)
                cur.execute(f"UPDATE {s.table} SET {', '.join(sets)} WHERE {s.id} = %s",
                            params + [user_id])
                conn.commit()

        self.log_audit(app_id, s.table, 'SUSPEND', user_id, actor,
                       before={"status": "active"}, after={"status": "suspended", "reason": reason})
        return True

    def reinstate_tenant_user(self, db_conn_str, user_id, actor=None, app_id=None, queue=DEFAULT_QUEUE):
        """Reinstates a user in the tenant DB. Grants are untouched by design."""
        from bifrost.utils.tenant_db import get_tenant_db
        s = queue.subject
        with get_tenant_db(db_conn_str) as conn:
            with conn.cursor() as cur:
                cols = _table_columns(cur, s.table)
                if s.status not in cols:
                    raise ValueError(f"{s.table}.{s.status} column is required to reinstate accounts.")
                sets = [f"{s.status} = 'active'"]
                if s.suspended_at in cols:
                    sets.append(f"{s.suspended_at} = NULL")
                cur.execute(f"UPDATE {s.table} SET {', '.join(sets)} WHERE {s.id} = %s", [user_id])
                conn.commit()

        self.log_audit(app_id, s.table, 'REINSTATE', user_id, actor,
                       before={"status": "suspended"}, after={"status": "active"})
        return True

    def set_entitlement(self, db_conn_str, user_id, track_id, status, actor=None, app_id=None,
                        queue=DEFAULT_QUEUE):
        """Manual grant override for support cases (SOW 3.5)."""
        from bifrost.utils.tenant_db import get_tenant_db
        g = queue.grant
        if not g:
            raise ValueError("This app's payment queue has no grant configured to override.")
        if status not in g.statuses:
            raise ValueError(f"Invalid entitlement status: {status}")
        with get_tenant_db(db_conn_str) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT {g.status} FROM {g.table} "
                    f"WHERE {g.subject_key} = %s AND {g.scope_key} = %s FOR UPDATE",
                    [user_id, track_id]
                )
                row = cur.fetchone()
                before = row[0] if row else None
                if row:
                    cur.execute(
                        f"UPDATE {g.table} SET {g.status} = %s, {g.activated_at} = NOW() "
                        f"WHERE {g.subject_key} = %s AND {g.scope_key} = %s",
                        [status, user_id, track_id]
                    )
                else:
                    cur.execute(
                        f"INSERT INTO {g.table} "
                        f"({g.subject_key}, {g.scope_key}, {g.status}, {g.activated_at}) "
                        f"VALUES (%s, %s, %s, NOW())",
                        [user_id, track_id, status]
                    )
                conn.commit()

        self.log_audit(app_id, g.table, 'OVERRIDE', f"{user_id}:{track_id}", actor,
                       before={"status": before}, after={"status": status})
        return True

    def validate_question_publishable(self, db_conn_str, question_id):
        """Publish-time MCQ validation (SOW 3.2). Returns a list of blocking reasons.

        Rules, in the client's words: exactly 4 choices, exactly 1 correct, explanation
        in both languages, non-empty source_ref. The bilingual-explanation rule is
        applied to the correct choice — that is the text the student is shown.
        """
        from bifrost.utils.tenant_db import get_tenant_db
        errors = []
        with get_tenant_db(db_conn_str) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT source_ref FROM questions WHERE id = %s", [question_id])
                row = cur.fetchone()
                if not row:
                    return ["Question not found."]
                if not (row[0] or '').strip():
                    errors.append("source_ref is empty — every published question must trace to a source document.")

                cur.execute(
                    "SELECT id, is_correct, explanation_kh, explanation_en "
                    "FROM choices WHERE question_id = %s ORDER BY id",
                    [question_id]
                )
                choices = cur.fetchall()

        if len(choices) != 4:
            errors.append(f"Question has {len(choices)} choices — exactly 4 are required.")
        correct = [c for c in choices if c[1]]
        if len(correct) != 1:
            errors.append(f"Question has {len(correct)} correct choices — exactly 1 is required.")
        for c in correct:
            if not (c[2] or '').strip():
                errors.append("Correct choice is missing its Khmer explanation.")
            if not (c[3] or '').strip():
                errors.append("Correct choice is missing its English explanation.")
        return errors

    def get_tenant_tables(self, db_conn_str):
        """Fetches the tenant's tables from whichever schema its connection is pinned to."""
        return _memoized(('tables', db_conn_str),
                         lambda: self._get_tenant_tables(db_conn_str))

    def _get_tenant_tables(self, db_conn_str):
        if cms_mongo.handles(db_conn_str):
            return cms_mongo.get_tenant_tables(db_conn_str)
        from bifrost.utils.tenant_db import get_tenant_db
        sql = """
            SELECT table_name FROM information_schema.tables
            WHERE table_schema=current_schema() AND table_type='BASE TABLE'
            ORDER BY table_name
        """
        with get_tenant_db(db_conn_str) as conn:
            with conn.cursor() as cur:
                cur.execute(sql)
                return [row[0] for row in cur.fetchall()]

    def get_tenant_table_schema(self, db_conn_str, table_name):
        """Returns column metadata: name, data_type, nullable, char_max_length, fk info."""
        return _memoized(('schema', db_conn_str, table_name),
                         lambda: self._get_tenant_table_schema(db_conn_str, table_name))

    def _get_tenant_table_schema(self, db_conn_str, table_name):
        if cms_mongo.handles(db_conn_str):
            return cms_mongo.get_tenant_table_schema(db_conn_str, table_name)
        from bifrost.utils.tenant_db import get_tenant_db
        sql = """
            SELECT
                c.column_name,
                c.data_type,
                c.udt_name,
                c.is_nullable,
                c.character_maximum_length,
                c.numeric_precision,
                fk.foreign_table,
                fk.foreign_column
            FROM information_schema.columns c
            LEFT JOIN (
                SELECT
                    kcu.column_name,
                    ccu.table_name AS foreign_table,
                    ccu.column_name AS foreign_column
                FROM information_schema.table_constraints tc
                JOIN information_schema.key_column_usage kcu 
                    ON tc.constraint_name = kcu.constraint_name
                JOIN information_schema.constraint_column_usage ccu 
                    ON ccu.constraint_name = tc.constraint_name
                WHERE tc.constraint_type = 'FOREIGN KEY'
                  AND tc.table_name = %s
            ) fk ON c.column_name = fk.column_name
            WHERE c.table_schema = current_schema() AND c.table_name = %s
            ORDER BY c.ordinal_position
        """
        with get_tenant_db(db_conn_str) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, [table_name, table_name])
                keys = [d[0] for d in cur.description]
                return [dict(zip(keys, r)) for r in cur.fetchall()]

    def get_tenant_table_data(self, db_conn_str, table_name, limit=50, offset=0, sort_by='id', sort_dir='desc', search_query=None):
        """Fetches rows for a target table with pagination, sorting, and optional search."""
        if cms_mongo.handles(db_conn_str):
            return cms_mongo.get_tenant_table_data(db_conn_str, table_name, limit, offset,
                                                   sort_by, sort_dir, search_query)
        from bifrost.utils.tenant_db import get_tenant_db
        from decimal import Decimal
        # Identifiers are validated against the introspected schema; never interpolated raw.
        safe_ident(table_name)
        sort_dir = 'ASC' if sort_dir.lower() == 'asc' else 'DESC'

        # Also validate sort_by against valid columns (fetch schema first)
        schema = self.get_tenant_table_schema(db_conn_str, table_name)
        valid_columns = [col['column_name'] for col in schema]
        if sort_by not in valid_columns:
            sort_by = valid_columns[0] if valid_columns else 'id'

        params = []
        where_clause = ""
        
        # If searching, we cast all text-like columns to text and check ILIKE
        if search_query:
            text_columns = [col['column_name'] for col in schema if col['data_type'] in ('character varying', 'text', 'character')]
            if text_columns:
                search_conditions = []
                for col in text_columns:
                    search_conditions.append(f'"{col}" ILIKE %s')
                    params.append(f'%{search_query}%')
                where_clause = "WHERE " + " OR ".join(search_conditions)

        sql = f'SELECT * FROM "{table_name}" {where_clause} ORDER BY "{sort_by}" {sort_dir} LIMIT %s OFFSET %s'
        params.extend([limit, offset])

        # We also want to get the total count for pagination
        count_sql = f'SELECT COUNT(*) FROM "{table_name}" {where_clause}'
        count_params = params[:-2]

        with get_tenant_db(db_conn_str) as conn:
            with conn.cursor() as cur:
                cur.execute(count_sql, count_params)
                total_count = cur.fetchone()[0]

                cur.execute(sql, params)
                columns = [desc[0] for desc in cur.description]
                results = []
                import json
                for row in cur.fetchall():
                    d = dict(zip(columns, row))
                    for k, v in d.items():
                        if isinstance(v, Decimal):
                            d[k] = float(v)
                        elif isinstance(v, datetime):
                            d[k] = v.isoformat()
                        elif isinstance(v, (dict, list)):
                            d[k] = json.dumps(v)
                    results.append(d)
                return columns, results, total_count

    def get_distinct_column_values(self, db_conn_str, table_name, column_name):
        """Returns distinct values for a column — used to build enum selects."""
        if cms_mongo.handles(db_conn_str):
            return cms_mongo.get_distinct_column_values(db_conn_str, table_name, column_name)
        from bifrost.utils.tenant_db import get_tenant_db
        safe_ident(table_name)
        safe_ident(column_name)
        sql = f'SELECT DISTINCT "{column_name}" FROM "{table_name}" WHERE "{column_name}" IS NOT NULL LIMIT 50'
        with get_tenant_db(db_conn_str) as conn:
            with conn.cursor() as cur:
                cur.execute(sql)
                return [row[0] for row in cur.fetchall()]

    # ------------------------------------------------------------------
    # CMS CONFIG — per-app schema annotations stored in MongoDB
    # ------------------------------------------------------------------

    def get_cms_config(self, app_id):
        """Returns the CMS config for an app. Creates default if missing.

        Memoized per request: one grid load asked Atlas for the same document up
        to eight times — the route body, hidden_columns_for, and each permission
        check fetched it independently.
        """
        return _memoized(('cms_config', str(app_id)),
                         lambda: self._get_cms_config(app_id))

    def _get_cms_config(self, app_id):
        doc = self.db.applications.find_one(
            {"_id": ObjectId(app_id)},
            {"cms_config": 1}
        )
        return (doc or {}).get("cms_config", {})

    def save_cms_config(self, app_id, config):
        """Persists CMS config dict to MongoDB applications document."""
        # Drop the memo: a save followed by a read in the same request must not
        # see the pre-save document.
        cache = _request_cache()
        if cache is not None:
            cache.pop(('cms_config', str(app_id)), None)
        self.db.applications.update_one(
            {"_id": ObjectId(app_id)},
            {"$set": {"cms_config": config}}
        )
        return True

    def log_audit(self, app_id, table_name, action, row_id, acting_user, before=None, after=None):
        """Single audit sink for every console mutation (SOW 3.9).

        Content edits, payment transitions and user actions all land here with
        actor / table / row / before / after / timestamp. Retention is a minimum of
        one year — there is deliberately no TTL index on this collection.
        """
        from datetime import datetime, timezone
        self.db.cms_audit_log.insert_one({
            "app_id": str(app_id) if app_id else None,
            "table": table_name,
            "action": action,
            "row_id": row_id,
            "acting_user": acting_user,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "before": before,
            "after": after
        })

    # Legacy name kept so existing call sites keep working.
    log_cms_mutation = log_audit

    def get_audit_log(self, app_id, table=None, actor=None, limit=200):
        """Filterable audit timeline for the console (SOW 3.9)."""
        query = {"app_id": str(app_id)}
        if table:
            query["table"] = table
        if actor:
            query["acting_user"] = actor
        return list(self.db.cms_audit_log.find(query).sort("timestamp", -1).limit(int(limit)))

    def _review_queue_owns(self, app_id, table_name):
        """True when this table's review columns are the review queue's to write."""
        if not app_id:
            return False
        block = (self.get_cms_config(app_id) or {}).get('review_queue') or {}
        return block.get('table') == table_name

    def save_tenant_table_row(self, db_conn_str, table_name, row_id, data, app_id=None, acting_user=None):
        """Updates a row in the tenant database public schema."""
        if cms_mongo.handles(db_conn_str):
            before, after = cms_mongo.update_row(db_conn_str, table_name, row_id, data)
            if app_id and acting_user:
                self.log_cms_mutation(app_id, table_name, "UPDATE", str(row_id),
                                      acting_user, before, after)
            return True
        # NOT int(): content tables are commonly keyed by UUID, and the cast
        # raised ValueError, which the route caught and flashed as "Update
        # failed" — every grid save on a UUID-keyed table failed silently.
        # The id travels as a bound parameter; Postgres coerces it either way.
        from bifrost.utils.tenant_db import get_tenant_db
        safe_ident(table_name)

        schema = self.get_tenant_table_schema(db_conn_str, table_name)
        valid_columns = {col['column_name'] for col in schema}

        fields = []
        params = []
        for k, v in data.items():
            if k == 'id' or k in REVIEW_STAMP or k not in valid_columns:
                continue
            fields.append(f'"{k}" = %s')
            params.append(v if v != '' else None)

        # Attestation columns are stamped server-side from the session, never
        # from the client: a reviewer must not be able to sign as someone else.
        # A table worked through the review queue is exempt: there the stamp
        # belongs to the review decision, and stamping again on an unrelated
        # grid edit would overwrite who actually signed it.
        if acting_user and not self._review_queue_owns(app_id, table_name):
            for col, val in (('reviewed_by', acting_user),
                             ('reviewed_at', datetime.now(UTC))):
                if col in valid_columns:
                    fields.append(f'"{col}" = %s')
                    params.append(val)

        if not fields:
            return True

        params.append(row_id)
        sql = f'UPDATE "{table_name}" SET {", ".join(fields)} WHERE id = %s RETURNING *'

        with get_tenant_db(db_conn_str) as conn:
            with conn.cursor() as cur:
                # Fetch BEFORE state for logging
                cur.execute(f'SELECT * FROM "{table_name}" WHERE id = %s', [row_id])
                before_row = cur.fetchone()
                before_dict = dict(zip([desc[0] for desc in cur.description], before_row)) if before_row else None

                cur.execute(sql, params)
                after_row = cur.fetchone()
                after_dict = dict(zip([desc[0] for desc in cur.description], after_row)) if after_row else None
                conn.commit()

        # Decimal and datetime are not JSON serializable out of the box for MongoDB
        def sanitize_dict(d):
            if not d: return d
            from decimal import Decimal
            out = {}
            for k,v in d.items():
                if isinstance(v, Decimal): out[k] = float(v)
                elif isinstance(v, datetime): out[k] = v.isoformat()
                else: out[k] = v
            return out

        if app_id and acting_user:
            self.log_cms_mutation(app_id, table_name, "UPDATE", row_id, acting_user, sanitize_dict(before_dict), sanitize_dict(after_dict))
        return True

    def insert_tenant_table_row(self, db_conn_str, table_name, data, app_id=None, acting_user=None):
        """Inserts a new row in the tenant database public schema."""
        if cms_mongo.handles(db_conn_str):
            after = cms_mongo.insert_row(db_conn_str, table_name, data)
            if app_id and acting_user:
                self.log_cms_mutation(app_id, table_name, "CREATE", after.get('id'),
                                      acting_user, None, after)
            return True
        from bifrost.utils.tenant_db import get_tenant_db
        safe_ident(table_name)

        schema = self.get_tenant_table_schema(db_conn_str, table_name)
        valid_columns = {col['column_name'] for col in schema}

        cols, placeholders, params = [], [], []
        for k, v in data.items():
            if k == 'id' or k not in valid_columns:
                continue
            cols.append(f'"{k}"')
            placeholders.append('%s')
            params.append(v if v != '' else None)

        sql = f'INSERT INTO "{table_name}" ({", ".join(cols)}) VALUES ({", ".join(placeholders)}) RETURNING *'

        with get_tenant_db(db_conn_str) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                after_row = cur.fetchone()
                after_dict = dict(zip([desc[0] for desc in cur.description], after_row)) if after_row else None
                conn.commit()

        def sanitize_dict(d):
            if not d: return d
            from decimal import Decimal
            out = {}
            for k,v in d.items():
                if isinstance(v, Decimal): out[k] = float(v)
                elif isinstance(v, datetime): out[k] = v.isoformat()
                else: out[k] = v
            return out

        if app_id and acting_user:
            self.log_cms_mutation(app_id, table_name, "CREATE", after_dict.get('id') if after_dict else None, acting_user, None, sanitize_dict(after_dict))
        return True

    def delete_tenant_table_row(self, db_conn_str, table_name, row_id, app_id=None, acting_user=None):
        """Deletes a row from the tenant database public schema."""
        if cms_mongo.handles(db_conn_str):
            before = cms_mongo.delete_row(db_conn_str, table_name, row_id)
            if app_id and acting_user:
                self.log_cms_mutation(app_id, table_name, "DELETE", str(row_id),
                                      acting_user, before, None)
            return True
        from bifrost.utils.tenant_db import get_tenant_db  # id stays as given; see save_tenant_table_row
        safe_ident(table_name)

        with get_tenant_db(db_conn_str) as conn:
            with conn.cursor() as cur:
                cur.execute(f'SELECT * FROM "{table_name}" WHERE id = %s', [row_id])
                before_row = cur.fetchone()
                before_dict = dict(zip([desc[0] for desc in cur.description], before_row)) if before_row else None
                
                sql = f'DELETE FROM "{table_name}" WHERE id = %s'
                cur.execute(sql, [row_id])
                conn.commit()

        def sanitize_dict(d):
            if not d: return d
            from decimal import Decimal
            out = {}
            for k,v in d.items():
                if isinstance(v, Decimal): out[k] = float(v)
                elif isinstance(v, datetime): out[k] = v.isoformat()
                else: out[k] = v
            return out

        if app_id and acting_user:
            self.log_cms_mutation(app_id, table_name, "DELETE", row_id, acting_user, sanitize_dict(before_dict), None)
        return True