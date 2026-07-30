# bifrost/backoffice/tenant_routes.py
from flask import render_template, request, redirect, url_for, flash, session, current_app, abort
from bson import ObjectId
from . import (backoffice_bp, get_db, login_required, requires, get_current_role_in_app,
               check_permission, cms_full_access)
from ..models.payments import (
    REJECT_REASON_CODES, REFUND_REASON_CODES, SLA_HOURS,
)
from ..models import cms_mongo
from ..models.queue_schema import QueueSchema

def managed_schema_for(app):
    """Postgres schema holding one tenant's tables inside the managed database.

    Managed mode is one physical database shared by every tenant, so `public` is
    not a tenancy boundary — two tenants with a `questions` table would read and
    write each other's rows. Each app gets its own schema instead.
    """
    name = app.get('db_schema') or f"tenant_{app.get('client_id', '')}"
    return ''.join(c if (c.isalnum() or c == '_') else '_' for c in name.lower())[:63]


def _with_schema(url, schema):
    """Pins search_path on the connection itself, via libpq options.

    Doing it in the DSN rather than per query means every existing call site is
    scoped without touching any of them — and since the pool is keyed by the
    connection string, one tenant's pooled connections can never be handed to
    another. `public` is deliberately absent from the path.
    """
    from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query) if k != 'options']
    query.append(('options', f'-c search_path={schema}'))
    return urlunsplit(parts._replace(query=urlencode(query)))


_ensured_schemas = set()


def _ensure_schema(base_url, schema):
    # ponytail: process-local memo, so a fresh worker pays one CREATE SCHEMA IF NOT
    # EXISTS per tenant and nothing after that. Move it to onboarding if that ever shows up.
    if schema in _ensured_schemas:
        return
    from psycopg2 import sql
    from ..utils.tenant_db import get_tenant_db
    with get_tenant_db(base_url) as conn:
        with conn.cursor() as cur:
            cur.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(schema)))
        conn.commit()
    _ensured_schemas.add(schema)


def get_tenant_db_conn_str(app):
    if not app:
        return None
    db_mode = app.get('db_mode')
    from config import Config
    db_conn = app.get('db_connection')

    if db_mode == 'managed' or not db_conn:
        # Tenant brings no database of its own: give it an isolated schema in ours.
        schema = managed_schema_for(app)
        _ensure_schema(Config.MANAGED_POSTGRES_URL, schema)
        return _with_schema(Config.MANAGED_POSTGRES_URL, schema)
    if isinstance(db_conn, dict):
        db_conn = db_conn.get('url')
        
    if db_conn and isinstance(db_conn, str) and db_conn.startswith('gAAAAA'):
        from ..utils.encryption import decrypt_value
        db_conn = decrypt_value(db_conn, app.get('webhook_secret', ''))
        
    return str(db_conn)


def locked_tables_for(app):
    """Tables the console must never let a tenant edit.

    Lives on the app document, so locking a new tenant's ledger is a console edit
    rather than a deploy. The tenant still cannot unlock itself: the field is
    writable only by a platform admin (see app_routes._save_platform_fields).
    """
    return sorted(set(app.get('platform_locked_tables') or []))


def _validate_payment_queue(db, db_conn_str, new_config):
    """Rejects a payment_queue block whose identifiers don't exist in the tenant schema.

    Runs at save time so a bad config is a readable error in the console rather than
    a 500 the first time someone opens the queue. No block configured, nothing to check.
    """
    if not (new_config or {}).get('payment_queue'):
        return []
    try:
        queue = QueueSchema.from_config(new_config)
    except (ValueError, TypeError) as e:
        return [str(e)]
    if not db_conn_str:
        return ["cannot validate against the tenant database — no connection configured."]
    if cms_mongo.handles(db_conn_str):
        return ["the payment queue is PostgreSQL-only; this tenant runs on MongoDB."]
    from ..utils.tenant_db import get_tenant_db
    with get_tenant_db(db_conn_str) as conn:
        with conn.cursor() as cur:
            return queue.validate(cur)


def _app_and_conn(db, app_id):
    """Loads the tenant app doc, its decrypted connection string and its queue schema.

    A payment_queue config that cannot be built is returned as a missing connection:
    every caller already refuses to touch the tenant DB in that case, which is exactly
    what we want — better a clear "go fix the config" than SQL built from a bad one.
    """
    app = db.db.applications.find_one({"_id": ObjectId(app_id)})
    if not app:
        return None, None, None
    try:
        queue = QueueSchema.from_config(db.get_cms_config(str(app['_id'])))
    except (ValueError, TypeError) as e:
        flash(f"Invalid payment_queue config — fix it in CMS settings: {e}", "danger")
        return app, None, None
    return app, get_tenant_db_conn_str(app), queue


