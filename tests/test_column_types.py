"""Every editor type the Schema screen offers must change the editor.

The screen offered five types and the drawer honoured two: text, textarea and
number fell through to the inferred branch and did nothing. Nothing failed —
the setting was simply ignored, which is the worst kind of configuration bug,
because the screen goes on reporting the value back to you.

This pins the two files together rather than the five values, so adding a sixth
option to the dropdown without handling it fails here.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_SCREEN = ROOT / 'bifrost/templates/backoffice/cms_config.html'
GRID = ROOT / 'bifrost/templates/backoffice/content_grid.html'


def offered_types():
    """The values in the Schema screen's editor-type dropdown."""
    src = SCHEMA_SCREEN.read_text()
    block = src[src.index('c-type'):src.index('c-hidden')]
    pairs = re.search(r"\[\((.*?)\]\s*%\}", block, re.S)
    return set(re.findall(r"\('([a-z]+)',", pairs.group(0)))


def handled_kinds():
    """The kinds the drawer's field builder actually branches on."""
    src = GRID.read_text()
    block = src[src.index('function buildFields'):src.index('function loadForeignOptions')
                if 'function loadForeignOptions' in src else None]
    return set(re.findall(r"kind === '([a-z]+)'", block))


class TestColumnEditorTypes(unittest.TestCase):
    def test_every_offered_type_is_handled(self):
        offered, handled = offered_types(), handled_kinds()
        self.assertTrue(offered, "could not read the dropdown — did the markup move?")
        ignored = offered - handled - {'text'}   # 'text' is the final else branch
        self.assertEqual(ignored, set(),
                         f"the Schema screen offers {sorted(ignored)} but the drawer "
                         f"ignores them; it handles {sorted(handled)}")

    def test_the_override_beats_the_inferred_type(self):
        # Otherwise a column that looks like text stays a textarea however it is
        # configured, which is how three of these came to do nothing.
        src = GRID.read_text()
        self.assertIn('const kind = override ||', src)

    def test_the_regression_shape_is_gone(self):
        # The old form tested the override only inside two branches.
        src = GRID.read_text()
        self.assertNotIn("override === 'boolean' || pgType === 'boolean'", src)
        self.assertNotIn("pgType === 'json' || override === 'json'", src)


if __name__ == '__main__':
    unittest.main()
