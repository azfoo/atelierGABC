"""Up-front verification for style bodies and the export template.

Both surfaces used to fail only at export time (style problems as a French
ExportError, template problems as caught Jinja exceptions in the export
log); this module catches bad edits at save/edit time instead. Two layers:

- Static checks, instant, no TeX involved: `check_style_body` scans one
  style's TeX body (parameter numbers, brace balance, \\begin/\\end pairs);
  `check_template` renders a candidate template through the REAL
  `exporter.build_tex` path (StrictUndefined, custom delimiters, style
  macroization) against a tiny synthetic project exercising every item
  branch — so it can't drift from what export actually does.
- Real compile, seconds: `verify_compile` runs that same synthetic project
  through the full export pipeline (lualatex + gregorio + lilypond) in a
  temp dir — the only way to catch genuine TeX errors (an undefined
  command in a style body, a broken preamble edit).

The LaTeX.js live preview is deliberately NOT part of this: it is a
permissive subset that silently renders things TeX rejects.
"""

import base64
import os
import re
import tempfile
import time

import exporter
import project as project_module

_PARAM_RE = re.compile(r'#(\d+)')
_BEGIN_END_RE = re.compile(r'\\(begin|end)\{([^}]*)\}')

# Minimal but real content, so the compile check exercises gregorio and
# lilypond, not just lualatex.
_GABC_STUB = 'name: Vérification;\n%%\n(c4) A(f)men.(g) (::)\n'
# \version low enough for any lilypond the app might drive (the bundle
# pins 2.26, but dev containers/system installs can be older): lilypond
# hard-fails on "program too old" if the declared version is newer than
# the binary.
_LY_STUB = '\\version "2.18.2"\n{ c\'4 d\' e\' f\' }\n'
# A real 1×1 PNG so the image branch (base64 → luacode* decode →
# \adjincludegraphics) is exercised end to end, crop included.
_PNG_STUB = base64.b64decode(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8'
    'z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==')


def check_style_body(body):
    """Static checks on one style's TeX macro body. Returns a list of
    French issue strings (empty = nothing suspicious). These are warnings:
    export itself only hard-blocks on the 9-parameter engine limit, but
    every one of these would make the eventual TeX compile fail or a
    content field be silently ignored."""
    issues = []
    body = str(body)

    nums = sorted({int(m.group(1)) for m in _PARAM_RE.finditer(body)})
    if nums and nums[-1] > 9:
        issues.append(
            'utilise #%d — TeX limite les macros à 9 paramètres' % nums[-1])
    else:
        for n in range(1, nums[-1] if nums else 0):
            if n not in nums:
                issues.append(
                    '#%d est utilisé mais jamais #%d — le champ %d des '
                    'éléments utilisant ce style serait ignoré'
                    % (nums[-1], n, n))

    # Brace balance, honouring \{ \} \\ escapes and % line comments —
    # the same characters TeX itself would honour inside the macro body.
    depth = 0
    i = 0
    stripped = []   # body with comments removed, for the begin/end scan
    while i < len(body):
        c = body[i]
        if c == '\\':
            stripped.append(body[i:i + 2])
            i += 2
            continue
        if c == '%':
            nl = body.find('\n', i)
            i = len(body) if nl == -1 else nl
            continue
        if c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth < 0:
                issues.append('accolade fermante } sans { ouvrante correspondante')
                depth = 0
        stripped.append(c)
        i += 1
    if depth > 0:
        issues.append('%d accolade(s) { jamais fermée(s)' % depth)

    stack = []
    for kind, name in _BEGIN_END_RE.findall(''.join(stripped)):
        if kind == 'begin':
            stack.append(name)
        elif not stack:
            issues.append('\\end{%s} sans \\begin{%s}' % (name, name))
        elif stack[-1] != name:
            issues.append(
                '\\begin{%s} fermé par \\end{%s}' % (stack[-1], name))
            stack.pop()
        else:
            stack.pop()
    for name in stack:
        issues.append('\\begin{%s} jamais fermé par \\end{%s}' % (name, name))

    return issues


def check_styles(styles):
    """Run check_style_body over a defs.styles dict. Returns a flat list of
    French warning strings prefixed with the style name (empty = all good)."""
    warnings = []
    if not isinstance(styles, dict):
        return warnings
    for key, body in styles.items():
        for issue in check_style_body(body):
            warnings.append('Style « %s » : %s' % (key, issue))
    return warnings


