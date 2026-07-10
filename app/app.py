from flask import Flask, request, jsonify, send_file, Response
import os, json, subprocess, shutil, sys, threading, webbrowser, time
import base64, hashlib, tempfile, re
import project as project_module
import exporter as exporter_module
import verifier as verifier_module
import lyimport as lyimport_module

app = Flask(__name__, static_folder='static', static_url_path='')

if getattr(sys, 'frozen', False):
    BASE_DIR = sys._MEIPASS
    _USER_DIR = os.path.join(os.path.expanduser('~'), '.atelier-gabc')
    SETTINGS_FILE = os.path.join(_USER_DIR, 'settings.json')
    os.makedirs(_USER_DIR, exist_ok=True)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    _USER_DIR = BASE_DIR
    SETTINGS_FILE = os.path.join(BASE_DIR, 'settings.json')

# User-customisable export template: a copy of templates/breviaire.tex.j2
# in the user dir takes precedence over the bundled one and survives app
# auto-updates (same mechanism the old app had for score.tex.j2).
USER_EXPORT_TEMPLATE = os.path.join(_USER_DIR, 'breviaire.tex.j2')

# ── Version + repo URL (read from pyproject.toml bundled alongside the app) ─

def _read_pyproject():
    try:
        import tomllib
        if getattr(sys, 'frozen', False):
            p = os.path.join(sys._MEIPASS, 'pyproject.toml')
        else:
            p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'pyproject.toml')
        with open(p, 'rb') as f:
            data = tomllib.load(f)
        return (
            data['project']['version'],
            data.get('project', {}).get('urls', {}).get('Repository', ''),
        )
    except Exception:
        return 'dev', ''

VERSION, UPDATE_REPO_URL = _read_pyproject()

# ── Heartbeat ────────────────────────────────────────────────────────────

_last_heartbeat = time.time()


def _heartbeat_monitor():
    # In the packaged app, exit if the browser tab has been closed for >3 min.
    # setInterval keeps firing in background tabs, so silence means tab closed.
    while True:
        time.sleep(30)
        if getattr(sys, 'frozen', False) and time.time() - _last_heartbeat > 180:
            os._exit(0)


# ── Velopack auto-update ──────────────────────────────────────────────────

_update_version = None
_update_info = None


def _make_velopack_locator():
    """Build a VelopackLocatorConfig from the installation layout next to the
    running executable.  Returns None if sq.version is not found (i.e. we are
    not running from a Velopack-managed install)."""
    from pathlib import Path
    current_dir = Path(sys.executable).parent
    if not (current_dir / 'sq.version').exists():
        return None
    try:
        from velopack import VelopackLocatorConfig
        if sys.platform == 'darwin':
            root_dir = current_dir.parents[1]
            update_exe = current_dir / 'UpdateMac'
            packages_dir = Path.home() / 'Library' / 'Caches' / 'AtelierGABC' / 'packages'
            try:
                packages_dir.mkdir(parents=True, exist_ok=True)
            except OSError:
                pass
        else:
            root_dir = current_dir.parent
            update_exe = root_dir / ('Update.exe' if sys.platform == 'win32' else 'UpdateNix')
            packages_dir = root_dir / 'packages'
        return VelopackLocatorConfig(
            RootAppDir=str(root_dir),
            UpdateExePath=str(update_exe),
            PackagesDir=str(packages_dir),
            ManifestPath=str(current_dir / 'sq.version'),
            CurrentBinaryDir=str(current_dir),
            IsPortable=False,
        )
    except Exception:
        return None


def _make_update_manager():
    locator = _make_velopack_locator()
    if locator is None:
        return None
    try:
        from velopack import UpdateManager
        return UpdateManager(UPDATE_REPO_URL, locator=locator)
    except Exception:
        return None


def _check_for_updates_bg():
    global _update_version, _update_info
    if not getattr(sys, 'frozen', False):
        return
    try:
        mgr = _make_update_manager()
        if mgr is None:
            return
        info = mgr.check_for_updates()
        if info:
            _update_info = info
            _update_version = str(info.target_full_release.version)
    except Exception:
        pass

BUNDLED_EXPORT_TEMPLATE = os.path.join(BASE_DIR, 'templates', 'breviaire.tex.j2')


def load_settings():
    if os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE, encoding='utf-8') as f:
            return json.load(f)
    return {}


def save_settings_data(data):
    with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)


def _macos_native_dialog(kind, filetypes=None, default_name=None):
    """File/folder picker via osascript — no main-thread constraint on macOS."""
    try:
        if kind == 'folder':
            script = 'POSIX path of (choose folder with prompt "Choisir un dossier")'
        elif kind == 'save':
            name_clause = f' default name "{default_name}"' if default_name else ''
            script = (
                f'POSIX path of (choose file name with prompt "Enregistrer sous"'
                f'{name_clause})'
            )
        else:
            exts = [p[2:] for _, p in (filetypes or []) if p not in ('*.*', '*')]
            if exts:
                type_list = '{' + ', '.join(f'"{e}"' for e in exts) + '}'
                script = (
                    f'POSIX path of (choose file of type {type_list}'
                    f' with prompt "Choisir un fichier")'
                )
            else:
                script = 'POSIX path of (choose file with prompt "Choisir un fichier")'
        r = subprocess.run(
            ['osascript', '-e', script],
            capture_output=True, text=True, timeout=300,
        )
        return r.stdout.strip() if r.returncode == 0 else ''
    except Exception:
        return ''


