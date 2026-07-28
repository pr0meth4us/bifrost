"""Admin Console Phase 1 — the tests SOW §6.3 asks for.

Covers, without needing a live Postgres:
  * approve -> entitlement activation (correct user, correct track)
  * refund  -> entitlement revocation (the payment's track, never a default)
  * duplicate txn_ref rejection
  * payment state machine (no double-approve, no refunding a rejection)
  * publish-time MCQ validation (4 choices / 1 correct / bilingual / source_ref)
  * role permission enforcement at the API layer (403 from the server, not a hidden button)

Bulk-import validation and offset integrity are Phase 2 and are NOT covered here —
that code does not exist yet.

Run:  python -m unittest discover -s tests -v
"""
import re
import sys
import types
import unittest
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bifrost.models.payments import PaymentMixin  # noqa: E402


# --------------------------------------------------------------------------
# A small fake Postgres: enough to answer the statements the console issues,
# and to record exactly which parameters it was called with.
# --------------------------------------------------------------------------

PAYMENT_COLUMNS = [
    'id', 'user_id', 'amount', 'txn_ref', 'receipt_url', 'status',
    'exam_track_id', 'receipt_checksum', 'created_at', 'reviewed_by',
    'reviewed_at', 'reject_reason', 'refund_reason',
]


class FakeCursor:
    def __init__(self, state, log):
        self.state = state
        self.log = log
        self.description = None
        self.rowcount = 0
        self._rows = []

    # -- helpers ----------------------------------------------------------
    def _set(self, rows, columns=None):
        self._rows = rows
        self.description = [(c,) for c in (columns or [])]
        self.rowcount = len(rows)

    def execute(self, sql, params=None):
        sql = ' '.join(sql.split())
        params = list(params or [])
        self.log.append((sql, params))
        self._rows, self.description, self.rowcount = [], None, 0

        if 'information_schema.columns' in sql:
            table = params[0]
            self._set([(c,) for c in self.state['columns'].get(table, [])], ['column_name'])
            return

        if sql.startswith('SELECT id, user_id') and 'FOR UPDATE' in sql:
            p = self.state['payments'].get(params[0])
            cols = re.search(r'SELECT (.+?) FROM payments', sql).group(1)
            names = [c.strip().split(' AS ')[-1].strip() for c in cols.split(',')]
            self._set([tuple(p.get(n) for n in names)] if p else [], names)
            return

        if 'FROM payments WHERE txn_ref' in sql:
            ref, exclude = params
            hit = [p for p in self.state['payments'].values()
                   if p['txn_ref'] == ref and p['id'] != exclude
                   and p['status'] in ('approved', 'refunded')]
            self._set([(hit[0]['id'],)] if hit else [], ['id'])
            return

        if sql.startswith('UPDATE payments SET'):
            pid = params[-1]
            payment = self.state['payments'][pid]
            for field, value in zip(re.findall(r'"?(\w+)"? = %s', sql), params[:-1]):
                payment[field] = value
            payment['status'] = re.search(r"status = '(\w+)'", sql).group(1)
            self.rowcount = 1
            return

        if sql.startswith('SELECT exam_track_id FROM entitlements'):
            rows = [(e['exam_track_id'],) for e in self.state['entitlements']
                    if e['user_id'] == params[0] and e['status'] == 'premium']
            self._set(rows, ['exam_track_id'])
            return

        if sql.startswith('SELECT status FROM entitlements'):
            hit = [e for e in self.state['entitlements']
                   if e['user_id'] == params[0] and e['exam_track_id'] == params[1]]
            self._set([(hit[0]['status'],)] if hit else [], ['status'])
            return

        if sql.startswith('UPDATE entitlements SET'):
            status = re.search(r"status = '(\w+)'", sql)
            new_status = status.group(1) if status else params[0]
            offset = 0 if status else 1
            user_id, track_id = params[offset], params[offset + 1]
            hits = [e for e in self.state['entitlements']
                    if e['user_id'] == user_id and e['exam_track_id'] == track_id]
            for e in hits:
                e['status'] = new_status
            self.rowcount = len(hits)
            return

        if sql.startswith('INSERT INTO entitlements'):
            self.state['entitlements'].append({
                'user_id': params[0], 'exam_track_id': params[1],
                'status': params[2] if len(params) > 2 else 'premium',
            })
            self.rowcount = 1
            return

        if sql.startswith('SELECT source_ref FROM questions'):
            q = self.state['questions'].get(params[0])
            self._set([(q['source_ref'],)] if q else [], ['source_ref'])
            return

        if 'FROM choices WHERE question_id' in sql:
            rows = [(c['id'], c['is_correct'], c['explanation_kh'], c['explanation_en'])
                    for c in self.state['choices'] if c['question_id'] == params[0]]
            self._set(rows, ['id', 'is_correct', 'explanation_kh', 'explanation_en'])
            return

        if sql.startswith('UPDATE users SET'):
            self.rowcount = 1
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
        self.state, self.log = state, log
        self.commits = 0

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
    """PaymentMixin wired to the fake Postgres and a fake Mongo audit sink."""

    def __init__(self, state):
        self.state = state
        self.log = []
        self.conn = FakeConnection(state, self.log)
        self.db = types.SimpleNamespace(cms_audit_log=FakeCollection())

    def statements(self):
        return [s for s, _ in self.log]


