"""One-file .tex generation (app/exporter.py) — data preparation and the
rendered document's shape. The full lualatex compile is env-gated (slow)."""

import os
import unittest
from unittest import mock

from .base import TempDirTestCase, GABC_STUB, LY_STUB, PNG_STUB, SLOW, has

import exporter
import project


def _project_with(items):
    data = project.new_project()
    data['items'] = items
    return data


class TestEscapes(unittest.TestCase):
    def test_tex_specials(self):
        self.assertEqual(exporter.escape_tex('50% & #1 _x'),
                         r'50\% \& \#1 \_x')
        self.assertEqual(exporter.escape_tex('a\\b'), r'a\textbackslash{}b')


class TestStyles(unittest.TestCase):
    def test_nargs_is_highest_param(self):
        self.assertEqual(exporter._style_nargs('{#1 et #3}'), 3)
        self.assertEqual(exporter._style_nargs('{pas de champ}'), 1)

    def test_ten_params_rejected_french(self):
        with self.assertRaises(exporter.ExportError) as ctx:
            exporter._style_nargs('#10')
        self.assertIn('9 paramètres', str(ctx.exception))

    def test_macro_name_sanitized_and_collisions_deterministic(self):
        defs, by_key = exporter._macroize_styles(
            {'en-tête': '#1', 'entête': '#1', '123': '#1'})
        names = [d['macro'] for d in defs]
        self.assertEqual(len(set(names)), 3)
        for n in names:
            self.assertRegex(n, r'^[a-z]+$')
        self.assertEqual(by_key['entête']['macro'],
                         by_key['en-tête']['macro'] + 'x')


