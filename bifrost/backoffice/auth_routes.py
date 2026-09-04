# bifrost/backoffice/auth_routes.py
from datetime import datetime, timezone
from flask import render_template, request, redirect, url_for, session, flash, current_app, make_response
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from werkzeug.security import check_password_hash, generate_password_hash
from ..models.auth import ANY_TENANT
from ..services.email_service import send_invite_email, send_reset_email, send_otp_email
from . import backoffice_bp, get_db

# Admin login rate limit (SOW 4.7). Per source IP, short window.
LOGIN_MAX_ATTEMPTS = 10
LOGIN_WINDOW_SECONDS = 300

# "Remember this device": the 30-minute idle timeout (SOW 4.6) means a normal
# working day costs several sign-ins, and mailing an OTP for every one of them
# trains people to click through codes without reading them. The device stays a
# second factor — it is a signed cookie bound to one account, opted into per
# device, and expires in 30 days — so MFA still holds for every new device.
# It deliberately survives logout: re-prompting there would put the OTP back on
# the exact path this removes.
TRUSTED_DEVICE_COOKIE = "bo_trusted_device"
TRUSTED_DEVICE_DAYS = 30


def _device_serializer():
    return URLSafeTimedSerializer(current_app.config['SECRET_KEY'], salt="backoffice-trusted-device")


def _device_trusted_for(user_id):
    token = request.cookies.get(TRUSTED_DEVICE_COOKIE)
    if not token:
        return False
    try:
        return _device_serializer().loads(token, max_age=TRUSTED_DEVICE_DAYS * 86400) == str(user_id)
    except (BadSignature, SignatureExpired):
        return False


def _issue_session(user_id, is_heimdall, app_id, email=None):
    now = datetime.now(timezone.utc).isoformat()
    session.pop('mfa_pending', None)
    session['backoffice_user'] = str(user_id)
    # Carried alongside the id, not instead of it: the id is the lookup key, the
    # email is what gets written into tenant attestation columns. An ObjectId in
    # a tenant's reviewed_by is unresolvable from their database.
    session['backoffice_email'] = (email or '').lower() or None
    session['is_heimdall'] = is_heimdall
    session['role'] = 'Heimdall' if is_heimdall else 'Tenant'
    session['session_started_at'] = now
    session['last_seen_at'] = now
    session.permanent = True
    if app_id:
        return redirect(url_for('backoffice.view_cms_grid', app_id=app_id))
    return redirect(url_for('backoffice.dashboard'))


def _login_rate_limited():
    """True when this IP has burned its login attempts. Fails open if Redis is down —
    availability of the console during an approval SLA window beats a hard lockout."""
    from .. import redis_client
    if not redis_client:
        return False
    key = f"bo:login:{request.headers.get('X-Forwarded-For', request.remote_addr)}"
    try:
        attempts = redis_client.incr(key)
        if attempts == 1:
            redis_client.expire(key, LOGIN_WINDOW_SECONDS)
        return attempts > LOGIN_MAX_ATTEMPTS
    except Exception:
        return False


def _start_mfa(db, user_doc, is_heimdall, tenant_app):
    """Password checked — now send the second factor. No session is issued yet."""
    email = (user_doc.get('email') or '').lower()
    if not email:
        flash("This account has no email address and cannot complete MFA. Contact an owner.", "danger")
        return redirect(url_for('backoffice.login'))

    if _device_trusted_for(user_doc['_id']):
        return _issue_session(user_doc['_id'], is_heimdall,
                              str(tenant_app['_id']) if tenant_app else None,
                              email=email)

    otp, _ = db.create_otp(email, channel="backoffice_mfa", account_id=user_doc['_id'])
    if not send_otp_email(email, otp, app_name="Bifrost Console"):
        # No session, no mfa_pending: sending them to a verify page for a code
        # that never left the building is how a broken mailbox looks like a
        # broken password. The real cause is in the log as "Email failed:".
        flash("We could not send your sign-in code. Try again, or contact an "
              "owner if it keeps failing.", "danger")
        return redirect(url_for('backoffice.login'))
    session['mfa_pending'] = {
        "user_id": str(user_doc['_id']),
        "email": email,
        "is_heimdall": is_heimdall,
        "app_id": str(tenant_app['_id']) if tenant_app else None,
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    return redirect(url_for('backoffice.mfa'))


def _resolve_tenant_app(db):
    from flask import g
    if hasattr(g, 'tenant_app'):
        return g.tenant_app
    client_id = request.args.get('client_id')
    return db.db.applications.find_one({"client_id": client_id}) if client_id else None


@backoffice_bp.route('/login', methods=['GET', 'POST'])
def login():
    db = get_db()
    tenant_app = _resolve_tenant_app(db)

    if request.method == 'POST':
        if _login_rate_limited():
            flash("Too many sign-in attempts. Try again in a few minutes.", "danger")
            return render_template('backoffice/login.html', tenant_app=tenant_app)

        identifier = (request.form.get('email') or '').strip()
        password = request.form.get('password')

        # 1. Heimdall Check
        admin_doc = db.db.admins.find_one({"email": identifier.lower()})
        if admin_doc and check_password_hash(admin_doc['password_hash'], password):
            if admin_doc.get('role') == 'heimdall':
                return _start_mfa(db, admin_doc, True, tenant_app)
            flash("Role deprecated. Update to 'heimdall'.", "warning")

        # 2. App Tenant Check. Console sign-in searches every directory on
        #    purpose: one person can own apps in more than one of them, and the
        #    managed-apps check below is what actually authorizes them.
        user = db.find_account_by_email(identifier, ANY_TENANT)
        if not user:
            user = db.find_account_by_username(identifier, ANY_TENANT)

        if user and user.get('password_hash') and check_password_hash(user['password_hash'], password):
            if db.get_managed_apps(user['_id']):
                return _start_mfa(db, user, False, tenant_app)
            flash("Access Denied: You do not manage any apps.", "danger")
        else:
            flash("Invalid credentials.", "danger")

    return render_template('backoffice/login.html', tenant_app=tenant_app)


@backoffice_bp.route('/mfa', methods=['GET', 'POST'])
def mfa():
    """Second factor for every console account, every role, no exceptions (SOW 4.1)."""
    pending = session.get('mfa_pending')
    if not pending:
        return redirect(url_for('backoffice.login'))

    if request.method == 'POST':
        db = get_db()
        if db.verify_otp(identifier=pending['email'], code=request.form.get('otp')):
            resp = make_response(_issue_session(pending['user_id'], pending['is_heimdall'],
                                                pending.get('app_id'),
                                                email=pending.get('email')))
            if request.form.get('remember_device'):
                resp.set_cookie(
                    TRUSTED_DEVICE_COOKIE,
                    _device_serializer().dumps(pending['user_id']),
                    max_age=TRUSTED_DEVICE_DAYS * 86400,
                    httponly=True, samesite='Lax',
                    secure=current_app.config.get('SESSION_COOKIE_SECURE', True),
                )
            return resp
        flash("Invalid or expired code.", "danger")

    return render_template('backoffice/mfa.html', email=pending['email'])


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
            user = db.find_account_by_email(email, ANY_TENANT)

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
