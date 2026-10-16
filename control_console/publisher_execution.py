"""Durable account-isolated publishing; never writes business workbooks."""
from __future__ import annotations
import asyncio
import importlib
import inspect
import json
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import time
import threading
import uuid
from collections import Counter
from datetime import datetime
from .importers import ImportFailure
from .store import dump, now


class ExecutionControl(Exception):
    pass


class PublishExecutionService:
    def __init__(self, store, publishing, logs=None, launch_workers=True, browser=None):
        self.store, self.publishing, self.logs = store, publishing, logs
        self.launch_workers, self.browser = launch_workers, browser
        self._workers = {}
        self._workers_lock = threading.Lock()
        with store.connection(write=True) as conn:
            conn.executescript('''CREATE TABLE IF NOT EXISTS publisher_objects(kind TEXT NOT NULL,id TEXT NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(kind,id));
            CREATE TABLE IF NOT EXISTS publisher_leases(account_id TEXT PRIMARY KEY,port INTEGER UNIQUE NOT NULL,run_id TEXT NOT NULL);''')

    def _adapter(self):
        try:return self.browser or importlib.import_module('.publisher_browser', __package__)
        except Exception as exc:self._fail('浏览器适配器不可用：'+str(exc))

    @staticmethod
    def _fail(message):
        raise ImportFailure('publisher_execution', message)

    def _all(self, conn, kind):
        return [json.loads(r[0]) for r in conn.execute('SELECT payload FROM publisher_objects WHERE kind=? ORDER BY rowid',(kind,))]

    def _get(self, conn, kind, key):
        row=conn.execute('SELECT payload FROM publisher_objects WHERE kind=? AND id=?',(kind,key)).fetchone()
        return json.loads(row[0]) if row else None

    def _put(self, conn, kind, key, value):
        conn.execute('INSERT INTO publisher_objects VALUES (?,?,?) ON CONFLICT(kind,id) DO UPDATE SET payload=excluded.payload',(kind,key,dump(value)))

    def _run(self, conn, key):
        run=self._get(conn,'run',key)
        if not run:self._fail('执行批次不存在。')
        return run

    def _busy(self, conn, account):
        return conn.execute('SELECT 1 FROM publisher_leases WHERE account_id=?',(account,)).fetchone() is not None

    def _summary(self, run):
        counts={s:sum(i['state']==s for i in run['items']) for s in ('published','pending_review','closed','unknown','failed','closing_failed','queued','paused','stopped')}
        run['aggregate']={'total':len(run['items']),'reported':sum(counts[s] for s in ('published','closed')),'pendingReview':counts['pending_review'],'unknown':counts['unknown'],'failed':counts['failed']+counts['closing_failed'],'pending':counts['queued']+counts['paused'],'verified':0,'counts':counts}
        return run

    def state(self):
        self.poll_workers()
        self.recover_interrupted(startup=False)
        with self.store.connection() as conn:
            return {'ok':True,'bindings':self._all(conn,'binding'),'templates':self._all(conn,'template'),'runs':[self._summary(r) for r in self._all(conn,'run')],'capabilities':{'maxAccountsPerBatch':5,'manualBatchStart':True,'automaticRetry':False,'productionVerified':False,'reconcileReadOnly':True}}

    @staticmethod
    def _await(value):
        return asyncio.run(value) if inspect.isawaitable(value) else value

    def probe(self, body):
        port=body.get('port') if isinstance(body,dict) else None
        if type(port) is not int or not 1<=port<=65535:self._fail('请填写有效本机端口。')
        try:evidence=self._await(self._adapter().probe_browser(port))
        except Exception as exc:self._fail('身份探测失败：'+str(exc))
        if not isinstance(evidence,dict):self._fail('端口身份探测没有返回可靠证据。')
        probe={'id':str(uuid.uuid4()),'port':port,'probedAt':now(),'expiresAt':time.time()+300,'evidence':evidence}
        with self.store.connection(write=True) as conn:self._put(conn,'probe',str(port),probe)
        return {'ok':True,'probe':probe,**evidence}

    def bind(self, body):
        if not isinstance(body,dict) or body.get('confirmed') is not True:self._fail('请明确核对账号身份后绑定。')
        account,port,uid=body.get('accountId'),body.get('port'),body.get('uid')
        if not isinstance(account,str) or not account or not isinstance(uid,str) or not uid or type(port) is not int:self._fail('账号、UID和独立端口不能为空。')
        with self.store.connection(write=True) as conn:
            if self._busy(conn,account):self._fail('账号执行尚未结束，不能修改绑定。')
            probe=self._get(conn,'probe',str(port));ev=probe['evidence'] if probe else {}
            douyin=ev.get('douyin') or {};qc=ev.get('qianchuan') or {}
            reliable=(ev.get('identityReliable') is True and ev.get('accountId')==account and ev.get('uid')==uid and str(ev.get('advertiserId') or '')==str(body.get('advertiserId') or '')) or (douyin.get('uid')==uid and douyin.get('accountId')==account and (not body.get('advertiserId') or qc.get('accountId')==account and str(qc.get('advertiserId'))==str(body['advertiserId'])))
            if not probe or probe['expiresAt']<time.time() or not reliable:self._fail('请先探测并核对可靠UID/广告账户证据。')
            if any(b['port']==port and b['accountId']!=account for b in self._all(conn,'binding')):self._fail('该端口已绑定其他账号。')
            for row in conn.execute('SELECT payload FROM publishing_packages'):
                package=json.loads(row[0])
                for batch in package['batches']:
                    for slot in batch['slots']:
                        if slot and slot['accountId']==account and slot['port']!=port:self._fail('端口必须与已准备发文包的显式端口一致。')
            binding={'accountId':account,'port':port,'uid':uid,'advertiserId':str(body.get('advertiserId') or ''),'confirmedAt':now(),'probeId':probe['id']}
            self._put(conn,'binding',account,binding)
        return {'ok':True,'binding':binding}

    def template(self, body):
        if not isinstance(body,dict) or body.get('confirmed') is not True:self._fail('请确认完整发布模板。')
        account,platform,params=body.get('accountId'),body.get('platform'),body.get('parameters')
        if not isinstance(account,str) or not account or platform not in ('douyin','qianchuan') or not isinstance(params,dict) or not params or not any(v not in ('',None,[],{}) for v in params.values()):self._fail('发布模板不能为空，也不能依赖页面默认参数。')
        validator=getattr(self._adapter(),'validate_template',None)
        if validator:
            try:validator(platform,params)
            except Exception as exc:self._fail('模板校验失败：'+str(exc))
        with self.store.connection(write=True) as conn:
            if self._busy(conn,account):self._fail('账号执行尚未结束，不能修改模板。')
            key=account+':'+platform;old=self._get(conn,'template',key)
            template={'accountId':account,'platform':platform,'parameters':params,'version':(old['version']+1 if old else 1),'confirmedAt':now(),'confirmed':True}
            self._put(conn,'template',key,template)
        return {'ok':True,'template':template}

    def start(self, body):
        if not isinstance(body,dict) or body.get('confirmed') is not True or type(body.get('batchIndex')) is not int:self._fail('请人工确认并启动指定批次。')
        with self.store.connection(write=True) as conn:
            package=self.publishing._package(conn,body)
            for run in self._all(conn,'run'):
                if run['packageId']==package['id'] and run['batchIndex']==body['batchIndex']:return {'ok':True,'run':self._summary(run),'reused':True}
            if package['state']!='prepared' or package.get('handoff') or package.get('executionOwner') not in (None,'native'):self._fail('发文包已被人工交接或回收，不能重复执行。')
            receipts=self.publishing._check(package)
            if any(r.get('M') or r.get('N') for r in receipts.values()):self._fail('工作副本已有回执，不能启动自动发文。')
            batch=next((b for b in package['batches'] if b['index']==body['batchIndex']),None)
            if not batch:self._fail('批次不存在。')
            slots=[s for s in batch['slots'] if s]
            if not 1<=len(slots)<=5:self._fail('每批只能启动1至5个账号。')
            run={'id':str(uuid.uuid4()),'packageId':package['id'],'batchIndex':batch['index'],'createdAt':now(),'state':'queued','accounts':[],'items':[]}
            for slot in slots:
                account=slot['accountId'];binding=self._get(conn,'binding',account)
                if not binding or binding['port']!=slot['port']:self._fail('请先绑定与发文包一致的账号端口。')
                if self._busy(conn,account) or conn.execute('SELECT 1 FROM publisher_leases WHERE port=?',(slot['port'],)).fetchone():self._fail('账号或端口正被其他执行批次占用。')
                platforms=['douyin','qianchuan'] if package['mode']=='both' else [package['mode']]
                if 'qianchuan' in platforms and not binding.get('advertiserId'):self._fail('千川执行必须绑定已核对的广告账户ID。')
                templates={p:self._get(conn,'template',account+':'+p) for p in platforms}
                if any(not t for t in templates.values()):self._fail('每个发布平台必须先保存确认模板。')
                run['accounts'].append({'accountId':account,'port':slot['port'],'state':'queued','binding':binding,'templates':templates,'control':'resume','pid':None})
                for platform in platforms:
                    for task_id in slot['taskIds']:
                        record=package['tasks'][task_id]
                        # Recheck original source identity as well as isolated copies.
                        self.publishing._evidence(conn,task_id,['speed' if platform=='douyin' else 'normal'])
                        files=sorted([f['copy'] for f in record['videos']['speed' if platform=='douyin' else 'normal']],key=lambda f:(f['path'].casefold(),f['path']))
                        groups=[[f] for f in files] if platform=='douyin' else [files]
                        for group in groups:run['items'].append({'id':str(uuid.uuid4()),'taskId':task_id,'accountId':account,'platform':platform,'files':group,'fields':record['fields'],'template':templates[platform],'state':'queued','stage':'queued','platformId':None,'evidence':None})
                conn.execute('INSERT INTO publisher_leases VALUES (?,?,?)',(account,slot['port'],run['id']))
            # Optional file covers are pinned alongside the execution snapshot.
            # A path string alone must not allow later edits to change a run.
            for account in run['accounts']:
                template=account['templates'].get('douyin')
                if template and template['parameters'].get('coverMode')=='file':
                    source=Path(template['parameters'].get('coverPath',''))
                    if not source.is_absolute() or source.is_symlink() or not source.is_file() or source.suffix.lower() not in ('.png','.jpg','.jpeg','.webp') or not 0<source.stat().st_size<=20*1024*1024:self._fail('封面必须是本机20MiB以内的普通图片文件。')
                    raw=source.read_bytes();sha=hashlib.sha256(raw).hexdigest()
                    target=self.store._path('publisher-execution',run['id'],'covers',sha+source.suffix.lower())
                    self.store._write_once(target,raw)
                    template['parameters']['coverPath']=str(target)
                    template['coverIdentity']={'path':str(target),'bytes':len(raw),'sha256':sha}
            package['executionOwner']='native'
            conn.execute('UPDATE publishing_packages SET payload=? WHERE id=?',(dump(package),package['id']))
            self._put(conn,'run',run['id'],run)
        if self.logs:
            try:
                for account in run['accounts']:
                    label=hashlib.sha256(account['accountId'].encode()).hexdigest()[:16]
                    self.logs.configure({'id':'publisher-'+run['id']+'-'+label,'software':'publish','label':'自动发文 '+run['id'][:8]+' / '+account['accountId'],'path':str(self._log_path(run['id'],account['accountId'])),'encoding':'utf-8','rotationGlob':'','enabled':True})
            except Exception as exc:
                for account in run['accounts']:self._finish_account(run['id'],account['accountId'],'failed','日志来源登记失败，未启动：'+str(exc))
                self._fail('日志来源登记失败，本批未启动。请查看执行记录。')
        if self.launch_workers:
            for account in run['accounts']:
                stderr=None
                try:
                    kwargs={'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {}
                    diagnostic=self._log_path(run['id'],account['accountId']).with_suffix('.worker.log')
                    diagnostic.parent.mkdir(parents=True,exist_ok=True);stderr=diagnostic.open('ab')
                    process=subprocess.Popen([sys.executable,'-B','-m','tools.publisher_worker','--runtime',str(self.store.root),'--run-id',run['id'],'--account-id',account['accountId']],cwd=str(Path(__file__).resolve().parent.parent),stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=stderr,env={**os.environ,'PYTHONUTF8':'1','PYTHONDONTWRITEBYTECODE':'1'},**kwargs)
                    with self._workers_lock:self._workers[(run['id'],account['accountId'])]=(process,stderr)
                    self._update_account(run['id'],account['accountId'],pid=process.pid)
                except Exception as exc:
                    if stderr:stderr.close()
                    self._finish_account(run['id'],account['accountId'],'failed','派生进程失败：'+str(exc))
        with self.store.connection() as conn:run=self._run(conn,run['id'])
        return {'ok':True,'run':self._summary(run),'reused':False}

    def _update_account(self, run_id, account_id, **changes):
        with self.store.connection(write=True) as conn:
            run=self._run(conn,run_id);account=next(a for a in run['accounts'] if a['accountId']==account_id);account.update(changes);self._put(conn,'run',run_id,run)

    def _finish_account(self, run_id, account_id, state, error=None):
        with self.store.connection(write=True) as conn:
            run=self._run(conn,run_id);account=next(a for a in run['accounts'] if a['accountId']==account_id);account.update(state=state,error=error)
            if state in ('failed','unknown','stopped'):
                for item in run['items']:
                    if item['accountId']==account_id and item['state']=='queued':item.update(state='stopped',error=error)
            if state not in ('paused','unknown','closing_failed','pending_review'):conn.execute('DELETE FROM publisher_leases WHERE account_id=? AND run_id=?',(account_id,run_id))
            states=[a['state'] for a in run['accounts']];run['state']='running' if any(s in ('queued','running') for s in states) else 'unknown' if any(s in ('unknown','closing_failed') for s in states) else 'paused' if 'paused' in states else 'pending_review' if 'pending_review' in states else 'reported' if all(s=='reported' for s in states) else 'failed' if 'failed' in states else 'stopped'
            self._put(conn,'run',run_id,run)

    def control(self, body):
        if not isinstance(body,dict) or body.get('action') not in ('pause','resume','stop'):self._fail('请选择暂停、继续或停止。')
        with self.store.connection(write=True) as conn:
            run=self._run(conn,body.get('runId'));selected=[a for a in run['accounts'] if not body.get('accountId') or a['accountId']==body['accountId']]
            if not selected:self._fail('账号不属于该执行批次。')
            for account in selected:
                if account['state'] in ('unknown','closing_failed','pending_review','reported','failed','stopped'):continue
                account['control']=body['action']
            self._put(conn,'run',run['id'],run)
        return {'ok':True,'run':self._summary(run)}

    def _checkpoint(self, run_id, account_id, item_id, stage, evidence=None):
        with self.store.connection(write=True) as conn:
            run=self._run(conn,run_id);account=next(a for a in run['accounts'] if a['accountId']==account_id);item=next(i for i in run['items'] if i['id']==item_id)
            if account['state']=='unknown':raise ExecutionControl('recovered')
            after_submit=item.get('submitIntent',False)
            if not after_submit and account['control'] in ('pause','stop'):raise ExecutionControl(account['control'])
            item.update(stage=stage,state='running',updatedAt=now())
            if stage=='before_submit':item['submitIntent']=True;item['state']='submit_intent'
            if evidence is not None:item['evidence']=evidence
            if isinstance(evidence,dict) and evidence.get('platformId'):item['platformId']=evidence['platformId']
            self._put(conn,'run',run_id,run)
        self._log(run_id,account_id,item_id,stage)

    def _log_path(self, run_id, account_id):
        name=hashlib.sha256(account_id.encode()).hexdigest()[:16]+'.log'
        return self.store._path('publisher-execution',run_id,name)

    def _log(self, run_id, account_id, item_id, message):
        path=self._log_path(run_id,account_id);path.parent.mkdir(parents=True,exist_ok=True)
        with self.store.connection() as conn:
            run=self._run(conn,run_id);item=next(i for i in run['items'] if i['id']==item_id)
        with path.open('a',encoding='utf-8') as stream:stream.write(now()+' INFO taskId='+item['taskId']+' '+dump({'runId':run_id,'accountId':account_id,'itemId':item_id,'message':message})+'\n')

    def run_account(self, run_id, account_id):
        with self.store.connection(write=True) as conn:
            run=self._run(conn,run_id);account=next(a for a in run['accounts'] if a['accountId']==account_id)
            if account['state']!='queued':return
            account.update(state='running',pid=os.getpid());run['state']='running';self._put(conn,'run',run_id,run)
        while True:
            with self.store.connection() as conn:
                run=self._run(conn,run_id);account=next(a for a in run['accounts'] if a['accountId']==account_id);item=next((i for i in run['items'] if i['accountId']==account_id and i['state']=='queued'),None)
            if not item:
                pending=any(i['accountId']==account_id and i['state']=='pending_review' for i in run['items'])
                self._finish_account(run_id,account_id,'pending_review' if pending else 'reported');return
            if account['control']=='stop':self._finish_account(run_id,account_id,'stopped');return
            if account['control']=='pause':self._update_account(run_id,account_id,state='paused');time.sleep(.25);continue
            try:
                cover=item['template'].get('coverIdentity')
                if cover:
                    path=Path(cover['path'])
                    if not path.is_file() or path.is_symlink() or path.stat().st_size!=cover['bytes'] or hashlib.sha256(path.read_bytes()).hexdigest()!=cover['sha256']:raise RuntimeError('冻结封面文件已变化，禁止发布。')
                self._checkpoint(run_id,account_id,item['id'],'checking')
                result=self._await(self._adapter().execute_item(account['port'],account['binding'],item['template'],item,lambda stage,evidence=None:self._checkpoint(run_id,account_id,item['id'],stage,evidence)))
                if not isinstance(result,dict) or result.get('state') not in ('published','pending_review','closed') or not result.get('platformId'):raise RuntimeError('适配器未返回精确作品/计划ID。')
                if item['platform']=='douyin' and result['state']=='closed':raise RuntimeError('抖音作品不能使用千川计划关闭状态。')
                if item['platform']=='qianchuan' and result['state']!='closed':
                    self._checkpoint(run_id,account_id,item['id'],'closing',{'platformId':result['platformId'],'result':result})
                    raise RuntimeError('千川计划尚未准确关闭。')
                with self.store.connection(write=True) as conn:
                    current=self._run(conn,run_id);target=next(i for i in current['items'] if i['id']==item['id']);target.update(result);target['updatedAt']=now();self._put(conn,'run',run_id,current)
            except ExecutionControl as exc:
                if str(exc)=='recovered':return
                with self.store.connection(write=True) as conn:
                    current=self._run(conn,run_id);target=next(i for i in current['items'] if i['id']==item['id']);target['state']='queued';self._put(conn,'run',run_id,current)
                if str(exc)=='stop':self._finish_account(run_id,account_id,'stopped');return
            except BaseException as exc:
                with self.store.connection(write=True) as conn:
                    current=self._run(conn,run_id);target=next(i for i in current['items'] if i['id']==item['id'])
                    state='closing_failed' if target['platform']=='qianchuan' and target.get('platformId') else 'unknown' if target.get('submitIntent') else 'failed'
                    target.update(state=state,error=type(exc).__name__+': '+str(exc));self._put(conn,'run',run_id,current)
                self._log(run_id,account_id,item['id'],str(exc));self._finish_account(run_id,account_id,state,str(exc));return

    def recover_interrupted(self, startup=True):
        """Explicit server-start recovery: never spawn or repeat a submission."""
        with self.store.connection(write=True) as conn:
            for run in self._all(conn,'run'):
                for account in run['accounts']:
                    if account['state'] in ('queued','running','paused'):
                        pid=account.get('pid')
                        if pid and self._pid_alive(pid):continue
                        if not pid and not startup:
                            # The parent commits a run before spawning children.
                            # Do not fence that normal startup window on a GET.
                            try:age=time.time()-datetime.fromisoformat(run['createdAt']).timestamp()
                            except (ValueError,TypeError):age=60
                            if age<60:continue
                        account.update(state='unknown',error='进程重启，待只读恢复；禁止自动重提。')
                        for item in run['items']:
                            if item['accountId']==account['accountId'] and item['state'] in ('queued','running','submit_intent'):item['state']='unknown'
                        run['state']='unknown'
                self._put(conn,'run',run['id'],run)

    @staticmethod
    def _pid_alive(pid):
        """Observe processes only. os.kill(pid, 0) is destructive on Windows."""
        if type(pid) is not int or pid<=0:return False
        if os.name=='nt':
            import ctypes
            from ctypes import wintypes
            kernel=ctypes.WinDLL('kernel32',use_last_error=True)
            kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
            kernel.OpenProcess.restype=wintypes.HANDLE
            kernel.GetExitCodeProcess.argtypes=[wintypes.HANDLE,ctypes.POINTER(wintypes.DWORD)]
            kernel.CloseHandle.argtypes=[wintypes.HANDLE]
            handle=kernel.OpenProcess(0x1000,False,pid)
            if not handle:return ctypes.get_last_error()==5  # Access denied: conservatively alive.
            try:
                code=wintypes.DWORD()
                return not kernel.GetExitCodeProcess(handle,ctypes.byref(code)) or code.value==259
            finally:kernel.CloseHandle(handle)
        try:os.kill(pid,0);return True
        except PermissionError:return True
        except ProcessLookupError:return False

    def poll_workers(self):
        """Detect exits of workers created here, without terminating any process."""
        with self._workers_lock:
            ended=[(key,p,stream,p.poll()) for key,(p,stream) in self._workers.items() if p.poll() is not None]
            for key,_,_,_ in ended:self._workers.pop(key,None)
        for (run_id,account_id),_,stream,code in ended:
            stream.close()
            with self.store.connection(write=True) as conn:
                run=self._run(conn,run_id);account=next(a for a in run['accounts'] if a['accountId']==account_id)
                if account['state'] not in ('queued','running','paused'):continue
                unfinished=[i for i in run['items'] if i['accountId']==account_id and i['state'] not in ('published','pending_review','closed','failed','stopped')]
                uncertain=any(i.get('submitIntent') for i in unfinished)
                for item in unfinished:item.update(state='closing_failed' if item['platform']=='qianchuan' and item.get('platformId') else 'unknown' if item.get('submitIntent') else 'failed',error='执行进程异常退出，退出码 '+str(code))
                self._put(conn,'run',run_id,run)
            self._finish_account(run_id,account_id,'unknown' if uncertain else 'failed','执行进程退出，退出码 '+str(code)+'；不会自动重启或重发。')

    def reconcile(self, body):
        with self.store.connection() as conn:
            run=self._run(conn,body.get('runId'));item=next((i for i in run['items'] if i['id']==body.get('itemId')),None)
            if not item:self._fail('执行项不存在。')
            account=next(a for a in run['accounts'] if a['accountId']==item['accountId'])
            if item['state'] not in ('unknown','closing_failed','pending_review'):self._fail('该执行项不需要只读恢复查询。')
        try:result=self._await(self._adapter().reconcile_item(account['port'],account['binding'],item))
        except Exception as exc:self._fail('只读恢复查询失败：'+str(exc))
        with self.store.connection(write=True) as conn:
            run=self._run(conn,run['id']);target=next(i for i in run['items'] if i['id']==item['id']);target['reconciliation']={'queriedAt':now(),'result':result}
            if isinstance(result,dict) and result.get('state') in ('published','pending_review','closed') and result.get('platformId'):
                if target.get('platformId') and result['platformId']!=target['platformId']:self._fail('查询返回其他平台ID，不能更新本项。')
                if target['platform']=='douyin' and result['state']=='closed':self._fail('抖音作品不能使用千川关闭状态。')
                if target['platform']=='qianchuan' and result['state']!='closed':
                    target.update(platformId=result['platformId'],evidence=result.get('evidence'),state='closing_failed')
                else:target.update(result)
            self._put(conn,'run',run['id'],run)
            account_items=[i for i in run['items'] if i['accountId']==item['accountId']]
            account_state='reported' if all(i['state'] in ('published','closed') for i in account_items) else None
        if account_state:
            self._finish_account(run['id'],item['accountId'],account_state)
            with self.store.connection() as conn:run=self._run(conn,run['id'])
        return {'ok':True,'run':self._summary(run),'item':target}

    def native_receipts(self, package_id):
        with self.store.connection() as conn:
            package=self.publishing._package(conn,{'packageId':package_id})
            runs=[r for r in self._all(conn,'run') if r['packageId']==package_id]
            if {r['batchIndex'] for r in runs}!={b['index'] for b in package['batches']}:self._fail('尚有批次未执行，不能回收成功回执。')
            items=[i for r in runs for i in r['items']]
            if not items or any(i['state'] not in ('published','closed') or not i.get('platformId') or i['platform']=='qianchuan' and i['state']!='closed' for i in items):self._fail('存在待审、未知、失败或未关闭计划，不能回收成功回执。')
            identifiers=[(i['accountId'],i['platform'],i['platformId']) for i in items]
            if len(set(identifiers))!=len(identifiers):self._fail('多个执行项指向同一作品或计划，不能当作逐项成功。')
            result={}
            for task_id in package['tasks']:
                rows=[i for i in items if i['taskId']==task_id]
                platforms={i['platform'] for i in rows}
                expected={'douyin','qianchuan'} if package['mode']=='both' else {package['mode']}
                if platforms!=expected:self._fail('自动执行清单不完整。')
                for platform in expected:
                    kind='speed' if platform=='douyin' else 'normal'
                    expected_files=[v['copy'] for v in package['tasks'][task_id]['videos'][kind]]
                    actual_files=[f for row in rows if row['platform']==platform for f in row['files']]
                    identity=lambda files:Counter((f['path'],f.get('sha256')) for f in files)
                    if identity(expected_files)!=identity(actual_files):self._fail('逐视频执行证据缺失、重复或归属变化。')
                    if platform=='douyin' and any(len(row['files'])!=1 or row['state']!='published' for row in rows if row['platform']==platform):self._fail('抖音每条视频须独立确认已发布。')
                result[task_id]={'M':'已发布' if 'douyin' in platforms else '', 'N':'计划已发布' if 'qianchuan' in platforms else '', 'evidence':[{'itemId':i['id'],'platform':i['platform'],'platformId':i['platformId'],'state':i['state'],'evidence':i.get('evidence')} for i in rows]}
            return result
