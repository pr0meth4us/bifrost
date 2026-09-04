"""The CMS signs review columns itself; the client cannot forge them."""
import sys, types, unittest
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bifrost.models.payments import PaymentMixin


class FakeCursor:
    def __init__(self, calls):
        self.calls, self.description = calls, [('id',)]

    def __enter__(self): return self
    def __exit__(self, *a): return False

    def execute(self, sql, params=None):
        self.calls.append((sql, params))

    def fetchone(self): return (1,)


class FakeConn:
    def __init__(self, calls): self.calls = calls
    def cursor(self): return FakeCursor(self.calls)
    def commit(self): pass


class DB(PaymentMixin):
    def __init__(self, columns):
        self.columns, self.calls = columns, []
        self.db = types.SimpleNamespace(cms_audit_log=types.SimpleNamespace(insert_one=lambda d: None))

    def get_tenant_table_schema(self, conn_str, table):
        return [{'column_name': c} for c in self.columns]


def save(columns, data, acting_user='reviewer@example.com'):
    db = DB(columns)

    @contextmanager
    def fake_get_tenant_db(conn_str):
        yield FakeConn(db.calls)

    import bifrost.utils.tenant_db as tdb
    tdb.get_tenant_db = fake_get_tenant_db
    db.save_tenant_table_row('postgres://x', 'questions', 1, data,
                             app_id='a1', acting_user=acting_user)
    update = [c for c in db.calls if c[0].startswith('UPDATE')]
    return update[0] if update else (None, None)


class TestReviewStamp(unittest.TestCase):
    COLS = ('id', 'status', 'reviewed_by', 'reviewed_at')

    def test_stamps_reviewer_from_session(self):
        sql, params = save(self.COLS, {'status': 'published'})
        self.assertIn('"reviewed_by" = %s', sql)
        self.assertIn('"reviewed_at" = %s', sql)
        self.assertIn('reviewer@example.com', params)

    def test_client_cannot_forge_reviewer(self):
        sql, params = save(self.COLS, {'status': 'published',
                                       'reviewed_by': 'somebody-else'})
        self.assertEqual(sql.count('"reviewed_by" = %s'), 1)
        self.assertNotIn('somebody-else', params)
        self.assertIn('reviewer@example.com', params)

    def test_untouched_when_table_has_no_review_columns(self):
        sql, params = save(('id', 'status'), {'status': 'published'})
        self.assertNotIn('reviewed_by', sql)

    def test_no_update_when_nothing_to_set(self):
        sql, _ = save(self.COLS, {'id': 1}, acting_user=None)
        self.assertIsNone(sql)


if __name__ == '__main__':
    unittest.main()
