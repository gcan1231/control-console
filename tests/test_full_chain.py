"""Cross-service fictional chains. Adapter simulates platforms; no CDP or apps.

run_account exercises the real persisted coordinator in process. Independent
worker/browser compatibility remains covered by publisher_browser tests.
"""
import unittest
from pathlib import Path
import test_selection as selectionfixtures
import test_editing as editingfixtures
from control_console.store import ConsoleStore
from control_console.materials import MaterialService
from control_console.editing import EditingService
from control_console.external_intake import ExternalIntakeService
from control_console.publishing import PublishingService
from control_console.publisher_execution import PublishExecutionService
from control_console.importers import ImportFailure, read_workbook


class SimulatedPlatform:
    def __init__(self, ports):
        self.ports=ports;self.calls=[];self.failure=None;self.ids={}
    async def probe_browser(self,port):
        return {'identityReliable':True,'accountId':self.ports[port],'uid':'fiction-uid','advertiserId':'fiction-ad'}
    def validate_template(self,*args):pass
    async def execute_item(self,port,binding,template,item,checkpoint):
        self.calls.append((port,item))
        checkpoint('uploading');checkpoint('before_submit')
        if self.failure=='unknown':raise RuntimeError('fiction interruption after submit intent')
        platform_id=str(8000000000000000000+len(self.calls))
        self.ids[item['id']]=platform_id
        checkpoint('submitted',{'platformId':platform_id})
        if self.failure=='close' and item['platform']=='qianchuan':raise RuntimeError('fiction exact plan closure failed')
        return {'state':'closed' if item['platform']=='qianchuan' else 'published','platformId':platform_id,'evidence':{'simulated':True,'exactId':platform_id}}


