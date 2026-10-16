"""007: isolated five-slot handoffs; browser execution lives in its own module."""
from __future__ import annotations
import hashlib
import json
import shutil
import uuid
import io
import zipfile
from xml.etree import ElementTree as ET
from pathlib import Path
from .importers import ImportFailure, parse_import, read_workbook, MAX_BYTES
from .materials import safe_component
from .store import dump, now
from .workbooks import task_workbook
from .external_intake import external_videos


class PublishingService:
    def __init__(self, store, editing):
        self.store, self.editing = store, editing
        with store.connection(write=True) as conn:
            conn.executescript('''CREATE TABLE IF NOT EXISTS publishing_packages(id TEXT PRIMARY KEY,payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS publishing_tasks(task_id TEXT PRIMARY KEY REFERENCES tasks(id),
                package_id TEXT NOT NULL REFERENCES publishing_packages(id),payload TEXT NOT NULL);''')

    def state(self):
        with self.store.connection() as conn:
            return {'ok':True,'publishingPackages':[json.loads(r[0]) for r in conn.execute('SELECT payload FROM publishing_packages ORDER BY rowid')],
                    'publishingTasks':{r['task_id']:json.loads(r['payload']) for r in conn.execute('SELECT * FROM publishing_tasks')}}

    @staticmethod
    def _fail(message):
        raise ImportFailure('publishing', message)

    def _package(self, conn, body):
        if not isinstance(body,dict) or not isinstance(body.get('packageId'),str): self._fail('请选择有效发文包。')
        row=conn.execute('SELECT payload FROM publishing_packages WHERE id=?',(body['packageId'],)).fetchone()
        if not row:self._fail('发文包不存在。')
        return json.loads(row[0])

    def _path(self, path, package_id):
        target=Path(path);base=self.store._path('publishing',package_id)
        if not target.is_absolute() or not target.resolve().is_relative_to(base.resolve()) or target.is_symlink():self._fail('发文文件路径归属变化。')
        current=target
        while current!=self.store.root:
            if current.is_symlink():self._fail('发文目录不能经过链接。')
            current=current.parent
        return target

    def _evidence(self, conn, task_id, kinds):
        external=external_videos(conn,task_id,kinds,self.editing)
        if external:return external
        _,fields,_=self.editing._gate(conn,task_id)
        record=None
        for kind in kinds:
            _,record=self.editing._verification_record(conn,task_id,kind)
            if not record[kind]['ready'] or not record[kind]['verification']:self._fail('所选模式的成片尚未人工核验ready。')
        package=self.editing._package(conn,record['packageId'])
        mapping=next(r for r in package['rows'] if r['taskId']==task_id)
        videos={}
        for kind in kinds:
            directory=Path(mapping['normalDir' if kind=='normal' else 'speedDir'])
            expected=Path(package['materialRoot'])/safe_component(fields[2])/safe_component(fields[5])/'成片'
            if kind=='speed':expected=expected/'变速'
            if directory.resolve()!=expected.resolve():self._fail('成片目录与账号/完整商品ID不一致。')
            files=record[kind]['files']
            report=record['report'].get('row',{})
            reported=report.get('results',[]) if kind=='normal' else [report.get('freeze',{})]
            identities={str(Path(r.get('output' if kind=='normal' else 'path','')).resolve()).casefold():r.get('output_identity') for r in reported if isinstance(r,dict)}
            actual={str(p.resolve()).casefold() for p in directory.iterdir() if p.is_file() and p.suffix.lower()=='.mp4'}
            if not files or actual!={str(Path(f['path']).resolve()).casefold() for f in files}:self._fail('成片数量或直接MP4清单已变化。')
            videos[kind]=[]
            for file in files:
                path=Path(file['path'])
                if path.parent.resolve()!=directory.resolve() or path.suffix.lower()!='.mp4' or path.is_symlink():self._fail('成片文件归属无效。')
                identity=self.editing._identity(path)
                if identities.get(str(path.resolve()).casefold())!=[identity['bytes'],path.stat().st_mtime_ns]:self._fail('成片时间/大小身份与剪辑报告不一致。')
                if identity['bytes']!=file['bytes'] or identity['sha256']!=file['sha256']:self._fail('已核验成片身份变化。')
                videos[kind].append({**identity,'mtimeNs':path.stat().st_mtime_ns})
        return fields,record['packageId'],videos

    def prepare(self, body):
        if not isinstance(body,dict):self._fail('请提供发文配置。')
        ids=body.get('taskIds');mode=body.get('mode');ports=body.get('ports')
        if not isinstance(ids,list) or not 1<=len(ids)<=500 or any(not isinstance(i,str) or not i for i in ids) or len(set(ids))!=len(ids) or mode not in ('douyin','qianchuan','both') or not isinstance(ports,dict):self._fail('请选择不同任务、发布模式和独立端口。')
        kinds=['normal','speed'] if mode=='both' else ['speed'] if mode=='douyin' else ['normal']
        with self.store.connection(write=True) as conn:
            self.store._revalidate(conn)
            order=[r['id'] for r in conn.execute('SELECT tasks.id FROM tasks JOIN batches ON tasks.batch_id=batches.id ORDER BY batches.rowid,tasks.source_row,tasks.rowid') if r['id'] in ids]
            if len(order)!=len(ids):self._fail('任务不存在。')
            tasks={};accounts={};locations=set()
            for task_id in order:
                fields,editing_id,videos=self._evidence(conn,task_id,kinds)
                account=fields[3];safe_component(fields[2]);safe_component(fields[5])
                port=ports.get(account)
                if type(port) is not int or not 1<=port<=65535:self._fail('每账号必须显式填写1—65535独立端口。')
                location=(fields[2].casefold(),fields[5].casefold())
                if location in locations:self._fail('账号/商品目录重复，不能合并发文任务。')
                locations.add(location)
                if account in accounts and accounts[account]['accountName']!=fields[2]:self._fail('同账号名称不一致。')
                accounts.setdefault(account,{'accountId':account,'accountName':fields[2],'port':port,'taskIds':[]})['taskIds'].append(task_id)
                row=conn.execute('SELECT source_row,batch_id FROM tasks WHERE id=?',(task_id,)).fetchone()
                tasks[task_id]={'fields':fields,'sourceRow':row['source_row'],'batchId':row['batch_id'],'editingPackageId':editing_id,'videos':videos}
            if len({a['port'] for a in accounts.values()})!=len(accounts):self._fail('不同账号不能共用端口。')
            signature=hashlib.sha256(dump({'mode':mode,'tasks':tasks,'ports':{a:v['port'] for a,v in accounts.items()}}).encode()).hexdigest()
            old=list(conn.execute('SELECT payload FROM publishing_packages'))
            for row in old:
                package=json.loads(row[0])
                if package['signature']==signature:
                    self._check(package)
                    return {'ok':True,'package':package,'reused':True}
            if any(conn.execute('SELECT 1 FROM publishing_tasks WHERE task_id=?',(i,)).fetchone() for i in ids):self._fail('任务已有发文尝试，待核验或未知也不能自动重复准备。')
            package_id=str(uuid.uuid4());folder=self.store._path('publishing',package_id)
            package={'id':package_id,'mode':mode,'videoRoot':str(folder/'media'),'createdAt':now(),'state':'prepared','attempt':1,'batches':[],'tasks':tasks,'signature':signature,'handoff':None}
            try:
                for task_id,record in tasks.items():
                    for kind,files in record['videos'].items():
                        copied=[]
                        for file in files:
                            target=Path(package['videoRoot'])/record['fields'][2]/record['fields'][5]/'成片'
                            if kind=='speed':target=target/'变速'
                            target=target/safe_component(Path(file['path']).name);self._path(target,package_id)
                            copy=self.editing._copy(file,target)
                            copied.append({'source':file,'copy':{**copy,'mtimeNs':target.stat().st_mtime_ns}})
                        record['videos'][kind]=copied
                for index,account in enumerate(accounts.values()):
                    batch=index//5
                    if len(package['batches'])<=batch:package['batches'].append({'index':batch+1,'slots':[None]*5})
                    slot=index%5+1;path=folder/f'batch-{batch+1:02d}'/f'slot-{slot}.xlsx'
                    self._path(path,package_id)
                    self.store._write_once(path,task_workbook([{'fields':tasks[i]['fields']} for i in account['taskIds']]))
                    package['batches'][batch]['slots'][slot-1]={**account,'slot':slot,'workbookPath':str(path)}
                manifest=dump(package).encode();self.store._write_once(folder/'manifest.json',manifest)
                package['manifestSha256']=hashlib.sha256(manifest).hexdigest()
                conn.execute('INSERT INTO publishing_packages VALUES (?,?)',(package_id,dump(package)))
                for i in order:conn.execute('INSERT INTO publishing_tasks VALUES (?,?,?)',(i,package_id,dump({'packageId':package_id,'state':'prepared','attempt':1,'receipt':None,'verification':None})))
                self.store.freeze_evidence(conn,order)
            except Exception:
                # Only this freshly generated UUID folder is ours to remove.
                expected=self.store.root/'publishing'/package_id
                if folder.exists() and not folder.is_symlink() and folder.resolve()==expected and expected.is_relative_to(self.store.root):
                    try:
                        self._path(folder,package_id)
                        shutil.rmtree(folder)
                    except (OSError,ImportFailure):
                        pass
                raise
        return {'ok':True,'package':package,'reused':False}

    def _check(self,package):
        manifest=self._path(self.store._path('publishing',package['id'],'manifest.json'),package['id'])
        if self.editing._identity(manifest)['sha256']!=package['manifestSha256']:self._fail('内部发文清单被改动。')
        for record in package['tasks'].values():
            for files in record['videos'].values():
                expected={str(Path(f['copy']['path']).resolve()).casefold() for f in files}
                directory=Path(files[0]['copy']['path']).parent
                if {str(p.resolve()).casefold() for p in directory.iterdir() if p.is_file() and p.suffix.lower()=='.mp4'}!=expected:self._fail('发文副本目录MP4数量或清单变化。')
                for file in files:
                    copy=file['copy'];path=self._path(copy['path'],package['id'])
                    if self.editing._identity(path)!= {k:copy[k] for k in ('path','bytes','sha256')} or path.stat().st_mtime_ns!=copy['mtimeNs']:self._fail('发文视频副本已变化。')
        results={}
        for batch in package['batches']:
            for slot in batch['slots']:
                if slot is None:continue
                path=self._path(slot['workbookPath'],package['id']);before=path.stat()
                if not 0<before.st_size<=MAX_BYTES:self._fail('发文工作簿为空或超过读取限制。')
                with path.open('rb') as stream:raw=stream.read(MAX_BYTES+1)
                after=path.stat()
                if len(raw)>MAX_BYTES:self._fail('发文工作簿超过读取限制。')
                if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):self._fail('工作表正在保存，请关闭后重试。')
                book=read_workbook(raw)
                with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                    for name in archive.namelist():
                        if name.startswith('xl/worksheets/') and name.endswith('.xml'):
                            xml=ET.fromstring(archive.read(name))
                            if any(element.tag.rsplit('}',1)[-1] in ('mergeCell','f') for element in xml.iter()):self._fail('发文工作表不允许合并或公式。')
                if len(book['sheets'])!=1 or book['sheets'][0]['name']!='Sheet1':self._fail('工作表身份变化。')
                if any(column>16 and cell['text'] for (_,column),cell in book['sheets'][0]['cells'].items()):self._fail('固定任务表A:P之外存在额外业务值。')
                parsed=parse_import('tasks',raw,{})['records']
                if len(parsed)!=len(slot['taskIds']):self._fail('工作表行数变化。')
                for number,(task_id,row) in enumerate(zip(slot['taskIds'],parsed),2):
                    expected=package['tasks'][task_id]['fields']
                    if row['row']!=number or any(row['fields'][i]!=expected[i] for i in range(16) if i not in (12,13)):self._fail('工作表仅允许M/N回执变化，其他业务列或顺序已改变。')
                    results[task_id]={'M':row['fields'][12],'N':row['fields'][13]}
        return results

    def handoff(self, body):
        if not isinstance(body,dict) or body.get('confirmed') is not True:self._fail('请确认人工已在原软件选入本包并将启动。')
        with self.store.connection(write=True) as conn:
            package=self._package(conn,body)
            if package.get('executionOwner'):self._fail('本包已由自动发文占用，不能再交给旧软件重复执行。')
            receipts=self._check(package)
            if package['state']=='prepared' and any(r['M'] or r['N'] for r in receipts.values()):self._fail('新交接工作副本已有回执，不能重复启动。')
            if package['state']!='prepared':return {'ok':True,'package':package,'reused':True}
            package.update(state='handed_off',handoff={'confirmedAt':now(),'manual':True})
            conn.execute('UPDATE publishing_packages SET payload=? WHERE id=?',(dump(package),package['id']))
            for task_id in package['tasks']:
                record=json.loads(conn.execute('SELECT payload FROM publishing_tasks WHERE task_id=?',(task_id,)).fetchone()[0]);record['state']='handed_off'
                conn.execute('UPDATE publishing_tasks SET payload=? WHERE task_id=?',(dump(record),task_id))
        return {'ok':True,'package':package,'reused':False}

    def collect(self, body):
        if not isinstance(body,dict) or body.get('confirmedClosed') is not True:self._fail('请确认工作副本已保存并关闭。')
        with self.store.connection(write=True) as conn:
            package=self._package(conn,body)
            if package.get('executionOwner'):self._fail('本包由自动发文管理，请查看逐项执行结果；不能混用旧软件回执。')
            if not package['handoff']:self._fail('请先记录人工交接。')
            receipts=self._check(package);records=[]
            receipt_raw=dump(receipts).encode('utf-8');receipt_hash=hashlib.sha256(receipt_raw).hexdigest()
            receipt_path=self.store._path('publishing',package['id'],'receipts',receipt_hash+'.json')
            for task_id,receipt in receipts.items():
                record=json.loads(conn.execute('SELECT payload FROM publishing_tasks WHERE task_id=?',(task_id,)).fetchone()[0])
                if record['receipt'] is not None and record['receipt']!=receipt:self._fail('回收后的M/N回执已变化，请人工处理冲突，不能覆盖。')
                if record['receipt'] is None:
                    required=['M','N'] if package['mode']=='both' else ['M'] if package['mode']=='douyin' else ['N']
                    state='reported' if all(receipt[k]=={'M':'已发布','N':'计划已发布'}[k] for k in required) and all(not value or value=={'M':'已发布','N':'计划已发布'}[key] for key,value in receipt.items()) else 'unknown'
                    record.update(state=state,receipt=receipt,collectedAt=now(),receiptSnapshot={'path':str(receipt_path),'sha256':receipt_hash})
                records.append((dump(record),task_id))
            self._path(receipt_path,package['id']);self.store._write_once(receipt_path,receipt_raw)
            conn.executemany('UPDATE publishing_tasks SET payload=? WHERE task_id=?',records)
            if package['state'] not in ('verified','reviewed'):package['state']='collected'
            conn.execute('UPDATE publishing_packages SET payload=? WHERE id=?',(dump(package),package['id']))
        return {'ok':True,'package':package,'rows':len(records)}

    def collect_native(self, body, evidence):
        """Bridge completed native evidence into the existing receipt/review flow.

        Evidence is obtained by the server from its execution service, never
        accepted from an HTTP request. Source and isolated XLSX bytes stay intact.
        """
        if not isinstance(body,dict) or body.get('confirmed') is not True:self._fail('请确认回收自动执行结果，随后另行人工核验。')
        with self.store.connection(write=True) as conn:
            package=self._package(conn,body)
            if package.get('executionOwner')!='native':self._fail('本包不是自动执行包。')
            workbook_receipts=self._check(package)
            if any(v for row in workbook_receipts.values() for v in row.values()):self._fail('自动执行工作副本被写入旧软件回执，请处理双执行冲突。')
            if not isinstance(evidence,dict) or set(evidence)!=set(package['tasks']):self._fail('自动执行证据与任务清单不一致。')
            required={'M','N'} if package['mode']=='both' else {'M'} if package['mode']=='douyin' else {'N'}
            for row in evidence.values():
                if not isinstance(row,dict) or not row.get('evidence') or any(row.get(k)!=({'M':'已发布','N':'计划已发布'}[k] if k in required else '') for k in ('M','N')):self._fail('自动执行尚无完整成功证据。')
            raw=dump(evidence).encode('utf-8');sha=hashlib.sha256(raw).hexdigest()
            path=self.store._path('publishing',package['id'],'native-receipts',sha+'.json')
            for task_id,row in evidence.items():
                record=json.loads(conn.execute('SELECT payload FROM publishing_tasks WHERE task_id=?',(task_id,)).fetchone()[0])
                receipt={k:row[k] for k in ('M','N')}
                if record.get('receipt') is not None:
                    if record['receipt']!=receipt:self._fail('已有回执不同，不能覆盖。')
                    continue
                record.update(state='reported',receipt=receipt,collectedAt=now(),receiptSnapshot={'path':str(path),'sha256':sha,'source':'native-execution'})
                conn.execute('UPDATE publishing_tasks SET payload=? WHERE task_id=?',(dump(record),task_id))
            self._path(path,package['id']);self.store._write_once(path,raw)
            package['nativeReceipt']=True
            if package['state'] not in ('verified','reviewed'):package['state']='collected'
            conn.execute('UPDATE publishing_packages SET payload=? WHERE id=?',(dump(package),package['id']))
        return {'ok':True,'package':package,'rows':len(evidence)}

    def verify(self, body):
        if not isinstance(body,dict) or body.get('confirmed') is not True or body.get('result') not in ('verified','failed','unknown') or not isinstance(body.get('note'),str) or not body['note'].strip() or len(body['note'])>4000:self._fail('请明确人工核验结果并填写说明。')
        with self.store.connection(write=True) as conn:
            package=self._package(conn,body);task_id=body.get('taskId')
            if task_id not in package['tasks']:self._fail('任务不属于本包。')
            receipts=self._check(package)
            record=json.loads(conn.execute('SELECT payload FROM publishing_tasks WHERE task_id=?',(task_id,)).fetchone()[0])
            if package.get('executionOwner')=='native':
                if not package.get('nativeReceipt') or any(v for row in receipts.values() for v in row.values()):self._fail('请先回收完整自动执行证据，并确保未混用旧软件。')
                snapshot=record.get('receiptSnapshot') or {}
                snapshot_path=self._path(snapshot.get('path',''),package['id'])
                if self.editing._identity(snapshot_path)['sha256']!=snapshot.get('sha256'):self._fail('自动执行回执快照发生变化。')
            elif record['receipt'] is not None and record['receipt']!=receipts[task_id]:self._fail('回执在回收后变化，请处理冲突后核验。')
            if record['receipt'] is None:self._fail('先回收软件回执，再人工核验。')
            verification={'result':body['result'],'note':body['note'].strip()}
            if record.get('verification'):
                if all(record['verification'][k]==v for k,v in verification.items()):return {'ok':True,'reused':True}
                if record['verification']['result']!='unknown':self._fail('已有最终核验记录不能静默覆盖。')
                record.setdefault('verificationHistory',[]).append(record['verification'])
            record.update(state=body['result'],verification={**verification,'verifiedAt':now()})
            conn.execute('UPDATE publishing_tasks SET payload=? WHERE task_id=?',(dump(record),task_id))
            all_records=[json.loads(r[0]) for r in conn.execute('SELECT payload FROM publishing_tasks WHERE package_id=?',(package['id'],))]
            terminal=all(r.get('verification') and r['state'] in ('verified','failed') for r in all_records)
            package['state']=('verified' if all(r['state']=='verified' for r in all_records) else 'reviewed') if terminal else 'collected'
            conn.execute('UPDATE publishing_packages SET payload=? WHERE id=?',(dump(package),package['id']))
        return {'ok':True,'reused':False}
