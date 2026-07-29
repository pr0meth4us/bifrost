from flask import Blueprint, request, jsonify, current_app
from .models import BifrostDB
from . import mongo

def get_db():
    return BifrostDB(mongo.cx, current_app.config['DB_NAME'])

config_api_bp = Blueprint('config_api', __name__, url_prefix='/api/v1')

@config_api_bp.route('/public/branding/<client_id>', methods=['GET'])
def get_public_branding(client_id):
    """Unauthenticated branding for a tenant frontend: logo and payment QR.

    Only fields an end user already sees on screen. The paywall reads
    payment_qr_url from here so ops can swap the receiving ABA account in the
    console without a frontend deploy.
    """
    app = get_db().get_app_by_client_id(client_id)
    if not app:
        return jsonify({"error": "Application not found"}), 404

    resp = jsonify({
        "status": "success",
        "data": {
            "app_name": app.get("app_name"),
            "logo_url": app.get("app_logo_url") or None,
            "payment_qr_url": app.get("app_qr_url") or None,
            # Which checkout the paywall should render: 'payway' (live bank API),
            # 'manual' (static QR + receipt upload), both, or neither.
            "payment_methods": app.get("payment_methods") or [],
        }
    })
    resp.headers['Cache-Control'] = 'public, max-age=300'
    resp.headers['Access-Control-Allow-Origin'] = '*'
    return resp


@config_api_bp.route('/config', methods=['GET'])
def get_config():
    """
    Returns the encrypted API keys and configuration for a given app.
    Requires X-Client-ID and X-Webhook-Secret headers.
    """
    client_id = request.headers.get('X-Client-ID')
    webhook_secret = request.headers.get('X-Webhook-Secret')

    if not client_id or not webhook_secret:
        return jsonify({"error": "Missing credentials. Require X-Client-ID and X-Webhook-Secret"}), 401

    db = get_db()
    app = db.get_app_by_client_id(client_id)

    if not app:
        return jsonify({"error": "Application not found"}), 404

    if not app.get("enabled_services", {}).get("secrets_vault", True):
        return jsonify({"error": "Secrets Vault Service is disabled for this application"}), 403

    # The webhook_secret acts as the master decryption key for the client.
    # We verify it here to ensure only the authorized client can download its config.
    if app.get("webhook_secret") != webhook_secret:
        return jsonify({"error": "Invalid webhook secret"}), 403

    encrypted_api_keys = app.get("api_keys", {})
    
    # Decrypt all keys server-side before sending
    from .utils.encryption import decrypt_value
    decrypted_keys = {}
    for k, v in encrypted_api_keys.items():
        if v:
            decrypted_keys[k] = decrypt_value(v, webhook_secret)
        else:
            decrypted_keys[k] = v
            
    return jsonify({
        "status": "success",
        "data": {
            "api_keys": decrypted_keys
        }
    })

@config_api_bp.route('/config/bulk-upload', methods=['POST'])
def bulk_upload_config():
    """
    Bulk uploads and encrypts API keys for a given app.
    Requires X-Client-ID and X-Webhook-Secret headers.
    Expects JSON payload: {"keys": {"API_KEY": "value", ...}}
    """
    client_id = request.headers.get('X-Client-ID')
    webhook_secret = request.headers.get('X-Webhook-Secret')

    if not client_id or not webhook_secret:
        return jsonify({"error": "Missing credentials. Require X-Client-ID and X-Webhook-Secret"}), 401

    db = get_db()
    app = db.get_app_by_client_id(client_id)

    if not app:
        return jsonify({"error": "Application not found"}), 404

    if not app.get("enabled_services", {}).get("secrets_vault", True):
        return jsonify({"error": "Secrets Vault Service is disabled for this application"}), 403

    if app.get("webhook_secret") != webhook_secret:
        return jsonify({"error": "Invalid webhook secret"}), 403

    payload = request.get_json()
    if not payload or "keys" not in payload:
        return jsonify({"error": "Invalid payload. Expected JSON with 'keys' dictionary"}), 400

    keys = payload["keys"]
    if not isinstance(keys, dict):
        return jsonify({"error": "'keys' must be a dictionary"}), 400

    count = 0
    for key_name, key_value in keys.items():
        if key_name and key_value:
            # Sync the top-level telegram_bot_token for Telegram Auth verification
            if key_name in ("TELEGRAM_TOKEN", "BIFROST_BOT_TOKEN"):
                db.update_app_details(app['_id'], {"telegram_bot_token": str(key_value)})
                
            # add_app_api_key securely encrypts the value and automatically triggers config_updated webhook
            db.add_app_api_key(app['_id'], key_name, str(key_value))
            count += 1

    return jsonify({
        "status": "success",
        "message": f"Successfully encrypted and uploaded {count} keys to Bifrost Vault."
    })