class FullChainTests(unittest.TestCase):
    def setUp(self):
        self.f=selectionfixtures.SelectionTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.store=self.f.store
        self.material=MaterialService(self.store);self.editing=EditingService(self.store,self.material)
        self.external=ExternalIntakeService(self.store,self.material,self.editing)
        self.publishing=PublishingService(self.store,self.editing)
        self.root=self.store.root.parent/'fiction-input'

    def generate(self,count=1):
        accounts=[{**self.f.account,'accountId':f'fiction-{n}','name':f'虚构账号{n}'} for n in range(count)]
        self.f.sources('accounts',accounts)
        for account in accounts:
            aid=account['accountId'];product=self.f.service.catalog({'accountId':aid,'range':'all'})['rows'][0]
            draft=self.f.service.add({'accountId':aid,'productId':product['id'],'date':'2026-10-15'})
            self.f.service.update({'draftId':draft['id'],'version':draft['version'],'changes':{'portHint':'7'}})
            preview=self.f.service.preview({'accountId':aid,'date':'2026-10-15'})
            self.assertEqual([],preview['issues'])
            self.f.service.generate({'token':preview['token'],'confirmed':True})
        tasks=self.store.state()['tasks']
        self.assertEqual(count,len(tasks))
        for task in tasks:
            self.assertEqual(16,len(task['fields']));self.assertEqual('9000000000000000001',task['fields'][5]);self.assertEqual('7',task['fields'][9])
        return tasks

    def video(self,path):
        path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'\x00\x00\x00\x18ftypisomFICTION-CHAIN')

    def accept(self,tasks,kind):
        p=self.external.preview({'taskIds':[t['id'] for t in tasks],'kind':kind,'root':str(self.root)})['preview']
        return self.external.accept({'previewId':p['id'],'confirmed':True,'note':'虚构逐项观看及账号归属核验'})

    def finished(self,count=1):
        tasks=self.generate(count)
        for t in tasks:
            d=self.root/t['fields'][2]/t['fields'][5]/'成片'
            self.video(d/'normal.mp4');self.video(d/'变速'/'speed-a.mp4');self.video(d/'变速'/'speed-b.mp4')
        for task in tasks:self.accept([task],'finished')
        return tasks

    def execution(self,tasks,mode='both'):
        ports={t['fields'][3]:19001+n for n,t in enumerate(tasks)}
        p=self.publishing.prepare({'taskIds':[t['id'] for t in tasks],'mode':mode,'ports':ports})['package']
        adapter=SimulatedPlatform({v:k for k,v in ports.items()})
        service=PublishExecutionService(self.store,self.publishing,launch_workers=False,browser=adapter)
        for aid,port in ports.items():
            service.probe({'port':port});service.bind({'accountId':aid,'port':port,'uid':'fiction-uid','advertiserId':'fiction-ad','confirmed':True})
            for platform in ('douyin','qianchuan'):
                service.template({'accountId':aid,'platform':platform,'parameters':{'fiction':True},'confirmed':True})
        return p,service,adapter

    def complete(self,p,service,adapter):
        for batch in p['batches']:
            run=service.start({'packageId':p['id'],'batchIndex':batch['index'],'confirmed':True})['run']
            for account in run['accounts']:service.run_account(run['id'],account['accountId'])
        evidence=service.native_receipts(p['id'])
        for row in evidence.values():
            for item in row['evidence']:
                self.assertEqual(adapter.ids[item['itemId']],item['platformId'])
                self.assertEqual(item['platformId'],item['evidence']['exactId'])
        self.publishing.collect_native({'packageId':p['id'],'confirmed':True},evidence)
        for tid in p['tasks']:
            record=self.publishing.state()['publishingTasks'][tid]
            self.assertEqual('reported',record['state']);self.assertIsNone(record['verification'])
            self.publishing.verify({'packageId':p['id'],'taskId':tid,'confirmed':True,'result':'verified','note':'虚构平台ID逐项人工核验'})
        self.assertEqual('verified',self.publishing.state()['publishingPackages'][0]['state'])

    def test_selection_external_finished_six_accounts_to_verified_receipts(self):
        tasks=self.finished(6);source=self.store.state()['tasks'];p,s,a=self.execution(tasks)
        self.assertEqual([5,1],[len([v for v in b['slots'] if v]) for b in p['batches']])
        for b in p['batches']:
            for slot in filter(None,b['slots']):
                cells=read_workbook(Path(slot['workbookPath']).read_bytes())['sheets'][0]['cells']
                self.assertEqual('7',cells[(2,10)]['text']);self.assertNotEqual(7,slot['port'])
                self.assertEqual('9000000000000000001',cells[(2,6)]['text'])
        self.complete(p,s,a);self.assertEqual(18,len(a.calls))
        for port,item in a.calls:
            self.assertEqual(item['accountId'],a.ports[port])
            self.assertEqual(1,len(item['files']))
            self.assertEqual(item['platform']=='douyin','变速' in Path(item['files'][0]['path']).parts)
        self.assertEqual(source,self.store.state()['tasks'])
        restored=PublishingService(ConsoleStore(self.store.root),self.editing)
        self.assertTrue(all(r['state']=='verified' for r in restored.state()['publishingTasks'].values()))

    def test_selection_external_material_edit_report_watch_to_publish(self):
        tasks=self.generate();t=tasks[0]
        self.video(self.root/t['fields'][2]/t['fields'][5]/'material.mp4');self.accept(tasks,'materials')
        p=self.editing.prepare([t['id']])['package']
        helper=editingfixtures.EditingTests();helper.rules=self.root/'fiction-rules.txt';helper.rules.write_text('虚构词',encoding='utf8')
        exe=self.root/'fiction.bin';exe.write_bytes(b'NOT EXECUTABLE')
        self.editing.gui(p['id'],{'confirmed':True,'targetCount':1,'rulesPath':str(helper.rules),'executablePath':str(exe),'decoration':'无装饰','version':'fiction','namingMode':'id','speedEnabled':True})
        helper.reports(p);self.editing.collect(p['id'])
        with self.assertRaises(ImportFailure):self.execution(tasks)
        self.editing.verify(t['id'],'normal','虚构普通成片观看',True)
        with self.assertRaises(ImportFailure):self.execution(tasks)
        self.editing.verify(t['id'],'speed','虚构变速成片观看',True)
        p,s,a=self.execution(tasks);self.complete(p,s,a)

    def test_native_material_report_and_editing_report_to_verified_publish(self):
        # Existing isolated material fixture uses its own loopback report API
        # and SQLite download report; it never starts a material program.
        fixture=editingfixtures.EditingTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        self.store=fixture.store;self.editing=fixture.service
        self.publishing=PublishingService(self.store,self.editing)
        task=fixture.task
        material=fixture.material.state()['materialTasks'][task['id']]
        self.assertEqual('ready',material['state'])
        package=fixture.prepare();self.editing.gui(package['id'],fixture.body)
        fixture.reports(package);self.editing.collect(package['id'])
        for kind in ('normal','speed'):
            self.editing.verify(task['id'],kind,'虚构原链路逐项观看',True)
        p,s,a=self.execution([task]);self.complete(p,s,a)
        self.assertEqual(2,len(a.calls))

    def test_unknown_restart_never_resubmits_and_cannot_collect(self):
        p,s,a=self.execution(self.finished(),mode='douyin');a.failure='unknown'
        run=s.start({'packageId':p['id'],'batchIndex':1,'confirmed':True})['run'];aid=run['accounts'][0]['accountId']
        s.run_account(run['id'],aid)
        restarted=PublishExecutionService(ConsoleStore(self.store.root),self.publishing,launch_workers=False,browser=a)
        restarted.recover_interrupted();restarted.run_account(run['id'],aid)
        self.assertEqual(1,len(a.calls));self.assertEqual('unknown',restarted.state()['runs'][0]['items'][0]['state'])
        with self.assertRaises(ImportFailure):restarted.native_receipts(p['id'])

    def test_exact_qianchuan_close_failure_blocks_success_receipt(self):
        p,s,a=self.execution(self.finished(),mode='qianchuan');a.failure='close'
        run=s.start({'packageId':p['id'],'batchIndex':1,'confirmed':True})['run'];aid=run['accounts'][0]['accountId'];s.run_account(run['id'],aid)
        item=s.state()['runs'][0]['items'][0]
        self.assertEqual('closing_failed',item['state']);self.assertEqual(a.ids[item['id']],item['platformId'])
        with self.assertRaises(ImportFailure):s.native_receipts(p['id'])
        self.assertNotEqual('verified',self.publishing.state()['publishingTasks'][next(iter(p['tasks']))]['state'])


if __name__=='__main__':unittest.main()
