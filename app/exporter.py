"""One-file .tex export + compile for .breviaire projects (TODO Phase 6).

The TeX structure lives in templates/breviaire.tex.j2 (same Jinja-in-TeX
idiom as score.tex.j2 and St-Josephs-Gateshead's missalette.tex.jinja);
this module only prepares data for it and runs the compile:

- reads each item's linked gabc/.ly file (a snapshot — re-export picks up
  later edits), flags missing files,
- TeX-escapes user text and fills the style templates' #1..#N slots,
- transforms frpunct.lua for verbatim luacode* embedding (the trailing
  `return french_punctuation` becomes a direct add_to_callback — a luacode*
  chunk's return value goes nowhere, unlike frpunct.tex's dofile),
- multi-pass lualatex -shell-escape with gregorio/lilypond dirs on PATH.

The generated .tex is genuinely self-contained: no \\input of companion
files, no pre-rendered scores; only standard packages are referenced. The
platform lessons baked into the template (file-based gabc compile, the
\\directlua character traps) are documented in the template itself and in
TODO.md Phase 0/6.
"""

import base64
import os
import re
import shutil
import subprocess
import sys
import tempfile

import jinja2

import project

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FRPUNCT_LUA = os.path.join(BASE_DIR, 'defaults', 'frpunct.lua')
TEMPLATE_FILE = os.path.join(BASE_DIR, 'templates', 'breviaire.tex.j2')

# TeX-special characters in user text content (style-field substitution).
_TEX_ESCAPES = {
    '\\': r'\textbackslash{}',
    '&': r'\&', '%': r'\%', '$': r'\$', '#': r'\#',
    '_': r'\_', '{': r'\{', '}': r'\}',
    '~': r'\textasciitilde{}', '^': r'\textasciicircum{}',
}
_TEX_ESCAPE_RE = re.compile(r'[\\&%$#_{}~^]')


class ExportError(Exception):
    pass


def escape_tex(text):
    return _TEX_ESCAPE_RE.sub(lambda m: _TEX_ESCAPES[m.group()], str(text))


_PARAM_RE = re.compile(r'#(\d+)')


def _style_nargs(body):
    """A style's parameter count = highest #N it references (min 1).
    TeX macros cap at 9 parameters — a hard engine limit."""
    nums = [int(m.group(1)) for m in _PARAM_RE.finditer(body)]
    n = max(nums) if nums else 1
    if n > 9:
        raise ExportError(
            'Le style utilise #%d — TeX limite les macros à 9 paramètres' % n)
    return n


def _macroize_styles(styles):
    """Turn defs.styles into \\newcommand-able definitions.

    Macro names must be letters only, so keys are sanitized (prefix
    'style', non-letters dropped, lowercased); collisions after
    sanitization get 'x' suffixes deterministically, in key order.
    Returns (defs list for the template, key→macro mapping).
    """
    defs_list, by_key, taken = [], {}, set()
    for key in styles:
        base = 'style' + re.sub(r'[^a-zA-Z]', '', str(key)).lower()
        if base == 'style':
            base = 'stylesans'
        name = base
        while name in taken:
            name += 'x'
        taken.add(name)
        body = styles[key]
        defs_list.append({'macro': name, 'nargs': _style_nargs(body), 'body': body})
        by_key[key] = defs_list[-1]
    return defs_list, by_key


def _frpunct_lua():
    with open(FRPUNCT_LUA, encoding='utf-8') as f:
        lua = f.read()
    return lua.replace(
        'return french_punctuation',
        'luatexbase.add_to_callback("kerning", french_punctuation, "frpunct.french_punctuation")',
        1,
    ).rstrip('\n')


def _read_linked(path):
    if not path or not os.path.isfile(path):
        return None
    with open(path, encoding='utf-8') as f:
        return f.read()


# Image formats \includegraphics reads natively under lualatex. SVG/EPS are
# rejected with an actionable French message: TeX cannot read SVG (every
# "SVG in LaTeX" package shells out to an external converter) and EPS only
# works via a hidden ghostscript conversion — anything that emits either
# also emits PDF. Revisit SVG once the Phase 5b converter spike lands.
_IMAGE_EXTS = {'.pdf', '.png', '.jpg', '.jpeg'}


