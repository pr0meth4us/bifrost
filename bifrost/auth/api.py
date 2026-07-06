from flask import Blueprint, request, jsonify, current_app
from datetime import datetime
from zoneinfo import ZoneInfo
import jwt
from werkzeug.security import check_password_hash
import logging
from bson import ObjectId
from bot.main import process_webhook_update
import asyncio
from .. import mongo
from ..models import BifrostDB
from ..services.email_service import send_otp_email
from ..services.sms_service import send_otp_sms
from ..utils.telegram import verify_telegram_data

auth_api_bp = Blueprint('auth_api', __name__, url_prefix='/auth/api')
UTC_TZ = ZoneInfo("UTC")
log = logging.getLogger(__name__)


# =========================================================
#  SECTION 1: PUBLIC OTP & ACCOUNT MANAGEMENT (HEADLESS)
# =========================================================

@auth_api_bp.route('/check-email', methods=['POST'])
def check_email():
    """
    Checks if an email or username is already registered.
    Useful for the frontend to decide whether to show 'Login' or 'Register'.
    Payload: { "email": "..." } OR { "username": "..." }
    """
    data = request.json
    identifier = data.get('email') or data.get('username')
    client_id = data.get('client_id')

    if not client_id:
        return jsonify({"error": "Missing client_id"}), 400

    db = BifrostDB(mongo.cx, current_app.config['DB_NAME'])

    # Check both fields
    user = db.find_account_by_email(identifier, client_id)
    if not user:
        user = db.find_account_by_username(identifier, client_id)

    return jsonify({"exists": bool(user)})


@auth_api_bp.route('/request-email-otp', methods=['POST'])
def request_email_otp():
    """
    Generates an OTP for an email address and sends it via SMTP.
    Payload: { "email": "user@example.com", "client_id": "..." }
    """
    data = request.json
    email = data.get('email')
    client_id = data.get('client_id')

    if not email or not client_id:
        return jsonify({"error": "Missing email or client_id"}), 400

    db = BifrostDB(mongo.cx, current_app.config['DB_NAME'])

    # 1. Validate Client & Get App Name for Branding
    app_config = db.get_app_by_client_id(client_id)
    if not app_config:
        return jsonify({"error": "Invalid client_id"}), 401

    # Default to 'Bifrost Identity' if app_name isn't set
    app_name = app_config.get('app_name', 'Bifrost Identity')

    # 2. Generate Code
    code, verification_id = db.create_otp(email, channel="email")

    # 3. Send Email with Dynamic Branding
    if send_otp_email(to_email=email, otp=code, app_name=app_name):
        return jsonify({
            "message": "OTP sent successfully",
            "verification_id": verification_id
        })
    else:
        return jsonify({"error": "Failed to send email. Check server logs."}), 500


@auth_api_bp.route('/verify-email-otp', methods=['POST'])
def verify_email_otp():
    """
    Verifies the code and returns a 'Proof Token'.
    Payload: { "verification_id": "...", "code": "123456" }
    """
    data = request.json
    verification_id = data.get('verification_id')
    code = data.get('code')

    if not verification_id or not code:
        return jsonify({"error": "Missing verification_id or code"}), 400

    db = BifrostDB(mongo.cx, current_app.config['DB_NAME'])

    # 1. Lookup OTP Record (Safe peek)
    try:
        # Use ObjectId directly from bson import
        oid = db.db.verification_codes.find_one({"_id": ObjectId(verification_id)})
    except Exception as e:
        log.error(f"Error looking up verification ID: {e}")
        return jsonify({"error": "Invalid verification ID format"}), 400

    if not oid:
        return jsonify({"error": "Invalid or expired verification session"}), 400

    email = oid['identifier']

    # 2. Verify and Consume (Delete) the OTP
    if not db.verify_otp(verification_id=verification_id, code=code):
        return jsonify({"error": "Invalid code"}), 401

    # 3. Issue Proof Token (Short lived 5-min token for setting credentials)
    proof_payload = {
        "email": email,
        "scope": "credential_change",
        "verified": True,
        "iss": "bifrost",
        "exp": datetime.now(UTC_TZ).timestamp() + 300 # 5 minutes
    }

    proof_token = jwt.encode(
        proof_payload,
        current_app.config['JWT_SECRET_KEY'],
        algorithm="HS256"
    )

    return jsonify({
        "success": True,
        "proof_token": proof_token,
        "email": email
    })