@backoffice_bp.route('/payments')
@backoffice_bp.route('/app/<app_id_or_slug>/payments')
@login_required
def view_manual_payments(app_id_or_slug=None):
    db = get_db()
    from . import resolve_app_doc
    app = resolve_app_doc(db, app_id_or_slug)
    if not app:
        flash("Application not found.", "danger")
        return redirect(url_for('backoffice.dashboard'))

    app_id = str(app['_id'])
    if not check_permission(app_id, "payments:view"):
        flash("Unauthorized.", "danger")
        return redirect(url_for('backoffice.dashboard'))

    _, db_conn_str, queue = _app_and_conn(db, app_id)


    payments, tracks = [], []
    if not db_conn_str:
        flash("Tenant database connection not configured.", "warning")
    elif cms_mongo.handles(db_conn_str):
        # The queue is built on QueueSchema's SQL. A Mongo tenant gets the rest of
        # the CMS and keeps its own payment handling.
        flash("The payment queue is available for PostgreSQL tenants only.", "warning")
    else:
        try:
            status_filter = request.args.get('status', 'pending')
            payments = db.get_manual_payments(db_conn_str, status_filter=status_filter, queue=queue)
            tracks = db.get_active_tracks(db_conn_str, queue=queue)
        except Exception as e:
            flash(f"Error querying tenant database: {e}", "danger")

    return render_template(
        'backoffice/payment_queue.html',
        app=app,
        payments=payments,
        tracks=tracks,
        reject_reasons=REJECT_REASON_CODES,
        refund_reasons=REFUND_REASON_CODES,
        sla_hours=SLA_HOURS,
        can_approve=check_permission(app_id, "payments:approve"),
        current_role=get_current_role_in_app(app_id),
        status_filter=request.args.get('status', 'pending')
    )


@backoffice_bp.route('/app/<app_id>/payments/<payment_id>/approve', methods=['POST'])
@requires("payments:approve")
def approve_payment(app_id, payment_id):
    db = get_db()
    app, db_conn_str, queue = _app_and_conn(db, app_id)
    if not db_conn_str:
        flash("Tenant DB connection not configured.", "danger")
        return redirect(url_for('backoffice.view_app', app_id=app_id))

    track_id = request.form.get('track_id')
    if queue.grant and not track_id:
        flash("Select the exam track to grant before approving.", "danger")
        return redirect(url_for('backoffice.view_manual_payments', app_id=app_id))

    reviewer_id = str(session.get('backoffice_user'))
    try:
        success, result = db.approve_manual_payment(
            db_conn_str, int(payment_id), int(track_id) if track_id else None, reviewer_id,
            app_id=app_id, queue=queue
        )
    except Exception as e:
        flash(f"Approval error: {e}", "danger")
        return redirect(url_for('backoffice.view_manual_payments', app_id=app_id))

    if not success:
        if result.get('error') == 'duplicate_txn_ref':
            flash(f"{result['message']} Open payment #{result['duplicate_of']} to compare.", "danger")
        else:
            flash(result.get('message', 'Failed to approve payment.'), "danger")
        return redirect(url_for('backoffice.view_manual_payments', app_id=app_id))

    # Notify the tenant app. The webhook must carry the PAYER's identity — resolved
    # from the tenant user's email — never the reviewing admin's, and never a raw
    # Postgres id where a Bifrost account id belongs.
    detail = db.get_manual_payment_by_id(db_conn_str, int(payment_id), queue=queue) or {}
    payer = db.find_account_by_email(detail['email'], db.directory_scope(app)) if detail.get('email') else None
    if payer:
        db._trigger_event_for_user(
            account_id=payer['_id'],
            event_type="subscription_success",
            specific_app_id=app_id,
            extra_data={
                "payment_id": int(payment_id),
                "txn_ref": result.get('txn_ref'),
                "amount": result.get('amount'),
                "exam_track_id": int(track_id) if track_id else None,
                "tenant_user_id": result.get('user_id'),
                "role": "premium_user",
                "method": "manual_approval",
            }
        )
        flash("Payment approved. Premium entitlement granted and the app was notified.", "success")
    else:
        flash("Payment approved and entitlement granted, but no Bifrost account matched "
              f"'{detail.get('email')}' — the app was NOT notified. Link the account, then re-send.",
              "warning")
    return redirect(url_for('backoffice.view_manual_payments', app_id=app_id))


@backoffice_bp.route('/app/<app_id>/payments/<payment_id>/reject', methods=['POST'])
@requires("payments:approve")
def reject_payment(app_id, payment_id):
    db = get_db()
    app, db_conn_str, queue = _app_and_conn(db, app_id)
    if not db_conn_str:
        flash("Tenant DB connection not configured.", "danger")
        return redirect(url_for('backoffice.view_app', app_id=app_id))

    try:
        success, result = db.reject_manual_payment(
            db_conn_str, int(payment_id), str(session.get('backoffice_user')),
            request.form.get('reason_code'), request.form.get('reason_text'), app_id=app_id,
            queue=queue
        )
        flash("Payment rejected. The user can retry." if success
              else result.get('message', 'Failed to reject payment.'),
              "warning" if success else "danger")
    except Exception as e:
        flash(f"Rejection error: {e}", "danger")

    return redirect(url_for('backoffice.view_manual_payments', app_id=app_id))