def make_db(**overrides):
    state = {
        'columns': {
            'payments': PAYMENT_COLUMNS,
            'users': ['id', 'email', 'status', 'suspended_at'],
        },
        'payments': {
            1: {'id': 1, 'user_id': 77, 'amount': 5.0, 'txn_ref': 'ABA123',
                'receipt_url': 'r1.png', 'status': 'pending', 'exam_track_id': None,
                'receipt_checksum': None},
        },
        'entitlements': [],
        'questions': {},
        'choices': [],
    }
    state.update(overrides)
    db = ConsoleDB(state)

    @contextmanager
    def fake_get_tenant_db(conn_str):
        yield db.conn

    import bifrost.utils.tenant_db as tdb
    tdb.get_tenant_db = fake_get_tenant_db
    return db


# --------------------------------------------------------------------------
# Money path
# --------------------------------------------------------------------------

class TestApprove(unittest.TestCase):
    def test_approve_activates_entitlement_for_the_payer_and_chosen_track(self):
        db = make_db()
        ok, payment = db.approve_manual_payment('dsn', 1, 42, reviewer_id='admin-9', app_id='app1')

        self.assertTrue(ok)
        self.assertEqual(db.state['payments'][1]['status'], 'approved')
        # The entitlement belongs to the PAYER (77), not the reviewer, on the
        # track that was actually selected (42) — both named defects in the SOW.
        self.assertEqual(db.state['entitlements'], [{'user_id': 77, 'exam_track_id': 42,
                                                     'status': 'premium'}])
        self.assertEqual(db.state['payments'][1]['reviewed_by'], 'admin-9')
        self.assertEqual(db.state['payments'][1]['exam_track_id'], 42)

    def test_approve_and_entitlement_share_one_commit(self):
        db = make_db()
        db.approve_manual_payment('dsn', 1, 42, 'admin-9')
        # One commit for both writes: a payment cannot be approved while the
        # entitlement fails to activate.
        self.assertEqual(db.conn.commits, 1)

    def test_approve_writes_an_audit_row(self):
        db = make_db()
        db.approve_manual_payment('dsn', 1, 42, 'admin-9', app_id='app1')
        entry = db.db.cms_audit_log.docs[-1]
        self.assertEqual(entry['action'], 'APPROVE')
        self.assertEqual(entry['acting_user'], 'admin-9')
        self.assertEqual(entry['before']['status'], 'pending')
        self.assertEqual(entry['after']['exam_track_id'], 42)

    def test_approve_requires_a_track(self):
        db = make_db()
        ok, err = db.approve_manual_payment('dsn', 1, None, 'admin-9')
        self.assertFalse(ok)
        self.assertEqual(err['error'], 'track_required')
        self.assertEqual(db.state['entitlements'], [])

    def test_duplicate_txn_ref_is_blocked_and_points_at_the_prior_payment(self):
        db = make_db(payments={
            1: {'id': 1, 'user_id': 77, 'amount': 5.0, 'txn_ref': 'ABA123', 'receipt_url': 'r.png',
                'status': 'approved', 'exam_track_id': 42, 'receipt_checksum': None},
            2: {'id': 2, 'user_id': 88, 'amount': 5.0, 'txn_ref': 'ABA123', 'receipt_url': 'r2.png',
                'status': 'pending', 'exam_track_id': None, 'receipt_checksum': None},
        })
        ok, err = db.approve_manual_payment('dsn', 2, 42, 'admin-9')

        self.assertFalse(ok)
        self.assertEqual(err['error'], 'duplicate_txn_ref')
        self.assertEqual(err['duplicate_of'], 1)
        self.assertEqual(db.state['payments'][2]['status'], 'pending')
        self.assertEqual(db.state['entitlements'], [])

    def test_double_approve_is_refused_by_the_state_machine(self):
        db = make_db()
        db.approve_manual_payment('dsn', 1, 42, 'admin-9')
        ok, err = db.approve_manual_payment('dsn', 1, 42, 'admin-9')

        self.assertFalse(ok)
        self.assertEqual(err['error'], 'invalid_transition')
        self.assertEqual(len(db.state['entitlements']), 1)

    def test_payment_row_is_locked_before_it_is_read(self):
        db = make_db()
        db.approve_manual_payment('dsn', 1, 42, 'admin-9')
        self.assertTrue(any('FOR UPDATE' in s for s in db.statements()))


