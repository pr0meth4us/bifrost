"""OpenID Connect provider.

Bifrost is the IdP; tenant apps are the relying parties. Authorization Code flow
with PKCE, refresh tokens, RP-initiated logout, token revocation, and a real IdP
session (see `sso.py`) so a second app does not re-prompt for a password.

Accounts belong to a *directory*, keyed by the app's `tenant_id` (defaulting to
its own client_id). Every app sharing a directory shares one user pool, which is
what makes SSO across apps mean anything. A session in one directory can never
satisfy an authorize call from another.
"""
import base64
import hashlib
import logging
import secrets
import time
import urllib.parse
from datetime import datetime, timezone

import jwt
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from flask import (Blueprint, current_app, jsonify, redirect, render_template,
                   request, session, url_for)

from . import sso
from .. import mongo
from ..models import BifrostDB
from ..utils.token import create_client_jwt
from ..utils.urls import public_url

log = logging.getLogger(__name__)

oidc_bp = Blueprint('oidc', __name__, url_prefix='/')

SUPPORTED_SCOPES = ["openid", "profile", "email", "roles", "offline_access"]
CODE_TTL = 60          # RFC 6749 §4.1.2 recommends a maximum of 10 minutes; one is plenty.
ACCESS_TOKEN_TTL = 3600
REFRESH_TOKEN_TTL = 30 * 86400
PENDING_KEY = 'oidc_pending'


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_db():
    return BifrostDB(mongo.cx, current_app.config['DB_NAME'])


def issuer():
    """The public origin. Config first, forwarded headers only as a fallback:
    `X-Forwarded-Host` is attacker-controlled, and an issuer an attacker can
    steer is an issuer relying parties cannot pin. See utils/urls.py.
    """
    return public_url()


def b64url(raw):
    return base64.urlsafe_b64encode(raw).decode('ascii').rstrip('=')


