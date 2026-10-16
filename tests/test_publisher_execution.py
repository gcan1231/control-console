"""Fictional execution tests; no CDP or business programs."""
import json
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from concurrent.futures import ThreadPoolExecutor
from control_console.store import ConsoleStore,dump
from control_console.importers import ImportFailure
from control_console.publisher_execution import PublishExecutionService

class Browser:
    def __init__(self):self.calls=[];self.fail=None
    async def probe_browser(self,port):return {'identityReliable':True,'accountId':'a'+str(port),'uid':'u'+str(port),'advertiserId':'ad'}
    def validate_template(self,platform,parameters):pass
    async def execute_item(self,port,binding,template,item,checkpoint):
        self.calls.append(item['id']);checkpoint('uploading');checkpoint('before_submit')
        if self.fail=='unknown':raise RuntimeError('interrupted after intent')
        checkpoint('submitted',{'platformId':'id-'+item['id']})
        if self.fail=='close':raise RuntimeError('close failed')
        return {'state':'closed' if item['platform']=='qianchuan' else 'published','platformId':'id-'+item['id'],'evidence':{'exact':True}}
    async def reconcile_item(self,port,binding,item):return {'state':'unknown'}

class Publishing:
    def __init__(self,store,count=6,mode='both'):
        self.store=store
        self.package={'id':'pkg','state':'prepared','mode':mode,'handoff':None,'tasks':{},'batches':[]}
        for x in range(1,count+1):
            if (x-1)%5==0:self.package['batches'].append({'index':len(self.package['batches'])+1,'slots':[]})
            task='t'+str(x);account='a'+str(x)
            self.package['batches'][-1]['slots'].append({'accountId':account,'port':x,'taskIds':[task]})
            self.package['tasks'][task]={'fields':['fiction']*16,'videos':{k:[{'copy':{'path':'fiction-'+str(x)+'.mp4'}}] for k in ('speed','normal')}}
        with store.connection(write=True) as conn:
            conn.execute('CREATE TABLE publishing_packages(id TEXT PRIMARY KEY,payload TEXT)');conn.execute('INSERT INTO publishing_packages VALUES (?,?)',('pkg',dump(self.package)))
    def _package(self,conn,body):return json.loads(conn.execute('SELECT payload FROM publishing_packages WHERE id=?',(body['packageId'],)).fetchone()[0])
    def _check(self,p):return {k:{'M':'','N':''} for k in p['tasks']}
    def _evidence(self,*args):return None

