"""Flask endpoint behaviour (app/app.py) through the test client, with
settings isolated per test (see base.AppTestCase). Native-dialog routes
are not tested — they block on a real picker."""

import json
import os
import unittest

from .base import AppTestCase, FIXTURES, GABC_STUB, PNG_STUB, SLOW, has


class TestProjectEndpoints(AppTestCase):
    def test_new_save_reload_roundtrip(self):
        path = os.path.join(self.tmp, 'x.breviaire')
        r = self.client.post('/api/project/new', json={'path': path})
        self.assertEqual(r.status_code, 200)
        data = r.get_json()['data']
        data['items'].append({'type': 'text', 'key': 'body', 'contents': ['é']})
        r = self.client.post('/api/project', json={'path': path, 'data': data})
        self.assertTrue(r.get_json()['ok'])
        r = self.client.get('/api/project?path=' + path)
        j = r.get_json()
        self.assertEqual(j['data']['items'][0]['contents'], ['é'])
        self.assertEqual(j['item_status'], [{'missing': []}])

    def test_save_returns_style_warnings_but_saves(self):
        path = os.path.join(self.tmp, 'x.breviaire')
        data = self.client.post('/api/project/new', json={'path': path}).get_json()['data']
        data['defs']['styles']['casse'] = '{#1'
        r = self.client.post('/api/project', json={'path': path, 'data': data})
        j = r.get_json()
        self.assertTrue(j['ok'])
        self.assertTrue(any('casse' in w for w in j['style_warnings']))

    def test_last_project_tracked(self):
        path = os.path.join(self.tmp, 'x.breviaire')
        self.client.post('/api/project/new', json={'path': path})
        j = self.client.get('/api/project/last').get_json()
        self.assertEqual(j['path'], path)
        self.assertTrue(j['exists'])

    def test_get_missing_project_404(self):
        r = self.client.get('/api/project?path=/nulle/part.breviaire')
        self.assertEqual(r.status_code, 404)


class TestStylesCheck(AppTestCase):
    def test_issues_keyed_per_style(self):
        r = self.client.post('/api/styles/check', json={
            'styles': {'ok': '{#1}', 'trou': '{#1 #3}', 'ouvert': '{#1'}})
        issues = r.get_json()['issues']
        self.assertNotIn('ok', issues)
        self.assertIn('trou', issues)
        self.assertIn('ouvert', issues)


class TestLinkedFile(AppTestCase):
    def test_read_write_roundtrip_atomic(self):
        path = os.path.join(self.tmp, 'a.gabc')
        r = self.client.post('/api/linked-file',
                             json={'path': path, 'content': GABC_STUB})
        self.assertTrue(r.get_json()['ok'])
        self.assertFalse(os.path.exists(path + '.tmp'))
        r = self.client.get('/api/linked-file?path=' + path)
        self.assertEqual(r.get_json()['content'], GABC_STUB)

    def test_extension_whitelist(self):
        for url in ('/api/linked-file?path=/etc/passwd',):
            self.assertEqual(self.client.get(url).status_code, 400)
        r = self.client.post('/api/linked-file',
                             json={'path': '/tmp/x.tex', 'content': 'x'})
        self.assertEqual(r.status_code, 400)

    def test_read_missing_404(self):
        r = self.client.get('/api/linked-file?path=' + os.path.join(self.tmp, 'gone.ly'))
        self.assertEqual(r.status_code, 404)

    def test_new_file_stub_and_no_overwrite(self):
        path = os.path.join(self.tmp, 'nouveau')
        r = self.client.post('/api/linked-file/new', json={'path': path, 'kind': 'gabc'})
        created = r.get_json()['path']
        self.assertTrue(created.endswith('.gabc'))   # bare name got the extension
        with open(created, encoding='utf-8') as f:
            self.assertIn('%%', f.read())
        r = self.client.post('/api/linked-file/new', json={'path': created, 'kind': 'gabc'})
        self.assertEqual(r.status_code, 400)
        self.assertIn('existe déjà', r.get_json()['error'])

    def test_new_ly_pins_version_low(self):
        path = os.path.join(self.tmp, 'p.ly')
        self.client.post('/api/linked-file/new', json={'path': path, 'kind': 'ly'})
        with open(path, encoding='utf-8') as f:
            self.assertIn('\\version "2.18.2"', f.read())


