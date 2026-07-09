# bifrost/backoffice/tenant_routes.py
from flask import render_template, request, redirect, url_for, flash, session, current_app
from bson import ObjectId
from . import backoffice_bp, get_db, login_required, get_current_role_in_app, check_permission

def get_tenant_db_conn_str(app):
    db_conn = app.get('db_connection')
    if not db_conn:
        return None
    if isinstance(db_conn, dict):
        return db_conn.get('url')
    return str(db_conn)

@backoffice_bp.route('/app/<app_id>/payments')
@login_required
def view_manual_payments(app_id):
    db = get_db()
    if not check_permission(app_id, "read:config"):
        flash("Unauthorized.", "danger")
        return redirect(url_for('backoffice.dashboard'))
        
    app = db.db.applications.find_one({"_id": ObjectId(app_id)})
    if not app:
        flash("Application not found.", "danger")
        return redirect(url_for('backoffice.dashboard'))
        
    db_conn_str = get_tenant_db_conn_str(app)
    if not db_conn_str:
        flash("Tenant database connection not configured.", "warning")
        payments = []
    else:
        try:
            status_filter = request.args.get('status', 'pending')
            payments = db.get_manual_payments(db_conn_str, status_filter=status_filter)
        except Exception as e:
            flash(f"Error querying tenant database: {e}", "danger")
            payments = []
            
    current_role = get_current_role_in_app(app_id)
    return render_template(
        'backoffice/payment_queue.html',
        app=app,
        payments=payments,
        current_role=current_role,
        status_filter=request.args.get('status', 'pending')
    )

@backoffice_bp.route('/app/<app_id>/payments/<payment_id>/approve', methods=['POST'])
@login_required
def approve_payment(app_id, payment_id):
    db = get_db()
    my_role = get_current_role_in_app(app_id)
    if my_role not in ('owner', 'super_admin', 'admin', 'billing_agent', 'operations', 'heimdall', 'pr0meth4us'):
        flash("Unauthorized to approve payments.", "danger")
        return redirect(url_for('backoffice.view_app', app_id=app_id))
        
    app = db.db.applications.find_one({"_id": ObjectId(app_id)})
    db_conn_str = get_tenant_db_conn_str(app)
    if not db_conn_str:
        flash("Tenant DB connection not configured.", "danger")
        return redirect(url_for('backoffice.view_app', app_id=app_id))
        
    track_id = request.form.get('track_id') or app.get('default_track_id') or 1
    reviewer_id = session.get('backoffice_user')
    
    try:
        success, payment = db.approve_manual_payment(db_conn_str, int(payment_id), int(track_id), str(reviewer_id))
        if success:
            # Trigger subscription success webhook via Bifrost event layer
            db._trigger_event_for_user(
                account_id=reviewer_id,
                event_type="subscription_success",
                specific_app_id=app_id,
                extra_data={
                    "payment_id": payment_id,
                    "txn_ref": payment.get('txn_ref'),
                    "amount": payment.get('amount'),
                    "role": "premium_user",
                    "method": "manual_approval"
                }
            )
            flash("Payment approved successfully. Premium entitlement granted.", "success")
        else:
            flash("Failed to approve payment.", "danger")
    except Exception as e:
        flash(f"Approval error: {e}", "danger")
        
    return redirect(url_for('backoffice.view_manual_payments', app_id=app_id))

@backoffice_bp.route('/app/<app_id>/payments/<payment_id>/reject', methods=['POST'])
@login_required
def reject_payment(app_id, payment_id):
    db = get_db()
    my_role = get_current_role_in_app(app_id)
    if my_role not in ('owner', 'super_admin', 'admin', 'billing_agent', 'operations', 'heimdall', 'pr0meth4us'):
        flash("Unauthorized to reject payments.", "danger")
        return redirect(url_for('backoffice.view_app', app_id=app_id))
        
    app = db.db.applications.find_one({"_id": ObjectId(app_id)})
    db_conn_str = get_tenant_db_conn_str(app)
    if not db_conn_str:
        flash("Tenant DB connection not configured.", "danger")
        return redirect(url_for('backoffice.view_app', app_id=app_id))
        
    reviewer_id = session.get('backoffice_user')
    reason = request.form.get('reason') or "Unspecified reason"
    
    try:
        success = db.reject_manual_payment(db_conn_str, int(payment_id), str(reviewer_id), reason)
        if success:
            flash("Payment rejected.", "warning")
        else:
            flash("Failed to reject payment.", "danger")
    except Exception as e:
        flash(f"Rejection error: {e}", "danger")
        
    return redirect(url_for('backoffice.view_manual_payments', app_id=app_id))

