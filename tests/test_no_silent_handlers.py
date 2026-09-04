"""No handler may report a failure to the user without logging it.

Four separate bugs in one day were invisible for the same reason: an exception
caught by a handler broad enough to describe it as something else. "Update
failed", "we sent you a code", "connect a tenant database". Each cost a
production log archaeology session to find, and two had been broken for an
unknown length of time.

The fix is not per-bug. It is that a failure a user is told about must also be
a failure someone can grep for.
"""
import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Where a caught exception becomes a user-visible message.
SURFACES = ('flash', 'jsonify', 'abort', 'render_template')
LOGGING = ('error', 'exception', 'warning', 'critical')


def silent_handlers(path):
    tree = ast.parse(path.read_text())
    out = []
    enclosing = {}
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for sub in ast.walk(fn):
                if isinstance(sub, ast.ExceptHandler):
                    enclosing.setdefault(id(sub), fn.name)
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        body = ast.dump(ast.Module(body=node.body, type_ignores=[]))
        logs = any(f"attr='{m}'" in body for m in LOGGING)
        surfaces = any(f"id='{f}'" in body for f in SURFACES)
        if surfaces and not logs:
            out.append(f"{path.relative_to(ROOT)}:{node.lineno} "
                       f"in {enclosing.get(id(node), '<module>')}")
    return out


class TestNoSilentHandlers(unittest.TestCase):
    def test_every_user_facing_handler_logs(self):
        offenders = []
        for path in sorted((ROOT / 'bifrost').rglob('*.py')):
            offenders.extend(silent_handlers(path))
        self.assertEqual(offenders, [], "these tell the user something went wrong "
                                        "but leave nothing in the log:\n  " +
                                        "\n  ".join(offenders))


if __name__ == '__main__':
    unittest.main()
