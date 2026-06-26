from flask import Flask, request, jsonify, send_file
import os, json, subprocess, shutil, sys, threading, webbrowser, time, hashlib, re
import jinja2

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

USER_TEMPLATE_FILE = os.path.join(_USER_DIR, 'score.tex.j2')

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

DEFAULTS_DIR = os.path.join(BASE_DIR, 'defaults')
DEFAULT_CONFIG_FILE = os.path.join(DEFAULTS_DIR, 'configuration.tex')
BUNDLED_TEMPLATE = os.path.join(BASE_DIR, 'templates', 'score.tex.j2')


def _read_template() -> str:
    path = USER_TEMPLATE_FILE if os.path.isfile(USER_TEMPLATE_FILE) else BUNDLED_TEMPLATE
    with open(path, encoding='utf-8') as f:
        return f.read()


def load_settings():
    if os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE, encoding='utf-8') as f:
            return json.load(f)
    return {}


def save_settings_data(data):
    with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)


def safe_join(root, rel):
    root_real = os.path.realpath(root)
    full = os.path.realpath(os.path.join(root, rel.replace('/', os.sep).replace('\\', os.sep)))
    if full != root_real and not full.startswith(root_real + os.sep):
        raise ValueError('Path outside root')
    return full


def _tk_dialog(kind, **kwargs):
    import tkinter as tk
    from tkinter import filedialog
    root_tk = tk.Tk()
    root_tk.withdraw()
    root_tk.attributes('-topmost', True)
    try:
        if kind == 'file':
            return filedialog.askopenfilename(**kwargs) or ''
        return filedialog.askdirectory(**kwargs) or ''
    finally:
        root_tk.destroy()


@app.route('/')
def index():
    return app.send_static_file('index.html')


@app.route('/api/settings', methods=['GET'])
def api_get_settings():
    s = load_settings()
    s['_default_config_file'] = DEFAULT_CONFIG_FILE
    return jsonify(s)


@app.route('/api/settings', methods=['POST'])
def api_post_settings():
    save_settings_data(request.json)
    return jsonify({'ok': True})


@app.route('/api/browse-file')
def api_browse_file():
    try:
        path = _tk_dialog('file', filetypes=[('Fichiers TeX', '*.tex'), ('Tous les fichiers', '*.*')])
        return jsonify({'path': path})
    except Exception as e:
        return jsonify({'path': '', 'error': str(e)})


@app.route('/api/browse-folder')
def api_browse():
    try:
        return jsonify({'path': _tk_dialog('folder')})
    except Exception as e:
        return jsonify({'path': '', 'error': str(e)})


@app.route('/api/tree')
def api_tree():
    settings = load_settings()
    root = settings.get('gabc_root', '')
    output_dir = settings.get('output_folder') or None
    if not root or not os.path.isdir(root):
        return jsonify([])

    def build(path, rel=''):
        items = []
        try:
            entries = sorted(os.scandir(path), key=lambda e: (not e.is_dir(), e.name.lower()))
        except PermissionError:
            return items
        for e in entries:
            if e.name.startswith('.') or e.name == 'tmp-gre':
                continue
            r = (rel + '/' + e.name) if rel else e.name
            if e.is_dir():
                children = build(e.path, r)
                if children:
                    items.append({'name': e.name, 'type': 'folder', 'path': r, 'children': children})
            elif e.name.endswith('.gabc'):
                gabc_dir = os.path.dirname(e.path)
                gabc_name = os.path.splitext(e.name)[0]
                out = output_dir or gabc_dir
                pdf_abs = os.path.join(out, gabc_name + '.pdf')
                pdf_rel = os.path.relpath(pdf_abs, root).replace('\\', '/') if os.path.isfile(pdf_abs) else None
                items.append({'name': e.name, 'type': 'file', 'path': r, 'pdf': pdf_rel})
        return items

    return jsonify(build(root))


@app.route('/api/file')
def api_get_file():
    settings = load_settings()
    root = settings.get('gabc_root', '')
    try:
        path = safe_join(root, request.args.get('path', ''))
    except ValueError:
        return jsonify({'error': 'Invalid path'}), 400
    if not os.path.isfile(path):
        return jsonify({'error': 'Not found'}), 404
    with open(path, encoding='utf-8') as f:
        content = f.read()

    # Check whether a PDF with the same basename exists and is newer than the gabc
    gabc_dir = os.path.dirname(path)
    gabc_name = os.path.splitext(os.path.basename(path))[0]
    output_dir = settings.get('output_folder') or gabc_dir
    pdf_abs = os.path.join(output_dir, gabc_name + '.pdf')
    pdf_info = None
    if os.path.isfile(pdf_abs):
        try:
            if os.path.getmtime(pdf_abs) > os.path.getmtime(path):
                pdf_info = {'rel': os.path.relpath(pdf_abs, root).replace('\\', '/')}
        except OSError:
            pass

    return jsonify({'content': content, 'pdf': pdf_info})