def _prepare_image(item, snippet_prefix, count):
    """Base64 the linked image for the luacode* block (one-file rule: binary
    payloads travel as text in the .tex; Lua decodes and writes a scratch
    file at compile time, same lifecycle as the gabc snippets)."""
    path = item['file_path']
    ext = os.path.splitext(path)[1].lower()
    if ext not in _IMAGE_EXTS:
        raise ExportError(
            'Image « %s » : format non pris en charge à l\'export — '
            'exportez en PDF (ou PNG/JPEG) depuis votre application de dessin'
            % os.path.basename(path))
    with open(path, 'rb') as f:
        b64 = base64.b64encode(f.read()).decode('ascii')
    b64 = '\n'.join(b64[i:i + 76] for i in range(0, len(b64), 76))

    opts = ['width=%g\\linewidth' % _fraction(item.get('width'), 1.0)]
    crop = item.get('crop') or {}
    left, top = _fraction(crop.get('left')), _fraction(crop.get('top'))
    right, bottom = _fraction(crop.get('right')), _fraction(crop.get('bottom'))
    if any((left, top, right, bottom)) and left + right < 1 and top + bottom < 1:
        # adjustbox trim order is left/bottom/right/top, as fractions of the
        # image's own \width/\height — Python never reads pixel dimensions.
        opts.append('trim={%g\\width} {%g\\height} {%g\\width} {%g\\height}'
                    % (left, bottom, right, top))
        opts.append('clip')
    return {
        'type': 'image', 'missing': False,
        'snippet': '%s-img-%d%s' % (snippet_prefix, count, ext),
        'b64': b64,
        'options': ','.join(opts),
    }


def _fraction(value, default=0.0):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return min(max(v, 0.0), 1.0)


def _prepare_items(project_data, styles_by_key, snippet_prefix,
                   include_missing_notes=True):
    """Enrich raw project items into render-ready dicts for the template.

    include_missing_notes=False drops items with missing linked files
    silently instead of emitting the "[Fichier manquant : …]" note (the
    Settings « fichiers manquants » toggle, per the original brief)."""
    prepared = []
    gabc_count = 0
    image_count = 0
    for item in project_data.get('items', []):
        kind = item.get('type')
        if kind == 'text':
            style = styles_by_key.get(item.get('key'))
            if style is None:
                raise ExportError(
                    'Style inconnu « %s » (défini nulle part dans defs.styles)'
                    % item.get('key'))
            contents = item.get('contents', [])
            # Pad/truncate to the macro's arity: defs may have changed since
            # the item was edited (the UI conforms on style change, but the
            # file can be edited outside the app).
            args = [escape_tex(contents[i] if i < len(contents) else '')
                    for i in range(style['nargs'])]
            prepared.append({
                'type': 'text', 'missing': False,
                'macro': style['macro'], 'args': args,
            })
        elif kind == 'gabc':
            content = _read_linked(item.get('file_path'))
            if content is None:
                if include_missing_notes:
                    prepared.append(_missing(item.get('file_path')))
                continue
            gabc_count += 1
            prepared.append({
                'type': 'gabc', 'missing': False,
                'snippet': f'{snippet_prefix}-gabc-{gabc_count}',
                'content': content.rstrip('\n'),
            })
        elif kind == 'myr':
            content = _read_linked(item.get('file_path'))
            if content is None:
                if include_missing_notes:
                    prepared.append(_missing(item.get('file_path')))
                continue
            prepared.append({
                'type': 'myr', 'missing': False,
                'content': content.rstrip('\n'),
            })
        elif kind == 'image':
            path = item.get('file_path')
            if not path or not os.path.isfile(path):
                if include_missing_notes:
                    prepared.append(_missing(path))
                continue
            image_count += 1
            prepared.append(_prepare_image(item, snippet_prefix, image_count))
    return prepared


def _missing(path):
    return {
        'type': 'missing', 'missing': True,
        'missing_path': escape_tex(path or '(aucun fichier lié)'),
    }


