"""Explicit human acceptance of existing local outputs; never a software receipt."""
from __future__ import annotations
import json
import os
import shutil
import uuid
from pathlib import Path
from .importers import ImportFailure
from .materials import safe_component
from .store import dump, now


def fail(message):
    raise ImportFailure('external_intake', message)


def safe_path(value):
    path=Path(value)
    if not path.is_absolute() or str(value).startswith(('\\\\','//')):
        fail('仅接受本机绝对路径，根目录必须是账号目录的上一级。')
    for part in (path,*path.parents):
        if part.is_symlink() or getattr(part,'is_junction',lambda:False)() or (part.exists() and getattr(part.stat(),'st_file_attributes',0)&0x400):
            fail('目录或文件不能经过符号链接、junction或其他重解析点。')
    return path


def _record(conn,task_id,kind):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='external_acceptances'").fetchone():return None
    for r in conn.execute('SELECT payload FROM external_acceptances ORDER BY rowid'):
        value=json.loads(r[0])
        if value['kind']==kind:
            for row in value['rows']:
                if row['taskId']==task_id:return value,row
    return None


def external_material(conn,task_id):
    value=_record(conn,task_id,'materials')
    if not value:return None
    accepted,row=value
    task=conn.execute('SELECT fields FROM tasks WHERE id=?',(task_id,)).fetchone()
    if not task or json.loads(task['fields'])!=row['fields']:fail('人工接纳后的任务字段变化，请处理归属冲突。')
    files=row['files']['materials'];directory=safe_path(Path(files[0]['path']).parent)
    if {str(p) for p in directory.iterdir() if p.suffix.lower()=='.mp4'}!={f['path'] for f in files}:fail('外部素材隔离副本清单已变化。')
    for f in files:
        path=safe_path(f['path'])
        if path.stat().st_mtime_ns!=f['mtimeNs']:fail('人工接纳素材的时间身份变化。')
    return {'state':'ready','files':row['files']['materials'],'confirmation':accepted['verification'],
            'source':'external-human-acceptance','acceptanceId':accepted['id']}


def external_videos(conn,task_id,kinds,editing):
    value=_record(conn,task_id,'finished')
    if not value:return None
    accepted,row=value;videos={}
    task=conn.execute('SELECT * FROM tasks WHERE id=?',(task_id,)).fetchone()
    if not task or task['archived'] or not json.loads(task['validation']).get('ok'):fail('当前任务品类校验未通过。')
    fields=json.loads(task['fields'])
    if fields!=row['fields'] or fields[12].strip() or fields[13].strip():fail('接纳后的任务字段变化或已有发布回执。')
    for kind in kinds:
        files=row['files'].get(kind,[])
        if not files:fail('所选模式缺少已人工接纳的对应成片。')
        directory=safe_path(Path(files[0]['path']).parent)
        if {str(p) for p in directory.iterdir() if p.suffix.lower()=='.mp4'}!={f['path'] for f in files}:fail('外部成片隔离副本清单已变化。')
        for f in files:
            p=safe_path(f['path']);fresh=editing._identity(p)
            if any(fresh[k]!=f[k] for k in fresh) or p.stat().st_mtime_ns!=f['mtimeNs']:fail('外部成片隔离副本已变化。')
        videos[kind]=files
    return fields,'external:'+accepted['id'],videos


