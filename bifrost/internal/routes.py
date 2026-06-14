# bifrost/internal/routes.py
import os
from flask import request, jsonify, current_app
import jwt
import logging
import asyncio

# Import the DB helper and Services
from .. import mongo
from ..models import BifrostDB
from ..utils.telegram import verify_telegram_data
from .utils import require_service_auth
from . import internal_bp

# Import Bot Logic
from bot.main import process_webhook_update

log = logging.getLogger(__name__)

@internal_bp.route('/generate-link-token', methods=['POST'])
@require_service_auth
def generate_link_token():
    """Generates a token for the user to click (Web -> Tele flow)."""
    data = request.json
    account_id = data.get('account_id')

    if not account_id:
        return jsonify({"error": "Missing account_id"}), 400

    db = BifrostDB(mongo.cx, current_app.config['DB_NAME'])
    token = db.create_deep_link_token(account_id)

    return jsonify({"token": token, "expires_in": "10 minutes"})


@internal_bp.route('/link-account', methods=['POST'])
@require_service_auth
def link_account_internal():
    """Connects a credential (Email/Pass or Telegram) to an existing account."""
    data = request.json
    db = BifrostDB(mongo.cx, current_app.config['DB_NAME'])
    client_id = request.authenticated_client_id
    app_config = db.get_app_by_client_id(client_id)

    # --- MODE 1: Link Email & Password ---
    if data.get('email') and data.get('password') and data.get('account_id'):
        account_id = data['account_id']
        email = data['email']
        password = data['password']

        success, msg = db.link_email_credentials(account_id, email, password)
        if success:
            return jsonify({"success": True, "message": "Email linked successfully", "email": email}), 200
        else:
            return jsonify({"error": msg}), 409

    # --- MODE 2: Link Telegram via Widget Data (Legacy/Widget Flow) ---
    elif data.get('telegram_data') and data.get('account_id'):
        account_id = data['account_id']
        tg_data = data['telegram_data']
        bot_token = app_config.get("telegram_bot_token")

        if not bot_token:
            return jsonify({"error": "Server misconfiguration: No Bot Token found for this app"}), 500

        if not verify_telegram_data(tg_data, bot_token):
            return jsonify({"error": "Invalid Telegram signature"}), 401

        telegram_id = str(tg_data['id'])
        display_name = tg_data.get('first_name', 'Unknown')

        success, msg = db.link_telegram(account_id, telegram_id, display_name)
        if success:
            return jsonify(
                {"success": True, "message": "Telegram linked successfully", "telegram_id": telegram_id}), 200
        else:
            return jsonify({"error": msg}), 409

    # --- MODE 3: Link via Deep Link Token (Bot -> Web User) ---
    elif data.get('link_token') and data.get('telegram_id'):
        token = data['link_token']
        telegram_id = str(data['telegram_id'])

        # 1. Verify Token
        record = db.verify_otp(code=token)
        if not record or record.get('channel') != 'deep_link':
            return jsonify({"error": "Invalid or expired link token"}), 400

        target_account_id = record.get('account_id')

        # 2. Perform Link
        success, msg = db.link_telegram(target_account_id, telegram_id, "Linked via Bot")

        if success:
            return jsonify({
                "success": True,
                "message": "Telegram linked successfully",
                "account_id": target_account_id
            }), 200
        else:
            return jsonify({"error": msg}), 409

    return jsonify({"error": "Invalid payload. Provide email/pass, telegram_data, or link_token"}), 400


@internal_bp.route('/validate-token', methods=['POST'])
@require_service_auth
def validate_token():
    """Validates a User JWT provided by a Client App."""
    data = request.json
    token = data.get('jwt') or data.get('token')
    client_id = request.authorization.username

    if not token:
        return jsonify({"is_valid": False, "error": "Missing token"}), 400

    try:
        # 1. Verify Signature using Bifrost's Secret
        payload = jwt.decode(
            token,
            current_app.config['JWT_SECRET_KEY'],
            algorithms=["HS256"],
            audience=client_id
        )

        account_id = payload['sub']

        # 2. Fetch Actual Role from Database
        db = BifrostDB(mongo.cx, current_app.config['DB_NAME'])
        app_doc = db.get_app_by_client_id(client_id)

        if not app_doc:
            return jsonify({"is_valid": False, "error": "App not found"}), 500

        user = db.find_account_by_id(account_id)
        if not user or not user.get('is_active', True):
            return jsonify({"is_valid": False, "error": "User inactive or not found"}), 403

        # Get Role from App Link (STRICT: app_specific_role)
        log.info(f"🔎 Validate Token: Checking role for User {account_id} in Client {client_id}")
        role = db.get_user_role_for_app(account_id, app_doc['_id'])
        final_role = role if role else "user"
        log.info(f"✅ Validate Token Result: {final_role}")

        return jsonify({
            "is_valid": True,
            "account_id": account_id,
            "app_specific_role": final_role, # Primary field
            "role": final_role,              # Legacy support for API consumers
            "email": user.get('email'),
            "username": user.get('username'),
            "display_name": user.get('display_name'),
            "telegram_id": user.get('telegram_id'),
            "exp": payload.get('exp')
        })

    except jwt.ExpiredSignatureError:
        return jsonify({"is_valid": False, "error": "Token has expired"}), 401
    except jwt.InvalidAudienceError:
        return jsonify({"is_valid": False, "error": "Invalid audience"}), 401
    except jwt.DecodeError:
        return jsonify({"is_valid": False, "error": "Malformed token"}), 401
    except Exception as e:
        log.error(f"Token validation failed: {e}")
        return jsonify({"is_valid": False, "error": f"Server Error: {str(e)}"}), 500


