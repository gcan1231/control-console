"""Discovery tests use only fictional files and injected health responses."""
import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock

from control_console.discovery import DiscoveryService, choose_local_path
from control_console.importers import ImportFailure


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.calls = []
        self.health = {'service': 'minimal-api', 'build_id': 'plan011-20261009-r1',
                       'version': '0.4.6', 'frozen': True,
                       'endpoints': ['/api/integration/task-table', '/api/task-table/row/materials']}
        def read(port):
            self.calls.append(port)
            return self.health
        self.service = DiscoveryService(None, roots=[self.root], health_reader=read)

    def fixture(self, folder=None, marker=True):
        folder = folder or self.root
        (folder / 'db').mkdir(parents=True)
        database = folder / 'db' / 'material.db'
        with closing(sqlite3.connect(database)) as connection:
            connection.execute('CREATE TABLE download_jobs (task_id TEXT, started_at TEXT, material_ids_json TEXT, items_json TEXT, state TEXT, failed INTEGER)')
        if marker:
            (folder / 'runtime.json').write_text(json.dumps({'port': 9988, 'pid': 987,
                'version': '0.4.6', 'frozen': True}), encoding='utf-8')
        return database

    def test_verified_marker_without_writes(self):
        database = self.fixture()
        before = {p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        result = self.service.scan({})
        self.assertEqual(len(result['candidates']), 1)
        candidate = result['candidates'][0]
        self.assertTrue(candidate['eligible'])
        self.assertEqual(candidate['database'], str(database.resolve()))
        self.assertEqual(self.calls, [9988])
        self.assertEqual(before, {p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()})

    def test_database_alone_never_probes(self):
        self.fixture(marker=False)
        candidate = self.service.scan()['candidates'][0]
        self.assertFalse(candidate['eligible'])
        self.assertIsNone(candidate['port'])
        self.assertEqual(self.calls, [])

    def test_wrong_identity_or_version_not_eligible(self):
        self.fixture()
        for change in ({'service': 'unrelated'}, {'version': 'wrong'}, {'endpoints': []}, {'build_id': 'other'}):
            with self.subTest(change=change):
                old = dict(self.health)
                self.health.update(change)
                self.assertFalse(self.service.scan()['candidates'][0]['eligible'])
                self.health = old

    def test_invalid_database_schema_never_verified_or_saved(self):
        database = self.fixture()
        material = Mock()
        for content in ('not sqlite', 'wrong table', 'missing columns', 'without rowid'):
            with self.subTest(content=content):
                database.unlink()
                if content == 'not sqlite':
                    database.write_bytes(b'fictional non SQLite file')
                else:
                    with closing(sqlite3.connect(database)) as connection:
                        if content == 'wrong table':
                            connection.execute('CREATE TABLE unrelated (task_id TEXT)')
                        elif content == 'missing columns':
                            connection.execute('CREATE TABLE download_jobs (task_id TEXT)')
                        else:
                            connection.execute('CREATE TABLE download_jobs (task_id TEXT PRIMARY KEY, started_at TEXT, material_ids_json TEXT, items_json TEXT, state TEXT, failed INTEGER) WITHOUT ROWID')
                before = database.read_bytes()
                candidate = self.service.scan()['candidates'][0]
                self.assertFalse(candidate['eligible'])
                self.assertEqual(candidate['status'], 'unverified')
                self.assertTrue(any('download_jobs' in item for item in candidate['evidence']))
                with self.assertRaises(ImportFailure):
                    self.service.select({'candidateId': candidate['id']}, material)
                self.assertEqual(before, database.read_bytes())
        material.settings.assert_not_called()
        self.assertEqual(self.calls, [])

    def test_schema_changed_after_scan_blocks_save(self):
        database = self.fixture()
        candidate = self.service.scan()['candidates'][0]
        with closing(sqlite3.connect(database)) as connection:
            connection.execute('DROP TABLE download_jobs')
        material = Mock()
        with self.assertRaises(ImportFailure):
            self.service.select({'candidateId': candidate['id']}, material)
        material.settings.assert_not_called()

    def test_reparse_points_and_linked_parents_are_rejected(self):
        database = self.fixture()
        original = Path.lstat
        def reparse(path, *args, **kwargs):
            info = original(path, *args, **kwargs)
            if path == self.root:
                return Mock(st_file_attributes=0x400, st_mode=info.st_mode)
            return info
        with patch.object(Path, 'lstat', reparse):
            self.assertTrue(self.service._linked_path(database))
            self.assertEqual(self.service.scan()['candidates'], [])
        candidate = self.service.scan()['candidates'][0]
        material = Mock()
        with patch.object(Path, 'lstat', reparse), self.assertRaises(ImportFailure):
            self.service.select({'candidateId': candidate['id']}, material)
        material.settings.assert_not_called()

    def test_unavailable_health_retains_unverified_candidate(self):
        self.fixture()
        self.service.health_reader = Mock(side_effect=OSError('offline'))
        self.assertFalse(self.service.scan()['candidates'][0]['eligible'])

    def test_source_tests_and_deep_paths_excluded(self):
        self.fixture(self.root / 'tests')
        self.fixture(self.root / 'src')
        self.fixture(self.root / 'a' / 'b' / 'c' / 'd')
        self.assertEqual(self.service.scan()['candidates'], [])
        self.assertEqual(self.calls, [])

    def test_invalid_marker_never_probes(self):
        self.fixture()
        (self.root / 'runtime.json').write_text('{"port": 9999}', encoding='utf-8')
        self.assertFalse(self.service.scan()['candidates'][0]['eligible'])
        self.assertEqual(self.calls, [])

    def test_selected_directory_and_invalid_inputs(self):
        self.fixture()
        service = DiscoveryService(None, roots=[], health_reader=lambda p: self.health)
        self.assertTrue(service.scan({'directory': str(self.root)})['candidates'][0]['eligible'])
        for body in ([], {'directory': 'relative'}, {'directory': '//server/share'}):
            with self.assertRaises(ImportFailure):
                service.scan(body)

    def test_picker_cancel_and_error(self):
        with patch('control_console.discovery.os.name', 'nt'), patch('control_console.discovery.subprocess.run') as run:
            run.return_value.stdout = ''
            self.assertEqual(choose_local_path(), '')
            run.return_value.stdout = 'E:\\虚构文件\\任务.xlsx\r\n'
            self.assertEqual(choose_local_path('taskWorkbook'), 'E:\\虚构文件\\任务.xlsx')
            self.assertIn('-STA', run.call_args.args[0])
            expected = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32' / 'WindowsPowerShell' / 'v1.0' / 'powershell.exe'
            self.assertEqual(run.call_args.args[0][0], str(expected))
            run.side_effect = OSError('not available')
            with self.assertRaisesRegex(ImportFailure, '手动填写本地绝对路径'):
                choose_local_path()
        with self.assertRaises(ImportFailure):
            choose_local_path('unsupported')

    def test_select_only_cached_verified_id_and_rechecks(self):
        self.fixture()
        candidate = self.service.scan()['candidates'][0]
        material = Mock()
        material.settings.return_value = {'ok': True}
        self.assertTrue(self.service.select({'candidateId': candidate['id']}, material)['ok'])
        material.settings.assert_called_once_with(9988, candidate['database'])
        self.health['service'] = 'other'
        with self.assertRaises(ImportFailure):
            self.service.select({'candidateId': candidate['id']}, material)
        self.assertEqual(material.settings.call_count, 1)
        for body in ({}, {'candidateId': 'unknown'}, {'port': 9988, 'database': candidate['database']}):
            with self.assertRaises(ImportFailure):
                self.service.select(body, material)

    def test_select_rejects_changed_marker_or_replaced_database(self):
        database = self.fixture()
        candidate = self.service.scan()['candidates'][0]
        material = Mock()
        marker = self.root / 'runtime.json'
        original = marker.read_bytes()
        marker.write_bytes(original + b' ')
        with self.assertRaises(ImportFailure):
            self.service.select({'candidateId': candidate['id']}, material)
        marker.write_bytes(original)
        replacement = self.root / 'replacement.db'
        replacement.write_bytes(b'other')
        replacement.replace(database)
        with self.assertRaises(ImportFailure):
            self.service.select({'candidateId': candidate['id']}, material)
        material.settings.assert_not_called()

    def test_select_repeated_saves_use_only_cached_configuration(self):
        self.fixture()
        candidate = self.service.scan()['candidates'][0]
        material = Mock()
        for _ in range(2):
            self.service.select({'candidateId': candidate['id'], 'port': 1,
                                 'database': 'tampered.db'}, material)
        self.assertEqual(material.settings.call_count, 2)
        material.settings.assert_called_with(9988, candidate['database'])
        self.assertEqual(self.calls, [9988, 9988, 9988])

    def test_unverified_and_expired_candidates_cannot_overwrite_settings(self):
        self.fixture(marker=False)
        candidate = self.service.scan()['candidates'][0]
        material = Mock()
        with self.assertRaises(ImportFailure):
            self.service.select({'candidateId': candidate['id']}, material)
        material.settings.assert_not_called()
        self.service.roots = []
        self.service.scan()
        with self.assertRaises(ImportFailure):
            self.service.select({'candidateId': candidate['id']}, material)
        material.settings.assert_not_called()

    def test_change_during_health_recheck_cannot_save(self):
        self.fixture()
        candidate = self.service.scan()['candidates'][0]
        material = Mock()
        def changed(port):
            marker = self.root / 'runtime.json'
            marker.write_bytes(marker.read_bytes() + b' ')
            return self.health
        self.service.health_reader = changed
        with self.assertRaises(ImportFailure):
            self.service.select({'candidateId': candidate['id']}, material)
        material.settings.assert_not_called()


if __name__ == '__main__':
    unittest.main()
