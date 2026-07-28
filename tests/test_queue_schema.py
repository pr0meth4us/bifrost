"""The payment queue against two tenants with nothing in common but shape.

test_console_phase1.py proves the money path is correct for Ministry Exam Prep and
is deliberately left untouched — if it ever needs editing, the abstraction changed
behaviour and that is a bug, not a test problem.

This file proves the same behaviour survives renaming. Every test runs twice: once
with QueueSchema defaults (Ministry: payments/users/entitlements/exam_track_id) and
once with a shop config that shares no identifier with it. Same assertions, same
expected outcomes.

The fake Postgres here is generic on purpose. It parses whatever identifiers the
statement actually used, so it cannot silently pass by hardcoding the names we are
trying to prove are configurable.

Run:  python -m unittest discover -s tests -v
"""
import re
import types
import unittest
from contextlib import contextmanager
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bifrost.models.payments import PaymentMixin  # noqa: E402
from bifrost.models.queue_schema import QueueSchema  # noqa: E402


# --------------------------------------------------------------------------
# The two tenants
# --------------------------------------------------------------------------

SHOP_CONFIG = {
    "payment_queue": {
        "table": "orders",
        "id": "order_id",
        "subject_key": "customer_id",
        "amount": "total",
        "reference": "ref",
        "receipt": "proof_url",
        "checksum": "proof_sha",
        "created": "placed_at",
        "status": "state",
        "reviewed_at": "checked_at",
        "reviewed_by": "checked_by",
        "scope": "plan_id",
        "reject_reason": ["decline_note"],
        "refund_reason": ["decline_note"],
        "open_states": ["awaiting_review"],
        "settled": ["paid", "refunded"],
        "transitions": {
            "awaiting_review": ["paid", "declined"],
            "paid": ["refunded"],
            "declined": [],
            "refunded": [],
        },
        "actions": {"approve": "paid", "reject": "declined", "refund": "refunded"},
        "subject": {"table": "customers", "id": "cid", "label": "contact_email",
                    "status": "state", "suspended_at": "held_at", "suspend_reason": "held_why"},
        "grant": {"table": "access_grants", "subject_key": "cid", "scope_key": "plan_id",
                  "status": "state", "activated_at": "granted_at",
                  "on_approve": "active", "on_revoke": "revoked",
                  "statuses": ["none", "pending", "active", "revoked"]},
        "scope_options": {"table": "plans", "id": "plan_id", "label": "title",
                          "group": "vendor", "active": "live"},
    }
}

MINISTRY = QueueSchema()
SHOP = QueueSchema.from_config(SHOP_CONFIG)


# --------------------------------------------------------------------------
# A fake Postgres that reads identifiers out of the SQL instead of assuming them.
# --------------------------------------------------------------------------

