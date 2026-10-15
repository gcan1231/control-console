"""007 HTTP checks use fictional reports and an ephemeral loopback server only."""
import http.client
import json
import threading
import unittest
from pathlib import Path
import test_editing as editingfixtures
from control_console.workbooks import task_workbook
from tools.local_server import create_server
from test_local_import import HEADERS, task, workbook
from control_console.importers import read_workbook


class PublishingHttpTests(unittest.TestCase):
    def setUp(self):
        self.fixture = editingfixtures.EditingTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        f = self.fixture
        p = f.prepare()
        f.service.gui(p['id'], f.body)
        f.reports(p)
        f.service.collect(p['id'])
        for kind in ('normal', 'speed'):
            f.service.verify(f.task['id'], kind, '虚构成片人工观看', True)
        self.server = create_server(0, f.store.root)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)
        self.body = {'taskIds': [f.task['id']], 'mode': 'both', 'ports': {'demo-a': 9001}}

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def request(self, route, body=None, headers=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=10)
        try:
            hdr = {'Content-Type': 'application/json', 'X-Console-Token': self.server.token}
            hdr.update(headers or {})
            conn.request('POST' if body is not None else 'GET', route,
                         None if body is None else json.dumps(body), hdr)
            response = conn.getresponse()
            return response.status, json.loads(response.read())
        finally:
            conn.close()

    def post(self, operation, body):
        status, value = self.request('/api/publishing/' + operation, body)
        self.assertEqual(status, 200, value)
        return value

    def six_ready(self):
        """Real fixture pipeline with fictional local reports for six accounts."""
        f = self.fixture
        extra = [task('demo-' + letter, '虚构账号' + letter) for letter in 'cdef']
        f.store.commit_import('tasks', 'fiction-extra.xlsx', workbook([HEADERS, *extra]), {})
        accounts = [['运营', '账号', '名称', 'UID', '合作', '类别']]
        accounts.extend([['虚构', 'demo-' + letter, name, 'D', 'D', '食品'] for letter, name in
                         [('a', '演示账号'), ('b', '另一账号'), *[(v, '虚构账号' + v) for v in 'cdef']]])
        f.store.commit_import('accounts', 'fiction-six.xlsx', workbook(accounts, hyperlink=False),
                              {'complete': True, 'sheet': 'Sheet1', 'headerRow': 1})
        tasks = f.store.state()['tasks']
        for t in tasks:
            if t['id'] == f.task['id']:
                continue
            material = f.material.prepare([t['id']])['package']
            item = f.fixture.item(material)
            f.fixture.jobs([item])
            f.material.collect(material['id'])
            f.material.confirm(t['id'], '虚构素材人工观看', True)
            p = f.service.prepare([t['id']])['package']
            f.service.gui(p['id'], f.body)
            f.reports(p)
            f.service.collect(p['id'])
            for kind in ('normal', 'speed'):
                f.service.verify(t['id'], kind, '虚构成片人工观看', True)
        return tasks

    def test_six_accounts_two_batches_five_isolated_slots(self):
        tasks = self.six_ready()
        body = {'taskIds': [t['id'] for t in reversed(tasks)], 'mode': 'both',
                'ports': {t['accountId']: 9100 + i for i, t in enumerate(tasks)}}
        package = self.post('prepare', body)['package']
        self.assertEqual([len([s for s in b['slots'] if s]) for b in package['batches']], [5, 1])
        slots = [s for b in package['batches'] for s in b['slots'] if s]
        self.assertEqual([s['accountId'] for s in slots], [t['accountId'] for t in tasks])
        self.assertEqual(len({s['workbookPath'] for s in slots}), 6)
        for slot, t in zip(slots, tasks):
            cells = read_workbook(Path(slot['workbookPath']).read_bytes())['sheets'][0]['cells']
            self.assertEqual([cells[(1, i)]['text'] for i in range(1, 17)], HEADERS)
            self.assertEqual(cells[(2, 4)]['text'], t['accountId'])
            self.assertEqual(cells[(2, 6)]['text'], t['fields'][5])
            self.assertEqual(cells[(2, 10)]['text'], t['fields'][9])
            self.assertFalse(any(row > 2 for row, _ in cells))

    def test_security_and_validation(self):
        for operation in ('prepare', 'handoff', 'collect', 'verify'):
            for headers in ({'Host': 'evil.invalid'}, {'Origin': 'https://evil.invalid'},
                            {'X-Console-Token': ''}, {'X-Console-Token': 'wrong'}):
                self.assertEqual(self.request('/api/publishing/' + operation, {}, headers)[0], 403)
        self.assertEqual(self.request('/api/publishing/state', headers={'Host': 'evil.invalid'})[0], 403)
        for update in ({'taskIds': []}, {'taskIds': ['missing']}, {'mode': 'invalid'},
                       {'ports': {}}, {'ports': {'demo-a': True}}, {'ports': {'demo-a': 0}}):
            self.assertEqual(self.request('/api/publishing/prepare', {**self.body, **update})[0], 422)
        for operation in ('handoff', 'collect', 'verify'):
            self.assertEqual(self.request('/api/publishing/' + operation, {})[0], 422)
        self.assertEqual(self.request('/api/publishing/run', {})[0], 404)

    def test_mode_requires_its_own_reviewed_output(self):
        with self.fixture.store.connection(write=True) as conn:
            task_id = self.fixture.task['id']
            record = json.loads(conn.execute('SELECT payload FROM editing_tasks WHERE task_id=?', (task_id,)).fetchone()[0])
            record['speed']['ready'] = False
            conn.execute('UPDATE editing_tasks SET payload=? WHERE task_id=?', (json.dumps(record), task_id))
        for mode in ('both', 'douyin'):
            self.assertEqual(self.request('/api/publishing/prepare', {**self.body, 'mode': mode})[0], 422)
        package = self.post('prepare', {**self.body, 'mode': 'qianchuan'})['package']
        self.assertEqual(set(package['tasks'][task_id]['videos']), {'normal'})

    def test_reported_requires_manual_verification_and_preserves_fields(self):
        package = self.post('prepare', self.body)['package']
        self.assertTrue(self.post('prepare', self.body)['reused'])
        slot = package['batches'][0]['slots'][0]
        self.assertEqual(slot['port'], 9001)
        self.assertEqual(package['tasks'][self.fixture.task['id']]['fields'][9], '7001')
        pid = {'packageId': package['id']}
        self.assertEqual(self.request('/api/publishing/handoff', {**pid, 'confirmed': False})[0], 422)
        self.post('handoff', {**pid, 'confirmed': True})
        fields = list(package['tasks'][self.fixture.task['id']]['fields'])
        fields[12:14] = ['已发布', '计划已发布']
        Path(slot['workbookPath']).write_bytes(task_workbook([{'fields': fields}]))
        self.post('collect', {**pid, 'confirmedClosed': True})
        state = self.request('/api/publishing/state')[1]
        record = state['publishingTasks'][self.fixture.task['id']]
        self.assertEqual(record['state'], 'reported')
        self.assertIsNone(record['verification'])
        self.assertTrue(state['tasks'][0]['editing'])
        verify = {**pid, 'taskId': self.fixture.task['id'], 'result': 'verified', 'note': '虚构人工核验', 'confirmed': True}
        self.post('verify', verify)
        self.assertTrue(self.post('verify', verify)['reused'])
        self.assertEqual(self.request('/api/publishing/verify', {**verify, 'result': 'failed'})[0], 422)
        self.assertEqual(self.request('/api/publishing/prepare', {**self.body, 'mode': 'douyin'})[0], 422)
        self.assertEqual(self.fixture.store.state()['tasks'][0]['fields'][12:14], ['', ''])

    def test_tampering_and_unknown_do_not_retry(self):
        package = self.post('prepare', self.body)['package']
        slot = package['batches'][0]['slots'][0]
        pid = {'packageId': package['id']}
        fields = list(package['tasks'][self.fixture.task['id']]['fields'])
        changed = list(fields)
        changed[5] = '9000000000000000000'
        Path(slot['workbookPath']).write_bytes(task_workbook([{'fields': changed}]))
        self.assertEqual(self.request('/api/publishing/handoff', {**pid, 'confirmed': True})[0], 422)
        Path(slot['workbookPath']).write_bytes(task_workbook([{'fields': fields}]))
        self.post('handoff', {**pid, 'confirmed': True})
        self.post('collect', {**pid, 'confirmedClosed': True})
        self.assertEqual(self.request('/api/publishing/state')[1]['publishingTasks'][self.fixture.task['id']]['state'], 'unknown')
        self.assertEqual(self.request('/api/publishing/prepare', {**self.body, 'ports': {'demo-a': 9002}})[0], 422)
        self.assertEqual(len(self.request('/api/publishing/state')[1]['publishingPackages']), 1)


if __name__ == '__main__':
    unittest.main()
