# bifrost/backoffice/review_routes.py
"""The review decision endpoint.

The review UI itself lives in the CMS grid's drawer: the defect prolong reported
— a reviewer cannot see the child rows they are attesting about — is a CMS
problem, not a review-specific one, and anyone editing a record has the same
blindness. Solving it with a second screen would have meant two places to look
at the same data, which is what a CMS is supposed to prevent.

What stays here is the decision itself, because a review is not an ordinary
save: it enforces the all-ticked gate and writes the attestation. Driven by
`cms_config.review_queue`; no block, no endpoint worth reaching.
"""
from flask import request, redirect, url_for, flash, abort
from bson import ObjectId

from . import (backoffice_bp, get_db, login_required, check_permission,
               get_current_role_in_app, acting_identity)
from .tenant_routes import get_tenant_db_conn_str
from ..models import cms_mongo
from ..models.review_queue import ReviewSchema, submit


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
    actor = acting_identity()

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
        return redirect(url_for('backoffice.view_cms_grid', app_id=app_id, table=schema.table))

    if not ok:
        flash(result, "danger")
        return redirect(url_for('backoffice.view_cms_grid', app_id=app_id, table=schema.table))

    db.log_cms_mutation(app_id, schema.table, f"REVIEW_{decision.upper()}", str(row_id),
                        actor, _jsonable(result), {"decision": decision,
                                                   "ticked": sorted(ticked)})
    flash(f"Review recorded: {decision}d.", "success")
    # Straight to the next item — working a queue, not hunting rows in a grid.
    return redirect(url_for('backoffice.view_cms_grid', app_id=app_id,
                            table=schema.table, status=request.form.get('status_filter')))


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