class FakeCursor:
    def __init__(self, state, log):
        self.state, self.log = state, log
        self.description, self.rowcount, self._rows = None, 0, []

    def _set(self, rows, columns):
        self._rows, self.rowcount = rows, len(rows)
        self.description = [(c,) for c in columns]

    def _rows_of(self, table):
        return self.state['tables'].setdefault(table, [])

    @staticmethod
    def _aliases(select_list):
        """[('total', 'amount'), ('NULL::int', 'exam_track_id'), ...] — source, key."""
        out = []
        for item in select_list.split(','):
            item = item.strip()
            if ' AS ' in item:
                src, key = item.split(' AS ')
            else:
                src = key = item
            # psycopg2 names an unaliased `p.id` column `id`, so drop the prefix on both.
            src = src.strip().split('.')[-1]
            out.append((src, key.strip().strip('"').split('.')[-1]))
        return out

    def _project(self, rows, select_list):
        pairs = self._aliases(select_list)
        self._set([tuple(None if src.startswith('NULL') else r.get(src) for src, _ in pairs)
                   for r in rows], [key for _, key in pairs])

    @staticmethod
    def _assignments(set_clause, params):
        """Column -> value for a SET clause, consuming %s params in order."""
        out, i = {}, 0
        for col, literal in re.findall(r'"?(\w+)"?\s*=\s*(\'[^\']*\'|NOW\(\)|NULL|%s)', set_clause):
            if literal == '%s':
                out[col], i = params[i], i + 1
            elif literal == 'NOW()':
                out[col] = 'now'
            elif literal == 'NULL':
                out[col] = None
            else:
                out[col] = literal.strip("'")
        return out, i

    def execute(self, sql, params=None):
        sql = ' '.join(sql.split())
        params = list(params or [])
        self.log.append((sql, params))
        self._rows, self.description, self.rowcount = [], None, 0

        if 'information_schema.columns' in sql:
            self._set([(c,) for c in self.state['columns'].get(params[0], [])], ['column_name'])
            return

        m = re.match(r'SELECT (.+?) FROM (\w+) WHERE (\w+) = %s FOR UPDATE$', sql)
        if m:
            select_list, table, key = m.groups()
            self._project([r for r in self._rows_of(table) if r.get(key) == params[0]], select_list)
            return

        # set_entitlement's lock: two-key lookup.
        m = re.match(r'SELECT (\w+) FROM (\w+) WHERE (\w+) = %s AND (\w+) = %s FOR UPDATE$', sql)
        if m:
            col, table, k1, k2 = m.groups()
            hits = [r for r in self._rows_of(table) if r.get(k1) == params[0] and r.get(k2) == params[1]]
            self._set([(r[col],) for r in hits], [col])
            return

        # Duplicate reference: settled statuses come from config, so match them loosely.
        m = re.match(r"SELECT (\w+) FROM (\w+) WHERE (\w+) = %s AND (\w+) <> %s "
                     r"AND (\w+) IN \((.+?)\) LIMIT 1$", sql)
        if m:
            idcol, table, refcol, _, statuscol, settled = m.groups()
            settled = [s.strip().strip("'") for s in settled.split(',')]
            hits = [r for r in self._rows_of(table)
                    if r.get(refcol) == params[0] and r.get(idcol) != params[1]
                    and r.get(statuscol) in settled]
            self._set([(hits[0][idcol],)] if hits else [], [idcol])
            return

        # Duplicate receipt.
        m = re.match(r'SELECT (\w+) FROM (\w+) WHERE "(\w+)" = %s AND (\w+) <> %s LIMIT 1$', sql)
        if m:
            idcol, table, field, _ = m.groups()
            hits = [r for r in self._rows_of(table)
                    if r.get(field) == params[0] and r.get(idcol) != params[1]]
            self._set([(hits[0][idcol],)] if hits else [], [idcol])
            return

        # Refund's legacy fallback: the payer's active grants.
        m = re.match(r"SELECT (\w+) FROM (\w+) WHERE (\w+) = %s AND (\w+) = '(\w+)'$", sql)
        if m:
            scope, table, subject_key, status_col, active = m.groups()
            hits = [r for r in self._rows_of(table)
                    if r.get(subject_key) == params[0] and r.get(status_col) == active]
            self._set([(r[scope],) for r in hits], [scope])
            return

        m = re.match(r'SELECT (.+?) FROM (\w+) p LEFT JOIN (\w+) u ON p\.(\w+) = u\.(\w+)'
                     r'(?: WHERE p\.(\w+) (?:= %s|IN \([%s, ]+\)))?(?: ORDER BY .+)?'
                     r'(?: WHERE p\.(\w+) = %s)?$', sql)
        if m:
            select_list, table, subject_table, fk, pk, where_in, where_eq = m.groups()
            rows = []
            for r in self._rows_of(table):
                subject = next((s for s in self._rows_of(subject_table) if s.get(pk) == r.get(fk)), {})
                rows.append({**subject, **r})
            status_col = where_in or where_eq
            if status_col:
                rows = [r for r in rows if r.get(status_col) in params]
            self._project(rows, select_list)
            return

        m = re.match(r'UPDATE (\w+) SET (.+?) WHERE (\w+) = %s AND (\w+) = %s$', sql)
        if m:
            table, set_clause, k1, k2 = m.groups()
            values, used = self._assignments(set_clause, params)
            key1, key2 = params[used], params[used + 1]
            hits = [r for r in self._rows_of(table) if r.get(k1) == key1 and r.get(k2) == key2]
            for r in hits:
                r.update(values)
            self.rowcount = len(hits)
            return

        m = re.match(r'UPDATE (\w+) SET (.+?) WHERE (\w+) = %s$', sql)
        if m:
            table, set_clause, key = m.groups()
            values, used = self._assignments(set_clause, params)
            hits = [r for r in self._rows_of(table) if r.get(key) == params[used]]
            for r in hits:
                r.update(values)
            self.rowcount = len(hits)
            return

        m = re.match(r'INSERT INTO (\w+) \((.+?)\) VALUES \((.+?)\)$', sql)
        if m:
            table, cols, values = m.groups()
            cols = [c.strip() for c in cols.split(',')]
            row, i = {}, 0
            for col, value in zip(cols, [v.strip() for v in values.split(',')]):
                if value == '%s':
                    row[col], i = params[i], i + 1
                elif value == 'NOW()':
                    row[col] = 'now'
                else:
                    row[col] = value.strip("'")
            self._rows_of(table).append(row)
            self.rowcount = 1
            return

        m = re.match(r'SELECT (\w+), (\w+|NULL), "(\w+)" FROM (\w+)(?: WHERE (\w+))? ORDER BY \w+$', sql)
        if m:
            idcol, group, label, table, active = m.groups()
            rows = [r for r in self._rows_of(table) if not active or r.get(active)]
            self._set([(r[idcol], r.get(group), r[label]) for r in rows], [idcol, group, label])
            return

        raise AssertionError(f"FakeCursor got an unexpected statement: {sql}")

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeConnection:
    def __init__(self, state, log):
        self.state, self.log, self.commits = state, log, 0

    def cursor(self):
        return FakeCursor(self.state, self.log)

    def commit(self):
        self.commits += 1


