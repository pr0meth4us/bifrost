"""Re-authentication in front of destructive actions (GitHub's sudo mode).

Session age is a poor proxy for risk: the old 30-minute idle timeout interrupted
everyone reading a log, while leaving DROP TABLE available for eight hours on
one morning sign-in. These tests pin the replacement.
"""
import re
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flask import Flask, session
from bifrost.backoffice import (SUDO_PERMISSIONS, SUDO_WINDOW_MINUTES,
                                grant_sudo, has_sudo)

ROOT = Path(__file__).resolve().parents[1]
app = Flask(__name__)
app.secret_key = 'test'


class TestSudoWindow(unittest.TestCase):
    def test_absent_stamp_is_not_sudo(self):
        with app.test_request_context('/'):
            self.assertFalse(has_sudo())

    def test_granting_opens_the_window(self):
        with app.test_request_context('/'):
            grant_sudo()
            self.assertTrue(has_sudo())

    def test_the_window_closes(self):
        with app.test_request_context('/'):
            stale = datetime.now(timezone.utc) - timedelta(minutes=SUDO_WINDOW_MINUTES + 1)
            session['sudo_at'] = stale.isoformat()
            self.assertFalse(has_sudo())

    def test_a_corrupt_stamp_is_not_sudo(self):
        # Fail closed: an unparseable value must not read as recently confirmed.
        with app.test_request_context('/'):
            session['sudo_at'] = 'whenever'
            self.assertFalse(has_sudo())

    def test_signing_in_counts_as_confirming(self):
        src = (ROOT / 'bifrost/backoffice/auth_routes.py').read_text()
        issue = src[src.index('def _issue_session'):src.index('def _resolve_tenant_app')]
        self.assertIn("session['sudo_at']", issue,
                      "a fresh sign-in must count as a recent authentication")


class TestGatedRoutes(unittest.TestCase):
    """The gate belongs on the action, not only on the page that leads to it."""

    GATED = [
        ('bifrost/backoffice/devtools_routes.py', 'devtools'),          # SQL Studio
        ('bifrost/backoffice/devtools_routes.py', 'devtools_execute'),  # runs the SQL
        ('bifrost/backoffice/app_routes.py', 'rotate_secret'),
        ('bifrost/backoffice/heimdall_routes.py', 'global_api_keys'),
        ('bifrost/backoffice/tenant_routes.py', 'refund_payment'),
    ]

    def test_every_destructive_route_is_gated(self):
        for path, func in self.GATED:
            with self.subTest(route=func):
                src = (ROOT / path).read_text()
                match = re.search(r'((?:@[^\n]*\n)+)def ' + func + r'\(', src)
                self.assertIsNotNone(match, f"{func} not found in {path}")
                self.assertIn('@requires_sudo', match.group(1),
                              f"{func} performs a destructive action ungated")

    def test_ordinary_work_is_not_gated(self):
        # If reviewing content or reading the queue needed sudo, people would
        # learn to type their password reflexively, which defeats the prompt.
        for path, func in [('bifrost/backoffice/tenant_routes.py', 'view_cms_grid'),
                           ('bifrost/backoffice/tenant_routes.py', 'save_cms_row'),
                           ('bifrost/backoffice/review_routes.py', 'submit_review')]:
            with self.subTest(route=func):
                src = (ROOT / path).read_text()
                match = re.search(r'((?:@[^\n]*\n)+)def ' + func + r'\(', src)
                self.assertNotIn('@requires_sudo', match.group(1))

    def test_json_endpoints_get_json_not_a_redirect(self):
        src = (ROOT / 'bifrost/backoffice/__init__.py').read_text()
        block = src[src.index('def requires_sudo'):src.index('def requires_sudo') + 1600]
        self.assertIn('jsonify', block,
                      "a fetch() cannot usefully follow a redirect to a login form")


class TestOpenRedirect(unittest.TestCase):
    def test_next_is_restricted_to_relative_paths(self):
        from bifrost.backoffice.auth_routes import _safe_next
        for hostile in ('https://evil.example/x', '//evil.example/x', 'javascript:alert(1)'):
            with self.subTest(value=hostile):
                self.assertIsNone(_safe_next(hostile))
        self.assertEqual(_safe_next('/backoffice/app/1/devtools'),
                         '/backoffice/app/1/devtools')


class TestSudoPermissionsAreDeclared(unittest.TestCase):
    def test_the_dangerous_permissions_are_named(self):
        self.assertEqual(
            SUDO_PERMISSIONS,
            frozenset({'db:execute', 'view:secrets', 'manage:secrets', 'transfer:ownership'}))


if __name__ == '__main__':
    unittest.main()