@backoffice_bp.route('/app/<app_id>/payments/<payment_id>/refund', methods=['POST'])
@login_required
def refund_payment(app_id, payment_id):
    db = get_db()
    my_role = get_current_role_in_app(app_id)
    if my_role not in ('owner', 'super_admin', 'admin', 'billing_agent', 'operations', 'heimdall', 'pr0meth4us'):
        flash("Unauthorized to refund payments.", "danger")
        return redirect(url_for('backoffice.view_app', app_id=app_id))
        
    app = db.db.applications.find_one({"_id": ObjectId(app_id)})
    db_conn_str = get_tenant_db_conn_str(app)
    if not db_conn_str:
        flash("Tenant DB connection not configured.", "danger")
        return redirect(url_for('backoffice.view_app', app_id=app_id))
        
    track_id = request.form.get('track_id') or app.get('default_track_id') or 1
    reviewer_id = session.get('backoffice_user')
    reason = request.form.get('reason') or "Refund issued"
    
    try:
        success, payment = db.refund_manual_payment(db_conn_str, int(payment_id), int(track_id), str(reviewer_id), reason)
        if success:
            flash("Payment refunded and premium entitlement revoked immediately.", "success")
        else:
            flash("Failed to issue refund.", "danger")
    except Exception as e:
        flash(f"Refund error: {e}", "danger")
        
    return redirect(url_for('backoffice.view_manual_payments', app_id=app_id))

@backoffice_bp.route('/app/<app_id>/users/<user_id>/suspend', methods=['POST'])
@login_required
def suspend_user(app_id, user_id):
    db = get_db()
    my_role = get_current_role_in_app(app_id)
    if my_role not in ('owner', 'super_admin', 'admin', 'heimdall', 'pr0meth4us'):
        flash("Unauthorized to suspend users.", "danger")
        return redirect(url_for('backoffice.view_app', app_id=app_id))
        
    app = db.db.applications.find_one({"_id": ObjectId(app_id)})
    db_conn_str = get_tenant_db_conn_str(app)
    if not db_conn_str:
        flash("Tenant DB connection not configured.", "danger")
        return redirect(url_for('backoffice.view_app', app_id=app_id))
        
    reason = request.form.get('reason') or "Anti-scraping trigger / admin suspension"
    
    try:
        db.suspend_tenant_user(db_conn_str, int(user_id), reason)
        flash("User suspended and active entitlements revoked on tenant database.", "warning")
    except Exception as e:
        flash(f"Suspension error: {e}", "danger")
        
    return redirect(url_for('backoffice.view_app', app_id=app_id))

@backoffice_bp.route('/app/<app_id>/users/<user_id>/reinstate', methods=['POST'])
@login_required
def reinstate_user(app_id, user_id):
    db = get_db()
    my_role = get_current_role_in_app(app_id)
    if my_role not in ('owner', 'super_admin', 'admin', 'heimdall', 'pr0meth4us'):
        flash("Unauthorized to reinstate users.", "danger")
        return redirect(url_for('backoffice.view_app', app_id=app_id))
        
    app = db.db.applications.find_one({"_id": ObjectId(app_id)})
    db_conn_str = get_tenant_db_conn_str(app)
    if not db_conn_str:
        flash("Tenant DB connection not configured.", "danger")
        return redirect(url_for('backoffice.view_app', app_id=app_id))
        
    try:
        db.reinstate_tenant_user(db_conn_str, int(user_id))
        flash("User status reinstated to active.", "success")
    except Exception as e:
        flash(f"Reinstatement error: {e}", "danger")
        
    return redirect(url_for('backoffice.view_app', app_id=app_id))

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

