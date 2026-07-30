"""Platform admins are not omnipotent over external tenants.

Internal tenant  -> a platform admin sees everything, as before.
External tenant  -> config, metrics and audit log only. No secrets, no CMS
                    content, no end-user records, no payments, no SQL.

    .venv/bin/python -m pytest tests/test_platform_scope.py -q
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

mongomock = pytest.importorskip("mongomock")

os.environ.setdefault('SECRET_KEY', 'test-secret')
os.environ.setdefault('JWT_SECRET_KEY', 'test-jwt-secret')
os.environ.setdefault('MONGO_URI', 'mongodb://localhost:27017/test')
os.environ.setdefault('EMAIL_PASSWORD', 'test')

from flask import Flask, session

from bifrost import backoffice
from bifrost.backoffice import (PLATFORM_EXTERNAL_PERMISSIONS, ROLE_PERMISSIONS,
                                check_permission, cms_full_access)
from bifrost.models import BifrostDB

# Everything a tenant owner can do. A platform admin must not reach all of it.
OWNER_PERMISSIONS = ROLE_PERMISSIONS['owner']
FORBIDDEN_ON_EXTERNAL = OWNER_PERMISSIONS - PLATFORM_EXTERNAL_PERMISSIONS


@pytest.fixture
def ctx(monkeypatch):
    client = mongomock.MongoClient()
    db = BifrostDB(client, 'test')

    app = Flask(__name__)
    app.config.update(SECRET_KEY='test-secret', DB_NAME='test', TESTING=True)
    monkeypatch.setattr(backoffice, 'get_db', lambda: db)

    internal = db.register_application("Our Product", "https://ours.test/cb",
                                       tenant_type="internal")
    external = db.register_application("Their Product", "https://theirs.test/cb",
                                       tenant_type="external")
    legacy = db.register_application("Unclassified", "https://legacy.test/cb")
    db.db.applications.update_one({"client_id": legacy['client_id']},
                                  {"$unset": {"tenant_type": ""}})

    ids = {k: str(db.get_app_by_client_id(v['client_id'])['_id'])
           for k, v in (('internal', internal), ('external', external), ('legacy', legacy))}
    return app, db, ids


def as_platform_admin(app):
    ctx_ = app.test_request_context()
    ctx_.push()
    session['is_heimdall'] = True
    session['backoffice_user'] = 'platform-staff'
    return ctx_


# ---------------------------------------------------------------------------

def test_internal_tenant_platform_admin_keeps_everything(ctx):
    app, _db, ids = ctx
    with as_platform_admin(app):
        for permission in sorted(OWNER_PERMISSIONS | {"db:execute"}):
            assert check_permission(ids['internal'], permission), permission


def test_external_tenant_platform_admin_is_limited(ctx):
    app, _db, ids = ctx
    with as_platform_admin(app):
        for permission in sorted(PLATFORM_EXTERNAL_PERMISSIONS):
            assert check_permission(ids['external'], permission), permission

        for permission in sorted(FORBIDDEN_ON_EXTERNAL):
            assert not check_permission(ids['external'], permission), permission


def test_the_dangerous_ones_specifically(ctx):
    """Named rather than set-derived, so these can't quietly drift into the
    allowlist without a test change."""
    app, _db, ids = ctx
    with as_platform_admin(app):
        for permission in ("view:secrets", "manage:secrets", "db:execute",
                           "payments:approve", "content:write", "manage:users",
                           "entitlements:override", "transfer:ownership",
                           "users:suspend"):
            assert not check_permission(ids['external'], permission), permission


def test_unclassified_tenant_is_treated_as_external(ctx):
    """A tenant nobody classified must get the narrow answer, never the wide one."""
    app, _db, ids = ctx
    with as_platform_admin(app):
        assert check_permission(ids['legacy'], "read:config")
        assert not check_permission(ids['legacy'], "view:secrets")
        assert not check_permission(ids['legacy'], "db:execute")


def test_legacy_numeric_levels_do_not_bypass_the_scope(ctx):
    """check_permission also accepts old integer levels; those must not become a
    blanket yes for platform admins on an external tenant."""
    app, _db, ids = ctx
    with as_platform_admin(app):
        assert check_permission(ids['internal'], 1)
        assert not check_permission(ids['external'], 1)
        assert not check_permission(ids['external'], 3)


def test_platform_level_pages_still_work(ctx):
    """No tenant in scope means the platform's own dashboard and intake queue."""
    app, _db, _ids = ctx
    with as_platform_admin(app):
        assert check_permission(None, "read:config")
        assert check_permission(None, "view:metrics")


def test_cms_full_access_follows_the_same_rule(ctx):
    app, _db, ids = ctx
    with as_platform_admin(app):
        assert cms_full_access(ids['internal'])
        assert not cms_full_access(ids['external'])
        assert not cms_full_access(ids['external'], roles=('owner', 'super_admin', 'admin'))


def test_tenant_owner_is_unaffected(ctx):
    """This change restricts platform staff, not the tenant's own people."""
    app, db, ids = ctx
    user_id = db.create_account({"client_id": "x", "email": "owner@theirs.test"})
    db.link_user_to_app(user_id, ids['external'], role="owner")

    with app.test_request_context():
        session['backoffice_user'] = str(user_id)
        assert check_permission(ids['external'], "view:secrets")
        assert check_permission(ids['external'], "payments:approve")
        assert cms_full_access(ids['external'])


