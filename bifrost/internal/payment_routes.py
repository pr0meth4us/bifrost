# bifrost/internal/payment_routes.py
from flask import request, jsonify, current_app
import logging
from .. import mongo
from ..models import BifrostDB
from ..services.payway import PayWayService, PayWayNotConfigured
from ..services.gumroad import GumroadService
from .utils import require_service_auth
from . import internal_bp
from werkzeug.utils import secure_filename
from ..utils.telegram import send_payment_proof_to_admin

log = logging.getLogger(__name__)

# Security: Roles that cannot be assigned via Payment or automated API calls
FORBIDDEN_ROLES = ['admin', 'super_admin', 'owner', 'god_admin', 'root', 'bifrost_admin']


# =========================================================
#  SECTION: PAYMENT ENDPOINTS (SECURE + HYBRID)
# =========================================================

@internal_bp.route('/payments/secure-intent', methods=['POST'])
@require_service_auth
def create_secure_payment_intent():
    """
    ENTERPRISE FLOW: Creates a transaction intent in the DB first.
    Returns a secure Transaction ID that the client uses in the Telegram link.
    """
    data = request.json

    # 1. Validate Inputs
    account_id = data.get('account_id')  # Optional (if known)
    amount = data.get('amount')
    currency = data.get('currency', 'USD')
    target_role = data.get('target_role', 'premium_user')
    duration = data.get('duration', '1m')
    description = data.get('description', 'Subscription Upgrade')
    ref_id = data.get('client_ref_id')

    if not amount or not ref_id:
        return jsonify({"error": "Missing amount or client_ref_id"}), 400

    # SECURITY CHECK: Prevent Privilege Escalation
    if target_role.lower() in FORBIDDEN_ROLES or 'admin' in target_role.lower():
        return jsonify({
            "error": "Security Violation",
            "message": f"Role '{target_role}' cannot be assigned via payment API."
        }), 403

    # 2. Get Authenticated App Context
    client_id = request.authenticated_client_id
    db = BifrostDB(mongo.cx, current_app.config['DB_NAME'])
    app_doc = db.get_app_by_client_id(client_id)

    if not app_doc:
        return jsonify({"error": "App context lost"}), 500

    # 3. Create Transaction Record (Pending)
    try:
        tx_id = db.create_transaction(
            account_id=account_id,
            app_id=app_doc['_id'],
            app_name=app_doc.get('app_name', 'Unknown App'),
            amount=amount,
            currency=currency,
            description=description,
            target_role=target_role,
            duration=duration,
            client_ref_id=ref_id
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    return jsonify({
        "success": True,
        "transaction_id": tx_id,
        "secure_link": f"https://t.me/bifrost_byhelm_bot?start={tx_id}",
        "manual_command": f"/pay {tx_id}"
    })


@internal_bp.route('/payments/status/<transaction_id>', methods=['GET'])
@require_service_auth
def check_transaction_status(transaction_id):
    """
    POLLING ENDPOINT: Allows the client frontend (via their backend proxy)
    to check if a payment has been completed.
    Useful for showing 'Payment Successful!' screens in real-time.
    """
    client_id = request.authenticated_client_id
    db = BifrostDB(mongo.cx, current_app.config['DB_NAME'])

    # 1. Ensure the calling app owns this transaction
    app_doc = db.get_app_by_client_id(client_id)
    if not app_doc:
        return jsonify({"error": "App context lost"}), 401

    tx = db.db.transactions.find_one({
        "transaction_id": transaction_id,
        "app_id": app_doc['_id']
    })

    if not tx:
        return jsonify({"error": "Transaction not found"}), 404

    return jsonify({
        "transaction_id": tx['transaction_id'],
        "status": tx['status'],  # 'pending' or 'completed'
        "amount": tx['amount'],
        "target_role": tx.get('target_role'),
        "updated_at": tx.get('updated_at')
    })


@internal_bp.route('/payments/create-intent', methods=['POST'])
@require_service_auth
def create_payment_intent():
    """LEGACY DIRECT GATEWAY (Payway/Gumroad)"""
    data = request.json
    account_id = data.get('account_id')
    amount = data.get('amount')
    region = data.get('region', 'local')
    target_role = data.get('target_role', 'premium_user')
    product_id = data.get('product_id')

    firstname = data.get('firstname', 'Bifrost')
    lastname = data.get('lastname', 'User')
    email = data.get('email', 'user@example.com')
    phone = data.get('phone', '099999999')

    # SECURITY CHECK
    if target_role.lower() in FORBIDDEN_ROLES or 'admin' in target_role.lower():
        return jsonify({"error": f"Role '{target_role}' restricted."}), 403

    client_id = request.authenticated_client_id

    db = BifrostDB(mongo.cx, current_app.config['DB_NAME'])
    app_doc = db.get_app_by_client_id(client_id)

    if not app_doc:
        return jsonify({"error": "App context lost"}), 500

    # 1. Create Pending Transaction in Bifrost
    tx_id = db.create_transaction(
        account_id=account_id,
        app_id=app_doc['_id'],
        app_name=app_doc.get('app_name', 'Unknown App'),
        amount=amount,
        currency="USD",
        description=f"Upgrade to {target_role}",
        target_role=target_role
    )

    # 2. ROUTER LOGIC
    if region == 'local':
        try:
            payway = PayWayService(app_doc)
        except PayWayNotConfigured as e:
            log.error(str(e))
            return jsonify({"error": "PayWay is not configured for this application"}), 400
        items = [{"name": target_role, "quantity": "1", "price": amount}]

        result = payway.create_transaction(
            transaction_id=tx_id,
            amount=amount,
            items=items,
            firstname=firstname,
            lastname=lastname,
            email=email,
            phone=phone
        )

        if result:
            return jsonify({
                "success": True,
                "transaction_id": tx_id,
                "provider": "payway",
                "qr_string": result['qr_string'],
                "deeplink": result['deeplink']
            })
        else:
            return jsonify({"error": "Failed to communicate with ABA"}), 502

    else:
        gumroad = GumroadService(app_doc)
        checkout_url = gumroad.generate_checkout_url(
            transaction_id=tx_id,
            email=email,
            product_permalink=product_id
        )

        if not checkout_url:
            return jsonify({"error": "Missing product configuration"}), 400

        return jsonify({
            "success": True,
            "transaction_id": tx_id,
            "provider": "gumroad",
            "payment_url": checkout_url
        })


# --- RENAMED ENDPOINT (With Legacy Alias) ---
@internal_bp.route('/grant-role', methods=['POST'])
@internal_bp.route('/grant-premium', methods=['POST'])  # LEGACY SUPPORT
@require_service_auth
def grant_role_by_admin():
    """
    Manually grants a specific tier (role) to a user via Telegram ID.
    Supports custom 'target_role' for easy tier management.
    """
    data = request.json
    telegram_id = data.get('telegram_id')
    account_id = data.get('account_id')
    target_client_id = data.get('target_client_id')

    # Allow custom tiers (e.g. 'gold', 'pro'), default to 'premium_user'
    target_role = data.get('target_role', 'premium_user')
    duration = data.get('duration', '1m')

    # SECURITY CHECK
    if target_role.lower() in FORBIDDEN_ROLES or 'admin' in target_role.lower():
        return jsonify({"error": f"Security Violation: Cannot grant '{target_role}' via API."}), 403

    # If no target provided, default to the caller's ID
    caller_client_id = request.authenticated_client_id
    app_client_id = target_client_id if target_client_id else caller_client_id

    if not telegram_id and not account_id:
        return jsonify({"error": "Missing telegram_id or account_id"}), 400

    db = BifrostDB(mongo.cx, current_app.config['DB_NAME'])
    app_doc = db.get_app_by_client_id(app_client_id)

    if not app_doc:
        return jsonify({"error": f"Target App ({app_client_id}) not found"}), 404

    # Find User
    if account_id:
        user = db.db.accounts.find_one({"_id": ObjectId(account_id)})
    else:
        user = db.find_account_by_telegram(telegram_id)
        
    if not user:
        return jsonify({"error": "User not found"}), 404

    # Update Role with Custom Tier and Duration
    db.link_user_to_app(user['_id'], app_doc['_id'], role=target_role, duration_str=duration)

    log.info(f"Admin manually granted '{target_role}' for App '{app_doc.get('app_name')}' to User {user['_id']}")

    return jsonify({
        "success": True,
        "message": f"User upgraded to {target_role}",
        "role": target_role,
        "app": app_doc.get('app_name')
    }), 200

@internal_bp.route('/payments/activate-trial', methods=['POST'])
@require_service_auth
def activate_free_trial():
    """
    Activates a one-time free trial for the user.
    """
    data = request.json
    account_id = data.get('account_id')
    target_client_id = data.get('target_client_id')
    duration = data.get('duration', '14d')

    caller_client_id = request.authenticated_client_id
    app_client_id = target_client_id if target_client_id else caller_client_id

    if not account_id:
        return jsonify({"error": "Missing account_id"}), 400

    db = BifrostDB(mongo.cx, current_app.config['DB_NAME'])
    app_doc = db.get_app_by_client_id(app_client_id)

    if not app_doc:
        return jsonify({"error": f"Target App ({app_client_id}) not found"}), 404

    success, msg = db.activate_free_trial(account_id, app_doc['_id'], duration_str=duration)
    
    if success:
        log.info(f"User {account_id} activated free trial for App '{app_doc.get('app_name')}'")
        return jsonify({"success": True, "message": msg}), 200
    else:
        return jsonify({"error": msg}), 400


@internal_bp.route('/payments/callback', methods=['POST'])
def payway_callback():
    data = request.form.to_dict()
    log.info(f"PayWay Webhook: {data}")

    status = data.get('status')
    tran_id = data.get('tran_id')
    apv = data.get('apv')

    db = BifrostDB(mongo.cx, current_app.config['DB_NAME'])

    # Which tenant's merchant this callback belongs to is only knowable from the
    # transaction — the callback URL is shared. Resolving it is also what makes
    # the signature checkable at all: the HMAC key is per-merchant.
    tx = db.get_transaction(tran_id) if tran_id else None
    if not tx:
        log.warning(f"PayWay callback for unknown transaction {tran_id!r}")
        return "OK", 200

    app_doc = db.db.applications.find_one({"_id": tx['app_id']})
    try:
        payway = PayWayService(app_doc)
    except PayWayNotConfigured as e:
        log.error(f"PayWay callback for unconfigured app: {e}")
        return "OK", 200

    if not payway.verify_webhook(data):
        log.warning(f"PayWay callback rejected: bad signature for {tran_id}")
        return "OK", 200

    if status == '00':
        success, msg = db.complete_transaction(transaction_id=tran_id, provider_ref=apv)
        log.info(f"PayWay TX {tran_id}: {msg}")

    return "OK", 200


@internal_bp.route('/payments/webhook/gumroad', methods=['POST'])
def gumroad_webhook():
    raw_data = request.form.to_dict()
    print(f"🔔 HIT! Webhook received. Raw Data: {raw_data}", flush=True)

    if raw_data.get('sale_id') == '1234' or raw_data.get('test'):
        print("✅ Gumroad Test Ping confirmed successful.", flush=True)
        return "OK", 200

    tx_id = raw_data.get('url_params[transaction_id]')
    if not tx_id:
        tx_id = raw_data.get('transaction_id')

    if not tx_id:
        print("⚠️ Warning: No transaction_id found in webhook. Ignoring.", flush=True)
        return "OK", 200

    sale_id = raw_data.get('sale_id')
    resource = raw_data.get('resource_name')

    if resource == 'sale' or resource == 'subscription':
        from ..models import BifrostDB
        from .. import mongo
        db = BifrostDB(mongo.cx, current_app.config['DB_NAME'])
        success, msg = db.complete_transaction(tx_id, provider_ref=sale_id)
        print(f"💰 Processed Sale {sale_id} for TX {tx_id}: {msg}", flush=True)

    return "OK", 200


@internal_bp.route('/payments/claim', methods=['POST'])
@require_service_auth
def api_claim_payment():
    """Universal Endpoint to claim a payment."""
    data = request.json
    trx_input = data.get('trx_input')
    target_app_id = data.get('target_app_id')
    identity_type = data.get('identity_type')
    identity_value = data.get('identity_value')

    if not trx_input or not target_app_id or not identity_type:
        return jsonify({"error": "Missing parameters"}), 400

    db = BifrostDB(mongo.cx, current_app.config['DB_NAME'])

    # Verify the App exists first
    app_doc = db.get_app_by_client_id(target_app_id)
    if not app_doc:
        return jsonify({"error": "Invalid App ID"}), 404

    # Construct Identity Dict
    user_identity = {identity_type: identity_value}

    # Perform Claim
    success, msg = db.claim_payment(trx_input, app_doc['_id'], user_identity)

    if success:
        return jsonify({"success": True, "message": msg}), 200
    else:
        return jsonify({"success": False, "error": msg}), 400


@internal_bp.route('/payments/submit-proof', methods=['POST'])
@require_service_auth
def submit_payment_proof():
    """
    Allows Client Apps to upload a payment receipt on behalf of a user.
    Forwards the proof to the Telegram Admin Group for manual approval.
    """
    # 1. Check for File
    if 'file' not in request.files:
        return jsonify({"error": "No file part"}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "No selected file"}), 400

    # 2. Get Metadata (Form Data)
    account_id = request.form.get('account_id')
    email = request.form.get('email')
    transaction_id = request.form.get('transaction_id')

    # 3. Authenticate Context
    client_id = request.authenticated_client_id
    db = BifrostDB(mongo.cx, current_app.config['DB_NAME'])
    app_doc = db.get_app_by_client_id(client_id)

    if not app_doc:
        return jsonify({"error": "Invalid Client App"}), 403

    # 4. Resolve User
    user = None
    if account_id:
        user = db.find_account_by_id(account_id)
    elif email:
        user = db.find_account_by_email(email)

    if not user:
        return jsonify({"error": "User not found"}), 404

    # 5. Validate Transaction (Optional but recommended for display)
    amount_display = "See Receipt"
    if transaction_id:
        tx = db.db.transactions.find_one({"transaction_id": transaction_id})
        if tx and tx.get('amount'):
            amount_display = f"${tx['amount']}"

    # 6. Send to Telegram Admin Group
    try:
        success = send_payment_proof_to_admin(
            file_stream=file.stream,
            file_name=secure_filename(file.filename),
            user_display_name=user.get('display_name', 'Web User'),
            user_identifier=str(user['_id']),
            app_name=app_doc.get('app_name', client_id),
            client_id=client_id,
            amount=amount_display,
            config=current_app.config
        )

        if success:
            return jsonify({"success": True, "message": "Proof submitted for review"}), 200
        else:
            return jsonify({"error": "Failed to forward to admin group"}), 502

    except Exception as e:
        log.error(f"Proof Upload Error: {e}")
        return jsonify({"error": str(e)}), 500