@app.route('/api/save', methods=['POST'])
def api_save():
    settings = load_settings()
    root = settings.get('gabc_root', '')
    data = request.json
    try:
        path = safe_join(root, data.get('path', ''))
    except ValueError:
        return jsonify({'error': 'Invalid path'}), 400
    with open(path, 'w', encoding='utf-8') as f:
        f.write(data.get('content', ''))
    return jsonify({'ok': True})


def _gregoriotex_version_suffix():
    """Return the gtex version suffix that gregoriotex uses (e.g. '6_1_0')."""
    try:
        kpw = shutil.which('kpsewhich')
        lua_path = None
        if kpw:
            r = subprocess.run([kpw, 'gregoriotex.lua'],
                               capture_output=True, text=True, timeout=10)
            lua_path = r.stdout.strip() or None
        if not lua_path:
            for candidate in (
                '/usr/local/texlive/2025/texmf-dist/tex/luatex/gregoriotex/gregoriotex.lua',
                '/usr/local/texlive/2024/texmf-dist/tex/luatex/gregoriotex/gregoriotex.lua',
                '/Library/TeX/Root/texmf-dist/tex/luatex/gregoriotex/gregoriotex.lua',
                'c:/texlive/2025/texmf-dist/tex/luatex/gregoriotex/gregoriotex.lua',
                'c:/texlive/2024/texmf-dist/tex/luatex/gregoriotex/gregoriotex.lua',
            ):
                if os.path.isfile(candidate):
                    lua_path = candidate
                    break
        if lua_path and os.path.isfile(lua_path):
            with open(lua_path, encoding='utf-8') as f:
                for line in f:
                    m = re.match(r"local internalversion\s*=\s*'([^']+)'", line)
                    if m:
                        return m.group(1).replace('.', '_')
    except Exception:
        pass
    return '6_1_0'


def _precompile_gabc(gregorio_bin, gabc_abs, gabc_dir, gabc_name):
    """Run gregorio to produce the versioned .gtex that \\gregorioscore[a] expects.

    If the versioned gtex already exists and is newer than the gabc,
    gregoriotex [a] mode skips calling gregorio itself (which fails in
    restricted shell mode due to kpathsea restrictions).

    kpathsea (used by gregorio) also restricts writes to absolute paths,
    so we use relative paths with cwd=gabc_dir.
    """
    suffix = _gregoriotex_version_suffix()
    tmp_gre = os.path.join(gabc_dir, 'tmp-gre')
    os.makedirs(tmp_gre, exist_ok=True)
    gabc_rel = os.path.basename(gabc_abs)
    gtex_rel = os.path.join('tmp-gre', f'{gabc_name}-{suffix}.gtex')
    r = subprocess.run(
        [gregorio_bin, '-o', gtex_rel, gabc_rel],
        cwd=gabc_dir, capture_output=True, text=True, timeout=30
    )
    return r


_RERUN_RE = re.compile(
    r'Rerun to get|rerunfilecheck|Label\(s\) may have changed',
    re.IGNORECASE,
)

def _gaux_hash(path):
    try:
        with open(path, 'rb') as f:
            return hashlib.md5(f.read()).hexdigest()
    except OSError:
        return None