class ExternalIntakeService:
    def __init__(self,store,material,editing):
        self.store,self.material,self.editing=store,material,editing
        with store.connection(write=True) as conn:
            conn.executescript('CREATE TABLE IF NOT EXISTS external_previews(id TEXT PRIMARY KEY,payload TEXT NOT NULL); CREATE TABLE IF NOT EXISTS external_acceptances(id TEXT PRIMARY KEY,preview_id TEXT UNIQUE NOT NULL,payload TEXT NOT NULL);')

    def state(self):
        with self.store.connection() as conn:
            previews=[json.loads(r[0]) for r in conn.execute('SELECT payload FROM external_previews ORDER BY rowid')]
            acceptances=[json.loads(r[0]) for r in conn.execute('SELECT payload FROM external_acceptances ORDER BY rowid')]
            tasks={}
            for a in acceptances:
                for row in a['rows']:
                    t=tasks.setdefault(row['taskId'],{})
                    if a['kind']=='materials':t['materials']={'ready':True,'acceptanceId':a['id'],'manual':True,'files':row['files']['materials'],'verification':a['verification']}
                    else:
                        t['finished']={'acceptanceId':a['id'],'manual':True}
                        for k in ('normal','speed'):t['finished'][k]={'ready':bool(row['files'][k]),'files':row['files'][k],'verification':a['verification']}
            return {'ok':True,'externalPreviews':previews,'externalAcceptances':acceptances,'externalTasks':tasks}

    def _gate(self,conn,ids,kind):
        rows=[]
        for task_id in ids:
            task=conn.execute('SELECT * FROM tasks WHERE id=?',(task_id,)).fetchone()
            if not task or task['archived'] or not json.loads(task['validation']).get('ok'):fail('所有任务必须通过当前品类证据校验。')
            f=json.loads(task['fields'])
            if f[12].strip() or f[13].strip():fail('任务已有发布回执，不能接纳。')
            for table in ('material_tasks','editing_tasks','publishing_tasks'):
                if not conn.execute('SELECT 1 FROM sqlite_master WHERE name=?',(table,)).fetchone():continue
                existing=conn.execute(f'SELECT payload FROM {table} WHERE task_id=?',(task_id,)).fetchone()
                if existing:
                    record=json.loads(existing[0])
                    if table=='material_tasks' and kind=='finished' and record.get('state')=='ready' and record.get('confirmation'):continue
                    fail('任务已有该阶段或后续交接，不能覆盖；请先处理原交接记录。')
            if _record(conn,task_id,'finished') or (kind=='materials' and _record(conn,task_id,'materials')):fail('任务已有同阶段或后续外部接纳，不能覆盖。')
            # The user selects exact dated task IDs. Never assign one directory
            # to two selected tasks; historical rows stay separate and visible.
            others=[]
            for other in conn.execute('SELECT id,fields FROM tasks WHERE archived=0 AND id!=?',(task_id,)):
                o=json.loads(other['fields'])
                if o[2].casefold()==f[2].casefold() and o[5].casefold()==f[5].casefold():others.append({'taskId':other['id'],'date':o[0]})
            rows.append({'taskId':task_id,'fields':f,'validation':json.loads(task['validation']),'accountName':safe_component(f[2]),'accountId':f[3],'productId':safe_component(f[5]),'otherTaskMatches':others})
        if len({r['accountId'] for r in rows})!=1 or len({r['accountName'] for r in rows})!=1:fail('每次仅接受一个账号。')
        if len({r['productId'].casefold() for r in rows})!=len(rows):fail('同一目录不能同时分配给两个所选任务；请明确选择实际日期对应的一行。')
        return rows

    def _scan(self,root,rows,kind):
        safe_path(root)
        if not root.is_dir():fail('根目录不存在；请填写账号目录的上一级。')
        for row in rows:
            product=safe_path(root/row['accountName']/row['productId'])
            if not product.is_dir():fail('缺少精确账号名称/完整商品ID目录。')
            row['files']={};row['reports']=[]
            directories={'materials':product} if kind=='materials' else {'normal':product/'成片','speed':product/'成片'/'变速'}
            if kind=='finished':
                for p in product.iterdir():
                    safe_path(p)
                    if p.is_file() and ('报告' in p.name or p.suffix.lower()=='.json'):
                        if p.name in ('批量导出报告.json','任务表导出报告.json'):fail('发现原软件报告，请走原阶段报告回收以处理可能冲突。')
                        row['reports'].append({**self.editing._identity(p),'status':'unverified','message':'未知报告未验证，不作为成功证据。'})
            for key,directory in directories.items():
                safe_path(directory);files=[]
                if directory.exists():
                    for p in sorted(directory.iterdir(),key=lambda p:p.name.casefold()):
                        safe_path(p)
                        if p.suffix.lower()=='.mp4':
                            identity=self.editing._identity(p)
                            with p.open('rb') as stream:
                                if stream.read(8)[4:8]!=b'ftyp':fail('文件缺少可识别MP4容器头。')
                            files.append({**identity,'mtimeNs':p.stat().st_mtime_ns})
                        elif p.is_file() and ('报告' in p.name or p.suffix.lower()=='.json'):
                            # Reports are never auto-adopted; recognized reports require native recovery.
                            if p.name in ('批量导出报告.json','任务表导出报告.json'):fail('发现原软件报告，请走原阶段报告回收以处理可能冲突。')
                            row['reports'].append({**self.editing._identity(p),'status':'unverified','message':'未知报告未验证，不作为成功证据。'})
                row['files'][key]=files
                if len({Path(f['path']).name.casefold() for f in files})!=len(files):fail('文件名在Windows中冲突，不能复制。')
            if not any(row['files'].values()):fail('指定直接目录没有MP4，不能仅凭目录接纳。')
        return rows

    def preview(self,body):
        if not isinstance(body,dict):fail('请提供接入配置。')
        ids=body.get('taskIds');kind=body.get('kind');root=body.get('root')
        if not isinstance(ids,list) or not 1<=len(ids)<=100 or any(not isinstance(i,str) for i in ids) or len(set(ids))!=len(ids) or kind not in ('materials','finished') or not isinstance(root,str):fail('请选择1—100个不同任务、接入类型及本地根目录。')
        root=safe_path(root)
        with self.store.connection(write=True) as conn:
            self.store._revalidate(conn);rows=self._scan(root,self._gate(conn,ids,kind),kind)
            preview={'id':str(uuid.uuid4()),'kind':kind,'root':str(root),'createdAt':now(),'state':'unverified','message':'未核验：文件存在和容器头不代表业务成功；请逐项观看并核对账号、商品和产物类型。','rows':rows}
            conn.execute('INSERT INTO external_previews VALUES (?,?)',(preview['id'],dump(preview)))
        return {'ok':True,'preview':preview}

    def accept(self,body):
        if not isinstance(body,dict) or body.get('confirmed') is not True or not isinstance(body.get('note'),str) or not body['note'].strip() or len(body['note'])>4000:fail('请明确人工核验确认并填写说明。')
        with self.store.connection(write=True) as conn:
            old=conn.execute('SELECT payload FROM external_acceptances WHERE preview_id=?',(body.get('previewId'),)).fetchone()
            if old:return {'ok':True,'reused':True,'acceptance':json.loads(old[0])}
            saved=conn.execute('SELECT payload FROM external_previews WHERE id=?',(body.get('previewId'),)).fetchone()
            if not saved:fail('接入预览不存在。')
            p=json.loads(saved[0]);self.store._revalidate(conn)
            fresh=self._scan(Path(p['root']),self._gate(conn,[r['taskId'] for r in p['rows']],p['kind']),p['kind'])
            if fresh!=p['rows']:fail('任务、证据、报告或文件全集发生变化，请重新预览。')
            aid=str(uuid.uuid4());folder=self.store._path('external',aid)
            accepted={'id':aid,'previewId':p['id'],'kind':p['kind'],'state':'accepted','sourceRows':p['rows'],'rows':json.loads(dump(fresh)), 'verification':{'manual':True,'confirmedAt':now(),'note':body['note'].strip()},'message':'仅人工接纳外部成果；未声明素材或剪辑软件运行成功。'}
            try:
                for row in accepted['rows']:
                    for key,files in row['files'].items():
                        copied=[]
                        for f in files:
                            target=folder/row['accountName']/row['productId']/key/Path(f['path']).name
                            safe_path(target);copy=self.editing._copy(f,target)
                            copied.append({**copy,'mtimeNs':target.stat().st_mtime_ns})
                        row['files'][key]=copied
                # Recheck originals after all copies to reject concurrent set changes.
                if self._scan(Path(p['root']),self._gate(conn,[r['taskId'] for r in p['rows']],p['kind']),p['kind'])!=fresh:fail('复制期间外部成果变化，请重新预览。')
                self.store._write_once(folder/'manifest.json',dump(accepted).encode('utf8'))
                conn.execute('INSERT INTO external_acceptances VALUES (?,?,?)',(aid,p['id'],dump(accepted)))
                self.store.freeze_evidence(conn,[r['taskId'] for r in fresh])
                self.material._audit(conn,'external_accept',aid,{'previewId':p['id'],'kind':p['kind'],'verification':accepted['verification']})
            except Exception:
                if folder.exists() and safe_path(folder).resolve()==self.store.root/'external'/aid:shutil.rmtree(folder)
                raise
        return {'ok':True,'reused':False,'acceptance':accepted}