_dialog_lock = threading.Lock()


def _tk_dialog(kind, **kwargs):
    # Prevent concurrent dialogs: Flask is threaded and a second click while the
    # first dialog is open would otherwise spawn a second picker.
    if not _dialog_lock.acquire(blocking=False):
        return ''
    try:
        if sys.platform == 'darwin':
            return _macos_native_dialog(
                kind, filetypes=kwargs.get('filetypes'), default_name=kwargs.get('default_name'),
            )
        import tkinter as tk
        from tkinter import filedialog
        root_tk = tk.Tk()
        root_tk.withdraw()
        root_tk.attributes('-topmost', True)
        try:
            if kind == 'file':
                return filedialog.askopenfilename(
                    filetypes=kwargs.get('filetypes'),
                ) or ''
            if kind == 'save':
                return filedialog.asksaveasfilename(
                    filetypes=kwargs.get('filetypes'),
                    defaultextension=kwargs.get('defaultextension'),
                    initialfile=kwargs.get('default_name'),
                ) or ''
            return filedialog.askdirectory() or ''
        finally:
            root_tk.destroy()
    finally:
        _dialog_lock.release()


@app.route('/')
def index():
    return app.send_static_file('index.html')


@app.route('/api/settings', methods=['GET'])
def api_get_settings():
    return jsonify(load_settings())


@app.route('/api/settings', methods=['POST'])
def api_post_settings():
    save_settings_data(request.json)
    return jsonify({'ok': True})


@app.route('/api/browse-project')
def api_browse_project():
    try:
        path = _tk_dialog('file', filetypes=[('Bréviaire', '*.breviaire'), ('Tous les fichiers', '*.*')])
        return jsonify({'path': path})
    except Exception as e:
        return jsonify({'path': '', 'error': str(e)})


@app.route('/api/browse-project-save')
def api_browse_project_save():
    try:
        path = _tk_dialog(
            'save',
            filetypes=[('Bréviaire', '*.breviaire')],
            defaultextension='.breviaire',
            default_name='sans titre.breviaire',
        )
        return jsonify({'path': path})
    except Exception as e:
        return jsonify({'path': '', 'error': str(e)})


@app.route('/api/project/last')
def api_project_last():
    """Tells the frontend whether there's a last-opened project to auto-load,
    so it can reopen it on launch and only prompt when there isn't one."""
    settings = load_settings()
    path = settings.get('last_project_path') or ''
    return jsonify({'path': path, 'exists': bool(path) and os.path.isfile(path)})


@app.route('/api/project')
def api_project_get():
    path = request.args.get('path') or load_settings().get('last_project_path') or ''
    if not path or not os.path.isfile(path):
        return jsonify({'error': 'Not found'}), 404
    try:
        data = project_module.load_project(path)
    except project_module.ProjectError as e:
        return jsonify({'error': str(e)}), 400
    except (OSError, json.JSONDecodeError) as e:
        return jsonify({'error': str(e)}), 400
    return jsonify({'path': path, 'data': data, 'item_status': project_module.file_status(data)})


@app.route('/api/project', methods=['POST'])
def api_project_save():
    body = request.json or {}
    path = body.get('path')
    data = body.get('data')
    if not path:
        return jsonify({'error': 'Chemin manquant'}), 400
    try:
        project_module.save_project(path, data)
    except project_module.ProjectError as e:
        return jsonify({'error': str(e)}), 400
    settings = load_settings()
    settings['last_project_path'] = path
    save_settings_data(settings)
    # Non-blocking static style checks (TODO Phase 2 verifier): a suspicious
    # style body never prevents saving the project, but the frontend
    # surfaces the warnings so problems appear at edit time, not export time.
    try:
        warnings = verifier_module.check_styles(
            (data.get('defs') or {}).get('styles') or {})
    except Exception:
        warnings = []
    return jsonify({'ok': True, 'style_warnings': warnings})


@app.route('/api/styles/check', methods=['POST'])
def api_styles_check():
    """Live validation for the defs editor: same static checks that run at
    save, but returned per style key so the UI can show each issue under
    the textarea being edited, as-you-type (debounced client-side)."""
    styles = (request.json or {}).get('styles') or {}
    issues = {}
    if isinstance(styles, dict):
        for key, body in styles.items():
            found = verifier_module.check_style_body(body)
            if found:
                issues[key] = found
    return jsonify({'issues': issues})


@app.route('/api/project/new', methods=['POST'])
def api_project_new():
    body = request.json or {}
    path = body.get('path')
    if not path:
        return jsonify({'error': 'Chemin manquant'}), 400
    data = project_module.new_project()
    project_module.save_project(path, data)
    settings = load_settings()
    settings['last_project_path'] = path
    save_settings_data(settings)
    return jsonify({'path': path, 'data': data, 'item_status': project_module.file_status(data)})


@app.route('/api/browse-file')
def api_browse_file():
    try:
        path = _tk_dialog('file', filetypes=[('Fichiers TeX', '*.tex'), ('Tous les fichiers', '*.*')])
        return jsonify({'path': path})
    except Exception as e:
        return jsonify({'path': '', 'error': str(e)})


