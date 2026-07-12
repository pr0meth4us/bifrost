# bifrost/backoffice/auth_routes.py
from flask import render_template, request, redirect, url_for, session, flash
from werkzeug.security import check_password_hash, generate_password_hash
from ..services.email_service import send_invite_email, send_reset_email
from . import backoffice_bp, get_db

@backoffice_bp.route('/login', methods=['GET', 'POST'])
def login():
    db = get_db()
    tenant_app = None

    # Detect Custom Domain / Query Param context
    from flask import g
    if hasattr(g, 'tenant_app'):
        tenant_app = g.tenant_app
    else:
        client_id = request.args.get('client_id')
        if client_id:
            tenant_app = db.db.applications.find_one({"client_id": client_id})

    if request.method == 'POST':
        identifier = request.form.get('email').strip()
        password = request.form.get('password')

        # 1. Heimdall Check
        admin_doc = db.db.admins.find_one({"email": identifier.lower()})
        if admin_doc and check_password_hash(admin_doc['password_hash'], password):
            if admin_doc.get('role') == 'heimdall':
                session['backoffice_user'] = str(admin_doc['_id'])
                session['is_heimdall'] = True
                session['role'] = 'Heimdall'
                return redirect(url_for('backoffice.dashboard'))
            else:
                flash("Role deprecated. Update to 'heimdall'.", "warning")

        # 2. App Tenant Check
        user = db.find_account_by_email(identifier)
        if not user: user = db.find_account_by_username(identifier)

        if user and user.get('password_hash') and check_password_hash(user['password_hash'], password):
            managed_apps = db.get_managed_apps(user['_id'])
            if managed_apps:
                session['backoffice_user'] = str(user['_id'])
                session['is_heimdall'] = False
                session['role'] = 'Tenant'  # General label
                
                if tenant_app:
                    return redirect(url_for('backoffice.view_cms_grid', app_id=str(tenant_app['_id'])))
                return redirect(url_for('backoffice.dashboard'))
            else:
                flash("Access Denied: You do not manage any apps.", "danger")
        else:
            flash("Invalid credentials.", "danger")

    return render_template('backoffice/login.html', tenant_app=tenant_app)


@backoffice_bp.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('backoffice.login'))


@backoffice_bp.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        email = request.form.get('email').strip().lower()
        db = get_db()
        is_heimdall = False
        user = db.db.admins.find_one({"email": email})
        if user:
            is_heimdall = True
        else:
            user = db.find_account_by_email(email)

        if user:
            otp, vid = db.create_otp(email, channel="email")
            if send_reset_email(email, otp):
                session['reset_email'] = email
                session['reset_is_heimdall'] = is_heimdall
                flash(f"Reset code sent to {email}", "success")
                return redirect(url_for('backoffice.reset_password'))
            else:
                flash("Error sending email.", "danger")
        else:
            flash("Email not found.", "danger")
    return render_template('backoffice/forgot_password.html')


@backoffice_bp.route('/reset-password', methods=['GET', 'POST'])
def reset_password():
    email = session.get('reset_email')
    if not email: return redirect(url_for('backoffice.forgot_password'))

    if request.method == 'POST':
        otp_input = request.form.get('otp').strip()
        new_password = request.form.get('password')
        db = get_db()

        if db.verify_otp(email, otp_input):
            hashed = generate_password_hash(new_password)
            is_heimdall = session.get('reset_is_heimdall')
            if is_heimdall:
                db.db.admins.update_one({"email": email}, {"$set": {"password_hash": hashed}})
            else:
                db.db.accounts.update_one({"email": email}, {"$set": {"password_hash": hashed}})

            session.pop('reset_email', None)
            flash("Password updated.", "success")
            return redirect(url_for('backoffice.login'))
        else:
            flash("Invalid OTP.", "danger")

    return render_template('backoffice/reset_password.html', email=email)
