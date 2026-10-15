"""006A: local reference revisions, merchant locks and reversible task imports.

Only registered local copies are read. Business source files are never written.
Software stages keep their own evidence; merchant colors never imply execution.
"""
from __future__ import annotations

import hashlib
import json
import threading
import uuid
from collections import defaultdict
from pathlib import Path
from urllib.parse import quote

from .importers import ImportFailure, HEADERS, split_categories
from .references import parse_reference, read_product_image
from .store import dump, now


def key(fields):
    return (fields[0].strip(), fields[3].strip(), fields[5].strip())


class WorkbenchService:
    def __init__(self, store):
        self.store = store
        self.guard = threading.RLock()
        with store.connection(write=True) as conn:
            conn.executescript('''
                CREATE TABLE IF NOT EXISTS reference_paths (
                    kind TEXT PRIMARY KEY,path TEXT NOT NULL,signature TEXT NOT NULL,
                    source_id TEXT NOT NULL,checked_at TEXT NOT NULL,error TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS merchant_locks (
                    task_id TEXT PRIMARY KEY REFERENCES tasks(id),payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS task_versions (
                    id INTEGER PRIMARY KEY,task_id TEXT NOT NULL REFERENCES tasks(id),
                    source_id TEXT NOT NULL,source_row INTEGER NOT NULL,fields TEXT NOT NULL,
                    cell_meta TEXT NOT NULL,created_at TEXT NOT NULL,action TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS task_layout (
                    task_id TEXT PRIMARY KEY REFERENCES tasks(id),source_id TEXT NOT NULL,
                    row_index INTEGER NOT NULL);
            ''')

    @staticmethod
    def _signature(path):
        value = Path(path).stat()
        return f'{value.st_size}:{value.st_mtime_ns}'

    def _audit(self, conn, action, subject, details):
        conn.execute('INSERT INTO audit(created_at,action,subject,details) VALUES (?,?,?,?)',
                     (now(), action, subject, dump(details)))

    def _save_source(self, conn, kind, parsed, snapshot=None):
        source_id = str(uuid.uuid5(uuid.NAMESPACE_URL,kind+':'+parsed['sha256']+':'+dump(parsed['options'])))
        conn.execute('UPDATE sources SET active=0 WHERE kind=?',(kind,))
        if not conn.execute('SELECT 1 FROM sources WHERE id=?',(source_id,)).fetchone():
            if snapshot is None:
                snapshot = self.store._path('reference', source_id+'.json')
                self.store._write_once(snapshot,dump(parsed).encode('utf-8'))
            conn.execute('INSERT INTO sources VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                         (source_id,kind,parsed['filename'],parsed['sha256'],now(),int(parsed['options'].get('complete',True)),1,
                          dump(parsed['options']),dump(parsed['records']),dump(parsed.get('issues',[])),str(snapshot),len(parsed['records'])))
            self._audit(conn,'reference_snapshot',source_id,{'kind':kind,'rows':len(parsed['records'])})
        else:
            conn.execute('UPDATE sources SET active=1 WHERE id=?',(source_id,))
        return source_id

    def register(self, kind, path, options=None):
        if kind not in ('products','collaboration') or not isinstance(path,str):
            raise ImportFailure('reference_kind','请选择本机商品库或协同表xlsx路径。')
        target=Path(path)
        if not target.is_absolute() or path.startswith(('\\\\','//')) or target.suffix.lower()!='.xlsx' or '\x00' in path:
            raise ImportFailure('reference_path','来源必须是本机绝对xlsx路径，不支持在线地址或网络共享。')
        if any(p.is_symlink() or getattr(p,'is_junction',lambda:False)() for p in (target,*target.parents)):
            raise ImportFailure('reference_path','来源路径不能包含链接或联接。')
        if not target.is_file():
            raise ImportFailure('reference_path','找不到本机xlsx副本，请检查文件路径。')
        with self.guard:
            if options is not None and not isinstance(options,dict):
                raise ImportFailure('reference_options','来源解析选项必须为对象。')
            if options is None:
                with self.store.connection() as conn:
                    previous=conn.execute('SELECT s.options FROM reference_paths p JOIN sources s ON s.id=p.source_id WHERE p.kind=? AND p.path=?',(kind,str(target.absolute()))).fetchone()
                options=json.loads(previous['options']) if previous else {}
            before=self._signature(path)
            parsed = parse_reference(path,kind,options)
            signature = self._signature(path)
            if signature!=before:
                raise ImportFailure('source_changed','读取期间来源变化，请等文件保存完成后重试。')
            # XML hashes avoid reading GB media. File revision also separates
            # media-only updates, so old image caches never acquire new bytes.
            parsed['options']['fileRevision']=signature
            with self.store.connection(write=True) as conn:
                source_id = self._save_source(conn,kind,parsed)
                conn.execute('INSERT INTO reference_paths VALUES (?,?,?,?,?,?) ON CONFLICT(kind) DO UPDATE SET path=excluded.path,signature=excluded.signature,source_id=excluded.source_id,checked_at=excluded.checked_at,error=excluded.error',
                             (kind,str(Path(path).absolute()),signature,source_id,now(),''))
                self._sync_locks(conn)
                self.store._revalidate(conn)
            return {'ok':True,'sourceId':source_id,'rowCount':len(parsed['records']),
                    'issueCount':len(parsed.get('issues',[])),'options':parsed['options']}

    def refresh_sources(self, force=False):
        outcomes=[]
        with self.guard:
            with self.store.connection() as conn:
                sources=[dict(r) for r in conn.execute('SELECT * FROM reference_paths')]
            for source in sources:
                try:
                    signature=self._signature(source['path'])
                    if signature!=source['signature'] or force:
                        result=self.register(source['kind'],source['path'])
                        outcomes.append({'kind':source['kind'],'changed':True,**result})
                    else:
                        with self.store.connection(write=True) as conn:
                            conn.execute('UPDATE reference_paths SET checked_at=?,error=? WHERE kind=?',(now(),'',source['kind']))
                except (ImportFailure,OSError,ValueError) as error:
                    message=error.message if isinstance(error,ImportFailure) else '本地副本暂时不可读；继续保留上一有效快照。'
                    with self.store.connection(write=True) as conn:
                        conn.execute('UPDATE reference_paths SET checked_at=?,error=? WHERE kind=?',(now(),message,source['kind']))
                    outcomes.append({'kind':source['kind'],'changed':False,'error':message})
        return {'ok':True,'sources':outcomes}

    @staticmethod
    def _collaboration(conn):
        source=conn.execute("SELECT * FROM sources WHERE kind='collaboration' AND active=1").fetchone()
        records=json.loads(source['payload']) if source else []
        records=[r for r in records if r.get('fields') and not r.get('issues') and all(key(r['fields']))]
        return source,records

    def _sync_locks(self, conn):
        source,records=self._collaboration(conn)
        if not source:
            return
        groups=defaultdict(list)
        for record in records:
            groups[key(record['fields'])].append(record)
        for task in conn.execute('SELECT * FROM tasks WHERE archived=0').fetchall():
            candidates=groups[key(json.loads(task['fields']))]
            if len(candidates)!=1:
                continue
            row=candidates[0]
            if not (row.get('accountSent') or row.get('productSent')):
                continue
            old=conn.execute('SELECT payload FROM merchant_locks WHERE task_id=?',(task['id'],)).fetchone()
            previous=json.loads(old[0]) if old else {}
            evidence={'sourceId':source['id'],'sheet':row['sheet'],'row':row['row'],
                      'accountSent':bool(row.get('accountSent')),'productSent':bool(row.get('productSent')),
                      'fields':row['fields'][:9]}
            history=previous.get('evidence',[])
            if evidence in history:
                continue
            payload={'locked':True,'since':previous.get('since',now()),
                     'accountSent':previous.get('accountSent',False) or evidence['accountSent'],
                     'productSent':previous.get('productSent',False) or evidence['productSent'],
                     'fields':previous.get('fields',row['fields'][:9]),
                     'evidence':history+[evidence]}
            conn.execute('INSERT INTO merchant_locks VALUES (?,?) ON CONFLICT(task_id) DO UPDATE SET payload=excluded.payload',(task['id'],dump(payload)))
            self._audit(conn,'merchant_lock',task['id'],evidence)

    @staticmethod
    def _handed(conn, task):
        if task['stage']!='material' or task['frozen_evidence']:
            return True
        for table in ('material_tasks','editing_tasks'):
            if conn.execute('SELECT 1 FROM sqlite_master WHERE type=? AND name=?',('table',table)).fetchone():
                if conn.execute(f'SELECT 1 FROM {table} WHERE task_id=?',(task['id'],)).fetchone():
                    return True
        return False

    def account_profiles(self, profiles, confirmed):
        if confirmed is not True or not isinstance(profiles,list) or not 1<=len(profiles)<=200:
            raise ImportFailure('account_profiles','请明确确认已依据飞书F列核对账号与允许类目。')
        with self.guard,self.store.connection(write=True) as conn:
            # The manual form cannot create task-name aliases. Capture binding
            # requires the separate, explicitly reviewed draft transaction.
            normalized=[]
            for p in profiles:
                if not isinstance(p,dict):
                    raise ImportFailure('account_profiles','账号资料格式不合法。')
                normalized.append({k:p.get(k) for k in ('accountId','name','allowed','evidence')})
            return self._account_profiles(conn,normalized)

    def _account_profiles(self,conn,profiles):
        """Save profile revisions inside the caller's single transaction."""
        active=conn.execute("SELECT payload FROM sources WHERE kind='accounts' AND active=1").fetchone()
        existing={r['accountId']:r for r in json.loads(active[0])} if active else {}
        seen=set()
        for profile in profiles:
            account=profile.get('accountId');name=profile.get('name');evidence=profile.get('evidence')
            allowed=profile.get('allowed')
            account=account.strip() if isinstance(account,str) else account
            if not isinstance(account,str) or not account or account in seen or not isinstance(name,str) or not name.strip() or not isinstance(evidence,str) or not evidence.strip() or not isinstance(allowed,list) or not allowed or any(not isinstance(c,str) or not c.strip() or len(c)>100 for c in allowed):
                raise ImportFailure('account_profiles','每个账号需名称、独立证据说明和允许类目；不能从选品反推。')
            if any(len(v)>4000 for v in (account,name,evidence)):
                raise ImportFailure('account_profiles','账号资料超过长度限制。')
            seen.add(account)
            old=existing.get(account,{})
            # A manual edit retains UID/H/source history. A changed name does
            # not silently retain approval of a previous execution alias.
            merged={**old,**profile,'accountId':account,'name':name,
                    'allowed':list(dict.fromkeys(c.strip() for c in allowed)),
                    'sheet':'人工核对','row':0,'issues':[],'evidence':evidence.strip(),'verifiedAt':now()}
            if 'taskNames' not in profile and old.get('name')!=name:
                merged['taskNames']=[]
            existing[account]=merged
        codes={}
        for profile in existing.values():
            code=profile.get('companyCode','').strip().casefold()
            if code and code in codes and codes[code]!=profile['accountId']:
                raise ImportFailure('company_code_duplicate','H列公司账号编号已属于其他账号，请先解决冲突；不会自动重新分配。')
            if code:
                codes[code]=profile['accountId']
        records=list(existing.values())
        content={'records':records,'options':{'complete':True,'manualVerified':True},'filename':'人工核对账号资料', 'issues':[]}
        content['sha256']=hashlib.sha256(dump(records).encode()).hexdigest()
        sid=self._save_source(conn,'accounts',content)
        self.store._revalidate(conn)
        return {'ok':True,'sourceId':sid,'rowCount':len(records)}

    def enrich(self,state):
        with self.store.connection() as conn:
            source,records=self._collaboration(conn)
            current=defaultdict(list);history=defaultdict(list)
            for record in records:
                current[key(record['fields'])].append(record)
                history[(record['accountId'],record['productId'])].append(record)
            locks={r['task_id']:json.loads(r['payload']) for r in conn.execute('SELECT * FROM merchant_locks')}
            products={}
            for row in conn.execute("SELECT * FROM sources WHERE kind='products'"):
                mapping=defaultdict(list)
                for record in json.loads(row['payload']):
                    mapping[record['productId']].append(record)
                products[row['id']]=mapping
            active=conn.execute("SELECT id FROM sources WHERE kind='products' AND active=1").fetchone()
            sources=[dict(r) for r in conn.execute('SELECT * FROM reference_paths')]
            accounts=conn.execute("SELECT payload FROM sources WHERE kind='accounts' AND active=1").fetchone()
            profiles=json.loads(accounts[0]) if accounts else []
            layout={r['task_id']:dict(r) for r in conn.execute('SELECT * FROM task_layout')}
            for task in state['tasks']:
                f=task['fields']; matches=current[key(f)]; old=locks.get(task['id'],{})
                match=matches[0] if len(matches)==1 else None
                differences=[{'column':HEADERS[i],'local':f[i],'source':match['fields'][i]} for i in range(9) if match and f[i]!=match['fields'][i]]
                prior=[{'date':r['fields'][0],'sheet':r['sheet'],'row':r['row'],'accountSent':r.get('accountSent',False),'productSent':r.get('productSent',False)}
                       for r in history[(task['accountId'],f[5].strip())] if r['fields'][0]<f[0]]
                task['collaboration']={'match':'matched' if match else 'ambiguous' if matches else 'missing',
                                       'accountSent':bool(old.get('accountSent') or match and match.get('accountSent')),
                                       'productSent':bool(old.get('productSent') or match and match.get('productSent')),
                                       'locked':bool(old),'differences':differences,'history':prior,
                                       'sourceId':source['id'] if source else None,
                                       'row':match['row'] if match else None,'evidence':old.get('evidence',[]),
                                       'markerChanged':bool(old and (not match or old.get('accountSent') and not match.get('accountSent') or old.get('productSent') and not match.get('productSent')))}
                sid=task.get('frozenEvidence',{}).get('products') or (active[0] if active else None)
                goods=[r for r in products.get(sid,{}).get(f[5].strip(),[]) if not r.get('issues')]
                main=next((r for r in goods if r.get('categories')),goods[0] if goods else {})
                task['product']={k:main.get(k,'') for k in ('title','requirement','link','group')}
                task['product'].update(categories=sorted({c for r in goods for c in r.get('categories',[])}),tags=sorted({c for r in goods for c in r.get('tags',[])}))
                image=next((r.get('image') for r in goods if r.get('image')),None)
                task['product']['imageUrl']=f'/api/products/image?sourceId={quote(sid)}&member={quote(image["member"],safe="")}' if sid and image else None
                task['currentSource']=layout.get(task['id'])
            # Re-import may insert an account/product before existing rows.
            # UUID/batch identity stays stable; daily sheet order is separate.
            ordered={}
            for task in state['tasks']:
                ordered.setdefault(tuple(task['fields'][:2]),[]).append(task)
            state['tasks']=[task for group in ordered.values() for task in sorted(group,key=lambda t:layout.get(t['id'],{}).get('row_index',t['sourceRow']))]
            state.update(referencePaths=sources,accountProfiles=profiles)
            visible=[t for t in state['tasks'] if not t.get('archived')]
            state['counts'].update(tasks=len(visible),archived=len(state['tasks'])-len(visible),
                                   blocked=sum(not t['validation']['ok'] for t in visible),
                                   eligible=sum(t['validation']['ok'] and t['stage']=='material' for t in visible))
        return state

    def image(self,source_id,member):
        if not isinstance(source_id,str) or not isinstance(member,str):
            raise ImportFailure('image','无效图片来源。')
        with self.store.connection() as conn:
            source=conn.execute("SELECT * FROM sources WHERE id=? AND kind='products'",(source_id,)).fetchone()
            path=conn.execute("SELECT * FROM reference_paths WHERE kind='products'").fetchone()
        if not source:
            raise ImportFailure('image','图片来源不存在。')
        referenced=any(r.get('image',{}).get('member')==member for r in json.loads(source['payload']) if r.get('image'))
        if not referenced:
            raise ImportFailure('image','该图片未在商品来源中引用。')
        cache_key=hashlib.sha256((source_id+':'+member).encode()).hexdigest()
        cached=self.store._path('reference','images',cache_key+'.bin')
        mimefile=cached.with_suffix('.mime')
        if cached.is_file() and mimefile.is_file():
            return cached.read_bytes(),mimefile.read_text(encoding='utf-8')
        # An old source may only use its own cached image, never today's file.
        if not path or path['source_id']!=source_id or path['signature']!=self._signature(path['path']):
            raise ImportFailure('image_snapshot','原版本图片尚未缓存；不会拿新版图片代替。')
        data,mime=read_product_image(path['path'],member)
        if path['signature']!=self._signature(path['path']):
            raise ImportFailure('image_snapshot','读取期间来源变化，请刷新后重试。')
        self.store._write_once(cached,data)
        self.store._write_once(mimefile,mime.encode())
        return data,mime

    def _revision(self,conn,parsed):
        self._sync_locks(conn)
        rows=conn.execute('SELECT * FROM tasks ORDER BY rowid').fetchall()
        active=[r for r in rows if not r['archived']]
        by_key=defaultdict(list)
        for row in rows:
            by_key[key(json.loads(row['fields']))].append(row)
        incoming=defaultdict(list)
        for record in parsed['records']:
            incoming[key(record['fields'])].append(record)
        scopes={(r['fields'][0],r['fields'][1]) for r in parsed['records']}
        changes=[];assigned=set()
        for record in parsed['records']:
            k=key(record['fields']);candidates=by_key[k]
            live=[r for r in candidates if not r['archived']]
            candidates=live or candidates
            if len(candidates)>1 or len(incoming[k])>1:
                changes.append({'action':'conflict','row':record['row'],'reason':'同日同账号商品存在多行，需人工核对，未合并。'})
                assigned.update(r['id'] for r in candidates)
                continue
            if not candidates:
                changes.append({'action':'add','row':record['row']})
                continue
            old=candidates[0];assigned.add(old['id']);f=json.loads(old['fields'])
            diffs=[{'column':HEADERS[i],'before':f[i],'after':record['fields'][i]} for i in range(16) if f[i]!=record['fields'][i]]
            locked=conn.execute('SELECT 1 FROM merchant_locks WHERE task_id=?',(old['id'],)).fetchone() is not None
            protected=self._handed(conn,old)
            conflict=bool(diffs) and (protected or locked and any(f[i]!=record['fields'][i] for i in range(9)))
            changes.append({'action':'conflict' if conflict else 'restore' if old['archived'] else 'update' if diffs else 'unchanged',
                            'taskId':old['id'],'row':record['row'],'differences':diffs,
                            'reason':'商家锁定A:I或已交接任务不能覆盖。' if conflict else ''})
        for old in active:
            if old['id'] in assigned or tuple(json.loads(old['fields'])[:2]) not in scopes:
                continue
            locked=conn.execute('SELECT 1 FROM merchant_locks WHERE task_id=?',(old['id'],)).fetchone() is not None
            protected=locked or self._handed(conn,old)
            changes.append({'action':'conflict' if protected else 'archive','taskId':old['id'],
                            'reason':'已锁定或交接任务不能从修订中移除。' if protected else '仅归档，保留历史。'})
        signature={'sha256':parsed['sha256'],'changes':changes,
                   'tasks':[(r['id'],r['fields'],r['archived'],r['frozen_evidence']) for r in rows],
                   'sources':[(r['kind'],r['id']) for r in conn.execute('SELECT kind,id FROM sources WHERE active=1 ORDER BY kind')],
                   'aliases':conn.execute("SELECT value FROM settings WHERE key='aliases'").fetchone()[0],
                   'locks':[(r['task_id'],r['payload']) for r in conn.execute('SELECT * FROM merchant_locks ORDER BY task_id')]}
        token=hashlib.sha256(dump(signature).encode()).hexdigest()
        return changes,token

    def preview_tasks(self,filename,content,options=None):
        _,parsed,result=self.store._prepared('tasks',filename,content,options)
        with self.guard,self.store.connection(write=True) as conn:
            changes,token=self._revision(conn,parsed)
        result.update(changes=changes,revisionToken=token,conflicts=sum(c['action']=='conflict' for c in changes))
        return result

    def commit_tasks(self,filename,content,options,token):
        filename,parsed,result=self.store._prepared('tasks',filename,content,options)
        with self.guard,self.store.connection(write=True) as conn:
            changes,current=self._revision(conn,parsed)
            if not isinstance(token,str) or token!=current:
                raise ImportFailure('revision_stale','任务或来源状态已变化，请重新预览差异。')
            if any(c['action']=='conflict' for c in changes):
                raise ImportFailure('revision_conflict','存在受保护或重复映射冲突，本次未修改任务。',changes)
            snapshot=self.store._path('sources',parsed['sha256']+'.xlsx')
            self.store._write_once(snapshot,content)
            source_id=self._save_source(conn,'tasks',{**parsed,'filename':filename},snapshot)
            batch_id=str(uuid.uuid5(uuid.UUID(source_id),'batch'))
            if not conn.execute('SELECT 1 FROM batches WHERE id=?',(batch_id,)).fetchone():
                copies={stage:str(self.store._path('work',batch_id,stage,'Sheet1.xlsx')) for stage in ('material','editing','publishing')}
                for path in copies.values():
                    self.store._write_once(Path(path),content)
                conn.execute('INSERT INTO batches VALUES (?,?,?,?,?)',(batch_id,source_id,filename,now(),dump(copies)))
            records={r['row']:r for r in parsed['records']}
            for change in changes:
                action=change['action']
                if action=='unchanged':
                    record=records[change['row']]
                    conn.execute('INSERT INTO task_layout VALUES (?,?,?) ON CONFLICT(task_id) DO UPDATE SET source_id=excluded.source_id,row_index=excluded.row_index',(change['taskId'],source_id,record['row']))
                    conn.execute('UPDATE tasks SET source_row=?,source_sheet=? WHERE id=?',(record['row'],record['sheet'],change['taskId']))
                    continue
                if action=='archive':
                    old=conn.execute('SELECT * FROM tasks WHERE id=?',(change['taskId'],)).fetchone()
                    self._version(conn,old,source_id,old['source_row'],'archive')
                    conn.execute('UPDATE tasks SET archived=1 WHERE id=?',(old['id'],))
                    continue
                record=records[change['row']];fields=record['fields']
                if action=='add':
                    task_id=str(uuid.uuid5(uuid.UUID(batch_id),record['sheet']+':'+str(record['row'])))
                    conn.execute('INSERT INTO tasks (id,batch_id,source_row,source_sheet,fields,cell_meta,account_id,stage,source_issues,events,validation) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                                 (task_id,batch_id,record['row'],record['sheet'],dump(fields),dump(record['cellMeta']),fields[3].strip(),
                                  'reported' if fields[12].strip() or fields[13].strip() else 'material',dump(record['issues']),
                                  dump([{'time':now(),'text':'由任务表修订新增，保留来源版本'}]),dump({'ok':False,'evidence':{}})))
                else:
                    task_id=change['taskId']
                    old=conn.execute('SELECT * FROM tasks WHERE id=?',(task_id,)).fetchone()
                    self._version(conn,old,source_id,old['source_row'],'before-'+action)
                    events=json.loads(old['events'])+[{'time':now(),'text':'任务表修订：'+action+'，原版本已保留'}]
                    conn.execute('UPDATE tasks SET fields=?,cell_meta=?,source_issues=?,source_row=?,source_sheet=?,account_id=?,archived=0,duplicate_confirmed=0,stage=?,events=? WHERE id=?',
                                 (dump(fields),dump(record['cellMeta']),dump(record['issues']),record['row'],record['sheet'],fields[3].strip(),
                                  'reported' if fields[12].strip() or fields[13].strip() else 'material',dump(events),task_id))
                updated=conn.execute('SELECT * FROM tasks WHERE id=?',(task_id,)).fetchone()
                self._version(conn,updated,source_id,record['row'],action)
                conn.execute('INSERT INTO task_layout VALUES (?,?,?) ON CONFLICT(task_id) DO UPDATE SET source_id=excluded.source_id,row_index=excluded.row_index',(task_id,source_id,record['row']))
            self._sync_locks(conn)
            self.store._revalidate(conn)
            self._audit(conn,'task_revision',source_id,{'changes':changes,'sha256':parsed['sha256']})
        result.update(changes=changes,revisionToken=current)
        return result

    @staticmethod
    def _version(conn,task,source_id,row,action):
        if action.startswith('before-') or action=='archive':
            previous=conn.execute('SELECT source_id FROM task_versions WHERE task_id=? ORDER BY id DESC LIMIT 1',(task['id'],)).fetchone()
            source_id=previous[0] if previous else conn.execute('SELECT source_id FROM batches WHERE id=?',(task['batch_id'],)).fetchone()[0]
        conn.execute('INSERT INTO task_versions(task_id,source_id,source_row,fields,cell_meta,created_at,action) VALUES (?,?,?,?,?,?,?)',
                     (task['id'],source_id,row,task['fields'],task['cell_meta'],now(),action))
