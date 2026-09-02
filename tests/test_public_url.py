"""The ticket's P1-2: a missing BIFROST_PUBLIC_URL must not resolve to localhost."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask

from bifrost.utils.urls import public_url


def _app(configured):
    app = Flask(__name__)
    app.config['BIFROST_PUBLIC_URL'] = configured
    return app


def test_config_wins_over_headers():
    """A header must never move the issuer relying parties pinned."""
    app = _app('https://id.example.com/')
    with app.test_request_context(headers={'X-Forwarded-Host': 'evil.example'}):
        assert public_url() == 'https://id.example.com'


def test_falls_back_to_forwarded_headers():
    app = _app(None)
    with app.test_request_context(headers={'X-Forwarded-Host': 'bifrost.example.com'}):
        assert public_url() == 'https://bifrost.example.com'


def test_no_config_no_request_yields_empty_not_localhost():
    app = _app(None)
    with app.app_context():
        assert public_url() == ''


def test_otp_creation_is_not_logged_with_the_code(caplog=None):
    """The code must not reach the log line (P0-2)."""
    import inspect
    from bifrost.models import auth
    src = inspect.getsource(auth.AuthMixin.create_otp)
    log_lines = [l for l in src.splitlines() if 'log.info' in l]
    assert log_lines, "create_otp no longer logs; update this test"
    assert not any('{code}' in l for l in log_lines), log_lines


if __name__ == '__main__':
    test_config_wins_over_headers()
    test_falls_back_to_forwarded_headers()
    test_no_config_no_request_yields_empty_not_localhost()
    test_otp_creation_is_not_logged_with_the_code()
    print('ok')