class TestBuildTex(TempDirTestCase):
    def build(self, items, **kw):
        return exporter.build_tex(_project_with(items), lilypond_path='lilypond',
                                  snippet_prefix='t', **kw)

    def test_text_item_becomes_macro_call(self):
        tex = self.build([{'type': 'text', 'key': 'header', 'contents': ['Vêpres']}])
        self.assertIn('\\newcommand{\\styleheader}', tex)
        self.assertIn('\\styleheader{Vêpres}', tex)

    def test_unknown_style_rejected_french(self):
        with self.assertRaises(exporter.ExportError) as ctx:
            self.build([{'type': 'text', 'key': 'fantôme', 'contents': ['x']}])
        self.assertIn('fantôme', str(ctx.exception))

    def test_contents_padded_and_truncated_to_arity(self):
        data = _project_with(
            [{'type': 'text', 'key': 'duo', 'contents': ['seul']}])
        data['defs']['styles']['duo'] = '{#1 | #2}'
        tex = exporter.build_tex(data, lilypond_path='lilypond', snippet_prefix='t')
        self.assertIn('\\styleduo{seul}{}', tex)

    def test_gabc_inlined_as_filecontents(self):
        gabc = self.write('k.gabc', GABC_STUB)
        tex = self.build([{'type': 'gabc', 'file_path': gabc}])
        self.assertIn('\\begin{filecontents*}[overwrite]{t-gabc-1.gabc}', tex)
        self.assertIn('\\breviairegabc{t-gabc-1}', tex)
        self.assertIn('(c4) A(f)men.(g) (::)', tex)

    def test_ly_inlined_in_ly_environment(self):
        ly = self.write('p.ly', LY_STUB)
        tex = self.build([{'type': 'myr', 'source_path': None, 'file_path': ly}])
        self.assertIn('\\begin{ly}', tex)
        self.assertIn("c'4 d'4", tex)

    def test_image_embedded_base64_with_width(self):
        png = self.write_bytes('logo.png', PNG_STUB)
        tex = self.build([{'type': 'image', 'file_path': png, 'width': 0.5}])
        # Decoder call + base64 payload travel inline; the scratch name keeps
        # the source extension so \includegraphics picks the right reader.
        self.assertIn("breviaire.writeimage('t-img-1.png'", tex)
        self.assertIn('\\adjincludegraphics', tex)
        self.assertIn('width=0.5\\linewidth', tex)
        self.assertIn('iVBORw0KGgo', tex)  # PNG magic, base64-encoded

    def test_image_crop_becomes_adjustbox_trim(self):
        png = self.write_bytes('logo.png', PNG_STUB)
        tex = self.build([{'type': 'image', 'file_path': png, 'width': 1,
                           'crop': {'left': 0.1, 'top': 0.2, 'right': 0.1, 'bottom': 0.2}}])
        # adjustbox trim order is left/bottom/right/top, as \width/\height
        # fractions, followed by clip.
        self.assertIn('trim={0.1\\width} {0.2\\height} {0.1\\width} {0.2\\height}', tex)
        self.assertIn('clip', tex)

    def test_image_no_crop_omits_trim(self):
        png = self.write_bytes('logo.png', PNG_STUB)
        tex = self.build([{'type': 'image', 'file_path': png, 'width': 1,
                           'crop': {'left': 0, 'top': 0, 'right': 0, 'bottom': 0}}])
        self.assertNotIn('trim=', tex)

    def test_image_degenerate_crop_ignored(self):
        # Opposite margins that meet/cross would trim the whole image away —
        # the exporter drops such a crop rather than emit an empty box.
        png = self.write_bytes('logo.png', PNG_STUB)
        tex = self.build([{'type': 'image', 'file_path': png, 'width': 1,
                           'crop': {'left': 0.6, 'top': 0, 'right': 0.6, 'bottom': 0}}])
        self.assertNotIn('trim=', tex)

    def test_image_unsupported_format_rejected_french(self):
        svg = self.write('vecteur.svg', '<svg/>')
        with self.assertRaises(exporter.ExportError) as ctx:
            self.build([{'type': 'image', 'file_path': svg, 'width': 1}])
        self.assertIn('exportez en PDF', str(ctx.exception))

    def test_image_missing_file_note(self):
        tex = self.build([{'type': 'image', 'file_path': '/nulle/part.png'}])
        self.assertIn('Fichier manquant', tex)
        self.assertIn('/nulle/part.png', tex)

    def test_missing_file_note_present_by_default(self):
        tex = self.build([{'type': 'gabc', 'file_path': '/nulle/part.gabc'}])
        self.assertIn('Fichier manquant', tex)
        self.assertIn('/nulle/part.gabc', tex)

    def test_missing_file_dropped_when_toggle_off(self):
        tex = self.build(
            [{'type': 'gabc', 'file_path': '/nulle/part.gabc'},
             {'type': 'text', 'key': 'body', 'contents': ['gardé']}],
            include_missing_notes=False)
        self.assertNotIn('Fichier manquant', tex)
        self.assertIn('gardé', tex)

    def test_geometry_from_defs(self):
        data = _project_with([])
        data['defs']['page'] = {'size': 'a4paper', 'margin': '2cm', 'gutter': '7mm'}
        data['defs']['music']['staffsize'] = 14
        tex = exporter.build_tex(data, lilypond_path='lilypond', snippet_prefix='t')
        self.assertIn('a4paper,margin=2cm,bindingoffset=7mm', tex)
        self.assertIn('\\grechangestaffsize{14}', tex)
        self.assertIn('staffsize=14', tex)

    def test_french_spacing_branches(self):
        data = _project_with([])
        tex = exporter.build_tex(data, lilypond_path='lilypond', snippet_prefix='t')
        self.assertIn('luacode*', tex)              # frpunct default
        # (frpunct.lua's own header says "derived from babel-french", so
        # check for the package line, not the word)
        self.assertNotIn('\\usepackage[french]{babel}', tex)
        data['defs']['typography']['french_spacing'] = 'babel'
        tex = exporter.build_tex(data, lilypond_path='lilypond', snippet_prefix='t')
        self.assertIn('\\usepackage[french]{babel}', tex)

    def _with_typo(self, **typo):
        music = {k: typo.pop(k) for k in list(typo) if k in ('staffsize', 'ly_staffsize')}
        data = _project_with([{'type': 'text', 'key': 'body', 'contents': ['x']}])
        data['defs']['typography'].update(typo)
        data['defs']['music'].update(music)
        return exporter.build_tex(data, lilypond_path='lilypond', snippet_prefix='t')

    def test_empty_font_falls_back_to_bundled_default(self):
        # An emptied text font is NOT the engine's Latin Modern surprise: with
        # the bundle present it falls back to the bundled house default, and ly
        # inherits it, so "no font" == the schema default face, uniformly.
        with mock.patch.object(exporter, '_bundled_fonts_dir', return_value='/x'):
            tex = self._with_typo(text_font='', gabc_font='', ly_font='', text_size=0)
        self.assertIn('\\setmainfont{%s}' % project.DEFAULT_TEXT_FONT, tex)
        self.assertIn('rmfamily={%s}' % project.DEFAULT_TEXT_FONT, tex)
        self.assertNotIn('\\newfontfamily', tex)    # gabc still follows main font

    def test_empty_font_without_bundle_uses_engine_default(self):
        # No bundle shipped (stripped/dev build): emitting \setmainfont for an
        # absent family would fail the compile, so empty stays the engine font.
        with mock.patch.object(exporter, '_bundled_fonts_dir', return_value=None):
            tex = self._with_typo(text_font='', gabc_font='', ly_font='', text_size=0)
        self.assertNotIn('\\setmainfont{', tex)
        self.assertNotIn('rmfamily={', tex)
        self.assertNotIn('\\newfontfamily', tex)

    def test_default_font_is_bundled(self):
        # New projects default to the app-bundled EB Garamond (house look,
        # machine-independent). Locks the decision made 2026-07-10.
        self.assertEqual(
            project.new_project()['defs']['typography']['text_font'], 'EB Garamond')

    def test_text_font_drives_body_and_ly(self):
        # The base font also flows to lilypond (rmfamily) so the default stays
        # uniform without a separate ly override.
        tex = self._with_typo(text_font='EB Garamond')
        self.assertIn('\\setmainfont{EB Garamond}', tex)
        self.assertIn('rmfamily={EB Garamond}', tex)

    def test_gabc_font_override_wraps_score(self):
        gabc = self.write('k.gabc', GABC_STUB)
        data = _project_with([{'type': 'gabc', 'file_path': gabc}])
        data['defs']['typography']['gabc_font'] = 'DejaVu Serif'
        tex = exporter.build_tex(data, lilypond_path='lilypond', snippet_prefix='t')
        self.assertIn('\\newfontfamily\\breviairegabcfont{DejaVu Serif}', tex)
        self.assertIn('\\begingroup\\breviairegabcfont', tex)
        self.assertIn('\\endgroup', tex)

    def test_ly_font_override_beats_base(self):
        tex = self._with_typo(text_font='EB Garamond', ly_font='Noto Sans')
        self.assertIn('rmfamily={Noto Sans}', tex)   # ly override wins
        self.assertIn('\\setmainfont{EB Garamond}', tex)  # body keeps base

    def test_ly_staffsize_independent_else_inherits(self):
        self.assertIn('staffsize=21', self._with_typo(staffsize=17, ly_staffsize=21))
        self.assertIn('\\grechangestaffsize{17}', self._with_typo(staffsize=17, ly_staffsize=21))
        # 0 = inherit the gabc staffsize, keeping the default uniform.
        self.assertIn('staffsize=17', self._with_typo(staffsize=17, ly_staffsize=0))

    def test_text_size_emits_fontsize(self):
        # The base size is unit-less (\fontsize{12}{14.4}); the default style
        # bodies use pt units (\fontsize{11pt}…), so this is unambiguous.
        self.assertIn('\\fontsize{12}{14.4}\\selectfont', self._with_typo(text_size=12))
        self.assertNotIn('\\fontsize{12}{14.4}', self._with_typo(text_size=0))

    def test_lilypond_path_forward_slashes(self):
        tex = exporter.build_tex(_project_with([]),
                                 lilypond_path='C:\\Program Files\\lilypond.exe',
                                 snippet_prefix='t')
        self.assertIn('program={C:/Program Files/lilypond.exe}', tex)