@backoffice_bp.route('/app/<app_id>/cms')
@login_required
def view_cms_grid(app_id):
    db = get_db()
    if not check_permission(app_id, "read:config"):
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

    # Load per-app CMS config from MongoDB
    cms_config = db.get_cms_config(str(app['_id']))
    table_groups = cms_config.get('table_groups', {})

    try:
        from config import Config
        tables = db.get_tenant_tables(db_conn_str)
        
        current_role = get_current_role_in_app(app_id)
        
        # Apply Platform-Level Locks (PRD Sec 8.4)
        client_id = app.get('client_id')
        locked_tables = Config.PLATFORM_LOCKED_TABLES.get(client_id, [])

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
            if current_role in ('owner', 'super_admin', 'heimdall', 'pr0meth4us'):
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

        # Apply Layer B Role-based column hiding
        role_hidden_cols = []
        if current_role not in ('owner', 'super_admin', 'heimdall', 'pr0meth4us'):
            tbl_cfg = role_config.get('tables', {}).get(selected_table, {})
            if isinstance(tbl_cfg, dict):
                role_hidden_cols = tbl_cfg.get('hidden_columns', [])

        # Filter hidden columns
        visible_columns = [
            c for c in columns
            if not table_col_config.get(c, {}).get('hidden', False) and c not in role_hidden_cols
        ]

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
        if current_role in ('owner', 'super_admin', 'heimdall', 'pr0meth4us'):
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

def check_cms_write_permission(db, app_id, table_name):
    """Verifies if the current user has write permission for a specific CMS table."""
    current_role = get_current_role_in_app(app_id)
    if current_role in ('owner', 'super_admin', 'heimdall', 'pr0meth4us'):
        return True
        
    cms_config = db.get_cms_config(app_id)
    tbl_cfg = cms_config.get('roles', {}).get(current_role, {}).get('tables', {}).get(table_name, [])
    perms_list = tbl_cfg.get('permissions', []) if isinstance(tbl_cfg, dict) else tbl_cfg
    return 'write' in perms_list

@backoffice_bp.route('/app/<app_id>/cms/<table_name>/save/<row_id>', methods=['POST'])
@login_required
def save_cms_row(app_id, table_name, row_id):
    db = get_db()
    if not check_cms_write_permission(db, app_id, table_name):
        flash("Unauthorized.", "danger")
        return redirect(url_for('backoffice.view_app', app_id=app_id))
        
    app = db.db.applications.find_one({"_id": ObjectId(app_id)})
    db_conn_str = get_tenant_db_conn_str(app)
    
    # Exclude internal form variables
    data = {k: v for k, v in request.form.items() if k not in ('csrf_token', '_method')}
    acting_user = session.get('backoffice_user', 'unknown')
    
    try:
        db.save_tenant_table_row(db_conn_str, table_name, int(row_id), data, app_id=app_id, acting_user=acting_user)
        flash("Row updated successfully.", "success")
    except Exception as e:
        flash(f"Update failed: {e}", "danger")
        
    return redirect(url_for('backoffice.view_cms_grid', app_id=app_id, table=table_name))

@backoffice_bp.route('/app/<app_id>/cms/<table_name>/create', methods=['POST'])
@login_required
def create_cms_row(app_id, table_name):
    db = get_db()
    if not check_cms_write_permission(db, app_id, table_name):
        flash("Unauthorized.", "danger")
        return redirect(url_for('backoffice.view_app', app_id=app_id))
        
    app = db.db.applications.find_one({"_id": ObjectId(app_id)})
    db_conn_str = get_tenant_db_conn_str(app)
    
    data = {k: v for k, v in request.form.items() if k not in ('csrf_token', '_method')}
    acting_user = session.get('backoffice_user', 'unknown')
    
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
        flash("Unauthorized.", "danger")
        return redirect(url_for('backoffice.view_app', app_id=app_id))
        
    app = db.db.applications.find_one({"_id": ObjectId(app_id)})
    db_conn_str = get_tenant_db_conn_str(app)
    acting_user = session.get('backoffice_user', 'unknown')
    
    try:
        db.delete_tenant_table_row(db_conn_str, table_name, int(row_id), app_id=app_id, acting_user=acting_user)
        flash("Row deleted.", "warning")
    except Exception as e:
        flash(f"Deletion failed: {e}", "danger")
        
    return redirect(url_for('backoffice.view_cms_grid', app_id=app_id, table=table_name))


# -----------------------------------------------------------------------
# CMS CONFIG — schema annotations (rename, hide, group, readonly)
# -----------------------------------------------------------------------

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

    from config import Config
    locked_tables = Config.PLATFORM_LOCKED_TABLES.get(app.get('client_id'), [])
    
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