class TestMyrImport(AppTestCase):
    @unittest.skipUnless(has('musicxml2ly'), 'musicxml2ly not installed')
    def test_import_appends_extension_and_postprocesses(self):
        target = os.path.join(self.tmp, 'piece')   # no extension on purpose
        r = self.client.post('/api/myr-import', json={
            'musicxml_path': os.path.join(FIXTURES, 'sample.musicxml'),
            'ly_path': target})
        j = r.get_json()
        self.assertTrue(j['ok'], j)
        self.assertEqual(j['ly_path'], target + '.ly')
        with open(j['ly_path'], encoding='utf-8') as f:
            text = f.read()
        self.assertIn('\\version "2.18.2"', text)
        self.assertIn('tagline = ##f', text)

    def test_missing_paths_400(self):
        r = self.client.post('/api/myr-import', json={'ly_path': '/x.ly'})
        self.assertEqual(r.status_code, 400)
        r = self.client.post('/api/myr-import', json={
            'musicxml_path': '/nulle/part.musicxml',
            'ly_path': os.path.join(self.tmp, 'x.ly')})
        self.assertEqual(r.status_code, 400)
        self.assertIn('introuvable', r.get_json()['error'])


class TestLyPreview(AppTestCase):
    @unittest.skipUnless(has('lilypond'), 'lilypond not installed')
    def test_compile_and_cache(self):
        ly = os.path.join(self.tmp, 'p.ly')
        with open(ly, 'w', encoding='utf-8') as f:
            f.write('\\version "2.18.2"\n{ c\'4 }\n')
        r = self.client.post('/api/ly-preview', json={'path': ly, 'staffsize': 16})
        j = r.get_json()
        self.assertTrue(j['ok'], j)
        self.assertTrue(j['png'].startswith('data:image/png;base64,'))
        # Cache: identical content must return the identical rendering.
        j2 = self.client.post('/api/ly-preview',
                              json={'path': ly, 'staffsize': 16}).get_json()
        self.assertEqual(j2['png'], j['png'])

    @unittest.skipUnless(has('lilypond'), 'lilypond not installed')
    def test_error_excerpt_on_bad_source(self):
        ly = os.path.join(self.tmp, 'p.ly')
        with open(ly, 'w', encoding='utf-8') as f:
            f.write('{ c\'4 }\n')
        r = self.client.post('/api/ly-preview',
                             json={'path': ly, 'content': '{ notanote }'})
        j = r.get_json()
        self.assertFalse(j['ok'])
        self.assertIn('error', j['error'])

    def test_extension_whitelist(self):
        r = self.client.post('/api/ly-preview', json={'path': '/etc/passwd'})
        self.assertEqual(r.status_code, 400)


class TestImagePreview(AppTestCase):
    def test_png_served_raw(self):
        png = self.write_bytes('logo.png', PNG_STUB)
        r = self.client.get('/api/image-preview?path=' + png)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.mimetype.startswith('image/'))
        self.assertEqual(r.data, PNG_STUB)
        r.close()  # send_file's handle, else a ResourceWarning in the suite

    def test_unsupported_ext_400(self):
        svg = self.write('vecteur.svg', '<svg/>')
        r = self.client.get('/api/image-preview?path=' + svg)
        self.assertEqual(r.status_code, 400)

    def test_missing_file_404(self):
        r = self.client.get('/api/image-preview?path=' + os.path.join(self.tmp, 'gone.png'))
        self.assertEqual(r.status_code, 404)

    @unittest.skipUnless(has('gs') or has('gswin64c'), 'ghostscript not installed')
    def test_pdf_rasterised_to_png(self):
        # A one-page PDF the exporter's own toolchain understands isn't handy
        # here, so lean on ghostscript being able to render whatever it makes;
        # a minimal hand-written PDF is enough to exercise the gs path.
        pdf = self.write_bytes('doc.pdf', _MINIMAL_PDF)
        r = self.client.get('/api/image-preview?path=' + pdf)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.mimetype, 'image/png')
        self.assertTrue(r.data.startswith(b'\x89PNG'))


