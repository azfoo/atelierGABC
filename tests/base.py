"""Shared test scaffolding.

The app modules live flat in app/ and import each other bare
(`import project`), so app/ goes on sys.path — inserted at position 0 so
`import app` finds app/app.py rather than treating the app/ directory as
a namespace package when tests run from the repo root.

Run from the repo root (the -t . keeps `tests` a package so the relative
imports in the test modules resolve):

    python3 -m unittest discover -s tests -t . -v

Include slow tests (real lualatex/gregorio/lilypond compiles, ~1–2 min):

    ATELIER_SLOW_TESTS=1 python3 -m unittest discover -s tests -t .
"""

import base64
import os
import shutil
import sys
import tempfile
import unittest

REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_DIR = os.path.join(REPO_DIR, 'app')
FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fixtures')
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

SLOW = os.environ.get('ATELIER_SLOW_TESTS') == '1'

GABC_STUB = 'name: Test;\n%%\n(c4) A(f)men.(g) (::)\n'
LY_STUB = '\\version "2.18.2"\n{\n  c\'4 d\'4 e\'4 f\'4\n}\n'
# A real 1×1 PNG, for exercising the image branch (base64 embed + crop)
# without depending on any external asset.
PNG_STUB = base64.b64decode(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8'
    'z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==')


def has(binary):
    return shutil.which(binary) is not None


class TempDirTestCase(unittest.TestCase):
    """A fresh temp dir per test, self-cleaning."""

    def setUp(self):
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory(prefix='atelier-test-')
        self.addCleanup(self._tmp.cleanup)
        self.tmp = self._tmp.name

    def write(self, name, content):
        path = os.path.join(self.tmp, name)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)
        return path

    def write_bytes(self, name, content):
        path = os.path.join(self.tmp, name)
        with open(path, 'wb') as f:
            f.write(content)
        return path


class AppTestCase(TempDirTestCase):
    """Flask test client with settings isolated to the temp dir, so tests
    can never touch the developer's real app/settings.json."""

    def setUp(self):
        super().setUp()
        import app as appmod
        self.appmod = appmod
        old = appmod.SETTINGS_FILE
        appmod.SETTINGS_FILE = os.path.join(self.tmp, 'settings.json')
        self.addCleanup(setattr, appmod, 'SETTINGS_FILE', old)
        self.client = appmod.app.test_client()