@backoffice_bp.route('/app/<app_id>/payments/<payment_id>/refund', methods=['POST'])
@requires("payments:approve")
def refund_payment(app_id, payment_id):
    db = get_db()
    app, db_conn_str, queue = _app_and_conn(db, app_id)
    if not db_conn_str:
        flash("Tenant DB connection not configured.", "danger")
        return redirect(url_for('backoffice.view_app', app_id=app_id))

    try:
        success, result = db.refund_manual_payment(
            db_conn_str, int(payment_id), str(session.get('backoffice_user')),
            request.form.get('reason_code'), request.form.get('reason_text'), app_id=app_id,
            queue=queue
        )
    except Exception as e:
        flash(f"Refund error: {e}", "danger")
        return redirect(url_for('backoffice.view_manual_payments', app_id=app_id))

    if not success:
        flash(result.get('message', 'Failed to issue refund.'), "danger")
        return redirect(url_for('backoffice.view_manual_payments', app_id=app_id))

    detail = db.get_manual_payment_by_id(db_conn_str, int(payment_id), queue=queue) or {}
    payer = db.find_account_by_email(detail['email'], db.directory_scope(app)) if detail.get('email') else None
    if payer:
        db._trigger_event_for_user(
            account_id=payer['_id'],
            event_type="subscription_revoked",
            specific_app_id=app_id,
            extra_data={
                "payment_id": int(payment_id),
                "exam_track_id": result.get('exam_track_id'),
                "reason": request.form.get('reason_code'),
                "method": "manual_refund",
            }
        )
    flash(f"Payment refunded. Access to track #{result.get('exam_track_id')} revoked immediately.",
          "success")
    return redirect(url_for('backoffice.view_manual_payments', app_id=app_id))


@backoffice_bp.route('/app/<app_id>/users/<user_id>/suspend', methods=['POST'])
@requires("users:suspend")
def suspend_user(app_id, user_id):
    db = get_db()
    app, db_conn_str, queue = _app_and_conn(db, app_id)
    if not db_conn_str:
        flash("Tenant DB connection not configured.", "danger")
        return redirect(url_for('backoffice.view_app', app_id=app_id))

    reason = (request.form.get('reason') or '').strip()
    if not reason:
        flash("A reason is required to suspend an account.", "danger")
        return redirect(url_for('backoffice.view_app', app_id=app_id))

    try:
        db.suspend_tenant_user(db_conn_str, int(user_id), reason,
                               actor=str(session.get('backoffice_user')), app_id=app_id,
                               queue=queue)
        flash("User suspended. Entitlements are preserved and restore on reinstatement.", "warning")
    except Exception as e:
        flash(f"Suspension error: {e}", "danger")

    return redirect(url_for('backoffice.view_app', app_id=app_id))


@backoffice_bp.route('/app/<app_id>/users/<user_id>/reinstate', methods=['POST'])
@requires("users:suspend")
def reinstate_user(app_id, user_id):
    db = get_db()
    app, db_conn_str, queue = _app_and_conn(db, app_id)
    if not db_conn_str:
        flash("Tenant DB connection not configured.", "danger")
        return redirect(url_for('backoffice.view_app', app_id=app_id))

    try:
        db.reinstate_tenant_user(db_conn_str, int(user_id),
                                 actor=str(session.get('backoffice_user')), app_id=app_id,
                                 queue=queue)
        flash("User status reinstated to active.", "success")
    except Exception as e:
        flash(f"Reinstatement error: {e}", "danger")

    return redirect(url_for('backoffice.view_app', app_id=app_id))


@backoffice_bp.route('/app/<app_id>/entitlements/override', methods=['POST'])
@requires("entitlements:override")
def override_entitlement(app_id):
    """Force-grant or revoke premium for support cases (SOW 3.5)."""
    db = get_db()
    app, db_conn_str, queue = _app_and_conn(db, app_id)
    if not db_conn_str:
        flash("Tenant DB connection not configured.", "danger")
        return redirect(url_for('backoffice.view_app', app_id=app_id))

    try:
        db.set_entitlement(
            db_conn_str, int(request.form['user_id']), int(request.form['track_id']),
            request.form['status'], actor=str(session.get('backoffice_user')), app_id=app_id,
            queue=queue
        )
        flash("Entitlement overridden and audit-logged.", "success")
    except (KeyError, ValueError) as e:
        flash(f"Invalid override request: {e}", "danger")
    except Exception as e:
        flash(f"Override error: {e}", "danger")

    return redirect(url_for('backoffice.view_app', app_id=app_id))


@backoffice_bp.route('/app/<app_id>/audit')
@requires("audit:view")
def view_audit_log(app_id):
    """Filterable before/after timeline of every console mutation (SOW 3.9)."""
    db = get_db()
    app = db.db.applications.find_one({"_id": ObjectId(app_id)})
    entries = db.get_audit_log(
        app_id,
        table=request.args.get('table') or None,
        actor=request.args.get('actor') or None,
        limit=min(int(request.args.get('limit', 200)), 1000),
    )
    return render_template('backoffice/audit_log.html', app=app, entries=entries,
                           current_role=get_current_role_in_app(app_id),
                           filter_table=request.args.get('table', ''),
                           filter_actor=request.args.get('actor', ''))


