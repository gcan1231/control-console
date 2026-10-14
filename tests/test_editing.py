"""006 fictional report contract tests. Never launch a business executable."""
import copy
import hashlib
import json
import os
import unittest
from pathlib import Path
import test_materials as materialfixtures
from datetime import datetime
from test_local_import import HEADERS,PID,task,workbook
from control_console.editing import EditingService
from control_console.importers import ImportFailure,read_workbook
from control_console.workbooks import task_workbook
from control_console.store import ConsoleStore
from control_console.materials import MaterialService

class EditingTests(unittest.TestCase):
    def setUp(self):
        self.fixture=materialfixtures.MaterialsTests();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.store=self.fixture.store;self.material=self.fixture.service;self.task=self.fixture.tasks[0]
        p=self.fixture.package();item=self.fixture.item(p);self.fixture.jobs([item]);self.material.collect(p['id']);self.material.confirm(self.task['id'],'虚构素材人工复核',True)
        self.service=EditingService(self.store,self.material);self.rules=self.fixture.root/'fiction-rules.txt';self.rules.write_text('虚构禁止词',encoding='utf8');self.exe=self.fixture.root/'fiction-program.bin';self.exe.write_bytes(b'NOT-AN-EXECUTABLE-FIXTURE')
        self.body={'confirmed':True,'targetCount':1,'rulesPath':str(self.rules),'executablePath':str(self.exe),'decoration':'无装饰','version':'fiction-006','namingMode':'id','speedEnabled':True}
    def prepare(self):return self.service.prepare([self.task['id']])['package']
    def record(self):return self.service.state()['editingTasks'][self.task['id']]
    def reports(self,p,with_speed=True):
        row=p['rows'][0];normal=Path(row['normalDir']);normal.mkdir(parents=True,exist_ok=True);speed=Path(row['speedDir']);speed.mkdir(parents=True,exist_ok=True)
        def video(path):
            path.write_bytes(b'\x00\x00\x00\x18ftypisomFICTIONAL-OUTPUT');s=path.stat();return [s.st_size,s.st_mtime_ns]
        output=normal/'001.mp4';identity=video(output);frozen=speed/'freeze.mp4';freeze_identity=video(frozen) if with_speed else None
        rules_info={'checked':True,'exists':True,'effective_term_count':1,'file_sha256':hashlib.sha256(self.rules.read_bytes()).hexdigest(),'parse_error_count':0,'source':str(self.rules)}
        result={'output':str(output),'output_identity':identity,'duration':10.0,'idx':1,'output_slot':1,'content_sha256':hashlib.sha256(output.read_bytes()).hexdigest(),'component_decisions':[{'source':row['inputs'][0]['path']}]}
        freeze={'ok':True,'error':'','path':str(frozen),'output_identity':freeze_identity,'duration':10.0} if with_speed else {'ok':False,'error':'虚构变速缺失'}
        generated=datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        details={'generated':generated,'export_dir':row['normalDir'],'rules_file':str(self.rules),'rules_info':rules_info,'done':1,'total':1,'pending_review_count':0,'results':[result],'freeze':freeze}
        summary={'row_no':row['row'],'account':row['accountName'],'douyin_id':row['accountId'],'product_id':row['productId'],'export_dir':row['inputDir'],'write_ok':True,'write_error':'','done':1,'total':1,'error':'','review_count':0,'freeze':copy.deepcopy(freeze),'status':'虚构剪辑完成'}
        outer={'generated':generated,'task_table':p['workbook'],'material_root':p['materialRoot'],'export_dir':p['reportDir'],'rules_file':str(self.rules),'rules_info':rules_info,'settings':{'export_naming':{'mode':'id'},'task_target_count':1,'batch_freeze':True},'total':1,'rows':[summary]}
        working=copy.deepcopy(row['expectedFields']);working[11]=summary['status'];Path(p['workbook']).write_bytes(task_workbook([{'fields':working}]))
        self.write_reports(p,outer,details);return outer,details
    def write_reports(self,p,outer,details):
        Path(p['reportPath']).write_text(json.dumps(outer,ensure_ascii=False),encoding='utf8');(Path(p['rows'][0]['inputDir'])/'批量导出报告.json').write_text(json.dumps(details,ensure_ascii=False),encoding='utf8')
    def test_batch_empty_note_atomic_failure_idempotence_and_archived(self):
        p=self.prepare();self.service.gui(p['id'],self.body);self.reports(p);self.service.collect(p['id'])
        task_id=self.task['id']
        for ids,kind,confirmed in [([], 'normal',True),([task_id]*2,'normal',True),([task_id],'normal',False),([task_id],'other',True),([task_id,'missing'],'normal',True)]:
            with self.assertRaises(ImportFailure):self.service.verify_batch(ids,kind,confirmed)
            self.assertFalse(self.record()['normal']['ready'])
        with self.store.connection(write=True) as conn:conn.execute('UPDATE tasks SET archived=1 WHERE id=?',(task_id,))
        with self.assertRaises(ImportFailure):self.service.verify_batch([task_id],'normal',True)
        with self.store.connection(write=True) as conn:conn.execute('UPDATE tasks SET archived=0 WHERE id=?',(task_id,))
        value=self.service.verify_batch([task_id],'normal',True)
        self.assertEqual(value,{'ok':True,'verified':[{'taskId':task_id,'kind':'normal','reused':False}]})
        self.assertEqual(self.record()['normal']['verification']['note'],'')
        self.assertFalse(self.record()['speed']['ready'])
        self.assertTrue(self.service.verify_batch([task_id],'normal',True)['verified'][0]['reused'])
        self.assertFalse(self.service.verify(task_id,'speed','',True)['reused'])

    def test_prepare_isolation_identity_no_source_mutation(self):
        source=Path(self.material.state()['materialPackages'][0]['workbook']);before=source.read_bytes();db=self.fixture.db.read_bytes();p=self.prepare();self.assertIsNone(p['gui']);self.assertTrue(self.service.prepare([self.task['id']])['reused'])
        self.assertEqual(source.read_bytes(),before);self.assertEqual(self.fixture.db.read_bytes(),db)
        row=p['rows'][0];self.assertEqual(row['taskId'],self.task['id']);self.assertEqual(row['productId'],PID);self.assertEqual(row['sourceRow'],2)
        cells=read_workbook(Path(p['workbook']).read_bytes())['sheets'][0]['cells'];self.assertEqual([cells[(1,i)]['text'] for i in range(1,17)],HEADERS);self.assertEqual(cells[(2,6)]['text'],PID);self.assertEqual(cells[(2,10)]['text'],'7001');self.assertNotIn((2,12),cells)
        original=self.material.state()['materialTasks'][self.task['id']]['files'][0];self.assertNotEqual(row['inputs'][0]['path'],original['path']);self.assertEqual(Path(row['inputs'][0]['path']).read_bytes(),Path(original['path']).read_bytes())
    def test_gui_no_defaults_and_changed_material_gate(self):
        p=self.prepare()
        workbook_path=Path(p['workbook']);original=workbook_path.read_bytes();changed=list(p['rows'][0]['expectedFields']);changed[9]='9999';workbook_path.write_bytes(task_workbook([{'fields':changed}]))
        with self.assertRaises(ImportFailure):self.service.gui(p['id'],self.body)
        workbook_path.write_bytes(original)
        for update in [{'confirmed':False},{'namingMode':'date'},{'targetCount':0},{'targetCount':201},{'rulesPath':''},{'executablePath':''}]:
            with self.assertRaises(ImportFailure):self.service.gui(p['id'],{**self.body,**update})
        self.assertFalse(self.service.gui(p['id'],self.body)['reused']);self.assertTrue(self.service.gui(p['id'],self.body)['reused'])
        material_file=Path(self.material.state()['materialTasks'][self.task['id']]['files'][0]['path']);material_file.write_bytes(material_file.read_bytes()[:-1]+b'X')
        with self.assertRaises(ImportFailure):self.service.prepare([self.task['id']])
    def test_prepare_current_category_source_receipt_material_review(self):
        with self.store.connection(write=True) as c:c.execute("UPDATE sources SET complete=0 WHERE kind='products'")
        with self.assertRaises(ImportFailure):self.prepare()
    def test_prepare_multiple_accounts_duplicate_products_and_overlap(self):
        second=self.fixture.tasks[1]
        def ready(t):
            p=self.material.prepare([t['id']])['package'];item=self.fixture.item(p);self.fixture.jobs([item]);self.material.collect(p['id']);self.material.confirm(t['id'],'虚构复核',True)
        ready(second)
        with self.assertRaises(ImportFailure) as error:self.service.prepare([self.task['id'],second['id']])
        self.assertEqual(error.exception.code,'editing_account')
        same=task();same[0]='2026-10-14';different=task(product='9000000000000000002');different[0]='2026-10-15'
        self.store.commit_import('tasks','extra-fiction.xlsx',workbook([HEADERS,same,different]),{})
        self.store.commit_import('products','two-products.xlsx',workbook([['ID','类别'],[PID,'食品'],['9000000000000000002','食品']],hyperlink=False),{'complete':True,'sheets':[{'sheet':'Sheet1','headerRow':1,'idColumn':'A','categoryColumn':'B','categoryFromSheet':False}]})
        tasks=self.store.state()['tasks'];same_t=next(t for t in tasks if t['fields'][0]=='2026-10-14');different_t=next(t for t in tasks if t['fields'][0]=='2026-10-15');ready(same_t);ready(different_t)
        with self.assertRaises(ImportFailure) as error:self.service.prepare([self.task['id'],same_t['id']])
        self.assertEqual(error.exception.code,'editing_product')
        self.prepare()
        with self.assertRaises(ImportFailure) as error:self.service.prepare([self.task['id'],different_t['id']])
        self.assertEqual(error.exception.code,'editing_overlap')
        self.fixture.evidence()
        with self.store.connection(write=True) as c:
            fields=list(self.task['fields']);fields[12]='已发布';c.execute('UPDATE tasks SET fields=? WHERE id=?',(json.dumps(fields),self.task['id']))
        with self.assertRaises(ImportFailure):self.prepare()
        record=self.material.state()['materialTasks'][self.task['id']];record['state']='reported'
        with self.store.connection(write=True) as c:
            c.execute('UPDATE tasks SET fields=? WHERE id=?',(json.dumps(self.task['fields']),self.task['id']));c.execute('UPDATE material_tasks SET payload=? WHERE task_id=?',(json.dumps(record),self.task['id']))
        with self.assertRaises(ImportFailure):self.prepare()
    def test_normal_speed_separate_human_verification_and_restart(self):
        p=self.prepare();self.service.gui(p['id'],self.body);self.reports(p,False);self.service.collect(p['id']);r=self.record();self.assertTrue(r['normal']['available']);self.assertFalse(r['speed']['available']);self.assertFalse(r['normal']['ready'])
        for kind,note,confirmed in [('normal','已观看',False),('speed','已观看',True)]:
            with self.assertRaises(ImportFailure):self.service.verify(self.task['id'],kind,note,confirmed)
        self.assertFalse(self.service.verify(self.task['id'],'normal','虚构观看核验',True)['reused']);self.assertTrue(self.service.verify(self.task['id'],'normal','再确认',True)['reused']);self.assertFalse(self.record()['speed']['ready'])
        self.service.collect(p['id']);self.assertTrue(self.record()['normal']['ready'])
        restored_store=ConsoleStore(self.store.root);restored=EditingService(restored_store,MaterialService(restored_store));self.assertTrue(restored.state()['editingTasks'][self.task['id']]['normal']['ready'])
        self.reports(p,True);self.service.collect(p['id']);self.assertFalse(self.record()['normal']['ready']);self.assertTrue(self.record()['speed']['available']);self.service.verify(self.task['id'],'speed','虚构变速观看',True);self.assertTrue(self.record()['speed']['ready'])
    def test_invalid_reports_and_output_evidence_block(self):
        p=self.prepare();self.service.gui(p['id'],self.body)
        mutations=['wrongroot','wrongaccount','missingrow','uncheckedrules','date-name','writefalse','pending','identity','duration','nested','extra','replace','rulemissing','ruleempty','rulehash','ruleparse','wrongreportdir','oldreport','slotzero','slotmismatch','contentsha','inputsource','logmismatch']
        for case in mutations:
            with self.subTest(case=case):
                for path in Path(p['rows'][0]['normalDir']).glob('*.mp4'):path.unlink()
                outer,details=self.reports(p)
                if case=='wrongroot':outer['material_root']=str(self.fixture.root)
                if case=='wrongaccount':outer['rows'][0]['account']='错账号'
                if case=='missingrow':outer['rows']=[]
                if case=='uncheckedrules':outer['rules_info']['checked']=False
                if case=='date-name':outer['settings']['export_naming']['mode']='date'
                if case=='writefalse':outer['rows'][0]['write_ok']=False
                if case=='pending':details['pending_review_count']=1
                if case=='identity':details['results'][0]['output_identity']=[1,1]
                if case=='duration':details['results'][0]['duration']=0
                if case=='nested':details['results'][0]['output']=str(Path(p['rows'][0]['normalDir'])/'nested'/'001.mp4')
                if case=='extra':(Path(p['rows'][0]['normalDir'])/'old.mp4').write_bytes(b'\x00\x00\x00\x18ftypOLD')
                if case=='rulemissing':outer['rules_info']['exists']=False
                if case=='ruleempty':outer['rules_info']['effective_term_count']=0
                if case=='rulehash':outer['rules_info']['file_sha256']='0'*64
                if case=='ruleparse':outer['rules_info']['parse_error_count']=1
                if case=='wrongreportdir':outer['export_dir']=str(self.fixture.root)
                if case=='oldreport':outer['generated']='2000-01-01 00:00:00'
                if case=='slotzero':details['results'][0]['output_slot']=details['results'][0]['idx']=0
                if case=='slotmismatch':details['results'][0]['output_slot']=2
                if case=='contentsha':details['results'][0]['content_sha256']='0'*64
                if case=='inputsource':details['results'][0]['component_decisions'][0]['source']=str(self.exe)
                if case=='logmismatch':outer['rows'][0]['status']='并非L实际回执'
                self.write_reports(p,outer,details)
                if case=='replace':Path(details['results'][0]['output']).write_bytes(b'\x00\x00\x00\x18ftypREPLACED')
                self.service.collect(p['id']);self.assertFalse(self.record()['normal']['available'])
        Path(p['reportPath']).write_text('{bad',encoding='utf8');self.service.collect(p['id']);self.assertEqual(self.record()['state'],'failed')
    def test_retry_history_preserved_and_hashes_current(self):
        p=self.prepare();self.service.gui(p['id'],self.body);self.service.collect(p['id']);old=self.record()['report'];data=Path(old['snapshot']).read_bytes()
        self.assertFalse(self.service.retry(self.task['id'],'虚构GUI补跑登记')['reused']);self.assertTrue(self.service.retry(self.task['id'],'虚构GUI补跑登记')['reused']);self.assertEqual(self.record()['attempt'],1);self.assertEqual(Path(old['snapshot']).read_bytes(),data)
        self.reports(p);self.rules.write_text('词表改变',encoding='utf8');self.service.collect(p['id']);self.assertEqual(self.record()['state'],'failed');self.assertEqual(Path(old['snapshot']).read_bytes(),data)
    def test_report_mtime_current_verification_and_raw_history(self):
        p=self.prepare();self.service.gui(p['id'],self.body);outer,details=self.reports(p);report=Path(p['reportPath']);os.utime(report,ns=(1,1));self.service.collect(p['id']);self.assertFalse(self.record()['normal']['available'])
        self.write_reports(p,outer,details);self.service.collect(p['id']);evidence=self.record()['report'];raws=evidence['rawReports'];self.assertEqual(set(raws),{'outer','row'})
        workbook_path=Path(p['workbook']);original=workbook_path.read_bytes()
        for index,value in [(9,'9999'),(11,'不同的L回执')]:
            changed=list(p['rows'][0]['expectedFields']);changed[11]=outer['rows'][0]['status'];changed[index]=value;workbook_path.write_bytes(task_workbook([{'fields':changed}]))
            with self.assertRaises(ImportFailure):self.service.verify(self.task['id'],'normal','观看',True)
            workbook_path.write_bytes(original)
        frozen={k:Path(v['snapshot']).read_bytes() for k,v in raws.items()}
        for kind in raws:self.assertEqual(hashlib.sha256(frozen[kind]).hexdigest(),raws[kind]['sha256'])
        report.write_text(report.read_text(encoding='utf8')+' ',encoding='utf8')
        with self.assertRaises(ImportFailure):self.service.verify(self.task['id'],'normal','观看',True)
        self.write_reports(p,outer,details);self.service.collect(p['id']);input_file=Path(p['rows'][0]['inputs'][0]['path']);input_file.write_bytes(input_file.read_bytes()[:-1]+b'Z')
        with self.assertRaises(ImportFailure):self.service.verify(self.task['id'],'normal','观看',True)
        self.service.collect(p['id']);self.assertFalse(self.record()['normal']['available'])
        for kind in raws:self.assertEqual(Path(raws[kind]['snapshot']).read_bytes(),frozen[kind])

if __name__=='__main__':unittest.main()
