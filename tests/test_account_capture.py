"""Fictional account capture contracts; isolated temporary runtime only."""
import unittest
import base64
from unittest.mock import patch
from test_workbench import WorkbenchTests
from test_local_import import task
from control_console.account_capture import AccountCaptureService, parse_text
from control_console.importers import ImportFailure

class AccountCaptureTests(unittest.TestCase):
    tasks=WorkbenchTests.tasks
    fixture_setup=WorkbenchTests.setUp
    # Reuse fixture helpers without rerunning inherited acceptance cases.
    def setUp(self):
        self.fixture_setup()
        self.capture=AccountCaptureService(self.store,self.service)
        self.tasks([task(account='000A01',name='虚构执行名称')])

    def preview(self, rows=None):
        rows=rows or [['虚构运营','000A01','虚构来源名称','0001234567890123456789','000567','食品/宠物','虚构备注','C001']]
        return self.capture.preview({'kind':'text','format':'tsv','text':'\n'.join('\t'.join(r) for r in rows)})

    def body(self, preview, indices=(0,), allowed=None):
        return {'confirmed':True,'captures':[{'captureId':preview['captureId'],'revisionToken':preview['revisionToken']}],
                'rows':[{'captureId':preview['captureId'],'rowId':r['id'],'fields':r['fields'],
                         'allowed':allowed or r['allowed'],'taskNames':r['taskNames'],'verified':True}
                        for i,r in enumerate(preview['rows']) if i in indices]}

    def test_capture_preserves_eight_columns_identity_and_explicit_binding(self):
        before=self.store.state()['tasks'];p=self.preview();r=p['rows'][0]
        self.assertTrue(r['selected']);self.assertEqual(r['fields'][1],'000A01');self.assertIn('绑定',''.join(r['issues']))
        bad=self.body(p);bad['rows'][0]['taskNames']=[]
        with self.assertRaises(ImportFailure):self.capture.confirm(bad)
        result=self.capture.confirm(self.body(p));self.assertEqual(result['savedCount'],1)
        self.assertEqual([(t['id'],t['fields']) for t in self.store.state()['tasks']],[(t['id'],t['fields']) for t in before])
        with self.store.connection() as conn:profiles,_,_=self.capture._context(conn)
        saved=profiles['000A01'];self.assertEqual(saved['rawFields'],r['fields']);self.assertEqual(saved['name'],'虚构执行名称');self.assertEqual(saved['sourceName'],'虚构来源名称')
        self.assertEqual(saved['uid'],'0001234567890123456789');self.assertEqual(saved['cooperationCode'],'000567')
        self.assertFalse(self.capture.draft(p['captureId'])['rows'][0]['selected'])
        with self.assertRaises(ImportFailure):self.capture.confirm(self.body(p))

    def test_capture_unknown_category_draft_and_partial_selection(self):
        p=self.preview([['虚构运营','000A01','虚构执行名称','001','002','待映射类别','',''],['虚构运营','other-B','虚构其他账号','003','004','母婴','','']])
        self.assertTrue(p['rows'][0]['selected']);self.assertFalse(p['rows'][1]['selected']);self.assertEqual(p['rows'][0]['allowed'],[])
        self.assertEqual(self.capture.list_drafts()[0]['status'],'draft')
        with self.assertRaises(ImportFailure):self.capture.confirm(self.body(p))
        result=self.capture.confirm(self.body(p,allowed=['百货/个护家清']));self.assertEqual(result['remainingCount'],1)
        self.assertEqual(self.capture.list_drafts()[0]['status'],'draft');self.assertFalse(self.capture.draft(p['captureId'])['rows'][0]['selected'])

    def test_capture_company_code_collision_is_atomic_and_stale_revision_rejected(self):
        p=self.preview([['虚构运营','000A01','虚构执行名称','001','002','母婴','','DUP'],['虚构运营','other-B','虚构其他账号','003','004','母婴','','dup']])
        with self.assertRaises(ImportFailure):self.capture.confirm(self.body(p,(0,1)))
        self.assertFalse(self.capture.draft(p['captureId'])['rows'][0]['saved'])
        self.capture.confirm(self.body(p))
        with self.assertRaises(ImportFailure):self.capture.confirm(self.body(p,(1,)))
        refreshed=self.capture.draft(p['captureId']);self.assertIn('公司编号',''.join(refreshed['rows'][1]['issues']))
        with self.assertRaises(ImportFailure):self.capture.confirm(self.body(refreshed,(1,)))

    def test_capture_requires_explicit_confirmation_and_rejects_markup_spans(self):
        p=self.preview();body=self.body(p);body['confirmed']=False
        with self.assertRaises(ImportFailure):self.capture.confirm(body)
        with self.assertRaises(ImportFailure):parse_text('<table><tr><td colspan="8">ambiguous</td></tr></table>','html')
        html='<table><tr>'+''.join('<td>'+x+'</td>' for x in ['虚构运营','000A01','虚构执行名称','0001','0002','母婴','',''])+'</tr></table><script>not account data</script>'
        parsed=parse_text(html,'html');self.assertEqual(len(parsed['rows']),1);self.assertEqual(parsed['rows'][0]['fields'][3],'0001')


    def test_capture_duplicate_candidates_and_numeric_cells_reject_without_write(self):
        fields=['虚构运营','000A01','虚构执行名称','UID-A001','CODE-002','母婴','','']
        p=self.preview([fields,fields]);self.assertFalse(any(r['saved'] for r in p['rows']))
        with self.assertRaises(ImportFailure):self.capture.confirm(self.body(p,(0,1)))
        body=self.body(p);body['rows'][0]['fields'][3]=123
        with self.assertRaises(ImportFailure):self.capture.confirm(body)
        body=self.body(p);body['rows'][0]['fields'][3]='1.23E+18'
        with self.assertRaises(ImportFailure):self.capture.confirm(body)
        self.assertFalse(self.capture.draft(p['captureId'])['rows'][0]['saved'])

    def test_capture_empty_supplement_retains_existing_identity_and_stale_rolls_back(self):
        first=self.preview();self.capture.confirm(self.body(first))
        empty=self.preview([['虚构运营','000A01','虚构来源名称','','','母婴','','']])
        self.capture.confirm(self.body(empty))
        with self.store.connection() as conn:profiles,_,_=self.capture._context(conn)
        self.assertEqual(profiles['000A01']['uid'],'0001234567890123456789')
        self.assertEqual(profiles['000A01']['companyCode'],'C001')
        pending=self.preview();self.tasks([task(account='000A01',name='虚构执行名称'),task(account='new-task',name='虚构新任务')])
        with self.assertRaises(ImportFailure):self.capture.confirm(self.body(pending))
        self.assertFalse(self.capture.draft(pending['captureId'])['rows'][0]['saved'])

    def test_capture_image_calibration_versions_and_original_snapshot(self):
        content=b'\x89PNG\r\n\x1a\nfictional mock image'
        words=[{'text':value,'x':i*100+10,'y':20,'width':30,'height':10} for i,value in enumerate(['运营','000A01','虚构执行名称','0001','0002','母婴','','']) if value]
        ocr={'width':800,'height':100,'lines':[{'words':words}]}
        with patch('control_console.account_ocr.recognize_image',return_value=ocr):
            p=self.capture.preview({'kind':'image','filename':'fiction.png','content':base64.b64encode(content).decode()})
            self.assertEqual(p['rows'],[]);self.assertIn('校准',''.join(p['warnings']))
            revised=self.capture.preview({'captureId':p['captureId'],'options':{'columns':list(range(0,801,100)),'rowBoundaries':[10,50]}})
        self.assertEqual(revised['captureId'],p['captureId']);self.assertNotEqual(revised['revisionToken'],p['revisionToken'])
        self.assertEqual(revised['rows'][0]['fields'][1],'000A01');self.assertEqual(self.capture.image(p['captureId'])[0],content)
        fresh=AccountCaptureService(self.store,self.service).draft(p['captureId']);self.assertEqual(fresh['rows'],revised['rows'])
        self.capture.confirm(self.body(fresh));self.assertTrue(self.capture.draft(p['captureId'])['rows'][0]['saved'])

    def test_capture_changes_keep_frozen_task_sources_and_manual_edit_keeps_metadata(self):
        first=self.preview();self.capture.confirm(self.body(first))
        task_before=self.store.state()['tasks'][0]
        with self.store.connection(write=True) as conn:self.store.freeze_evidence(conn,[task_before['id']])
        frozen=self.store.state()['tasks'][0]
        updated=self.preview([['虚构运营','000A01','虚构新来源名','UID-NEW','CODE-NEW','母婴','虚构新备注','C002']])
        self.capture.confirm(self.body(updated))
        after=self.store.state()['tasks'][0]
        self.assertEqual(after['frozenEvidence'],frozen['frozenEvidence']);self.assertEqual(after['fields'],task_before['fields']);self.assertEqual(after['id'],task_before['id'])
        self.service.account_profiles([{'accountId':'000A01','name':'虚构执行名称','allowed':['食品/宠物'],'evidence':'虚构人工F复核'}],True)
        with self.store.connection() as conn:profiles,_,_=self.capture._context(conn)
        p=profiles['000A01'];self.assertEqual(p['uid'],'UID-NEW');self.assertEqual(p['companyCode'],'C002');self.assertEqual(p['sourceName'],'虚构新来源名')
        self.assertEqual(self.store.state()['tasks'][0]['frozenEvidence'],frozen['frozenEvidence'])

del WorkbenchTests