def _synthetic_project(tmpdir):
    """A tiny project exercising every template branch: a text item (style
    macro call), a linked gabc item, a linked .ly item, and a missing file."""
    gabc_path = os.path.join(tmpdir, 'verification.gabc')
    ly_path = os.path.join(tmpdir, 'verification.ly')
    png_path = os.path.join(tmpdir, 'verification.png')
    with open(gabc_path, 'w', encoding='utf-8') as f:
        f.write(_GABC_STUB)
    with open(ly_path, 'w', encoding='utf-8') as f:
        f.write(_LY_STUB)
    with open(png_path, 'wb') as f:
        f.write(_PNG_STUB)
    data = project_module.new_project()
    data['items'] = [
        {'type': 'text', 'key': 'header', 'contents': ['Vérification']},
        {'type': 'gabc', 'file_path': gabc_path},
        {'type': 'myr', 'source_path': None, 'file_path': ly_path},
        {'type': 'image', 'file_path': png_path, 'width': 0.5,
         'crop': {'left': 0.1, 'top': 0.1, 'right': 0.1, 'bottom': 0.1}},
        {'type': 'gabc', 'file_path': None},  # missing-file branch
    ]
    return data


def check_template(template_text):
    """Static template check: render the candidate through the real
    build_tex against the synthetic project, once per french_spacing
    branch so both sides of the babel/frpunct conditional are exercised.
    Returns a list of French error strings (empty = valid). No TeX runs."""
    errors = []
    with tempfile.TemporaryDirectory(prefix='verif-tmpl-') as tmpdir:
        tmpl_path = os.path.join(tmpdir, 'candidate.tex.j2')
        with open(tmpl_path, 'w', encoding='utf-8') as f:
            f.write(template_text)
        data = _synthetic_project(tmpdir)
        for spacing in ('frpunct', 'babel'):
            data['defs']['typography']['french_spacing'] = spacing
            try:
                tex = exporter.build_tex(
                    data, lilypond_path='lilypond',
                    snippet_prefix='verification', template_path=tmpl_path)
                # Renders fine but produces no document — an emptied-out
                # template would otherwise sail through to a failed export.
                for needed in ('\\documentclass', '\\begin{document}',
                               '\\end{document}'):
                    if needed not in tex:
                        msg = 'le modèle rendu ne contient pas %s' % needed
                        if msg not in errors:
                            errors.append(msg)
                        break
            except exporter.ExportError as e:
                msg = str(e)
                # Both branches are always tried: a frpunct failure is
                # usually global (a syntax error) but an error may live
                # inside the babel conditional only. An error already seen
                # on the frpunct branch is global — don't repeat it with a
                # babel label.
                if msg in errors:
                    continue
                if spacing == 'babel':
                    msg += ' (variante babel du réglage typographie)'
                errors.append(msg)
    return errors


_ERROR_LINE_RE = re.compile(r'^!|^.{0,20}[Ee]rror[ :]|Fatal error')


def _failure_excerpt(log):
    """The interesting lines of a failed compile log: TeX errors start with
    '!' (with the offending input on the following lines); lilypond and
    lyluatex say 'error:'. The raw tail is useless — lualatex ends every
    run with pages of font/node statistics."""
    lines = log.split('\n')
    picked = []
    for i, line in enumerate(lines):
        if _ERROR_LINE_RE.search(line):
            for ctx in lines[i:i + 3]:
                if ctx.strip() and ctx not in picked[-3:]:
                    picked.append(ctx)
        if len(picked) >= 24:
            break
    if picked:
        return '\n'.join(picked)
    return '\n'.join(l for l in lines if l.strip())[-2000:]


def verify_compile(template_text, settings):
    """Full pipeline check: export the synthetic project with the candidate
    template in a temp dir (lualatex + gregorio + lilypond). Returns
    {success, log, seconds}. Everything it writes is cleaned up."""
    static_errors = check_template(template_text)
    if static_errors:
        return {'success': False, 'log': '\n'.join(static_errors), 'seconds': 0}
    start = time.time()
    with tempfile.TemporaryDirectory(prefix='verif-compile-') as tmpdir:
        tmpl_path = os.path.join(tmpdir, 'candidate.tex.j2')
        with open(tmpl_path, 'w', encoding='utf-8') as f:
            f.write(template_text)
        data = _synthetic_project(tmpdir)
        run_settings = dict(settings)
        run_settings['output_folder'] = tmpdir
        try:
            result = exporter.export_project(
                os.path.join(tmpdir, 'verification.breviaire'),
                data, run_settings, template_path=tmpl_path)
        except exporter.ExportError as e:
            return {'success': False, 'log': str(e),
                    'seconds': round(time.time() - start, 1)}
        return {
            'success': result['success'],
            'log': '' if result['success'] else _failure_excerpt(result['log']),
            'seconds': round(time.time() - start, 1),
        }