class FakeCollection:
    def __init__(self):
        self.docs = []

    def insert_one(self, doc):
        self.docs.append(doc)


class ConsoleDB(PaymentMixin):
    def __init__(self, state):
        self.state, self.log = state, []
        self.conn = FakeConnection(state, self.log)
        self.db = types.SimpleNamespace(cms_audit_log=FakeCollection())


def make_db(queue, payment_overrides=None, grants=None):
    """A one-payment tenant DB shaped by `queue`, whatever it calls its tables."""
    q, g, s, o = queue, queue.grant, queue.subject, queue.scope_options
    payment = {
        q.id: 1, q.subject_key: 77, q.amount: 5.0, q.reference: 'ABA123',
        q.receipt: 'r1.png', q.status: q.open_states[0], q.scope: None, q.checksum: None,
    }
    payment.update(payment_overrides or {})
    state = {
        'columns': {
            q.table: [q.id, q.subject_key, q.amount, q.reference, q.receipt, q.status,
                      q.scope, q.checksum, q.created, q.reviewed_by, q.reviewed_at,
                      q.reject_reason[0], q.refund_reason[0]],
            s.table: [s.id, s.label, s.status, s.suspended_at, s.suspend_reason],
            g.table: [g.subject_key, g.scope_key, g.status, g.activated_at],
            o.table: [o.id, o.label, o.group, o.active],
        },
        'tables': {
            q.table: [payment],
            s.table: [{s.id: 77, s.label: 'payer@example.com', s.status: 'active'}],
            g.table: list(grants or []),
            o.table: [{o.id: 42, o.label: 'Track 42', o.group: 'moeys', o.active: True},
                      {o.id: 99, o.label: 'Track 99', o.group: 'moeys', o.active: False}],
        },
    }
    db = ConsoleDB(state)

    @contextmanager
    def fake_get_tenant_db(conn_str):
        yield db.conn

    import bifrost.utils.tenant_db as tdb
    tdb.get_tenant_db = fake_get_tenant_db
    return db


