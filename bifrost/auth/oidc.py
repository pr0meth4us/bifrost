from flask import Blueprint, jsonify, request, redirect, current_app, session, url_for
from werkzeug.security import gen_salt
import time
import jwt
from .. import mongo
from ..models import BifrostDB
import urllib.parse
from cryptography.hazmat.primitives import serialization

oidc_bp = Blueprint('oidc', __name__, url_prefix='/')

def get_app_config(client_id):
    db = BifrostDB(mongo.cx, current_app.config['DB_NAME'])
    return db, db.get_app_by_client_id(client_id)

@oidc_bp.route('/.well-known/openid-configuration')
def openid_configuration():
    # Dynamically build the exact public URL from proxy headers to ensure Issuer matches Supabase
    host = request.headers.get('X-Forwarded-Host', request.host)
    proto = request.headers.get('X-Forwarded-Proto', 'https' if not request.host.startswith('localhost') else 'http')
    base_url = f"{proto}://{host}"
    
    return jsonify({
        "issuer": base_url,
        "authorization_endpoint": f"{base_url}/oidc/authorize",
        "token_endpoint": f"{base_url}/oidc/token",
        "userinfo_endpoint": f"{base_url}/oidc/userinfo",
        "jwks_uri": f"{base_url}/.well-known/jwks.json",
        "response_types_supported": ["code"],
        "subject_types_supported": ["public"],
        "id_token_signing_alg_values_supported": ["RS256"],
        "scopes_supported": ["openid", "email", "profile"],
        "claims_supported": ["sub", "iss", "aud", "exp", "iat", "email", "name"]
    })

@oidc_bp.route('/.well-known/jwks.json')
def jwks():
    return jsonify({
        "keys": [current_app.config['OIDC_PUBLIC_JWK']]
    })

@oidc_bp.route('/oidc/authorize')
def authorize():
    client_id = request.args.get('client_id')
    redirect_uri = request.args.get('redirect_uri')
    state = request.args.get('state')
    nonce = request.args.get('nonce')
    
    if not client_id or not redirect_uri:
        return "Missing client_id or redirect_uri", 400
        
    db, app_config = get_app_config(client_id)
    if not app_config:
        return "Invalid client_id", 400
        
    # Store OIDC context in session for when ui.py completes login
    session['oidc_auth'] = {
        'client_id': client_id,
        'redirect_uri': redirect_uri,
        'state': state,
        'nonce': nonce
    }
    
    # Redirect to normal login UI
    return redirect(url_for('auth_ui.login', client_id=client_id))

@oidc_bp.route('/oidc/token', methods=['POST'])
def token():
    # Supports client authentication via Basic Auth or POST body
    client_id = request.form.get('client_id')
    if not client_id and request.authorization:
        client_id = request.authorization.username
        
    code = request.form.get('code')
    
    if not client_id or not code:
        return jsonify({"error": "invalid_request"}), 400
        
    db, app_config = get_app_config(client_id)
    if not app_config:
        return jsonify({"error": "invalid_client"}), 401
        
    # Find the auth code in DB (we will save this in ui.py when login completes)
    auth_code_doc = db.db.auth_codes.find_one({"code": code, "client_id": client_id})
    if not auth_code_doc:
        return jsonify({"error": "invalid_grant"}), 400
        
    if time.time() > auth_code_doc['expires_at']:
        db.db.auth_codes.delete_one({"_id": auth_code_doc['_id']})
        return jsonify({"error": "invalid_grant", "error_description": "Code expired"}), 400
        
    user = db.get_account(auth_code_doc['user_id'])
    if not user:
        return jsonify({"error": "invalid_grant"}), 400
        
    # Delete code so it can't be reused
    db.db.auth_codes.delete_one({"_id": auth_code_doc['_id']})
    
    # Dynamically build the exact public URL from proxy headers to ensure Issuer matches Supabase
    host = request.headers.get('X-Forwarded-Host', request.host)
    proto = request.headers.get('X-Forwarded-Proto', 'https' if not request.host.startswith('localhost') else 'http')
    base_url = f"{proto}://{host}"
    
    # Create RS256 ID Token
    now = int(time.time())
    id_token_payload = {
        "iss": base_url,
        "aud": client_id,
        "exp": now + 3600,
        "iat": now,
        "sub": str(user['_id']),
        "email": user.get('email', ''),
        "name": user.get('full_name') or user.get('username') or ''
    }
    
    nonce = auth_code_doc.get('nonce')
    if nonce:
        id_token_payload['nonce'] = nonce
        
    id_token = jwt.encode(
        id_token_payload,
        current_app.config['OIDC_PRIVATE_KEY'],
        algorithm='RS256',
        headers={"kid": current_app.config['OIDC_PUBLIC_JWK']['kid']}
    )
    
    # Use HS256 for standard access token (as Bifrost normally does)
    from ..utils.token import create_client_jwt
    access_token = create_client_jwt(user, client_id, db, app_config)
    
    return jsonify({
        "access_token": access_token,
        "id_token": id_token,
        "token_type": "Bearer",
        "expires_in": 3600
    })

@oidc_bp.route('/oidc/userinfo')
def userinfo():
    auth_header = request.headers.get('Authorization')
    if not auth_header or not auth_header.startswith('Bearer '):
        return jsonify({"error": "unauthorized"}), 401
        
    token = auth_header.split(' ')[1]
    
    try:
        payload = jwt.decode(token, current_app.config['JWT_SECRET_KEY'], algorithms=["HS256"])
        client_id = payload.get('aud')
        db, app_config = get_app_config(client_id)
        user = db.get_account(payload.get('sub'))
        
        return jsonify({
            "sub": str(user['_id']),
            "name": user.get('full_name') or user.get('username') or '',
            "email": user.get('email', '')
        })
    except Exception as e:
        return jsonify({"error": "invalid_token"}), 401
