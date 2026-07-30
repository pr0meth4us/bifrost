"""The Mongo CMS backend, and the write-path controls that used to be cosmetic.

Two things under test:

  1. `cms_mongo` mirrors the Postgres CMS contract against a Mongo tenant —
     discovery, inferred schema, paging, search, and typed writes.
  2. Locked tables and hidden columns are enforced when saving, not only when
     rendering. Both previously filtered the screen and nothing else.

    .venv/bin/python -m pytest tests/test_cms_mongo.py -q
"""
import os
import sys
from datetime import datetime

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

mongomock = pytest.importorskip("mongomock")

os.environ.setdefault('SECRET_KEY', 'test-secret')
os.environ.setdefault('JWT_SECRET_KEY', 'test-jwt-secret')
os.environ.setdefault('MONGO_URI', 'mongodb://localhost:27017/test')
os.environ.setdefault('EMAIL_PASSWORD', 'test')

from bson import ObjectId
from flask import Flask, session

from bifrost import backoffice
from bifrost.models import cms_mongo
from bifrost.models import BifrostDB

TENANT_URI = "mongodb://tenant-host/savvify"


@pytest.fixture
def tenant(monkeypatch):
    """A Mongo tenant holding a small finance-shaped collection."""
    client = mongomock.MongoClient()
    monkeypatch.setattr(cms_mongo, '_database', lambda _uri: client['savvify'])

    client['savvify'].transactions.insert_many([
        {"user_id": 1, "amount": 12.50, "note": "coffee",
         "settled": True, "created_at": datetime(2026, 1, 5)},
        {"user_id": 1, "amount": 900.00, "note": "rent",
         "settled": True, "created_at": datetime(2026, 1, 1)},
        {"user_id": 2, "amount": 4.25, "note": "bus fare",
         "settled": False, "created_at": datetime(2026, 1, 7), "tags": ["transit"]},
    ])
    client['savvify'].users.insert_one({"email": "a@b.test"})
    return client['savvify']


# ---------------------------------------------------------------------------
# Backend selection
# ---------------------------------------------------------------------------

def test_backend_is_chosen_by_connection_string():
    assert cms_mongo.handles("mongodb://host/db")
    assert cms_mongo.handles("mongodb+srv://user:pw@host/db")
    assert not cms_mongo.handles("postgresql://user:pw@host:5432/db")
    assert not cms_mongo.handles(None)


def test_mongo_uri_without_a_database_is_rejected():
    with pytest.raises(ValueError, match="must name a database"):
        cms_mongo._database("mongodb://host:27017")


# ---------------------------------------------------------------------------
# Discovery and inferred schema
# ---------------------------------------------------------------------------

def test_collections_are_listed_as_tables(tenant):
    assert cms_mongo.get_tenant_tables(TENANT_URI) == ["transactions", "users"]


def test_schema_is_inferred_from_documents(tenant):
    schema = {c['column_name']: c for c in
              cms_mongo.get_tenant_table_schema(TENANT_URI, "transactions")}

    assert schema['id']['data_type'] == 'objectid'   # _id surfaces as id
    assert '_id' not in schema
    assert schema['user_id']['data_type'] == 'integer'
    assert schema['amount']['data_type'] == 'double precision'
    assert schema['note']['data_type'] == 'text'
    assert schema['settled']['data_type'] == 'boolean'
    assert schema['created_at']['data_type'] == 'timestamp without time zone'
    assert schema['tags']['data_type'] == 'jsonb'

    # Present on one document out of three, so nullable.
    assert schema['tags']['is_nullable'] == 'YES'


def test_empty_collection_still_reports_an_identity_column(tenant):
    tenant.create_collection("audit")
    schema = cms_mongo.get_tenant_table_schema(TENANT_URI, "audit")
    assert [c['column_name'] for c in schema] == ['id']


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def test_rows_come_back_json_safe_with_an_id_the_grid_can_link(tenant):
    columns, rows, total = cms_mongo.get_tenant_table_data(TENANT_URI, "transactions")

    assert total == 3
    assert columns[0] == 'id'
    for row in rows:
        # content_grid.html builds its edit/delete URLs from row.id
        assert isinstance(row['id'], str)
        assert '_id' not in row
        assert isinstance(row['created_at'], str)   # ISO, not a datetime
    assert isinstance(rows[0]['tags'], str) or 'tags' not in rows[0]


def test_paging_and_sorting(tenant):
    _, page1, total = cms_mongo.get_tenant_table_data(
        TENANT_URI, "transactions", limit=2, offset=0, sort_by="amount", sort_dir="asc")
    _, page2, _ = cms_mongo.get_tenant_table_data(
        TENANT_URI, "transactions", limit=2, offset=2, sort_by="amount", sort_dir="asc")

    assert total == 3
    assert [r['amount'] for r in page1] == [4.25, 12.50]
    assert [r['amount'] for r in page2] == [900.00]


def test_search_matches_text_fields_only(tenant):
    _, rows, _ = cms_mongo.get_tenant_table_data(TENANT_URI, "transactions",
                                                 search_query="rent")
    assert [r['note'] for r in rows] == ["rent"]


# ---------------------------------------------------------------------------
# Writing — the part that matters for a finance collection
# ---------------------------------------------------------------------------