@app.route('/api/browse-linked-file')
def api_browse_linked_file():
    """Generic extension-filterable open dialog, for linking a .gabc/.myr/.ly
    file to a composer item."""
    ext = (request.args.get('ext') or '').strip().lstrip('.')
    label = request.args.get('label') or ('Fichiers .' + ext if ext else 'Fichiers')
    filetypes = [(label, f'*.{ext}')] if ext else []
    filetypes.append(('Tous les fichiers', '*.*'))
    try:
        path = _tk_dialog('file', filetypes=filetypes)
        return jsonify({'path': path})
    except Exception as e:
        return jsonify({'path': '', 'error': str(e)})


# Only the two text formats the composer edits in-app. This is a loopback
# local app, but the whitelist keeps the generic file read/write endpoints
# from ever touching anything the UI has no business editing.
_EDITABLE_LINKED_EXTS = {'.gabc', '.ly'}

# Starter contents for files created from scratch in the composer (TODO
# Phase 3): same header stub the old single-file editor used for gabc; the
# .ly stub pins \version low — lilypond hard-fails "program too old" on
# files declaring a version newer than the binary (see verifier._LY_STUB).
_NEW_FILE_STUBS = {
    '.gabc': 'name: Nouveau chant;\n%%\n(c4) A(f)men.(g) (::)\n',
    '.ly': '\\version "2.18.2"\n{\n  c\'4 d\'4 e\'4 f\'4\n}\n',
}


def _editable_linked_path(path):
    if not path:
        return None, 'Chemin manquant'
    ext = os.path.splitext(path)[1].lower()
    if ext not in _EDITABLE_LINKED_EXTS:
        return None, f'Extension non modifiable ici : {ext or "(aucune)"}'
    return path, None


@app.route('/api/linked-file')
def api_get_linked_file():
    path, err = _editable_linked_path(request.args.get('path') or '')
    if err:
        return jsonify({'error': err}), 400
    if not os.path.isfile(path):
        return jsonify({'error': 'Fichier introuvable : %s' % path}), 404
    try:
        with open(path, encoding='utf-8') as f:
            return jsonify({'content': f.read()})
    except (OSError, UnicodeDecodeError) as e:
        return jsonify({'error': str(e)}), 400


@app.route('/api/linked-file', methods=['POST'])
def api_save_linked_file():
    body = request.json or {}
    path, err = _editable_linked_path(body.get('path'))
    if err:
        return jsonify({'error': err}), 400
    # Same atomic write pattern as save_project: a crash mid-write must not
    # leave a half-written chant on disk.
    tmp = path + '.tmp'
    try:
        with open(tmp, 'w', encoding='utf-8') as f:
            f.write(body.get('content') or '')
        os.replace(tmp, path)
    except OSError as e:
        return jsonify({'error': str(e)}), 400
    return jsonify({'ok': True})


@app.route('/api/browse-linked-save')
def api_browse_linked_save():
    """Save-as dialog for creating a new .gabc/.ly from scratch."""
    ext = (request.args.get('ext') or '').strip().lstrip('.')
    if '.' + ext not in _EDITABLE_LINKED_EXTS:
        return jsonify({'path': '', 'error': 'Extension inconnue'}), 400
    default = 'nouveau chant.gabc' if ext == 'gabc' else 'nouvelle pièce.ly'
    try:
        path = _tk_dialog(
            'save',
            filetypes=[(f'Fichiers .{ext}', f'*.{ext}')],
            defaultextension='.' + ext,
            default_name=default,
        )
        return jsonify({'path': path})
    except Exception as e:
        return jsonify({'path': '', 'error': str(e)})


@app.route('/api/linked-file/new', methods=['POST'])
def api_new_linked_file():
    """Create a starter .gabc/.ly at the given path (from the save dialog
    above). Refuses to overwrite — 'new file' must never clobber an
    existing chant the dialog happened to point at."""
    body = request.json or {}
    path = body.get('path') or ''
    ext = os.path.splitext(path)[1].lower()
    if ext not in _NEW_FILE_STUBS:
        # The save dialogs add the extension, but a hand-typed name may not.
        exts = [e for e in _NEW_FILE_STUBS if path.lower().endswith(e)]
        if not exts and body.get('kind') in ('gabc', 'ly'):
            path += '.' + body['kind']
            ext = os.path.splitext(path)[1].lower()
    if ext not in _NEW_FILE_STUBS:
        return jsonify({'error': 'Extension inconnue'}), 400
    if os.path.exists(path):
        return jsonify({'error': 'Ce fichier existe déjà : %s' % path}), 400
    try:
        with open(path, 'w', encoding='utf-8') as f:
            f.write(_NEW_FILE_STUBS[ext])
    except OSError as e:
        return jsonify({'error': str(e)}), 400
    return jsonify({'path': path})


@app.route('/api/browse-musicxml')
def api_browse_musicxml():
    """Open dialog for a MusicXML file to import into a .ly score. MusicXML is
    an interchange format any notation program exports (Harmony Assistant,
    MuseScore, Sibelius, Finale…) — not tied to one editor. Three extensions
    in the wild, so not api_browse_linked_file's single-ext shape."""
    filetypes = [
        ('MusicXML', '*.musicxml'), ('MusicXML', '*.xml'), ('MusicXML', '*.mxl'),
        ('Tous les fichiers', '*.*'),
    ]
    try:
        path = _tk_dialog('file', filetypes=filetypes)
        return jsonify({'path': path})
    except Exception as e:
        return jsonify({'path': '', 'error': str(e)})


