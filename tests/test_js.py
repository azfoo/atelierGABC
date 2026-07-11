"""Frontend sanity: the inline <script> in index.html must at least parse
(node --check). Not a substitute for browser testing — there is none in
this environment — but it catches the syntax-level breakage that a big
hand-edited single-file UI is most prone to."""

import os
import re
import subprocess
import tempfile
import unittest

from .base import APP_DIR, has

INDEX = os.path.join(APP_DIR, 'static', 'index.html')


class TestInlineJs(unittest.TestCase):
    def _scripts(self):
        with open(INDEX, encoding='utf-8') as f:
            html = f.read()
        return re.findall(r'<script>(.*?)</script>', html, re.S)

    @unittest.skipUnless(has('node'), 'node not installed')
    def test_inline_script_parses(self):
        scripts = self._scripts()
        self.assertTrue(scripts, 'no inline <script> found in index.html')
        with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False,
                                         encoding='utf-8') as f:
            f.write('\n'.join(scripts))
            path = f.name
        try:
            r = subprocess.run(['node', '--check', path],
                               capture_output=True, text=True, timeout=30)
            self.assertEqual(r.returncode, 0, r.stderr)
        finally:
            os.unlink(path)

    def test_worker_still_imports_exsurge(self):
        # The gabc preview depends on this exact contract.
        worker = os.path.join(APP_DIR, 'static', 'preview-worker.js')
        with open(worker, encoding='utf-8') as f:
            self.assertIn("importScripts('/exsurge.js')", f.read())