@backoffice_bp.route('/api/tenant/<app_id>/payments/notify-new', methods=['POST'])
def api_notify_new_payment(app_id):
    """
    Ingests notifications from tenant apps when a receipt is uploaded.
    Triggers Telegram alerts based on the tenant's notification config.
    """
    db = get_db()
    app = db.db.applications.find_one({"_id": ObjectId(app_id)})
    if not app:
        return {"status": "error", "message": "App not found"}, 404
        
    import hmac
    webhook_secret = request.headers.get('X-Webhook-Secret') or ''
    if not hmac.compare_digest(str(webhook_secret), str(app.get('webhook_secret') or '')):
        return {"status": "error", "message": "Unauthorized"}, 401
        
    data = request.json or {}
    txn_ref = data.get('txn_ref')
    email = data.get('email')
    amount = data.get('amount')
    receipt_url = data.get('receipt_url')
    
    if not txn_ref or not email or not amount:
        return {"status": "error", "message": "Missing required fields (txn_ref, email, amount)"}, 400
        
    from bifrost.services.notification_service import dispatch_sla_alert
    try:
        sent = dispatch_sla_alert(app, txn_ref, email, amount, receipt_url)
        return {"status": "success", "alert_sent": sent}, 200
    except Exception as e:
        return {"status": "error", "message": str(e)}, 500

@backoffice_bp.route('/cms')
@backoffice_bp.route('/app/<app_id_or_slug>/cms')
@login_required
def view_cms_grid(app_id_or_slug=None):
    db = get_db()
    from . import resolve_app_doc
    app = resolve_app_doc(db, app_id_or_slug)
    if not app:
        flash("Application not found.", "danger")
        return redirect(url_for('backoffice.dashboard'))

    app_id = str(app['_id'])
    if not check_permission(app_id, "read:config"):
        flash("Unauthorized.", "danger")
        return redirect(url_for('backoffice.dashboard'))

    db_conn_str = get_tenant_db_conn_str(app)
    if not db_conn_str:
        flash("Tenant DB connection not configured.", "warning")
        return redirect(url_for('backoffice.view_app', app_id_or_slug=app_id))


    # Load per-app CMS config from MongoDB
    cms_config = db.get_cms_config(str(app['_id']))
    
    # Check if onboarded
    is_onboarded = cms_config.get('is_onboarded', False)
    if not is_onboarded:
        if cms_full_access(app_id, roles=('owner', 'super_admin', 'admin')):
            return redirect(url_for('backoffice.cms_onboarding', app_id=app_id))
        else:
            flash("CMS has not been configured by an admin yet.", "warning")
            return redirect(url_for('backoffice.dashboard'))

    table_groups = cms_config.get('table_groups', {})

    try:
        tables = db.get_tenant_tables(db_conn_str)

        current_role = get_current_role_in_app(app_id)

        # Apply Platform-Level Locks (PRD Sec 8.4)
        locked_tables = locked_tables_for(app)

        # Apply hidden tables from CMS config
        hidden = cms_config.get('hidden_tables', [])
        
        # Filter tables by visibility
        visible_tables = []
        tenant_roles = cms_config.get('roles', {})
        role_config = tenant_roles.get(current_role, {})
        
        for t in tables:
            if t in hidden or t in locked_tables:
                continue
            
            # Layer A Global Roles have full read access
            if cms_full_access(app_id):
                visible_tables.append(t)
            else:
                # Layer B Tenant Roles check
                table_config = role_config.get('tables', {}).get(t, [])
                perms_list = table_config.get('permissions', []) if isinstance(table_config, dict) else table_config
                
                if 'read' in perms_list or 'write' in perms_list:
                    visible_tables.append(t)
                    
        tables = visible_tables

        selected_table = request.args.get('table')
        if not selected_table or selected_table not in tables:
            selected_table = tables[0] if tables else None

        page = int(request.args.get('page', 1))
        limit = int(request.args.get('limit', 50))
        offset = (page - 1) * limit
        sort_by = request.args.get('sort_by', 'id')
        sort_dir = request.args.get('sort_dir', 'desc')
        search_query = request.args.get('q', None)

        columns, rows, schema_meta = [], [], []
        total_count = 0
        if selected_table:
            columns, rows, total_count = db.get_tenant_table_data(
                db_conn_str, selected_table, limit, offset, sort_by, sort_dir, search_query
            )
            schema_meta = db.get_tenant_table_schema(db_conn_str, selected_table)

        # Merge CMS column config (labels, hidden, readonly, type overrides)
        table_col_config = cms_config.get('tables', {}).get(selected_table, {}).get('columns', {})
        readonly_table = cms_config.get('tables', {}).get(selected_table, {}).get('readonly', False)

        # Apply Layer B Role-based column hiding. Same helper the save path uses,
        # so what a role cannot see is exactly what it cannot write.
        role_hidden_cols = hidden_columns_for(db, app_id, selected_table)

        # Filter hidden columns
        visible_columns = [
            c for c in columns
            if not table_col_config.get(c, {}).get('hidden', False) and c not in role_hidden_cols
        ]

        # Strip hidden columns from the DATA, not just the rendering. A role without
        # access to a column must never receive its value in the payload — the drawer
        # serialises whole rows into the page, so filtering in the template leaks.
        if len(visible_columns) != len(columns):
            rows = [{k: v for k, v in r.items() if k in visible_columns} for r in rows]

        # Build augmented schema: merge pg types with cms overrides
        schema_by_col = {s['column_name']: s for s in schema_meta}

    except Exception as e:
        flash(f"Error loading tenant schema: {e}", "danger")
        tables, selected_table, columns, rows = [], None, [], []
        schema_meta, schema_by_col, table_col_config = [], {}, {}
        visible_columns, readonly_table = [], False
        page, limit, total_count, sort_by, sort_dir, search_query = 1, 50, 0, 'id', 'desc', None
        current_role = get_current_role_in_app(app_id)

    # Determine Write Permission for the selected table
    can_write = False
    role_readonly_cols = []
    if selected_table and not readonly_table:
        if cms_full_access(app_id):
            can_write = True
        else:
            tbl_cfg = cms_config.get('roles', {}).get(current_role, {}).get('tables', {}).get(selected_table, [])
            perms_list = tbl_cfg.get('permissions', []) if isinstance(tbl_cfg, dict) else tbl_cfg
            if 'write' in perms_list:
                can_write = True
            if isinstance(tbl_cfg, dict):
                role_readonly_cols = tbl_cfg.get('readonly_columns', [])

    # We can pass role_readonly_cols to the template so it disables specific inputs
    return render_template(
        'backoffice/content_grid.html',
        app=app,
        tables=tables,
        table_groups=table_groups,
        selected_table=selected_table,
        columns=columns,
        visible_columns=visible_columns,
        rows=rows,
        schema_by_col=schema_by_col,
        table_col_config=table_col_config,
        readonly_table=readonly_table,
        can_write=can_write,
        cms_config=cms_config,
        current_role=current_role,
        page=page,
        limit=limit,
        total_count=total_count,
        sort_by=sort_by,
        sort_dir=sort_dir,
        search_query=search_query,
        role_readonly_cols=role_readonly_cols
    )

