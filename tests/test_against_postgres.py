"""The same paths, against a real PostgreSQL instead of a fake cursor.

Every other test here proves my model of a tenant's schema. These prove the
behaviour, on UUID keys, a real publish constraint, and real Khmer text — which
is the difference that mattered: int(row_id) passed every fake-cursor test in
the suite while breaking every save prolong ever made.

Skipped unless a scratch server is reachable. Bring one up with:
    initdb -D <dir> -U bifrost --auth=trust
    pg_ctl -D <dir> -o "-p 55432 -c unix_socket_directories=" start
"""
import os
import sys
import types
import unittest
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DSN = os.environ.get('BIFROST_TEST_PG',
                     'postgresql://bifrost@127.0.0.1:55432/tenant')

try:
    import psycopg2
    psycopg2.connect(DSN).close()
    HAVE_PG = True
except Exception:
    HAVE_PG = False

from bifrost.models.payments import PaymentMixin
from bifrost.models.review_queue import ReviewSchema, span_check, submit

QUESTION = '11111111-1111-4111-8111-111111111111'
APP_ID = '6a68326cfd37081598552ec8'  # a real ObjectId shape; the model parses it

CONFIG = {'review_queue': {
    'table': 'questions', 'id': 'id', 'status': 'status', 'awaiting': ['review'],
    'display': ['body_kh'], 'on_approve': 'published', 'on_reject': 'draft',
    'controls': ['fluency_passed', 'distractors_passed', 'correctness_passed'],
    'reject_reason': ['reject_reason'],
    'evidence': [{'column': 'source_ref', 'role': 'citation'}],
    'child': {'table': 'choices', 'fk': 'question_id',
              'columns': ['body_kh'], 'flag': 'is_correct'},
    'annotations': {'table': 'question_terms', 'fk': 'question_id',
                    'start': 'start_char', 'end': 'end_char',
                    'target': 'body_kh', 'surface': 'surface'},
}}


class DB(PaymentMixin):
    """The real model against a real database; only Mongo is stubbed."""

    def __init__(self, config):
        self.audit = []
        outer = self
        self.db = types.SimpleNamespace(
            applications=types.SimpleNamespace(
                find_one=lambda *a, **k: {"cms_config": config}),
            cms_audit_log=types.SimpleNamespace(
                insert_one=lambda doc: outer.audit.append(doc)))


@contextmanager
def real_tenant_db():
    import bifrost.utils.tenant_db as tdb
    original = tdb.get_tenant_db

    @contextmanager
    def factory(conn_str):
        conn = psycopg2.connect(DSN)
        try:
            yield conn
        finally:
            conn.close()

    tdb.get_tenant_db = factory
    try:
        yield
    finally:
        tdb.get_tenant_db = original


def fresh_row():
    with psycopg2.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("""UPDATE questions SET status='review', fluency_passed=false,
                       distractors_passed=false, correctness_passed=false,
                       reviewed_by=NULL, reviewed_at=NULL, reject_reason=NULL
                       WHERE id=%s""", [QUESTION])
        conn.commit()


def read(*columns):
    with psycopg2.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(f'SELECT {", ".join(columns)} FROM questions WHERE id=%s', [QUESTION])
        return dict(zip(columns, cur.fetchone()))


@unittest.skipUnless(HAVE_PG, f"no scratch PostgreSQL at {DSN}")
class TestSavePathOnUuidKeys(unittest.TestCase):
    def setUp(self):
        fresh_row()
        self.db = DB(CONFIG)

    def test_a_uuid_row_actually_saves(self):
        # The bug that broke every prolong grid save, against a real UUID key.
        with real_tenant_db():
            self.db.save_tenant_table_row(DSN, 'questions', QUESTION,
                                          {'source_ref': 'UNCLOS, Art 221'},
                                          app_id=APP_ID, acting_user='ed@example.com')
        self.assertEqual(read('source_ref')['source_ref'], 'UNCLOS, Art 221')

    def test_an_ordinary_save_does_not_touch_the_attestation(self):
        # prolong asked for this one three times: the review queue owns the
        # stamp, so an unrelated edit must not overwrite who signed the review.
        with psycopg2.connect(DSN) as conn, conn.cursor() as cur:
            cur.execute("""UPDATE questions SET reviewed_by='original@example.com',
                           reviewed_at=now() WHERE id=%s""", [QUESTION])
            conn.commit()
        with real_tenant_db():
            self.db.save_tenant_table_row(DSN, 'questions', QUESTION,
                                          {'source_ref': 'edited'},
                                          app_id=APP_ID, acting_user='someone@else.com')
        self.assertEqual(read('reviewed_by')['reviewed_by'], 'original@example.com')

    def test_controls_submitted_to_the_save_path_do_not_persist(self):
        """Half of the back-door guard, and only half — read this before trusting it.

        The route strips the review controls from the payload; that is asserted
        structurally in test_review_queue.TestReviewControlsAreNotWritableBySave,
        because exercising the route needs a session and a logged-in role. What
        this proves is the other half: that a payload carrying them, stripped as
        the route strips it, leaves the columns alone in a real database.

        End to end through the HTTP layer is still unproven. prolong's reviewer
        clicking Save in the drawer is the test that closes it.
        """
        review = ReviewSchema.from_config(CONFIG)
        submitted = {'fluency_passed': '1', 'distractors_passed': '1',
                     'correctness_passed': '1', 'source_ref': 'x'}
        data = {k: v for k, v in submitted.items() if k not in set(review.controls)}
        with real_tenant_db():
            self.db.save_tenant_table_row(DSN, 'questions', QUESTION, data,
                                          app_id=APP_ID, acting_user='ed@example.com')
        row = read('fluency_passed', 'distractors_passed', 'correctness_passed')
        self.assertEqual(list(row.values()), [False, False, False])


