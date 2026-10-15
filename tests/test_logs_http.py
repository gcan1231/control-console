"""006B HTTP checks: only fictional files and an ephemeral loopback server."""
import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.parse import urlencode
from tools.local_server import create_server


class LogsHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.server = create_server(0, self.root / 'runtime')
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)

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

    def config(self, **overrides):
        return dict(software='material', label='虚构素材', path=str(self.root / 'fiction.log'),
                    encoding='utf-8', enabled=True, **overrides)

    def test_security_and_invalid_configuration(self):
        for route in ('/api/logs/configure', '/api/logs/poll'):
            for headers in ({'Host': 'evil.invalid'}, {'Origin': 'https://evil.invalid'},
                            {'X-Console-Token': ''}, {'X-Console-Token': 'wrong'}):
                self.assertEqual(self.request(route, {}, headers)[0], 403, (route, headers))
        for route in ('/api/logs/state', '/api/logs/summary'):
            self.assertEqual(self.request(route, headers={'Host': 'evil.invalid'})[0], 403)
        valid = self.config()
        for key, values in {'software': ['bad', [], {}], 'encoding': ['gbk', []],
                            'path': ['relative.log', 'https://invalid/log', str(self.root), []],
                            'enabled': ['true', 1], 'rotationGlob': ['../*', '**', []],
                            'label': [[], {}]}.items():
            for value in values:
                self.assertEqual(self.request('/api/logs/configure', {**valid, key: value})[0], 422, (key, value))
        origin = f'http://127.0.0.1:{self.server.server_port}'
        self.assertEqual(self.request('/api/logs/configure', valid, {'Origin': origin})[0], 200)

    def test_filters_encoding_pagination_and_read_only_sources(self):
        files = []
        for software, encoding in [('material', 'utf-8'), ('editing', 'utf-8'), ('publish', 'utf-16')]:
            path = self.root / (software + '.log')
            path.write_text(''.join(f'[INFO] 虚构中文{software} {n}\n' for n in range(55)) + '[ERROR] <img src=x onerror=alert(1)>\n', encoding=encoding)
            files.append((path, path.read_bytes()))
            self.assertEqual(self.request('/api/logs/configure', dict(software=software, path=str(path), encoding=encoding, enabled=True))[0], 200)
        self.assertEqual(self.request('/api/logs/poll', {})[0], 200)
        _, state = self.request('/api/logs/state')
        self.assertEqual((state['total'], len(state['events']), state['pageSize']), (168, 50, 50))
        for query, total in [({'software':'publish'},56), ({'level':'ERROR'},3), ({'search':'虚构中文'},165)]:
            self.assertEqual(self.request('/api/logs/state?' + urlencode(query))[1]['total'], total)
        _, second = self.request('/api/logs/state?software=material&page=2')
        self.assertEqual(len(second['events']), 6)
        self.request('/api/logs/poll', {})
        self.assertEqual(self.request('/api/logs/state')[1]['total'],168)
        self.assertEqual(self.request('/api/logs/summary?taskId=untrusted')[1]['tasks'], {})
        for path, original in files:
            self.assertEqual(path.read_bytes(), original)

    def test_invalid_query_and_disabled_default(self):
        for query in ('page=no','page=0','page=-1','page=1000001','software=bad','level=bad'):
            self.assertEqual(self.request('/api/logs/state?' + query)[0],422, query)
        path = self.root / 'fiction.log'
        path.write_text('[INFO] 不启用不读取\n', encoding='utf-8')
        self.request('/api/logs/configure', {'software':'editing','path':str(path)})
        self.request('/api/logs/poll', {})
        state = self.request('/api/logs/state')[1]
        self.assertFalse(state['sources'][0]['enabled'])
        self.assertEqual(state['total'], 0)

if __name__ == '__main__':
    unittest.main()
