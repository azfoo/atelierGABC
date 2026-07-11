"""CI check: does LilyPond/Pango actually resolve the app-bundled font?

This is the empirical half of the cross-platform font story (the unit tests
cover the generated fontconfig config; this proves LilyPond *uses* it). The
LilyPond/Pango side is the platform-sensitive one — the TeX side resolves via
OSFONTDIR, which is engine-level and OS-independent — so this runs on Linux,
macOS and Windows in CI (see .github/workflows/ci.yml) to confirm the bundled
EB Garamond is visible to the engraver on each, especially Windows where there
is no /etc/fonts to inherit.

`lilypond -dshow-available-fonts` prints every family Pango/fontconfig exposes
(then errors on the dummy input file — we only read its output). We assert the
bundled family shows up under the environment exporter._font_env() builds.
"""

import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'app'))
import exporter  # noqa: E402

# Every bundled family must be visible to Pango, not just the default.
NEEDLES = ('eb garamond', 'cormorant')


def main():
    lily = shutil.which('lilypond')
    if not lily:
        sys.exit('FAIL: lilypond not on PATH')
    if not exporter._bundled_fonts_dir():
        sys.exit('FAIL: no bundled fonts in this checkout (app/fonts/ empty)')

    env = exporter._font_env(lilypond_bin=lily)
    print('lilypond        :', lily)
    print('OSFONTDIR       :', env.get('OSFONTDIR'))
    print('FONTCONFIG_FILE :', env.get('FONTCONFIG_FILE'))
    conf = env.get('FONTCONFIG_FILE')
    if conf and os.path.isfile(conf):
        with open(conf, encoding='utf-8') as f:
            print('--- generated fonts.conf ---\n' + f.read())

    # Capture bytes and decode ourselves: LilyPond's font dump contains bytes
    # that are neither valid UTF-8 (macOS) nor the Windows ANSI codepage, so
    # text=True would crash before we could inspect the output.
    out = subprocess.run([lily, '-dshow-available-fonts', 'x'],
                         env=env, capture_output=True, timeout=300)
    text = (out.stdout + out.stderr).decode('utf-8', 'replace')
    log = text.lower()
    missing = [n for n in NEEDLES if n not in log]
    if missing:
        print(text[-4000:])
        sys.exit('FAIL: LilyPond/Pango does not see bundled families: %s'
                 % ', '.join(missing))
    print('OK: LilyPond/Pango sees every bundled family: %s' % ', '.join(NEEDLES))


if __name__ == '__main__':
    main()
