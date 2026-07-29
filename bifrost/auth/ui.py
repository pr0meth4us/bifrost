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
from ..services.sms_service import send_otp_sms
from ..utils.token import create_client_jwt

auth_ui_bp = Blueprint('auth_ui', __name__, url_prefix='/auth/ui')
UTC = ZoneInfo("UTC")

def get_app_config(client_id):
    """Helper to fetch App configuration and DB instance."""
    db = BifrostDB(mongo.cx, current_app.config['DB_NAME'])
    return db, db.get_app_by_client_id(client_id)

def create_session_token(user, client_id, db, app_config):
    """Helper to generate the JWT for the client app."""
    return create_client_jwt(user, client_id, db, app_config)

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

        user = db.find_account_by_email(identifier, client_id)
        if not user:
            user = db.find_account_by_username(identifier, client_id)

        if user and user.get('password_hash') and check_password_hash(user['password_hash'], password):
            db.link_user_to_app(user['_id'], app_config['_id'])
            
            # Check if this is an OIDC login flow
            oidc_context = session.get('oidc_auth')
            if oidc_context and oidc_context.get('client_id') == client_id:
                # Generate authorization code
                import secrets
                import time
                code = secrets.token_urlsafe(32)
                
                db.db.auth_codes.insert_one({
                    "code": code,
                    "client_id": client_id,
                    "user_id": user['_id'],
                    "nonce": oidc_context.get('nonce'),
                    "expires_at": time.time() + 600  # 10 minutes
                })
                
                # Clear session
                session.pop('oidc_auth', None)
                
                redirect_uri = oidc_context.get('redirect_uri')
                state = oidc_context.get('state')
                
                params = {"code": code}
                if state:
                    params["state"] = state
                
                separator = '&' if '?' in redirect_uri else '?'
                query_string = urllib.parse.urlencode(params)
                return redirect(f"{redirect_uri}{separator}{query_string}")

            # Standard Bifrost login flow
            token = create_session_token(user, client_id, db, app_config)
            callback_url = app_config.get('app_callback_url')
            separator = '&' if '?' in callback_url else '?'
            return redirect(f"{callback_url}{separator}token={token}")
        else:
            flash("Invalid email or password", "danger")

    sso_enabled = app_config.get("enabled_services", {}).get("sso", True)
    phone_otp_enabled = app_config.get("enabled_services", {}).get("phone_otp", True)
    email_otp_enabled = app_config.get("enabled_services", {}).get("email_otp", True)

    sso_providers = {
        "google": bool(current_app.config.get('GOOGLE_CLIENT_ID')) if sso_enabled else False,
        "github": bool(current_app.config.get('GITHUB_CLIENT_ID')) if sso_enabled else False,
        "microsoft": bool(current_app.config.get('MICROSOFT_CLIENT_ID')) if sso_enabled else False,
        "apple": bool(current_app.config.get('APPLE_CLIENT_ID')) if sso_enabled else False,
        "facebook": bool(current_app.config.get('FACEBOOK_CLIENT_ID')) if sso_enabled else False
    }
    return render_template('auth/login.html', app=app_config, sso_providers=sso_providers, phone_otp_enabled=phone_otp_enabled, email_otp_enabled=email_otp_enabled)


@auth_ui_bp.route('/register', methods=['GET', 'POST'])
def register():
    client_id = request.args.get('client_id')
    if not client_id:
        return render_template('auth/error.html', error="Missing client_id")

    db, app_config = get_app_config(client_id)
    if not app_config:
        return render_template('auth/error.html', error="Invalid client_id")

    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        display_name = request.form.get('display_name')

        if db.find_account_by_email(email, client_id):
            flash("An account with that email already exists.", "danger")
            return render_template('auth/register.html', app=app_config)

        # Create user
        user_id = db.create_account({
            "client_id": client_id,
            "email": email,
            "password": password,
            "display_name": display_name
        })
        user = db.find_account_by_email(email, client_id)
        db.link_user_to_app(user['_id'], app_config['_id'])

        # Check OIDC flow
        if 'auth_code' in session:
            user_data = {
                "id": str(user['_id']),
                "email": user.get('email'),
                "name": user.get('display_name', ''),
            }
            code = session.pop('auth_code')
            session[f"code_data_{code}"] = user_data

            redirect_uri = session.pop('redirect_uri', None)
            state = session.pop('state', None)
            if not redirect_uri:
                return render_template('auth/error.html', error="Missing redirect_uri in session")
                
            import urllib.parse
            params = {"code": code}
            if state:
                params["state"] = state
            
            separator = '&' if '?' in redirect_uri else '?'
            query_string = urllib.parse.urlencode(params)
            return redirect(f"{redirect_uri}{separator}{query_string}")

        # Standard flow
        token = create_session_token(user, client_id, db, app_config)
        callback_url = app_config.get('app_callback_url')
        separator = '&' if '?' in callback_url else '?'
        return redirect(f"{callback_url}{separator}token={token}")

    return render_template('auth/register.html', app=app_config)