@auth_api_bp.route('/request-phone-otp', methods=['POST'])
def request_phone_otp():
    """
    Generates an OTP for a phone number and dispatches via Twilio SMS.
    Payload: { "phone_number": "+85512345678", "client_id": "..." }
    """
    data = request.json
    phone = data.get('phone_number')
    client_id = data.get('client_id')

    if not phone or not client_id:
        return jsonify({"error": "Missing phone_number or client_id"}), 400

    db = BifrostDB(mongo.cx, current_app.config['DB_NAME'])
    app_config = db.get_app_by_client_id(client_id)
    if not app_config:
        return jsonify({"error": "Invalid client_id"}), 401

    app_name = app_config.get('app_name', 'Bifrost Identity')
    code, verification_id = db.create_otp(phone, channel="sms")

    if send_otp_sms(to_phone=phone, otp=code, app_name=app_name):
        return jsonify({
            "message": "SMS OTP sent successfully",
            "verification_id": verification_id
        })
    else:
        return jsonify({"error": "Failed to send SMS. Check server logs."}), 500


@auth_api_bp.route('/verify-phone-otp', methods=['POST'])
def verify_phone_otp():
    """
    Verifies the phone OTP and returns a signed proof token.
    Payload: { "verification_id": "...", "code": "123456" }
    """
    data = request.json
    verification_id = data.get('verification_id')
    code = data.get('code')

    if not verification_id or not code:
        return jsonify({"error": "Missing verification_id or code"}), 400

    db = BifrostDB(mongo.cx, current_app.config['DB_NAME'])

    try:
        oid = db.db.verification_codes.find_one({"_id": ObjectId(verification_id)})
    except Exception:
        return jsonify({"error": "Invalid verification ID format"}), 400

    if not oid:
        return jsonify({"error": "Invalid or expired verification session"}), 400

    phone = oid['identifier']

    if not db.verify_otp(verification_id=verification_id, code=code):
        return jsonify({"error": "Invalid code"}), 401

    proof_payload = {
        "phone_number": phone,
        "scope": "credential_change",
        "verified": True,
        "iss": "bifrost",
        "exp": datetime.now(UTC_TZ).timestamp() + 300
    }

    proof_token = jwt.encode(
        proof_payload,
        current_app.config['JWT_SECRET_KEY'],
        algorithm="HS256"
    )

    return jsonify({
        "success": True,
        "proof_token": proof_token,
        "phone_number": phone
    })


@auth_api_bp.route('/complete-registration', methods=['POST'])
def complete_registration():
    """
    Finalizes account creation.
    Requires a valid 'Proof Token' from verify-email-otp.
    Payload: { 
        "proof_token": "...", 
        "password": "...", 
        "display_name": "...", 
        "username": "...",
        "client_id": "..." 
    }
    """
    data = request.json
    proof_token = data.get('proof_token')
    password = data.get('password')
    display_name = data.get('display_name')
    username = data.get('username')
    client_id = data.get('client_id')

    if not proof_token or not password or not client_id:
        return jsonify({"error": "Missing required fields"}), 400

    # 1. Verify Proof Token
    try:
        payload = jwt.decode(proof_token, current_app.config['JWT_SECRET_KEY'], algorithms=["HS256"])
        if payload.get('scope') != 'credential_change' or not payload.get('verified'):
            raise jwt.InvalidTokenError("Invalid scope")
        email = payload.get('email')
    except jwt.InvalidTokenError:
        return jsonify({"error": "Invalid or expired proof token"}), 403

    db = BifrostDB(mongo.cx, current_app.config['DB_NAME'])
    app_config = db.get_app_by_client_id(client_id)
    if not app_config:
        return jsonify({"error": "Invalid client_id"}), 401

    # 2. Check Username Uniqueness if provided
    if username and db.find_account_by_username(username, client_id):
        return jsonify({"error": "Username already taken"}), 409

    # 3. Create or Update Account
    existing_user = db.find_account_by_email(email, client_id)

    if existing_user:
        db.update_password(email, password, client_id)
        # If updating, optionally set username if not set? 
        # For safety, we only set username on creation or explicit profile update,
        # but here we can allow it if the user doesn't have one.
        if username and not existing_user.get('username'):
            db.db.accounts.update_one({"_id": existing_user['_id']}, {"$set": {"username": username.lower()}})

        user_id = existing_user['_id']
    else:
        user_id = db.create_account({
            "client_id": client_id,
            "email": email,
            "username": username,
            "password": password,
            "display_name": display_name or username or email.split('@')[0],
            "auth_providers": ["email"]
        })

    # 4. Link User to App
    db.link_user_to_app(user_id, app_config['_id'])

    # 5. Issue App JWT
    token_payload = {
        "sub": str(user_id),
        "iss": "bifrost",
        "aud": client_id,
        "iat": datetime.now(UTC_TZ),
        "exp": datetime.now(UTC_TZ).timestamp() + 3600 * 24 * 7 # 7 Days
    }

    encoded_jwt = jwt.encode(token_payload, current_app.config['JWT_SECRET_KEY'], algorithm="HS256")

    return jsonify({
        "success": True,
        "jwt": encoded_jwt,
        "account_id": str(user_id),
        "username": username,
        "display_name": display_name or email
    })