class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.store=ConsoleStore(self.temp.name);self.publishing=Publishing(self.store);self.browser=Browser();self.service=PublishExecutionService(self.store,self.publishing,launch_workers=False,browser=self.browser)
        for x in range(1,7):
            self.service.probe({'port':x});self.service.bind({'accountId':'a'+str(x),'port':x,'uid':'u'+str(x),'advertiserId':'ad','confirmed':True})
            for p in ('douyin','qianchuan'):self.service.template({'accountId':'a'+str(x),'platform':p,'parameters':{'fiction':'yes'},'confirmed':True})
    def start(self,batch=1):return self.service.start({'packageId':'pkg','batchIndex':batch,'confirmed':True})
    def test_six_accounts_manual_batches_and_frozen_templates(self):
        run=self.start()['run'];self.assertEqual(5,len(run['accounts']));self.assertEqual(10,len(run['items']));self.assertEqual(1,len(self.start(2)['run']['accounts']))
        with self.assertRaises(ImportFailure):self.service.template({'accountId':'a1','platform':'douyin','parameters':{'changed':True},'confirmed':True})
    def test_wrong_identity_and_empty_template(self):
        with self.assertRaises(ImportFailure):self.service.bind({'accountId':'wrong','port':1,'uid':'u1','advertiserId':'ad','confirmed':True})
        with self.assertRaises(ImportFailure):self.service.template({'accountId':'a1','platform':'douyin','parameters':{},'confirmed':True})
    def test_concurrent_start_is_idempotent(self):
        with ThreadPoolExecutor(2) as pool:results=list(pool.map(lambda _:self.start(),range(2)))
        self.assertEqual(results[0]['run']['id'],results[1]['run']['id']);self.assertEqual(1,sum(not r['reused'] for r in results))
    def test_serial_both_and_no_repeat(self):
        run=self.start()['run'];self.service.run_account(run['id'],'a1');self.service.run_account(run['id'],'a1')
        self.assertEqual(2,len(self.browser.calls));items=self.service.state()['runs'][0]['items'][:2];self.assertEqual(['published','closed'],[i['state'] for i in items]);self.assertEqual(0,self.service.state()['runs'][0]['aggregate']['verified'])
    def test_submit_unknown_never_retries(self):
        run=self.start()['run'];self.browser.fail='unknown';self.service.run_account(run['id'],'a1');self.service.run_account(run['id'],'a1');self.assertEqual(1,len(self.browser.calls));self.assertEqual('unknown',self.service.state()['runs'][0]['items'][0]['state']);self.assertTrue(self.start()['reused'])
        with self.assertRaises(ImportFailure):self.service.native_receipts('pkg')
    def test_closing_failure_retains_plan_id(self):
        run=self.start()['run'];self.browser.fail='close';self.service.run_account(run['id'],'a1');item=self.service.state()['runs'][0]['items'][0];self.assertEqual('unknown',item['state'])
        # Qianchuan-specific closing failure, without an earlier douyin failure.
        run2=self.start(2)['run'];self.browser.fail=None
        original=self.browser.execute_item
        async def execute(*args):
            self.browser.fail='close' if args[3]['platform']=='qianchuan' else None
            return await original(*args)
        self.browser.execute_item=execute;self.service.run_account(run2['id'],'a6');item=self.service.state()['runs'][1]['items'][1];self.assertEqual('closing_failed',item['state']);self.assertTrue(item['platformId'])
    def test_stop_before_submit_prevents_browser_submission(self):
        run=self.start()['run'];self.service.control({'runId':run['id'],'accountId':'a1','action':'stop'});self.service.run_account(run['id'],'a1');self.assertEqual([],self.browser.calls);self.assertEqual('stopped',self.service.state()['runs'][0]['accounts'][0]['state'])
    def test_stop_after_qianchuan_submission_finishes_close(self):
        with self.store.connection(write=True) as conn:
            package=self.publishing._package(conn,{'packageId':'pkg'});package['mode']='qianchuan';conn.execute('UPDATE publishing_packages SET payload=?',(dump(package),))
        run=self.start(2)['run']
        async def execute(port,binding,template,item,checkpoint):
            checkpoint('before_submit');checkpoint('submitted',{'platformId':'exact-plan'})
            self.service.control({'runId':run['id'],'accountId':'a6','action':'stop'})
            checkpoint('closing',{'platformId':'exact-plan'})
            return {'state':'closed','platformId':'exact-plan','evidence':{'closed':True}}
        self.browser.execute_item=execute;self.service.run_account(run['id'],'a6');self.assertEqual('closed',self.service.state()['runs'][0]['items'][0]['state'])
    def test_reconcile_created_qianchuan_does_not_claim_closed(self):
        run=self.start()['run'];self.service.recover_interrupted();qc=next(i for i in run['items'] if i['platform']=='qianchuan')
        async def reconcile(*args):return {'state':'published','platformId':'open-plan','evidence':{'status':'running'}}
        self.browser.reconcile_item=reconcile
        result=self.service.reconcile({'runId':run['id'],'itemId':qc['id']});self.assertEqual('closing_failed',result['item']['state']);self.assertEqual(0,result['run']['aggregate']['reported'])
    def test_restart_marks_unknown_without_work(self):
        self.start();self.service.recover_interrupted();self.assertEqual('unknown',self.service.state()['runs'][0]['state']);self.assertEqual([],self.browser.calls)
    def test_reconcile_is_read_only(self):
        run=self.start()['run'];self.service.recover_interrupted();self.service.reconcile({'runId':run['id'],'itemId':run['items'][0]['id']});self.assertEqual([],self.browser.calls)

    @unittest.skipUnless(os.name=='nt','Windows process observation contract')
    def test_windows_process_probe_never_uses_kill(self):
        with patch('control_console.publisher_execution.os.kill',side_effect=AssertionError('kill is forbidden')):
            self.assertTrue(self.service._pid_alive(os.getpid()))
            self.assertFalse(self.service._pid_alive(0))

    def test_abnormal_worker_exit_preserves_unknown_and_never_respawns(self):
        run=self.start()['run'];item=run['items'][0]
        self.service._checkpoint(run['id'],'a1',item['id'],'before_submit')
        process=Mock();process.poll.return_value=9;stream=io.BytesIO()
        self.service._workers[(run['id'],'a1')]=(process,stream)
        current=self.service.state()['runs'][0]
        self.assertTrue(stream.closed);self.assertEqual('unknown',current['items'][0]['state'])
        self.assertEqual('unknown',current['accounts'][0]['state'])
        self.assertTrue(self.start()['reused']);self.service.run_account(run['id'],'a1')
        self.assertEqual([],self.browser.calls)
        with self.store.connection() as conn:self.assertTrue(self.service._busy(conn,'a1'))

    def test_pause_after_qianchuan_submission_still_closes_exact_plan(self):
        with self.store.connection(write=True) as conn:
            package=self.publishing._package(conn,{'packageId':'pkg'});package['mode']='qianchuan';conn.execute('UPDATE publishing_packages SET payload=?',(dump(package),))
        run=self.start(2)['run'];stages=[]
        async def execute(port,binding,template,item,checkpoint):
            checkpoint('before_submit');checkpoint('submitted',{'platformId':'exact-paused-plan'})
            self.service.control({'runId':run['id'],'accountId':'a6','action':'pause'})
            checkpoint('closing',{'platformId':'exact-paused-plan'});stages.append('closed')
            return {'state':'closed','platformId':'exact-paused-plan','evidence':{'closed':True}}
        self.browser.execute_item=execute;self.service.run_account(run['id'],'a6')
        item=self.service.state()['runs'][0]['items'][0]
        self.assertEqual(['closed'],stages);self.assertEqual('closed',item['state']);self.assertEqual('exact-paused-plan',item['platformId'])

    def test_file_cover_is_frozen_and_tampered_copy_blocks_submission(self):
        cover=Path(self.temp.name)/'fiction.jpg';cover.write_bytes(b'fictional image')
        self.service.template({'accountId':'a6','platform':'douyin','parameters':{'coverMode':'file','coverPath':str(cover)},'confirmed':True})
        run=self.start(2)['run'];frozen=Path(run['items'][0]['template']['parameters']['coverPath'])
        self.assertNotEqual(cover,frozen);self.assertEqual(b'fictional image',frozen.read_bytes())
        cover.write_bytes(b'changed source');self.assertEqual(b'fictional image',frozen.read_bytes())
        frozen.write_bytes(b'tampered copy');self.service.run_account(run['id'],'a6')
        self.assertEqual([],self.browser.calls);self.assertEqual('failed',self.service.state()['runs'][0]['items'][0]['state'])

    def test_native_receipts_require_every_frozen_video(self):
        with self.store.connection(write=True) as conn:
            package=self.publishing._package(conn,{'packageId':'pkg'})
            package['tasks']['t6']['videos']['speed'].append({'copy':{'path':'fiction-extra.mp4'}})
            conn.execute('UPDATE publishing_packages SET payload=?',(dump(package),))
        first=self.start()['run'];last=self.start(2)['run']
        for account in first['accounts']:self.service.run_account(first['id'],account['accountId'])
        self.service.run_account(last['id'],'a6');self.service.native_receipts('pkg')
        with self.store.connection(write=True) as conn:
            stored=self.service._run(conn,last['id']);index=next(n for n,i in enumerate(stored['items']) if i['platform']=='douyin');stored['items'].pop(index);self.service._put(conn,'run',last['id'],stored)
        with self.assertRaises(ImportFailure):self.service.native_receipts('pkg')

if __name__=='__main__':unittest.main()
