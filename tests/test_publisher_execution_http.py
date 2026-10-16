"""Native publisher HTTP integration, fictional adapters and ephemeral loopback only."""
import json
import http.client
from pathlib import Path
import socket
import time
import unittest
import test_publishing_http as publishing_http_fixture
from test_local_import import HEADERS
from control_console.importers import read_workbook


class FakeAdapter:
    def __init__(self):self.calls=[];self.unknown=False
    async def probe_browser(self,port):
        return {'douyin':{'accountId':'demo-a','uid':'fiction-uid','url':'http://fiction.invalid'},'qianchuan':{'accountId':'demo-a','advertiserId':'fiction-ad','url':'http://fiction.invalid'}}
    def validate_template(self,platform,parameters):pass
    async def execute_item(self,port,binding,template,item,checkpoint):
        self.calls.append(item['id']);checkpoint('before_submit')
        if self.unknown:raise RuntimeError('fictional lost submission result')
        platform_id='fiction-'+item['id'];checkpoint('submitted',{'platformId':platform_id})
        if item['platform']=='qianchuan':checkpoint('closing',{'platformId':platform_id})
        return {'state':'closed' if item['platform']=='qianchuan' else 'published','platformId':platform_id,'evidence':{'fictional':True}}
    async def reconcile_item(self,port,binding,item):return {'state':'unknown','evidence':{'fictional':True}}