@app.route('/api/browse-music-source')
def api_browse_music_source():
    """Open dialog for the optional *source* document a .ly score was derived
    from — the original the user maintains the music in, kept only so the
    staleness check can flag "source newer than the imported score". It can be
    any notation program's native file (Harmony Assistant .myr, MuseScore
    .mscz, Sibelius .sib, Finale .musx…), so the filter is deliberately wide
    with « Tous les fichiers » as the real escape hatch."""
    filetypes = [
        ('Harmony Assistant', '*.myr'),
        ('MuseScore', '*.mscz'), ('MuseScore', '*.mscx'),
        ('Sibelius', '*.sib'),
        ('Finale', '*.musx'), ('Finale', '*.mus'),
        ('MusicXML', '*.musicxml'), ('MusicXML', '*.xml'), ('MusicXML', '*.mxl'),
        ('Tous les fichiers', '*.*'),
    ]
    try:
        path = _tk_dialog('file', filetypes=filetypes)
        return jsonify({'path': path})
    except Exception as e:
        return jsonify({'path': '', 'error': str(e)})


def _musicxml2ly_bin():
    """musicxml2ly ships in lilypond's bin dir — prefer the sibling of the
    configured lilypond so import and export drive the same version."""
    lp = load_settings().get('lilypond_path')
    if lp and os.path.isfile(lp):
        for name in ('musicxml2ly', 'musicxml2ly.py'):
            cand = os.path.join(os.path.dirname(lp), name)
            if os.path.isfile(cand):
                return cand
    return shutil.which('musicxml2ly')


@app.route('/api/myr-import', methods=['POST'])
def api_myr_import():
    """One-time MusicXML → .ly conversion for a myr item (TODO Phase 4).
    Deliberately explicit — never run on load, it overwrites hand-edits to
    the .ly (the frontend confirms before re-importing over an existing
    file)."""
    body = request.json or {}
    musicxml_path = body.get('musicxml_path') or ''
    ly_path = body.get('ly_path') or ''
    if not musicxml_path or not ly_path:
        return jsonify({'error': 'Chemin manquant'}), 400
    if not ly_path.lower().endswith('.ly'):
        ly_path += '.ly'
    bin_path = _musicxml2ly_bin()
    if not bin_path:
        return jsonify({'error': 'musicxml2ly introuvable — il accompagne '
                                 'LilyPond ; configurez lilypond dans les '
                                 'Paramètres'}), 400
    try:
        lyimport_module.import_musicxml(musicxml_path, ly_path, bin_path)
    except lyimport_module.LyImportError as e:
        return jsonify({'error': str(e)}), 400
    return jsonify({'ok': True, 'ly_path': ly_path})


# Rendered-preview cache for .ly items, keyed by content+staffsize hash:
# renderItemList re-renders cards on every edit, and even a *saved* file
# re-requested after an app-side cache miss shouldn't cost a 3–5 s
# lilypond run twice. In-memory only (process lifetime), small cap.
_LY_PREVIEW_CACHE = {}
_LY_PREVIEW_CACHE_MAX = 48


@app.route('/api/ly-preview', methods=['POST'])
def api_ly_preview():
    """Compile a .ly to a cropped PNG for the item card / edit panel
    (Phase 0 measured ~3–5 s warm — hence the cache and the frontend's
    queue). Accepts explicit content (unsaved editor buffer) or reads the
    linked file. Returns a data URI so no file-serving route is needed."""
    body = request.json or {}
    path = body.get('path') or ''
    content = body.get('content')
    if content is None:
        checked, err = _editable_linked_path(path)
        if err:
            return jsonify({'ok': False, 'error': err}), 400
        if not os.path.isfile(checked):
            return jsonify({'ok': False, 'error': 'Fichier introuvable : %s' % checked}), 404
        with open(checked, encoding='utf-8') as f:
            content = f.read()
    try:
        staffsize = float(body.get('staffsize') or 16)
    except (TypeError, ValueError):
        staffsize = 16.0
    # Resolved polyphony font (ly_font or, uniform default, the base text font)
    # so the preview's lyrics use the same family the export will — WYSIWYG.
    font = ((body.get('font') or '').strip()).replace('"', '')
    key = hashlib.sha1(('%g\n%s\n%s' % (staffsize, font, content)).encode('utf-8')).hexdigest()
    if key in _LY_PREVIEW_CACHE:
        return jsonify({'ok': True, 'png': _LY_PREVIEW_CACHE[key]})

    try:
        lilypond_bin = exporter_module._tool_path(load_settings(), 'lilypond_path', 'lilypond')
    except exporter_module.ExportError as e:
        return jsonify({'ok': False, 'error': str(e)}), 400
    env = exporter_module._compile_env(lilypond_bin, lilypond_bin, lilypond_bin)
    with tempfile.TemporaryDirectory(prefix='ly-preview-') as tmpdir:
        ly_file = os.path.join(tmpdir, 'apercu.ly')
        with open(ly_file, 'w', encoding='utf-8') as f:
            # Same knob lyluatex applies at export (staffsize option), so
            # the preview's proportions match the exported document.
            f.write('#(set-global-staff-size %g)\n' % staffsize)
            # Roman font for markup/lyrics, matching lyluatex's rmfamily= at
            # export. make-pango-font-tree sets only the *text* fonts (the
            # staff size stays set-global-staff-size's job); factor staffsize/20
            # keeps lyric size tracking the notes, as the default tree does.
            if font:
                f.write('\\paper { #(define fonts (make-pango-font-tree '
                        '"%s" "sans" "mono" (/ %g 20))) }\n' % (font, staffsize))
            f.write(content)
        cmd = [lilypond_bin, '-fpng', '-dcrop', '-dresolution=220',
               '-dno-point-and-click', '-o', os.path.join(tmpdir, 'apercu')]
        if path:
            cmd += ['-I', os.path.dirname(os.path.abspath(path))]
        cmd.append(ly_file)
        try:
            result = subprocess.run(cmd, cwd=tmpdir, env=env,
                                    capture_output=True, text=True, timeout=180)
        except subprocess.TimeoutExpired:
            return jsonify({'ok': False, 'error': 'Compilation interrompue après 180 s'})
        png_path = os.path.join(tmpdir, 'apercu.cropped.png')
        if not os.path.isfile(png_path):  # e.g. a piece with no music
            png_path = os.path.join(tmpdir, 'apercu.png')
        if result.returncode != 0 or not os.path.isfile(png_path):
            excerpt = lyimport_module._failure_excerpt(
                result.stdout + (result.stderr or ''))
            return jsonify({'ok': False, 'error': excerpt})
        with open(png_path, 'rb') as f:
            data_uri = 'data:image/png;base64,' + base64.b64encode(f.read()).decode('ascii')
    while len(_LY_PREVIEW_CACHE) >= _LY_PREVIEW_CACHE_MAX:
        _LY_PREVIEW_CACHE.pop(next(iter(_LY_PREVIEW_CACHE)))
    _LY_PREVIEW_CACHE[key] = data_uri
    return jsonify({'ok': True, 'png': data_uri})