class TestReject(unittest.TestCase):
    def test_reject_requires_a_valid_reason_code(self):
        db = make_db()
        ok, err = db.reject_manual_payment('dsn', 1, 'admin-9', 'because i said so')
        self.assertFalse(ok)
        self.assertEqual(err['error'], 'bad_reason')
        self.assertEqual(db.state['payments'][1]['status'], 'pending')

    def test_reject_records_the_reason(self):
        db = make_db()
        ok, _ = db.reject_manual_payment('dsn', 1, 'admin-9', 'unreadable', 'blurry photo',
                                         app_id='app1')
        self.assertTrue(ok)
        self.assertEqual(db.state['payments'][1]['status'], 'rejected')
        self.assertEqual(db.state['payments'][1]['reject_reason'], 'unreadable: blurry photo')
        self.assertEqual(db.db.cms_audit_log.docs[-1]['action'], 'REJECT')


class TestRefund(unittest.TestCase):
    def _approved(self):
        return make_db(
            payments={1: {'id': 1, 'user_id': 77, 'amount': 5.0, 'txn_ref': 'ABA123',
                          'receipt_url': 'r.png', 'status': 'approved', 'exam_track_id': 42,
                          'receipt_checksum': None}},
            entitlements=[{'user_id': 77, 'exam_track_id': 42, 'status': 'premium'},
                          {'user_id': 77, 'exam_track_id': 7, 'status': 'premium'}],
        )

    def test_refund_revokes_the_track_the_payment_paid_for(self):
        db = self._approved()
        ok, result = db.refund_manual_payment('dsn', 1, 'admin-9', 'user_request', app_id='app1')

        self.assertTrue(ok)
        self.assertEqual(db.state['payments'][1]['status'], 'refunded')
        self.assertEqual(result['exam_track_id'], 42)
        by_track = {e['exam_track_id']: e['status'] for e in db.state['entitlements']}
        self.assertEqual(by_track[42], 'rejected')
        # The unrelated track this user also paid for must be untouched.
        self.assertEqual(by_track[7], 'premium')

    def test_refund_never_guesses_when_the_track_is_ambiguous(self):
        db = self._approved()
        db.state['payments'][1]['exam_track_id'] = None  # legacy row, pre-migration
        ok, err = db.refund_manual_payment('dsn', 1, 'admin-9', 'user_request')

        self.assertFalse(ok)
        self.assertEqual(err['error'], 'ambiguous_track')
        self.assertTrue(all(e['status'] == 'premium' for e in db.state['entitlements']))

    def test_refund_infers_the_track_when_there_is_exactly_one(self):
        db = self._approved()
        db.state['payments'][1]['exam_track_id'] = None
        db.state['entitlements'] = [{'user_id': 77, 'exam_track_id': 7, 'status': 'premium'}]
        ok, result = db.refund_manual_payment('dsn', 1, 'admin-9', 'user_request')

        self.assertTrue(ok)
        self.assertEqual(result['exam_track_id'], 7)
        self.assertEqual(db.state['entitlements'][0]['status'], 'rejected')

    def test_a_rejected_payment_cannot_be_refunded(self):
        db = make_db()
        db.state['payments'][1]['status'] = 'rejected'
        ok, err = db.refund_manual_payment('dsn', 1, 'admin-9', 'user_request')
        self.assertFalse(ok)
        self.assertEqual(err['error'], 'invalid_transition')

    def test_refund_requires_a_valid_reason_code(self):
        db = self._approved()
        ok, err = db.refund_manual_payment('dsn', 1, 'admin-9', '')
        self.assertFalse(ok)
        self.assertEqual(err['error'], 'bad_reason')
        self.assertEqual(db.state['payments'][1]['status'], 'approved')


class TestSuspension(unittest.TestCase):
    def test_suspension_preserves_entitlements(self):
        db = make_db(entitlements=[{'user_id': 77, 'exam_track_id': 42, 'status': 'premium'}])
        db.suspend_tenant_user('dsn', 77, 'scraping signals', actor='admin-9', app_id='app1')

        # Suspension is reversible; revoking here would destroy a purchase that
        # reinstate cannot restore.
        self.assertEqual(db.state['entitlements'][0]['status'], 'premium')
        self.assertFalse(any('entitlements' in s for s in db.statements()))
        self.assertEqual(db.db.cms_audit_log.docs[-1]['action'], 'SUSPEND')


# --------------------------------------------------------------------------
# Publish-time MCQ validation (SOW §3.2)
# --------------------------------------------------------------------------

