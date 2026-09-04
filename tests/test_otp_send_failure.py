"""A failed OTP send must not look like a delivered one.

send_email swallows SMTP exceptions and returns False. Every caller that
routes a user onward from "we sent you a code" has to check it, or a dead
mailbox is indistinguishable from a wrong password.
"""
import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# (file, enclosing function) for every send_otp_email call on a login path.
CALLERS = [
    ('bifrost/backoffice/auth_routes.py', '_start_mfa'),
    ('bifrost/auth/ui.py', 'forgot_password'),
    ('bifrost/auth/api.py', None),  # module-level route fn, name resolved below
]


def send_calls(path):
    """Every send_otp_email call node in the file, with its parent statement."""
    tree = ast.parse((ROOT / path).read_text())
    found = []
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            child.parent = node
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and getattr(node.func, 'id', None) == 'send_otp_email'):
            found.append(node)
    return found


class TestOtpSendResultIsChecked(unittest.TestCase):
    def test_no_caller_discards_the_send_result(self):
        for path, _fn in CALLERS:
            for call in send_calls(path):
                parent = call.parent
                # A bare `send_otp_email(...)` sits directly under an Expr
                # statement: the return value goes nowhere.
                self.assertNotIsInstance(
                    parent, ast.Expr,
                    f"{path}: send_otp_email result is discarded — a failed "
                    f"send will be reported to the user as a sent code")

    def test_every_login_path_file_is_covered(self):
        for path, _fn in CALLERS:
            self.assertTrue(send_calls(path), f"{path}: no send_otp_email call found — "
                                              f"did it move? update CALLERS")


if __name__ == '__main__':
    unittest.main()