def test_form_strings_are_coerced_to_the_collections_types(tenant):
    """A posted "42" must not land in a numeric field as the string "42"."""
    doc = tenant.transactions.find_one({"note": "coffee"})

    before, after = cms_mongo.update_row(TENANT_URI, "transactions", str(doc['_id']),
                                         {"amount": "99.95", "user_id": "7",
                                          "settled": "false", "note": "lunch"})

    stored = tenant.transactions.find_one({"_id": doc['_id']})
    assert stored['amount'] == 99.95 and isinstance(stored['amount'], float)
    assert stored['user_id'] == 7 and isinstance(stored['user_id'], int)
    assert stored['settled'] is False
    assert stored['note'] == "lunch"

    # Audit payload carries both sides, JSON-safe.
    assert before['amount'] == 12.50 and after['amount'] == 99.95
    assert isinstance(after['id'], str)


def test_uncastable_values_are_kept_not_dropped(tenant):
    doc = tenant.transactions.find_one({"note": "rent"})
    cms_mongo.update_row(TENANT_URI, "transactions", str(doc['_id']),
                         {"amount": "not-a-number"})
    assert tenant.transactions.find_one({"_id": doc['_id']})['amount'] == "not-a-number"


def test_empty_form_value_becomes_null(tenant):
    doc = tenant.transactions.find_one({"note": "rent"})
    cms_mongo.update_row(TENANT_URI, "transactions", str(doc['_id']), {"note": ""})
    assert tenant.transactions.find_one({"_id": doc['_id']})['note'] is None


def test_insert_and_delete_round_trip(tenant):
    created = cms_mongo.insert_row(TENANT_URI, "transactions",
                                   {"user_id": "3", "amount": "10.00", "note": "book"})
    assert tenant.transactions.count_documents({}) == 4
    assert created['user_id'] == 3

    deleted = cms_mongo.delete_row(TENANT_URI, "transactions", created['id'])
    assert deleted['note'] == "book"
    assert tenant.transactions.count_documents({}) == 3


def test_id_is_never_overwritten_by_a_form_field(tenant):
    doc = tenant.transactions.find_one({"note": "coffee"})
    cms_mongo.update_row(TENANT_URI, "transactions", str(doc['_id']),
                         {"id": str(ObjectId()), "note": "still here"})
    assert tenant.transactions.find_one({"_id": doc['_id']})['note'] == "still here"


def test_missing_row_raises_rather_than_silently_doing_nothing(tenant):
    with pytest.raises(ValueError):
        cms_mongo.update_row(TENANT_URI, "transactions", str(ObjectId()), {"note": "x"})
    with pytest.raises(ValueError):
        cms_mongo.delete_row(TENANT_URI, "transactions", str(ObjectId()))


def test_non_objectid_keys_still_work(tenant):
    tenant.settings.insert_one({"_id": "currency", "value": "USD"})
    _, rows, _ = cms_mongo.get_tenant_table_data(TENANT_URI, "settings")
    assert rows[0]['id'] == "currency"

    cms_mongo.update_row(TENANT_URI, "settings", "currency", {"value": "KHR"})
    assert tenant.settings.find_one({"_id": "currency"})['value'] == "KHR"


# ---------------------------------------------------------------------------
# Write-path enforcement (was display-only)
# ---------------------------------------------------------------------------

@pytest.fixture
def console(monkeypatch):
    client = mongomock.MongoClient()
    db = BifrostDB(client, 'test')
    app = Flask(__name__)
    app.config.update(SECRET_KEY='test-secret', DB_NAME='test', TESTING=True)
    monkeypatch.setattr(backoffice, 'get_db', lambda: db)

    creds = db.register_application("Fin", "https://fin.test/cb", tenant_type="internal")
    app_doc = db.get_app_by_client_id(creds['client_id'])
    app_id = str(app_doc['_id'])
    db.update_app_details(app_id, {"platform_locked_tables": ["ledger", "transactions"]})

    owner = db.create_account({"client_id": "fin", "email": "owner@fin.test"})
    db.link_user_to_app(owner, app_id, role="owner")
    return app, db, app_id, str(owner)


def test_locked_table_refuses_writes_from_the_owner(console):
    """Hiding it from the table list was never enough — a hand-made POST wrote."""
    from bifrost.backoffice.tenant_routes import check_cms_write_permission
    app, db, app_id, owner = console

    with app.test_request_context():
        session['backoffice_user'] = owner
        assert check_cms_write_permission(db, app_id, "ledger") is False
        assert check_cms_write_permission(db, app_id, "transactions") is False
        assert check_cms_write_permission(db, app_id, "notes") is True


def test_locked_table_refuses_writes_from_a_platform_admin(console):
    from bifrost.backoffice.tenant_routes import check_cms_write_permission
    app, db, app_id, _owner = console

    with app.test_request_context():
        session['is_heimdall'] = True
        session['backoffice_user'] = 'platform-staff'
        assert check_cms_write_permission(db, app_id, "ledger") is False


def test_hidden_columns_are_stripped_for_the_role_that_cannot_see_them(console):
    from bifrost.backoffice.tenant_routes import hidden_columns_for
    app, db, app_id, _owner = console

    db.save_cms_config(app_id, {"roles": {"operations": {"tables": {
        "invoices": {"permissions": ["read", "write"], "hidden_columns": ["amount"]}}}}})

    agent = db.create_account({"client_id": "fin", "email": "ops@fin.test"})
    db.link_user_to_app(agent, app_id, role="operations")

    with app.test_request_context():
        session['backoffice_user'] = str(agent)
        assert hidden_columns_for(db, app_id, "invoices") == {"amount"}

    # An owner sees everything, so nothing is stripped from their save.
    owner = db.db.app_links.find_one({"app_specific_role": "owner"})
    with app.test_request_context():
        session['backoffice_user'] = str(owner['account_id'])
        assert hidden_columns_for(db, app_id, "invoices") == set()
