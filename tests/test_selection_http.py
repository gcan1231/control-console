"""Selection HTTP checks use fictional sources and an ephemeral loopback server."""
import http.client
import json
import threading
import unittest
from urllib.parse import urlencode

import test_selection as fixtures
import test_selection_references as reference_fixtures
from control_console.importers import parse_import
from tools.local_server import create_server


class SelectionHttpTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.SelectionTests()
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

    def request(self, path, body=None, headers=None, raw=False):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=10)
        self.addCleanup(conn.close)
        hdr = {'Content-Type':'application/json', 'X-Console-Token':self.server.token}
        hdr.update(headers or {})
        conn.request('POST' if body is not None else 'GET', path,
                     None if body is None else json.dumps(body), hdr)
        response = conn.getresponse()
        content = response.read()
        return response.status, content if raw else json.loads(content)

    def test_new_post_boundaries_and_bad_parameters(self):
        for operation in ('add','update','remove','target','preset','apply-preset','preview','generate'):
            url = '/api/selection/' + operation
            for headers in ({'Host':'evil.invalid'}, {'Origin':'https://evil.invalid'}, {'X-Console-Token':''}):
                self.assertEqual(self.request(url, {}, headers)[0], 403)
            self.assertNotEqual(self.request(url, {})[0], 500, operation)
            for key in ('accountId','productId','draftId','presetId','id','editId','token'):
                for value in ([], {}):
                    self.assertEqual(self.request(url, {key:value})[0], 422, (operation,key,value))
        for query in ('page=no', 'page=-1', 'page=0', 'page=999999999999999999999999', 'date=bad', 'accountId=missing', ''):
            self.assertNotEqual(self.request('/api/selection/products?'+query)[0], 500, query)
        for url in ('/api/selection/product', '/api/selection/export?date=bad'):
            self.assertNotEqual(self.request(url)[0], 500)

    def test_pagination_append_restart_idempotence_and_export(self):
        f = self.fixture
        f.sources('products', [{**f.goods, 'row':n+2, 'productId':str(9000000000000000000+n)} for n in range(55)])
        status, page = self.request('/api/selection/products?accountId=fiction-a&range=all&page=2')
        self.assertEqual(status, 200)
        self.assertEqual((page['total'], len(page['rows']), page['pageSize']), (55,5,50))
        item = page['rows'][0]['id']
        self.assertEqual(self.request('/api/selection/add', {'accountId':'fiction-a','productId':item,'date':'2026-10-14'})[0], 200)
        _, preview = self.request('/api/selection/preview', {'accountId':'fiction-a','date':'2026-10-14'})
        _, first = self.request('/api/selection/generate', {'token':preview['token'],'confirmed':True})
        with f.store.connection() as c:
            before = [dict(r) for r in c.execute('SELECT * FROM tasks')]
        self.stop(); self.start()
        self.assertTrue(self.request('/api/selection/generate', {'token':preview['token'],'confirmed':True})[1]['reused'])
        self.request('/api/selection/add', {'accountId':'fiction-a','productId':item,'date':'2026-10-15'})
        _, preview = self.request('/api/selection/preview', {'accountId':'fiction-a','date':'2026-10-15'})
        self.assertEqual(len(preview['rows'][0]['fields']),16)
        self.assertEqual(self.request('/api/selection/generate', {'token':preview['token'],'confirmed':True})[0],200)
        with f.store.connection() as c:
            self.assertEqual(before[0],dict(c.execute('SELECT * FROM tasks WHERE id=?',(first['taskIds'][0],)).fetchone()))
        status, content = self.request('/api/selection/export?date=2026-10-15&accountId=fiction-a',raw=True)
        self.assertEqual(status,200)
        fields = parse_import('tasks',content,{})['records'][0]['fields']
        self.assertEqual(len(fields),16)
        self.assertEqual(fields[5],page['rows'][0]['productId'])
        self.assertEqual(fields[9:], ['']*7)

    def test_confirmed_source_year_survives_refresh_without_source_write(self):
        source = reference_fixtures.SelectionReferenceTests()
        source.setUp()
        self.addCleanup(source.doCleanups)
        source.product()
        before = source.path.read_bytes()
        self.server.workbench.register('products', str(source.path), {'statisticYear':2026})
        self.server.workbench.refresh_sources(force=True)
        with self.fixture.store.connection() as c:
            row = c.execute("SELECT options,payload FROM sources WHERE kind='products' AND active=1").fetchone()
        self.assertEqual(json.loads(row['options'])['statisticYear'],2026)
        self.assertEqual(json.loads(row['payload'])[0]['statisticDate'],'2026-10-14')
        self.assertEqual(source.path.read_bytes(),before)

    def test_sales_query_protocol_and_metadata(self):
        f=self.fixture
        f.sources('products',[{**f.goods,'row':n+2,'productId':str(9000000000000000000+n),
            'statisticDate':'2026-10-15' if n<30 else '2026-10-14',
            'totalSales':'大于1万' if n<54 else 'all','dailySales':'10~20' if n<30 else ''} for n in range(55)])
        def catalog(**query):
            return self.request('/api/selection/products?'+urlencode({'accountId':'fiction-a',**query}))
        status,page=catalog(page=2)
        self.assertEqual((status,page['total'],len(page['rows'])),(200,55,5))
        self.assertEqual(catalog(range='latest')[1]['total'],30)
        status,filtered=catalog(totalSales='raw:大于1万',dailySales='missing',range='2026-10-14',search='虚构')
        self.assertEqual((status,filtered['total']),(200,24))
        self.assertEqual(filtered['salesOptions'],page['salesOptions'])
        self.assertIn({'value':'raw:all','label':'all'},page['salesOptions']['totalSales'])
        self.assertEqual(catalog(totalSales='raw:all')[1]['total'],1)
        for query in ({'totalSales':'大于1万'},{'dailySales':'raw:'},{'totalSales':'[]'},{'range':'bad'}):
            self.assertEqual(catalog(**query)[0],422)


if __name__ == '__main__':
    unittest.main()