@unittest.skipUnless(HAVE_PG, f"no scratch PostgreSQL at {DSN}")
class TestReviewAgainstTheRealConstraint(unittest.TestCase):
    def setUp(self):
        fresh_row()
        self.schema = ReviewSchema.from_config(CONFIG)

    def test_approving_satisfies_the_publish_check_constraint(self):
        conn = psycopg2.connect(DSN)
        ok, _ = submit(conn, self.schema, QUESTION, set(self.schema.controls),
                       'approve', 'reviewer@example.com')
        conn.close()
        self.assertTrue(ok)
        row = read('status', 'reviewed_by', 'reviewed_at')
        self.assertEqual(row['status'], 'published')
        self.assertEqual(row['reviewed_by'], 'reviewer@example.com')
        self.assertIsNotNone(row['reviewed_at'])

    def test_a_partial_approve_never_reaches_the_database(self):
        conn = psycopg2.connect(DSN)
        ok, msg = submit(conn, self.schema, QUESTION, {'fluency_passed'},
                         'approve', 'reviewer@example.com')
        conn.close()
        self.assertFalse(ok)
        self.assertEqual(read('status')['status'], 'review')

    def test_send_back_records_the_failing_check(self):
        conn = psycopg2.connect(DSN)
        ok, _ = submit(conn, self.schema, QUESTION, set(), 'reject',
                       'reviewer@example.com', reason='Failed: correctness passed',
                       reason_column='reject_reason')
        conn.close()
        self.assertTrue(ok)
        row = read('status', 'reject_reason')
        self.assertEqual(row['status'], 'draft')
        self.assertIn('correctness', row['reject_reason'])


@unittest.skipUnless(HAVE_PG, f"no scratch PostgreSQL at {DSN}")
class TestSpanIntegrityOnRealKhmer(unittest.TestCase):
    """Code point offsets, exclusive end, NFC — against actual Khmer text."""

    def setUp(self):
        fresh_row()
        self.schema = ReviewSchema.from_config(CONFIG)

    def _check(self, new_text):
        with psycopg2.connect(DSN) as conn, conn.cursor() as cur:
            return span_check(cur, self.schema, QUESTION, new_text, fk_type='uuid')

    def test_the_stored_span_really_is_the_surface_it_claims(self):
        row = read('body_kh')
        with psycopg2.connect(DSN) as conn, conn.cursor() as cur:
            cur.execute("SELECT start_char, end_char, surface FROM question_terms")
            start, end, surface = cur.fetchone()
        self.assertEqual(row['body_kh'][start:end], surface,
                         "code point offsets with an exclusive end")

    def test_editing_the_annotated_column_is_refused(self):
        reason = self._check('កែសម្រួល ' + read('body_kh')['body_kh'])
        self.assertIsNotNone(reason)
        self.assertIn('term spans', reason)

    def test_an_unchanged_value_is_not_refused(self):
        self.assertIsNone(self._check(read('body_kh')['body_kh']))

    def test_drift_is_reported_differently_from_a_shift(self):
        # Spans that already fail against the stored text: the row is broken
        # before the reviewer touched it, and that needs different words.
        with psycopg2.connect(DSN) as conn, conn.cursor() as cur:
            cur.execute("UPDATE question_terms SET surface='មិនត្រូវ'")
            conn.commit()
        reason = self._check('anything else')
        self.assertIn('already out of sync', reason)
        with psycopg2.connect(DSN) as conn, conn.cursor() as cur:
            cur.execute("""UPDATE question_terms qt SET surface =
                           substring(q.body_kh from qt.start_char + 1
                                     for qt.end_char - qt.start_char)
                           FROM questions q WHERE q.id = qt.question_id""")
            conn.commit()


if __name__ == '__main__':
    unittest.main()
