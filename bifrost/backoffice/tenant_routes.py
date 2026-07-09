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
        
    try:
        tables = db.get_tenant_tables(db_conn_str)
        selected_table = request.args.get('table', 'questions')
        if selected_table not in tables and tables:
            selected_table = tables[0]
            
        columns = []
        rows = []
        if selected_table in tables:
            columns, rows = db.get_tenant_table_data(db_conn_str, selected_table)
    except Exception as e:
        flash(f"Error loading tenant schema: {e}", "danger")
        tables = []
        selected_table = None
        columns = []
        rows = []
        
    current_role = get_current_role_in_app(app_id)
    return render_template(
        'backoffice/content_grid.html',
        app=app,
        tables=tables,
        selected_table=selected_table,
        columns=columns,
        rows=rows,
        current_role=current_role
    )

@backoffice_bp.route('/app/<app_id>/cms/<table_name>/save/<row_id>', methods=['POST'])
@login_required
def save_cms_row(app_id, table_name, row_id):
    db = get_db()
    if not check_permission(app_id, "write:config"):
        flash("Unauthorized.", "danger")
        return redirect(url_for('backoffice.view_app', app_id=app_id))
        
    app = db.db.applications.find_one({"_id": ObjectId(app_id)})
    db_conn_str = get_tenant_db_conn_str(app)
    
    # Exclude internal form variables
    data = {k: v for k, v in request.form.items() if k not in ('csrf_token', '_method')}
    
    try:
        db.save_tenant_table_row(db_conn_str, table_name, int(row_id), data)
        flash("Row updated successfully.", "success")
    except Exception as e:
        flash(f"Update failed: {e}", "danger")
        
    return redirect(url_for('backoffice.view_cms_grid', app_id=app_id, table=table_name))

@backoffice_bp.route('/app/<app_id>/cms/<table_name>/create', methods=['POST'])
@login_required
def create_cms_row(app_id, table_name):
    db = get_db()
    if not check_permission(app_id, "write:config"):
        flash("Unauthorized.", "danger")
        return redirect(url_for('backoffice.view_app', app_id=app_id))
        
    app = db.db.applications.find_one({"_id": ObjectId(app_id)})
    db_conn_str = get_tenant_db_conn_str(app)
    
    data = {k: v for k, v in request.form.items() if k not in ('csrf_token', '_method')}
    
    try:
        db.insert_tenant_table_row(db_conn_str, table_name, data)
        flash("Row inserted successfully.", "success")
    except Exception as e:
        flash(f"Insertion failed: {e}", "danger")
        
    return redirect(url_for('backoffice.view_cms_grid', app_id=app_id, table=table_name))

@backoffice_bp.route('/app/<app_id>/cms/<table_name>/delete/<row_id>', methods=['POST'])
@login_required
def delete_cms_row(app_id, table_name, row_id):
    db = get_db()
    if not check_permission(app_id, "write:config"):
        flash("Unauthorized.", "danger")
        return redirect(url_for('backoffice.view_app', app_id=app_id))
        
    app = db.db.applications.find_one({"_id": ObjectId(app_id)})
    db_conn_str = get_tenant_db_conn_str(app)
    
    try:
        db.delete_tenant_table_row(db_conn_str, table_name, int(row_id))
        flash("Row deleted.", "warning")
    except Exception as e:
        flash(f"Deletion failed: {e}", "danger")
        
    return redirect(url_for('backoffice.view_cms_grid', app_id=app_id, table=table_name))
