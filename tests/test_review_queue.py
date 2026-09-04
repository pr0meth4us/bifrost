"""The review queue: config gates, the approve gate, and the attestation.

The queue is generic by construction, so the tests use a shape that is NOT
prolong's — articles/revisions — and one that is, to prove neither is special.
"""
import sys, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bifrost.models.review_queue import ReviewSchema, load_item, submit


PROLONG = {'review_queue': {
    'table': 'questions', 'awaiting': ['review'],
    'display': ['stem_kh', 'source_ref'],
    'controls': ['fluency_passed', 'distractors_passed', 'correctness_passed'],
    'on_approve': 'published', 'on_reject': 'draft',
    'child': {'table': 'choices', 'fk': 'question_id',
              'columns': ['body_kh', 'explanation_kh'], 'flag': 'is_correct'},
}}

ARTICLES = {'review_queue': {
    'table': 'articles', 'awaiting': ['submitted', 'resubmitted'],
    'display': ['headline'], 'controls': ['legal_ok'],
    'on_approve': 'live', 'on_reject': 'spiked',
    'child': {'table': 'revisions', 'fk': 'article_id', 'columns': ['body']},
}}


class FakeCursor:
    def __init__(self, store): self.store, self.sql, self.rowcount = store, [], 1
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def execute(self, sql, params=None):
        self.sql.append((sql, params))
        self.store.setdefault('executed', []).append((sql, params))
    def fetchone(self): return self.store.get('one')
    def fetchall(self): return self.store.get('all', [])


class FakeConn:
    def __init__(self, store): self.store, self.committed = store, False
    def cursor(self): return FakeCursor(self.store)
    def commit(self): self.committed = True


class TestConfigGate(unittest.TestCase):
    def test_absent_block_yields_no_queue(self):
        self.assertIsNone(ReviewSchema.from_config({}))
        self.assertIsNone(ReviewSchema.from_config({'payment_queue': {'table': 'p'}}))

    def test_injection_in_any_identifier_is_refused(self):
        for bad in ('questions; DROP TABLE users', 'a b', '"q"', '1questions'):
            with self.subTest(ident=bad):
                with self.assertRaises(ValueError):
                    ReviewSchema.from_config({'review_queue': {
                        'table': bad, 'controls': ['ok']}})

    def test_injection_in_a_child_identifier_is_refused(self):
        with self.assertRaises(ValueError):
            ReviewSchema.from_config({'review_queue': {
                'table': 'questions', 'controls': ['ok'],
                'child': {'table': 'choices', 'fk': 'x; DELETE FROM choices'}}})

    def test_a_queue_with_no_controls_is_refused(self):
        with self.assertRaises(ValueError):
            ReviewSchema.from_config({'review_queue': {'table': 'questions'}})

    def test_status_vocabulary_is_not_treated_as_an_identifier(self):
        # 'not a column name' would fail safe_ident if statuses were identifiers.
        s = ReviewSchema.from_config({'review_queue': {
            'table': 't', 'controls': ['c'], 'on_approve': 'ready to ship'}})
        self.assertEqual(s.on_approve, 'ready to ship')


class TestApproveGate(unittest.TestCase):
    def setUp(self):
        self.schema = ReviewSchema.from_config(PROLONG)

    def test_approve_refused_until_every_control_is_ticked(self):
        conn = FakeConn({})
        ok, msg = submit(conn, self.schema, 'abc', {'fluency_passed'}, 'approve', 'me')
        self.assertFalse(ok)
        self.assertIn('correctness_passed', msg)
        self.assertFalse(conn.committed)

    def test_approve_allowed_when_all_ticked(self):
        store = {'one': ('abc', 'review', 'stem', 'src', True, True, True)}
        conn = FakeConn(store)
        ok, _ = submit(conn, self.schema, 'abc', set(self.schema.controls), 'approve', 'me')
        self.assertTrue(ok)
        self.assertTrue(conn.committed)

    def test_reject_needs_no_ticks(self):
        store = {'one': ('abc', 'review', 'stem', 'src', False, False, False)}
        ok, _ = submit(FakeConn(store), self.schema, 'abc', set(), 'reject', 'me')
        self.assertTrue(ok)

    def test_unknown_decision_is_refused(self):
        ok, msg = submit(FakeConn({}), self.schema, 'abc', set(), 'delete', 'me')
        self.assertFalse(ok)
        self.assertIn('delete', msg)


class TestAttestation(unittest.TestCase):
    def setUp(self):
        self.schema = ReviewSchema.from_config(PROLONG)

    def _update_sql(self, store):
        return [(s, p) for s, p in store['executed'] if s.startswith('UPDATE')][0]

    def test_reviewer_is_the_session_actor_and_time_is_the_server_clock(self):
        store = {'one': ('abc', 'review', 'stem', 'src', True, True, True)}
        submit(FakeConn(store), self.schema, 'abc', set(self.schema.controls),
               'approve', 'reviewer@example.com')
        sql, params = self._update_sql(store)
        self.assertIn('"reviewed_by" = %s', sql)
        self.assertIn('"reviewed_at" = NOW()', sql)  # not a client-supplied timestamp
        self.assertIn('reviewer@example.com', params)

    def test_row_id_is_not_coerced_to_int(self):
        # Content tables are commonly keyed by UUID; int() would reject them.
        uid = '3f2a1c88-0000-4000-8000-000000000001'
        store = {'one': (uid, 'review', 'stem', 'src', True, True, True)}
        submit(FakeConn(store), self.schema, uid, set(self.schema.controls), 'approve', 'me')
        _, params = self._update_sql(store)
        self.assertIn(uid, params)

    def test_missing_row_is_reported_not_silently_ignored(self):
        ok, msg = submit(FakeConn({'one': None}), self.schema, 'gone',
                         set(self.schema.controls), 'approve', 'me')
        self.assertFalse(ok)
        self.assertIn('no longer exists', msg)


class TestGenericShape(unittest.TestCase):
    """Nothing above depends on the tenant being prolong."""

    def test_a_different_parent_child_shape_works_identically(self):
        schema = ReviewSchema.from_config(ARTICLES)
        store = {'one': ('7', 'submitted', 'Headline', True)}
        ok, _ = submit(FakeConn(store), schema, '7', {'legal_ok'}, 'approve', 'ed@x.com')
        self.assertTrue(ok)
        sql, params = [(s, p) for s, p in store['executed'] if s.startswith('UPDATE')][0]
        self.assertIn('"articles"', sql)
        self.assertIn('live', params)
        self.assertNotIn('published', params)

    def test_child_query_filters_by_the_configured_fk(self):
        schema = ReviewSchema.from_config(ARTICLES)
        store = {'one': ('7', 'submitted', 'Headline', True), 'all': [('1', 'body text')]}
        cur = FakeCursor(store)
        parent, children = load_item(cur, schema, '7')
        child_sql, child_params = cur.sql[-1]
        self.assertIn('"revisions"', child_sql)
        self.assertIn('"article_id" = %s', child_sql)
        self.assertEqual(child_params, ['7'])
        self.assertEqual(children[0]['body'], 'body text')

    def test_a_queue_without_children_is_valid(self):
        schema = ReviewSchema.from_config({'review_queue': {
            'table': 'pages', 'controls': ['checked']}})
        self.assertIsNone(schema.child)
        cur = FakeCursor({'one': ('1', 'review', True)})
        parent, children = load_item(cur, schema, '1')
        self.assertEqual(children, [])


if __name__ == '__main__':
    unittest.main()