def both_tenants(test):
    """Runs a test body against Ministry's defaults and the shop config."""
    def wrapper(self):
        for name, queue in (('ministry', MINISTRY), ('shop', SHOP)):
            with self.subTest(tenant=name):
                test(self, queue)
    return wrapper


# --------------------------------------------------------------------------
# The same money path, twice
# --------------------------------------------------------------------------

class TestApproveAcrossTenants(unittest.TestCase):
    @both_tenants
    def test_approve_grants_the_payer_the_chosen_scope(self, queue):
        db = make_db(queue)
        ok, _ = db.approve_manual_payment('dsn', 1, 42, 'admin-9', app_id='a', queue=queue)

        g = queue.grant
        self.assertTrue(ok)
        self.assertEqual(db.state['tables'][queue.table][0][queue.status],
                         queue.status_for('approve'))
        self.assertEqual(db.state['tables'][g.table],
                         [{g.subject_key: 77, g.scope_key: 42,
                           g.status: g.on_approve, g.activated_at: 'now'}])

    @both_tenants
    def test_approve_and_grant_share_one_commit(self, queue):
        db = make_db(queue)
        db.approve_manual_payment('dsn', 1, 42, 'admin-9', queue=queue)
        self.assertEqual(db.conn.commits, 1)

    @both_tenants
    def test_duplicate_reference_is_blocked_against_settled_rows(self, queue):
        q = queue
        db = make_db(queue)
        db.state['tables'][q.table] = [
            {q.id: 1, q.subject_key: 77, q.amount: 5.0, q.reference: 'ABA123',
             q.receipt: 'r.png', q.status: q.settled[0], q.scope: 42, q.checksum: None},
            {q.id: 2, q.subject_key: 88, q.amount: 5.0, q.reference: 'ABA123',
             q.receipt: 'r2.png', q.status: q.open_states[0], q.scope: None, q.checksum: None},
        ]
        ok, err = db.approve_manual_payment('dsn', 2, 42, 'admin-9', queue=queue)

        self.assertFalse(ok)
        self.assertEqual(err['error'], 'duplicate_txn_ref')
        self.assertEqual(err['duplicate_of'], 1)
        self.assertEqual(db.state['tables'][q.table][1][q.status], q.open_states[0])
        self.assertEqual(db.state['tables'][queue.grant.table], [])

    @both_tenants
    def test_double_approve_is_refused_by_the_state_machine(self, queue):
        db = make_db(queue)
        db.approve_manual_payment('dsn', 1, 42, 'admin-9', queue=queue)
        ok, err = db.approve_manual_payment('dsn', 1, 42, 'admin-9', queue=queue)

        self.assertFalse(ok)
        self.assertEqual(err['error'], 'invalid_transition')
        self.assertEqual(len(db.state['tables'][queue.grant.table]), 1)

    @both_tenants
    def test_payment_row_is_locked_before_it_is_read(self, queue):
        db = make_db(queue)
        db.approve_manual_payment('dsn', 1, 42, 'admin-9', queue=queue)
        self.assertTrue(any('FOR UPDATE' in s for s, _ in db.log))


