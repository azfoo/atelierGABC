"""MusicXML → editable .ly import for myr items (TODO Phase 4).

Harmony Assistant has no CLI export, so the user exports MusicXML by hand;
this module runs musicxml2ly on it and post-processes the output into the
tracked, hand-editable .ly source. The post-processing fixes the exact
quirks found in the Phase 0 fidelity spike (and reproduced against a real
musicxml2ly run):

- pin \\version to 2.18.2: lilypond hard-fails "program too old" on a file
  declaring a version newer than the binary, so a document imported on a
  2.26 machine would refuse to compile on a legacy Mac's 2.22. Old-but-
  valid is accepted by every lilypond the app might drive.
- strip stray leading newlines inside quoted lyric syllables (a Harmony
  Assistant export artifact: the syllable arrives as "\\nQuand").
- replace the top-level \\header block with `tagline = ##f`: musicxml2ly
  copies page-header/footer credits (page numbers, export timestamps) into
  header fields, and even the legitimate ones (title, composer) would
  print *inside* the embedded score — titles in a bréviaire come from text
  items. Killing the tagline also keeps "Music engraving by LilyPond" out
  of previews.
"""

import os
import re
import subprocess

PINNED_VERSION = '2.18.2'

_VERSION_RE = re.compile(r'\\version\s*"[^"]*"')
# Complete quoted strings (escaped quotes allowed). A global sub pairs
# quotes correctly left-to-right; matching a bare `"` + newline instead
# would also hit closing quotes at end-of-line and join unrelated source
# lines (caught by test).
_STRING_RE = re.compile(r'"((?:[^"\\]|\\.)*)"', re.S)


def _strip_leading_string_ws(m):
    body = m.group(1)
    lead = body[:len(body) - len(body.lstrip())]
    if '\n' in lead:  # only the newline artifact, not intentional spaces
        return '"' + body.lstrip() + '"'
    return m.group(0)


class LyImportError(Exception):
    pass


def _replace_header_block(text):
    """Replace the first top-level \\header {...} with a tagline-off stub.
    Brace matching skips quoted strings and % comments so a brace inside a
    title can't derail it."""
    m = re.search(r'\\header\s*\{', text)
    stub = '\\header {\n    tagline = ##f\n    }'
    if not m:
        return text.rstrip('\n') + '\n\n' + stub + '\n'
    i = m.end()  # just past the opening brace
    depth = 1
    n = len(text)
    while i < n and depth:
        c = text[i]
        if c == '%':
            i = text.find('\n', i)
            if i == -1:
                break
        elif c == '"':
            i += 1
            while i < n and text[i] != '"':
                i += 2 if text[i] == '\\' else 1
        elif c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
        i += 1
    if depth:  # unbalanced — leave the file alone rather than corrupt it
        return text
    return text[:m.start()] + stub + text[i:]


def postprocess_ly(text):
    text, n = _VERSION_RE.subn('\\\\version "%s"' % PINNED_VERSION, text, count=1)
    if n == 0:
        text = '\\version "%s"\n' % PINNED_VERSION + text
    text = _STRING_RE.sub(_strip_leading_string_ws, text)
    text = _replace_header_block(text)
    return text


def _failure_excerpt(output, limit=12):
    lines = [l for l in output.splitlines() if 'error' in l.lower()]
    if not lines:
        lines = output.splitlines()[-limit:]
    return '\n'.join(lines[:limit])


def import_musicxml(musicxml_path, ly_path, musicxml2ly_bin):
    """Run musicxml2ly then post-process in place. Overwrites ly_path —
    callers confirm with the user first (it clobbers hand-edits)."""
    if not os.path.isfile(musicxml_path):
        raise LyImportError('Fichier MusicXML introuvable : %s' % musicxml_path)
    try:
        result = subprocess.run(
            [musicxml2ly_bin, '-o', ly_path, musicxml_path],
            capture_output=True, text=True, timeout=120,
        )
    except subprocess.TimeoutExpired:
        raise LyImportError('musicxml2ly interrompu après 120 s')
    except OSError as e:
        raise LyImportError('Impossible de lancer musicxml2ly : %s' % e)
    if result.returncode != 0 or not os.path.isfile(ly_path):
        raise LyImportError(
            'Échec de la conversion MusicXML :\n'
            + _failure_excerpt(result.stdout + (result.stderr or '')))
    with open(ly_path, encoding='utf-8') as f:
        text = f.read()
    text = postprocess_ly(text)
    tmp = ly_path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        f.write(text)
    os.replace(tmp, ly_path)