def hidden_columns_for(db, app_id, table_name):
    """Columns the current role may not see — and therefore may not write.

    One source of truth for the grid, the drawer and the save path. Filtering
    only on render meant a hidden column was still writable by anyone who could
    craft the POST.
    """
    if cms_full_access(app_id):
        return set()
    cms_config = db.get_cms_config(app_id)
    tbl_cfg = (cms_config.get('roles', {})
               .get(get_current_role_in_app(app_id), {})
               .get('tables', {}).get(table_name, {}))
    if not isinstance(tbl_cfg, dict):
        return set()
    return set(tbl_cfg.get('hidden_columns') or [])


def check_cms_write_permission(db, app_id, table_name):
    """Verifies if the current user has write permission for a specific CMS table."""
    app = db.db.applications.find_one({"_id": ObjectId(app_id)})

    # Platform lock beats every role, owner included — that is the whole point of
    # the setting. It used to filter the table list only, so a locked ledger was
    # merely hidden and a hand-made POST still wrote to it.
    if table_name in locked_tables_for(app or {}):
        return False

    current_role = get_current_role_in_app(app_id)
    if cms_full_access(app_id):
        return True

    cms_config = db.get_cms_config(app_id)
    tbl_cfg = cms_config.get('roles', {}).get(current_role, {}).get('tables', {}).get(table_name, [])
    perms_list = tbl_cfg.get('permissions', []) if isinstance(tbl_cfg, dict) else tbl_cfg
    return 'write' in perms_list

def check_publish_permission(app_id, table_name, data, db, db_conn_str, row_id=None):
    """Guards the draft -> review -> published workflow (SOW 3.2).

    Returns an error string, or None when the write may proceed. Content Managers
    may move draft -> review; only Admin/Owner may publish, and nothing publishes
    that fails MCQ validation. This runs on the write path, so it covers the grid,
    the drawer and the status-pill quick menu with one check.
    """
    if 'status' not in data or str(data.get('status')).lower() != 'published':
        return None
    if not check_permission(app_id, "content:publish"):
        return "Only an Admin or Owner may publish content. Move it to 'review' instead."
    # WHICH table gets publish validation is configuration; the rules themselves stay
    # domain-specific on purpose — a rules-config would be worse than a second function.
    validated = (db.get_cms_config(app_id) or {}).get('publish_validation_table', 'questions')
    if table_name != validated or row_id is None:
        return None
    # Structural validation is a SQL query. A Mongo-backed tenant keeps the role
    # check above and skips it rather than crashing on int(ObjectId).
    from ..models import cms_mongo
    if cms_mongo.handles(db_conn_str):
        return None
    errors = db.validate_question_publishable(db_conn_str, int(row_id))
    if errors:
        return "Cannot publish: " + " ".join(errors)
    return None


