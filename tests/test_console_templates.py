"""Every backoffice page renders, and DevTools is gated on db:execute.

These are the two things the console-wide redesign can break silently: a
template that only fails when a real request hits it, and a permission gate
that looks right in the sidebar but isn't enforced on the route.

Run:  .venv/bin/python tests/test_console_templates.py
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bson import ObjectId
from flask import render_template, session

from bifrost import create_app
from bifrost.backoffice import ROLE_PERMISSIONS, CONSOLE_ROLES, check_permission
from bifrost.backoffice.devtools_routes import _jsonable
from config import Config


APP_ID = ObjectId()
APP = {
    "_id": APP_ID,
    "app_name": "Ministry Exam Prep",
    "client_id": "ministry_exam_prep",
    "webhook_secret": "whsec_example",
    "app_web_url": "https://example.com",
    "app_callback_url": "https://example.com/cb",
    "app_qr_url": "https://example.com/qr.png",
    "api_keys": {"GEMINI_API_KEY": "secret-value"},
    "db_connection": "postgresql://u:p@h:5432/db",
}

SCHEMA = [
    {"column_name": "id", "data_type": "integer"},
    {"column_name": "title", "data_type": "text", "character_maximum_length": None},
    {"column_name": "status", "data_type": "character varying"},
]

# One context per template. Deliberately includes the awkward cases the real
# console hits: a None cell, an empty table list, a row whose text is hostile.
PAGES = {
    "dashboard.html": dict(apps=[APP], title="Applications"),
    "create_app.html": {},
    "login.html": dict(tenant_app=APP),
    "mfa.html": dict(email="admin@example.com"),
    "forgot_password.html": {},
    "reset_password.html": dict(email="admin@example.com"),
    "global_users.html": dict(users=[{
        "_id": ObjectId(), "email": "u@example.com",
        "display_name": "<script>alert(1)</script>", "auth_providers": ["email"],
    }], query=""),
    "global_api_keys.html": dict(apps=[APP]),
    "request_tenancy.html": dict(form={}),
    "tenant_requests.html": dict(
        pending=[{
            "_id": ObjectId(), "app_name": "Ministry Exam Prep",
            "admin_email": "owner@example.com", "created_at": datetime.now(timezone.utc),
            "web_url": "https://example.com", "callback_url": "", "api_url": "", "logo_url": "",
            "payments_enabled": "on", "pay_payway": "", "pay_manual": "on",
            "notes": "<script>alert(1)</script>",
        }],
        decided=[{
            "_id": ObjectId(), "app_name": "Old App", "admin_email": "x@example.com",
            "status": "rejected", "decision_reason": "duplicate", "client_id": None,
        }],
        current_role="heimdall",
    ),
    "audit_log.html": dict(
        app=APP, entries=[{
            "action": "DELETE", "table": "questions", "row_id": 3,
            "acting_user": "admin", "timestamp": datetime.now(timezone.utc).isoformat(),
            "before": {"title": "old"}, "after": None,
        }],
        current_role="owner", filter_table="", filter_actor="",
    ),
    "app_users.html": dict(
        app=APP, users=[{
            "account_id": ObjectId(), "email": "u@example.com",
            "display_name": "User", "role": "developer", "expires_at": None,
        }],
        owner={"display_name": "Owner"}, current_role="owner",
    ),
    "payment_queue.html": dict(
        app=APP, payments=[{
            "id": 1, "email": "p@example.com", "txn_ref": "TX1", "amount": 12,
            "currency": "USD", "status": "pending", "sla_state": "warn", "age_hours": 20,
        }],
        tracks=[{"id": 1, "ministry": "MoEYS", "name": "Grade 12"}],
        reject_reasons=["blurry_receipt"], refund_reasons=["duplicate"],
        sla_hours=24, can_approve=True, current_role="operations", status_filter="pending",
    ),
    "monitor.html": dict(current_role="heimdall"),
    "ai_metrics.html": dict(
        error=None, grand_input=10, grand_output=5, grand_requests=3, grand_cost=0.12,
        grand_models={"gemini-2.0": 15}, billing=None, dates=["2026-07-01"],
        projects=[{
            "label": "proj", "color": "rgba(1, 2, 3", "cost": 0.1,
            "input": 10, "output": 5, "requests": 3,
            "input_by_day": [10], "output_by_day": [5], "requests_by_day": [3],
        }],
        current_role="heimdall",
    ),
    "content_grid.html": dict(
        app=APP, tables=["questions"], table_groups={"Content": ["questions"]},
        selected_table="questions", columns=["id", "title", "status"],
        visible_columns=["id", "title", "status"],
        rows=[{"id": 1, "title": None, "status": "draft"}],
        schema_by_col={s["column_name"]: s for s in SCHEMA},
        table_col_config={}, readonly_table=False, can_write=True,
        cms_config={"tables": {}}, current_role="owner",
        page=1, limit=50, total_count=1, sort_by="id", sort_dir="desc",
        search_query=None, role_readonly_cols=[],
    ),
    "cms_config.html": dict(
        app=APP, all_tables=["questions"], table_schemas={"questions": SCHEMA},
        cms_config={"tables": {}, "roles": {}}, current_role="owner",
        locked_tables=["payments"],
    ),
    "cms_rbac.html": dict(
        app=APP, all_tables=["questions"], table_schemas={"questions": SCHEMA},
        cms_config={"roles": {"editor": {"tables": {}}}}, current_role="owner",
    ),
    "cms_onboarding.html": dict(
        app=APP, all_tables=["questions"], table_schemas={"questions": SCHEMA},
        smart_config={"hidden_tables": [], "tables": {"questions": {"label": "Questions"}}},
        current_role="owner",
    ),
    "devtools.html": dict(
        app=APP, tables=["questions"], db_configured=True,
        max_rows=500, timeout_seconds=15, current_role="developer",
    ),
}

# The empty-database branch of onboarding renders entirely different markup —
# it is the path that was broken by invalid form nesting, so it gets its own case.
PAGES_EXTRA = [
    ("cms_onboarding.html", dict(
        app=APP, all_tables=[], table_schemas={},
        smart_config={"hidden_tables": [], "tables": {}}, current_role="owner",
    )),
    ("content_grid.html", dict(
        app=APP, tables=[], table_groups={}, selected_table=None, columns=[],
        visible_columns=[], rows=[], schema_by_col={}, table_col_config={},
        readonly_table=False, can_write=False, cms_config={}, current_role="owner",
        page=1, limit=50, total_count=0, sort_by="id", sort_dir="desc",
        search_query=None, role_readonly_cols=[],
    )),
    ("ai_metrics.html", dict(error="BigQuery unavailable", current_role="heimdall")),
]


def test_public_docs_render():
    """The public docs page shares the token set, and documents SQL Studio."""
    app = create_app(Config)
    with app.test_request_context("/docs"):
        html = render_template("docs.html", version="1.4.0", date="2026-07-28")

    assert "valhalla.css" in html
    for expected in ("SQL Studio", "db:execute", "developer", "statement timeout",
                     "Commit changes"):
        assert expected in html, f"docs.html no longer mentions {expected!r}"
    for smell in ("Plus+Jakarta+Sans", "highlight.min.js", "on-surface-variant"):
        assert smell not in html, f"docs.html still carries {smell!r}"
    print("  ok  public docs on the shared token set, SQL Studio documented")


def test_every_page_renders():
    app = create_app(Config)
    cases = [(name, ctx) for name, ctx in PAGES.items()] + PAGES_EXTRA

    for name, context in cases:
        with app.test_request_context("/backoffice/"):
            session["backoffice_user"] = str(ObjectId())
            session["is_heimdall"] = True
            session["active_app_id"] = str(APP_ID)

            html = render_template(f"backoffice/{name}", **context)

            assert "valhalla.css" in html, f"{name} is not on the shared stylesheet"
            assert "<!DOCTYPE html>" in html, f"{name} produced no document"
            # The old console's five design languages, by their fingerprints.
            for smell in ("Plus+Jakarta+Sans", "bg-slate-", "glass-panel",
                          "on-surface-variant"):
                assert smell not in html, f"{name} still carries {smell!r}"
    print(f"  ok  {len(cases)} page renders")


def test_devtools_is_gated_on_db_execute():
    holders = [r for r, perms in ROLE_PERMISSIONS.items() if "db:execute" in perms]
    assert holders == ["developer"], f"db:execute leaked to {holders}"
    assert "developer" in CONSOLE_ROLES, "developer cannot sign in to the console"

    app = create_app(Config)

    # A platform admin gets raw SQL on a platform-owned tenant, and never on a
    # customer's. An app that does not resolve at all is treated as external —
    # the narrow answer is the safe one for an unknown tenant.
    # See tests/test_platform_scope.py for the full matrix.
    with app.test_request_context("/backoffice/"):
        session["is_heimdall"] = True
        assert check_permission(str(APP_ID), "db:execute") is False

    with app.test_request_context("/backoffice/"):
        session.clear()
        assert check_permission(str(APP_ID), "db:execute") is False

    routes = {r.endpoint: r for r in app.url_map.iter_rules()}
    assert "backoffice.devtools" in routes
    assert "backoffice.devtools_execute" in routes
    assert "POST" in routes["backoffice.devtools_execute"].methods
    print("  ok  db:execute held only by developer; both routes registered")


def test_jsonable_handles_postgres_types():
    from decimal import Decimal
    from datetime import date
    from uuid import UUID

    assert _jsonable(None) is None
    assert _jsonable(Decimal("1.50")) == 1.5
    assert _jsonable(date(2026, 7, 28)) == "2026-07-28"
    assert _jsonable(UUID(int=1)) == "00000000-0000-0000-0000-000000000001"
    assert _jsonable(b"abc") == "<3 bytes>"
    assert _jsonable({"a": [Decimal("2"), None]}) == {"a": [2.0, None]}
    print("  ok  _jsonable covers the types psycopg2 returns")


if __name__ == "__main__":
    test_every_page_renders()
    test_public_docs_render()
    test_devtools_is_gated_on_db_execute()
    test_jsonable_handles_postgres_types()
    print("all console template checks passed")