@internal_bp.route('/generate-otp', methods=['POST'])
@require_service_auth
def generate_otp():
    """Generates a login code for a specific Telegram ID."""
    data = request.json
    telegram_id = data.get('telegram_id')

    if not telegram_id:
        return jsonify({"error": "Missing telegram_id"}), 400

    db = BifrostDB(mongo.cx, current_app.config['DB_NAME'])
    code = db.create_login_code(telegram_id)

    return jsonify({"code": code, "expires_in": "10 minutes"})


@internal_bp.route('/set-credentials', methods=['POST'])
@require_service_auth
def set_credentials():
    """Sets the password for an email account using a proof token."""
    data = request.json
    email = data.get('email')
    password = data.get('password')
    proof_token = data.get('proof_token')

    if not email or not password or not proof_token:
        return jsonify({"error": "Missing email, password, or proof_token"}), 400

    try:
        payload = jwt.decode(
            proof_token,
            current_app.config['JWT_SECRET_KEY'],
            algorithms=["HS256"]
        )
    except jwt.InvalidTokenError:
        return jsonify({"error": "Invalid proof_token"}), 403

    if payload.get('scope') != 'credential_reset':
        return jsonify({"error": "Invalid token scope"}), 403

    if payload.get('email') != email:
        return jsonify({"error": "Token does not match provided email"}), 403

    db = BifrostDB(mongo.cx, current_app.config['DB_NAME'])
    context_account_id = payload.get('account_id')

    if context_account_id:
        success, message = db.link_email_credentials(context_account_id, email, password)
        if success:
            return jsonify({"success": True, "message": "Account linked successfully", "mode": "linked"})
        else:
            return jsonify({"error": message}), 409
    else:
        user = db.find_account_by_email(email)
        if not user:
            db.create_account({
                "email": email,
                "password": password,
                "display_name": "New User",
                "auth_providers": ["email"]
            })
            return jsonify({"success": True, "message": "Account created", "mode": "created"})
        else:
            db.update_password(email, password)
            return jsonify({"success": True, "message": "Credentials updated", "mode": "updated"})


@internal_bp.route('/users/<account_id>/update', methods=['POST'])
@require_service_auth
def update_user_profile(account_id):
    """Updates basic user profile information (display_name, email, username)."""
    data = request.json
    db = BifrostDB(mongo.cx, current_app.config['DB_NAME'])

    updates = {}
    if 'display_name' in data:
        updates['display_name'] = data['display_name']
    if 'email' in data:
        updates['email'] = data['email']
    if 'username' in data:
        updates['username'] = data['username']

    if not updates:
        return jsonify({"error": "No fields to update"}), 400

    success, msg = db.update_account_profile(account_id, updates)
    if success:
        return jsonify({"success": True, "message": msg})
    else:
        return jsonify({"error": msg}), 409


@internal_bp.route('/users/<user_id>', methods=['GET'])
@require_service_auth
def get_user_info(user_id):
    """Retrieve public user info for a client service"""
    db = BifrostDB(mongo.cx, current_app.config['DB_NAME'])
    user = db.find_account_by_id(user_id)
    if not user:
        return jsonify({"error": "User not found"}), 404

    return jsonify({
        "id": str(user['_id']),
        "email": user.get('email'),
        "username": user.get('username'),
        "display_name": user.get('display_name'),
        "telegram_id": user.get('telegram_id')
    }), 200


