# bifrost/backoffice/app_routes.py
from flask import render_template, request, redirect, url_for, session, flash
from bson import ObjectId
from . import backoffice_bp, get_db, login_required, heimdall_required, check_permission, get_current_role_in_app
from ..services.email_service import send_invite_email

@backoffice_bp.route('/')
@login_required
def dashboard():
    db = get_db()
    if session.get('is_heimdall'):
        apps = db.get_all_apps()
        title = "Bifrost Console (Admin)"
    else:
        apps = db.get_managed_apps(session['backoffice_user'])
        if not apps:
            session.clear()
            return redirect(url_for('backoffice.login'))
        title = "Bifrost Console"
    return render_template('backoffice/dashboard.html', apps=apps, title=title)


def _save_payment_setup(db, app_doc, form):
    """Records how a new app collects money. Both methods can run side by side.

    'payway'  — the tenant's own ABA merchant. Credentials go straight into that
                app's vault; Bifrost never holds a shared merchant (see payway.py).
    'manual'  — Bifrost's KHQR image + receipt approval queue, no bank API needed.

    Returns the stored method list so onboarding can tell the operator what is
    still missing.
    """
    if not app_doc:
        return []
    if not form.get('payments_enabled'):
        db.update_app_details(app_doc['_id'], {'payment_methods': []})
        return []

    methods = [m for m in ('payway', 'manual') if form.get(f'pay_{m}')]
    updates = {'payment_methods': methods}

    if 'manual' in methods and form.get('qr_url'):
        updates['app_qr_url'] = form.get('qr_url').strip()
    db.update_app_details(app_doc['_id'], updates)

    if 'payway' in methods:
        vault = app_doc.get('api_keys') or {}
        missing = []
        for field, key in (('payway_merchant_id', 'PAYWAY_MERCHANT_ID'),
                           ('payway_api_key', 'PAYWAY_API_KEY')):
            value = (form.get(field) or '').strip()
            if value:
                db.add_app_api_key(app_doc['_id'], key, value)
            elif not vault.get(key):
                # Blank means "unchanged", matching the DB connection field — so
                # only a credential that is absent from the vault too is missing.
                missing.append(key)
        if missing:
            flash(f"Bank API selected but {' and '.join(missing)} not set — "
                  f"checkout will fail until they're in the vault.", "warning")

    return methods


def _provision_application(db, form):
    """Creates an application, its payment setup and its owner from one form.

    Deliberately takes a plain mapping, not a request: the intake queue replays a
    stored request through here, so an approved tenant is provisioned from what
    they already typed instead of a platform admin re-keying it into a second form.
    """
    app_name = form.get('app_name')
    creds = db.register_application(app_name, form.get('callback_url'),
                                    web_url=form.get('web_url'),
                                    api_url=form.get('api_url'),
                                    logo_url=form.get('logo_url'))

    app_doc = db.get_app_by_client_id(creds['client_id'])
    _save_payment_setup(db, app_doc, form)

    admin_email = (form.get('admin_email') or '').strip().lower()
    if admin_email:
        user = db.find_account_by_email(admin_email)
        if not user:
            user_id = db.create_account(
                {"email": admin_email, "display_name": admin_email.split('@')[0], "auth_providers": ["email"]})
            otp, vid = db.create_otp(admin_email, channel="email")
            send_invite_email(admin_email, otp, app_name, vid, creds['client_id'])
        else:
            user_id = user['_id']
        db.link_user_to_app(user_id, app_doc['_id'], role="owner", duration_str="lifetime")

    return creds, app_doc


@backoffice_bp.route('/apps/create', methods=['GET', 'POST'])
@login_required
@heimdall_required
def create_app():
    if request.method == 'POST':
        _provision_application(get_db(), request.form)
        return redirect(url_for('backoffice.dashboard'))
    return render_template('backoffice/create_app.html')


