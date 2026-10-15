"""Fictional publishing handoff tests; no external application runs."""
import copy
import json
import unittest
from pathlib import Path
import test_editing as editingfixtures
from control_console.publishing import PublishingService
from control_console.importers import ImportFailure,read_workbook
from control_console.workbooks import task_workbook


class PublishingTests(unittest.TestCase):
    def setUp(self):
        self.fixture=editingfixtures.EditingTests();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.store=self.fixture.store;self.editing=self.fixture.service
        self.p=self.fixture.prepare();self.editing.gui(self.p['id'],self.fixture.body);self.fixture.reports(self.p);self.editing.collect(self.p['id'])
        self.task_id=self.fixture.task['id'];self.editing.verify(self.task_id,'normal','虚构观看',True);self.editing.verify(self.task_id,'speed','虚构观看',True)
        self.service=PublishingService(self.store,self.editing)
        self.body={'taskIds':[self.task_id],'mode':'both','ports':{self.fixture.task['fields'][3]:9001}}
    def prepare(self):return self.service.prepare(self.body)['package']
    def slot(self,p):return p['batches'][0]['slots'][0]
    def receipt(self,p,m='已发布',n='计划已发布'):
        fields=list(p['tasks'][self.task_id]['fields']);fields[12]=m;fields[13]=n
        Path(self.slot(p)['workbookPath']).write_bytes(task_workbook([{'fields':fields}]))
    def test_isolation_long_id_j_idempotence_and_human_verification(self):
        original=Path(self.p['workbook']).read_bytes();source=copy.deepcopy(self.store.state()['tasks'])
        p=self.prepare();self.assertTrue(self.service.prepare(self.body)['reused']);slot=self.slot(p)
        self.assertEqual(len(p['batches'][0]['slots']),5);self.assertEqual(p['batches'][0]['slots'][1:],[None]*4)
        cells=read_workbook(Path(slot['workbookPath']).read_bytes())['sheets'][0]['cells']
        self.assertEqual(cells[(2,6)]['text'],self.fixture.task['fields'][5]);self.assertEqual(cells[(2,10)]['text'],'7001')
        self.service.handoff({'packageId':p['id'],'confirmed':True});self.receipt(p)
        self.service.collect({'packageId':p['id'],'confirmedClosed':True})
        record=self.service.state()['publishingTasks'][self.task_id];self.assertEqual(record['state'],'reported');self.assertIsNone(record['verification'])
        body={'packageId':p['id'],'taskId':self.task_id,'result':'verified','note':'虚构人工核验','confirmed':True}
        self.service.verify(body);self.service.collect({'packageId':p['id'],'confirmedClosed':True})
        self.assertEqual(self.service.state()['publishingTasks'][self.task_id]['state'],'verified');self.assertTrue(self.service.verify(body)['reused'])
        self.assertEqual(Path(self.p['workbook']).read_bytes(),original);self.assertEqual(self.store.state()['tasks'],source)
    def test_missing_ready_and_changed_report_or_video_block(self):
        with self.store.connection(write=True) as conn:
            record=json.loads(conn.execute('SELECT payload FROM editing_tasks WHERE task_id=?',(self.task_id,)).fetchone()[0]);record['speed']['ready']=False
            conn.execute('UPDATE editing_tasks SET payload=? WHERE task_id=?',(json.dumps(record),self.task_id))
        with self.assertRaises(ImportFailure):self.prepare()
        normal={**self.body,'mode':'qianchuan'};p=self.service.prepare(normal)['package']
        copied=p['tasks'][self.task_id]['videos']['normal'][0]['copy'];Path(copied['path']).write_bytes(b'changed')
        with self.assertRaises(ImportFailure):self.service.handoff({'packageId':p['id'],'confirmed':True})
        self.assertEqual(self.service.state()['publishingPackages'][0]['state'],'prepared')
    def test_unknown_no_retry_conflict_and_tampered_workbook_atomic(self):
        p=self.prepare();self.service.handoff({'packageId':p['id'],'confirmed':True});self.receipt(p,'审核中','')
        self.service.collect({'packageId':p['id'],'confirmedClosed':True});before=self.service.state()
        self.assertEqual(before['publishingTasks'][self.task_id]['state'],'unknown')
        with self.assertRaises(ImportFailure):self.service.prepare({**self.body,'mode':'douyin'})
        self.receipt(p)
        with self.assertRaises(ImportFailure):self.service.collect({'packageId':p['id'],'confirmedClosed':True})
        self.assertEqual(before,self.service.state())
        fields=list(p['tasks'][self.task_id]['fields']);fields[9]='bad'
        Path(self.slot(p)['workbookPath']).write_bytes(task_workbook([{'fields':fields}]))
        with self.assertRaises(ImportFailure):self.service.collect({'packageId':p['id'],'confirmedClosed':True})
        self.assertEqual(before,self.service.state())
    def test_copy_failure_rolls_back_and_cleans_only_new_package(self):
        from unittest.mock import patch
        keep=self.store.root/'publishing'/'keep-existing';keep.mkdir(parents=True);(keep/'keep.txt').write_text('fixture')
        original=self.editing._copy
        calls=[]
        def fail_second(source,target):
            calls.append(target)
            if len(calls)==2:raise OSError('fictional copy failure')
            return original(source,target)
        with patch.object(self.editing,'_copy',side_effect=fail_second):
            with self.assertRaises(OSError):self.prepare()
        self.assertEqual(self.service.state()['publishingPackages'],[])
        self.assertEqual(list((self.store.root/'publishing').iterdir()),[keep])
        self.assertEqual((keep/'keep.txt').read_text(),'fixture')
        self.prepare()

    def test_unknown_verification_remains_pending_then_resolves_with_history(self):
        p=self.prepare();self.service.handoff({'packageId':p['id'],'confirmed':True})
        self.service.collect({'packageId':p['id'],'confirmedClosed':True})
        self.service.verify({'packageId':p['id'],'taskId':self.task_id,'result':'unknown','note':'fictional unknown','confirmed':True})
        self.assertEqual(self.service.state()['publishingPackages'][0]['state'],'collected')
        self.service.collect({'packageId':p['id'],'confirmedClosed':True})
        self.assertEqual(self.service.state()['publishingPackages'][0]['state'],'collected')
        before=self.service.state()['publishingTasks'][self.task_id]
        final={'packageId':p['id'],'taskId':self.task_id,'result':'verified','note':'fictional resolved','confirmed':True}
        self.service.verify(final)
        after=self.service.state()['publishingTasks'][self.task_id]
        self.assertEqual(after['verificationHistory'],[before['verification']])
        self.assertEqual(after['receipt'],before['receipt']);self.assertEqual(after['attempt'],1)
        self.assertEqual(self.service.state()['publishingPackages'][0]['state'],'verified')
        self.assertTrue(self.service.verify(final)['reused'])
        with self.assertRaises(ImportFailure):self.service.verify({**final,'result':'failed'})

    def test_extra_nonempty_q2_is_rejected_without_state_change(self):
        import io
        import zipfile
        p=self.prepare();slot=self.slot(p);path=Path(slot['workbookPath'])
        original=path.read_bytes();output=io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(original)) as incoming, zipfile.ZipFile(output,'w') as outgoing:
            for item in incoming.infolist():
                raw=incoming.read(item.filename)
                if item.filename=='xl/worksheets/sheet1.xml':
                    raw=raw.replace(b'</row></sheetData>',b'<c r="Q2" t="inlineStr"><is><t>fiction-extra-value</t></is></c></row></sheetData>')
                outgoing.writestr(item,raw)
        path.write_bytes(output.getvalue());before=self.service.state()
        with self.assertRaises(ImportFailure):self.service.handoff({'packageId':p['id'],'confirmed':True})
        self.assertEqual(self.service.state(),before)

    def test_confirmation_and_source_receipts(self):
        for update in [{'ports':{}},{'ports':{self.fixture.task['fields'][3]:True}},{'taskIds':[]},{'mode':'bad'}]:
            with self.assertRaises(ImportFailure):self.service.prepare({**self.body,**update})
        p=self.prepare()
        with self.assertRaises(ImportFailure):self.service.collect({'packageId':p['id'],'confirmedClosed':True})
        with self.assertRaises(ImportFailure):self.service.handoff({'packageId':p['id'],'confirmed':False})
        with self.assertRaises(ImportFailure):self.service.verify({'packageId':p['id'],'taskId':self.task_id,'result':'verified','note':'','confirmed':True})

if __name__=='__main__':unittest.main()