# A minimal but valid single-page PDF (empty Letter page), enough for
# ghostscript to rasterise in the PDF-preview test.
_MINIMAL_PDF = (
    b'%PDF-1.4\n'
    b'1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n'
    b'2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n'
    b'3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]>>endobj\n'
    b'xref\n0 4\n'
    b'0000000000 65535 f \n'
    b'0000000009 00000 n \n'
    b'0000000052 00000 n \n'
    b'0000000101 00000 n \n'
    b'trailer<</Size 4/Root 1 0 R>>\n'
    b'startxref\n169\n%%EOF\n')


class TestFonts(AppTestCase):
    def test_validate_font_empty_is_ok(self):
        # Empty = engine default = valid, and no compile (so always runnable).
        j = self.client.post('/api/validate-font', json={'font': ''}).get_json()
        self.assertTrue(j['ok'])

    @unittest.skipUnless(has('lilypond'), 'lilypond not installed')
    def test_available_fonts_lists_families(self):
        j = self.client.get('/api/available-fonts').get_json()
        self.assertIsInstance(j['fonts'], list)
        self.assertTrue(j['fonts'])
        # Music/symbol families are filtered out of the text picker.
        self.assertFalse(any('Emmentaler' in f for f in j['fonts']))

    def test_rescan_clears_caches_and_reenumerates(self):
        # Rescan must force a full DB rebuild AND drop both process-lifetime
        # caches, then return a fresh list — stubbed, so no toolchain needed.
        m = self.appmod
        self.addCleanup(setattr, m, '_luaotfload_refresh', m._luaotfload_refresh)
        self.addCleanup(setattr, m, 'api_available_fonts', m.api_available_fonts)
        self.addCleanup(setattr, m, '_FONT_LIST_CACHE', m._FONT_LIST_CACHE)
        old_valid = dict(m._FONT_VALID_CACHE)
        self.addCleanup(lambda: (m._FONT_VALID_CACHE.clear(),
                                 m._FONT_VALID_CACHE.update(old_valid)))
        forced = []
        m._luaotfload_refresh = lambda force=False: forced.append(force) or True
        m.api_available_fonts = lambda: m.jsonify({'fonts': ['Fresh']})
        m._FONT_LIST_CACHE = ['Stale']
        m._FONT_VALID_CACHE['Stale'] = True
        r = self.client.post('/api/fonts/rescan')
        self.assertEqual(r.get_json()['fonts'], ['Fresh'])
        self.assertEqual(forced, [True])                  # forced full rebuild
        self.assertIsNone(m._FONT_LIST_CACHE)             # list cache dropped
        self.assertNotIn('Stale', m._FONT_VALID_CACHE)    # validate cache cleared

    @unittest.skipUnless(has('lualatex'), 'lualatex not installed')
    def test_validate_font_good_vs_bogus(self):
        good = self.client.post('/api/validate-font',
                                json={'font': 'Latin Modern Roman'}).get_json()
        self.assertTrue(good['ok'])
        bad = self.client.post('/api/validate-font',
                               json={'font': 'No Such Font 9Q'}).get_json()
        self.assertFalse(bad['ok'])

    def test_stale_font_recovered_by_db_refresh(self):
        # A font the OS lists but luaotfload misses first time: the endpoint
        # refreshes luaotfload's DB once and retries, so the miss self-heals.
        # No toolchain — stub the compile (fail then pass) and the refresh.
        m = self.appmod
        self.addCleanup(setattr, m, '_font_loads', m._font_loads)
        self.addCleanup(setattr, m, '_luaotfload_refresh', m._luaotfload_refresh)
        m._font_loads = lambda lx, f: self._loads.pop(0)
        m._luaotfload_refresh = lambda: True
        self._loads = [False, True]                 # miss, then hit after refresh
        old_list, old_valid = m._FONT_LIST_CACHE, dict(m._FONT_VALID_CACHE)
        m._FONT_LIST_CACHE = ['Ghost Serif']        # OS knows it → on disk
        m._FONT_VALID_CACHE.clear()
        self.addCleanup(setattr, m, '_FONT_LIST_CACHE', old_list)
        self.addCleanup(m._FONT_VALID_CACHE.update, old_valid)
        j = self.client.post('/api/validate-font',
                             json={'font': 'Ghost Serif'}).get_json()
        self.assertTrue(j['ok'])
        self.assertEqual(self._loads, [])           # both attempts consumed

    def test_typo_font_does_not_trigger_db_refresh(self):
        # A name no engine knows must NOT pay for a DB rebuild: refresh is only
        # for fonts the OS lists (on disk) that luaotfload missed.
        m = self.appmod
        self.addCleanup(setattr, m, '_font_loads', m._font_loads)
        self.addCleanup(setattr, m, '_luaotfload_refresh', m._luaotfload_refresh)
        m._font_loads = lambda lx, f: False
        refreshed = []
        m._luaotfload_refresh = lambda: refreshed.append(1) or True
        old_list, old_valid = m._FONT_LIST_CACHE, dict(m._FONT_VALID_CACHE)
        m._FONT_LIST_CACHE = ['Real Serif']         # 'Nope 9Q' is absent
        m._FONT_VALID_CACHE.clear()
        self.addCleanup(setattr, m, '_FONT_LIST_CACHE', old_list)
        self.addCleanup(m._FONT_VALID_CACHE.update, old_valid)
        j = self.client.post('/api/validate-font',
                             json={'font': 'Nope 9Q'}).get_json()
        self.assertFalse(j['ok'])
        self.assertEqual(refreshed, [])             # never rebuilt the DB