@backoffice_bp.route('/request', methods=['GET', 'POST'])
def request_tenancy():
    """Public intake. Creates a request, never an application.

    No login: the people who need this do not have a console account yet — that
    was the gap. Nothing here provisions anything, so an abusive submission costs
    a row in tenant_requests and a platform admin clicking Reject.
    """
    if request.method == 'POST':
        db = get_db()
        if not (request.form.get('app_name') or '').strip() or not (request.form.get('admin_email') or '').strip():
            flash("Application name and contact email are required.", "danger")
            return render_template('backoffice/request_tenancy.html', form=request.form)
        db.create_tenant_request(request.form)
        return render_template('backoffice/request_tenancy.html', submitted=True)
    return render_template('backoffice/request_tenancy.html', form={})


@backoffice_bp.route('/heimdall/requests')
@login_required
@heimdall_required
def tenant_requests():
    db = get_db()
    requests_all = db.list_tenant_requests()
    return render_template(
        'backoffice/tenant_requests.html',
        pending=[r for r in requests_all if r.get('status') == 'pending'],
        decided=[r for r in requests_all if r.get('status') != 'pending'],
    )


@backoffice_bp.route('/heimdall/requests/<request_id>/<decision>', methods=['POST'])
@login_required
@heimdall_required
def decide_tenant_request(request_id, decision):
    if decision not in ('approve', 'reject'):
        flash("Unknown decision.", "danger")
        return redirect(url_for('backoffice.tenant_requests'))

    db = get_db()
    req = db.get_tenant_request(request_id)
    if not req or req.get('status') != 'pending':
        flash("That request has already been decided.", "warning")
        return redirect(url_for('backoffice.tenant_requests'))

    if decision == 'reject':
        db.decide_tenant_request(request_id, 'rejected', session.get('backoffice_user'),
                                 reason=(request.form.get('reason') or '').strip() or None)
        flash(f"Rejected '{req.get('app_name')}'.", "success")
        return redirect(url_for('backoffice.tenant_requests'))

    # Claim the request BEFORE provisioning: the status guard is what stops a
    # double-clicked Approve from registering the same tenant twice.
    if not db.decide_tenant_request(request_id, 'approved', session.get('backoffice_user')):
        flash("That request has already been decided.", "warning")
        return redirect(url_for('backoffice.tenant_requests'))

    creds, app_doc = _provision_application(db, req)
    db.db.tenant_requests.update_one({"_id": req["_id"]}, {"$set": {"client_id": creds['client_id']}})
    flash(f"Approved '{req.get('app_name')}' — client_id {creds['client_id']}. "
          f"An invite was sent to {req.get('admin_email')}.", "success")
    return redirect(url_for('backoffice.select_app', app_id=str(app_doc['_id'])))


@backoffice_bp.route('/select-app/<app_id>')
@login_required
def select_app(app_id):
    db = get_db()
    from . import resolve_app_doc
    app = resolve_app_doc(db, app_id)
    if not app:
        flash("Application not found.", "danger")
        return redirect(url_for('backoffice.dashboard'))
    session['active_app_id'] = str(app['_id'])
    return redirect(url_for('backoffice.view_app'))


@backoffice_bp.route('/app')
@backoffice_bp.route('/users')
@login_required
def view_app(app_id_or_slug=None):
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

    users = db.get_app_users(app_id)
    owner = db.get_app_owner(app_id)
    current_role = get_current_role_in_app(app_id)

    return render_template('backoffice/app_users.html', app=app, users=users, owner=owner, current_role=current_role)