class TestBundledFonts(TempDirTestCase):
    """The app-bundled-fonts dir is wired into OSFONTDIR only once it actually
    holds fonts — scaffold-safe until the OFL binaries are shipped."""

    def test_empty_dir_is_ignored(self):
        self.write('README.md', 'notes, no fonts')   # only non-font files
        self.assertIsNone(exporter._bundled_fonts_dir(self.tmp))

    def test_dir_with_a_font_is_used(self):
        self.write('EBGaramond-Regular.otf', 'stub')  # extension is what counts
        self.assertEqual(exporter._bundled_fonts_dir(self.tmp), self.tmp)

    def test_missing_dir_is_ignored(self):
        self.assertIsNone(exporter._bundled_fonts_dir(os.path.join(self.tmp, 'nope')))


class TestFontconfigConf(TempDirTestCase):
    """The LilyPond/Pango-side fontconfig config generation — the piece that has
    to work without an /etc/fonts (the Windows gap). No LilyPond invoked."""

    def setUp(self):
        super().setUp()
        # Regenerate per test (module caches it once per process).
        self.addCleanup(setattr, exporter, '_FONTCONFIG_FILE', exporter._FONTCONFIG_FILE)
        exporter._FONTCONFIG_FILE = None

    def _fake_lilypond(self):
        """A minimal LilyPond install tree: <root>/bin/lilypond plus its font
        dir and its own fonts.conf, as discovered relative to the binary."""
        root = os.path.join(self.tmp, 'ly')
        binp = os.path.join(root, 'bin')
        share = os.path.join(root, 'share', 'lilypond')
        etc = os.path.join(root, 'etc', 'fonts')
        for d in (binp, share, etc):
            os.makedirs(d)
        lily = os.path.join(binp, 'lilypond' + ('.exe' if os.name == 'nt' else ''))
        open(lily, 'w').close()
        base = os.path.join(etc, 'fonts.conf')
        with open(base, 'w', encoding='utf-8') as f:
            f.write('<fontconfig/>')
        return lily, share, base

    def test_lilypond_font_dirs_discovers_share_and_base(self):
        lily, share, base = self._fake_lilypond()
        dirs, found = exporter._lilypond_font_dirs(lily)
        self.assertIn(os.path.abspath(share), [os.path.abspath(d) for d in dirs])
        self.assertEqual(os.path.abspath(found), os.path.abspath(base))

    def test_lilypond_font_dirs_none_without_binary(self):
        self.assertEqual(exporter._lilypond_font_dirs(None), ([], None))

    def test_conf_lists_bundle_and_lilypond_dirs(self):
        # The invariant that closes the Windows gap: bundled dir AND LilyPond's
        # own font dir are both listed as <dir>, and a base is included — no
        # dependence on a system /etc/fonts existing.
        fonts = os.path.join(self.tmp, 'fonts')
        os.makedirs(fonts)
        lily, share, _ = self._fake_lilypond()
        conf = exporter._fontconfig_file(fonts, lily)
        with open(conf, encoding='utf-8') as f:
            text = f.read()
        self.assertIn('<dir>%s</dir>' % os.path.abspath(fonts), text)
        self.assertIn('<dir>%s</dir>' % os.path.abspath(share), text)
        self.assertIn('<include', text)      # a base config is always included
        self.assertIn('<cachedir>', text)

    def test_system_font_dirs_are_real_directories(self):
        for d in exporter._system_font_dirs():
            self.assertTrue(os.path.isdir(d), d)


