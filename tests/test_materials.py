"""005 tests use a fake loopback preview API and isolated fictional reports."""
from contextlib import closing
import hashlib
import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from test_local_import import workbook,task,HEADERS,PID,rewrite_package
from control_console.store import ConsoleStore
from control_console.materials import MaterialService
from control_console.importers import ImportFailure,read_workbook

class MaterialsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.store=ConsoleStore(self.root/'runtime');self.service=MaterialService(self.store)
        second=task('demo-b','另一账号');source=rewrite_package(workbook([HEADERS,task(),second]),{'xl/worksheets/sheet1.xml':lambda x:x.replace('hyperlink ref="H2"','hyperlink ref="F2"')});self.store.commit_import('tasks','fiction.xlsx',source,{})
        self.evidence();self.tasks=self.store.state()['tasks'];self.calls=[];self.response={'row_number':2,'task_id':'fake-software-row','items':[],'total':0}
        owner=self
        class Fake(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                owner.calls.append(('POST',self.path));data=json.loads(self.rfile.read(int(self.headers['Content-Length'])));self.reply(data)
            def do_GET(self):owner.calls.append(('GET',self.path));self.reply(owner.response)
            def reply(self,data):
                body=json.dumps({'ok':True,'data':data}).encode();self.send_response(200);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        self.fake=ThreadingHTTPServer(('127.0.0.1',0),Fake);self.thread=threading.Thread(target=self.fake.serve_forever,daemon=True);self.thread.start();self.addCleanup(self.stop)
        self.db=self.root/'fake-material.db'
        with closing(sqlite3.connect(self.db)) as c, c:c.execute('CREATE TABLE download_jobs (task_id TEXT,started_at TEXT,state TEXT,failed INTEGER,material_ids_json TEXT,items_json TEXT)')
        self.service.settings(self.fake.server_port,str(self.db))

    def stop(self):self.fake.shutdown();self.fake.server_close();self.thread.join()
    def evidence(self):
        self.store.commit_import('products','p.xlsx',workbook([['ID','类别'],[PID,'食品']],hyperlink=False),{'complete':True,'sheets':[{'sheet':'Sheet1','headerRow':1,'idColumn':'A','categoryColumn':'B','categoryFromSheet':False}]})
        self.store.commit_import('accounts','a.xlsx',workbook([['运营','账号','名称','UID','合作','类别'],['演示','demo-a','演示账号','D','D','食品'],['演示','demo-b','另一账号','D','D','食品']],hyperlink=False),{'complete':True,'sheet':'Sheet1','headerRow':1})
    def package(self,index=0):return self.service.prepare([self.tasks[index]['id']])['package']
    def record(self,index=0):return self.service.state()['materialTasks'][self.tasks[index]['id']]
    def item(self,p,mid='m1',name='fake.mp4',payload=b'\x00\x00\x00\x18ftypisomFAKE-DATA'):
        directory=Path(p['rows'][0]['outputDir']);directory.mkdir(parents=True,exist_ok=True);file=directory/name;file.write_bytes(payload)
        return {'material_id':mid,'aweme_id':'fake-'+mid,'state':'done','output_result':{'dir':str(directory),'items':[{'name':name,'state':'done','aweme_id':'fake-'+mid,'bytes':len(payload)}]}}
    def jobs(self,items,ids=None,state='done',failed=0,append=False,bad_json=False):
        previous=self.response['items'] if append else []
        by_id={i['material_id']:i for i in previous}
        by_id.update({i['material_id']:{'material_id':i['material_id'],'aweme_id':i.get('aweme_id'),'eligibility':{'allowed':True},'title':'虚构候选'} for i in items})
        self.response.update(items=list(by_id.values()),total=len(by_id))
        with closing(sqlite3.connect(self.db)) as c, c:
            if not append:c.execute('DELETE FROM download_jobs')
            c.execute('INSERT INTO download_jobs VALUES (?,?,?,?,?,?)',('fake-software-row','2026-'+str(c.execute('SELECT COUNT(*) FROM download_jobs').fetchone()[0]),state,failed,'bad-json' if bad_json else json.dumps(ids or [i['material_id'] for i in items]),json.dumps(items)))
    def test_workbook_identity_isolation_and_prepare_idempotence(self):
        a=self.package();b=self.package(1);self.assertNotEqual(a['exportRoot'],b['exportRoot']);self.assertNotEqual(a['rows'][0]['outputDir'],b['rows'][0]['outputDir']);self.assertEqual(a['rows'][0]['productId'],PID)
        self.assertIsNotNone(self.tasks[0]['cellMeta'][5]['hyperlink'])
        book=read_workbook(Path(a['workbook']).read_bytes());cells=book['sheets'][0]['cells'];self.assertEqual([cells[(1,i)]['text'] for i in range(1,17)],HEADERS)
        self.assertEqual([cells.get((2,i),{'text':''})['text'] for i in range(1,17)],self.tasks[0]['fields']);self.assertIsNone(cells[(2,6)]['hyperlink']);self.assertEqual(a['rows'][0]['taskId'],self.tasks[0]['id']);self.assertEqual(a['rows'][0]['sourceRow'],2)
        before=Path(a['workbook']).read_bytes();again=self.service.prepare([self.tasks[0]['id']]);self.assertTrue(again['reused']);self.assertEqual(again['package']['id'],a['id']);self.assertEqual(Path(a['workbook']).read_bytes(),before)
        try:import openpyxl
        except ImportError:self.skipTest('Independent openpyxl consumer not installed; no dependency installed')
        independent=openpyxl.load_workbook(a['workbook'],read_only=True,data_only=False)
        try:
            self.assertEqual(independent.sheetnames,['Sheet1']);rows=list(independent['Sheet1'].values)
            self.assertEqual(list(rows[0]),HEADERS);self.assertEqual(len(rows),2);self.assertEqual(rows[1][5],PID);self.assertEqual(rows[1][9],'7001');self.assertEqual(rows[1][3],'demo-a')
        finally:independent.close()
        with self.assertRaises(ImportFailure):self.service.prepare([t['id'] for t in self.tasks])
    def test_gates_reject_failed_category_and_reported(self):
        with self.store.connection(write=True) as c:c.execute("UPDATE sources SET complete=0 WHERE kind='products'")
        with self.assertRaises(ImportFailure):self.package()

    def test_duplicate_product_and_overlapping_package_rejected(self):
        same=task();same[0]='2026-10-14';different=task(product='9000000000000000002');different[0]='2026-10-15'
        self.store.commit_import('tasks','more-fiction.xlsx',workbook([HEADERS,same,different]),{})
        self.store.commit_import('products','both-products.xlsx',workbook([['ID','类别'],[PID,'食品'],['9000000000000000002','食品']],hyperlink=False),{'complete':True,'sheets':[{'sheet':'Sheet1','headerRow':1,'idColumn':'A','categoryColumn':'B','categoryFromSheet':False}]})
        tasks=self.store.state()['tasks'];same_task=next(t for t in tasks if t['fields'][0]=='2026-10-14');different_task=next(t for t in tasks if t['fields'][0]=='2026-10-15')
        with self.assertRaises(ImportFailure) as e:self.service.prepare([self.tasks[0]['id'],same_task['id']])
        self.assertEqual(e.exception.code,'material_product')
        self.package()
        with self.assertRaises(ImportFailure) as e:self.service.prepare([self.tasks[0]['id'],different_task['id']])
        self.assertEqual(e.exception.code,'material_overlap')
        self.evidence()
        with self.store.connection(write=True) as c:c.execute("UPDATE tasks SET stage='reported' WHERE id=?",(self.tasks[0]['id'],))
        with self.assertRaises(ImportFailure):self.package()
    def test_preview_get_allowlist_and_readonly_database(self):
        p=self.package();self.service.send(p['id']);before=self.db.read_bytes();self.service.collect(p['id']);self.assertEqual(self.db.read_bytes(),before);self.assertEqual(self.record()['state'],'review')
        self.assertEqual(self.calls[0],('POST','/api/integration/task-table'));self.assertTrue(all(method=='GET' and route.startswith('/api/task-table/row/materials?') for method,route in self.calls[1:]));self.assertEqual(len(self.calls),2)
        with self.assertRaises(ImportFailure):self.service.confirm(self.tasks[0]['id'],'checked',True)
    def test_report_failure_cases(self):
        p=self.package()
        cases=['badjson','running','partial','missing-output','wrong-account','traversal','empty','badhead','badsize']
        for case in cases:
            with self.subTest(case=case):
                item=self.item(p)
                if case=='missing-output':item.pop('output_result')
                if case=='wrong-account':item['output_result']['dir']=str(self.root/'other')
                if case=='traversal':item['output_result']['items'][0]['name']='../fake.mp4'
                if case=='empty':self.item(p,payload=b'')
                if case=='badhead':self.item(p,payload=b'BAD-HEADER-FAKE-DATA')
                if case=='badsize':item['output_result']['items'][0]['bytes']+=1
                self.jobs([item],state='running' if case=='running' else 'done',failed=1 if case=='partial' else 0,bad_json=case=='badjson')
                self.service.collect(p['id']);self.assertEqual(self.record()['state'],'failed')
                with self.assertRaises(ImportFailure):self.service.confirm(self.tasks[0]['id'],'checked',True)

    def test_missing_or_disallowed_current_candidate_blocks(self):
        p=self.package();item=self.item(p);self.jobs([item])
        self.response.update(items=[],total=0);self.service.collect(p['id']);self.assertEqual(self.record()['state'],'failed')
        self.jobs([item]);self.response['items'][0]['eligibility']['allowed']=False
        self.service.collect(p['id']);self.assertEqual(self.record()['state'],'failed')
    def test_retry_aggregation_confirmation_hash_change_and_restart(self):
        p=self.package();ok=self.item(p);failed={'material_id':'m2','aweme_id':'f2','state':'failed'};self.jobs([ok,failed],failed=1);self.service.collect(p['id']);self.assertEqual(self.record()['state'],'failed')
        prior=self.record()['report'];prior_path=Path(prior['snapshot']);prior_bytes=prior_path.read_bytes();self.assertEqual(hashlib.sha256(prior_bytes).hexdigest(),prior['sha256'])
        self.assertFalse(self.service.retry(self.tasks[0]['id'],'retry m2')['reused']);self.assertTrue(self.service.retry(self.tasks[0]['id'],'retry m2')['reused']);self.assertEqual(self.record()['attempt'],1)
        self.jobs([ok],ids=['m1'],append=True);self.service.collect(p['id']);self.assertEqual(self.record()['state'],'failed','latest subset must not hide m2 failure')
        self.assertEqual(prior_path.read_bytes(),prior_bytes)
        with self.store.connection() as conn:
            audits=[json.loads(row[0]) for row in conn.execute("SELECT details FROM audit WHERE action='material_collect'")]
        matching=[entry for audit in audits for entry in audit['states'] if entry.get('snapshot')==str(prior_path)]
        self.assertTrue(matching);self.assertEqual(matching[0]['reportSha256'],hashlib.sha256(prior_bytes).hexdigest())
        other=self.item(p,'m2','other.mp4');self.jobs([other],ids=['m2'],append=True);self.service.collect(p['id']);self.assertEqual(self.record()['state'],'reported')
        with self.assertRaises(ImportFailure):self.service.confirm(self.tasks[0]['id'],'',True)
        with self.assertRaises(ImportFailure):self.service.confirm(self.tasks[0]['id'],'checked',False)
        file=Path(self.record()['files'][0]['path']);original=file.read_bytes();file.write_bytes(original[:-1]+b'X')
        with self.assertRaises(ImportFailure):self.service.confirm(self.tasks[0]['id'],'checked',True)
        file.write_bytes(original);self.assertFalse(self.service.confirm(self.tasks[0]['id'],'checked',True)['reused']);self.assertTrue(self.service.confirm(self.tasks[0]['id'],'again',True)['reused']);confirmation=self.record()['confirmation'];self.service.collect(p['id']);self.assertEqual(self.record()['state'],'ready');self.assertEqual(self.record()['confirmation'],confirmation)
        restored=MaterialService(ConsoleStore(self.root/'runtime'));self.assertEqual(restored.state()['materialTasks'][self.tasks[0]['id']]['state'],'ready')
        file.write_bytes(original[:-1]+b'Y')
        with self.assertRaises(ImportFailure):self.service.confirm(self.tasks[0]['id'],'duplicate check',True)
        self.service.collect(p['id']);self.assertEqual(self.record()['state'],'reported');self.assertIsNone(self.record()['confirmation'])

if __name__=='__main__':unittest.main()