@app.route('/api/browse-image')
def api_browse_image():
    """Open dialog for an image item (Phase 5b). Only the formats
    \\includegraphics reads natively — SVG/EPS are refused client-side
    with « exportez en PDF » (see TODO Phase 5b for the converter spike)."""
    filetypes = [
        ('Images (PDF, PNG, JPEG)', '*.pdf'),
        ('Images (PDF, PNG, JPEG)', '*.png'),
        ('Images (PDF, PNG, JPEG)', '*.jpg'),
        ('Images (PDF, PNG, JPEG)', '*.jpeg'),
        ('Tous les fichiers', '*.*'),
    ]
    try:
        path = _tk_dialog('file', filetypes=filetypes)
        return jsonify({'path': path})
    except Exception as e:
        return jsonify({'path': '', 'error': str(e)})


def _gs_bin():
    """ghostscript for PDF thumbnails: system gs, or the copy the official
    lilypond archive bundles in <bin>/../libexec (the same one the
    lyluatex export route already relies on)."""
    found = shutil.which('gs') or shutil.which('gswin64c')
    if found:
        return found
    lp = load_settings().get('lilypond_path')
    if lp and os.path.isfile(lp):
        cand = os.path.join(os.path.dirname(lp), '..', 'libexec', 'gs')
        if os.path.isfile(cand):
            return cand
    return None


_PDF_THUMB_CACHE = {}   # path -> (mtime, png bytes); small, process lifetime
_PDF_THUMB_CACHE_MAX = 24


@app.route('/api/image-preview')
def api_image_preview():
    """Serve a browser-displayable rendering of a linked image: PNG/JPEG
    raw, PDF rasterised to PNG (first page) via ghostscript."""
    path = request.args.get('path') or ''
    ext = os.path.splitext(path)[1].lower()
    if ext not in ('.png', '.jpg', '.jpeg', '.pdf'):
        return jsonify({'error': 'Format non affichable : %s' % (ext or '(aucun)')}), 400
    if not os.path.isfile(path):
        return jsonify({'error': 'Fichier introuvable : %s' % path}), 404
    if ext != '.pdf':
        return send_file(path)
    mtime = os.path.getmtime(path)
    cached = _PDF_THUMB_CACHE.get(path)
    if cached and cached[0] == mtime:
        return Response(cached[1], mimetype='image/png')
    gs = _gs_bin()
    if not gs:
        return jsonify({'error': 'ghostscript introuvable — aperçu PDF indisponible'}), 503
    with tempfile.TemporaryDirectory(prefix='img-preview-') as tmpdir:
        out = os.path.join(tmpdir, 'apercu.png')
        try:
            r = subprocess.run(
                [gs, '-q', '-dNOPAUSE', '-dBATCH', '-dSAFER', '-sDEVICE=png16m',
                 '-r120', '-dFirstPage=1', '-dLastPage=1', '-o', out, path],
                capture_output=True, text=True, timeout=60)
        except subprocess.TimeoutExpired:
            return jsonify({'error': 'Rendu PDF interrompu après 60 s'}), 500
        if r.returncode != 0 or not os.path.isfile(out):
            return jsonify({'error': 'Rendu PDF impossible : %s'
                            % (r.stderr or r.stdout or '?').strip()[:300]}), 500
        with open(out, 'rb') as f:
            png = f.read()
    while len(_PDF_THUMB_CACHE) >= _PDF_THUMB_CACHE_MAX:
        _PDF_THUMB_CACHE.pop(next(iter(_PDF_THUMB_CACHE)))
    _PDF_THUMB_CACHE[path] = (mtime, png)
    return Response(png, mimetype='image/png')


# Music/symbol families LilyPond lists but which are never a text choice —
# dropped from the picker so the list is fonts a user would actually set.
_NON_TEXT_FONT_RE = re.compile(
    r'^(Emmentaler|Standard Symbols|.*Math$|D0500|C059$|Noto Music)', re.IGNORECASE)
_FONT_LIST_CACHE = None   # [family names]; enumeration is slow, cache for life