def _compile_file(settings, rel_path):
    root = settings.get('gabc_root', '')
    config_file = settings.get('config_file') or DEFAULT_CONFIG_FILE
    config_folder = os.path.dirname(config_file)
    lualatex_bin = settings.get('lualatex_path') or shutil.which('lualatex') or 'lualatex'
    gregorio_bin = settings.get('gregorio_path') or shutil.which('gregorio') or 'gregorio'

    try:
        gabc_abs = safe_join(root, rel_path)
    except ValueError:
        return {'success': False, 'log': 'Invalid path', 'returncode': -1, 'pdf_rel': None}

    gabc_dir = os.path.dirname(gabc_abs)
    gabc_name = os.path.splitext(os.path.basename(gabc_abs))[0]

    # Create the .tex wrapper only when it doesn't already exist so users can
    # customise it per-file without having it overwritten on each compile.
    tex_file = os.path.join(gabc_dir, f'{gabc_name}.tex')
    if not os.path.isfile(tex_file):
        jenv = jinja2.Environment(
            variable_start_string='<<',
            variable_end_string='>>',
            block_start_string='<%',
            block_end_string='%>',
            loader=jinja2.BaseLoader(),
        )
        tmpl = jenv.from_string(_read_template())
        tex = tmpl.render(
            config_file=config_file.replace('\\', '/') if config_file else '',
            gabc_name=gabc_name,
        )
        with open(tex_file, 'w', encoding='utf-8') as f:
            f.write(tex)

    output_dir = settings.get('output_folder') or gabc_dir

    sep = ';' if sys.platform == 'win32' else ':'
    env = os.environ.copy()
    env['TEXINPUTS'] = config_folder + sep + gabc_dir + sep + env.get('TEXINPUTS', '')

    log_lines = []

    gr = _precompile_gabc(gregorio_bin, gabc_abs, gabc_dir, gabc_name)
    if gr.returncode != 0:
        log_lines.append(f'[gregorio pre-compile warning]\n{gr.stdout}{gr.stderr}\n')

    gaux_path = os.path.join(gabc_dir, f'{gabc_name}.gaux')
    prev_gaux_hash = _gaux_hash(gaux_path)
    returncode = -1

    try:
        for run in range(1, 6):
            result = subprocess.run(
                [lualatex_bin,
                 '--interaction=nonstopmode',
                 f'--jobname={gabc_name}',
                 '--output-directory', output_dir,
                 tex_file],
                cwd=gabc_dir,
                env=env,
                capture_output=True,
                text=True,
                timeout=120
            )
            returncode = result.returncode
            run_log = result.stdout + (result.stderr or '')
            log_lines.append(f'--- Pass {run} ---\n{run_log}' if run > 1 else run_log)

            curr_gaux_hash = _gaux_hash(gaux_path)
            needs_rerun = bool(_RERUN_RE.search(run_log)) or (curr_gaux_hash != prev_gaux_hash)
            prev_gaux_hash = curr_gaux_hash

            if not needs_rerun or run == 5:
                break

        pdf_path = os.path.join(output_dir, f'{gabc_name}.pdf')
        pdf_rel = os.path.relpath(pdf_path, root).replace('\\', '/') if os.path.isfile(pdf_path) else None
        return {
            'success': pdf_rel is not None,
            'pdf_rel': pdf_rel,
            'log': '\n'.join(log_lines),
            'returncode': returncode,
        }
    except FileNotFoundError:
        return {'success': False, 'log': f'lualatex introuvable : {lualatex_bin}', 'returncode': -1, 'pdf_rel': None}
    except subprocess.TimeoutExpired:
        return {'success': False, 'log': 'Compilation timed out after 120 seconds.', 'returncode': -1, 'pdf_rel': None}
    finally:
        for ext in ('.aux', '.log', '.gaux', '.gtmp', '.gsniplog'):
            for d in (output_dir, gabc_dir):
                f = os.path.join(d, gabc_name + ext)
                try:
                    if os.path.exists(f):
                        os.remove(f)
                except Exception:
                    pass
        tmp_gre = os.path.join(gabc_dir, 'tmp-gre')
        try:
            shutil.rmtree(tmp_gre, ignore_errors=True)
        except Exception:
            pass


@app.route('/api/compile', methods=['POST'])
def api_compile():
    settings = load_settings()
    data = request.json
    result = _compile_file(settings, data.get('path', ''))
    if result.get('log') == 'Invalid path' and result.get('returncode') == -1:
        return jsonify({'error': 'Invalid path'}), 400
    return jsonify(result)


@app.route('/api/compile-all')
def api_compile_all():
    from flask import Response, stream_with_context

    def generate():
        settings = load_settings()
        root = settings.get('gabc_root', '')
        if not root or not os.path.isdir(root):
            yield f"data: {json.dumps({'error': 'Aucun dossier GABC configuré'})}\n\n"
            return

        gabc_files = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames if not d.startswith('.') and d != 'tmp-gre')
            for filename in sorted(filenames):
                if filename.endswith('.gabc'):
                    rel = os.path.relpath(os.path.join(dirpath, filename), root).replace('\\', '/')
                    gabc_files.append(rel)

        total = len(gabc_files)
        yield f"data: {json.dumps({'total': total})}\n\n"

        succeeded = 0
        for i, rel_path in enumerate(gabc_files):
            result = _compile_file(settings, rel_path)
            if result['success']:
                succeeded += 1
            yield f"data: {json.dumps({'index': i + 1, 'total': total, 'path': rel_path, 'success': result['success']})}\n\n"

        yield f"data: {json.dumps({'done': True, 'total': total, 'succeeded': succeeded})}\n\n"

    resp = Response(stream_with_context(generate()), mimetype='text/event-stream')
    resp.headers['Cache-Control'] = 'no-cache'
    resp.headers['X-Accel-Buffering'] = 'no'
    return resp