class TestExportState(TempDirTestCase):
    """The 'has the exported .tex been hand-edited?' guard — no compile."""

    def _proj(self, last_export=None):
        data = project.new_project()
        if last_export is not None:
            data['last_export'] = last_export
        return data

    def _state(self, data):
        ppath = os.path.join(self.tmp, 'b.breviaire')
        return exporter.export_state(data, ppath, {'output_folder': self.tmp})

    def test_no_prior_export(self):
        st = self._state(self._proj())
        self.assertIsNone(st['prior'])
        self.assertTrue(st['target_tex'].endswith('b.tex'))

    def test_clean_when_untouched(self):
        tex = self.write('b.tex', 'x')
        st = self._state(self._proj({'tex_path': tex, 'exported_at': os.path.getmtime(tex)}))
        self.assertEqual(st['prior']['state'], 'clean')

    def test_modified_when_newer_than_stamp(self):
        tex = self.write('b.tex', 'x')
        st = self._state(self._proj({'tex_path': tex, 'exported_at': os.path.getmtime(tex) - 100}))
        self.assertEqual(st['prior']['state'], 'modified')
        self.assertGreater(st['prior']['modified_at'], st['prior']['exported_at'])

    def test_missing_when_tex_gone(self):
        gone = os.path.join(self.tmp, 'b.tex')  # never created
        st = self._state(self._proj({'tex_path': gone, 'exported_at': 123.0}))
        self.assertEqual(st['prior']['state'], 'missing')

    def test_record_pointing_elsewhere_is_not_guarded(self):
        # A record for a different path than we'd overwrite now = no clobber.
        st = self._state(self._proj({'tex_path': '/ailleurs/b.tex', 'exported_at': 1.0}))
        self.assertIsNone(st['prior'])


@unittest.skipUnless(
    SLOW and has('lualatex') and has('gregorio') and has('lilypond'),
    'slow compile test (set ATELIER_SLOW_TESTS=1, needs the TeX toolchain)')
class TestExportCompile(TempDirTestCase):
    def test_full_pipeline_produces_pdf(self):
        gabc = self.write('k.gabc', GABC_STUB)
        ly = self.write('p.ly', LY_STUB)
        png = self.write_bytes('logo.png', PNG_STUB)
        data = _project_with([
            {'type': 'text', 'key': 'header', 'contents': ['Test complet']},
            {'type': 'gabc', 'file_path': gabc},
            {'type': 'myr', 'source_path': None, 'file_path': ly},
            {'type': 'image', 'file_path': png, 'width': 0.4,
             'crop': {'left': 0.1, 'top': 0.1, 'right': 0.1, 'bottom': 0.1}},
        ])
        ppath = os.path.join(self.tmp, 'suite.breviaire')
        project.save_project(ppath, data)
        res = exporter.export_project(ppath, data, {})
        self.assertTrue(res['success'], res['log'][-2000:])
        self.assertTrue(os.path.isfile(res['pdf_path']))
        self.assertTrue(os.path.isfile(res['tex_path']))
        # Export stamp recorded and, right after writing, reads back as clean.
        self.assertEqual(res['last_export']['tex_path'], res['tex_path'])
        data['last_export'] = res['last_export']
        st = exporter.export_state(data, ppath, {})
        self.assertEqual(st['prior']['state'], 'clean')