@backoffice_bp.route('/app/<app_id>/cms/<table_name>/save/<row_id>', methods=['POST'])
@login_required
def save_cms_row(app_id, table_name, row_id):
    db = get_db()
    if not check_cms_write_permission(db, app_id, table_name):
        abort(403, description="No write access to this table.")

    app = db.db.applications.find_one({"_id": ObjectId(app_id)})
    db_conn_str = get_tenant_db_conn_str(app)

    # Exclude internal form variables, and any column this role is not allowed to
    # see — a value it cannot read is a value it cannot write.
    forbidden = hidden_columns_for(db, app_id, table_name) | {'csrf_token', '_method'}
    data = {k: v for k, v in request.form.items() if k not in forbidden}
    acting_user = session.get('backoffice_user', 'unknown')

    blocked = check_publish_permission(app_id, table_name, data, db, db_conn_str, row_id)
    if blocked:
        flash(blocked, "danger")
        return redirect(url_for('backoffice.view_cms_grid', app_id=app_id, table=table_name))

    try:
        db.save_tenant_table_row(db_conn_str, table_name, row_id, data, app_id=app_id, acting_user=acting_user)
        flash("Row updated successfully.", "success")
    except Exception as e:
        flash(f"Update failed: {e}", "danger")

    return redirect(url_for('backoffice.view_cms_grid', app_id=app_id, table=table_name))

@backoffice_bp.route('/app/<app_id>/cms/<table_name>/create', methods=['POST'])
@login_required
def create_cms_row(app_id, table_name):
    db = get_db()
    if not check_cms_write_permission(db, app_id, table_name):
        abort(403, description="No write access to this table.")

    app = db.db.applications.find_one({"_id": ObjectId(app_id)})
    db_conn_str = get_tenant_db_conn_str(app)

    forbidden = hidden_columns_for(db, app_id, table_name) | {'csrf_token', '_method'}
    data = {k: v for k, v in request.form.items() if k not in forbidden}
    acting_user = session.get('backoffice_user', 'unknown')

    blocked = check_publish_permission(app_id, table_name, data, db, db_conn_str)
    if blocked:
        flash(blocked, "danger")
        return redirect(url_for('backoffice.view_cms_grid', app_id=app_id, table=table_name))

    try:
        db.insert_tenant_table_row(db_conn_str, table_name, data, app_id=app_id, acting_user=acting_user)
        flash("Row inserted successfully.", "success")
    except Exception as e:
        flash(f"Insertion failed: {e}", "danger")
        
    return redirect(url_for('backoffice.view_cms_grid', app_id=app_id, table=table_name))

@backoffice_bp.route('/app/<app_id>/cms/<table_name>/delete/<row_id>', methods=['POST'])
@login_required
def delete_cms_row(app_id, table_name, row_id):
    db = get_db()
    if not check_cms_write_permission(db, app_id, table_name):
        abort(403, description="No write access to this table.")


    app = db.db.applications.find_one({"_id": ObjectId(app_id)})
    db_conn_str = get_tenant_db_conn_str(app)
    acting_user = session.get('backoffice_user', 'unknown')
    
    try:
        db.delete_tenant_table_row(db_conn_str, table_name, row_id, app_id=app_id, acting_user=acting_user)
        flash("Row deleted.", "warning")
    except Exception as e:
        flash(f"Deletion failed: {e}", "danger")
        
    return redirect(url_for('backoffice.view_cms_grid', app_id=app_id, table=table_name))


# -----------------------------------------------------------------------
# CMS CONFIG — schema annotations (rename, hide, group, readonly)
# -----------------------------------------------------------------------

@backoffice_bp.route('/api/app/<app_id>/cms/<table_name>/lookup', methods=['GET'])
@login_required
def lookup_cms_table(app_id, table_name):
    from flask import jsonify
    db = get_db()
    
    # We do a basic read permission check (any role can read for lookup if they have access to CMS)
    # But for strict security, we just ensure they are logged in and part of the app
    current_role = get_current_role_in_app(app_id)
    if not current_role:
        return jsonify({"error": "Unauthorized"}), 403

    app = db.db.applications.find_one({"_id": ObjectId(app_id)})
    if not app:
        return jsonify({"error": "App not found"}), 404

    db_conn_str = get_tenant_db_conn_str(app)
    if not db_conn_str:
        return jsonify({"error": "DB not configured"}), 400

    try:
        _, rows, _ = db.get_tenant_table_data(db_conn_str, table_name, limit=200, offset=0)
        results = []
        for r in rows:
            # Synthesize label
            label = str(r.get('id', ''))
            for key in ['name', 'title', 'email', 'display_name', 'username']:
                if key in r and r[key]:
                    label = str(r[key])
                    break
            results.append({"id": r.get('id'), "label": label})
        return jsonify(results)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@backoffice_bp.route('/app/<app_id>/cms/settings', methods=['GET', 'POST'])
@login_required
def cms_settings(app_id):
    db = get_db()
    if not check_permission(app_id, "write:config"):
        flash("Unauthorized.", "danger")
        return redirect(url_for('backoffice.dashboard'))

    app = db.db.applications.find_one({"_id": ObjectId(app_id)})
    if not app:
        flash("Application not found.", "danger")
        return redirect(url_for('backoffice.dashboard'))

    db_conn_str = get_tenant_db_conn_str(app)
    cms_config = db.get_cms_config(str(app['_id']))

    if request.method == 'POST':
        import json
        raw = request.form.get('cms_config_json', '{}')
        try:
            new_config = json.loads(raw)
            errors = _validate_payment_queue(db, db_conn_str, new_config)
            if errors:
                flash("Payment queue config rejected: " + " ".join(errors), "danger")
            else:
                db.save_cms_config(str(app['_id']), new_config)
                flash("CMS configuration saved.", "success")
        except Exception as e:
            flash(f"Invalid config JSON: {e}", "danger")
        return redirect(url_for('backoffice.cms_settings', app_id=app_id))

    try:
        all_tables = db.get_tenant_tables(db_conn_str)
        table_schemas = {}
        for t in all_tables:
            table_schemas[t] = db.get_tenant_table_schema(db_conn_str, t)
    except Exception as e:
        flash(f"DB error: {e}", "danger")
        all_tables, table_schemas = [], {}

    locked_tables = locked_tables_for(app)
    
    current_role = get_current_role_in_app(app_id)
    return render_template(
        'backoffice/cms_config.html',
        app=app,
        all_tables=all_tables,
        table_schemas=table_schemas,
        cms_config=cms_config,
        current_role=current_role,
        locked_tables=locked_tables
    )

