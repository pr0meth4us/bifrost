from flask import Blueprint, render_template, request, redirect, flash, current_app, url_for, session
from werkzeug.security import check_password_hash
import jwt
import datetime
import requests
import urllib.parse
from zoneinfo import ZoneInfo
from .. import mongo
from ..models import BifrostDB
from ..services.email_service import send_otp_email

auth_ui_bp = Blueprint('auth_ui', __name__, url_prefix='/auth/ui')
UTC = ZoneInfo("UTC")

def get_app_config(client_id):
    """Helper to fetch App configuration and DB instance."""
    db = BifrostDB(mongo.cx, current_app.config['DB_NAME'])
    return db, db.get_app_by_client_id(client_id)

def create_session_token(user, client_id):
    """Helper to generate the JWT for the client app."""
    token_payload = {
        "sub": str(user['_id']),
        "iss": "bifrost",
        "aud": client_id,
        "iat": datetime.datetime.now(UTC),
        "exp": datetime.datetime.now(UTC) + datetime.timedelta(days=7),
        "email": user.get('email'),
        "name": user.get('display_name'),
        "role": "user"
    }
    return jwt.encode(
        token_payload,
        current_app.config['JWT_SECRET_KEY'],
        algorithm="HS256"
    )

@auth_ui_bp.route('/login', methods=['GET', 'POST'])
def login():
    client_id = request.args.get('client_id')
    if not client_id:
        return render_template('auth/error.html', error="Missing client_id")

    db, app_config = get_app_config(client_id)
    if not app_config:
        return render_template('auth/error.html', error="Invalid client_id")

    if request.method == 'POST':
        identifier = request.form.get('email')
        password = request.form.get('password')

        user = db.find_account_by_email(identifier)
        if not user:
            user = db.find_account_by_username(identifier)

        if user and user.get('password_hash') and check_password_hash(user['password_hash'], password):
            db.link_user_to_app(user['_id'], app_config['_id'])
            token = create_session_token(user, client_id)
            callback_url = app_config.get('app_callback_url')
            separator = '&' if '?' in callback_url else '?'
            return redirect(f"{callback_url}{separator}token={token}")
        else:
            flash("Invalid email or password", "danger")

    return render_template('auth/login.html', app=app_config)


@auth_ui_bp.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    client_id = request.args.get('client_id')
    db, app_config = get_app_config(client_id)

    if request.method == 'POST':
        email = request.form.get('email').strip().lower()
        user = db.find_account_by_email(email)

        if user:
            # Create OTP and get verification ID
            otp, ver_id = db.create_otp(email, channel='email', account_id=user['_id'])

            # Build the URL to the OTP entry page
            verify_url = url_for('auth_ui.verify_otp',
                                 verification_id=ver_id,
                                 client_id=client_id,
                                 _external=True)

            # Send email with OTP
            send_otp_email(
                to_email=email,
                otp=otp,
                app_name=app_config.get('app_name', 'Bifrost'),
                logo_url=app_config.get('app_logo_url'),
                app_url=verify_url
            )

            # Redirect to OTP entry page
            return redirect(verify_url)

        flash("If an account exists, a reset code has been sent.", "info")

    return render_template('auth/forgot_password.html', app=app_config)


@auth_ui_bp.route('/verify-otp', methods=['GET', 'POST'])
def verify_otp():
    ver_id = request.args.get('verification_id')
    client_id = request.args.get('client_id')
    db, app_config = get_app_config(client_id)

    if request.method == 'POST':
        code = request.form.get('otp')
        record = db.verify_otp(verification_id=ver_id, code=code)

        if record:
            # Generate proof token for password reset
            proof_payload = {
                "email": record['identifier'],
                "scope": "credential_change",
                "exp": datetime.datetime.now(UTC) + datetime.timedelta(minutes=10)
            }
            proof_token = jwt.encode(proof_payload, current_app.config['JWT_SECRET_KEY'], algorithm="HS256")
            return redirect(url_for('auth_ui.reset_password', proof_token=proof_token, client_id=client_id))
        else:
            flash("Invalid or expired code.", "danger")

    return render_template('auth/verify_otp.html', app=app_config, verification_id=ver_id)


@auth_ui_bp.route('/reset-password', methods=['GET', 'POST'])
def reset_password():
    proof_token = request.args.get('proof_token')
    client_id = request.args.get('client_id')
    db, app_config = get_app_config(client_id)

    try:
        payload = jwt.decode(proof_token, current_app.config['JWT_SECRET_KEY'], algorithms=["HS256"])
        email = payload['email']
    except:
        flash("Session expired. Please start over.", "danger")
        return redirect(url_for('auth_ui.forgot_password', client_id=client_id))

    if request.method == 'POST':
        new_password = request.form.get('password')
        db.update_password(email, new_password)
        flash("Password updated successfully. Please login.", "success")
        return redirect(url_for('auth_ui.login', client_id=client_id))

    return render_template('auth/reset_password.html', app=app_config, proof_token=proof_token)


@auth_ui_bp.route('/set-password', methods=['GET', 'POST'])
def set_password():
    """
    NEW ROUTE: For invited users to activate their account.
    They enter the OTP they received and set their password in one form.
    """
    ver_id = request.args.get('verification_id')
    client_id = request.args.get('client_id')

    db, app_config = get_app_config(client_id)

    if not ver_id or not app_config:
        return render_template('auth/error.html', error="Invalid invite link")

    if request.method == 'POST':
        otp = request.form.get('otp')
        password = request.form.get('password')

        # Verify OTP
        record = db.verify_otp(verification_id=ver_id, code=otp)

        if record:
            email = record['identifier']
            user = db.find_account_by_email(email)

            if user:
                # User exists - just set password
                db.update_password(email, password)
                db.link_user_to_app(user['_id'], app_config['_id'])

                # Log them in automatically
                token = create_session_token(user, client_id)
                callback_url = app_config.get('app_callback_url')
                separator = '&' if '?' in callback_url else '?'
                flash("Account activated! Welcome.", "success")
                return redirect(f"{callback_url}{separator}token={token}")
            else:
                flash("Account setup error. Please contact support.", "danger")
        else:
            flash("Invalid or expired code.", "danger")

    return render_template('auth/set_password.html', app=app_config, verification_id=ver_id)


