"""Bifrost's own IdP session — the thing that makes single sign-on single.

Every successful end-user authentication (password, email OTP, SMS OTP, social,
Telegram) calls `establish()`. `/oidc/authorize` calls `current()` and, when it
finds a live session for the same directory, mints an authorization code without
ever rendering the login form. That skip *is* single sign-on; without it Bifrost
only does federated login, where every app re-prompts for a password.

The session lives in the Flask signed cookie on the Bifrost domain, so it is
shared by every app that redirects here — which is the point.
"""
import time

from flask import current_app, session

SESSION_KEY = 'bifrost_sso'
DEFAULT_MAX_AGE = 12 * 3600


def _max_age():
    return current_app.config.get('OIDC_SSO_SESSION_SECONDS', DEFAULT_MAX_AGE)


def establish(user, scope, amr):
    """Record that this browser has authenticated `user` within `scope`.

    `amr` is the OIDC "authentication methods references" list — pwd, otp, sms,
    telegram, the social provider name. It rides into the id_token so relying
    parties can refuse, say, a session that was only ever established by SMS.
    """
    session.permanent = True
    session[SESSION_KEY] = {
        'uid': str(user['_id']),
        'scope': scope,
        'auth_time': int(time.time()),
        'amr': list(amr) if isinstance(amr, (list, tuple)) else [amr],
    }


def current(scope, max_age=None):
    """The live session for `scope`, or None.

    Returns None when the session belongs to a different directory: an account
    pool is a trust boundary, and a session in tenant A must never satisfy an
    authorize call from tenant B.
    """
    sess = session.get(SESSION_KEY)
    if not sess or sess.get('scope') != scope:
        return None

    age = int(time.time()) - sess.get('auth_time', 0)
    if age > _max_age():
        session.pop(SESSION_KEY, None)
        return None

    # RP asked for a fresher authentication than we have on file.
    if max_age is not None and age > max_age:
        return None

    return sess


def end():
    session.pop(SESSION_KEY, None)