@auth_api_bp.route('/reset-password', methods=['POST'])
def reset_password():
    """
    Resets password using a Proof Token.
    Payload: { "proof_token": "...", "password": "..." }
    """
    data = request.json
    proof_token = data.get('proof_token')
    password = data.get('password')
    client_id = data.get('client_id')

    if not proof_token or not password or not client_id:
        return jsonify({"error": "Missing data"}), 400

    # 1. Verify Proof Token
    try:
        payload = jwt.decode(proof_token, current_app.config['JWT_SECRET_KEY'], algorithms=["HS256"])
        if payload.get('scope') != 'credential_change':
            raise jwt.InvalidTokenError("Invalid scope")
        email = payload.get('email')
    except jwt.InvalidTokenError:
        return jsonify({"error": "Invalid or expired proof token"}), 403

    db = BifrostDB(mongo.cx, current_app.config['DB_NAME'])

    user = db.find_account_by_email(email, client_id)
    if not user:
        return jsonify({"error": "User not found"}), 404

    # 2. Update Password
    db.update_password(email, password, client_id)

    return jsonify({"success": True, "message": "Password updated successfully"})


# =========================================================
#  SECTION 2: LOGIN ENDPOINTS
# =========================================================

@auth_api_bp.route('/login', methods=['POST'])
def login():
    """
    Standard Email/Username + Password Login.
    """
    data = request.json
    client_id = data.get('client_id')
    # Allow 'identifier', 'email', or 'username'
    identifier = data.get('identifier') or data.get('email') or data.get('username')
    password = data.get('password')

    if not client_id or not identifier or not password:
        return jsonify({"error": "Missing credentials"}), 400

    db = BifrostDB(mongo.cx, current_app.config['DB_NAME'])
    app_config = db.get_app_by_client_id(client_id)

    if not app_config:
        return jsonify({"error": "Invalid client_id"}), 401

    # Try finding by email first, then username
    user = db.find_account_by_email(identifier, client_id)
    if not user:
        user = db.find_account_by_username(identifier, client_id)

    # Validate Password
    if not user or not user.get('password_hash') or not check_password_hash(user['password_hash'], password):
        return jsonify({"error": "Invalid credentials"}), 401

    if not user.get('is_active', True):
        return jsonify({"error": "Account is disabled"}), 403

    # Link User to App
    db.link_user_to_app(user['_id'], app_config['_id'])
    role = db.get_user_role_for_app(user['_id'], app_config['_id']) or "user"

    # Issue JWT
    token_payload = {
        "sub": str(user['_id']),
        "iss": "bifrost",
        "aud": client_id,
        "iat": datetime.now(UTC_TZ),
        "exp": datetime.now(UTC_TZ).timestamp() + 3600 * 24 * 7
    }

    encoded_jwt = jwt.encode(token_payload, current_app.config['JWT_SECRET_KEY'], algorithm="HS256")

    return jsonify({
        "jwt": encoded_jwt,
        "account_id": str(user['_id']),
        "username": user.get('username'),
        "display_name": user.get('display_name'),
        "email": user.get('email', ''),
        "role": role
    })


