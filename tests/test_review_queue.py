"""The review queue: config gates, the approve gate, and the attestation.

The queue is generic by construction, so the tests use a shape that is NOT
prolong's — articles/revisions — and one that is, to prove neither is special.
"""
import sys, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bifrost.models.review_queue import ReviewSchema, children_for, submit


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
        cur = FakeCursor({'all': [('7', '1', 'body text')]})
        grouped = children_for(cur, schema, ['7'], fk_type='uuid')
        child_sql, child_params = cur.sql[-1]
        self.assertIn('"revisions"', child_sql)
        self.assertIn('"article_id" = ANY(%s::uuid[])', child_sql)
        self.assertEqual(child_params, [['7']])
        self.assertEqual(grouped['7'][0]['body'], 'body text')

    def test_uuid_fks_get_an_explicit_array_cast(self):
        # psycopg2 interpolates a bound list as ARRAY['a','b'], which resolves
        # to text[], and Postgres refuses uuid = text. A scalar `= %s` is fine
        # (an unknown literal coerces to the column type); only arrays break.
        schema = ReviewSchema.from_config(PROLONG)
        cur = FakeCursor({'all': []})
        children_for(cur, schema, ['3f2a1c88-0000-4000-8000-000000000001'],
                     fk_type='uuid')
        sql, params = cur.sql[-1]
        self.assertIn('= ANY(%s::uuid[])', sql)
        self.assertNotIn('::text = ANY', sql)  # the index on the FK stays usable

    def test_unknown_fk_type_falls_back_to_a_text_comparison(self):
        schema = ReviewSchema.from_config(PROLONG)
        cur = FakeCursor({'all': []})
        children_for(cur, schema, ['1'], fk_type=None)
        self.assertIn('::text = ANY(%s)', cur.sql[-1][0])

    def test_a_bogus_fk_type_cannot_reach_the_query(self):
        schema = ReviewSchema.from_config(PROLONG)
        with self.assertRaises(ValueError):
            children_for(FakeCursor({'all': []}), schema, ['1'],
                         fk_type='uuid[]; DROP TABLE choices')

    def test_ids_are_bound_as_strings_whatever_they_arrive_as(self):
        import uuid as uuid_mod
        schema = ReviewSchema.from_config(PROLONG)
        cur = FakeCursor({'all': []})
        uid = uuid_mod.UUID('3f2a1c88-0000-4000-8000-000000000001')
        children_for(cur, schema, [uid], fk_type='uuid')
        self.assertEqual(cur.sql[-1][1], [[str(uid)]])

    def test_children_are_fetched_for_a_whole_page_in_one_query(self):
        # One query per drawer-able row would be the N+1 the drawer avoids.
        schema = ReviewSchema.from_config(PROLONG)
        cur = FakeCursor({'all': [('q1', 'c1', 'a', 'why', True),
                                  ('q1', 'c2', 'b', '', False),
                                  ('q2', 'c3', 'c', '', True)]})
        grouped = children_for(cur, schema, ['q1', 'q2'])
        self.assertEqual(len(cur.sql), 1)
        self.assertEqual(len(grouped['q1']), 2)
        self.assertEqual(len(grouped['q2']), 1)

    def test_a_queue_without_children_needs_no_query(self):
        schema = ReviewSchema.from_config({'review_queue': {
            'table': 'pages', 'controls': ['checked']}})
        self.assertIsNone(schema.child)
        cur = FakeCursor({})
        self.assertEqual(children_for(cur, schema, ['1']), {})
        self.assertEqual(cur.sql, [])


if __name__ == '__main__':
    unittest.main()


class TestReviewControlsAreNotWritableBySave(unittest.TestCase):
    """The controls share the grid drawer, so they post on an ordinary Save too.

    They must be dropped there: submit() is what enforces the all-ticked gate
    and writes the attestation, so a plain save setting them would be a way to
    mark a record reviewed without a review.
    """

    def test_the_save_path_excludes_them(self):
        import re
        src = (Path(__file__).resolve().parents[1]
               / 'bifrost/backoffice/tenant_routes.py').read_text()
        # Both the save and the create path build `forbidden`; each must widen
        # it with the review controls.
        guards = re.findall(r'forbidden = forbidden \| set\(_review\.controls\)', src)
        self.assertEqual(len(guards), 2,
                         "save and create must both exclude the review controls")


class TestChildFetchIsIsolated(unittest.TestCase):
    """A failed child fetch costs the drawer its children, not the whole page.

    The first version let the exception escape into the grid's schema-loading
    handler, which rendered "No tables are visible. Connect a tenant database"
    — sending the tenant to re-check a connection string that was fine.
    """

    def test_the_grid_route_catches_child_fetch_failures_separately(self):
        src = (Path(__file__).resolve().parents[1]
               / 'bifrost/backoffice/tenant_routes.py').read_text()
        call = src.index('children_by_parent, reason_column = _load_children(')
        before, after = src[max(0, call - 400):call], src[call:call + 400]
        self.assertIn('try:', before,
                      "the child fetch must sit in its own try, not the schema handler's")
        self.assertIn('except Exception', after,
                      "a failed child fetch must degrade the drawer, not the page")
