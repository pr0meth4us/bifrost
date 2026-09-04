# bifrost/backoffice/review_routes.py
"""One screen per record under review, parent row and children together.

Why this exists rather than a column in the grid: the grid renders one table at a
time, so a reviewer attesting that the marked-correct child is correct is ticking
a box about rows they cannot see. That is worse than no box — it turns an
unreviewed record into one carrying a signed attestation.

Entirely driven by `cms_config.review_queue`. No block, no queue, no route.
"""
from flask import render_template, request, redirect, url_for, flash, session, abort
from bson import ObjectId

from . import backoffice_bp, get_db, login_required, check_permission, get_current_role_in_app
from .tenant_routes import get_tenant_db_conn_str
from ..models import cms_mongo
from ..models.review_queue import ReviewSchema, load_item, next_ids, pending_count, submit


def _queue_for(db, app_id):
    """(app, conn_str, schema). Any of them None means the queue cannot run."""
    app = db.db.applications.find_one({"_id": ObjectId(app_id)})
    if not app:
        return None, None, None
    try:
        schema = ReviewSchema.from_config(db.get_cms_config(app_id))
    except (ValueError, TypeError) as e:
        flash(f"Invalid review_queue config — fix it in CMS settings: {e}", "danger")
        return app, None, None
    return app, get_tenant_db_conn_str(app), schema


def _reason_column(cur, schema):
    """First configured reason column that exists. None -> rejection records no reason."""
    from ..models.queue_schema import _table_columns
    present = _table_columns(cur, schema.table)
    return next((c for c in schema.reject_reason if c in present), None)


@backoffice_bp.route('/app/<app_id>/review')
@backoffice_bp.route('/app/<app_id>/review/<row_id>')
@login_required
def review_queue(app_id, row_id=None):
    db = get_db()
    if not check_permission(app_id, "content:read"):
        abort(403, description="No access to content in this application.")

    app, db_conn_str, schema = _queue_for(db, app_id)
    if not app:
        flash("Application not found.", "danger")
        return redirect(url_for('backoffice.dashboard'))
    if not schema:
        flash("No review queue is configured for this application.", "warning")
        return redirect(url_for('backoffice.view_app'))
    if not db_conn_str or cms_mongo.handles(db_conn_str):
        flash("The review queue is available for PostgreSQL tenants only.", "warning")
        return redirect(url_for('backoffice.view_app'))

    from ..utils.tenant_db import get_tenant_db
    parent, children, remaining, queue, reason_column = None, [], 0, [], None
    try:
        with get_tenant_db(db_conn_str) as conn:
            with conn.cursor() as cur:
                queue = next_ids(cur, schema)
                remaining = pending_count(cur, schema)
                # No row named: take the head of the queue.
                target = row_id if row_id is not None else (queue[0] if queue else None)
                if target is not None:
                    parent, children = load_item(cur, schema, target)
                reason_column = _reason_column(cur, schema)
    except Exception as e:
        flash(f"Error querying tenant database: {e}", "danger")

    return render_template(
        'backoffice/review_queue.html',
        app=app, schema=schema, parent=parent, children=children,
        remaining=remaining, queue=[str(q) for q in queue],
        reason_column=reason_column,
        # Approving is a publish. Content Managers work the queue and tick the
        # boxes; only content:publish turns that into a published record.
        can_approve=check_permission(app_id, "content:publish"),
        can_review=check_permission(app_id, "content:write"),
        current_role=get_current_role_in_app(app_id),
    )


@backoffice_bp.route('/app/<app_id>/review/<row_id>/submit', methods=['POST'])
@login_required
def submit_review(app_id, row_id):
    db = get_db()
    decision = request.form.get('decision', 'approve')
    needed = "content:publish" if decision == 'approve' else "content:write"
    if not check_permission(app_id, needed):
        abort(403, description="Not permitted to make that review decision.")

    app, db_conn_str, schema = _queue_for(db, app_id)
    if not (app and schema and db_conn_str) or cms_mongo.handles(db_conn_str):
        flash("The review queue is not available for this application.", "danger")
        return redirect(url_for('backoffice.dashboard'))

    ticked = {c for c in schema.controls if request.form.get(c)}
    actor = session.get('backoffice_user', 'unknown')

    from ..utils.tenant_db import get_tenant_db
    try:
        with get_tenant_db(db_conn_str) as conn:
            with conn.cursor() as cur:
                reason_column = _reason_column(cur, schema)
            ok, result = submit(conn, schema, row_id, ticked, decision, actor,
                                reason=request.form.get('reason'),
                                reason_column=reason_column)
    except Exception as e:
        flash(f"Review failed: {e}", "danger")
        return redirect(url_for('backoffice.review_queue', app_id=app_id, row_id=row_id))

    if not ok:
        flash(result, "danger")
        return redirect(url_for('backoffice.review_queue', app_id=app_id, row_id=row_id))

    db.log_cms_mutation(app_id, schema.table, f"REVIEW_{decision.upper()}", str(row_id),
                        actor, _jsonable(result), {"decision": decision,
                                                   "ticked": sorted(ticked)})
    flash(f"Review recorded: {decision}d.", "success")
    # Straight to the next item — working a queue, not hunting rows in a grid.
    nxt = request.form.get('next') or None
    if nxt:
        return redirect(url_for('backoffice.review_queue', app_id=app_id, row_id=nxt))
    return redirect(url_for('backoffice.review_queue', app_id=app_id))


def _jsonable(d):
    """Mongo cannot store Decimal, datetime or UUID; the audit log must not fail the review."""
    from datetime import datetime, date
    from decimal import Decimal
    out = {}
    for k, v in (d or {}).items():
        if isinstance(v, Decimal):
            out[k] = float(v)
        elif isinstance(v, (datetime, date)):
            out[k] = v.isoformat()
        elif isinstance(v, (str, int, float, bool, type(None))):
            out[k] = v
        else:
            out[k] = str(v)
    return out