@auth_api_bp.route('/verify-otp-login', methods=['POST'])
def verify_otp_login():
    """
    Verifies a code for Login (Telegram mainly).
    """
    data = request.json
    client_id = data.get('client_id')
    code = data.get('code')

    if not client_id or not code:
        return jsonify({"error": "Missing client_id or code"}), 400

    db = BifrostDB(mongo.cx, current_app.config['DB_NAME'])
    app_config = db.get_app_by_client_id(client_id)

    if not app_config:
        return jsonify({"error": "Invalid client_id"}), 401

    # Verify Code
    telegram_id = db.verify_and_consume_code(code)

    if not telegram_id:
        return jsonify({"error": "Invalid or expired code"}), 401

    # Find or Create Account
    user = db.find_account_by_telegram(telegram_id, client_id)

    if not user:
        user_id = db.create_account({
            "client_id": client_id,
            "telegram_id": telegram_id,
            "display_name": "Telegram User",
            "auth_providers": ["telegram"]
        })
        username = None
    else:
        user_id = user['_id']
        username = user.get('username')

    db.link_user_to_app(user_id, app_config['_id'])
    role = db.get_user_role_for_app(user_id, app_config['_id']) or "user"

    token_payload = {
        "sub": str(user_id),
        "iss": "bifrost",
        "aud": client_id,
        "iat": datetime.now(UTC_TZ),
        "exp": datetime.now(UTC_TZ).timestamp() + 3600 * 24 * 7
    }

    encoded_jwt = jwt.encode(token_payload, current_app.config['JWT_SECRET_KEY'], algorithm="HS256")

    return jsonify({
        "jwt": encoded_jwt,
        "account_id": str(user_id),
        "username": username,
        "display_name": "Telegram User" if not user else user.get('display_name'),
        "email": "" if not user else user.get('email', ''),
        "role": role
    })


@auth_api_bp.route('/telegram-login', methods=['POST'])
def telegram_login():
    """
    Headless/Widget Telegram Login.
    """
    data = request.json
    client_id = data.get('client_id')
    tg_data = data.get('telegram_data')

    if not client_id or not tg_data:
        return jsonify({"error": "Missing client_id or telegram_data"}), 400

    db = BifrostDB(mongo.cx, current_app.config['DB_NAME'])
    app_config = db.get_app_by_client_id(client_id)

    if not app_config:
        return jsonify({"error": "Invalid client_id"}), 401

    bot_token = app_config.get("telegram_bot_token")
    if not bot_token:
        return jsonify({"error": "Server misconfiguration: No Bot Token found"}), 500

    if not verify_telegram_data(tg_data, bot_token):
        return jsonify({"error": "Authentication verification failed"}), 401

    telegram_id = str(tg_data['id'])
    user = db.find_account_by_telegram(telegram_id, client_id)

    if not user:
        user_id = db.create_account({
            "client_id": client_id,
            "telegram_id": telegram_id,
            "display_name": tg_data.get('first_name', 'Unknown'),
            "auth_providers": ["telegram"]
        })
        username = None
    else:
        user_id = user['_id']
        username = user.get('username')

    db.link_user_to_app(user_id, app_config['_id'])
    role = db.get_user_role_for_app(user_id, app_config['_id']) or "user"

    token_payload = {
        "sub": str(user_id),
        "iss": "bifrost",
        "aud": client_id,
        "iat": datetime.now(UTC_TZ),
        "exp": datetime.now(UTC_TZ).timestamp() + 3600 * 24 * 7
    }

    encoded_jwt = jwt.encode(token_payload, current_app.config['JWT_SECRET_KEY'], algorithm="HS256")

    return jsonify({
        "jwt": encoded_jwt,
        "account_id": str(user_id),
        "username": username,
        "display_name": user.get('display_name') if user else "Telegram User",
        "email": "" if not user else user.get('email', ''),
        "role": role
    })

@auth_api_bp.route('/telegram-webhook', methods=['POST'])
def telegram_webhook():
    """
    Receives updates from Telegram and passes them to the Bot.
    This runs inside the Flask Process!
    """
    data = request.get_json(force=True)

    # We must run the async bot logic in a sync Flask context
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(process_webhook_update(data))
        loop.close()
        return "OK", 200
    except Exception as e:
        log.error(f"Bot Webhook Error: {e}")
        return "Error", 500