def question_fixture(n_choices=4, n_correct=1, source_ref='MFAIC-2019 p.14',
                     explanation_kh='ពន្យល់', explanation_en='Because.'):
    choices = []
    for i in range(n_choices):
        correct = i < n_correct
        choices.append({
            'id': i + 1, 'question_id': 1, 'is_correct': correct,
            'explanation_kh': explanation_kh if correct else '',
            'explanation_en': explanation_en if correct else '',
        })
    return {'questions': {1: {'id': 1, 'source_ref': source_ref}}, 'choices': choices}


class TestPublishValidation(unittest.TestCase):
    def test_a_valid_question_publishes(self):
        db = make_db(**question_fixture())
        self.assertEqual(db.validate_question_publishable('dsn', 1), [])

    def test_three_choices_is_blocked(self):
        db = make_db(**question_fixture(n_choices=3))
        errors = db.validate_question_publishable('dsn', 1)
        self.assertTrue(any('3 choices' in e for e in errors))

    def test_two_correct_answers_is_blocked(self):
        db = make_db(**question_fixture(n_correct=2))
        errors = db.validate_question_publishable('dsn', 1)
        self.assertTrue(any('2 correct' in e for e in errors))

    def test_missing_english_explanation_is_blocked(self):
        db = make_db(**question_fixture(explanation_en=''))
        errors = db.validate_question_publishable('dsn', 1)
        self.assertTrue(any('English explanation' in e for e in errors))

    def test_missing_khmer_explanation_is_blocked(self):
        db = make_db(**question_fixture(explanation_kh='   '))
        errors = db.validate_question_publishable('dsn', 1)
        self.assertTrue(any('Khmer explanation' in e for e in errors))

    def test_empty_source_ref_is_blocked(self):
        db = make_db(**question_fixture(source_ref=''))
        errors = db.validate_question_publishable('dsn', 1)
        self.assertTrue(any('source_ref' in e for e in errors))


# --------------------------------------------------------------------------
# SQL identifier validation (SOW §4.4)
# --------------------------------------------------------------------------

class TestIdentifierGuard(unittest.TestCase):
    def test_injection_attempt_raises_rather_than_asserting(self):
        from bifrost.models.payments import safe_ident
        for bad in ('users; DROP TABLE payments', 'users"', '1users', '', None):
            with self.assertRaises(ValueError):
                safe_ident(bad)
        self.assertEqual(safe_ident('exam_tracks'), 'exam_tracks')


# --------------------------------------------------------------------------
# Role enforcement at the API layer (SOW §3.8 / acceptance criterion 4)
# --------------------------------------------------------------------------

class TestRolePermissions(unittest.TestCase):
    def setUp(self):
        from bifrost.backoffice import ROLE_PERMISSIONS
        self.perms = ROLE_PERMISSIONS

    def test_content_manager_cannot_touch_money(self):
        cm = self.perms['content_manager']
        self.assertNotIn('payments:view', cm)
        self.assertNotIn('payments:approve', cm)

    def test_content_manager_cannot_publish(self):
        self.assertNotIn('content:publish', self.perms['content_manager'])
        self.assertIn('content:write', self.perms['content_manager'])

    def test_admin_can_publish(self):
        for role in ('admin', 'owner', 'super_admin'):
            self.assertIn('content:publish', self.perms[role])

    def test_operations_cannot_edit_content_or_config(self):
        ops = self.perms['operations']
        self.assertNotIn('content:write', ops)
        self.assertNotIn('write:config', ops)
        self.assertIn('payments:approve', ops)

    def test_server_rejects_a_content_manager_on_a_payments_endpoint(self):
        """The real check: a 403 from the server, not a hidden button."""
        from flask import Flask
        import bifrost.backoffice as bo

        app = Flask(__name__)
        app.config['SECRET_KEY'] = 'test'
        role_holder = {'role': 'content_manager'}
        original = bo.get_current_role_in_app
        bo.get_current_role_in_app = lambda app_id: role_holder['role']

        @app.route('/app/<app_id>/payments/<pid>/approve', methods=['POST'])
        @bo.requires('payments:approve')
        def approve(app_id, pid):
            return 'approved', 200

        try:
            with app.test_client() as client:
                with client.session_transaction() as sess:
                    sess['backoffice_user'] = 'cm-1'

                res = client.post('/app/a1/payments/1/approve')
                self.assertEqual(res.status_code, 403)

                role_holder['role'] = 'admin'
                with client.session_transaction() as sess:
                    sess['backoffice_user'] = 'admin-1'
                res = client.post('/app/a1/payments/1/approve')
                self.assertEqual(res.status_code, 200)
        finally:
            bo.get_current_role_in_app = original


if __name__ == '__main__':
    unittest.main(verbosity=2)
