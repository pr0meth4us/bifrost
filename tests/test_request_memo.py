"""Schema introspection is memoized per request, and row ids are not coerced.

Both were measured problems, not hypotheticals: one grid load ran the
information_schema join twice and fetched the same Mongo document eight times,
and a UUID row id raised inside the save path.
"""
import sys, types, unittest
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flask import Flask
from bifrost.models.payments import PaymentMixin

app = Flask(__name__)


class FakeCursor:
    def __init__(self, owner): self.owner, self.description = owner, [('id',)]
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def execute(self, sql, params=None):
        self.owner.queries.append((sql, params))
    def fetchone(self): return (1,)
    def fetchall(self): return [('questions',), ('choices',)]


class FakeConn:
    def __init__(self, owner): self.owner = owner
    def cursor(self): return FakeCursor(self.owner)
    def commit(self): pass


class DB(PaymentMixin):
    def __init__(self):
        self.queries, self.mongo_reads = [], 0
        outer = self

        class Apps:
            def find_one(self, *a, **k):
                outer.mongo_reads += 1
                return {"cms_config": {"tables": {}}}

        self.db = types.SimpleNamespace(
            applications=Apps(),
            cms_audit_log=types.SimpleNamespace(insert_one=lambda d: None))

    def _get_tenant_table_schema(self, conn_str, table):
        self.queries.append(('SCHEMA', table))
        return [{'column_name': c} for c in ('id', 'status', 'stem_kh')]


@contextmanager
def _fake_tenant_db(db):
    @contextmanager
    def factory(conn_str):
        yield FakeConn(db)
    import bifrost.utils.tenant_db as tdb
    original = tdb.get_tenant_db
    tdb.get_tenant_db = factory
    try:
        yield
    finally:
        tdb.get_tenant_db = original


class TestRequestMemo(unittest.TestCase):
    def test_schema_is_introspected_once_per_request(self):
        db = DB()
        with app.test_request_context('/'):
            for _ in range(5):
                db.get_tenant_table_schema('postgres://x', 'questions')
        self.assertEqual([q for q in db.queries if q[0] == 'SCHEMA'], [('SCHEMA', 'questions')])

    def test_different_tables_are_cached_separately(self):
        db = DB()
        with app.test_request_context('/'):
            db.get_tenant_table_schema('postgres://x', 'questions')
            db.get_tenant_table_schema('postgres://x', 'choices')
        self.assertEqual(len([q for q in db.queries if q[0] == 'SCHEMA']), 2)

    def test_cache_does_not_leak_between_requests(self):
        db = DB()
        for _ in range(2):
            with app.test_request_context('/'):
                db.get_tenant_table_schema('postgres://x', 'questions')
        self.assertEqual(len([q for q in db.queries if q[0] == 'SCHEMA']), 2)

    def test_cms_config_is_fetched_once_per_request(self):
        db = DB()
        with app.test_request_context('/'):
            for _ in range(8):
                db.get_cms_config('64b8f0000000000000000000')
        self.assertEqual(db.mongo_reads, 1)

    def test_a_save_invalidates_the_config_memo(self):
        db = DB()
        db.save_cms_config = PaymentMixin.save_cms_config.__get__(db)
        with app.test_request_context('/'):
            db.get_cms_config('64b8f0000000000000000000')
            db.db.applications.update_one = lambda *a, **k: None
            db.save_cms_config('64b8f0000000000000000000', {'tables': {'q': {}}})
            db.get_cms_config('64b8f0000000000000000000')
        self.assertEqual(db.mongo_reads, 2)

    def test_works_outside_a_request_context(self):
        # The scheduler calls these with no request in flight.
        db = DB()
        db.get_tenant_table_schema('postgres://x', 'questions')
        db.get_tenant_table_schema('postgres://x', 'questions')
        self.assertEqual(len([q for q in db.queries if q[0] == 'SCHEMA']), 2)


class TestRowIdNotCoerced(unittest.TestCase):
    UUID = 'f06eaf4e-a4ca-40c0-b1e2-c244acb068ad'

    def test_save_accepts_a_uuid_row_id(self):
        db = DB()
        with _fake_tenant_db(db), app.test_request_context('/'):
            db.save_tenant_table_row('postgres://x', 'questions', self.UUID,
                                     {'status': 'published'})
        update = [q for q in db.queries if str(q[0]).startswith('UPDATE')]
        self.assertTrue(update, "no UPDATE ran — the id was rejected before the query")
        self.assertIn(self.UUID, update[0][1])

    def test_delete_accepts_a_uuid_row_id(self):
        db = DB()
        with _fake_tenant_db(db), app.test_request_context('/'):
            db.delete_tenant_table_row('postgres://x', 'questions', self.UUID)
        deletes = [q for q in db.queries if str(q[0]).startswith('DELETE')]
        self.assertTrue(deletes)
        self.assertIn(self.UUID, deletes[0][1])


if __name__ == '__main__':
    unittest.main()
