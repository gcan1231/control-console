"""005 material handoff; never search, confirm, download or repair the source app."""
from __future__ import annotations

import hashlib
import http.client
import json
import re
import sqlite3
import uuid
from contextlib import closing
from pathlib import Path
from urllib.parse import urlencode

from .importers import ImportFailure
from .store import dump, now
from .workbooks import task_workbook


def safe_component(value):
    if (not isinstance(value,str) or not value or len(value)>100 or value != value.strip().strip('.')
        or re.search(r'[\\/:*?"<>|\x00-\x1f]',value)
        or re.match(r'^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)',value,re.I)):
        raise ImportFailure('material_path','账号名称或商品ID不能安全作为Windows目录名，请先核对来源；不会自动改名。')
    return value


class MaterialService:
    def __init__(self, store):
        self.store = store
        with store.connection(write=True) as conn:
            conn.executescript('''
                CREATE TABLE IF NOT EXISTS material_packages (
                    id TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS material_tasks (
                    task_id TEXT PRIMARY KEY REFERENCES tasks(id),
                    package_id TEXT NOT NULL REFERENCES material_packages(id),
                    payload TEXT NOT NULL);
            ''')
            conn.execute('INSERT OR IGNORE INTO settings VALUES (?,?)',('material',dump({'port':None,'database':''})))

    def settings(self, port, database):
        if type(port) is not int or not 1 <= port <= 65535 or not isinstance(database,str):
            raise ImportFailure('material_config','请输入素材软件的本机端口和实际material.db绝对路径。')
        path = Path(database)
        if not path.is_absolute() or not path.is_file() or path.suffix.lower() not in ('.db','.sqlite','.sqlite3') or database.startswith(('\\\\','//')):
            raise ImportFailure('material_config','数据库必须是已存在的本机SQLite文件。请从原软件核对实际数据目录。')
        value={'port':port,'database':str(path.resolve())}
        with self.store.connection(write=True) as conn:
            conn.execute('UPDATE settings SET value=? WHERE key=?',(dump(value),'material'))
            self._audit(conn,'material_settings','material',value)
        return {'ok':True,'materialSettings':value}

    def _configuration(self):
        with self.store.connection() as conn:
            config=json.loads(conn.execute('SELECT value FROM settings WHERE key=?',('material',)).fetchone()[0])
        if config['port'] is None or not config['database']:
            raise ImportFailure('material_config','先配置素材软件本机端口与实际数据库路径。')
        return config

    def state(self):
        with self.store.connection() as conn:
            return {'materialPackages':[json.loads(r['payload']) for r in conn.execute('SELECT payload FROM material_packages ORDER BY rowid')],
                    'materialTasks':{r['task_id']:json.loads(r['payload']) for r in conn.execute('SELECT * FROM material_tasks')},
                    'materialSettings':json.loads(conn.execute('SELECT value FROM settings WHERE key=?',('material',)).fetchone()[0])}

    def _package(self, conn, package_id):
        if not isinstance(package_id,str):
            raise ImportFailure('material_package','请选择有效交接包。')
        row=conn.execute('SELECT payload FROM material_packages WHERE id=?',(package_id,)).fetchone()
        if not row:
            raise ImportFailure('material_package','交接包不存在。')
        return json.loads(row[0])

    def prepare(self, task_ids):
        if not isinstance(task_ids,list) or not 1 <= len(task_ids) <= 100 or any(not isinstance(t,str) for t in task_ids) or len(set(task_ids))!=len(task_ids):
            raise ImportFailure('material_selection','每包选择1—100个不同任务，且只能属于一个账号。')
        with self.store.connection(write=True) as conn:
            self.store._revalidate(conn)
            tasks=[]
            for task_id in task_ids:
                from .external_intake import _record
                if _record(conn,task_id,'materials') or _record(conn,task_id,'finished'):
                    raise ImportFailure('material_gate','任务已人工接纳外部成果，请从相应后续阶段继续，不能重新采集。')
                row=conn.execute('SELECT * FROM tasks WHERE id=?',(task_id,)).fetchone()
                if not row or row['archived'] or not json.loads(row['validation'])['ok'] or row['stage']=='reported':
                    raise ImportFailure('material_gate','所有任务必须通过最新品类/重复行校验，且来源无发布回执。')
                tasks.append({'id':row['id'],'batchId':row['batch_id'],'sourceRow':row['source_row'],'fields':json.loads(row['fields']),'evidence':json.loads(row['validation'])['evidence']})
            if len({t['fields'][3] for t in tasks})!=1 or len({t['fields'][2] for t in tasks})!=1:
                raise ImportFailure('material_account','一个素材交接包仅允许一个账号。')
            if len({t['fields'][5] for t in tasks})!=len(tasks):
                raise ImportFailure('material_product','同包同商品会共用目录，请拆分重复商品任务，不能混用。')
            tasks.sort(key=lambda t:(t['fields'][0],t['sourceRow'],t['batchId']))
            package_id=str(uuid.uuid5(uuid.NAMESPACE_URL,'material:'+':'.join(t['id'] for t in tasks)))
            existing=conn.execute('SELECT payload FROM material_packages WHERE id=?',(package_id,)).fetchone()
            if existing:
                return {'ok':True,'reused':True,'package':json.loads(existing[0])}
            if any(conn.execute('SELECT 1 FROM material_tasks WHERE task_id=?',(t['id'],)).fetchone() for t in tasks):
                raise ImportFailure('material_overlap','任务已属于其他交接包，请使用已有包继续或重试，避免重复采集。')
            account_name=safe_component(tasks[0]['fields'][2])
            folder=self.store._path('material',package_id)
            workbook=folder/'Sheet1.xlsx'
            root=folder/'media'
            root.mkdir(parents=True,exist_ok=True)
            content=task_workbook(tasks)
            manifest={'id':package_id,'accountId':tasks[0]['fields'][3],'accountName':account_name,'createdAt':now(),
                      'workbook':str(workbook),'exportRoot':str(root),'sha256':hashlib.sha256(content).hexdigest(),
                      'state':'prepared','message':'工作表已准备；请发送到原软件预览后人工执行。','rows':[]}
            records=[]
            for number,t in enumerate(tasks,2):
                product=safe_component(t['fields'][5])
                item={'taskId':t['id'],'row':number,'sourceRow':t['sourceRow'],'batchId':t['batchId'],
                      'productId':product,'outputDir':str(root/account_name/product),'evidence':t['evidence']}
                manifest['rows'].append(item)
                record={'packageId':package_id,'row':number,'state':'prepared','message':'等待原软件预览和人工执行。','report':None,'files':[],'confirmation':None,'attempt':0}
                records.append((t['id'],package_id,dump(record)))
            self.store._write_once(workbook,content)
            immutable_manifest={k:v for k,v in manifest.items() if k not in ('createdAt','state','message')}
            self.store._write_once(folder/'manifest.json',dump(immutable_manifest).encode('utf-8'))
            conn.execute('INSERT INTO material_packages VALUES (?,?)',(package_id,dump(manifest)))
            conn.executemany('INSERT INTO material_tasks VALUES (?,?,?)',records)
            self.store.freeze_evidence(conn,task_ids)
            self._audit(conn,'material_prepare',package_id,{'taskIds':task_ids})
        return {'ok':True,'reused':False,'package':manifest}

    def _audit(self,conn,action,subject,details):
        conn.execute('INSERT INTO audit (created_at,action,subject,details) VALUES (?,?,?,?)',(now(),action,subject,dump(details)))

    def _request(self, config, method, path, body=None):
        # Fixed loopback host, allowlisted paths, no redirects/proxies or source
        # Repo imports. All call sites are the preview handoff or candidate GET.
        connection=http.client.HTTPConnection('127.0.0.1',config['port'],timeout=8)
        try:
            connection.request(method,path,body=None if body is None else dump(body).encode('utf-8'),headers={'Content-Type':'application/json'})
            response=connection.getresponse()
            raw=response.read(8*1024*1024+1)
            if len(raw)>8*1024*1024:
                raise ImportFailure('material_response','素材软件响应过大。')
            value=json.loads(raw)
            if response.status!=200 or not isinstance(value,dict) or value.get('ok') is not True or not isinstance(value.get('data'),dict):
                raise ImportFailure('material_response','素材软件尚无对应行或返回错误，请在原界面核对任务表。')
            return value['data']
        except (OSError,http.client.HTTPException,ValueError) as error:
            raise ImportFailure('material_connection','无法读取素材软件，请核对本机端口、软件是否运行及r2版本。') from error
        finally:
            connection.close()

    def send(self, package_id):
        config=self._configuration()
        with self.store.connection(write=True) as conn:
            self.store._revalidate(conn)
            package=self._package(conn,package_id)
            for row in package['rows']:
                task=conn.execute('SELECT validation,archived FROM tasks WHERE id=?',(row['taskId'],)).fetchone()
                if not task or task['archived'] or not json.loads(task[0])['ok']:
                    raise ImportFailure('material_gate','最新分类证据未通过，不能发送交接。')
        path=Path(package['workbook'])
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=package['sha256']:
            raise ImportFailure('material_workbook','交接工作表已改变或丢失，不能发送。')
        receipt=self._request(config,'POST','/api/integration/task-table',{'path':package['workbook'],'export_root':package['exportRoot']})
        if receipt.get('path')!=package['workbook'] or receipt.get('export_root')!=package['exportRoot']:
            raise ImportFailure('material_receipt','素材软件返回的交接路径不一致。')
        with self.store.connection(write=True) as conn:
            package=self._package(conn,package_id)
            package.update(state='sent',message='已发送到原软件预览；在原界面建任务、搜索、预览并确认输出。',receipt=receipt)
            conn.execute('UPDATE material_packages SET payload=? WHERE id=?',(dump(package),package_id))
            self._audit(conn,'material_send',package_id,receipt)
        return {'ok':True,'package':package}

    def _jobs(self, database, task_id):
        path=Path(database)
        if not path.is_file():
            raise ImportFailure('material_database','素材数据库不存在；不会创建或迁移原软件数据库。')
        try:
            with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=3)) as conn:
                conn.row_factory=sqlite3.Row
                conn.execute('PRAGMA query_only=ON')
                conn.execute('BEGIN')
                rows=conn.execute('SELECT * FROM download_jobs WHERE task_id=? ORDER BY started_at,rowid',(task_id,)).fetchall()
                if len(rows)>1000:
                    raise ImportFailure('material_report','作业报告数量超过读取限制。')
                jobs=[]
                for row in rows:
                    item=dict(row)
                    for field in ('material_ids','items'):
                        value=json.loads(item.pop(field+'_json'))
                        if not isinstance(value,list):
                            raise ValueError('Invalid job list')
                        item[field]=value
                    jobs.append(item)
                return jobs
        except (sqlite3.Error,ValueError,TypeError,KeyError) as error:
            raise ImportFailure('material_report','只读报告格式异常或数据库版本不兼容，请核对material.db路径。') from error

    def _file(self, path, expected_dir, expected_size):
        target=Path(path)
        if target.parent.resolve()!=Path(expected_dir).resolve() or target.is_symlink() or not target.resolve().is_relative_to(self.store.root) or not target.is_file() or target.suffix.lower()!='.mp4':
            raise ImportFailure('material_file','素材路径不在本任务独立目录内，或文件丢失。')
        before=target.stat();size=before.st_size
        if type(expected_size) is not int or size<=0 or size!=expected_size:
            raise ImportFailure('material_file','素材文件大小与逐条输出报告不符。')
        digest=hashlib.sha256()
        with target.open('rb') as stream:
            header=stream.read(16)
            if len(header)<8 or header[4:8]!=b'ftyp':
                raise ImportFailure('material_file','文件没有可识别的MP4容器头；请在原软件重新检查。')
            digest.update(header)
            for chunk in iter(lambda:stream.read(1024*1024),b''):
                digest.update(chunk)
        after=target.stat()
        if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):
            raise ImportFailure('material_file','读取期间素材变化，请等原软件保存完毕后重新回收。')
        return {'path':str(target),'bytes':size,'sha256':digest.hexdigest()}

    def _read_row(self, config, package, row):
        query=urlencode({'path':package['workbook'],'sheet_name':'Sheet1','row_number':row['row'],'size':100})
        candidates=self._request(config,'GET','/api/task-table/row/materials?'+query)
        if candidates.get('row_number')!=row['row'] or not isinstance(candidates.get('task_id'),str):
            raise ImportFailure('material_mapping','素材软件返回行号或任务映射不一致。')
        items=candidates.get('items')
        if not isinstance(items,list) or any(not isinstance(i,dict) for i in items) or type(candidates.get('total')) is not int or not 0 <= candidates['total'] <= len(items):
            raise ImportFailure('material_candidates','候选清单不完整（当前只读接口最多100条），不能确认交接。请在原软件缩小候选范围。')
        jobs=self._jobs(config['database'],candidates['task_id'])
        summary={'softwareTaskId':candidates['task_id'],'candidateCount':candidates['total'],
                 'candidates':[{'materialId':i.get('material_id'),'title':i.get('title'),'selected':i.get('download_selected'),'error':i.get('download_error_label')} for i in items],
                 'jobs':jobs,'readAt':now()}
        if not jobs:
            return 'review','候选已回收，等待原界面人工确认输出；尚无持久作业报告。',summary,[]
        # Retrying only failed items must not hide earlier failures. Aggregate
        # all frozen selection IDs; the newest per-item output wins.
        wanted=set(); latest={}
        for job in jobs:
            if any(not isinstance(mid,str) or not mid for mid in job['material_ids']):
                raise ImportFailure('material_report','作业冻结的素材ID无效。')
            wanted.update(job['material_ids'])
            for item in job['items']:
                if not isinstance(item,dict) or not isinstance(item.get('material_id'),str) or not item['material_id']:
                    raise ImportFailure('material_report','逐条作业结果缺少素材ID。')
                latest[item['material_id']]=item
        if jobs[-1].get('state')!='done' or jobs[-1].get('failed')!=0:
            return 'failed','原软件作业未全部完成；请在原界面处理失败项后回收。',summary,[]
        if not wanted or not wanted.issubset(latest):
            return 'failed','缺少已确认素材的逐条报告，不能仅按文件存在放行。',summary,[]
        current={i.get('material_id'):i for i in items if isinstance(i.get('material_id'),str)}
        if any(mid not in current or not isinstance(current[mid].get('eligibility'),dict) or current[mid]['eligibility'].get('allowed') is not True for mid in wanted):
            return 'failed','素材当前可用性证据缺失或被原软件阻止，请在原界面重新核对候选。',summary,[]
        files=[]
        for mid in sorted(wanted):
            item=latest[mid]
            output=item.get('output_result') or {}
            if not isinstance(output,dict) or not isinstance(item.get('aweme_id'),str) or not item['aweme_id'] or current[mid].get('aweme_id')!=item['aweme_id']:
                raise ImportFailure('material_mapping','输出结果与当前候选视频标识不一致。')
            exported=output.get('items') or []
            if item.get('state') not in ('done','skipped') or not isinstance(exported,list) or len(exported)!=1:
                return 'failed','有素材失败或缺少精确输出映射；请在原软件重新确认输出。',summary,[]
            record=exported[0]
            if not isinstance(record,dict) or record.get('state') not in ('done','already_exported') or output.get('dir')!=row['outputDir'] or record.get('aweme_id')!=item.get('aweme_id'):
                raise ImportFailure('material_mapping','输出报告的账号目录或视频标识不一致。')
            name=record.get('name')
            if not isinstance(name,str) or Path(name).name!=name or '/' in name or '\\' in name:
                raise ImportFailure('material_file','输出文件名无效。')
            file=self._file(Path(row['outputDir'])/name,row['outputDir'],record.get('bytes'))
            files.append({**file,'materialId':mid,'awemeId':item.get('aweme_id')})
        if len({f['path'].casefold() for f in files})!=len(files):
            raise ImportFailure('material_mapping','多条素材指向同一文件，不能放行。')
        return 'reported','软件逐条输出报告与文件清单一致，等待人工复核素材内容。',summary,files

    def collect(self,package_id):
        config=self._configuration()
        with self.store.connection() as conn:
            package=self._package(conn,package_id)
        outcomes=[]
        for row in package['rows']:
            try:
                state,message,report,files=self._read_row(config,package,row)
                outcomes.append((row,state,message,report,files))
            except ImportFailure as error:
                outcomes.append((row,'failed',error.message,{'error':error.as_dict(),'readAt':now()},[]))
            except OSError:
                outcomes.append((row,'failed','素材文件暂时不可读，请核对占用、权限与磁盘后重新回收。',{'error':{'code':'material_file','message':'素材文件暂时不可读'},'readAt':now()},[]))
        with self.store.connection(write=True) as conn:
            self.store._revalidate(conn)
            for row,status,message,report,files in outcomes:
                record=json.loads(conn.execute('SELECT payload FROM material_tasks WHERE task_id=?',(row['taskId'],)).fetchone()[0])
                evidence=json.loads(conn.execute('SELECT validation FROM tasks WHERE id=?',(row['taskId'],)).fetchone()[0])
                snapshot=dump({'taskId':row['taskId'],'status':status,'report':report,'files':files}).encode('utf-8')
                report_hash=hashlib.sha256(snapshot).hexdigest()
                report_path=self.store._path('material',package_id,'reports',report_hash+'.json')
                self.store._write_once(report_path,snapshot)
                report.update(snapshot=str(report_path),sha256=report_hash)
                task=conn.execute('SELECT archived FROM tasks WHERE id=?',(row['taskId'],)).fetchone()
                if status=='reported' and (record.get('report') or {}).get('jobs')==report.get('jobs') and record.get('files') and record['files']!=files:
                    status='failed';message='同一逐条输出报告对应文件发生变化，请在原软件重新输出并回收。'
                ready=status=='reported' and evidence['ok'] and not task['archived']
                same=record.get('files')==files and (record.get('report') or {}).get('jobs')==report.get('jobs')
                confirmation=record.get('confirmation') if ready and same and record.get('confirmation') else {'time':now(),'kind':'source-confirmation','reportSha256':report_hash,'evidence':evidence['evidence']} if ready else None
                record.update(state='ready' if ready else status,message='原软件已确认输出，逐条报告和文件核验通过，可进入剪辑交接。' if ready else message,report=report,files=files,confirmation=confirmation)
                conn.execute('UPDATE material_tasks SET payload=? WHERE task_id=?',(dump(record),row['taskId']))
                conn.execute('UPDATE tasks SET stage=? WHERE id=?',('editing' if ready else 'material',row['taskId']))
            self._audit(conn,'material_collect',package_id,{'states':[{'taskId':r['taskId'],'state':s,'reportSha256':report['sha256'],'snapshot':report['snapshot']} for r,s,_,report,_ in outcomes]})
        return {'ok':True,'rows':len(outcomes)}

    def confirm(self,task_id,note,confirmed):
        if not isinstance(task_id,str) or not task_id or len(task_id)>100:
            raise ImportFailure('material_task','请选择有效素材任务。')
        if confirmed is not True or not isinstance(note,str) or not note.strip() or len(note)>4000:
            raise ImportFailure('material_confirmation','请明确确认已在原界面选中并输出，已复核内容，并填写复核记录。')
        with self.store.connection(write=True) as conn:
            self.store._revalidate(conn)
            task=conn.execute('SELECT * FROM tasks WHERE id=?',(task_id,)).fetchone()
            row=conn.execute('SELECT payload FROM material_tasks WHERE task_id=?',(task_id,)).fetchone()
            if not task or task['archived'] or not row or not json.loads(task['validation'])['ok']:
                raise ImportFailure('material_gate','任务不存在或最新来源校验未通过。')
            record=json.loads(row[0])
            if record['state'] not in ('reported','ready') or not record['files']:
                raise ImportFailure('material_confirmation','先回收完整逐条报告与文件清单，未确认或失败不能进入剪辑。')
            for file in record['files']:
                fresh=self._file(file['path'],Path(file['path']).parent,file['bytes'])
                if fresh['sha256']!=file['sha256']:
                    raise ImportFailure('material_file','素材在回收后已变化，请重新回收并复核。')
            if record['state']=='ready':
                return {'ok':True,'reused':True}
            record.update(state='ready',message='素材已人工复核，可进入剪辑交接。',confirmation={'time':now(),'note':note.strip(),'reportSha256':record['report']['sha256'],'evidence':json.loads(task['validation'])['evidence']})
            conn.execute('UPDATE material_tasks SET payload=? WHERE task_id=?',(dump(record),task_id))
            conn.execute('UPDATE tasks SET stage=? WHERE id=?',('editing',task_id))
            self._audit(conn,'material_confirm',task_id,record['confirmation'])
        return {'ok':True,'reused':False}

    def retry(self,task_id,note):
        if not isinstance(task_id,str) or not task_id or len(task_id)>100:
            raise ImportFailure('material_task','请选择有效素材任务。')
        if not isinstance(note,str) or not note.strip() or len(note)>4000:
            raise ImportFailure('material_retry','请填写失败处理与原软件重试记录。')
        with self.store.connection(write=True) as conn:
            row=conn.execute('SELECT payload FROM material_tasks WHERE task_id=?',(task_id,)).fetchone()
            if not row:
                raise ImportFailure('material_retry','任务尚无素材交接。')
            record=json.loads(row[0])
            if record['state']=='retry' and record.get('retryNote')==note.strip():
                return {'ok':True,'reused':True}
            if record['state']!='failed':
                raise ImportFailure('material_retry','只对失败任务登记重试；不自动启动原软件。')
            record.update(state='retry',message='重试记录已保存，请在原软件单独重试本行，再回收结果。',retryNote=note.strip(),attempt=record['attempt']+1,confirmation=None)
            conn.execute('UPDATE material_tasks SET payload=? WHERE task_id=?',(dump(record),task_id))
            self._audit(conn,'material_retry',task_id,{'note':note.strip(),'attempt':record['attempt']})
        return {'ok':True,'reused':False}
