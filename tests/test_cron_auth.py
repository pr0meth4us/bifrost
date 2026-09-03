"""The cron routes must fail closed.

They downgrade subscriptions and fire webhooks, so every way of arriving
without a valid Cloud Scheduler token has to be refused — including the
misconfiguration where nobody set CRON_SERVICE_ACCOUNT at all.
"""
import os
import sys
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask

from bifrost.internal import internal_bp

SA = 'bifrost-cron@example.iam.gserviceaccount.com'
ROUTES = ('/internal/cron/reap', '/internal/cron/payment-sla')


PUBLIC = 'https://bifrost-abc.a.run.app'


def _client(service_account=SA, public_url=PUBLIC):
    app = Flask(__name__)
    app.config['CRON_SERVICE_ACCOUNT'] = service_account
    app.config['CRON_AUDIENCE'] = None
    app.config['BIFROST_PUBLIC_URL'] = public_url
    app.register_blueprint(internal_bp)
    return app.test_client()


def _verifies_as(claims):
    """Patch token verification to return these claims, as Google would."""
    return mock.patch(
        'bifrost.internal.cron_routes.id_token.verify_oauth2_token',
        return_value=claims,
    )


def test_unconfigured_refuses_even_a_valid_token():
    """No CRON_SERVICE_ACCOUNT must not mean 'let everyone in'."""
    c = _client(service_account=None)
    with _verifies_as({'email': SA, 'email_verified': True}):
        for route in ROUTES:
            assert c.post(route).status_code == 503, route


def test_missing_and_malformed_headers_are_rejected():
    c = _client()
    for route in ROUTES:
        assert c.post(route).status_code == 401, route
        assert c.post(route, headers={'Authorization': SA}).status_code == 401, route
        assert c.post(route, headers={'Authorization': 'Basic x'}).status_code == 401, route


def test_unverifiable_token_is_rejected():
    c = _client()
    with mock.patch(
        'bifrost.internal.cron_routes.id_token.verify_oauth2_token',
        side_effect=ValueError('bad signature'),
    ):
        for route in ROUTES:
            r = c.post(route, headers={'Authorization': 'Bearer forged'})
            assert r.status_code == 401, route
            # The reason must not leak back to a prober.
            assert b'signature' not in r.data.lower(), route


def test_another_service_account_is_rejected():
    """A valid Google token is not the same as OUR scheduler's token."""
    c = _client()
    with _verifies_as({'email': 'someone-else@example.com', 'email_verified': True}):
        for route in ROUTES:
            r = c.post(route, headers={'Authorization': 'Bearer valid-but-wrong'})
            assert r.status_code == 403, route


def test_unverified_email_is_rejected():
    c = _client()
    with _verifies_as({'email': SA, 'email_verified': False}):
        for route in ROUTES:
            r = c.post(route, headers={'Authorization': 'Bearer x'})
            assert r.status_code == 403, route


def test_audience_uses_the_public_https_origin_not_the_proxied_scheme():
    """Cloud Run terminates TLS and forwards plain HTTP.

    request.base_url therefore says http:// while Cloud Scheduler's token
    audience says https://, and every real call 401s for a wrong audience.
    The expected audience must come from BIFROST_PUBLIC_URL.
    """
    c = _client()
    seen = {}

    def capture(token, request, audience=None):
        seen['audience'] = audience
        return {'email': SA, 'email_verified': True}

    with mock.patch('bifrost.internal.cron_routes.id_token.verify_oauth2_token', capture), \
            mock.patch('bifrost.internal.cron_routes.run_expiration_check'), \
            mock.patch('bifrost.internal.cron_routes.run_expiration_warning_check'):
        c.post('/internal/cron/reap', headers={'Authorization': 'Bearer good'})

    assert seen['audience'] == f'{PUBLIC}/internal/cron/reap', seen['audience']
    assert seen['audience'].startswith('https://'), seen['audience']


def test_explicit_cron_audience_overrides():
    c = _client()
    seen = {}

    def capture(token, request, audience=None):
        seen['audience'] = audience
        return {'email': SA, 'email_verified': True}

    # Re-create with an explicit override.
    app = Flask(__name__)
    app.config.update(CRON_SERVICE_ACCOUNT=SA, CRON_AUDIENCE='https://pinned/aud',
                      BIFROST_PUBLIC_URL=PUBLIC)
    app.register_blueprint(internal_bp)

    with mock.patch('bifrost.internal.cron_routes.id_token.verify_oauth2_token', capture), \
            mock.patch('bifrost.internal.cron_routes.run_payment_sla_check'):
        app.test_client().post('/internal/cron/payment-sla',
                               headers={'Authorization': 'Bearer good'})

    assert seen['audience'] == 'https://pinned/aud'


def test_authorized_token_runs_the_jobs():
    c = _client()
    with _verifies_as({'email': SA, 'email_verified': True}), \
            mock.patch('bifrost.internal.cron_routes.run_expiration_check') as reap, \
            mock.patch('bifrost.internal.cron_routes.run_expiration_warning_check') as warn, \
            mock.patch('bifrost.internal.cron_routes.run_payment_sla_check') as sla:
        assert c.post('/internal/cron/reap',
                      headers={'Authorization': 'Bearer good'}).status_code == 200
        assert reap.call_count == 1 and warn.call_count == 1
        assert sla.call_count == 0

        assert c.post('/internal/cron/payment-sla',
                      headers={'Authorization': 'Bearer good'}).status_code == 200
        assert sla.call_count == 1


if __name__ == '__main__':
    for name, fn in sorted(globals().items()):
        if name.startswith('test_'):
            fn()
            print('PASS', name)
    print('ok')