def _generate_smart_cms_config(all_tables):
    """
    Auto-detect backend tables to hide, and suggest friendly labels for the rest.
    """
    config = {
        "is_onboarded": False,
        "hidden_tables": [],
        "table_groups": {},
        "tables": {},
        "roles": {}
    }
    
    backend_keywords = ['migration', 'schema', 'auth', 'token', 'session', 'spatial_ref_sys', 'logs', 'audit']
    
    for t in all_tables:
        t_lower = t.lower()
        # Hide backend/system tables
        if any(k in t_lower for k in backend_keywords):
            config["hidden_tables"].append(t)
            continue
            
        # Suggest friendly labels
        label = t.replace('_', ' ').title()
        if t_lower == 'users': label = 'Customers'
        
        config["tables"][t] = {
            "label": label,
            "columns": {}
        }
    
    return config

@backoffice_bp.route('/onboarding', methods=['GET', 'POST'])
@backoffice_bp.route('/app/<app_id_or_slug>/cms/onboarding', methods=['GET', 'POST'])
@login_required
def cms_onboarding(app_id_or_slug=None):
    db = get_db()
    from . import resolve_app_doc
    app = resolve_app_doc(db, app_id_or_slug)
    if not app:
        flash("Application not found.", "danger")
        return redirect(url_for('backoffice.dashboard'))

    app_id = str(app['_id'])
    if not check_permission(app_id, "write:config"):
        flash("Unauthorized.", "danger")
        return redirect(url_for('backoffice.dashboard'))

    db_conn_str = get_tenant_db_conn_str(app)
    if not db_conn_str:
        flash("Tenant DB connection not configured.", "warning")
        return redirect(url_for('backoffice.view_app', app_id_or_slug=app_id))
        
    cms_config = db.get_cms_config(str(app['_id']))

    
    if request.method == 'POST':
        action = request.form.get('action')
        # Regardless of action, we mark as onboarded if they save or continue
        raw_config = request.form.get('cms_config_json')
        if raw_config:
            import json
            try:
                new_config = json.loads(raw_config)
                new_config['is_onboarded'] = True
                db.save_cms_config(str(app['_id']), new_config)
            except Exception as e:
                flash(f"Error parsing config: {e}", "danger")
                
        if action == 'customize':
            return redirect(url_for('backoffice.cms_settings', app_id=app_id))
        else:
            flash("Welcome to your CMS!", "success")
            return redirect(url_for('backoffice.view_cms_grid', app_id=app_id))
            
    try:
        all_tables = db.get_tenant_tables(db_conn_str)
        table_schemas = {}
        for t in all_tables:
            table_schemas[t] = db.get_tenant_table_schema(db_conn_str, t)
    except Exception as e:
        flash(f"DB error: {e}", "danger")
        all_tables, table_schemas = [], {}

    # If it's empty, generate smart config
    if not cms_config.get('tables') and not cms_config.get('hidden_tables'):
        smart_config = _generate_smart_cms_config(all_tables)
        # Preserve non-schema config (e.g. payment_queue) from bootstrap or previous setup
        for k, v in cms_config.items():
            if k not in ('is_onboarded', 'hidden_tables', 'tables', 'roles', 'table_groups'):
                smart_config[k] = v
    else:
        smart_config = cms_config
        
    current_role = get_current_role_in_app(app_id)
    return render_template(
        'backoffice/cms_onboarding.html',
        app=app,
        all_tables=all_tables,
        table_schemas=table_schemas,
        smart_config=smart_config,
        current_role=current_role
    )