@app.route('/api/available-fonts')
def api_available_fonts():
    """Text-font families installed on THIS system, for the typography picker.
    Enumerated via `lilypond -dshow-available-fonts` because Pango/fontconfig
    is the *stricter* of the two engines the export drives — a system family
    on this list is also visible to luaotfload (TeX side) by the same name,
    so what the user picks here will resolve in both. Slow (~seconds), so the
    result is cached for the process lifetime."""
    global _FONT_LIST_CACHE
    if _FONT_LIST_CACHE is not None:
        return jsonify({'fonts': _FONT_LIST_CACHE})
    try:
        lilypond_bin = exporter_module._tool_path(load_settings(), 'lilypond_path', 'lilypond')
    except exporter_module.ExportError as e:
        return jsonify({'fonts': [], 'error': str(e)}), 200
    try:
        r = subprocess.run([lilypond_bin, '-dshow-available-fonts', 'x'],
                           env=exporter_module._font_env(),
                           capture_output=True, text=True, timeout=60)
    except (subprocess.TimeoutExpired, OSError) as e:
        return jsonify({'fonts': [], 'error': str(e)}), 200
    fonts = set()
    for line in (r.stdout + r.stderr).splitlines():
        # Family header lines look like: "family Latin Modern Roman".
        if line.startswith('family '):
            name = line[len('family '):].strip()
            if name and not _NON_TEXT_FONT_RE.match(name):
                fonts.add(name)
    _FONT_LIST_CACHE = sorted(fonts, key=str.lower)
    return jsonify({'fonts': _FONT_LIST_CACHE})


_FONT_VALID_CACHE = {}   # font name -> bool; small, process lifetime
_LUAOTFLOAD_UPDATED = False   # DB refreshed this process? (guards a costly rebuild)


def _font_loads(lualatex, font):
    """True if `font` resolves by name in luaotfload — a real fontspec-only
    compile (no gregorio/lilypond, ~1–2 s). Raises on toolchain failure so the
    caller can report it rather than mistake it for an unloadable font."""
    # Brace the name so spaces are safe; fontspec reports "cannot be found".
    doc = ('\\documentclass{article}\\usepackage{fontspec}\n'
           '\\setmainfont{%s}\n\\begin{document}Aa\\end{document}\n' % font)
    with tempfile.TemporaryDirectory(prefix='font-check-') as d:
        with open(os.path.join(d, 'f.tex'), 'w', encoding='utf-8') as f:
            f.write(doc)
        r = subprocess.run(
            [lualatex, '--interaction=nonstopmode', 'f.tex'],
            cwd=d, env=exporter_module._font_env(), capture_output=True,
            text=True, timeout=120)
        log = r.stdout + (r.stderr or '')
    # fontspec's own error strings are the ground truth for "can't load this".
    return 'cannot be found' not in log and 'not loadable' not in log


def _luaotfload_refresh(force=False):
    """Rebuild luaotfload's font-name database, at most once per process (unless
    `force`, for an explicit user rescan).

    luaotfload resolves \\setmainfont{Name} against its own cached name index,
    NOT the OS — so a font that is on disk (and visible to fontconfig/Pango)
    can still be unknown to luaotfload until that index is rebuilt. This is the
    stale-cache miss we hit with TeX Gyre Schola. Returns True only on the call
    that actually ran the rebuild, so an auto-heal caller retries a failed load
    exactly once against the fresh DB; later misses in the same process skip it
    (the DB is already current) and fail fast. `force` always rebuilds."""
    global _LUAOTFLOAD_UPDATED
    if _LUAOTFLOAD_UPDATED and not force:
        return False
    _LUAOTFLOAD_UPDATED = True
    # --force does a full rebuild (thorough, for an explicit user rescan); the
    # auto-heal path uses a plain incremental --update. OSFONTDIR is carried in
    # so the bundled fonts are indexed alongside the system ones.
    cmd = ['luaotfload-tool', '--update'] + (['--force'] if force else [])
    try:
        subprocess.run(cmd, env=exporter_module._font_env(),
                       capture_output=True, text=True, timeout=180)
    except (OSError, subprocess.TimeoutExpired):
        return False   # tool absent/hung: nothing gained, don't retry
    return True


def _font_resolves(lualatex, font):
    """Cached: does `font` load in luaotfload? On a first miss for a family the
    OS list contains (i.e. genuinely on disk → stale-DB signature, not a typo),
    refresh luaotfload's index once and retry. May raise (TimeoutExpired/OSError)
    from the underlying compile — callers decide how to report it."""
    if font in _FONT_VALID_CACHE:
        return _FONT_VALID_CACHE[font]
    ok = _font_loads(lualatex, font)
    # Gated on the enumerated list so genuine typos, which no engine knows,
    # never trigger a costly rebuild. The picker fills that cache before the
    # user can pick, so in real use it is warm.
    if (not ok and _FONT_LIST_CACHE is not None and font in _FONT_LIST_CACHE
            and _luaotfload_refresh()):
        ok = _font_loads(lualatex, font)
    _FONT_VALID_CACHE[font] = ok
    return ok


def _log_has_font_miss(log):
    """A compile log whose failure is a font luaotfload couldn't find by name
    (fontspec's own wording) — the stale-index signature worth one refresh."""
    return 'cannot be found' in (log or '') or 'not loadable' in (log or '')