class TestRefundAcrossTenants(unittest.TestCase):
    @both_tenants
    def test_refund_revokes_the_scope_the_payment_bought(self, queue):
        q, g = queue, queue.grant
        db = make_db(queue,
                     payment_overrides={q.status: q.status_for('approve'), q.scope: 42},
                     grants=[{g.subject_key: 77, g.scope_key: 42, g.status: g.on_approve},
                             {g.subject_key: 77, g.scope_key: 99, g.status: g.on_approve}])
        ok, _ = db.refund_manual_payment('dsn', 1, 'admin-9', 'user_request', app_id='a', queue=queue)

        self.assertTrue(ok)
        revoked = {r[g.scope_key]: r[g.status] for r in db.state['tables'][g.table]}
        # Only the track this payment paid for. The untouched one is the whole point.
        self.assertEqual(revoked, {42: g.on_revoke, 99: g.on_approve})

    @both_tenants
    def test_refund_refuses_to_guess_when_the_payment_has_no_scope(self, queue):
        q, g = queue, queue.grant
        db = make_db(queue,
                     payment_overrides={q.status: q.status_for('approve'), q.scope: None},
                     grants=[{g.subject_key: 77, g.scope_key: 42, g.status: g.on_approve},
                             {g.subject_key: 77, g.scope_key: 99, g.status: g.on_approve}])
        ok, err = db.refund_manual_payment('dsn', 1, 'admin-9', 'user_request', queue=queue)

        self.assertFalse(ok)
        self.assertEqual(err['error'], 'ambiguous_track')
        self.assertTrue(all(r[g.status] == g.on_approve for r in db.state['tables'][g.table]))

    @both_tenants
    def test_refunding_a_rejection_is_refused(self, queue):
        db = make_db(queue, payment_overrides={queue.status: queue.status_for('reject')})
        ok, err = db.refund_manual_payment('dsn', 1, 'admin-9', 'user_request', queue=queue)
        self.assertFalse(ok)
        self.assertEqual(err['error'], 'invalid_transition')


class TestRejectAcrossTenants(unittest.TestCase):
    @both_tenants
    def test_reject_records_the_reason_in_the_tenant_column(self, queue):
        db = make_db(queue)
        ok, _ = db.reject_manual_payment('dsn', 1, 'admin-9', 'unreadable', 'blurry',
                                         app_id='a', queue=queue)
        row = db.state['tables'][queue.table][0]
        self.assertTrue(ok)
        self.assertEqual(row[queue.status], queue.status_for('reject'))
        self.assertEqual(row[queue.reject_reason[0]], 'unreadable: blurry')

    @both_tenants
    def test_reject_still_requires_a_valid_reason_code(self, queue):
        db = make_db(queue)
        ok, err = db.reject_manual_payment('dsn', 1, 'admin-9', 'because i said so', queue=queue)
        self.assertFalse(ok)
        self.assertEqual(err['error'], 'bad_reason')


class TestQueueReadsAcrossTenants(unittest.TestCase):
    @both_tenants
    def test_rows_come_back_under_canonical_keys(self, queue):
        db = make_db(queue)
        rows = db.get_manual_payments('dsn', status_filter=queue.open_states[0], queue=queue)
        self.assertEqual(len(rows), 1)
        # Whatever the tenant calls its columns, the console sees the same keys.
        for key in ('id', 'user_id', 'amount', 'txn_ref', 'receipt_url', 'status', 'email'):
            self.assertIn(key, rows[0])
        self.assertEqual(rows[0]['email'], 'payer@example.com')
        self.assertEqual(rows[0]['txn_ref'], 'ABA123')

    @both_tenants
    def test_the_approve_dropdown_offers_only_active_options(self, queue):
        db = make_db(queue)
        options = db.get_active_tracks('dsn', queue=queue)
        self.assertEqual([o['id'] for o in options], [42])
        self.assertEqual(options[0]['name'], 'Track 42')

    @both_tenants
    def test_suspension_writes_the_tenant_subject_table(self, queue):
        db = make_db(queue)
        db.suspend_tenant_user('dsn', 77, 'chargeback abuse', actor='admin-9', queue=queue)
        s = queue.subject
        row = db.state['tables'][s.table][0]
        self.assertEqual(row[s.status], 'suspended')
        self.assertEqual(row[s.suspend_reason], 'chargeback abuse')
        # Grants survive a suspension — it is reversible.
        self.assertEqual(db.state['tables'][queue.grant.table], [])