@backoffice_bp.route('/app/<app_id>/update', methods=['POST'])
@login_required
def update_app_settings(app_id):
    db = get_db()
    # HIERARCHY CHECK: Super Admin or Owner required
    if not check_permission(app_id, "write:config"):
        flash("Access Denied: App Admins cannot change configuration.", "danger")
        return redirect(url_for('backoffice.view_app', app_id=app_id))

    enabled_services = {
        'sso': bool(request.form.get('service_sso')),
        'phone_otp': bool(request.form.get('service_phone_otp')),
        'email_otp': bool(request.form.get('service_email_otp')),
        'secrets_vault': bool(request.form.get('service_secrets_vault')),
        'payment_bot': bool(request.form.get('service_payment_bot')),
        'heimdall_monitor': bool(request.form.get('service_heimdall_monitor'))
    }

    # The form no longer renders the stored value, so blank means "unchanged"
    # rather than "clear it". Only a value the operator actually typed is
    # plaintext, which is why the old startswith('gAAAAA') ciphertext sniff —
    # and its one-character-typo failure mode of double-encrypting — is gone.
    raw_db_conn = (request.form.get('db_connection') or '').strip()
    if raw_db_conn:
        from ..utils.encryption import encrypt_value
        app_doc = db.db.applications.find_one({"_id": ObjectId(app_id)})
        if app_doc and app_doc.get('webhook_secret'):
            raw_db_conn = encrypt_value(raw_db_conn, app_doc['webhook_secret'])

    data = {
        'app_name': request.form.get('app_name'),
        'app_web_url': request.form.get('web_url'),
        'app_callback_url': request.form.get('callback_url'),
        'app_api_url': request.form.get('api_url'),
        'app_logo_url': request.form.get('logo_url'),
        'telegram_bot_token': request.form.get('telegram_bot_token'),
        'db_mode': request.form.get('db_mode', 'custom'),
        'enabled_services': enabled_services
    }
    if raw_db_conn:
        data['db_connection'] = raw_db_conn

    # Platform-super-admin only. An owner must not be able to unlock their own
    # ledger tables, so this field is ignored on a tenant's own POST rather than
    # merely hidden from their form.
    if session.get('is_heimdall') or session.get('is_pr0meth4us'):
        data['platform_locked_tables'] = [
            t.strip() for t in (request.form.get('platform_locked_tables') or '').split(',') if t.strip()
        ]


    if db.update_app_details(app_id, data):
        flash("Settings updated.", "success")
    else:
        flash("Failed to update.", "danger")

    # Same handler as onboarding — the QR URL, the method list and the merchant
    # credentials are one setting, not three, so they are edited by one function.
    _save_payment_setup(db, db.db.applications.find_one({"_id": ObjectId(app_id)}), request.form)

    return redirect(url_for('backoffice.view_app', app_id=app_id))


@backoffice_bp.route('/app/<app_id>/rotate-secret', methods=['POST'])
@login_required
def rotate_secret(app_id):
    db = get_db()
    # HIERARCHY CHECK: Owner Only
    if not check_permission(app_id, "transfer:ownership"):
        flash("Access Denied: Only the Owner can rotate secrets.", "danger")
        return redirect(url_for('backoffice.view_app', app_id=app_id))

    new_secret = db.rotate_app_secret(app_id)
    flash(f"SECRET ROTATED! {new_secret}", "warning")
    return redirect(url_for('backoffice.view_app', app_id=app_id))


@backoffice_bp.route('/app/<app_id>/api-keys/add', methods=['POST'])
@login_required
def add_api_key(app_id):
    db = get_db()
    if not check_permission(app_id, "manage:secrets"): # Super Admin or Owner
        flash("Access Denied: Only Admins can manage API Keys.", "danger")
        return redirect(url_for('backoffice.view_app', app_id=app_id))
    
    key_name = request.form.get('key_name')
    key_value = request.form.get('key_value')
    if key_name and key_value:
        db.add_app_api_key(app_id, key_name, key_value)
        flash(f"API Key '{key_name.upper()}' updated successfully.", "success")
    else:
        flash("Key Name and Key Value are required.", "danger")
    
    return redirect(url_for('backoffice.view_app', app_id=app_id))


@backoffice_bp.route('/app/<app_id>/api-keys/delete', methods=['POST'])
@login_required
def delete_api_key(app_id):
    db = get_db()
    if not check_permission(app_id, "manage:secrets"):
        flash("Access Denied: Only Admins can manage API Keys.", "danger")
        return redirect(url_for('backoffice.view_app', app_id=app_id))
    
    key_name = request.form.get('key_name')
    if key_name:
        db.remove_app_api_key(app_id, key_name)
        flash(f"API Key '{key_name}' removed.", "success")
        
    return redirect(url_for('backoffice.view_app', app_id=app_id))
