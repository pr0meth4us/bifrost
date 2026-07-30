"""End-to-end checks for the OIDC provider and the SSO session.

Runs against mongomock — no live database, no network. Every assertion here
covers something that was actually broken or missing:

  * client authentication at the token endpoint
  * redirect_uri allowlisting
  * PKCE
  * authorization code replay
  * SSO: the second app must not re-prompt
  * directory isolation: a session in tenant A must not satisfy tenant B

    .venv/bin/python -m pytest tests/test_oidc.py -q
"""
import base64
import hashlib
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

mongomock = pytest.importorskip("mongomock")

os.environ.setdefault('SECRET_KEY', 'test-secret')
os.environ.setdefault('JWT_SECRET_KEY', 'test-jwt-secret')
os.environ.setdefault('MONGO_URI', 'mongodb://localhost:27017/test')
os.environ.setdefault('EMAIL_PASSWORD', 'test')
os.environ.setdefault('BIFROST_PUBLIC_URL', 'https://id.test')
os.environ.setdefault('SESSION_COOKIE_SECURE', 'false')

import jwt as pyjwt
from flask import Flask
from werkzeug.security import generate_password_hash

from bifrost.auth import oidc, sso
from bifrost.models import BifrostDB

CALLBACK_A = "https://app-a.test/callback"
CALLBACK_B = "https://app-b.test/callback"


@pytest.fixture
def ctx():
    """A Flask app with the OIDC + login blueprints over an in-memory Mongo."""
    client = mongomock.MongoClient()
    db = BifrostDB(client, 'test')

    app = Flask(__name__, template_folder=os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'bifrost', 'templates'))
    app.config.update(
        SECRET_KEY='test-secret',
        JWT_SECRET_KEY='test-jwt-secret',
        DB_NAME='test',
        BIFROST_PUBLIC_URL='https://id.test',
        OIDC_SSO_SESSION_SECONDS=3600,
        WTF_CSRF_ENABLED=False,
        TESTING=True,
    )

    # Point the blueprints' db helpers at mongomock instead of a real server.
    from bifrost.auth import api as auth_api, ui as auth_ui
    for module in (oidc, auth_ui, auth_api):
        if hasattr(module, 'get_db'):
            module.get_db = lambda _db=db: _db
        if hasattr(module, 'get_app_config'):
            module.get_app_config = lambda cid, _db=db: (_db, _db.get_app_by_client_id(cid))

    app.register_blueprint(oidc.oidc_bp)
    app.register_blueprint(auth_ui.auth_ui_bp)

    oidc._KEY_CACHE.clear()

    # Two apps in ONE directory (tenant "acme") plus one in its own.
    creds_a = db.register_application("App A", CALLBACK_A, tenant_id="acme")
    creds_b = db.register_application("App B", CALLBACK_B, tenant_id="acme")
    creds_c = db.register_application("Other Co", "https://other.test/cb")

    app_a = db.get_app_by_client_id(creds_a['client_id'])
    user_id = db.create_account({
        "client_id": "acme",
        "email": "user@acme.test",
        "display_name": "Acme User",
    })
    db.db.accounts.update_one(
        {"_id": user_id},
        {"$set": {"password_hash": generate_password_hash("hunter2")}})
    db.link_user_to_app(user_id, app_a['_id'])

    yield app, db, creds_a, creds_b, creds_c
    oidc._KEY_CACHE.clear()


def authorize_url(client_id, redirect_uri, **extra):
    from urllib.parse import urlencode
    params = {"client_id": client_id, "redirect_uri": redirect_uri,
              "response_type": "code", "scope": "openid email"}
    params.update(extra)
    return "/oidc/authorize?" + urlencode(params)


def code_from(response):
    from urllib.parse import parse_qs, urlparse
    return parse_qs(urlparse(response.headers['Location']).query)['code'][0]


def basic(client_id, secret):
    raw = base64.b64encode(f"{client_id}:{secret}".encode()).decode()
    return {"Authorization": f"Basic {raw}"}


def login(client, client_id):
    return client.post(f"/auth/ui/login?client_id={client_id}",
                       data={"email": "user@acme.test", "password": "hunter2"})


# ---------------------------------------------------------------------------

