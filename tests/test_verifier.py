"""Static verifier checks (app/verifier.py). The real-compile verify path
is exercised by the slow exporter test instead (same pipeline)."""

import unittest

from .base import APP_DIR

import os
import verifier


class TestStyleBody(unittest.TestCase):
    def check(self, body):
        return verifier.check_style_body(body)

    def test_clean_default_styles_pass(self):
        import project
        for key, body in project.DEFAULT_STYLES.items():
            self.assertEqual(self.check(body), [], key)

    def test_param_over_nine(self):
        issues = self.check('{#10}')
        self.assertTrue(any('9' in i for i in issues))

    def test_param_gap(self):
        issues = self.check('{#1 #3}')
        self.assertTrue(any('#2' in i for i in issues))

    def test_unbalanced_brace(self):
        self.assertTrue(self.check('{#1'))

    def test_escaped_braces_ok(self):
        self.assertEqual(self.check(r'{\{ #1 \}}'), [])

    def test_comment_ignored(self):
        self.assertEqual(self.check('{#1}% {unclosed in comment'), [])

    def test_env_mismatch(self):
        issues = self.check(r'\begin{center}#1\end{flushright}')
        self.assertTrue(issues)

    def test_unclosed_env(self):
        issues = self.check(r'\begin{multicols}{2}#1')
        self.assertTrue(any('multicols' in i for i in issues))


class TestCheckStyles(unittest.TestCase):
    def test_prefixed_flat_list(self):
        warnings = verifier.check_styles({'ok': '{#1}', 'bad': '{#1'})
        self.assertEqual(len(warnings), 1)
        self.assertIn('bad', warnings[0])


class TestCheckTemplate(unittest.TestCase):
    def _bundled(self):
        path = os.path.join(APP_DIR, 'templates', 'breviaire.tex.j2')
        with open(path, encoding='utf-8') as f:
            return f.read()

    def test_bundled_template_valid(self):
        self.assertEqual(verifier.check_template(self._bundled()), [])

    def test_jinja_error_reported_with_line(self):
        errors = verifier.check_template('<% if oops %>')
        self.assertTrue(errors)

    def test_emptied_template_rejected(self):
        # Renders fine through Jinja but produces no document — must not
        # sail through to a broken export.
        errors = verifier.check_template('% rien\n')
        self.assertTrue(errors)

    def test_babel_only_error_labelled(self):
        # Break just the babel branch: reference a variable that only
        # renders there.
        tmpl = self._bundled().replace(
            '\\usepackage[french]{babel}',
            '\\usepackage[french]{babel}<< inconnue_babel >>')
        errors = verifier.check_template(tmpl)
        self.assertTrue(errors)
