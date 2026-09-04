# bifrost/backoffice/devtools_routes.py
"""SQL Studio — raw SQL against a tenant's PostgreSQL database.

This is the most dangerous surface in the console: `DROP TABLE users CASCADE` is
one keystroke away, and no amount of statement parsing would make that safe. So
the safety story is deliberately NOT "block scary statements" — a blocklist is
trivially bypassed (`DO $$ ... $$`, `EXECUTE`, comments) and lulls people into
trusting it. Instead:

  * a dedicated `db:execute` permission that is in exactly one role (`developer`)
    and has to be granted by hand — see ROLE_PERMISSIONS in __init__.py;
  * every statement is written to the audit log BEFORE it runs, so a query that
    takes the database down still leaves a record of who ran what;
  * a statement timeout, because the tenant pool is 8 connections wide and one
    runaway scan would otherwise take the whole console down for that tenant;
  * writes commit only when the caller ticks the box. A stray SELECT can't be an
    accident, an uncommitted UPDATE can.
"""
from flask import render_template, request, jsonify, session
from bson import ObjectId

from . import backoffice_bp, get_db, requires, get_current_role_in_app, acting_identity
from .tenant_routes import get_tenant_db_conn_str

# A query slower than this is a mistake, not a report. Keeps one bad scan from
# holding a connection out of the MAX_CONNECTIONS=8 tenant pool (see tenant_db.py).
STATEMENT_TIMEOUT_MS = 15_000

# Enough to eyeball a result, small enough that SELECT * on a million-row table
# doesn't try to serialise the whole thing into a JSON response.
MAX_ROWS = 500


@backoffice_bp.route('/app/<app_id>/devtools')
@requires("db:execute")
def devtools(app_id):
    db = get_db()
    app = db.db.applications.find_one({"_id": ObjectId(app_id)})
    if not app:
        return "Application not found.", 404

    tables = []
    conn_str = get_tenant_db_conn_str(app)
    if conn_str:
        # Best-effort: the editor is still usable if the schema listing fails,
        # and the error will be far more legible once they run a query.
        try:
            tables = db.get_tenant_tables(conn_str)
        except Exception:
            tables = []

    return render_template(
        'backoffice/devtools.html',
        app=app,
        tables=tables,
        db_configured=bool(conn_str),
        max_rows=MAX_ROWS,
        timeout_seconds=STATEMENT_TIMEOUT_MS // 1000,
        current_role=get_current_role_in_app(app_id),
    )


@backoffice_bp.route('/api/app/<app_id>/devtools/execute', methods=['POST'])
@requires("db:execute")
def devtools_execute(app_id):
    db = get_db()
    app = db.db.applications.find_one({"_id": ObjectId(app_id)})
    if not app:
        return jsonify({"error": "Application not found."}), 404

    conn_str = get_tenant_db_conn_str(app)
    if not conn_str:
        return jsonify({"error": "Tenant database connection is not configured."}), 400

    payload = request.get_json(silent=True) or {}
    sql = (payload.get('sql') or '').strip()
    commit = bool(payload.get('commit'))
    if not sql:
        return jsonify({"error": "No SQL supplied."}), 400

    actor = acting_identity()

    # Logged BEFORE execution on purpose: a statement that hangs the database or
    # kills the process must still leave a trace of who ran it.
    db.log_audit(app_id, "__devtools__", "sql:execute", None, actor,
                 before=None, after={"sql": sql, "commit": commit})

    from ..utils.tenant_db import get_tenant_db
    import time

    started = time.monotonic()
    try:
        # Pooled connections are already autocommit=False, so the first execute
        # opens a transaction — which is what makes SET LOCAL scoped to this query
        # and what lets us throw the work away below. Don't touch conn.autocommit:
        # flipping it while a transaction is open is itself an error, and would
        # mask the real one on the failure path.
        with get_tenant_db(conn_str) as conn:
            with conn.cursor() as cur:
                cur.execute(f"SET LOCAL statement_timeout = {STATEMENT_TIMEOUT_MS}")
                cur.execute(sql)

                # cursor.description is None for anything that returns no rows
                # (DDL, INSERT without RETURNING), which is how we tell the two
                # result shapes apart without parsing the SQL ourselves.
                if cur.description is None:
                    result = {
                        "kind": "command",
                        "affected_rows": cur.rowcount,
                        "message": cur.statusmessage,
                    }
                else:
                    columns = [d[0] for d in cur.description]
                    fetched = cur.fetchmany(MAX_ROWS + 1)
                    truncated = len(fetched) > MAX_ROWS
                    rows = [[_jsonable(v) for v in row] for row in fetched[:MAX_ROWS]]
                    result = {
                        "kind": "rows",
                        "columns": columns,
                        "rows": rows,
                        "row_count": len(rows),
                        "truncated": truncated,
                    }

            if commit:
                conn.commit()
                result["committed"] = True
            else:
                # Nothing is written unless it was asked for explicitly. A
                # forgotten checkbox costs a re-run; a forgotten WHERE clause
                # that auto-commits costs the table.
                conn.rollback()
                result["committed"] = False
    except Exception as e:
        # psycopg2 errors carry the position/hint the developer actually needs,
        # and this endpoint is already behind db:execute — so return the real text
        # rather than a sanitised "query failed".
        return jsonify({
            "error": str(e).strip(),
            "elapsed_ms": round((time.monotonic() - started) * 1000, 1),
        }), 400

    result["elapsed_ms"] = round((time.monotonic() - started) * 1000, 1)
    return jsonify(result)


def _jsonable(value):
    """Postgres hands back types json can't encode — dates, Decimal, memoryview."""
    from datetime import date, time as _time, datetime
    from decimal import Decimal
    from uuid import UUID

    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (datetime, date, _time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (bytes, memoryview)):
        return f"<{len(bytes(value))} bytes>"
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    return str(value)
