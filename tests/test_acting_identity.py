"""Attestation records a person, not a Mongo ObjectId.

reviewed_by is read from the tenant's database, where a Bifrost ObjectId
resolves to nothing. The id remains the session's lookup key; the email is what
gets written into tenant columns and audit rows.
"""
import sys, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flask import Flask, session
from bifrost.backoffice import acting_identity

app = Flask(__name__)
app.secret_key = 'test'

OID = '6a68326cfd37081598552ec8'


class TestActingIdentity(unittest.TestCase):
    def test_email_is_preferred(self):
        with app.test_request_context('/'):
            session['backoffice_user'] = OID
            session['backoffice_email'] = 'reviewer@example.com'
            self.assertEqual(acting_identity(), 'reviewer@example.com')

    def test_falls_back_to_the_id_for_sessions_issued_before_this_change(self):
        # Live sessions predate backoffice_email; they must keep attributing to
        # something rather than logging every action as 'unknown'.
        with app.test_request_context('/'):
            session['backoffice_user'] = OID
            self.assertEqual(acting_identity(), OID)

    def test_unknown_only_when_there_is_no_session_at_all(self):
        with app.test_request_context('/'):
            self.assertEqual(acting_identity(), 'unknown')

    def test_the_lookup_key_is_left_alone(self):
        # Changing backoffice_user itself would break get_managed_apps and the
        # role lookups, which key on the id.
        with app.test_request_context('/'):
            session['backoffice_user'] = OID
            session['backoffice_email'] = 'reviewer@example.com'
            acting_identity()
            self.assertEqual(session['backoffice_user'], OID)


if __name__ == '__main__':
    unittest.main()
