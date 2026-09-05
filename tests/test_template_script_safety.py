"""No HTML entity may reach a <script> block.

An autoescaped Jinja template renders the STRING '""' as &#34;&#34;. Inside
markup that is invisible; inside <script> it is a syntax error that kills the
whole block, so every handler on the page silently stops existing while the
page itself looks perfectly normal. That is what `{{ x | tojson if c else '""' }}`
did — the fallback branch pasted a pre-formed literal instead of encoding a
value, and only tenants who took that branch ever saw it.

The rule this enforces: tojson is applied to a value, never used to paste a
literal. It renders the grid through every combination of optional config
blocks, because the bug lived exclusively in the branches my own fixture took
the other way.
"""
import itertools
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jinja2 import Environment, FileSystemLoader, select_autoescape

from bifrost.models.review_queue import ReviewSchema

ROOT = Path(__file__).resolve().parents[1]

BASE = {'table': 'questions', 'controls': ['fluency_passed', 'correctness_passed'],
        'display': ['body_kh']}
OPTIONAL = {
    'child': {'table': 'choices', 'fk': 'question_id',
              'columns': ['body_kh'], 'flag': 'is_correct'},
    'evidence': [{'column': 'source_ref', 'role': 'citation'}],
    'annotations': {'table': 'question_terms', 'fk': 'question_id',
                    'start': 'start_char', 'end': 'end_char', 'target': 'body_kh'},
    'verdicts': {'column': 'pipeline_meta',
                 'controls': {'correctness_passed': {'path': 'correctness',
                                                     'provenance': 'checked_by'}}},
}


def render(review):
    env = Environment(loader=FileSystemLoader(str(ROOT / 'bifrost/templates')),
                      autoescape=select_autoescape(['html']))
    env.globals.update(url_for=lambda e, **k: '/x/' + e,
                       csrf_token=lambda: 'tok',
                       session={}, request=type('R', (), {'endpoint': '', 'args': {}})())
    src = (ROOT / 'bifrost/templates/backoffice/content_grid.html').read_text()
    # Render the script block alone: the surrounding chrome needs the whole app.
    start = src.index('{% block scripts %}')
    tmpl = env.from_string(src[start:src.index('{% endblock %}', start)]
                           .replace('{% block scripts %}', ''))
    return tmpl.render(
        app={'_id': 'a1'}, selected_table='questions', tables=['questions'],
        review=review, schema_by_col={}, table_col_config={}, visible_columns=[],
        role_readonly_cols=[], children_by_parent={}, span_counts_by_parent={},
        review_reason_column=None, can_write=True, can_approve=True, rows=[])


def script_bodies(html):
    return re.findall(r'<script\b[^>]*>(.*?)</script>', html, re.S)


class TestNoEntitiesInScripts(unittest.TestCase):
    def test_every_config_shape_renders_clean_javascript(self):
        names = list(OPTIONAL)
        for r in range(len(names) + 1):
            for combo in itertools.combinations(names, r):
                block = dict(BASE, **{k: OPTIONAL[k] for k in combo})
                schema = ReviewSchema.from_config({'review_queue': block})
                with self.subTest(config=combo or ('minimal',)):
                    for body in script_bodies(render(schema)):
                        found = re.findall(r'&(?:amp|quot|#\d+|lt|gt|#x[0-9a-f]+);',
                                           body, re.I)
                        self.assertEqual(
                            found, [],
                            f"HTML entities reached a <script> with config {combo}: "
                            f"{found[:3]} — the whole block fails to parse")

    def test_no_review_queue_at_all_renders_clean(self):
        for body in script_bodies(render(None)):
            self.assertEqual(re.findall(r'&(?:amp|quot|#\d+);', body), [])

    def test_the_fallback_literal_pattern_is_gone(self):
        # tojson encodes a value; it must never paste a pre-formed literal.
        src = (ROOT / 'bifrost/templates/backoffice/content_grid.html').read_text()
        self.assertNotIn("tojson if", src,
                         "a conditional that chooses between tojson and a literal "
                         "will escape the literal branch")


if __name__ == '__main__':
    unittest.main()