@auth_ui_bp.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    client_id = request.args.get('client_id')
    db, app_config = get_app_config(client_id)

    if not app_config.get("enabled_services", {}).get("email_otp", True):
        flash("Email password reset service is disabled for this application.", "danger")
        return redirect(url_for('auth_ui.login', client_id=client_id))

    if request.method == 'POST':
        email = request.form.get('email').strip().lower()
        user = db.find_account_by_email(email, client_id)

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
            user = db.find_account_by_email(email, client_id)

            if user:
                # User exists - just set password
                db.update_password(email, password)
                db.link_user_to_app(user['_id'], app_config['_id'])

                # Log them in automatically
                token = create_session_token(user, client_id, db, app_config)
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

    if not app_config.get("enabled_services", {}).get("sso", True):
        return render_template('auth/error.html', error="OAuth SSO Service is disabled for this application")

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

    elif provider == "microsoft":
        ms_id = current_app.config.get('MICROSOFT_CLIENT_ID')
        if not ms_id:
            return render_template('auth/error.html', error="Microsoft SSO is not configured on this server")
        params = {
            "client_id": ms_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": "openid email profile User.Read",
            "state": client_id
        }
        auth_url = f"https://login.microsoftonline.com/common/oauth2/v2.0/authorize?{urllib.parse.urlencode(params)}"
        return redirect(auth_url)

    elif provider == "apple":
        apple_id = current_app.config.get('APPLE_CLIENT_ID')
        if not apple_id:
            return render_template('auth/error.html', error="Apple SSO is not configured on this server")
        params = {
            "client_id": apple_id,
            "redirect_uri": redirect_uri,
            "response_type": "code id_token",
            "response_mode": "form_post",
            "scope": "name email",
            "state": client_id
        }
        auth_url = f"https://appleid.apple.com/auth/authorize?{urllib.parse.urlencode(params)}"
        return redirect(auth_url)

    elif provider == "facebook":
        fb_id = current_app.config.get('FACEBOOK_CLIENT_ID')
        if not fb_id:
            return render_template('auth/error.html', error="Facebook SSO is not configured on this server")
        params = {
            "client_id": fb_id,
            "redirect_uri": redirect_uri,
            "scope": "email public_profile",
            "state": client_id
        }
        auth_url = f"https://www.facebook.com/v12.0/dialog/oauth?{urllib.parse.urlencode(params)}"
        return redirect(auth_url)

    return render_template('auth/error.html', error=f"Unsupported SSO provider: {provider}")


@auth_ui_bp.route('/sso/<provider>/callback', methods=['GET', 'POST'])
def sso_callback(provider):
    code = request.form.get('code') or request.args.get('code')
    id_token = request.form.get('id_token')
    
    state = request.form.get('state') or request.args.get('state')
    client_id = session.get('sso_client_id') or state
    if not client_id:
        return render_template('auth/error.html', error="SSO login session expired. Please try again.")

    db, app_config = get_app_config(client_id)
    if not app_config:
        return render_template('auth/error.html', error="Invalid application client configuration")

    if not app_config.get("enabled_services", {}).get("sso", True):
        return render_template('auth/error.html', error="OAuth SSO Service is disabled for this application")

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

        elif provider == "microsoft":
            res = requests.post("https://login.microsoftonline.com/common/oauth2/v2.0/token", data={
                "code": code,
                "client_id": current_app.config['MICROSOFT_CLIENT_ID'],
                "client_secret": current_app.config['MICROSOFT_CLIENT_SECRET'],
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code"
            }, timeout=10)
            res.raise_for_status()
            tokens = res.json()
            access_token = tokens.get("access_token")

            profile_res = requests.get("https://graph.microsoft.com/v1.0/me", headers={
                "Authorization": f"Bearer {access_token}"
            }, timeout=10)
            profile_res.raise_for_status()
            profile = profile_res.json()
            email = profile.get("mail") or profile.get("userPrincipalName")
            display_name = profile.get("displayName", email.split('@')[0] if email else "Microsoft User")
            provider_id = str(profile.get("id"))

        elif provider == "apple":
            if id_token:
                decoded = jwt.decode(id_token, options={"verify_signature": False})
                email = decoded.get("email")
                provider_id = str(decoded.get("sub"))
                
                user_payload = request.form.get('user')
                if user_payload:
                    try:
                        user_info = json.loads(user_payload)
                        name_info = user_info.get("name", {})
                        first = name_info.get("firstName", "")
                        last = name_info.get("lastName", "")
                        display_name = f"{first} {last}".strip() or "Apple User"
                    except Exception:
                        pass
                if not display_name or display_name == "SSO User":
                    display_name = email.split('@')[0] if email else "Apple User"
            else:
                res = requests.post("https://appleid.apple.com/auth/token", data={
                    "code": code,
                    "client_id": current_app.config['APPLE_CLIENT_ID'],
                    "client_secret": current_app.config['APPLE_CLIENT_SECRET'],
                    "redirect_uri": redirect_uri,
                    "grant_type": "authorization_code"
                }, timeout=10)
                res.raise_for_status()
                tokens = res.json()
                id_token = tokens.get("id_token")
                decoded = jwt.decode(id_token, options={"verify_signature": False})
                email = decoded.get("email")
                provider_id = str(decoded.get("sub"))
                display_name = email.split('@')[0] if email else "Apple User"

        elif provider == "facebook":
            res = requests.get("https://graph.facebook.com/v12.0/oauth/access_token", params={
                "code": code,
                "client_id": current_app.config['FACEBOOK_CLIENT_ID'],
                "client_secret": current_app.config['FACEBOOK_CLIENT_SECRET'],
                "redirect_uri": redirect_uri
            }, timeout=10)
            res.raise_for_status()
            tokens = res.json()
            access_token = tokens.get("access_token")

            profile_res = requests.get("https://graph.facebook.com/me", params={
                "fields": "id,name,email",
                "access_token": access_token
            }, timeout=10)
            profile_res.raise_for_status()
            profile = profile_res.json()
            email = profile.get("email")
            display_name = profile.get("name", "Facebook User")
            provider_id = str(profile.get("id"))

    except Exception as e:
        return render_template('auth/error.html', error=f"SSO Handshake failed: {str(e)}")

    if not provider_id or not email:
        return render_template('auth/error.html', error="Could not retrieve email or identity ID from the SSO provider")

    # Find or provision account
    user = db.find_account_by_sso(provider, provider_id, client_id)
    if not user:
        user = db.find_account_by_email(email, client_id)
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
    token = create_session_token(user, client_id, db, app_config)
    callback_url = app_config.get('app_callback_url')
    separator = '&' if '?' in callback_url else '?'
    return redirect(f"{callback_url}{separator}token={token}")


