"""007 external intake HTTP checks: temporary fictional files and loopback only."""
import http.client
import json
import threading
import unittest
from unittest.mock import patch

import test_external_intake as fixtures
from test_local_import import HEADERS, task, workbook
from tools.local_server import create_server


class ExternalHttpTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ExternalIntakeTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.start()
        self.addCleanup(self.stop)

    def start(self):
        self.server = create_server(0, self.fixture.store.root)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def request(self, path, body=None, headers=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=10)
        try:
            hdr = {'Content-Type': 'application/json', 'X-Console-Token': self.server.token,
                   'Origin': f'http://127.0.0.1:{self.server.server_port}'}
            hdr.update(headers or {})
            conn.request('GET' if body is None else 'POST', path,
                         None if body is None else json.dumps(body), hdr)
            response = conn.getresponse()
            return response.status, json.loads(response.read())
        finally:
            conn.close()

    def preview(self, kind='materials', ids=None):
        return self.request('/api/external/preview', {
            'taskIds': ids or [self.fixture.task['id']], 'kind': kind,
            'root': str(self.fixture.root)})

    def accept(self, preview):
        return self.request('/api/external/accept', {'previewId': preview['id'],
            'confirmed': True, 'note': '虚构逐项人工核验'})

    def test_host_origin_token_guards_both_mutations(self):
        for route in ('preview', 'accept'):
            for headers in ({'Host': 'evil.invalid'}, {'Origin': 'https://evil.invalid'},
                            {'X-Console-Token': ''}, {'X-Console-Token': 'wrong'}):
                with self.subTest(route=route, headers=headers):
                    self.assertEqual(self.request('/api/external/' + route, {}, headers)[0], 403)
        self.assertEqual(self.request('/api/external/state')[1]['externalPreviews'], [])

    def test_accept_is_independent_persistent_and_preserves_original_contract(self):
        f = self.fixture
        source = f.product / 'a.mp4'
        f.video(source)
        original = source.read_bytes()
        fields = list(f.task['fields'])
        snapshots = {p: p.read_bytes() for p in (f.store.root / 'sources').glob('*.xlsx')}
        with patch('subprocess.Popen', side_effect=AssertionError('Must not launch business software')):
            status, response = self.preview()
            self.assertEqual(status, 200)
            self.assertEqual(response['preview']['state'], 'unverified')
            self.assertEqual(self.accept(response['preview'])[0], 200)
        self.assertEqual(f.f.calls, [])
        self.stop()
        self.start()
        status, state = self.request('/api/state')
        self.assertEqual(status, 200)
        self.assertTrue(state['externalTasks'][f.task['id']]['materials']['manual'])
        self.assertEqual(state['materialTasks'], {})
        self.assertEqual(state['editingTasks'], {})
        saved = next(t for t in state['tasks'] if t['id'] == f.task['id'])
        self.assertEqual(saved['fields'], fields)
        self.assertEqual(len(saved['fields']), len(HEADERS))
        self.assertEqual(saved['fields'][9], '7001')
        self.assertEqual(source.read_bytes(), original)
        self.assertEqual({p: p.read_bytes() for p in snapshots}, snapshots)
        self.assertTrue(self.accept(response['preview'])[1]['reused'])

    def test_preview_restarts_and_file_change_blocks_acceptance(self):
        f = self.fixture
        f.video(f.product / 'a.mp4')
        status, response = self.preview()
        self.assertEqual(status, 200)
        self.stop()
        self.start()
        f.video(f.product / 'new.mp4')
        self.assertEqual(self.accept(response['preview'])[0], 422)
        self.assertEqual(self.request('/api/external/state')[1]['externalAcceptances'], [])

    def test_finished_qualification_does_not_imply_materials_or_speed(self):
        f = self.fixture
        f.video(f.product / '成片' / 'normal.mp4')
        status, response = self.preview('finished')
        self.assertEqual(status, 200)
        self.assertEqual(self.accept(response['preview'])[0], 200)
        state = self.request('/api/state')[1]
        external = state['externalTasks'][f.task['id']]
        self.assertNotIn('materials', external)
        self.assertTrue(external['finished']['normal']['ready'])
        self.assertFalse(external['finished']['speed']['ready'])
        self.assertEqual(state['editingTasks'], {})

    def test_exact_dated_task_selection_warns_history_and_rejects_shared_selection(self):
        f = self.fixture
        later = task()
        later[0] = '2026-10-14'
        f.store.commit_import('tasks', 'later-fiction.xlsx', workbook([HEADERS, later]), {})
        dated = next(t for t in f.store.state()['tasks'] if t['fields'][0] == later[0])
        f.video(f.product / 'a.mp4')
        status, response = self.preview(ids=[dated['id']])
        self.assertEqual(status, 200)
        row = response['preview']['rows'][0]
        self.assertEqual(row['taskId'], dated['id'])
        self.assertIn({'taskId': f.task['id'], 'date': f.task['fields'][0]}, row['otherTaskMatches'])
        self.assertEqual(self.preview(ids=[dated['id'], f.task['id']])[0], 422)
        self.assertEqual(self.accept(response['preview'])[0], 200)
        self.assertEqual(set(self.request('/api/state')[1]['externalTasks']), {dated['id']})

    def test_materials_then_finished_and_no_original_stage_reexecution(self):
        f = self.fixture
        f.video(f.product / 'a.mp4')
        self.assertEqual(self.accept(self.preview()[1]['preview'])[0], 200)
        self.assertEqual(self.request('/api/material/prepare', {'taskIds': [f.task['id']]})[0], 422)
        f.video(f.product / '成片' / 'a.mp4')
        self.assertEqual(self.accept(self.preview('finished')[1]['preview'])[0], 200)
        self.assertEqual(self.preview()[0], 422)
        self.assertEqual(self.preview('finished')[0], 422)
        self.assertEqual(self.request('/api/editing/prepare', {'taskIds': [f.task['id']]})[0], 422)
        external = self.request('/api/state')[1]['externalTasks'][f.task['id']]
        self.assertTrue(external['materials']['ready'])
        self.assertTrue(external['finished']['normal']['ready'])

    def test_native_ready_material_allows_finished_only_after_report_confirmation(self):
        f = self.fixture
        package = f.f.package()
        item = f.f.item(package)
        f.f.jobs([item])
        f.f.service.send(package['id'])
        f.video(f.product / '成片' / 'a.mp4')
        self.assertEqual(self.preview('finished')[0], 422)
        f.f.service.collect(package['id'])
        self.assertEqual(f.f.record()['state'], 'ready')
        self.assertTrue(f.f.record()['confirmation'])
        self.assertEqual(self.accept(self.preview('finished')[1]['preview'])[0], 200)


if __name__ == '__main__':
    unittest.main()
