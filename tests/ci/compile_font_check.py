"""CI check: the TeX/luaotfload side of bundled-font resolution, per OS.

Complements lilypond_font_check.py (the LilyPond/Pango side): here we drive the
app's own exporter to build AND compile a real one-file .tex with each bundled
family as the text font, and assert (1) a PDF is produced and (2) the log shows
no font-not-found — proving luaotfload resolves the bundled family by name via
OSFONTDIR on this platform, not just that fontconfig lists it.

Needs the full toolchain on PATH (lualatex + gregorio + lilypond); the CI
compile job provisions it. Run from the repo root.
"""

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'app'))
import exporter  # noqa: E402
import project   # noqa: E402

FAMILIES = ('EB Garamond', 'Cormorant')


def _check(family):
    data = project.new_project()
    data['defs']['typography']['text_font'] = family
    data['items'] = [{'type': 'text', 'key': 'body',
                      'contents': ['Deus, in adiutorium meum intende.']}]
    tmp = tempfile.mkdtemp(prefix='atelier-ci-')
    ppath = os.path.join(tmp, 'p.breviaire')
    project.save_project(ppath, data)
    res = exporter.export_project(ppath, data, {})
    log = res.get('log', '')
    miss = ('cannot be found' in log) or ('not loadable' in log)
    ok = bool(res.get('success')) and not miss
    print('  %-14s success=%s font_miss=%s pdf=%s'
          % (family, res.get('success'), miss, bool(res.get('pdf_path'))))
    if not ok:
        print(log[-3500:])
    return ok


def main():
    for tool in ('lualatex', 'gregorio', 'lilypond'):
        if not shutil.which(tool):
            sys.exit('FAIL: %s not on PATH' % tool)
    if not exporter._bundled_fonts_dir():
        sys.exit('FAIL: no bundled fonts in this checkout (app/fonts/ empty)')
    print('compiling a project per bundled family (TeX side):')
    if not all(_check(f) for f in FAMILIES):
        sys.exit('FAIL: a bundled family did not compile cleanly on the TeX side')
    print('OK: every bundled family compiles to PDF with no font-not-found')


if __name__ == '__main__':
    main()
