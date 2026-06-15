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
        title = "Heimdall Dashboard"
    else:
        apps = db.get_managed_apps(session['backoffice_user'])
        if not apps:
            session.clear()
            return redirect(url_for('backoffice.login'))
        title = "Tenant Dashboard"
    return render_template('backoffice/dashboard.html', apps=apps, title=title)


@backoffice_bp.route('/apps/create', methods=['GET', 'POST'])
@login_required
@heimdall_required
def create_app():
    if request.method == 'POST':
        db = get_db()
        app_name = request.form.get('app_name')
        callback_url = request.form.get('callback_url')
        creds = db.register_application(app_name, callback_url, web_url=request.form.get('web_url'),
                                        api_url=request.form.get('api_url'), logo_url=request.form.get('logo_url'))

        admin_email = request.form.get('admin_email').strip().lower()
        if admin_email:
            app_doc = db.get_app_by_client_id(creds['client_id'])
            user = db.find_account_by_email(admin_email)
            if not user:
                new_id = db.create_account(
                    {"email": admin_email, "display_name": admin_email.split('@')[0], "auth_providers": ["email"]})
                otp, vid = db.create_otp(admin_email, channel="email")
                send_invite_email(admin_email, otp, app_name, vid, creds['client_id'])
                user_id = new_id
            else:
                user_id = user['_id']
            db.link_user_to_app(user_id, app_doc['_id'], role="owner", duration_str="lifetime")

        return redirect(url_for('backoffice.dashboard'))
    return render_template('backoffice/create_app.html')


@backoffice_bp.route('/app/<app_id>')
@login_required
def view_app(app_id):
    db = get_db()
    # Check if user has ANY access
    if not check_permission(app_id, 1):  # Level 1 = Admin or higher
        flash("Unauthorized.", "danger")
        return redirect(url_for('backoffice.dashboard'))

    app = db.db.applications.find_one({"_id": ObjectId(app_id)})
    users = db.get_app_users(app_id)
    owner = db.get_app_owner(app_id)
    current_role = get_current_role_in_app(app_id)

    return render_template('backoffice/app_users.html', app=app, users=users, owner=owner, current_role=current_role)


@backoffice_bp.route('/app/<app_id>/update', methods=['POST'])
@login_required
def update_app_settings(app_id):
    db = get_db()
    # HIERARCHY CHECK: Super Admin (2) or Owner (3) required
    if not check_permission(app_id, 2):
        flash("Access Denied: App Admins cannot change configuration.", "danger")
        return redirect(url_for('backoffice.view_app', app_id=app_id))

    data = {
        'app_name': request.form.get('app_name'),
        'app_web_url': request.form.get('web_url'),
        'app_callback_url': request.form.get('callback_url'),
        'app_api_url': request.form.get('api_url'),
        'app_logo_url': request.form.get('logo_url'),
        'app_qr_url': request.form.get('qr_url'),
        'telegram_bot_token': request.form.get('telegram_bot_token')
    }

    if db.update_app_details(app_id, data):
        flash("Settings updated.", "success")
    else:
        flash("Failed to update.", "danger")

    return redirect(url_for('backoffice.view_app', app_id=app_id))


@backoffice_bp.route('/app/<app_id>/rotate-secret', methods=['POST'])
@login_required
def rotate_secret(app_id):
    db = get_db()
    # HIERARCHY CHECK: Owner (3) Only
    if not check_permission(app_id, 3):
        flash("Access Denied: Only the Owner can rotate secrets.", "danger")
        return redirect(url_for('backoffice.view_app', app_id=app_id))

    new_secret = db.rotate_app_secret(app_id)
    flash(f"SECRET ROTATED! {new_secret}", "warning")
    return redirect(url_for('backoffice.view_app', app_id=app_id))


@backoffice_bp.route('/app/<app_id>/api-keys/add', methods=['POST'])
@login_required
def add_api_key(app_id):
    db = get_db()
    if not check_permission(app_id, 2): # Super Admin or Owner
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
    if not check_permission(app_id, 2):
        flash("Access Denied: Only Admins can manage API Keys.", "danger")
        return redirect(url_for('backoffice.view_app', app_id=app_id))
    
    key_name = request.form.get('key_name')
    if key_name:
        db.remove_app_api_key(app_id, key_name)
        flash(f"API Key '{key_name}' removed.", "success")
        
    return redirect(url_for('backoffice.view_app', app_id=app_id))
