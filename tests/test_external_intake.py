"""Fictional external outputs; no business app, real paths or accounts."""
import unittest
from pathlib import Path
import test_materials as fixtures
from control_console.editing import EditingService
from control_console.publishing import PublishingService
from control_console.external_intake import ExternalIntakeService
from control_console.importers import ImportFailure


class ExternalIntakeTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.MaterialsTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.store=self.f.store;self.editing=EditingService(self.store,self.f.service)
        self.service=ExternalIntakeService(self.store,self.f.service,self.editing)
        self.publishing=PublishingService(self.store,self.editing)
        self.task=self.f.tasks[0];self.root=self.f.root/'external-source'
        self.product=self.root/self.task['fields'][2]/self.task['fields'][5]
        self.product.mkdir(parents=True)
    def video(self,path):
        path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'\x00\x00\x00\x18ftypisomFICTIONAL')
    def preview(self,kind='materials'):
        return self.service.preview({'taskIds':[self.task['id']],'kind':kind,'root':str(self.root)})['preview']
    def accept(self,p):return self.service.accept({'previewId':p['id'],'confirmed':True,'note':'虚构逐个观看核对'})
    def test_material_isolation_freeze_editing_gate_and_idempotence(self):
        source=self.product/'a.mp4';self.video(source);original=source.read_bytes();p=self.preview()
        self.assertEqual(p['state'],'unverified');a=self.accept(p)['acceptance']
        self.assertTrue(self.accept(p)['reused']);self.assertEqual(source.read_bytes(),original)
        self.assertEqual(self.f.service.state()['materialTasks'],{})
        prepared=self.editing.prepare([self.task['id']])['package']
        self.assertIn('external',a['rows'][0]['files']['materials'][0]['path'])
        self.assertEqual(prepared['rows'][0]['materialConfirmation']['manual'],True)
        self.assertTrue(self.accept(p)['reused'])
        with self.store.connection() as conn:self.assertTrue(conn.execute('SELECT frozen_evidence FROM tasks WHERE id=?',(self.task['id'],)).fetchone()[0])
    def test_finished_independent_per_kind_and_publish(self):
        self.video(self.product/'成片'/'a.mp4');p=self.preview('finished');self.accept(p)
        self.assertEqual(self.editing.state()['editingTasks'],{})
        body={'taskIds':[self.task['id']],'mode':'douyin','ports':{self.task['fields'][3]:9001}}
        with self.assertRaises(ImportFailure):self.publishing.prepare(body)
        package=self.publishing.prepare({**body,'mode':'qianchuan'})['package']
        self.assertTrue(package['tasks'][self.task['id']]['editingPackageId'].startswith('external:'))
    def test_changed_set_hash_task_and_confirmation_rejected(self):
        self.video(self.product/'a.mp4');p=self.preview();self.video(self.product/'b.mp4')
        with self.assertRaises(ImportFailure):self.accept(p)
        p=self.preview();(self.product/'a.mp4').write_bytes(b'\x00\x00\x00\x18ftypchanged')
        with self.assertRaises(ImportFailure):self.accept(p)
        p=self.preview()
        with self.assertRaises(ImportFailure):self.service.accept({'previewId':p['id'],'confirmed':True,'note':''})
        with self.store.connection(write=True) as conn:conn.execute('UPDATE tasks SET archived=1 WHERE id=?',(self.task['id'],))
        with self.assertRaises(ImportFailure):self.accept(p)
        self.assertEqual(self.service.state()['externalAcceptances'],[])
    def test_existing_handoff_report_and_unsafe_root_rejected(self):
        self.video(self.product/'a.mp4');(self.product/'批量导出报告.json').write_text('{}')
        with self.assertRaises(ImportFailure):self.preview()
        (self.product/'批量导出报告.json').unlink();(self.product/'unknown.json').write_text('{}')
        p=self.preview();self.assertEqual(p['rows'][0]['reports'][0]['status'],'unverified')
        self.f.package()
        with self.assertRaises(ImportFailure):self.accept(p)
        for root in ('relative','\\\\server\\share'):
            with self.assertRaises(ImportFailure):self.service.preview({'taskIds':[self.task['id']],'kind':'materials','root':root})
    def test_copy_failure_rolls_back_and_cleans(self):
        from unittest.mock import patch
        self.video(self.product/'a.mp4');p=self.preview()
        with patch.object(self.editing,'_copy',side_effect=OSError('fiction')):
            with self.assertRaises(OSError):self.accept(p)
        self.assertEqual(self.service.state()['externalAcceptances'],[])
        self.assertFalse(any((self.store.root/'external').glob('*')))
    def test_tampered_finished_copy_blocks_publish(self):
        self.video(self.product/'成片'/'变速'/'a.mp4');a=self.accept(self.preview('finished'))['acceptance']
        Path(a['rows'][0]['files']['speed'][0]['path']).write_bytes(b'changed')
        with self.assertRaises(ImportFailure):self.publishing.prepare({'taskIds':[self.task['id']],'mode':'douyin','ports':{self.task['fields'][3]:9001}})
    def test_concurrent_accept_is_single_record(self):
        from concurrent.futures import ThreadPoolExecutor
        self.video(self.product/'a.mp4');p=self.preview()
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:self.accept(p),range(2)))
        self.assertEqual(sum(not r['reused'] for r in results),1)
        self.assertEqual(len(self.service.state()['externalAcceptances']),1)
    def test_account_root_mismatch_and_symlink_block(self):
        self.video(self.product/'a.mp4')
        with self.assertRaises(ImportFailure):self.service.preview({'taskIds':[t['id'] for t in self.f.tasks],'kind':'materials','root':str(self.root)})
        with self.assertRaises(ImportFailure):self.service.preview({'taskIds':[self.task['id']],'kind':'materials','root':str(self.root/self.task['fields'][2])})
        link=self.product/'linked.mp4'
        try:link.symlink_to(self.product/'a.mp4')
        except OSError:self.skipTest('Windows symlink privilege unavailable')
        with self.assertRaises(ImportFailure):self.preview()

if __name__=='__main__':unittest.main()