# ---------------------------------------------------------
# SSO MULTI-PROVIDER AUTHENTICATION
# ---------------------------------------------------------

@auth_ui_bp.route('/sso/<provider>/login')
def sso_login(provider):
    client_id = request.args.get('client_id')
    if not client_id:
        return render_template('auth/error.html', error="Missing client_id")

    db, app_config = get_app_config(client_id)
    if not app_config:
        return render_template('auth/error.html', error="Invalid client_id")

    session['sso_client_id'] = client_id
    redirect_uri = url_for('auth_ui.sso_callback', provider=provider, _external=True)

    if provider == "google":
        google_id = current_app.config.get('GOOGLE_CLIENT_ID')
        if not google_id:
            return render_template('auth/error.html', error="Google SSO is not configured on this server")
        
        params = {
            "client_id": google_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": "openid email profile",
            "state": client_id
        }
        auth_url = f"https://accounts.google.com/o/oauth2/v2/auth?{urllib.parse.urlencode(params)}"
        return redirect(auth_url)

    elif provider == "github":
        github_id = current_app.config.get('GITHUB_CLIENT_ID')
        if not github_id:
            return render_template('auth/error.html', error="GitHub SSO is not configured on this server")
        
        params = {
            "client_id": github_id,
            "redirect_uri": redirect_uri,
            "scope": "user:email",
            "state": client_id
        }
        auth_url = f"https://github.com/login/oauth/authorize?{urllib.parse.urlencode(params)}"
        return redirect(auth_url)

    return render_template('auth/error.html', error=f"Unsupported SSO provider: {provider}")


@auth_ui_bp.route('/sso/<provider>/callback')
def sso_callback(provider):
    code = request.args.get('code')
    if not code:
        return render_template('auth/error.html', error="Auth code missing from SSO callback redirect")

    client_id = session.get('sso_client_id') or request.args.get('state')
    if not client_id:
        return render_template('auth/error.html', error="SSO login session expired. Please try again.")

    db, app_config = get_app_config(client_id)
    if not app_config:
        return render_template('auth/error.html', error="Invalid application client configuration")

    redirect_uri = url_for('auth_ui.sso_callback', provider=provider, _external=True)
    email = None
    display_name = "SSO User"
    provider_id = None

    try:
        if provider == "google":
            res = requests.post("https://oauth2.googleapis.com/token", data={
                "code": code,
                "client_id": current_app.config['GOOGLE_CLIENT_ID'],
                "client_secret": current_app.config['GOOGLE_CLIENT_SECRET'],
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code"
            }, headers={"Accept": "application/json"}, timeout=10)
            res.raise_for_status()
            tokens = res.json()
            access_token = tokens.get("access_token")

            profile_res = requests.get("https://www.googleapis.com/oauth2/v2/userinfo", headers={
                "Authorization": f"Bearer {access_token}"
            }, timeout=10)
            profile_res.raise_for_status()
            profile = profile_res.json()
            email = profile.get("email")
            display_name = profile.get("name", email.split('@')[0])
            provider_id = str(profile.get("id"))

        elif provider == "github":
            res = requests.post("https://github.com/login/oauth/access_token", data={
                "code": code,
                "client_id": current_app.config['GITHUB_CLIENT_ID'],
                "client_secret": current_app.config['GITHUB_CLIENT_SECRET'],
                "redirect_uri": redirect_uri
            }, headers={"Accept": "application/json"}, timeout=10)
            res.raise_for_status()
            tokens = res.json()
            access_token = tokens.get("access_token")

            profile_res = requests.get("https://api.github.com/user", headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/vnd.github.v3+json"
            }, timeout=10)
            profile_res.raise_for_status()
            profile = profile_res.json()
            provider_id = str(profile.get("id"))
            display_name = profile.get("name") or profile.get("login") or "GitHub User"

            email_res = requests.get("https://api.github.com/user/emails", headers={
                "Authorization": f"Bearer {access_token}"
            }, timeout=10)
            if email_res.status_code == 200:
                emails = email_res.json()
                primary_email = next((e.get("email") for e in emails if e.get("primary")), None)
                email = primary_email or (emails[0].get("email") if emails else None)

    except Exception as e:
        return render_template('auth/error.html', error=f"SSO Handshake failed: {str(e)}")

    if not provider_id or not email:
        return render_template('auth/error.html', error="Could not retrieve email or identity ID from the SSO provider")

    # Find or provision account
    user = db.find_account_by_sso(provider, provider_id)
    if not user:
        user = db.find_account_by_email(email)
        if user:
            db.link_sso(user['_id'], provider, provider_id)
        else:
            account_data = {
                "client_id": client_id,
                "email": email,
                "display_name": display_name,
                "auth_providers": [provider],
                f"{provider}_id": provider_id
            }
            new_id = db.create_account(account_data)
            db.link_sso(new_id, provider, provider_id)
            user = db.find_account_by_id(new_id)

    db.link_user_to_app(user['_id'], app_config['_id'])
    token = create_session_token(user, client_id)
    callback_url = app_config.get('app_callback_url')
    separator = '&' if '?' in callback_url else '?'
    return redirect(f"{callback_url}{separator}token={token}")