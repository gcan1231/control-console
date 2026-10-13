"""006: isolated GUI handoffs and read-only VideoClean report verification."""
from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from .importers import ImportFailure, parse_import
from .materials import safe_component
from .store import dump, now
from .workbooks import task_workbook


class EditingService:
    def __init__(self, store, material):
        self.store,self.material=store,material
        with store.connection(write=True) as conn:
            conn.executescript('''
                CREATE TABLE IF NOT EXISTS editing_packages (id TEXT PRIMARY KEY,payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS editing_tasks (task_id TEXT PRIMARY KEY REFERENCES tasks(id),
                    package_id TEXT NOT NULL REFERENCES editing_packages(id),payload TEXT NOT NULL);
            ''')

    def state(self):
        with self.store.connection() as conn:
            return {'editingPackages':[json.loads(r[0]) for r in conn.execute('SELECT payload FROM editing_packages ORDER BY rowid')],
                    'editingTasks':{r['task_id']:json.loads(r['payload']) for r in conn.execute('SELECT * FROM editing_tasks')}}

    def _package(self, conn, package_id):
        if not isinstance(package_id,str) or len(package_id)>100:
            raise ImportFailure('editing_package','请选择有效剪辑包。')
        row=conn.execute('SELECT payload FROM editing_packages WHERE id=?',(package_id,)).fetchone()
        if not row:
            raise ImportFailure('editing_package','剪辑交接包不存在。')
        return json.loads(row[0])

    def _identity(self, path, max_bytes=None):
        target=Path(path)
        if not target.is_absolute() or not target.is_file() or str(path).startswith(('\\\\','//')):
            raise ImportFailure('editing_file','请填写已存在的本机文件绝对路径。')
        before=target.stat()
        if before.st_size<=0 or max_bytes and before.st_size>max_bytes:
            raise ImportFailure('editing_file','文件为空或超过读取限制。')
        digest=hashlib.sha256()
        with target.open('rb') as stream:
            for chunk in iter(lambda:stream.read(1024*1024),b''):
                digest.update(chunk)
        after=target.stat()
        if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):
            raise ImportFailure('editing_changed','读取期间文件变化，请等原软件保存完毕后重试。')
        return {'path':str(target.resolve()),'bytes':after.st_size,'sha256':digest.hexdigest()}

    def _gate(self, conn, task_id):
        task=conn.execute('SELECT * FROM tasks WHERE id=?',(task_id,)).fetchone()
        material=conn.execute('SELECT payload FROM material_tasks WHERE task_id=?',(task_id,)).fetchone()
        if not task or not material or not json.loads(task['validation'])['ok']:
            raise ImportFailure('editing_gate','任务必须通过当前品类证据校验。')
        fields=json.loads(task['fields']); material=json.loads(material[0])
        if fields[12].strip() or fields[13].strip() or material['state']!='ready' or not material.get('confirmation') or not material.get('files'):
            raise ImportFailure('editing_gate','仅接受已人工复核素材且无发布回执的任务。')
        for file in material['files']:
            fresh=self.material._file(file['path'],Path(file['path']).parent,file['bytes'])
            if fresh['sha256']!=file['sha256']:
                raise ImportFailure('editing_input','已复核素材变化，请回素材阶段重新回收并复核。')
        return task,fields,material

    def _copy(self, source, target):
        if not target.resolve().is_relative_to(self.store.root) or target.is_symlink():
            raise ImportFailure('editing_input','剪辑副本必须位于本机runtime独立目录，不能经链接改写其他目录。')
        if target.exists():
            fresh=self._identity(target)
            if fresh['sha256']!=source['sha256']:
                raise ImportFailure('editing_input','已有剪辑输入副本变化，不能覆盖。')
            return fresh
        target.parent.mkdir(parents=True,exist_ok=True)
        temporary=None; digest=hashlib.sha256()
        try:
            with tempfile.NamedTemporaryFile(dir=target.parent,prefix='.editing-',suffix='.tmp',delete=False) as stream:
                temporary=Path(stream.name)
                with Path(source['path']).open('rb') as incoming:
                    for chunk in iter(lambda:incoming.read(1024*1024),b''):
                        stream.write(chunk);digest.update(chunk)
                stream.flush();os.fsync(stream.fileno())
            if digest.hexdigest()!=source['sha256']:
                raise ImportFailure('editing_input','复制过程中素材变化，未发布输入副本。')
            os.replace(temporary,target)
        finally:
            if temporary and temporary.exists():temporary.unlink()
        return self._identity(target)

    @staticmethod
    def _empty(message='待取得剪辑报告与对应产物。'):
        return {'available':False,'ready':False,'files':[],'message':message,'verification':None}

    def prepare(self, task_ids):
        if not isinstance(task_ids,list) or not 1<=len(task_ids)<=100 or any(not isinstance(i,str) for i in task_ids) or len(set(task_ids))!=len(task_ids):
            raise ImportFailure('editing_selection','每包选择1—100个不同任务，且只能属于一个账号。')
        with self.store.connection(write=True) as conn:
            self.store._revalidate(conn)
            selected=[self._gate(conn,i) for i in task_ids]
            selected.sort(key=lambda t:(t[0]['batch_id'],t[0]['source_row']))
            if len({fields[3] for _,fields,_ in selected})!=1:
                raise ImportFailure('editing_account','一个剪辑包只接受一个账号。')
            if len({f[5] for _,f,_ in selected})!=len(selected):
                raise ImportFailure('editing_product','同包重复商品会合并目录，请拆开交接。')
            package_id=str(uuid.uuid5(uuid.NAMESPACE_URL,'editing:'+':'.join(t[0]['id'] for t in selected)))
            existing=conn.execute('SELECT payload FROM editing_packages WHERE id=?',(package_id,)).fetchone()
            if existing:return {'ok':True,'reused':True,'package':json.loads(existing[0])}
            if any(conn.execute('SELECT 1 FROM editing_tasks WHERE task_id=?',(i,)).fetchone() for i in task_ids):
                raise ImportFailure('editing_overlap','任务已属于其他剪辑包，请沿用已有包补跑，不能重复准备。')
            folder=self.store._path('editing',package_id);root=folder/'media';reports=folder/'reports'
            root.mkdir(parents=True,exist_ok=True);reports.mkdir(parents=True,exist_ok=True)
            package={'id':package_id,'accountName':selected[0][1][2],'accountId':selected[0][1][3],
                     'createdAt':now(),'workbook':str(folder/'Sheet1.xlsx'),'materialRoot':str(root),
                     'reportDir':str(reports),'reportPath':str(reports/'任务表导出报告.json'),'gui':None,
                     'state':'prepared','message':'独立输入已准备，请在VideoClean GUI手工选入并核对正式设置。','rows':[]}
            workbook_rows=[];records=[]
            for number,(task,fields,material) in enumerate(selected,2):
                account=safe_component(fields[2]);product=safe_component(fields[5]);directory=root/account/product
                if len(account)>80 or len(product)>80:
                    raise ImportFailure('editing_path','VideoClean会截断超过80字符的目录名，请核对来源，不能静默改名。')
                inputs=[]
                for source in material['files']:
                    target=directory/Path(source['path']).name
                    inputs.append(self._copy(source,target))
                if len({i['path'].casefold() for i in inputs})!=len(inputs):
                    raise ImportFailure('editing_input','剪辑输入文件名发生冲突。')
                row={'taskId':task['id'],'row':number,'sourceRow':task['source_row'],'batchId':task['batch_id'],
                     'accountName':account,'accountId':fields[3],'productId':product,'inputDir':str(directory),
                     'normalDir':str(directory/'成片'),'speedDir':str(directory/'成片'/'变速'),
                     'inputs':inputs,'materialConfirmation':material['confirmation']}
                package['rows'].append(row)
                working=list(fields);working[11]=''  # New exclusive work copy only; source L remains intact.
                workbook_rows.append({'fields':working});row['expectedFields']=working
                records.append((task['id'],package_id,dump({'packageId':package_id,'row':number,'state':'prepared',
                               'message':'等待原GUI人工确认与执行。','normal':self._empty(),'speed':self._empty(),'report':None,'attempt':0})))
            content=task_workbook(workbook_rows);package['workbookSha256']=hashlib.sha256(content).hexdigest()
            self.store._write_once(Path(package['workbook']),content)
            self.store._write_once(folder/'manifest.json',dump({k:v for k,v in package.items() if k not in ('createdAt','state','message','gui')}).encode('utf-8'))
            conn.execute('INSERT INTO editing_packages VALUES (?,?)',(package_id,dump(package)))
            conn.executemany('INSERT INTO editing_tasks VALUES (?,?,?)',records)
            self.material._audit(conn,'editing_prepare',package_id,{'taskIds':task_ids})
        return {'ok':True,'reused':False,'package':package}

    def gui(self, package_id, body):
        if body.get('confirmed') is not True or body.get('namingMode')!='id' or type(body.get('targetCount')) is not int or not 1<=body['targetCount']<=200 or type(body.get('speedEnabled')) is not bool:
            raise ImportFailure('editing_gui','请在原GUI确认每行产量1—200、正式词表启用、ID命名与变速开关。')
        for key in ('rulesPath','executablePath','decoration','version'):
            if not isinstance(body.get(key),str) or not body[key].strip() or len(body[key])>1000:
                raise ImportFailure('editing_gui','请填写正式词表、实际程序文件、版本与账号装饰方案；不用装饰明确填无装饰。')
        rules=self._identity(body['rulesPath'],20*1024*1024);program=self._identity(body['executablePath'],256*1024*1024)
        value={k:body[k] for k in ('targetCount','rulesPath','executablePath','decoration','version','namingMode','speedEnabled')}
        value.update(rulesPath=rules['path'],executablePath=program['path'],rulesIdentity=rules,programIdentity=program)
        with self.store.connection(write=True) as conn:
            self.store._revalidate(conn);package=self._package(conn,package_id)
            self._workbook(package)
            for row in package['rows']:self._gate(conn,row['taskId']);self._inputs(row)
            old=package['gui']
            if old and all(old.get(k)==v for k,v in value.items()):return {'ok':True,'reused':True}
            value.update(confirmedAt=now(),confirmedAtNs=str(time.time_ns()));package.update(gui=value,state='gui_confirmed',message='原GUI设置已记录；请随后回原GUI手工启动，不代表剪辑完成。')
            conn.execute('UPDATE editing_packages SET payload=? WHERE id=?',(dump(package),package_id))
            for row in package['rows']:
                record=json.loads(conn.execute('SELECT payload FROM editing_tasks WHERE task_id=?',(row['taskId'],)).fetchone()[0])
                record.update(state='gui_confirmed',message='等待手工剪辑完成后回收。',normal=self._empty(),speed=self._empty(),report=None)
                conn.execute('UPDATE editing_tasks SET payload=? WHERE task_id=?',(dump(record),row['taskId']))
            self.material._audit(conn,'editing_gui',package_id,value)
        return {'ok':True,'reused':False}

    def _inputs(self,row):
        for file in row['inputs']:
            if not Path(file['path']).resolve().is_relative_to(self.store.root) or Path(file['path']).parent.resolve()!=Path(row['inputDir']).resolve():
                raise ImportFailure('editing_input','独立素材路径归属发生变化。')
            fresh=self._identity(file['path'])
            if fresh!=file:raise ImportFailure('editing_input','独立剪辑素材已改变，不能沿用原交接记录。')

    def _workbook(self,package):
        saved=parse_import('tasks',Path(package['workbook']).read_bytes(),{})['records']
        if len(saved)!=len(package['rows']):raise ImportFailure('editing_workbook','剪辑工作表行数变化。')
        for mapping,actual in zip(package['rows'],saved):
            if actual['row']!=mapping['row'] or any(actual['fields'][i]!=mapping['expectedFields'][i] for i in range(16) if i!=11):
                raise ImportFailure('editing_workbook','剪辑工作表除L日志外有业务字段变化，不能交接或回收。')
        return saved

    def _json(self,path):
        path=Path(path)
        if not path.resolve().is_relative_to(self.store.root) or not path.is_file():
            raise ImportFailure('editing_report','未取得本包报告，请在原GUI确认报告目录并保存完成。')
        before=path.stat();raw=path.read_bytes()
        if len(raw)>20*1024*1024 or len(raw)!=before.st_size or (before.st_size,before.st_mtime_ns)!=(path.stat().st_size,path.stat().st_mtime_ns):
            raise ImportFailure('editing_report','报告过大或写入中，请等保存结束后重试。')
        try:value=json.loads(raw.decode('utf-8-sig'))
        except (ValueError,UnicodeError) as error:raise ImportFailure('editing_report','报告JSON格式不完整，请等原软件保存完成。') from error
        if not isinstance(value,dict):raise ImportFailure('editing_report','报告根必须为JSON对象。')
        return value,raw

    def _rules(self,report,gui):
        info=report.get('rules_info')
        if not isinstance(info,dict) or info.get('checked') is not True or info.get('exists') is not True or type(info.get('effective_term_count')) is not int or info['effective_term_count']<=0 or info.get('parse_error_count')!=0 or info.get('file_sha256')!=gui['rulesIdentity']['sha256'] or not self._same_path(info.get('source'),gui['rulesPath']):
            raise ImportFailure('editing_rules','报告未证明启用本次正式有效词表；缺有效词、解析错误或跳过禁词不能放行。')

    def _fresh(self,report,gui):
        try:
            generated=datetime.strptime(report.get('generated',''),'%Y-%m-%d %H:%M:%S')
            confirmed=datetime.fromisoformat(gui['confirmedAt']).replace(tzinfo=None)
            if generated<confirmed or generated>datetime.now()+timedelta(minutes=5):raise ValueError('old report')
        except (ValueError,TypeError):raise ImportFailure('editing_stale','报告时间早于本次GUI确认或格式无效，不能沿用旧批次成功。')

    @staticmethod
    def _same_path(value,expected):
        return isinstance(value,str) and Path(value).is_absolute() and Path(value).resolve()==Path(expected).resolve()

    def _video(self,record,directory,kind):
        path=record.get('output' if kind=='normal' else 'path')
        if not isinstance(path,str) or not Path(path).is_absolute() or Path(path).parent.resolve()!=Path(directory).resolve():
            raise ImportFailure('editing_output','成片不在本账号、完整商品ID的指定直接目录内。')
        identity=record.get('output_identity')
        if not isinstance(identity,list) or len(identity)!=2 or any(type(v) is not int for v in identity):
            raise ImportFailure('editing_output','报告缺少成片字节/时间身份，不能仅按文件存在放行。')
        duration=record.get('duration')
        if type(duration) not in (int,float) or not math.isfinite(duration) or duration<=0:
            raise ImportFailure('editing_output','成片报告时长无效。')
        target=Path(path)
        file=self.material._file(target,directory,identity[0])
        if target.stat().st_mtime_ns!=identity[1]:raise ImportFailure('editing_output','成片时间身份与报告不同，可能已被替换。')
        if record.get('content_sha256') and record['content_sha256']!=file['sha256']:
            raise ImportFailure('editing_output','成片摘要与原软件发布记录不同。')
        if record.get('error'):raise ImportFailure('editing_output','成片记录仍有错误。')
        return {**file,'duration':duration}

    def _component(self,details,row,kind,count,summary):
        component=self._empty()
        try:
            if kind=='normal':
                if type(summary.get('done')) is not int or summary['done']!=count or summary.get('total')!=count or summary.get('error') or summary.get('review_count') or details.get('pending_review_count'):
                    raise ImportFailure('editing_normal','普通成片未达到人工确认产量，或仍有错误/待复核。')
                candidates=details.get('results')
                if not isinstance(candidates,list):raise ImportFailure('editing_normal','缺少普通成片详细清单。')
                selected=[r for r in candidates if isinstance(r,dict) and r.get('output')]
                if len(selected)!=count or details.get('done')!=count or details.get('total')!=count:
                    raise ImportFailure('editing_normal','普通成片报告数量不一致。')
                slots=[];inputs={str(Path(f['path']).resolve()).casefold() for f in row['inputs']}
                for item in selected:
                    slot=item.get('output_slot',item.get('idx'))
                    if type(slot) is not int or not 1<=slot<=count or item.get('idx',slot)!=slot:
                        raise ImportFailure('editing_normal','普通成片槽号或idx不一致。')
                    slots.append(slot)
                    decisions=item.get('component_decisions')
                    if not isinstance(decisions,list) or not decisions or any(not isinstance(d,dict) or not isinstance(d.get('source'),str) or str(Path(d['source']).resolve()).casefold() not in inputs for d in decisions):
                        raise ImportFailure('editing_normal','普通成片来源组合未证明属于本包已确认素材。')
                if len(set(slots))!=count:raise ImportFailure('editing_normal','普通成片槽号重复，不能按数组条数满足产量。')
                files=[self._video(r,row['normalDir'],kind) for r in selected]
            else:
                freeze=details.get('freeze');outer=summary.get('freeze')
                if not isinstance(freeze,dict) or freeze.get('ok') is not True or freeze.get('error') or not isinstance(outer,dict) or outer.get('ok') is not True or not self._same_path(outer.get('path'),freeze.get('path')):
                    raise ImportFailure('editing_speed','变速成片未输出、失败或逐行/汇总报告不一致。')
                files=[self._video(freeze,row['speedDir'],kind)]
            if len({f['path'].casefold() for f in files})!=len(files):raise ImportFailure('editing_output','多条成片记录指向同一文件。')
            actual={str(p.resolve()).casefold() for p in Path(row['normalDir' if kind=='normal' else 'speedDir']).iterdir() if p.is_file() and p.suffix.lower()=='.mp4'}
            if actual!={str(Path(f['path']).resolve()).casefold() for f in files}:
                raise ImportFailure('editing_output','目录直接MP4与本次报告清单不一致；不混用旧成片或子目录。')
            component.update(available=True,files=files,message='软件报告与文件一致，等待人工观看复核。')
        except ImportFailure as error:component['message']=error.message
        except OSError:component['message']='成片目录暂时不可读，请等保存完成并核对权限后重试。'
        return component

    def collect(self,package_id):
        with self.store.connection() as conn:package=self._package(conn,package_id)
        gui=package['gui']
        if not gui:raise ImportFailure('editing_gui','先记录原GUI实际设置与人工启动确认。')
        outcomes=[]
        try:
            if self._identity(gui['rulesPath'])!=gui['rulesIdentity'] or self._identity(gui['executablePath'])!=gui['programIdentity']:
                raise ImportFailure('editing_config','正式词表或执行文件已变化，请重新确认实际GUI设置。')
            outer,outer_raw=self._json(package['reportPath'])
            self._fresh(outer,gui)
            if Path(package['reportPath']).stat().st_mtime_ns<int(gui['confirmedAtNs']):
                raise ImportFailure('editing_stale','汇总报告文件早于本次GUI确认，不能复用旧成片成功。')
            if not all(self._same_path(outer.get(k),package[v]) for k,v in (('task_table','workbook'),('material_root','materialRoot'),('export_dir','reportDir'))) or not self._same_path(outer.get('rules_file'),gui['rulesPath']):
                raise ImportFailure('editing_scope','汇总报告不是本包工作表、素材Root、报告目录或正式词表。')
            self._rules(outer,gui)
            settings=outer.get('settings') or {}
            if not isinstance(settings,dict):raise ImportFailure('editing_config','汇总设置必须为对象。')
            naming=settings.get('export_naming') or {}
            target=settings.get('task_target_count') if settings.get('task_target_count') is not None else settings.get('target_count')
            if not isinstance(naming,dict) or naming.get('mode','id')!='id' or type(target) is not int or target!=gui['targetCount'] or type(settings.get('batch_freeze',False)) is not bool or settings.get('batch_freeze',False)!=gui['speedEnabled']:
                raise ImportFailure('editing_config','报告产量、ID命名或变速开关与人工确认不同。')
            rows=outer.get('rows')
            if not isinstance(rows,list) or outer.get('total')!=len(package['rows']) or any(not isinstance(r,dict) or type(r.get('row_no')) is not int for r in rows) or len({r['row_no'] for r in rows})!=len(rows) or any(r['row_no'] not in {r['row'] for r in package['rows']} for r in rows):
                raise ImportFailure('editing_scope','汇总报告行映射不完整或含其他交接任务。')
            saved=self._workbook(package)
            for mapping,actual in zip(package['rows'],saved):
                normal=self._empty();speed=self._empty();evidence={'readAt':now(),'log':actual['fields'][11],'_rawOuter':outer_raw}
                try:
                    self._inputs(mapping)
                    summary=next((r for r in rows if r['row_no']==mapping['row']),None)
                    if not summary or summary.get('account')!=mapping['accountName'] or summary.get('douyin_id')!=mapping['accountId'] or summary.get('product_id')!=mapping['productId'] or not self._same_path(summary.get('export_dir'),mapping['inputDir']) or summary.get('write_ok') is not True or summary.get('write_error'):
                        raise ImportFailure('editing_mapping','逐行账号、完整ID、输出目录或日志写回证据不一致。')
                    if not actual['fields'][11] or actual['fields'][11]!=summary.get('status'):
                        raise ImportFailure('editing_workbook','工作副本L日志未保存或与逐行报告不一致。')
                    details,detail_raw=self._json(Path(mapping['inputDir'])/'批量导出报告.json')
                    evidence['_rawRow']=detail_raw
                    self._fresh(details,gui);self._rules(details,gui)
                    if (Path(mapping['inputDir'])/'批量导出报告.json').stat().st_mtime_ns<int(gui['confirmedAtNs']):
                        raise ImportFailure('editing_stale','逐行报告早于本次GUI确认。')
                    if not self._same_path(details.get('export_dir'),mapping['normalDir']) or not self._same_path(details.get('rules_file'),gui['rulesPath']) or not isinstance(details.get('rules_info'),dict) or details['rules_info'].get('checked') is not True:
                        raise ImportFailure('editing_scope','逐行报告目标目录或正式词表证据不符。')
                    evidence.update(summary=summary,row=details,outerSha256=hashlib.sha256(outer_raw).hexdigest(),rowSha256=hashlib.sha256(detail_raw).hexdigest())
                    normal=self._component(details,mapping,'normal',gui['targetCount'],summary)
                    speed=self._component(details,mapping,'speed',1,summary) if gui['speedEnabled'] else self._empty('原GUI未开启变速，普通与变速资格独立。')
                except (ImportFailure,OSError) as error:evidence['error']=str(error);normal['message']=speed['message']=str(error)
                outcomes.append((mapping,normal,speed,evidence))
        except (ImportFailure,OSError,ValueError,TypeError) as error:
            outcomes=[(m,self._empty(str(error)),self._empty(str(error)),{'readAt':now(),'error':str(error)}) for m in package['rows']]
        with self.store.connection(write=True) as conn:
            self.store._revalidate(conn)
            for mapping,normal,speed,evidence in outcomes:
                record=json.loads(conn.execute('SELECT payload FROM editing_tasks WHERE task_id=?',(mapping['taskId'],)).fetchone()[0])
                try:self._gate(conn,mapping['taskId'])
                except (ImportFailure,OSError) as error:
                    normal=self._empty(str(error));speed=self._empty(str(error));evidence['error']=str(error)
                raw_reports={}
                for label in ('Outer','Row'):
                    original=evidence.pop('_raw'+label,None)
                    if original is not None:
                        original_hash=hashlib.sha256(original).hexdigest()
                        original_path=self.store._path('editing',package_id,'snapshots','raw-'+original_hash+'.json')
                        self.store._write_once(original_path,original)
                        raw_reports[label.lower()]={'snapshot':str(original_path),'sha256':original_hash}
                evidence['rawReports']=raw_reports
                raw=dump({'taskId':mapping['taskId'],'gui':gui,'evidence':evidence,'normal':normal,'speed':speed}).encode('utf-8')
                sha=hashlib.sha256(raw).hexdigest();path=self.store._path('editing',package_id,'snapshots',sha+'.json');self.store._write_once(path,raw)
                evidence.update(snapshot=str(path),sha256=sha)
                for kind,component in (('normal',normal),('speed',speed)):
                    old=record[kind]
                    if component['available'] and old.get('verification') and old.get('files')==component['files'] and record.get('report',{}).get('rowSha256')==evidence.get('rowSha256') and record.get('report',{}).get('outerSha256')==evidence.get('outerSha256'):
                        component.update(ready=True,verification=old['verification'],message='已人工观看复核，可交接对应发布目标。')
                state='ready' if normal['ready'] or speed['ready'] else 'reported' if normal['available'] and speed['available'] else 'partial' if normal['available'] or speed['available'] else 'failed'
                record.update(state=state,message='普通/变速成片分别判定，未人工核验不放行。',normal=normal,speed=speed,report=evidence)
                conn.execute('UPDATE editing_tasks SET payload=? WHERE task_id=?',(dump(record),mapping['taskId']))
                self.material._audit(conn,'editing_collect',mapping['taskId'],{'snapshot':str(path),'sha256':sha,'state':state})
        return {'ok':True,'rows':len(outcomes)}

    def verify(self,task_id,kind,note,confirmed):
        if not isinstance(task_id,str) or len(task_id)>100 or kind not in ('normal','speed') or confirmed is not True or not isinstance(note,str) or not note.strip() or len(note)>4000:
            raise ImportFailure('editing_verify','请选择普通或变速，明确人工观看核验并填写记录。')
        with self.store.connection(write=True) as conn:
            self.store._revalidate(conn);self._gate(conn,task_id)
            row=conn.execute('SELECT payload FROM editing_tasks WHERE task_id=?',(task_id,)).fetchone()
            if not row:raise ImportFailure('editing_verify','任务尚无剪辑交接。')
            record=json.loads(row[0]);component=record[kind]
            if not component['available'] or not component['files']:raise ImportFailure('editing_verify','先取得对应成片完整报告与文件证据。')
            package=self._package(conn,record['packageId']);mapping=next(r for r in package['rows'] if r['taskId']==task_id);self._inputs(mapping)
            saved=self._workbook(package);current_row=next(r for r in saved if r['row']==mapping['row'])
            if current_row['fields'][11]!=record['report']['log']:
                raise ImportFailure('editing_changed','工作副本日志在回收后变化，请重新回收核验。')
            gui=package['gui']
            if not gui or self._identity(gui['rulesPath'])!=gui['rulesIdentity'] or self._identity(gui['executablePath'])!=gui['programIdentity']:
                raise ImportFailure('editing_config','正式词表或实际执行文件变化，请重新核对GUI。')
            _,outer_raw=self._json(package['reportPath']);_,row_raw=self._json(Path(mapping['inputDir'])/'批量导出报告.json')
            if hashlib.sha256(outer_raw).hexdigest()!=record['report'].get('outerSha256') or hashlib.sha256(row_raw).hexdigest()!=record['report'].get('rowSha256'):
                raise ImportFailure('editing_changed','剪辑报告在回收后变化，请重新回收并复核。')
            for file in component['files']:
                fresh=self.material._file(file['path'],Path(file['path']).parent,file['bytes'])
                if fresh['sha256']!=file['sha256']:raise ImportFailure('editing_changed','成片在回收后已变化，请重新回收与核验。')
            if component['ready']:return {'ok':True,'reused':True}
            component.update(ready=True,message='已人工观看复核，可交接对应发布目标。',verification={'time':now(),'note':note.strip(),'reportSha256':record['report']['sha256']})
            record['state']='ready';conn.execute('UPDATE editing_tasks SET payload=? WHERE task_id=?',(dump(record),task_id))
            self.material._audit(conn,'editing_verify',task_id,{'kind':kind,'verification':component['verification']})
        return {'ok':True,'reused':False}

    def retry(self,task_id,note):
        if not isinstance(task_id,str) or not isinstance(note,str) or not note.strip() or len(note)>4000:raise ImportFailure('editing_retry','请填写在原GUI处理失败/缺额的重试记录。')
        with self.store.connection(write=True) as conn:
            row=conn.execute('SELECT payload FROM editing_tasks WHERE task_id=?',(task_id,)).fetchone()
            if not row:raise ImportFailure('editing_retry','任务尚无剪辑包。')
            record=json.loads(row[0])
            if record['state']=='retry' and record.get('retryNote')==note.strip():return {'ok':True,'reused':True}
            package=self._package(conn,record['packageId'])
            if not record['report'] or record['normal']['available'] and (record['speed']['available'] or not package['gui']['speedEnabled']):raise ImportFailure('editing_retry','所需成片证据已齐全或尚未回收失败，请人工核验；不登记重复运行。')
            record.update(state='retry',retryNote=note.strip(),attempt=record['attempt']+1,message='已登记，请在原GUI补跑失败项后回收；没有自动启动。')
            conn.execute('UPDATE editing_tasks SET payload=? WHERE task_id=?',(dump(record),task_id))
            self.material._audit(conn,'editing_retry',task_id,{'note':note.strip(),'attempt':record['attempt']})
        return {'ok':True,'reused':False}