def int_to_b64url(num):
    return b64url(num.to_bytes((num.bit_length() + 7) // 8, byteorder='big'))


_KEY_CACHE = {}


def signing_key():
    """The RSA keypair, persisted in Mongo and shared by every worker.

    Generating this per process meant worker A signed id_tokens that worker B's
    JWKS could not verify, and every restart invalidated every token in flight.
    """
    if 'private' in _KEY_CACHE:
        return _KEY_CACHE['private'], _KEY_CACHE['jwk']

    pem = current_app.config.get('OIDC_PRIVATE_KEY_PEM')
    if not pem:
        db = get_db()
        doc = db.db.oidc_keys.find_one({"_id": "active"})
        if not doc:
            key = rsa.generate_private_key(public_exponent=65537, key_size=2048,
                                           backend=default_backend())
            pem = key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            ).decode()
            # Two workers can race here. `$setOnInsert` means exactly one wins and
            # the loser reads the winner's key back rather than serving its own.
            db.db.oidc_keys.update_one(
                {"_id": "active"},
                {"$setOnInsert": {"pem": pem, "created_at": time.time()}},
                upsert=True,
            )
            doc = db.db.oidc_keys.find_one({"_id": "active"})
        pem = doc['pem']

    if isinstance(pem, str):
        pem = pem.encode()
    private = serialization.load_pem_private_key(pem, password=None, backend=default_backend())

    numbers = private.public_key().public_numbers()
    # RFC 7638 JWK thumbprint, so the kid is derived from the key and rotating the
    # key necessarily rotates the kid.
    thumb_src = f'{{"e":"{int_to_b64url(numbers.e)}","kty":"RSA","n":"{int_to_b64url(numbers.n)}"}}'
    jwk = {
        "kty": "RSA",
        "alg": "RS256",
        "use": "sig",
        "kid": b64url(hashlib.sha256(thumb_src.encode()).digest()),
        "n": int_to_b64url(numbers.n),
        "e": int_to_b64url(numbers.e),
    }

    _KEY_CACHE['private'], _KEY_CACHE['jwk'] = private, jwk
    return private, jwk


def registered_redirect_uris(app_config):
    uris = list(app_config.get('oidc_redirect_uris') or [])
    if app_config.get('app_callback_url'):
        uris.append(app_config['app_callback_url'])
    return uris


def redirect_uri_allowed(app_config, redirect_uri):
    """Exact string match, per OIDC Core §3.1.2.1.

    Substring or prefix matching is how open redirectors get built: an attacker
    registers `https://good.example` and walks in with
    `https://good.example.evil.test`.
    """
    return redirect_uri in registered_redirect_uris(app_config)


def error_redirect(redirect_uri, error, description=None, state=None):
    params = {"error": error}
    if description:
        params["error_description"] = description
    if state:
        params["state"] = state
    sep = '&' if '?' in redirect_uri else '?'
    return redirect(f"{redirect_uri}{sep}{urllib.parse.urlencode(params)}")


def authenticate_client(db):
    """Resolve and authenticate the calling client (RFC 6749 §2.3.1).

    Accepts client_secret_basic and client_secret_post. Public clients (no
    secret, PKCE mandatory) authenticate by proving possession of the verifier
    instead, so they are allowed through here with no secret.
    """
    client_id = client_secret = None

    if request.authorization:
        client_id = request.authorization.username
        client_secret = request.authorization.password
    if not client_id:
        client_id = request.form.get('client_id')
        client_secret = request.form.get('client_secret')

    if not client_id:
        return None, None, ("invalid_client", "No client credentials supplied")

    app_config = db.get_app_by_client_id(client_id)
    if not app_config:
        return None, None, ("invalid_client", "Unknown client_id")

    if app_config.get('oidc_public_client'):
        return client_id, app_config, None

    if not client_secret or not db.verify_client_secret(client_id, client_secret):
        return None, None, ("invalid_client", "Client authentication failed")

    return client_id, app_config, None


def verify_pkce(code_doc, verifier):
    challenge = code_doc.get('code_challenge')
    if not challenge:
        return True  # No PKCE was requested at authorize time.
    if not verifier:
        return False

    if code_doc.get('code_challenge_method') == 'plain':
        computed = verifier
    else:
        computed = b64url(hashlib.sha256(verifier.encode('ascii')).digest())
    return secrets.compare_digest(computed, challenge)


def issue_code(db, app_config, user, ctx, auth_time, amr):
    """Mint a single-use authorization code and bounce back to the RP."""
    code = secrets.token_urlsafe(32)
    db.db.auth_codes.insert_one({
        "code": code,
        "client_id": app_config['client_id'],
        "directory": db.directory_scope(app_config),
        "user_id": user['_id'],
        "nonce": ctx.get('nonce'),
        "redirect_uri": ctx.get('redirect_uri'),
        "scopes": ctx.get('scopes') or ["openid"],
        "code_challenge": ctx.get('code_challenge'),
        "code_challenge_method": ctx.get('code_challenge_method'),
        "auth_time": auth_time,
        "amr": amr,
        "used": False,
        # A real datetime, because Mongo TTL indexes only reap Date fields.
        "created_at": datetime.now(timezone.utc),
        "expires_at": time.time() + CODE_TTL,
    })

    params = {"code": code}
    if ctx.get('state'):
        params["state"] = ctx['state']
    redirect_uri = ctx['redirect_uri']
    sep = '&' if '?' in redirect_uri else '?'
    return redirect(f"{redirect_uri}{sep}{urllib.parse.urlencode(params)}")


def resume_pending(db, user, amr):
    """Called by the login flows after a successful authentication.

    Returns a redirect back to the relying party if an OIDC authorize request is
    waiting on this browser, otherwise None so the caller falls through to the
    normal Bifrost callback.
    """
    ctx = session.get(PENDING_KEY)
    if not ctx:
        return None

    app_config = db.get_app_by_client_id(ctx.get('client_id'))
    if not app_config:
        session.pop(PENDING_KEY, None)
        return None

    # The account that just authenticated must live in the directory the
    # authorize request was made against.
    if db.directory_scope(app_config) != ctx.get('directory'):
        return None

    session.pop(PENDING_KEY, None)
    sess = sso.current(ctx['directory']) or {}
    return issue_code(db, app_config, user, ctx,
                      sess.get('auth_time', int(time.time())), amr)


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

@oidc_bp.route('/.well-known/openid-configuration')
def openid_configuration():
    base = issuer()
    return jsonify({
        "issuer": base,
        "authorization_endpoint": f"{base}/oidc/authorize",
        "token_endpoint": f"{base}/oidc/token",
        "userinfo_endpoint": f"{base}/oidc/userinfo",
        "jwks_uri": f"{base}/.well-known/jwks.json",
        "end_session_endpoint": f"{base}/oidc/logout",
        "revocation_endpoint": f"{base}/oidc/revoke",
        "response_types_supported": ["code"],
        "response_modes_supported": ["query"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "subject_types_supported": ["public"],
        "id_token_signing_alg_values_supported": ["RS256"],
        "token_endpoint_auth_methods_supported": ["client_secret_basic", "client_secret_post", "none"],
        "code_challenge_methods_supported": ["S256", "plain"],
        "scopes_supported": SUPPORTED_SCOPES,
        "claims_supported": ["sub", "iss", "aud", "exp", "iat", "auth_time", "nonce",
                             "amr", "email", "email_verified", "name",
                             "preferred_username", "picture", "role", "permissions"],
        "claims_parameter_supported": False,
        "request_parameter_supported": False,
        "frontchannel_logout_supported": True,
    })


@oidc_bp.route('/.well-known/jwks.json')
def jwks():
    _, jwk = signing_key()
    return jsonify({"keys": [jwk]})


# ---------------------------------------------------------------------------
# Authorization endpoint
# ---------------------------------------------------------------------------

@oidc_bp.route('/oidc/authorize')
def authorize():
    db = get_db()
    client_id = request.args.get('client_id')
    redirect_uri = request.args.get('redirect_uri')
    state = request.args.get('state')

    if not client_id or not redirect_uri:
        return render_template('auth/error.html',
                               error="Missing client_id or redirect_uri"), 400

    app_config = db.get_app_by_client_id(client_id)
    if not app_config:
        return render_template('auth/error.html', error="Invalid client_id"), 400

    # Until the redirect_uri is proven registered, errors must be rendered here
    # rather than redirected — otherwise the error response is itself the open
    # redirect (OIDC Core §3.1.2.6).
    if not redirect_uri_allowed(app_config, redirect_uri):
        log.warning("OIDC: unregistered redirect_uri %r for client %s", redirect_uri, client_id)
        return render_template('auth/error.html',
                               error="redirect_uri is not registered for this application"), 400

    response_type = request.args.get('response_type', 'code')
    if response_type != 'code':
        return error_redirect(redirect_uri, "unsupported_response_type",
                              "Only the authorization code flow is supported", state)

    scopes = (request.args.get('scope') or 'openid').split()
    if 'openid' not in scopes:
        return error_redirect(redirect_uri, "invalid_scope",
                              "The openid scope is required", state)
    unknown = [s for s in scopes if s not in SUPPORTED_SCOPES]
    if unknown:
        return error_redirect(redirect_uri, "invalid_scope",
                              f"Unsupported scope: {' '.join(unknown)}", state)

    code_challenge = request.args.get('code_challenge')
    code_challenge_method = request.args.get('code_challenge_method', 'plain' if code_challenge else None)
    if code_challenge and code_challenge_method not in ('S256', 'plain'):
        return error_redirect(redirect_uri, "invalid_request",
                              "Unsupported code_challenge_method", state)
    # A public client has no secret to prove, so the PKCE verifier is the only
    # thing standing between a stolen code and a token.
    if app_config.get('oidc_public_client') and not code_challenge:
        return error_redirect(redirect_uri, "invalid_request",
                              "PKCE is required for this client", state)

    max_age = request.args.get('max_age', type=int)
    prompt = set((request.args.get('prompt') or '').split())
    directory = db.directory_scope(app_config)

    ctx = {
        "client_id": client_id,
        "directory": directory,
        "redirect_uri": redirect_uri,
        "state": state,
        "nonce": request.args.get('nonce'),
        "scopes": scopes,
        "code_challenge": code_challenge,
        "code_challenge_method": code_challenge_method,
    }

    # --- Single sign-on: reuse an existing Bifrost session ---
    existing = None if 'login' in prompt else sso.current(directory, max_age)
    if existing:
        user = db.find_account_by_id(existing['uid'])
        if user and user.get('is_active', True):
            return issue_code(db, app_config, user, ctx,
                              existing['auth_time'], existing.get('amr', []))
        sso.end()

    if 'none' in prompt:
        return error_redirect(redirect_uri, "login_required",
                              "No active session and prompt=none was requested", state)

    session[PENDING_KEY] = ctx
    return redirect(url_for('auth_ui.login', client_id=client_id))


# ---------------------------------------------------------------------------
# Token endpoint
# ---------------------------------------------------------------------------

def token_error(code, description=None, status=400):
    body = {"error": code}
    if description:
        body["error_description"] = description
    resp = jsonify(body)
    resp.status_code = status
    if code == "invalid_client":
        resp.headers['WWW-Authenticate'] = 'Basic realm="bifrost"'
    return resp


@oidc_bp.route('/oidc/token', methods=['POST'])
def token():
    db = get_db()
    client_id, app_config, err = authenticate_client(db)
    if err:
        return token_error(err[0], err[1], 401)

    grant_type = request.form.get('grant_type')
    if grant_type == 'authorization_code':
        return _authorization_code_grant(db, client_id, app_config)
    if grant_type == 'refresh_token':
        return _refresh_token_grant(db, client_id, app_config)
    return token_error("unsupported_grant_type",
                       "Supported: authorization_code, refresh_token")


def _authorization_code_grant(db, client_id, app_config):
    code = request.form.get('code')
    if not code:
        return token_error("invalid_request", "Missing code")

    code_doc = db.db.auth_codes.find_one({"code": code, "client_id": client_id})
    if not code_doc:
        return token_error("invalid_grant", "Unknown authorization code")

    # Replay. RFC 6749 §4.1.2 says revoke everything that code produced — a
    # second presentation means either the client is broken or someone else has
    # the code, and only one of those is safe to ignore.
    if code_doc.get('used'):
        log.warning("OIDC: authorization code replayed for client %s; revoking derived tokens", client_id)
        db.db.oidc_refresh_tokens.delete_many({"code_id": code_doc['_id']})
        db.db.auth_codes.delete_one({"_id": code_doc['_id']})
        return token_error("invalid_grant", "Authorization code already used")

    if time.time() > code_doc['expires_at']:
        db.db.auth_codes.delete_one({"_id": code_doc['_id']})
        return token_error("invalid_grant", "Authorization code expired")

    # Must match the value the code was issued against (RFC 6749 §4.1.3).
    supplied_uri = request.form.get('redirect_uri')
    if code_doc.get('redirect_uri') and supplied_uri != code_doc['redirect_uri']:
        return token_error("invalid_grant", "redirect_uri mismatch")

    if not verify_pkce(code_doc, request.form.get('code_verifier')):
        return token_error("invalid_grant", "PKCE verification failed")

    user = db.find_account_by_id(code_doc['user_id'])
    if not user:
        return token_error("invalid_grant", "Account no longer exists")

    # Mark rather than delete, so a replay is detectable instead of merely absent.
    db.db.auth_codes.update_one({"_id": code_doc['_id']}, {"$set": {"used": True}})

    return _issue_tokens(db, client_id, app_config, user,
                         scopes=code_doc.get('scopes') or ["openid"],
                         nonce=code_doc.get('nonce'),
                         auth_time=code_doc.get('auth_time'),
                         amr=code_doc.get('amr') or [],
                         code_id=code_doc['_id'])


def _refresh_token_grant(db, client_id, app_config):
    supplied = request.form.get('refresh_token')
    if not supplied:
        return token_error("invalid_request", "Missing refresh_token")

    token_hash = hashlib.sha256(supplied.encode()).hexdigest()
    doc = db.db.oidc_refresh_tokens.find_one({"token_hash": token_hash, "client_id": client_id})
    if not doc:
        return token_error("invalid_grant", "Unknown refresh token")
    if time.time() > doc['expires_at']:
        db.db.oidc_refresh_tokens.delete_one({"_id": doc['_id']})
        return token_error("invalid_grant", "Refresh token expired")

    user = db.find_account_by_id(doc['user_id'])
    if not user or not user.get('is_active', True):
        db.db.oidc_refresh_tokens.delete_one({"_id": doc['_id']})
        return token_error("invalid_grant", "Account is not active")

    requested = (request.form.get('scope') or '').split()
    scopes = doc.get('scopes') or ["openid"]
    if requested:
        # Narrowing is allowed, widening is not (RFC 6749 §6).
        if not set(requested).issubset(set(scopes)):
            return token_error("invalid_scope", "Cannot broaden scope on refresh")
        scopes = requested

    # Rotate: the presented token dies with this response.
    db.db.oidc_refresh_tokens.delete_one({"_id": doc['_id']})

    return _issue_tokens(db, client_id, app_config, user,
                         scopes=scopes, nonce=None,
                         auth_time=doc.get('auth_time'),
                         amr=doc.get('amr') or [],
                         code_id=doc.get('code_id'))


def _issue_tokens(db, client_id, app_config, user, scopes, nonce, auth_time, amr, code_id):
    private_key, jwk = signing_key()
    now = int(time.time())

    claims = {
        "iss": issuer(),
        "aud": client_id,
        "azp": client_id,
        "exp": now + ACCESS_TOKEN_TTL,
        "iat": now,
        "auth_time": auth_time or now,
        "sub": str(user['_id']),
    }
    if amr:
        claims["amr"] = amr
    if nonce:
        claims["nonce"] = nonce
    claims.update(userinfo_claims(db, app_config, user, scopes, include_sub=False))

    id_token = jwt.encode(claims, private_key, algorithm='RS256',
                          headers={"kid": jwk['kid']})

    access_token = create_client_jwt(user, client_id, db, app_config,
                                     ttl_seconds=ACCESS_TOKEN_TTL, scopes=scopes)

    body = {
        "access_token": access_token,
        "id_token": id_token,
        "token_type": "Bearer",
        "expires_in": ACCESS_TOKEN_TTL,
        "scope": " ".join(scopes),
    }

    if "offline_access" in scopes:
        refresh_token = secrets.token_urlsafe(48)
        db.db.oidc_refresh_tokens.insert_one({
            "token_hash": hashlib.sha256(refresh_token.encode()).hexdigest(),
            "client_id": client_id,
            "user_id": user['_id'],
            "scopes": scopes,
            "auth_time": auth_time or now,
            "amr": amr,
            "code_id": code_id,
            "created_at": datetime.now(timezone.utc),
            "expires_at": now + REFRESH_TOKEN_TTL,
        })
        body["refresh_token"] = refresh_token

    return jsonify(body)


# ---------------------------------------------------------------------------
# UserInfo
# ---------------------------------------------------------------------------

def userinfo_claims(db, app_config, user, scopes, include_sub=True):
    claims = {"sub": str(user['_id'])} if include_sub else {}

    if "email" in scopes and user.get('email'):
        claims["email"] = user['email']
        claims["email_verified"] = bool(user.get('email_verified', False))

    if "profile" in scopes:
        name = user.get('display_name') or user.get('full_name') or user.get('username')
        if name:
            claims["name"] = name
        if user.get('username'):
            claims["preferred_username"] = user['username']
        if user.get('avatar_url'):
            claims["picture"] = user['avatar_url']

    if "roles" in scopes:
        from ..utils.token import CLIENT_ROLE_PERMISSIONS
        role = db.get_user_role_for_app(user['_id'], app_config['_id']) or "user"
        claims["role"] = role
        claims["permissions"] = CLIENT_ROLE_PERMISSIONS.get(role, ["read:app"])

    return claims


def bearer_error(description, status=401):
    resp = jsonify({"error": "invalid_token", "error_description": description})
    resp.status_code = status
    resp.headers['WWW-Authenticate'] = (
        f'Bearer realm="bifrost", error="invalid_token", error_description="{description}"'
    )
    return resp


@oidc_bp.route('/oidc/userinfo', methods=['GET', 'POST'])
def userinfo():
    auth_header = request.headers.get('Authorization', '')
    if not auth_header.startswith('Bearer '):
        resp = jsonify({"error": "invalid_request"})
        resp.status_code = 401
        resp.headers['WWW-Authenticate'] = 'Bearer realm="bifrost"'
        return resp

    raw = auth_header[7:].strip()
    try:
        payload = jwt.decode(raw, current_app.config['JWT_SECRET_KEY'],
                             algorithms=["HS256"], options={"verify_aud": False})
    except jwt.ExpiredSignatureError:
        return bearer_error("Access token expired")
    except jwt.InvalidTokenError:
        return bearer_error("Access token is not valid")

    scopes = (payload.get('scope') or '').split()
    if 'openid' not in scopes:
        return bearer_error("Token was not issued for the openid scope", 403)

    db = get_db()
    app_config = db.get_app_by_client_id(payload.get('aud'))
    if not app_config:
        return bearer_error("Token audience is not a known client")

    user = db.find_account_by_id(payload.get('sub'))
    if not user or not user.get('is_active', True):
        return bearer_error("Account is not active")

    return jsonify(userinfo_claims(db, app_config, user, scopes))


# ---------------------------------------------------------------------------
# Revocation (RFC 7009) and RP-initiated logout
# ---------------------------------------------------------------------------

@oidc_bp.route('/oidc/revoke', methods=['POST'])
def revoke():
    db = get_db()
    client_id, _app_config, err = authenticate_client(db)
    if err:
        return token_error(err[0], err[1], 401)

    supplied = request.form.get('token')
    if supplied:
        db.db.oidc_refresh_tokens.delete_one({
            "token_hash": hashlib.sha256(supplied.encode()).hexdigest(),
            "client_id": client_id,
        })

    # RFC 7009 §2.2: unknown tokens still return 200, so a caller cannot use this
    # endpoint to probe which tokens exist.
    return '', 200


@oidc_bp.route('/oidc/logout', methods=['GET', 'POST'])
def end_session():
    """RP-initiated logout. Ends the Bifrost SSO session, so the next authorize
    from any app in the directory prompts again."""
    db = get_db()
    hint = request.values.get('id_token_hint')
    post_logout = request.values.get('post_logout_redirect_uri')
    state = request.values.get('state')

    app_config = None
    if hint:
        try:
            # Read-only: the hint only tells us which client is asking, and the
            # logout itself is authorized by holding the session cookie.
            claims = jwt.decode(hint, options={"verify_signature": False})
            app_config = db.get_app_by_client_id(claims.get('aud'))
        except jwt.InvalidTokenError:
            app_config = None
    if not app_config and request.values.get('client_id'):
        app_config = db.get_app_by_client_id(request.values['client_id'])

    sso.end()
    session.pop(PENDING_KEY, None)

    # Only bounce to a URI the client actually registered; an unvalidated
    # post_logout_redirect_uri is the same open redirect as an unvalidated
    # redirect_uri, just at the other end of the session.
    if post_logout and app_config:
        allowed = list(app_config.get('oidc_post_logout_redirect_uris') or [])
        allowed += registered_redirect_uris(app_config)
        if post_logout in allowed:
            sep = '&' if '?' in post_logout else '?'
            suffix = f"{sep}state={urllib.parse.quote(state)}" if state else ''
            return redirect(f"{post_logout}{suffix}")

    return render_template('auth/logged_out.html', app=app_config)
