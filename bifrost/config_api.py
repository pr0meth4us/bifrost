from flask import Blueprint, request, jsonify, current_app
from ..models import get_db

config_api_bp = Blueprint('config_api', __name__, url_prefix='/api/v1')

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
    app = db.applications.find_one({"client_id": client_id})

    if not app:
        return jsonify({"error": "Application not found"}), 404

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
