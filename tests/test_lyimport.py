"""MusicXML→ly post-processing (app/lyimport.py) — the three quirks fixed
at import, plus the real musicxml2ly round-trip when the binary exists."""

import os
import unittest

from .base import TempDirTestCase, FIXTURES, has

import lyimport


# Shape of real musicxml2ly output (reproduced 2026-07-09, LilyPond 2.22):
# version stamp, header with credits, a lyric syllable with the Harmony
# Assistant leading-newline artifact, and a string at end-of-line that a
# naive newline-stripping regex would mangle.
SAMPLE = '''\\version "2.22.1"
% automatically converted by musicxml2ly
\\pointAndClickOff

\\header {
    title =  "Le temps des cerises"
    composer =  "A. Renard"
    encodingsoftware =  "Harmony Assistant"
    }

PartPOneVoiceOne =  \\relative c' {
    \\clef "treble" \\time 4/4 \\key c \\major | % 1
    c4 d4 e2 }

PartPOneVoiceOneLyricsOne =  \\lyricmode {\\set ignoreMelismata = ##t
    "
Quand" -- nous chan
    }

\\score {
    <<
        \\new Staff
        <<
            \\set Staff.instrumentName = "Voix"
            \\context Voice = "PartPOneVoiceOne" {  \\PartPOneVoiceOne }
        >>
    >>
    \\layout {}
    }
'''


class TestPostprocess(unittest.TestCase):
    def test_version_pinned(self):
        out = lyimport.postprocess_ly(SAMPLE)
        self.assertIn('\\version "2.18.2"', out)
        self.assertNotIn('2.22.1', out)

    def test_version_prepended_when_absent(self):
        out = lyimport.postprocess_ly('{ c4 }\n')
        self.assertTrue(out.startswith('\\version "2.18.2"'))

    def test_lyric_leading_newline_stripped(self):
        out = lyimport.postprocess_ly(SAMPLE)
        self.assertNotIn('"\nQuand"', out)
        self.assertIn('"Quand"', out)

    def test_closing_quote_at_eol_not_mangled(self):
        # A global complete-string match pairs quotes correctly; matching a
        # bare quote+newline would join this line with the next.
        out = lyimport.postprocess_ly(SAMPLE)
        self.assertIn('\\set Staff.instrumentName = "Voix"\n', out)

    def test_header_replaced_with_tagline_off(self):
        out = lyimport.postprocess_ly(SAMPLE)
        self.assertNotIn('encodingsoftware', out)
        self.assertNotIn('Le temps des cerises', out)
        self.assertIn('tagline = ##f', out)

    def test_header_added_when_absent(self):
        out = lyimport.postprocess_ly('{ c4 }\n')
        self.assertIn('tagline = ##f', out)

    def test_header_brace_inside_string(self):
        out = lyimport.postprocess_ly(
            '\\version "2.24.0"\n\\header { title = "a { b" }\n{ c4 }\n')
        self.assertNotIn('a { b', out)
        self.assertIn('tagline = ##f', out)

    def test_unbalanced_header_left_alone(self):
        # Corrupting the file would be worse than leaving the header.
        out = lyimport.postprocess_ly('\\header { title = "x"\n{ c4 }\n')
        self.assertIn('title = "x"', out)


class TestImportMusicXml(TempDirTestCase):
    @unittest.skipUnless(has('musicxml2ly'), 'musicxml2ly not installed')
    def test_real_conversion(self):
        ly_path = os.path.join(self.tmp, 'out.ly')
        lyimport.import_musicxml(
            os.path.join(FIXTURES, 'sample.musicxml'), ly_path, 'musicxml2ly')
        with open(ly_path, encoding='utf-8') as f:
            text = f.read()
        self.assertIn('\\version "2.18.2"', text)
        self.assertIn('tagline = ##f', text)
        self.assertIn('"Quand"', text)   # artifact fixed
        self.assertIn('\\lyricmode', text)

    def test_missing_musicxml_raises_french(self):
        with self.assertRaises(lyimport.LyImportError) as ctx:
            lyimport.import_musicxml(
                os.path.join(self.tmp, 'absent.musicxml'),
                os.path.join(self.tmp, 'out.ly'), 'musicxml2ly')
        self.assertIn('introuvable', str(ctx.exception))