def test_discovery_and_jwks(ctx):
    app, *_ = ctx
    with app.test_client() as c:
        doc = c.get('/.well-known/openid-configuration').get_json()
        assert doc['issuer'] == 'https://id.test'
        assert 'refresh_token' in doc['grant_types_supported']
        assert 'S256' in doc['code_challenge_methods_supported']

        keys = c.get('/.well-known/jwks.json').get_json()['keys']
        assert keys[0]['kty'] == 'RSA' and keys[0]['kid']


def test_signing_key_is_stable_across_workers(ctx):
    """The bug: each worker generated its own key, so JWKS did not match the
    id_token that another worker had signed."""
    app, db, *_ = ctx
    with app.test_request_context():
        first = oidc.signing_key()[1]['kid']
    oidc._KEY_CACHE.clear()          # simulate a second worker process
    with app.test_request_context():
        assert oidc.signing_key()[1]['kid'] == first


def test_unregistered_redirect_uri_is_refused(ctx):
    app, _db, creds_a, *_ = ctx
    with app.test_client() as c:
        resp = c.get(authorize_url(creds_a['client_id'], "https://evil.test/steal"))
        assert resp.status_code == 400
        # Must not bounce to the attacker, not even with an error payload.
        assert 'Location' not in resp.headers


def test_token_endpoint_requires_client_authentication(ctx):
    app, _db, creds_a, *_ = ctx
    with app.test_client() as c:
        c.get(authorize_url(creds_a['client_id'], CALLBACK_A))
        code = code_from(login(c, creds_a['client_id']))

    with app.test_client() as c:
        # No secret at all.
        resp = c.post('/oidc/token', data={
            "grant_type": "authorization_code", "code": code,
            "client_id": creds_a['client_id'], "redirect_uri": CALLBACK_A})
        assert resp.status_code == 401
        assert resp.get_json()['error'] == 'invalid_client'

        # Wrong secret.
        resp = c.post('/oidc/token', data={
            "grant_type": "authorization_code", "code": code,
            "redirect_uri": CALLBACK_A}, headers=basic(creds_a['client_id'], "wrong"))
        assert resp.status_code == 401


def test_full_code_flow_issues_verifiable_id_token(ctx):
    app, _db, creds_a, *_ = ctx
    with app.test_client() as c:
        c.get(authorize_url(creds_a['client_id'], CALLBACK_A, nonce="n-123"))
        code = code_from(login(c, creds_a['client_id']))

        resp = c.post('/oidc/token', data={
            "grant_type": "authorization_code", "code": code,
            "redirect_uri": CALLBACK_A},
            headers=basic(creds_a['client_id'], creds_a['client_secret']))
        assert resp.status_code == 200
        body = resp.get_json()
        assert body['token_type'] == 'Bearer'
        assert body['expires_in'] == oidc.ACCESS_TOKEN_TTL

        jwks = c.get('/.well-known/jwks.json').get_json()
        key = pyjwt.PyJWK.from_dict(jwks['keys'][0]).key
        claims = pyjwt.decode(body['id_token'], key, algorithms=['RS256'],
                              audience=creds_a['client_id'])
        assert claims['iss'] == 'https://id.test'
        assert claims['nonce'] == 'n-123'
        assert claims['email'] == 'user@acme.test'
        assert claims['amr'] == ['pwd']
        assert claims['auth_time'] <= claims['iat']

        # userinfo accepts the access token it was issued alongside.
        info = c.get('/oidc/userinfo',
                     headers={"Authorization": f"Bearer {body['access_token']}"})
        assert info.get_json()['email'] == 'user@acme.test'


def test_access_token_without_openid_scope_cannot_read_userinfo(ctx):
    """The classic Bifrost session token has no scope claim and must not work
    as an OIDC access token."""
    app, db, creds_a, *_ = ctx
    from bifrost.utils.token import create_client_jwt
    app_a = db.get_app_by_client_id(creds_a['client_id'])
    user = db.find_account_by_email("user@acme.test", "acme")
    with app.test_request_context():
        legacy = create_client_jwt(user, creds_a['client_id'], db, app_a)
    with app.test_client() as c:
        resp = c.get('/oidc/userinfo', headers={"Authorization": f"Bearer {legacy}"})
        assert resp.status_code == 403