class NativePublishingHttpTests(unittest.TestCase):
    setUp=publishing_http_fixture.PublishingHttpTests.setUp
    stop=publishing_http_fixture.PublishingHttpTests.stop
    request=publishing_http_fixture.PublishingHttpTests.request
    post=publishing_http_fixture.PublishingHttpTests.post

    def prepare_native(self):
        self.adapter=FakeAdapter();self.engine=self.server.publisher_execution;self.engine.browser=self.adapter;self.engine.launch_workers=False
        self.package=self.post('prepare',self.body)['package'];self.pid={'packageId':self.package['id']}
        self.post('execution/probe',{'port':9001})
        self.post('execution/bind',{'accountId':'demo-a','port':9001,'uid':'fiction-uid','advertiserId':'fiction-ad','confirmed':True})
        for platform,parameters in [('douyin',{'coverMode':'recommended'}),('qianchuan',{'dailyBudget':'100','bidStrategy':'fiction','roi':'2','target':'fiction','placement':'fiction'})]:
            self.post('execution/template',{'accountId':'demo-a','platform':platform,'parameters':parameters,'confirmed':True})
        return self.package

    def start_native(self):return self.post('execution/start',{**self.pid,'batchIndex':1,'confirmed':True})['run']
    def finish_native(self):
        run=self.start_native();self.engine.run_account(run['id'],'demo-a');return run
    def verify_body(self):return {**self.pid,'taskId':self.fixture.task['id'],'result':'verified','note':'fictional manual verification','confirmed':True}

    def test_native_reported_requires_manual_verification_and_preserves_xlsx(self):
        package=self.prepare_native();workbook=Path(package['batches'][0]['slots'][0]['workbookPath']);before=workbook.read_bytes();source=self.fixture.store.state()['tasks'][0]['fields'][:]
        original_books={p:p.read_bytes() for p in self.fixture.store.root.rglob('*.xlsx')}
        cells=read_workbook(before)['sheets'][0]['cells'];self.assertEqual(cells[(2,6)]['text'],self.fixture.task['fields'][5]);self.assertEqual(cells[(2,10)]['text'],'7001')
        self.assertEqual(HEADERS,[cells[(1,column)]['text'] for column in range(1,17)]);self.assertEqual(16,max(column for row,column in cells))
        self.finish_native();self.post('collect_native',{**self.pid,'confirmed':True})
        record=self.request('/api/publishing/state')[1]['publishingTasks'][self.fixture.task['id']];self.assertEqual('reported',record['state']);self.assertIsNone(record['verification'])
        self.post('verify',self.verify_body());self.assertEqual('verified',self.request('/api/publishing/state')[1]['publishingTasks'][self.fixture.task['id']]['state'])
        self.assertEqual(before,workbook.read_bytes());self.assertEqual(source,self.fixture.store.state()['tasks'][0]['fields']);self.assertEqual(['',''],self.fixture.store.state()['tasks'][0]['fields'][12:14])
        self.assertEqual(original_books,{p:p.read_bytes() for p in original_books})

    def test_security_on_every_native_endpoint(self):
        for operation in ('probe','bind','template','start','control','reconcile'):
            for headers in ({'X-Console-Token':''},{'X-Console-Token':'wrong'},{'Origin':'https://evil.invalid'},{'Host':'evil.invalid'}):
                self.assertEqual(403,self.request('/api/publishing/execution/'+operation,{},headers)[0])
        self.assertEqual(403,self.request('/api/publishing/execution/state',headers={'Host':'evil.invalid'})[0])
        self.assertEqual(403,self.request('/api/publishing/collect_native',{}, {'X-Console-Token':''})[0])

    def test_rejected_split_or_incomplete_body_returns_bounded_json_without_execution(self):
        adapter=FakeAdapter();self.server.publisher_execution.browser=adapter
        before=self.request('/api/publishing/execution/state')[1]
        publishing_before=self.request('/api/publishing/state')[1]
        port=self.server.server_port;self.assertNotEqual(port,8861)
        for body in (b'{}',b'{'):
            with self.subTest(body=body), socket.create_connection(('127.0.0.1',port),timeout=2) as connection:
                connection.settimeout(2)
                headers=(f'POST /api/publishing/execution/start HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n'
                         'Content-Type: application/json\r\nX-Console-Token: wrong\r\n'
                         'Content-Length: 2\r\nConnection: close\r\n\r\n')
                started=time.monotonic();connection.sendall(headers.encode('ascii'))
                # Deliver the body separately while the rejected request is draining.
                time.sleep(.03);connection.sendall(body)
                response=http.client.HTTPResponse(connection);response.begin()
                self.assertEqual(403,response.status)
                result=json.loads(response.read());response.close()
                self.assertFalse(result['ok']);self.assertEqual('token',result['error']['code'])
                self.assertLess(time.monotonic()-started,1.5,'Rejected partial body must not wait indefinitely')
        self.assertEqual([],adapter.calls)
        self.assertEqual(before,self.request('/api/publishing/execution/state')[1])
        self.assertEqual(publishing_before,self.request('/api/publishing/state')[1])

    def test_confirmation_and_double_execution_are_blocked(self):
        self.prepare_native()
        for operation,body in [('execution/bind',{'accountId':'demo-a','port':9001,'uid':'fiction-uid'}),('execution/template',{'accountId':'demo-a','platform':'douyin','parameters':{'coverMode':'recommended'}}),('execution/start',{**self.pid,'batchIndex':1}),('collect_native',self.pid)]:self.assertEqual(422,self.request('/api/publishing/'+operation,body)[0])
        self.assertEqual([],self.adapter.calls);self.start_native()
        for operation,body in [('handoff',{**self.pid,'confirmed':True}),('collect',{**self.pid,'confirmedClosed':True})]:self.assertEqual(422,self.request('/api/publishing/'+operation,body)[0])
        self.assertEqual([],self.adapter.calls)

    def test_old_handoff_blocks_native(self):
        self.prepare_native();self.post('handoff',{**self.pid,'confirmed':True});self.assertEqual(422,self.request('/api/publishing/execution/start',{**self.pid,'batchIndex':1,'confirmed':True})[0]);self.assertEqual([],self.adapter.calls)

    def test_unknown_never_retries_or_claims_reported(self):
        self.prepare_native();self.adapter.unknown=True;run=self.finish_native();calls=self.adapter.calls[:]
        repeated=self.post('execution/start',{**self.pid,'batchIndex':1,'confirmed':True});self.assertTrue(repeated['reused']);self.engine.run_account(run['id'],'demo-a');self.assertEqual(calls,self.adapter.calls)
        self.assertEqual(422,self.request('/api/publishing/collect_native',{**self.pid,'confirmed':True})[0]);self.assertEqual(0,self.request('/api/publishing/execution/state')[1]['runs'][0]['aggregate']['reported'])
        self.post('execution/reconcile',{'runId':run['id'],'itemId':run['items'][0]['id']});self.assertEqual(calls,self.adapter.calls)

    def test_video_tampering_after_receipt_blocks_verify(self):
        package=self.prepare_native();self.finish_native();self.post('collect_native',{**self.pid,'confirmed':True})
        path=Path(package['tasks'][self.fixture.task['id']]['videos']['speed'][0]['copy']['path']);path.write_bytes(b'fictional tampered video');self.assertEqual(422,self.request('/api/publishing/verify',self.verify_body())[0])

    def test_receipt_snapshot_tampering_blocks_verify(self):
        self.prepare_native();self.finish_native();self.post('collect_native',{**self.pid,'confirmed':True});record=self.request('/api/publishing/state')[1]['publishingTasks'][self.fixture.task['id']];Path(record['receiptSnapshot']['path']).write_bytes(b'{}');self.assertEqual(422,self.request('/api/publishing/verify',self.verify_body())[0])

if __name__=='__main__':unittest.main()