# --------------------------------------------------------------------------
# Config is an injection surface. These are the gates.
# --------------------------------------------------------------------------

class TestConfigSafety(unittest.TestCase):
    def test_an_injected_identifier_is_refused_at_build_time(self):
        for payload in ('payments; DROP TABLE users', 'payments--', '"payments"', ''):
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    QueueSchema.from_config({"payment_queue": {"table": payload}})

    def test_a_nested_injected_identifier_is_refused_too(self):
        with self.assertRaises(ValueError):
            QueueSchema.from_config(
                {"payment_queue": {"grant": {"table": "entitlements; DELETE FROM users"}}})

    def test_validate_rejects_columns_the_tenant_does_not_have(self):
        db = make_db(MINISTRY)
        bad = QueueSchema.from_config({"payment_queue": {"reference": "no_such_column"}})
        with db.conn.cursor() as cur:
            errors = bad.validate(cur)
        self.assertTrue(any('no_such_column' in e for e in errors), errors)

    def test_validate_rejects_a_table_the_tenant_does_not_have(self):
        db = make_db(MINISTRY)
        bad = QueueSchema.from_config({"payment_queue": {"table": "invoices"}})
        with db.conn.cursor() as cur:
            errors = bad.validate(cur)
        self.assertTrue(any("'invoices' does not exist" in e for e in errors), errors)

    def test_validate_rejects_an_action_that_produces_an_undeclared_state(self):
        db = make_db(MINISTRY)
        bad = QueueSchema.from_config({"payment_queue": {"actions": {"approve": "settled"}}})
        with db.conn.cursor() as cur:
            errors = bad.validate(cur)
        self.assertTrue(any("'settled'" in e for e in errors), errors)

    def test_a_clean_shop_config_validates(self):
        db = make_db(SHOP)
        with db.conn.cursor() as cur:
            self.assertEqual(SHOP.validate(cur), [])

    def test_unknown_config_keys_are_ignored_not_fatal(self):
        queue = QueueSchema.from_config({"payment_queue": {"table": "orders", "future_key": 1}})
        self.assertEqual(queue.table, 'orders')


# --------------------------------------------------------------------------
# The grant-less tenant: settle the payment, grant nothing, let the app decide.
# --------------------------------------------------------------------------

class TestGrantlessQueue(unittest.TestCase):
    def test_approve_settles_the_payment_and_writes_no_grant(self):
        queue = SHOP.without_grant()
        db = make_db(SHOP)          # tables exist; nothing should touch access_grants
        ok, _ = db.approve_manual_payment('dsn', 1, None, 'admin-9', app_id='a', queue=queue)

        self.assertTrue(ok)
        self.assertEqual(db.state['tables'][queue.table][0][queue.status],
                         queue.status_for('approve'))
        self.assertEqual(db.state['tables']['access_grants'], [])
        self.assertFalse(any('access_grants' in s for s, _ in db.log))

    def test_refund_settles_without_revoking_anything(self):
        queue = SHOP.without_grant()
        db = make_db(SHOP, payment_overrides={SHOP.status: SHOP.status_for('approve')})
        ok, _ = db.refund_manual_payment('dsn', 1, 'admin-9', 'user_request', queue=queue)

        self.assertTrue(ok)
        self.assertEqual(db.state['tables'][queue.table][0][queue.status],
                         queue.status_for('refund'))
        self.assertFalse(any('access_grants' in s for s, _ in db.log))

    def test_overriding_a_grant_that_does_not_exist_is_refused(self):
        db = make_db(SHOP)
        with self.assertRaises(ValueError):
            db.set_entitlement('dsn', 77, 42, 'active', queue=SHOP.without_grant())


if __name__ == '__main__':
    unittest.main()