def test_authorization_code_is_single_use(ctx):
    app, db, creds_a, *_ = ctx
    with app.test_client() as c:
        c.get(authorize_url(creds_a['client_id'], CALLBACK_A, scope="openid offline_access"))
        code = code_from(login(c, creds_a['client_id']))
        auth = basic(creds_a['client_id'], creds_a['client_secret'])
        form = {"grant_type": "authorization_code", "code": code,
                "redirect_uri": CALLBACK_A}

        first = c.post('/oidc/token', data=form, headers=auth)
        assert first.status_code == 200
        assert 'refresh_token' in first.get_json()

        replay = c.post('/oidc/token', data=form, headers=auth)
        assert replay.status_code == 400
        assert replay.get_json()['error'] == 'invalid_grant'

    # Replay revokes what the code produced, so the refresh token dies with it.
    assert db.db.oidc_refresh_tokens.count_documents({}) == 0


def test_redirect_uri_must_match_at_token_exchange(ctx):
    app, _db, creds_a, *_ = ctx
    with app.test_client() as c:
        c.get(authorize_url(creds_a['client_id'], CALLBACK_A))
        code = code_from(login(c, creds_a['client_id']))
        resp = c.post('/oidc/token', data={
            "grant_type": "authorization_code", "code": code,
            "redirect_uri": "https://app-a.test/elsewhere"},
            headers=basic(creds_a['client_id'], creds_a['client_secret']))
        assert resp.status_code == 400
        assert resp.get_json()['error'] == 'invalid_grant'


def test_pkce_rejects_a_wrong_verifier(ctx):
    app, _db, creds_a, *_ = ctx
    verifier = "a" * 64
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')

    with app.test_client() as c:
        c.get(authorize_url(creds_a['client_id'], CALLBACK_A,
                            code_challenge=challenge, code_challenge_method="S256"))
        code = code_from(login(c, creds_a['client_id']))
        auth = basic(creds_a['client_id'], creds_a['client_secret'])

        bad = c.post('/oidc/token', data={
            "grant_type": "authorization_code", "code": code,
            "redirect_uri": CALLBACK_A, "code_verifier": "b" * 64}, headers=auth)
        assert bad.status_code == 400

    with app.test_client() as c:
        c.get(authorize_url(creds_a['client_id'], CALLBACK_A,
                            code_challenge=challenge, code_challenge_method="S256"))
        code = code_from(login(c, creds_a['client_id']))
        good = c.post('/oidc/token', data={
            "grant_type": "authorization_code", "code": code,
            "redirect_uri": CALLBACK_A, "code_verifier": verifier},
            headers=basic(creds_a['client_id'], creds_a['client_secret']))
        assert good.status_code == 200


def test_refresh_token_rotates_and_cannot_widen_scope(ctx):
    app, _db, creds_a, *_ = ctx
    auth = basic(creds_a['client_id'], creds_a['client_secret'])
    with app.test_client() as c:
        c.get(authorize_url(creds_a['client_id'], CALLBACK_A,
                            scope="openid email offline_access"))
        code = code_from(login(c, creds_a['client_id']))
        first = c.post('/oidc/token', data={
            "grant_type": "authorization_code", "code": code,
            "redirect_uri": CALLBACK_A}, headers=auth).get_json()

        refreshed = c.post('/oidc/token', data={
            "grant_type": "refresh_token",
            "refresh_token": first['refresh_token']}, headers=auth)
        assert refreshed.status_code == 200
        assert refreshed.get_json()['refresh_token'] != first['refresh_token']

        # The old one is dead after rotation.
        reused = c.post('/oidc/token', data={
            "grant_type": "refresh_token",
            "refresh_token": first['refresh_token']}, headers=auth)
        assert reused.status_code == 400

        widened = c.post('/oidc/token', data={
            "grant_type": "refresh_token",
            "refresh_token": refreshed.get_json()['refresh_token'],
            "scope": "openid email roles offline_access"}, headers=auth)
        assert widened.get_json()['error'] == 'invalid_scope'