@app.route('/api/pdf')
def api_serve_pdf():
    settings = load_settings()
    root = settings.get('gabc_root', '')
    rel = request.args.get('path', '')
    try:
        path = safe_join(root, rel)
    except ValueError:
        return jsonify({'error': 'Invalid path'}), 400
    if not os.path.isfile(path) or not path.lower().endswith('.pdf'):
        return jsonify({'error': 'Not found'}), 404
    return send_file(path, mimetype='application/pdf', as_attachment=False)


@app.route('/api/move', methods=['POST'])
def api_move():
    settings = load_settings()
    root = settings.get('gabc_root', '')
    data = request.json
    src_rel = data.get('src', '')
    dst_folder_rel = data.get('dst_folder', '')

    try:
        src_abs = safe_join(root, src_rel)
    except ValueError:
        return jsonify({'error': 'Invalid source path'}), 400

    if dst_folder_rel:
        try:
            dst_folder_abs = safe_join(root, dst_folder_rel)
        except ValueError:
            return jsonify({'error': 'Invalid destination path'}), 400
    else:
        dst_folder_abs = root

    if not os.path.isfile(src_abs):
        return jsonify({'error': 'Source introuvable'}), 404
    if not os.path.isdir(dst_folder_abs):
        return jsonify({'error': 'Dossier de destination introuvable'}), 404

    filename = os.path.basename(src_abs)
    dst_abs = os.path.join(dst_folder_abs, filename)

    if src_abs == dst_abs:
        return jsonify({'ok': True, 'new_path': src_rel})
    if os.path.exists(dst_abs):
        return jsonify({'error': f'« {filename} » existe déjà dans ce dossier'}), 409

    shutil.move(src_abs, dst_abs)
    new_rel = os.path.relpath(dst_abs, root).replace('\\', '/')
    return jsonify({'ok': True, 'new_path': new_rel})


@app.route('/api/open-pdf', methods=['POST'])
def api_open_pdf():
    settings = load_settings()
    root = settings.get('gabc_root', '')
    rel = request.json.get('path', '')
    try:
        path = safe_join(root, rel)
    except ValueError:
        return jsonify({'error': 'Invalid path'}), 400
    if not os.path.isfile(path):
        return jsonify({'error': 'Not found'}), 404
    try:
        if sys.platform == 'win32':
            os.startfile(path)
        elif sys.platform == 'darwin':
            subprocess.Popen(['open', path])
        else:
            subprocess.Popen(['xdg-open', path])
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/open-folder', methods=['POST'])
def api_open_folder():
    settings = load_settings()
    root = settings.get('gabc_root', '')
    rel = request.json.get('rel', '')
    if rel:
        try:
            folder = safe_join(root, rel)
        except ValueError:
            return jsonify({'error': 'Invalid path'}), 400
        if not os.path.isdir(folder):
            folder = os.path.dirname(folder)
    else:
        folder = root
    if not folder or not os.path.isdir(folder):
        return jsonify({'error': 'Not a directory'}), 400
    try:
        if sys.platform == 'win32':
            subprocess.Popen(['explorer', os.path.normpath(folder)])
        elif sys.platform == 'darwin':
            subprocess.Popen(['open', folder])
        else:
            subprocess.Popen(['xdg-open', folder])
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


@app.route('/api/template')
def api_get_template():
    is_custom = os.path.isfile(USER_TEMPLATE_FILE)
    try:
        content = _read_template()
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    return jsonify({'content': content, 'is_custom': is_custom})


@app.route('/api/template', methods=['POST'])
def api_save_template():
    content = request.json.get('content', '')
    with open(USER_TEMPLATE_FILE, 'w', encoding='utf-8') as f:
        f.write(content)
    return jsonify({'ok': True})


@app.route('/api/template/reset', methods=['POST'])
def api_reset_template():
    try:
        os.remove(USER_TEMPLATE_FILE)
    except FileNotFoundError:
        pass
    return jsonify({'ok': True})


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
