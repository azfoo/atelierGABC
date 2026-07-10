"""Project file schema, load/save, and linked-file status (app/project.py)."""

import json
import os
import time

from .base import TempDirTestCase, GABC_STUB

import project


class TestValidate(TempDirTestCase):
    def test_new_project_is_valid(self):
        project.validate_project(project.new_project())

    def test_rejects_non_dict(self):
        with self.assertRaises(project.ProjectError):
            project.validate_project([])

    def test_rejects_missing_schema_version(self):
        with self.assertRaises(project.ProjectError):
            project.validate_project({'items': [], 'defs': {}})

    def test_rejects_newer_schema(self):
        data = project.new_project()
        data['schema_version'] = project.SCHEMA_VERSION + 1
        with self.assertRaises(project.ProjectError):
            project.validate_project(data)

    def test_rejects_bad_item_type(self):
        data = project.new_project()
        data['items'] = [{'type': 'nope'}]
        with self.assertRaises(project.ProjectError):
            project.validate_project(data)

    def test_text_contents_must_be_list(self):
        data = project.new_project()
        data['items'] = [{'type': 'text', 'key': 'body', 'contents': 'plain'}]
        with self.assertRaises(project.ProjectError):
            project.validate_project(data)

    def test_legacy_myr_fields_renamed(self):
        # Pre-Phase-4 files used myr_path/ly_path; the load shim renames
        # them to the generic source_path/file_path convention.
        data = {'schema_version': 1, 'defs': {},
                'items': [{'type': 'myr', 'myr_path': '/a.myr', 'ly_path': '/b.ly'}]}
        out = project.validate_project(data)
        item = out['items'][0]
        self.assertEqual(item['file_path'], '/b.ly')
        self.assertEqual(item['source_path'], '/a.myr')
        self.assertNotIn('ly_path', item)
        self.assertNotIn('myr_path', item)


class TestSaveLoad(TempDirTestCase):
    def test_roundtrip(self):
        path = os.path.join(self.tmp, 'x.breviaire')
        data = project.new_project()
        data['items'] = [{'type': 'text', 'key': 'body', 'contents': ['Bonjour é à']}]
        project.save_project(path, data)
        loaded = project.load_project(path)
        self.assertEqual(loaded['items'][0]['contents'], ['Bonjour é à'])

    def test_save_is_atomic_no_tmp_left(self):
        path = os.path.join(self.tmp, 'x.breviaire')
        project.save_project(path, project.new_project())
        self.assertFalse(os.path.exists(path + '.tmp'))

    def test_extra_item_keys_survive(self):
        # ignored_missing (Phase 5) and future fields must round-trip.
        path = os.path.join(self.tmp, 'x.breviaire')
        data = project.new_project()
        data['items'] = [{'type': 'gabc', 'file_path': '/gone.gabc',
                          'ignored_missing': ['file_path']}]
        project.save_project(path, data)
        loaded = project.load_project(path)
        self.assertEqual(loaded['items'][0]['ignored_missing'], ['file_path'])

    def test_save_rejects_invalid(self):
        path = os.path.join(self.tmp, 'x.breviaire')
        with self.assertRaises(project.ProjectError):
            project.save_project(path, {'items': 'nope'})
        self.assertFalse(os.path.exists(path))


class TestFileStatus(TempDirTestCase):
    def test_missing_and_present(self):
        gabc = self.write('a.gabc', GABC_STUB)
        data = project.new_project()
        data['items'] = [
            {'type': 'gabc', 'file_path': gabc},
            {'type': 'gabc', 'file_path': os.path.join(self.tmp, 'gone.gabc')},
            {'type': 'gabc', 'file_path': None},   # unlinked ≠ missing
            {'type': 'text', 'key': 'body', 'contents': ['x']},
        ]
        st = project.file_status(data)
        self.assertEqual([e['missing'] for e in st],
                         [[], ['file_path'], [], []])

    def test_stale_when_source_newer(self):
        ly = self.write('a.ly', '{ c4 }')
        myr = self.write('a.myr', 'binary-ish')
        data = project.new_project()
        data['items'] = [{'type': 'myr', 'source_path': myr, 'file_path': ly}]

        now = time.time()
        os.utime(myr, (now + 100, now + 100))
        self.assertTrue(project.file_status(data)[0]['stale'])
        os.utime(myr, (now - 100, now - 100))
        self.assertFalse(project.file_status(data)[0]['stale'])

    def test_no_stale_key_without_source(self):
        ly = self.write('a.ly', '{ c4 }')
        data = project.new_project()
        data['items'] = [{'type': 'myr', 'source_path': None, 'file_path': ly}]
        self.assertNotIn('stale', project.file_status(data)[0])

    def test_missing_source_reported_but_not_stale(self):
        ly = self.write('a.ly', '{ c4 }')
        data = project.new_project()
        data['items'] = [{'type': 'myr',
                          'source_path': os.path.join(self.tmp, 'gone.myr'),
                          'file_path': ly}]
        st = project.file_status(data)[0]
        self.assertEqual(st['missing'], ['source_path'])
        self.assertNotIn('stale', st)