# ---------------------------------------------------------------------------
# Cross-tenant Heimdall views
# ---------------------------------------------------------------------------

def test_global_user_views_exclude_external_tenants(ctx):
    from bifrost.backoffice.heimdall_routes import internal_directories, visible_account
    app, db, ids = ctx

    internal_app = db.db.applications.find_one({"_id": __import__('bson').ObjectId(ids['internal'])})
    external_app = db.db.applications.find_one({"_id": __import__('bson').ObjectId(ids['external'])})

    ours = db.create_account({"client_id": db.directory_scope(internal_app),
                              "email": "staff@ours.test"})
    theirs = db.create_account({"client_id": db.directory_scope(external_app),
                                "email": "customer@theirs.test"})

    with app.test_request_context():
        directories = internal_directories(db)
        assert db.directory_scope(internal_app) in directories
        assert db.directory_scope(external_app) not in directories

        assert visible_account(db, ours) is not None
        # Indistinguishable from "no such account", so the endpoint cannot be
        # used to enumerate a customer's users.
        assert visible_account(db, theirs) is None


# ---------------------------------------------------------------------------
# Locked tables are data, not source code
# ---------------------------------------------------------------------------

def test_locked_tables_come_from_the_app_document(ctx):
    from bifrost.backoffice.tenant_routes import locked_tables_for
    _app, db, ids = ctx
    import bson

    assert locked_tables_for({}) == []

    db.update_app_details(ids['external'],
                          {"platform_locked_tables": ["ledger", "transactions"]})
    app_doc = db.db.applications.find_one({"_id": bson.ObjectId(ids['external'])})
    assert locked_tables_for(app_doc) == ["ledger", "transactions"]


def test_no_hardcoded_lock_table_remains():
    from config import Config
    assert not hasattr(Config, 'PLATFORM_LOCKED_TABLES')


# ---------------------------------------------------------------------------
# Configurable role matrix (Layer A)
# ---------------------------------------------------------------------------

def test_absent_override_keeps_the_platform_default(ctx):
    from bifrost.backoffice import effective_role_permissions
    _app, db, ids = ctx
    import bson
    doc = db.db.applications.find_one({"_id": bson.ObjectId(ids['external'])})
    assert effective_role_permissions(doc, 'content_manager') == \
        ROLE_PERMISSIONS['content_manager']


def test_override_replaces_the_default_for_that_role_only(ctx):
    """A tenant wanting content_manager to publish is a console edit, not a release."""
    from bifrost.backoffice import effective_role_permissions
    _app, db, ids = ctx
    import bson

    db.update_app_details(ids['external'], {"role_permissions": {
        "content_manager": ["content:read", "content:write", "content:publish"]}})
    doc = db.db.applications.find_one({"_id": bson.ObjectId(ids['external'])})

    assert "content:publish" in effective_role_permissions(doc, 'content_manager')
    # Untouched roles are unaffected.
    assert effective_role_permissions(doc, 'operations') == ROLE_PERMISSIONS['operations']


def test_managed_database_tenant_cannot_grant_raw_sql(ctx):
    """db:execute against the platform's own Postgres stays platform-granted."""
    from bifrost.backoffice import effective_role_permissions
    _app, db, ids = ctx
    import bson

    # No db_connection and no db_mode -> managed, i.e. not the tenant's database.
    db.update_app_details(ids['external'],
                          {"role_permissions": {"developer": ["read:config", "db:execute"]}})
    doc = db.db.applications.find_one({"_id": bson.ObjectId(ids['external'])})
    assert db.owns_its_database(doc) is False
    assert "db:execute" not in effective_role_permissions(doc, 'developer')
    assert "read:config" in effective_role_permissions(doc, 'developer')


def test_byodb_tenant_may_grant_raw_sql_over_its_own_database(ctx):
    """It is their database and their credentials — they can reach it without
    Bifrost, so gatekeeping it in the console would protect nothing."""
    from bifrost.backoffice import effective_role_permissions
    _app, db, ids = ctx
    import bson

    db.update_app_details(ids['external'], {
        "db_mode": "custom",
        "db_connection": "postgresql://user:pw@their-host:5432/theirs",
        "role_permissions": {"developer": ["read:config", "db:execute"]},
    })
    doc = db.db.applications.find_one({"_id": bson.ObjectId(ids['external'])})
    assert db.owns_its_database(doc) is True
    assert "db:execute" in effective_role_permissions(doc, 'developer')


def test_override_is_enforced_through_check_permission(ctx):
    app, db, ids = ctx
    user = db.create_account({"client_id": "x", "email": "cm@theirs.test"})
    db.link_user_to_app(user, ids['internal'], role="content_manager")

    with app.test_request_context():
        session['backoffice_user'] = str(user)
        assert check_permission(ids['internal'], "content:publish") is False

    db.update_app_details(ids['internal'], {"role_permissions": {
        "content_manager": ["content:read", "content:write", "content:publish"]}})

    with app.test_request_context():
        session['backoffice_user'] = str(user)
        assert check_permission(ids['internal'], "content:publish") is True


def test_an_emptied_role_loses_everything(ctx):
    """An explicit empty list is a decision, not a fall-through to the default."""
    from bifrost.backoffice import effective_role_permissions
    _app, db, ids = ctx
    import bson
    db.update_app_details(ids['external'], {"role_permissions": {"operations": []}})
    doc = db.db.applications.find_one({"_id": bson.ObjectId(ids['external'])})
    assert effective_role_permissions(doc, 'operations') == set()