def build_tex(project_data, *, lilypond_path, snippet_prefix, template_path=None,
              include_missing_notes=True):
    """Render the single self-contained .tex for a project.

    snippet_prefix names the per-item gabc scratch files the compile writes
    next to the .tex (e.g. '<prefix>-gabc-3.gabc'); callers pass a sanitized
    stem so filecontents* never sees spaces or TeX specials.

    template_path lets app.py point at a user-customised copy of
    breviaire.tex.j2 (the Settings-editable export template); defaults to
    the bundled one.
    """
    defs = project_data.get('defs', {})
    page = defs.get('page', {})

    geometry_opts = [page.get('size', 'a5paper')]
    if page.get('margin'):
        geometry_opts.append('margin=%s' % page['margin'])
    if page.get('gutter'):
        geometry_opts.append('bindingoffset=%s' % page['gutter'])

    typography = defs.get('typography', {})
    music = defs.get('music', {})
    french_spacing = typography.get('french_spacing', 'frpunct')
    # Per-surface fonts, uniform by default: the base text font also drives
    # gabc and lilypond lyrics unless each is given its own override.
    text_font = (typography.get('text_font') or '').strip()
    # An emptied text font falls back to the bundled house default (EB Garamond)
    # rather than the engine's Latin Modern — "no font" and the schema default
    # are the same face, no surprise substitution. Gated on the bundle actually
    # being present, so a stripped/dev checkout without the OFL binaries still
    # compiles (emitting \setmainfont for an absent family would fail). ly_font
    # inherits this resolved value below, keeping the default uniform.
    if not text_font and _bundled_fonts_dir():
        text_font = project.DEFAULT_TEXT_FONT
    gabc_font = (typography.get('gabc_font') or '').strip()   # '' = inherit main font (no wrap)
    ly_font = (typography.get('ly_font') or '').strip() or text_font  # rmfamily fallback
    try:
        text_size = float(typography.get('text_size') or 0)
    except (TypeError, ValueError):
        text_size = 0
    staffsize = music.get('staffsize', 16)          # gabc staff size
    ly_staffsize = music.get('ly_staffsize') or staffsize   # 0/None = inherit
    style_defs, styles_by_key = _macroize_styles(defs.get('styles', {}))

    jenv = jinja2.Environment(
        variable_start_string='<<', variable_end_string='>>',
        block_start_string='<%', block_end_string='%>',
        trim_blocks=True, lstrip_blocks=True,
        undefined=jinja2.StrictUndefined,
        loader=jinja2.BaseLoader(),
        keep_trailing_newline=True,
    )
    with open(template_path or TEMPLATE_FILE, encoding='utf-8') as f:
        try:
            template = jenv.from_string(f.read())
        except jinja2.TemplateSyntaxError as e:
            raise ExportError(
                'Modèle d\'export invalide (ligne %s) : %s' % (e.lineno, e.message))

    try:
        return template.render(
            geometry_options=','.join(geometry_opts),
            french_spacing=french_spacing,
            frpunct_lua=_frpunct_lua() if french_spacing != 'babel' else '',
            staffsize=staffsize,
            ly_staffsize=ly_staffsize,
            text_font=text_font,
            text_size=('%g' % text_size) if text_size else '',
            gabc_font=gabc_font,
            ly_font=ly_font,
            lilypond_path=lilypond_path.replace('\\', '/'),
            styles=style_defs,
            items=_prepare_items(project_data, styles_by_key, snippet_prefix,
                                 include_missing_notes=include_missing_notes),
        )
    except jinja2.UndefinedError as e:
        raise ExportError('Modèle d\'export invalide : %s' % e)


_RERUN_RE = re.compile(
    r'Rerun to get|Rerun to fix|rerunfilecheck|may have changed',
    re.IGNORECASE,
)


def _needs_rerun(run_log):
    # TeX hard-wraps log lines at ~79 columns, splitting phrases at arbitrary
    # points ("may have cha\nnged") — collapse newlines before matching.
    return bool(_RERUN_RE.search(run_log.replace('\n', '')))


def _tool_path(settings, key, binary):
    configured = settings.get(key)
    if configured and os.path.isfile(configured):
        return configured
    found = shutil.which(binary)
    if not found:
        raise ExportError(f'{binary} introuvable — configurez-le dans les Paramètres')
    return found