def test_sso_second_app_does_not_reprompt(ctx):
    """The whole point. App B shares App A's directory, so authorize returns a
    code straight away instead of rendering the login form."""
    app, _db, creds_a, creds_b, _ = ctx
    with app.test_client() as c:
        c.get(authorize_url(creds_a['client_id'], CALLBACK_A))
        login(c, creds_a['client_id'])

        resp = c.get(authorize_url(creds_b['client_id'], CALLBACK_B))
        assert resp.status_code == 302
        assert resp.headers['Location'].startswith(CALLBACK_B)

        code = code_from(resp)
        body = c.post('/oidc/token', data={
            "grant_type": "authorization_code", "code": code,
            "redirect_uri": CALLBACK_B},
            headers=basic(creds_b['client_id'], creds_b['client_secret'])).get_json()
        assert 'id_token' in body


def test_sso_session_does_not_cross_directories(ctx):
    """A session in tenant 'acme' must never satisfy an authorize call from an
    app in a different directory."""
    app, _db, creds_a, _creds_b, creds_c = ctx
    with app.test_client() as c:
        c.get(authorize_url(creds_a['client_id'], CALLBACK_A))
        login(c, creds_a['client_id'])

        resp = c.get(authorize_url(creds_c['client_id'], "https://other.test/cb"))
        assert resp.status_code == 302
        assert '/auth/ui/login' in resp.headers['Location']


def test_prompt_none_without_a_session_returns_login_required(ctx):
    app, _db, creds_a, *_ = ctx
    with app.test_client() as c:
        resp = c.get(authorize_url(creds_a['client_id'], CALLBACK_A, prompt="none"))
        assert resp.status_code == 302
        assert 'error=login_required' in resp.headers['Location']


def test_prompt_login_forces_reauthentication(ctx):
    app, _db, creds_a, *_ = ctx
    with app.test_client() as c:
        c.get(authorize_url(creds_a['client_id'], CALLBACK_A))
        login(c, creds_a['client_id'])

        resp = c.get(authorize_url(creds_a['client_id'], CALLBACK_A, prompt="login"))
        assert '/auth/ui/login' in resp.headers['Location']


def test_logout_ends_the_sso_session(ctx):
    app, _db, creds_a, creds_b, _ = ctx
    with app.test_client() as c:
        c.get(authorize_url(creds_a['client_id'], CALLBACK_A))
        login(c, creds_a['client_id'])

        c.get(f"/oidc/logout?client_id={creds_a['client_id']}")

        resp = c.get(authorize_url(creds_b['client_id'], CALLBACK_B))
        assert '/auth/ui/login' in resp.headers['Location']


def test_registering_mid_flow_returns_a_code(ctx):
    """Signing up during an OIDC login used to drop the relying party entirely."""
    app, _db, creds_a, *_ = ctx
    with app.test_client() as c:
        c.get(authorize_url(creds_a['client_id'], CALLBACK_A))
        resp = c.post(f"/auth/ui/register?client_id={creds_a['client_id']}",
                      data={"email": "fresh@acme.test", "password": "hunter2",
                            "display_name": "Fresh"})
        assert resp.status_code == 302
        assert resp.headers['Location'].startswith(CALLBACK_A)
        assert 'code=' in resp.headers['Location']


# ---------------------------------------------------------------------------
# Directory scoping (the fa5a5dc regression)
# ---------------------------------------------------------------------------

def test_unscoped_account_lookup_is_an_error_not_a_silent_miss(ctx):
    """Forgetting the scope used to quietly search a phantom null tenant, which
    is how the bot and the payment webhooks stopped finding anyone."""
    _app, db, *_ = ctx
    with pytest.raises(ValueError):
        db.find_account_by_email("user@acme.test", None)


def test_directory_scope_defaults_to_the_apps_own_client_id(ctx):
    _app, db, _a, _b, creds_c = ctx
    app_c = db.get_app_by_client_id(creds_c['client_id'])
    assert db.directory_scope(app_c) == creds_c['client_id']


def test_accounts_are_isolated_between_directories(ctx):
    _app, db, *_ = ctx
    from bifrost.models.auth import ANY_TENANT
    assert db.find_account_by_email("user@acme.test", "acme") is not None
    assert db.find_account_by_email("user@acme.test", "somebody-else") is None
    assert db.find_account_by_email("user@acme.test", ANY_TENANT) is not None
