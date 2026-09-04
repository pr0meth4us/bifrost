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
from flask import request, redirect, url_for, flash, abort, jsonify, session
from bson import ObjectId

from . import (backoffice_bp, get_db, login_required, check_permission,
               get_current_role_in_app, acting_identity)
from .tenant_routes import get_tenant_db_conn_str
from ..models import cms_mongo
from ..models.review_queue import (ReviewSchema, children_for, clear_spans,
                                   next_awaiting, pending_count, restore, submit)
import logging

log = logging.getLogger(__name__)


def _wants_json():
    """The drawer submits with fetch and stays put; a plain form post navigates.

    Both paths are kept: the drawer is JavaScript, and a review that only works
    with JavaScript is a review that stops working the day a CSP header changes.
    """
    return request.headers.get('X-Requested-With') == 'fetch'


def _queue_for(db, app_id):
    """(app, conn_str, schema). Any of them None means the queue cannot run."""
    app = db.db.applications.find_one({"_id": ObjectId(app_id)})
    if not app:
        return None, None, None
    try:
        schema = ReviewSchema.from_config(db.get_cms_config(app_id))
    except (ValueError, TypeError) as e:
        log.exception("_queue_for failed")
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
        log.exception("submit_review failed")
        flash(f"Review failed: {e}", "danger")
        return redirect(url_for('backoffice.view_cms_grid', app_id=app_id, table=schema.table))

    if not ok:
        if _wants_json():
            return jsonify(error=result), 400
        flash(result, "danger")
        return redirect(url_for('backoffice.view_cms_grid', app_id=app_id, table=schema.table))

    db.log_cms_mutation(app_id, schema.table, f"REVIEW_{decision.upper()}", str(row_id),
                        actor, _jsonable(result), {"decision": decision,
                                                   "ticked": sorted(ticked)})
    # Kept for undo. Server-read values only: taking them from the client would
    # make undo a way to write an arbitrary status and forge an attestation.
    session['last_review'] = {"app_id": app_id, "table": schema.table,
                              "row_id": str(row_id), "before": _jsonable(result)}

    if _wants_json():
        return jsonify(ok=True, decision=decision, row_id=str(row_id))
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


def _jsonable_row(row):
    return _jsonable(row or {})


def _child_fk_type(db, db_conn_str, schema):
    if not schema.child:
        return None
    cols = db.get_tenant_table_schema(db_conn_str, schema.child.table)
    return next((c.get('udt_name') for c in cols
                 if c['column_name'] == schema.child.fk), None)


@backoffice_bp.route('/api/app/<app_id>/review/next')
@login_required
def review_next(app_id):
    """The next record awaiting review, so a decision does not send the reviewer
    back to a table to re-find their place.

    Traversal is a server query rather than a walk over the loaded page: the page
    is fifty rows of an arbitrary sort, and a reviewer working a queue of hundreds
    would silently stop at its edge.
    """
    db = get_db()
    if not check_permission(app_id, "content:read"):
        abort(403, description="No access to content in this application.")

    app, db_conn_str, schema = _queue_for(db, app_id)
    if not (app and schema and db_conn_str) or cms_mongo.handles(db_conn_str):
        return jsonify(error="No review queue for this application."), 400

    after = request.args.get('after') or None
    from ..utils.tenant_db import get_tenant_db
    try:
        with get_tenant_db(db_conn_str) as conn:
            with conn.cursor() as cur:
                record = next_awaiting(cur, schema, after_id=after)
                remaining = pending_count(cur, schema)
                kids = {}
                if record is not None:
                    kids = children_for(cur, schema, [record[schema.id]],
                                        fk_type=_child_fk_type(db, db_conn_str, schema))
    except Exception:
        log.exception("review_next failed")
        return jsonify(error="Could not load the next record."), 500

    if record is None:
        return jsonify(record=None, remaining=remaining)
    key = str(record[schema.id])
    return jsonify(record=_jsonable_row(record), remaining=remaining,
                   children=[_jsonable_row(k) for k in kids.get(key, [])])


@backoffice_bp.route('/api/app/<app_id>/review/undo', methods=['POST'])
@login_required
def undo_review(app_id):
    """Reverts the last decision made in this session.

    One level, session-scoped, and the values come from the server's own read of
    the row before the decision — never from the client, or undo would be a way
    to write an arbitrary status and forge an attestation.
    """
    db = get_db()
    last = session.get('last_review')
    if not last or last.get('app_id') != app_id:
        return jsonify(error="Nothing to undo."), 400
    if not check_permission(app_id, "content:write"):
        abort(403, description="Not permitted to undo a review decision.")

    app, db_conn_str, schema = _queue_for(db, app_id)
    if not (app and schema and db_conn_str) or schema.table != last.get('table'):
        return jsonify(error="Nothing to undo."), 400

    from ..utils.tenant_db import get_tenant_db
    try:
        with get_tenant_db(db_conn_str) as conn:
            ok = restore(conn, schema, last['row_id'], last['before'])
    except Exception:
        log.exception("undo_review failed")
        return jsonify(error="Could not undo that decision."), 500

    if not ok:
        return jsonify(error="Nothing to undo."), 400
    db.log_cms_mutation(app_id, schema.table, "REVIEW_UNDO", str(last['row_id']),
                        acting_identity(), None, last['before'])
    session.pop('last_review', None)
    return jsonify(ok=True, row_id=str(last['row_id']))


@backoffice_bp.route('/app/<app_id>/review/<row_id>/clear-spans', methods=['POST'])
@login_required
def clear_record_spans(app_id, row_id):
    """Removes a record's text annotations so its text can be edited.

    Deliberate, confirmed and audit-logged, because the alternative — letting an
    edit through and shifting every span — is corruption nobody detects. The
    removed spans go into the audit log in full: they are cheap for the tenant to
    recompute, but nothing else records what was there, and "40 spans deleted" is
    not an audit trail.
    """
    db = get_db()
    if not check_permission(app_id, "content:write"):
        abort(403, description="No write access to this application's content.")

    app, db_conn_str, schema = _queue_for(db, app_id)
    if not (app and schema and schema.annotations and db_conn_str) or cms_mongo.handles(db_conn_str):
        return jsonify(error="No annotations are configured for this application."), 400

    from ..utils.tenant_db import get_tenant_db
    try:
        fk_type = next((c.get('udt_name') for c in
                        db.get_tenant_table_schema(db_conn_str, schema.annotations.table)
                        if c['column_name'] == schema.annotations.fk), None)
        with get_tenant_db(db_conn_str) as conn:
            removed = clear_spans(conn, schema, row_id, fk_type=fk_type)
    except Exception:
        log.exception("clear_record_spans failed")
        return jsonify(error="Could not clear the spans."), 500

    db.log_cms_mutation(app_id, schema.annotations.table, "SPANS_CLEARED", str(row_id),
                        acting_identity(), [_jsonable(r) for r in removed], None)
    return jsonify(ok=True, removed=len(removed))