def _compile_env(lualatex_bin, gregorio_bin, lilypond_bin):
    """PATH with the tool dirs prepended — shell escapes inside the compile
    call `gregorio` and `repstopdf` by NAME, and repstopdf needs a `gs`
    (lilypond's bundled one lives in <bin>/../libexec). Absolute paths to
    lualatex alone are not enough; this mirrors the CI smoke exactly."""
    env = os.environ.copy()
    dirs = []
    for b in (lualatex_bin, gregorio_bin, lilypond_bin):
        d = os.path.dirname(os.path.abspath(b))
        if d not in dirs:
            dirs.append(d)
    libexec = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(lilypond_bin)), '..', 'libexec'))
    if os.path.isdir(libexec):
        dirs.append(libexec)
    env['PATH'] = os.pathsep.join(dirs + [env.get('PATH', '')])
    return _font_env(env, lilypond_bin=lilypond_bin)


def _bundled_fonts_dir(d=None):
    """The app's bundled-fonts directory, but only if it exists and holds at
    least one font file — so pointing the engines at it never breaks a compile
    before the binaries are actually shipped. Returns None otherwise."""
    d = d or os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fonts')
    try:
        if any(f.lower().endswith(('.otf', '.ttf', '.ttc')) for f in os.listdir(d)):
            return d
    except OSError:
        pass
    return None


def _xml_escape(text):
    return (str(text).replace('&', '&amp;')
            .replace('<', '&lt;').replace('>', '&gt;'))


def _system_font_dirs():
    """Per-OS directories where the system keeps its fonts. Listed explicitly in
    our generated fontconfig config so a bundled font sits *alongside* the
    system faces even when no full base config is found to include — this is
    what closes the Windows gap (no /etc/fonts there). Only existing dirs."""
    if sys.platform == 'win32':
        dirs = [os.path.join(os.environ.get('WINDIR', r'C:\Windows'), 'Fonts')]
        local = os.environ.get('LOCALAPPDATA')
        if local:
            dirs.append(os.path.join(local, 'Microsoft', 'Windows', 'Fonts'))
    elif sys.platform == 'darwin':
        home = os.path.expanduser('~')
        dirs = ['/System/Library/Fonts', '/Library/Fonts',
                os.path.join(home, 'Library', 'Fonts')]
    else:
        home = os.path.expanduser('~')
        dirs = ['/usr/share/fonts', '/usr/local/share/fonts',
                os.path.join(home, '.fonts'),
                os.path.join(home, '.local', 'share', 'fonts')]
    return [d for d in dirs if os.path.isdir(d)]


def _lilypond_font_dirs(lilypond_bin):
    """LilyPond ships its own faces (Emmentaler, the music/text fonts) and its
    own fontconfig config under its install tree. When we hand LilyPond a
    FONTCONFIG_FILE of our own we MUST keep those visible or engraving loses its
    music font — so return (font_dirs, base_conf|None) discovered next to the
    binary: <root>/share/lilypond (fonts, scanned recursively) and, if present,
    <root>/etc/fonts/fonts.conf to include. ([], None) when unlocatable."""
    if not lilypond_bin:
        return [], None
    root = os.path.dirname(os.path.dirname(os.path.abspath(lilypond_bin)))
    font_dirs = [os.path.join(root, *rel) for rel in (
        ('share', 'lilypond'), ('usr', 'share', 'lilypond'),
        ('share', 'fonts'), ('usr', 'share', 'fonts'))]
    font_dirs = [d for d in font_dirs if os.path.isdir(d)]
    base = next((os.path.join(root, *rel) for rel in (
        ('etc', 'fonts', 'fonts.conf'), ('usr', 'etc', 'fonts', 'fonts.conf'))
        if os.path.isfile(os.path.join(root, *rel))), None)
    return font_dirs, base


_FONTCONFIG_FILE = None   # generated once per process