class TestFontPreflight(AppTestCase):
    def _project_with_fonts(self, **typo):
        path = os.path.join(self.tmp, 'p.breviaire')
        data = self.client.post('/api/project/new', json={'path': path}).get_json()['data']
        data['defs']['typography'].update(typo)
        return path, data

    def test_referenced_fonts_dedup_and_skip_empty(self):
        m = self.appmod
        # gabc '' inherits text_font; duplicate ly collapses; order preserved.
        self.assertEqual(
            m._referenced_fonts({'defs': {'typography': {
                'text_font': 'A', 'gabc_font': '', 'ly_font': 'A'}}}), ['A'])
        self.assertEqual(
            m._referenced_fonts({'defs': {'typography': {
                'text_font': 'A', 'gabc_font': 'B', 'ly_font': 'C'}}}), ['A', 'B', 'C'])
        self.assertEqual(m._referenced_fonts({}), [])

    def test_check_fonts_reports_missing(self):
        m = self.appmod
        self.addCleanup(setattr, m, '_missing_fonts', m._missing_fonts)
        m._missing_fonts = lambda fonts: [f for f in fonts if f == 'Ghost']
        r = self.client.post('/api/check-fonts', json={'defs': {'typography': {
            'text_font': 'Ghost', 'ly_font': 'Real'}}})
        self.assertEqual(r.get_json()['missing'], ['Ghost'])

    def test_export_rewarns_on_missing_font(self):
        # A referenced font this machine can't load blocks (needs_confirm)
        # before any compile — testable with the font check stubbed.
        m = self.appmod
        self.addCleanup(setattr, m, '_missing_fonts', m._missing_fonts)
        m._missing_fonts = lambda fonts: list(fonts)
        path, data = self._project_with_fonts(text_font='Ghost Serif')
        j = self.client.post('/api/export', json={'path': path, 'data': data}).get_json()
        self.assertTrue(j.get('needs_confirm'))
        self.assertEqual(j['reason'], 'fonts_missing')
        self.assertIn('Ghost Serif', j['fonts'])

    def test_export_force_skips_font_guard(self):
        # force=true bypasses the guard and goes straight to the compile.
        m = self.appmod
        self.addCleanup(setattr, m, '_missing_fonts', m._missing_fonts)
        self.addCleanup(setattr, m.exporter_module, 'export_project',
                        m.exporter_module.export_project)
        def _boom(fonts):
            raise AssertionError('font check must not run under force')
        called = []
        m._missing_fonts = _boom
        m.exporter_module.export_project = lambda *a, **k: called.append(1) or {
            'success': True, 'tex_path': 't', 'pdf_path': 'p', 'log': '', 'last_export': None}
        path, data = self._project_with_fonts(text_font='Ghost Serif')
        j = self.client.post('/api/export',
                             json={'path': path, 'data': data, 'force': True}).get_json()
        self.assertTrue(j['success'])
        self.assertEqual(called, [1])

    def test_export_autoheal_retries_on_font_miss(self):
        # Preflight passes (font on disk) but the first compile fails with a
        # font-not-found: refresh the index once and retry, which then succeeds.
        m = self.appmod
        self.addCleanup(setattr, m, '_missing_fonts', m._missing_fonts)
        self.addCleanup(setattr, m, '_luaotfload_refresh', m._luaotfload_refresh)
        self.addCleanup(setattr, m.exporter_module, 'export_project',
                        m.exporter_module.export_project)
        m._missing_fonts = lambda fonts: []
        m._luaotfload_refresh = lambda: True
        results = [
            {'success': False, 'tex_path': 't', 'pdf_path': None,
             'log': 'The font "StaleFont" cannot be found.', 'last_export': None},
            {'success': True, 'tex_path': 't', 'pdf_path': 'p', 'log': '', 'last_export': None},
        ]
        m.exporter_module.export_project = lambda *a, **k: results.pop(0)
        path, data = self._project_with_fonts(text_font='StaleFont')
        j = self.client.post('/api/export', json={'path': path, 'data': data}).get_json()
        self.assertTrue(j['success'])
        self.assertEqual(results, [])   # both attempts consumed → it retried

    @unittest.skipUnless(has('lilypond'), 'lilypond not installed')
    def test_ly_preview_accepts_font(self):
        ly = os.path.join(self.tmp, 'p.ly')
        with open(ly, 'w', encoding='utf-8') as f:
            f.write('\\version "2.18.2"\n{ c\'4 }\n')
        j = self.client.post('/api/ly-preview', json={
            'path': ly, 'staffsize': 16, 'font': 'DejaVu Serif'}).get_json()
        self.assertTrue(j['ok'], j)
        self.assertTrue(j['png'].startswith('data:image/png;base64,'))


