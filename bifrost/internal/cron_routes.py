"""Scheduled jobs, driven externally.

scheduler.py runs these on a daemon thread, which works anywhere the process
stays alive. It does not work on Cloud Run: CPU is throttled to near zero
between requests, so the thread stalls and expired subscriptions are never
downgraded — silently, with nothing in the UI to show for it. Cloud Scheduler
calls these routes instead, on the same cadence the thread used.

Authenticated by the OIDC token Cloud Scheduler attaches with
--oidc-service-account-email. An unauthenticated endpoint that downgrades
subscriptions is worse than the bug it fixes, so the routes fail closed: with
no CRON_SERVICE_ACCOUNT configured they refuse every request rather than
running the job.
"""
import logging

from flask import current_app, jsonify, request
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token

from . import internal_bp
from ..utils.urls import public_url
from ..scheduler import (
    run_expiration_check,
    run_expiration_warning_check,
    run_payment_sla_check,
)

log = logging.getLogger(__name__)


def _authorize():
    """Verify the caller is our Cloud Scheduler service account.

    Returns None when the request is authorized, or an error response tuple.
    """
    expected_sa = current_app.config.get('CRON_SERVICE_ACCOUNT')
    if not expected_sa:
        log.error("Cron endpoint called but CRON_SERVICE_ACCOUNT is unset; refusing.")
        return jsonify({"error": "cron endpoints not configured"}), 503

    header = request.headers.get('Authorization', '')
    if not header.startswith('Bearer '):
        return jsonify({"error": "unauthorized"}), 401

    # When --oidc-token-audience is omitted, Cloud Scheduler uses the job's own
    # URI as the audience. Rebuild that from public_url(), NOT request.base_url:
    # Cloud Run terminates TLS at the proxy and forwards plain HTTP, so
    # base_url says "http://" while the token says "https://" and every call is
    # rejected for a wrong audience.
    expected_audience = (
        current_app.config.get('CRON_AUDIENCE')
        or f"{public_url()}{request.path}"
    )

    try:
        # Verifies Google's signature, expiry, and the audience.
        claims = id_token.verify_oauth2_token(
            header[len('Bearer '):],
            google_requests.Request(),
            audience=expected_audience,
        )
    except Exception as exc:
        # Never echo the reason: it tells a prober which half they got wrong.
        log.warning("Cron token rejected: %s", exc)
        return jsonify({"error": "unauthorized"}), 401

    if claims.get('email') != expected_sa or not claims.get('email_verified'):
        log.warning("Cron token from unexpected principal: %s", claims.get('email'))
        return jsonify({"error": "unauthorized"}), 403

    return None


@internal_bp.route('/cron/reap', methods=['POST'])
def cron_reap():
    """Hourly. Downgrades expired subscriptions, then warns the ones expiring soon.

    Both hourly jobs share one endpoint so this costs one Cloud Scheduler job
    instead of two — the free tier is three per billing account.
    """
    denied = _authorize()
    if denied:
        return denied

    app = current_app._get_current_object()
    run_expiration_check(app)
    run_expiration_warning_check(app)
    return jsonify({"ok": True, "jobs": ["expiration", "expiration_warning"]})


@internal_bp.route('/cron/payment-sla', methods=['POST'])
def cron_payment_sla():
    """Every 15 minutes. SLA is measured in hours; sweep often enough that
    "approaching" still means something."""
    denied = _authorize()
    if denied:
        return denied

    run_payment_sla_check(current_app._get_current_object())
    return jsonify({"ok": True, "jobs": ["payment_sla"]})