# ---------------------------------------------------------
# PHONE OTP MULTI-PROVIDER AUTHENTICATION
# ---------------------------------------------------------

@auth_ui_bp.route('/request-phone-otp', methods=['POST'])
def request_phone_otp_ui():
    phone = request.form.get('phone_number')
    client_id = request.form.get('client_id')
    
    if not phone or not client_id:
        return render_template('auth/error.html', error="Missing phone_number or client_id")

    db, app_config = get_app_config(client_id)
    if not app_config:
        return render_template('auth/error.html', error="Invalid client_id")

    if not app_config.get("enabled_services", {}).get("phone_otp", True):
        return render_template('auth/error.html', error="Phone SMS OTP service is disabled for this application")

    phone = phone.strip()
    code, verification_id = db.create_otp(phone, channel="sms")
    
    app_name = app_config.get('app_name', 'Bifrost Identity')
    if send_otp_sms(to_phone=phone, otp=code, app_name=app_name):
        return redirect(url_for('auth_ui.verify_phone_otp_ui', 
                                verification_id=verification_id, 
                                client_id=client_id))
    else:
        flash("Failed to dispatch SMS code. Please check server configurations.", "danger")
        return redirect(url_for('auth_ui.login', client_id=client_id))


@auth_ui_bp.route('/verify-phone-otp', methods=['GET', 'POST'])
def verify_phone_otp_ui():
    ver_id = request.args.get('verification_id')
    client_id = request.args.get('client_id')
    
    db, app_config = get_app_config(client_id)
    if not app_config or not ver_id:
        return render_template('auth/error.html', error="Invalid verification request parameters")

    if not app_config.get("enabled_services", {}).get("phone_otp", True):
        return render_template('auth/error.html', error="Phone SMS OTP service is disabled for this application")

    if request.method == 'POST':
        code = request.form.get('otp')
        record = db.verify_otp(verification_id=ver_id, code=code)

        if record:
            phone_number = record['identifier']
            user = db.find_account_by_phone(phone_number, client_id)
            
            if not user:
                account_data = {
                    "client_id": client_id,
                    "phone_number": phone_number,
                    "display_name": f"User {phone_number[-4:]}",
                    "auth_providers": ["sms"]
                }
                new_id = db.create_account(account_data)
                user = db.find_account_by_id(new_id)

            db.link_user_to_app(user['_id'], app_config['_id'])
            token = create_session_token(user, client_id, db, app_config)
            callback_url = app_config.get('app_callback_url')
            separator = '&' if '?' in callback_url else '?'
            return redirect(f"{callback_url}{separator}token={token}")
        else:
            flash("Invalid or expired SMS verification code.", "danger")

    action_url = url_for('auth_ui.verify_phone_otp_ui', verification_id=ver_id, client_id=client_id)
    resend_url = url_for('auth_ui.login', client_id=client_id)
    description = "Check your phone for a 6-digit SMS OTP code."
    
    return render_template('auth/verify_otp.html', 
                           app=app_config, 
                           verification_id=ver_id,
                           action_url=action_url,
                           resend_url=resend_url,
                           description=description)