@internal_bp.route('/get-role', methods=['POST'])
@require_service_auth
def get_user_role_internal():
    """Allows a Service (like Finance Bot) to check the role of a Telegram User."""
    data = request.json
    telegram_id = data.get('telegram_id')
    client_id = request.authenticated_client_id

    log.info(f"🔎 Get Role Request: TelegramID={telegram_id} for Client={client_id}")

    if not telegram_id:
        return jsonify({"error": "Missing telegram_id"}), 400

    db = BifrostDB(mongo.cx, current_app.config['DB_NAME'])

    # 1. Find the User Account
    user = db.find_account_by_telegram(telegram_id)
    if not user:
        log.warning(f"⚠️ User not found for TelegramID {telegram_id}. Returning 'guest'.")
        return jsonify({"role": "guest"}), 200

    # 2. Find the App ID for the calling service
    app_doc = db.get_app_by_client_id(client_id)
    if not app_doc:
        log.error(f"❌ Calling App {client_id} not found in DB.")
        return jsonify({"error": "Calling App not found"}), 404

    # 3. Get the Role (Uses strict app_specific_role check)
    role = db.get_user_role_for_app(user['_id'], app_doc['_id'])

    final_response = role or "user"
    log.info(f"✅ Role Result for {telegram_id}: {final_response}")

    return jsonify({"role": final_response}), 200


@internal_bp.route('/get-roles-bulk', methods=['POST'])
@require_service_auth
def get_user_roles_bulk_internal():
    """Allows a Service to check roles for multiple account IDs at once."""
    data = request.json
    account_ids = data.get('account_ids', [])
    client_id = request.authenticated_client_id

    if not account_ids:
        return jsonify({"roles": {}}), 200

    db = BifrostDB(mongo.cx, current_app.config['DB_NAME'])
    app_doc = db.get_app_by_client_id(client_id)
    if not app_doc:
        return jsonify({"error": "Calling App not found"}), 404

    from bson import ObjectId
    object_ids = [ObjectId(aid) for aid in account_ids if ObjectId.is_valid(aid)]
    
    links = db.db.app_links.find({
        "app_id": app_doc['_id'],
        "account_id": {"$in": object_ids}
    })

    roles_map = {}
    for link in links:
        roles_map[str(link['account_id'])] = {
            "role": link.get("role", "user"),
            "has_used_trial": link.get("trial_used", False)
        }

    return jsonify({"roles": roles_map}), 200


@internal_bp.route('/me', methods=['GET'])
@require_service_auth
def get_current_user():
    """Introspection Endpoint."""
    return jsonify({"error": "Not implemented in headless mode"}), 501


@internal_bp.route('/telegram-webhook', methods=['POST'])
def telegram_webhook():
    """Receives updates from Telegram."""
    # 1. SECURITY CHECK
    # Fetch directly from os.environ to avoid Config import-time issues
    server_secret = os.environ.get('BIFROST_BOT_SECRET')
    secret_header = request.headers.get('X-Telegram-Bot-Api-Secret-Token')

    if not server_secret:
        log.error("❌ BIFROST_BOT_SECRET is missing from server environment variables!")
        return jsonify({"error": "Configuration Error"}), 500

    if secret_header != server_secret:
        # Log masked values for debugging
        header_preview = secret_header[:5] + "..." if secret_header else "None"
        config_preview = server_secret[:5] + "..." if server_secret else "None"
        log.warning(f"⚠️ MISMATCH! Header='{header_preview}' vs Env='{config_preview}'")
        return jsonify({"error": "Unauthorized", "message": "Invalid Secret Token"}), 403

    # 2. Process Update safely
    data = request.get_json(force=True)

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(process_webhook_update(data))
        loop.close()
        return jsonify({"status": "ok"}), 200
    except Exception as e:
        log.error(f"Bot Webhook Processing Error: {e}")
        return jsonify({"error": "Internal Error"}), 500

@internal_bp.route('/logs', methods=['POST'])
@require_service_auth
def ingest_logs():
    """Ingests streaming logs from Helm ecosystem apps into Redis."""
    from bifrost import redis_client
    import json
    import time
    import datetime
    
    data = request.json
    if not data:
        return jsonify({"error": "No data provided"}), 400
        
    client_id = request.authenticated_client_id
    db = BifrostDB(mongo.cx, current_app.config['DB_NAME'])
    app_doc = db.get_app_by_client_id(client_id)
    app_name = app_doc['app_name'] if app_doc else "Unknown App"
    
    if redis_client:
        try:
            log_entry = {
                "app_name": app_name,
                "client_id": client_id,
                "level": data.get("level", "INFO"),
                "logger": data.get("logger_name", "root"),
                "message": data.get("message", ""),
                "timestamp": data.get("timestamp", datetime.datetime.utcnow().isoformat()),
                "ingested_at": time.time()
            }
            redis_client.lpush("ecosystem_logs", json.dumps(log_entry))
            # Trim the list to max 1000 items to prevent unbounded growth
            redis_client.ltrim("ecosystem_logs", 0, 999)
            # Set TTL to 7 days
            redis_client.expire("ecosystem_logs", 604800)
            return jsonify({"status": "ok"}), 200
        except Exception as e:
            log.error(f"Redis log insertion failed: {e}")
            return jsonify({"error": "Log storage failed"}), 500
    else:
        return jsonify({"error": "Redis not configured"}), 503