def _referenced_fonts(data):
    """Distinct non-empty families a project actually requests: the base text
    font, plus the gabc/ly overrides. Empty overrides inherit text_font (or the
    engine default) and so need no separate check."""
    typo = (data or {}).get('defs', {}).get('typography', {})
    seen, out = set(), []
    for key in ('text_font', 'gabc_font', 'ly_font'):
        f = (typo.get(key) or '').strip()
        if f and f not in seen:
            seen.add(f)
            out.append(f)
    return out


def _missing_fonts(fonts):
    """Of `fonts`, those luaotfload cannot load on this machine (so the export
    would fail on them). A toolchain error is treated as 'not missing' — better
    to let the real export surface it than to false-alarm the user."""
    if not fonts:
        return []
    try:
        lualatex = exporter_module._tool_path(load_settings(), 'lualatex_path', 'lualatex')
    except exporter_module.ExportError:
        return []
    missing = []
    for f in fonts:
        try:
            if not _font_resolves(lualatex, f):
                missing.append(f)
        except (subprocess.TimeoutExpired, OSError):
            pass
    return missing


@app.route('/api/validate-font', methods=['POST'])
def api_validate_font():
    """Confirm a chosen family actually loads in the *TeX* engine (fontspec /
    luaotfload) — the stricter side for texmf-adjacent fonts, where a family
    Pango lists can still be unknown to luaotfload by that name. So a
    non-technical user learns at pick time, not as a cryptic error at export.
    Empty font = engine default = always valid."""
    font = ((request.json or {}).get('font') or '').strip()
    if not font:
        return jsonify({'ok': True})
    try:
        lualatex = exporter_module._tool_path(load_settings(), 'lualatex_path', 'lualatex')
    except exporter_module.ExportError as e:
        return jsonify({'ok': False, 'error': str(e)}), 200
    try:
        ok = _font_resolves(lualatex, font)
    except (subprocess.TimeoutExpired, OSError) as e:
        return jsonify({'ok': False, 'error': str(e)}), 200
    return jsonify({'ok': ok})


@app.route('/api/check-fonts', methods=['POST'])
def api_check_fonts():
    """Preflight: which of a project's referenced families are unloadable on
    THIS machine — for the load-time warning banner (a .breviaire made where a
    font was installed, opened where it isn't). Returns {missing:[names]}."""
    return jsonify({'missing': _missing_fonts(_referenced_fonts(request.json or {}))})


@app.route('/api/fonts/rescan', methods=['POST'])
def api_fonts_rescan():
    """Force a fresh font scan so a family installed mid-session appears without
    an app restart: rebuild luaotfload's index AND drop our process-lifetime
    caches (`_FONT_LIST_CACHE`/`_FONT_VALID_CACHE`), then re-enumerate. Returns
    the refreshed picker list (same shape as /api/available-fonts)."""
    global _FONT_LIST_CACHE
    _luaotfload_refresh(force=True)
    _FONT_LIST_CACHE = None
    _FONT_VALID_CACHE.clear()
    return api_available_fonts()


@app.route('/api/browse-folder')
def api_browse():
    try:
        return jsonify({'path': _tk_dialog('folder')})
    except Exception as e:
        return jsonify({'path': '', 'error': str(e)})


@app.route('/api/export', methods=['POST'])
def api_export():
    """One-file export + compile of a .breviaire project (TODO Phase 6).
    Saves the project first server-side so what's exported is what's on disk.

    Guards a hand-edited export: if the .tex a re-export would overwrite has
    been modified since we last wrote it, return needs_confirm instead of
    exporting; the client re-calls with force=true once the user agrees. A
    prior export whose .tex has since vanished is reported as a notice, not a
    block (nothing to clobber)."""
    body = request.json or {}
    path = body.get('path')
    data = body.get('data')
    force = bool(body.get('force'))
    if not path:
        return jsonify({'error': 'Chemin manquant'}), 400
    try:
        project_module.save_project(path, data)
    except project_module.ProjectError as e:
        return jsonify({'error': str(e)}), 400
    settings = load_settings()
    template_path = USER_EXPORT_TEMPLATE if os.path.isfile(USER_EXPORT_TEMPLATE) else None

    state = exporter_module.export_state(data, path, settings)
    prior = state.get('prior')
    if not force and prior and prior['state'] == 'modified':
        return jsonify({'needs_confirm': True, 'reason': 'modified',
                        'tex_path': prior['tex_path'],
                        'exported_at': prior.get('exported_at'),
                        'modified_at': prior.get('modified_at')})
    # Re-warn (even if dismissed at load) before spending a compile on a font
    # this machine can't load — the user confirms and re-calls with force=true.
    if not force:
        missing = _missing_fonts(_referenced_fonts(data))
        if missing:
            return jsonify({'needs_confirm': True, 'reason': 'fonts_missing',
                            'fonts': missing})
    # A missing prior export is surfaced to the user but never blocks.
    notice = None
    if prior and prior['state'] == 'missing':
        notice = {'reason': 'missing', 'tex_path': prior['tex_path']}

    try:
        result = exporter_module.export_project(path, data, settings,
                                                template_path=template_path)
        # Export-time auto-heal: a font set outside the picker (hand-edited
        # JSON, or a project exported without opening the defs editor) can be
        # on disk yet missing from luaotfload's stale index. On a font-not-found
        # failure, rebuild the index once and retry — same stale-cache cure as
        # validate-font, for the path that bypasses it. (Does NOT conjure a
        # genuinely-absent font; the preflight guard above catches those.)
        if (not result.get('success')
                and _log_has_font_miss(result.get('log', ''))
                and _luaotfload_refresh()):
            result = exporter_module.export_project(path, data, settings,
                                                    template_path=template_path)
    except exporter_module.ExportError as e:
        return jsonify({'success': False, 'error': str(e), 'log': str(e)}), 200

    # Persist the new export stamp into the project so the next re-export can
    # detect a hand-edited .tex. Best-effort: a failure here mustn't sink an
    # otherwise-successful export.
    if result.get('last_export'):
        data['last_export'] = result['last_export']
        try:
            project_module.save_project(path, data)
        except project_module.ProjectError:
            pass
    if notice:
        result['notice'] = notice
    return jsonify(result)


