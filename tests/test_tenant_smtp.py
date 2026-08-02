"""A tenant's own SMTP config must be all-or-nothing, and must carry its identity.

Two failure modes this guards:

  * Half-configured tenant silently mixed with platform credentials — the
    tenant's host with our password fails auth, our host with the tenant's From
    address fails SPF/DKIM alignment at the recipient. Both look like "email
    just stopped working" to the tenant.
  * A fully configured tenant still sending as bifrostbyhelm@gmail.com, which
    is the entire thing they configured it to avoid.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from flask import Flask

from bifrost.services.email_service import resolve_smtp
from bifrost.utils.encryption import encrypt_value

WEBHOOK_SECRET = "b" * 48
PLATFORM = {
    'SMTP_SERVER': 'smtp.gmail.com',
    'SMTP_PORT': 587,
    'SENDER_EMAIL': 'bifrostbyhelm@gmail.com',
    'EMAIL_PASSWORD': 'platform-pw',
}


def tenant(**overrides):
    doc = {
        'app_name': 'Acme',
        'webhook_secret': WEBHOOK_SECRET,
        'smtp_host': 'smtp.acme.com',
        'smtp_port': 2525,
        'smtp_sender': 'noreply@acme.com',
        'smtp_password': encrypt_value('tenant-pw', WEBHOOK_SECRET),
    }
    doc.update(overrides)
    return doc


def test_resolve_smtp():
    app = Flask(__name__)
    app.config.update(PLATFORM)

    with app.app_context():
        # No app doc at all: platform mailbox.
        assert resolve_smtp(None)['sender'] == PLATFORM['SENDER_EMAIL']
        assert resolve_smtp({})['from_name'] is None

        # Fully configured tenant sends as itself, with its own credentials.
        got = resolve_smtp(tenant())
        assert got == {'host': 'smtp.acme.com', 'port': 2525,
                       'sender': 'noreply@acme.com', 'password': 'tenant-pw',
                       'from_name': 'Acme'}

        # from_name is overridable, and independent of app_name.
        assert resolve_smtp(tenant(smtp_sender_name='Acme Support'))['from_name'] == 'Acme Support'

        # Port defaults rather than crashing on a blank form field.
        assert resolve_smtp(tenant(smtp_port=''))['port'] == 587
        assert resolve_smtp(tenant(smtp_port='465'))['port'] == 465

        # Any missing piece falls all the way back — never a mix.
        for missing in ({'smtp_host': ''}, {'smtp_sender': ''}, {'smtp_password': ''}):
            got = resolve_smtp(tenant(**missing))
            assert got['host'] == PLATFORM['SMTP_SERVER'], missing
            assert got['sender'] == PLATFORM['SENDER_EMAIL'], missing
            assert got['password'] == PLATFORM['EMAIL_PASSWORD'], missing


if __name__ == "__main__":
    test_resolve_smtp()
    print("ok: tenant SMTP is all-or-nothing and sends under its own identity")