@backoffice_bp.route('/app/<app_id>/cms/bootstrap-schema', methods=['POST'])
@login_required
def bootstrap_schema(app_id):
    db = get_db()
    if not check_permission(app_id, "write:config"):
        flash("Unauthorized.", "danger")
        return redirect(url_for('backoffice.dashboard'))

    app = db.db.applications.find_one({"_id": ObjectId(app_id)})
    if not app:
        flash("Application not found.", "danger")
        return redirect(url_for('backoffice.dashboard'))

    db_conn_str = get_tenant_db_conn_str(app)
    if not db_conn_str:
        flash("Tenant DB connection not configured.", "warning")
        return redirect(url_for('backoffice.view_app', app_id=app_id))

    template_type = request.form.get('template', 'exam_prep')

    EXAM_PREP_DDL = """
    CREATE TABLE IF NOT EXISTS users (
        id SERIAL PRIMARY KEY,
        email VARCHAR(255) UNIQUE NOT NULL,
        display_name VARCHAR(255),
        role VARCHAR(50) DEFAULT 'user',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS tracks (
        id SERIAL PRIMARY KEY,
        title VARCHAR(255) NOT NULL,
        description TEXT,
        grade_level VARCHAR(50),
        is_active BOOLEAN DEFAULT TRUE,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS questions (
        id SERIAL PRIMARY KEY,
        track_id INTEGER REFERENCES tracks(id),
        question_text TEXT NOT NULL,
        explanation_km TEXT,
        explanation_en TEXT,
        source_ref VARCHAR(255),
        status VARCHAR(50) DEFAULT 'draft',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS choices (
        id SERIAL PRIMARY KEY,
        question_id INTEGER REFERENCES questions(id) ON DELETE CASCADE,
        choice_text TEXT NOT NULL,
        is_correct BOOLEAN DEFAULT FALSE,
        explanation_km TEXT,
        explanation_en TEXT
    );

    CREATE TABLE IF NOT EXISTS payments (
        id SERIAL PRIMARY KEY,
        user_id INTEGER REFERENCES users(id),
        amount NUMERIC(10,2) NOT NULL,
        txn_ref VARCHAR(255) UNIQUE,
        receipt_url TEXT,
        status VARCHAR(50) DEFAULT 'pending',
        rejection_reason VARCHAR(255),
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS entitlements (
        id SERIAL PRIMARY KEY,
        user_id INTEGER REFERENCES users(id),
        exam_track_id INTEGER REFERENCES tracks(id),
        status VARCHAR(50) DEFAULT 'active',
        expires_at TIMESTAMP,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """

    ECOMMERCE_DDL = """
    CREATE TABLE IF NOT EXISTS customers (
        id SERIAL PRIMARY KEY,
        email VARCHAR(255) UNIQUE NOT NULL,
        name VARCHAR(255),
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS products (
        id SERIAL PRIMARY KEY,
        title VARCHAR(255) NOT NULL,
        price NUMERIC(10,2) NOT NULL,
        is_active BOOLEAN DEFAULT TRUE
    );

    CREATE TABLE IF NOT EXISTS orders (
        id SERIAL PRIMARY KEY,
        customer_id INTEGER REFERENCES customers(id),
        total NUMERIC(10,2) NOT NULL,
        state VARCHAR(50) DEFAULT 'awaiting_review',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS payments (
        id SERIAL PRIMARY KEY,
        order_id INTEGER REFERENCES orders(id),
        amount NUMERIC(10,2) NOT NULL,
        txn_ref VARCHAR(255) UNIQUE,
        status VARCHAR(50) DEFAULT 'pending',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS access_grants (
        id SERIAL PRIMARY KEY,
        customer_id INTEGER REFERENCES customers(id),
        plan_id VARCHAR(255),
        status VARCHAR(50) DEFAULT 'active',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """

    ddl_to_run = EXAM_PREP_DDL if template_type == 'exam_prep' else ECOMMERCE_DDL

    try:
        from ..utils.tenant_db import get_tenant_db
        with get_tenant_db(db_conn_str) as conn:
            with conn.cursor() as cur:
                cur.execute(ddl_to_run)
            conn.commit()
        flash("✨ Database tables created successfully!", "success")
    except Exception as e:
        flash(f"Error creating database schema: {e}", "danger")

    return redirect(url_for('backoffice.cms_onboarding', app_id=app_id))


@backoffice_bp.route('/app/<app_id>/cms/rbac', methods=['GET', 'POST'])

@login_required
def cms_rbac(app_id):
    db = get_db()
    if not check_permission(app_id, "write:config"):
        flash("Unauthorized.", "danger")
        return redirect(url_for('backoffice.dashboard'))

    app = db.db.applications.find_one({"_id": ObjectId(app_id)})
    if not app:
        flash("Application not found.", "danger")
        return redirect(url_for('backoffice.dashboard'))

    db_conn_str = get_tenant_db_conn_str(app)
    cms_config = db.get_cms_config(str(app['_id']))
    
    if request.method == 'POST':
        import json
        raw = request.form.get('cms_rbac_json', '{}')
        try:
            new_roles = json.loads(raw)
            cms_config['roles'] = new_roles
            db.save_cms_config(str(app['_id']), cms_config)
            flash("RBAC configuration saved.", "success")
        except Exception as e:
            flash(f"Invalid config JSON: {e}", "danger")
        return redirect(url_for('backoffice.cms_rbac', app_id=app_id))
        
    try:
        all_tables = db.get_tenant_tables(db_conn_str)
        table_schemas = {}
        for t in all_tables:
            table_schemas[t] = db.get_tenant_table_schema(db_conn_str, t)
    except Exception as e:
        flash(f"DB error: {e}", "danger")
        all_tables, table_schemas = [], {}

    current_role = get_current_role_in_app(app_id)
    return render_template(
        'backoffice/cms_rbac.html',
        app=app,
        all_tables=all_tables,
        table_schemas=table_schemas,
        cms_config=cms_config,
        current_role=current_role
    )