def _fontconfig_file(fonts_dir, lilypond_bin=None):
    """Generate (once) a fontconfig config for the LilyPond/Pango side that lists
    the bundled-fonts dir *alongside* the system font dirs and LilyPond's own
    font dirs, and includes a full base config when one can be found (so system
    aliases/rendering rules survive). Returns the path.

    Unlike the earlier version this never returns None: rather than depend on
    finding a base file (there is none at /etc/fonts on Windows) it always
    emits explicit <dir> entries, so the bundled font — and, crucially, the
    system faces and LilyPond's own music fonts — stay visible on every OS."""
    global _FONTCONFIG_FILE
    if _FONTCONFIG_FILE is not None:
        return _FONTCONFIG_FILE
    ly_dirs, ly_base = _lilypond_font_dirs(lilypond_bin)
    # A base config to <include> so system alias/rendering rules are preserved.
    # LilyPond's own bundle sets FONTCONFIG_FILE (mac/Windows); Linux has
    # /etc/fonts; homebrew elsewhere; else LilyPond's own etc/fonts.
    base = os.environ.get('FONTCONFIG_FILE')
    if not base or not os.path.isfile(base):
        fp = os.environ.get('FONTCONFIG_PATH')
        candidates = ([os.path.join(fp, 'fonts.conf')] if fp else []) + [
            '/etc/fonts/fonts.conf',
            '/usr/local/etc/fonts/fonts.conf',
            '/opt/homebrew/etc/fonts/fonts.conf',
        ] + ([ly_base] if ly_base else [])
        base = next((c for c in candidates if os.path.isfile(c)), None)

    seen, dir_lines = set(), []
    for d in [fonts_dir] + ly_dirs + _system_font_dirs():
        ad = os.path.abspath(d)
        if ad not in seen:
            seen.add(ad)
            dir_lines.append('  <dir>%s</dir>' % _xml_escape(ad))

    work = os.path.join(tempfile.gettempdir(), 'atelier-fontconfig')
    cache = os.path.join(work, 'cache')
    os.makedirs(cache, exist_ok=True)
    conf = os.path.join(work, 'fonts.conf')
    lines = ['<?xml version="1.0"?>',
             '<!DOCTYPE fontconfig SYSTEM "fonts.dtd">',
             '<fontconfig>'] + dir_lines
    if base:
        lines.append('  <include ignore_missing="yes">%s</include>'
                     % _xml_escape(base))
    lines.append('  <cachedir>%s</cachedir>' % _xml_escape(cache))
    lines.append('</fontconfig>')
    with open(conf, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')
    _FONTCONFIG_FILE = conf
    return conf


def _font_env(env=None, lilypond_bin=None):
    """Merge the bundled-fonts lookups into an environment so a bundled family
    resolves *by name* in BOTH engines — OSFONTDIR (luaotfload/TeX, platform-
    independent) and a fontconfig config (LilyPond/Pango) — with no system
    install. No-op when nothing is bundled, so every font-resolving call
    (export, ly-preview, validate-font, available-fonts) shares one consistent
    view. lilypond_bin, when known, lets the fontconfig side keep LilyPond's own
    music fonts visible; callers that don't have it fall back to PATH lookup."""
    env = os.environ.copy() if env is None else env
    fonts_dir = _bundled_fonts_dir()
    if not fonts_dir:
        return env
    prior = env.get('OSFONTDIR', '')
    env['OSFONTDIR'] = os.pathsep.join([fonts_dir] + ([prior] if prior else []))
    conf = _fontconfig_file(fonts_dir, lilypond_bin or shutil.which('lilypond'))
    if conf:
        env['FONTCONFIG_FILE'] = conf
    return env


def target_tex_path(project_path, settings):
    """Where the next export would write its .tex — next to the project, or in
    output_folder if set. Shared by export_project and export_state so the
    "did the exported file change" check looks at exactly the file a re-export
    would overwrite."""
    stem = os.path.splitext(os.path.basename(project_path))[0]
    out_dir = settings.get('output_folder') or os.path.dirname(os.path.abspath(project_path))
    return os.path.join(out_dir, stem + '.tex')


def export_state(project_data, project_path, settings):
    """Status of any prior export recorded in the project, so a re-export can
    warn before clobbering a hand-edited .tex (the exported .tex is a
    deliverable users legitimately tweak by hand). Returns
    {target_tex, prior} where `prior` is None (no comparable prior export) or
    {state, tex_path, exported_at, ...} with state one of:

      'missing'  — the recorded .tex is gone (moved/deleted); nothing to
                   clobber, but the caller should say so
      'modified' — the recorded .tex exists and its mtime is newer than the
                   moment we wrote it: edited since export, re-export would
                   overwrite those edits
      'clean'    — recorded .tex present and untouched since export

    Only the file a re-export would actually overwrite (target_tex) is
    guarded; if the record points elsewhere (output folder changed, project
    renamed) there is no clobber risk and `prior` is None.
    """
    target = target_tex_path(project_path, settings)
    rec = (project_data or {}).get('last_export') or {}
    rec_path = rec.get('tex_path')
    prior = None
    if rec_path and os.path.abspath(rec_path) == os.path.abspath(target):
        exported_at = rec.get('exported_at')
        if not os.path.isfile(rec_path):
            prior = {'state': 'missing', 'tex_path': rec_path, 'exported_at': exported_at}
        elif exported_at is not None and os.path.getmtime(rec_path) > exported_at:
            prior = {'state': 'modified', 'tex_path': rec_path,
                     'exported_at': exported_at, 'modified_at': os.path.getmtime(rec_path)}
        else:
            prior = {'state': 'clean', 'tex_path': rec_path, 'exported_at': exported_at}
    return {'target_tex': target, 'prior': prior}


def export_project(project_path, project_data, settings, template_path=None):
    """Write <stem>.tex next to the project (or in output_folder) and compile
    it to <stem>.pdf. Returns {success, tex_path, pdf_path, log, returncode}
    plus, on success, `last_export` ({tex_path, exported_at}) which the caller
    should persist into the project so the next re-export can detect a
    hand-edited .tex (see export_state).

    The .tex is a deliverable in its own right (the original brief asks for
    tex export), so it is kept, not cleaned up.
    """
    lualatex_bin = _tool_path(settings, 'lualatex_path', 'lualatex')
    gregorio_bin = _tool_path(settings, 'gregorio_path', 'gregorio')
    lilypond_bin = _tool_path(settings, 'lilypond_path', 'lilypond')

    stem = os.path.splitext(os.path.basename(project_path))[0]
    safe_stem = re.sub(r'[^A-Za-z0-9_-]+', '_', stem) or 'breviaire'
    out_dir = settings.get('output_folder') or os.path.dirname(os.path.abspath(project_path))
    os.makedirs(out_dir, exist_ok=True)

    tex = build_tex(project_data, lilypond_path=lilypond_bin,
                    snippet_prefix=safe_stem, template_path=template_path,
                    include_missing_notes=settings.get('missing_files_in_tex', True))
    tex_path = os.path.join(out_dir, stem + '.tex')
    with open(tex_path, 'w', encoding='utf-8') as f:
        f.write(tex)
    # Record the mtime of the file exactly as we wrote it, so a later manual
    # edit (which bumps mtime strictly past this) is detectable on re-export.
    last_export = {'tex_path': tex_path, 'exported_at': os.path.getmtime(tex_path)}

    env = _compile_env(lualatex_bin, gregorio_bin, lilypond_bin)
    log_lines = []
    returncode = -1
    try:
        # Multi-pass like _compile_file: gregoriotex asks for a rerun on the
        # first pass, and the spike showed the first macOS pass can log a
        # spurious snippet error — the final pass decides.
        for run in range(1, 4):
            result = subprocess.run(
                [lualatex_bin,
                 '--shell-escape',           # required by lyluatex + gregorio spawn
                 '--interaction=nonstopmode',
                 f'--jobname={stem}',
                 os.path.basename(tex_path)],
                cwd=out_dir, env=env,
                capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=600,
            )
            returncode = result.returncode
            run_log = result.stdout + (result.stderr or '')
            log_lines.append(f'--- Passe {run} ---\n{run_log}')
            if not _needs_rerun(run_log) or run == 3:
                break

        pdf_path = os.path.join(out_dir, stem + '.pdf')
        success = returncode == 0 and os.path.isfile(pdf_path)
        return {
            'success': success,
            'tex_path': tex_path,
            'pdf_path': pdf_path if success else None,
            'log': '\n'.join(log_lines),
            'returncode': returncode,
            'last_export': last_export,
        }
    except subprocess.TimeoutExpired:
        return {'success': False, 'tex_path': tex_path, 'pdf_path': None,
                'log': '\n'.join(log_lines) + '\nCompilation interrompue après 600 s.',
                'returncode': -1, 'last_export': last_export}
    finally:
        # Scratch files from the compile; the .tex itself is a deliverable
        # and lyluatex's tmp-ly/ is a cache that speeds up re-exports.
        for f in os.listdir(out_dir):
            if (f.startswith(safe_stem + '-gabc-')
                    or f.startswith(safe_stem + '-img-')
                    or f in (stem + '.aux', stem + '.log')):
                try:
                    os.remove(os.path.join(out_dir, f))
                except OSError:
                    pass