class TestExportGuard(AppTestCase):
    def test_needs_confirm_when_tex_hand_edited(self):
        # A recorded export whose .tex is now newer than its stamp returns
        # needs_confirm *before* compiling — so this needs no TeX toolchain.
        ppath = os.path.join(self.tmp, 'b.breviaire')
        data = self.client.post('/api/project/new', json={'path': ppath}).get_json()['data']
        tex = os.path.join(self.tmp, 'b.tex')
        with open(tex, 'w', encoding='utf-8') as f:
            f.write('modifié à la main')
        data['last_export'] = {'tex_path': tex, 'exported_at': os.path.getmtime(tex) - 50}
        j = self.client.post('/api/export', json={'path': ppath, 'data': data}).get_json()
        self.assertTrue(j.get('needs_confirm'))
        self.assertEqual(j['reason'], 'modified')
        self.assertEqual(j['tex_path'], tex)


class TestExportTemplate(AppTestCase):
    def test_get_bundled_template(self):
        j = self.client.get('/api/export-template').get_json()
        self.assertIn('\\documentclass', j['content'])
        self.assertFalse(j['is_custom'])

    def test_broken_template_save_rejected(self):
        r = self.client.post('/api/export-template', json={'content': '<% if %>'})
        self.assertEqual(r.status_code, 400)
        self.assertTrue(r.get_json()['errors'])