@app.route('/api/export-template')
def api_get_export_template():
    is_custom = os.path.isfile(USER_EXPORT_TEMPLATE)
    path = USER_EXPORT_TEMPLATE if is_custom else BUNDLED_EXPORT_TEMPLATE
    with open(path, encoding='utf-8') as f:
        return jsonify({'content': f.read(), 'is_custom': is_custom})


@app.route('/api/export-template', methods=['POST'])
def api_save_export_template():
    """Static-verify before accepting the save (TODO Phase 2 verifier): a
    template that can't even render against a synthetic project would break
    every future export, so it is rejected here with the Jinja error
    (including line number) instead of being written."""
    content = (request.json or {}).get('content', '')
    errors = verifier_module.check_template(content)
    if errors:
        return jsonify({'ok': False, 'errors': errors}), 400
    with open(USER_EXPORT_TEMPLATE, 'w', encoding='utf-8') as f:
        f.write(content)
    return jsonify({'ok': True})


@app.route('/api/export-template/verify', methods=['POST'])
def api_verify_export_template():
    """Real-compile check of the (possibly unsaved) editor content: runs a
    synthetic mini-project through the full export pipeline in a temp dir."""
    content = (request.json or {}).get('content', '')
    settings = load_settings()
    result = verifier_module.verify_compile(content, settings)
    return jsonify(result)


@app.route('/api/export-template/reset', methods=['POST'])
def api_reset_export_template():
    try:
        os.remove(USER_EXPORT_TEMPLATE)
    except FileNotFoundError:
        pass
    return jsonify({'ok': True})


def _open_with_os_default(path):
    if sys.platform == 'win32':
        os.startfile(path)
    elif sys.platform == 'darwin':
        subprocess.Popen(['open', path])
    else:
        subprocess.Popen(['xdg-open', path])


@app.route('/api/open-external', methods=['POST'])
def api_open_external():
    """Opens an absolute path with the OS default app — for composer items
    (gabc/myr/ly), which reference files anywhere on disk, not relative to
    a gabc_root."""
    path = (request.json or {}).get('path', '')
    if not path or not os.path.isfile(path):
        return jsonify({'error': 'Not found'}), 404
    try:
        _open_with_os_default(path)
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/detect-lualatex')
def api_detect_lualatex():
    path = shutil.which('lualatex')
    return jsonify({'path': path or '', 'found': path is not None})


@app.route('/api/version')
def api_version():
    return jsonify({'version': VERSION})


@app.route('/api/check-update')
def api_check_update():
    v = _update_version
    return jsonify({
        'available': v is not None,
        'current_version': VERSION,
        'latest_version': v,
    })


@app.route('/api/apply-update', methods=['POST'])
def api_apply_update():
    if not getattr(sys, 'frozen', False):
        return jsonify({'error': 'Updates only apply to the packaged app'}), 400
    info = _update_info
    if not info:
        return jsonify({'error': 'No update available'}), 400
    try:
        mgr = _make_update_manager()
        if mgr is None:
            return jsonify({'error': 'Not running from a Velopack install'}), 400
        mgr.download_updates(info)
        mgr.apply_updates_and_restart(info)
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/heartbeat', methods=['POST'])
def api_heartbeat():
    global _last_heartbeat
    _last_heartbeat = time.time()
    return jsonify({'ok': True})


PORT = 5173


def open_browser():
    time.sleep(1.2)
    webbrowser.open(f'http://127.0.0.1:{PORT}')


if __name__ == '__main__':
    if getattr(sys, 'frozen', False) and sys.platform == 'darwin':
        # console=False means all stderr is swallowed; redirect to a log file so
        # startup crashes are diagnosable (~/.atelier-gabc/startup.log).
        try:
            _log_path = os.path.join(_USER_DIR, 'startup.log')
            _log_f = open(_log_path, 'w', buffering=1, encoding='utf-8')
            sys.stdout = _log_f
            sys.stderr = _log_f
        except Exception:
            pass

    if getattr(sys, 'frozen', False):
        # Velopack calls this binary with --veloapp-* args for lifecycle hooks.
        # Must exit quickly without starting Flask or opening a browser.
        _is_hook = any(a.startswith('--veloapp') for a in sys.argv[1:])

        try:
            from velopack import VelopackApp
            VelopackApp.build().run()
        except SystemExit:
            pass  # SDK may call sys.exit() — always override with 0 below
        except Exception:
            pass

        if _is_hook:
            sys.exit(0)

        threading.Thread(target=_check_for_updates_bg, daemon=True).start()

    threading.Thread(target=_heartbeat_monitor, daemon=True).start()
    threading.Thread(target=open_browser, daemon=True).start()
    app.run(host='127.0.0.1', port=PORT, debug=False, threaded=True)
