"""SQLite metadata and immutable byte snapshots, independent of business XLSX."""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path, PureWindowsPath

from .importers import ImportFailure, parse_import


def now():
    return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec='seconds')


def dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',',':'))


class ConsoleStore:
    def __init__(self, runtime_dir):
        self.root = Path(runtime_dir).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = self.root / 'console.sqlite3'
        setup = sqlite3.connect(self.db, timeout=30)
        try:
            setup.execute('PRAGMA journal_mode=WAL')
        finally:
            setup.close()
        with self.connection() as conn:
            conn.executescript('''
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS sources (
                    id TEXT PRIMARY KEY, kind TEXT NOT NULL, filename TEXT NOT NULL,
                    sha256 TEXT NOT NULL, created_at TEXT NOT NULL, complete INTEGER NOT NULL,
                    active INTEGER NOT NULL, options TEXT NOT NULL, payload TEXT NOT NULL,
                    issues TEXT NOT NULL, snapshot TEXT NOT NULL, row_count INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS batches (
                    id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES sources(id),
                    filename TEXT NOT NULL, created_at TEXT NOT NULL, copies TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY, batch_id TEXT NOT NULL REFERENCES batches(id),
                    source_row INTEGER NOT NULL, source_sheet TEXT NOT NULL, fields TEXT NOT NULL,
                    cell_meta TEXT NOT NULL, account_id TEXT NOT NULL, stage TEXT NOT NULL,
                    source_issues TEXT NOT NULL, duplicate_confirmed INTEGER NOT NULL DEFAULT 0,
                    note TEXT NOT NULL DEFAULT '', events TEXT NOT NULL, validation TEXT NOT NULL,
                    category TEXT NOT NULL DEFAULT '', allowed TEXT NOT NULL DEFAULT '[]');
                CREATE TABLE IF NOT EXISTS audit (
                    id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, action TEXT NOT NULL,
                    subject TEXT NOT NULL, details TEXT NOT NULL);
            ''')
            conn.execute('INSERT OR IGNORE INTO settings VALUES (?,?)', ('aliases',dump({'食品-宠物':'食品/宠物','百货个护家清':'百货/个护家清'})))

    @contextmanager
    def connection(self, write=False):
        conn = sqlite3.connect(self.db, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys=ON')
        try:
            conn.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _path(self, *parts):
        path = self.root.joinpath(*parts)
        if not path.resolve().is_relative_to(self.root):
            raise ImportFailure('storage_path','本地存储路径必须位于指定runtime内。')
        return path

    def _write_once(self, path, content):
        path.parent.mkdir(parents=True,exist_ok=True)
        if path.exists():
            if hashlib.sha256(path.read_bytes()).hexdigest() != hashlib.sha256(content).hexdigest():
                raise ImportFailure('snapshot_changed','已有快照或工作副本内容已变化，不能覆盖。')
            return
        temporary = None
        try:
            # Public callers hold the SQLite write transaction. Publish only
            # after a complete durable temp write, so a disk failure can retry.
            with tempfile.NamedTemporaryFile(dir=path.parent,prefix='.import-',suffix='.tmp',delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            if path.exists():
                if hashlib.sha256(path.read_bytes()).hexdigest() != hashlib.sha256(content).hexdigest():
                    raise ImportFailure('snapshot_changed','已有快照或工作副本内容已变化，不能覆盖。')
            else:
                os.replace(temporary,path)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()

    def _prepared(self, kind, filename, content, options):
        if not isinstance(filename,str) or not filename.lower().endswith('.xlsx') or len(filename) > 255:
            raise ImportFailure('file_type','首版只接受.xlsx文件，请保留原文件并取得xlsx副本。')
        filename = PureWindowsPath(filename).name
        parsed = parse_import(kind,content,options)
        identity = kind + ':' + parsed['sha256'] + ':' + dump(parsed['options'])
        source_id = str(uuid.uuid5(uuid.NAMESPACE_URL,identity))
        batch_id = str(uuid.uuid5(uuid.UUID(source_id),'batch')) if kind == 'tasks' else None
        result = {'ok':True,'kind':kind,'sourceId':source_id,'reused':False,'rowCount':len(parsed['records']),
                  'validCount':sum(not r.get('issues') for r in parsed['records']),'issues':parsed['issues'],
                  'previewRows':[{'row':r['row'],'fields':r.get('fields') or [r.get('productId') or r.get('accountId',''),r.get('name',''), '、'.join(r.get('categories') or r.get('allowed') or [])]} for r in parsed['records'][:20]]}
        if batch_id:
            result['batchId'] = batch_id
        return filename,parsed,result

    def preview_import(self, kind, filename, content, options=None):
        _,_,result = self._prepared(kind,filename,content,options)
        with self.connection() as conn:
            result['reused'] = conn.execute('SELECT 1 FROM sources WHERE id=?',(result['sourceId'],)).fetchone() is not None
        return result

    def commit_import(self, kind, filename, content, options=None):
        filename,parsed,result = self._prepared(kind,filename,content,options)
        source_id = result['sourceId']
        created_at = now()
        with self.connection(write=True) as conn:
            existing = conn.execute('SELECT * FROM sources WHERE id=?',(source_id,)).fetchone()
            conn.execute('UPDATE sources SET active=0 WHERE kind=?',(kind,))
            if existing:
                conn.execute('UPDATE sources SET active=1 WHERE id=?',(source_id,))
                result['reused'] = True
            else:
                snapshot = self._path('sources',parsed['sha256'] + '.xlsx')
                self._write_once(snapshot,content)
                conn.execute('INSERT INTO sources VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                             (source_id,kind,filename,parsed['sha256'],created_at,int(parsed['options'].get('complete',True)),1,dump(parsed['options']),dump(parsed['records']),dump(parsed['issues']),str(snapshot),len(parsed['records'])))
                if kind == 'tasks':
                    copies = {stage:str(self._path('work',result['batchId'],stage,'Sheet1.xlsx')) for stage in ('material','editing','publishing')}
                    for path in copies.values():
                        self._write_once(Path(path),content)
                    conn.execute('INSERT INTO batches VALUES (?,?,?,?,?)',(result['batchId'],source_id,filename,created_at,dump(copies)))
                    for record in parsed['records']:
                        fields = record['fields']
                        task_id = str(uuid.uuid5(uuid.UUID(result['batchId']),record['sheet'] + ':' + str(record['row'])))
                        stage = 'reported' if fields[12].strip() or fields[13].strip() else 'material'
                        events = [{'time':created_at,'text':'本地导入，保留源表原始行' + str(record['row'])}]
                        if stage == 'reported':
                            events.append({'time':created_at,'text':'来源含M/N软件回执，尚未人工核验，不自动重复发布'})
                        conn.execute('INSERT INTO tasks (id,batch_id,source_row,source_sheet,fields,cell_meta,account_id,stage,source_issues,events,validation) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                                     (task_id,result['batchId'],record['row'],record['sheet'],dump(fields),dump(record['cellMeta']),fields[3].strip(),stage,dump(record['issues']),dump(events),dump({'ok':False,'code':'evidence_missing','reason':'等待分类证据','issues':[]})))
                conn.execute('INSERT INTO audit (created_at,action,subject,details) VALUES (?,?,?,?)',(created_at,'import',source_id,dump({'kind':kind,'sha256':parsed['sha256'],'rows':result['rowCount']})))
            self._revalidate(conn)
        return result

    def _revalidate(self, conn):
        aliases = json.loads(conn.execute('SELECT value FROM settings WHERE key=?',('aliases',)).fetchone()[0])
        canonical = lambda value: aliases.get(value.strip(),value.strip())
        active = {r['kind']:r for r in conn.execute('SELECT * FROM sources WHERE active=1')}
        products,accounts = {},{}
        for record in json.loads(active['products']['payload']) if 'products' in active else []:
            products.setdefault(record['productId'],[]).append(record)
        for record in json.loads(active['accounts']['payload']) if 'accounts' in active else []:
            accounts.setdefault(record['accountId'],[]).append(record)
        all_tasks = conn.execute('SELECT * FROM tasks').fetchall()
        duplicate_groups = {}
        for task in all_tasks:
            values = json.loads(task['fields'])
            key = (values[0],values[3].strip(),values[5].strip())
            if all(key):
                duplicate_groups.setdefault(key,[]).append(task['id'])
        for task in all_tasks:
            fields = json.loads(task['fields'])
            problems = [p for p in json.loads(task['source_issues']) if p['code'] != 'duplicate' or not task['duplicate_confirmed']]
            key = (fields[0],fields[3].strip(),fields[5].strip())
            duplicates = duplicate_groups.get(key,[])
            if len(duplicates)>1 and not task['duplicate_confirmed'] and not any(p['code']=='duplicate' for p in problems):
                problems.append({'row':task['source_row'],'code':'duplicate','message':'同日期、同账号、同商品存在其他来源行，请人工核对；所有任务与来源映射均保留。','taskIds':duplicates})
            category,allowed = '',[]
            if problems:
                code,reason = problems[0]['code'],problems[0]['message']
            elif 'products' not in active or 'accounts' not in active or not active['products']['complete'] or not active['accounts']['complete']:
                code,reason = 'evidence_missing','分类证据缺失：需要完整商品分类和账号F列快照。'
            else:
                goods = products.get(fields[5].strip(),[])
                owners = accounts.get(task['account_id'],[])
                categories = {canonical(c) for r in goods for c in r['categories']}
                account_versions = {(r['name'],tuple(sorted({canonical(c) for c in r['allowed']}))) for r in owners}
                if not goods:
                    code,reason = 'product_missing','商品分类证据缺失，不能凭标题推断。'
                elif any(r['issues'] for r in goods):
                    code,reason = 'product_invalid','商品来源行存在无效标识或类别值，请核对分类副本。'
                elif len(categories) != 1:
                    code,reason = 'category_conflict','同商品存在多个分类或未知分类，证据冲突。'
                elif not owners:
                    code,reason = 'account_missing','账号不在导入的账号F列快照中。'
                elif any(r['issues'] for r in owners):
                    code,reason = 'account_invalid','账号来源行缺值或标识不安全，请核对账号副本。'
                elif len(account_versions) != 1:
                    code,reason = 'account_conflict','同账号在来源中存在不同名称或允许品类。'
                else:
                    category = next(iter(categories))
                    name,categories_allowed = next(iter(account_versions))
                    allowed = list(categories_allowed)
                    if name != fields[2]:
                        code,reason = 'account_name','任务抖音名称与账号来源不一致，避免文件归属错配。'
                    elif category not in allowed:
                        code,reason = 'category_mismatch','品类不符：账号只允许 ' + '、'.join(allowed)
                    else:
                        code,reason = 'ok','商品类别与账号F列允许品类一致。'
            validation = {'ok':code == 'ok','code':code,'reason':reason,'issues':problems,
                          'evidence':{k:active[k]['id'] for k in ('products','accounts') if k in active}}
            conn.execute('UPDATE tasks SET validation=?,category=?,allowed=? WHERE id=?',(dump(validation),category,dump(allowed),task['id']))

    def set_aliases(self, aliases):
        if not isinstance(aliases,dict) or len(aliases) > 500 or any(not isinstance(k,str) or not isinstance(v,str) or not k.strip() or not v.strip() or len(k)>200 or len(v)>200 for k,v in aliases.items()):
            raise ImportFailure('aliases','类别映射必须是来源名称到对应类别的非空文本对象。')
        normalized = {k.strip():v.strip() for k,v in aliases.items()}
        with self.connection(write=True) as conn:
            conn.execute('UPDATE settings SET value=? WHERE key=?',(dump(normalized),'aliases'))
            conn.execute('INSERT INTO audit (created_at,action,subject,details) VALUES (?,?,?,?)',(now(),'aliases','aliases',dump(normalized)))
            self._revalidate(conn)
        return {'ok':True,'aliases':normalized}

    def review_task(self, task_id, note, duplicate_confirmed=None):
        if not isinstance(task_id,str) or not task_id or len(task_id)>100:
            raise ImportFailure('task_missing','请选择有效的本地任务。')
        if not isinstance(note,str) or len(note)>4000 or duplicate_confirmed is not None and type(duplicate_confirmed) is not bool:
            raise ImportFailure('review','备注限4000字符，重复核对值必须是布尔值。')
        with self.connection(write=True) as conn:
            task = conn.execute('SELECT * FROM tasks WHERE id=?',(task_id,)).fetchone()
            if not task:
                raise ImportFailure('task_missing','任务不存在。')
            confirmed = task['duplicate_confirmed'] if duplicate_confirmed is None else int(duplicate_confirmed)
            if note != task['note'] or confirmed != task['duplicate_confirmed']:
                events = json.loads(task['events'])
                events.append({'time':now(),'text':'更新独立备注与重复行核对记录；源任务表未改变'})
                conn.execute('UPDATE tasks SET note=?,duplicate_confirmed=?,events=? WHERE id=?',(note,confirmed,dump(events),task_id))
                conn.execute('INSERT INTO audit (created_at,action,subject,details) VALUES (?,?,?,?)',(now(),'review',task_id,dump({'note':note,'duplicateConfirmed':bool(confirmed)})))
                self._revalidate(conn)
        return {'ok':True,'taskId':task_id}

    def state(self):
        with self.connection() as conn:
            tasks = []
            for row in conn.execute('SELECT tasks.* FROM tasks JOIN batches ON tasks.batch_id=batches.id ORDER BY batches.created_at,batches.id,tasks.source_row'):
                fields = json.loads(row['fields'])
                tasks.append({'id':row['id'],'batchId':row['batch_id'],'sourceRow':row['source_row'],'sourceSheet':row['source_sheet'],
                              'fields':fields,'cellMeta':json.loads(row['cell_meta']),'accountId':row['account_id'],'category':row['category'],
                              'allowedCategories':json.loads(row['allowed']),'stage':row['stage'],'validation':json.loads(row['validation']),
                              'duplicateConfirmed':bool(row['duplicate_confirmed']),'note':row['note'],'events':json.loads(row['events']),
                              'sourceReports':{'m':fields[12],'n':fields[13]}})
            batches = [{'id':r['id'],'sourceId':r['source_id'],'filename':r['filename'],'createdAt':r['created_at'],
                        'taskCount':sum(t['batchId']==r['id'] for t in tasks),'workCopies':json.loads(r['copies'])} for r in conn.execute('SELECT * FROM batches ORDER BY created_at,id')]
            sources = [{'id':r['id'],'kind':r['kind'],'filename':r['filename'],'sha256':r['sha256'],'createdAt':r['created_at'],
                        'complete':bool(r['complete']),'active':bool(r['active']),'rowCount':r['row_count'],'options':json.loads(r['options']),
                        'issues':json.loads(r['issues'])} for r in conn.execute('SELECT * FROM sources ORDER BY created_at,id')]
            aliases = json.loads(conn.execute('SELECT value FROM settings WHERE key=?',('aliases',)).fetchone()[0])
            return {'ok':True,'tasks':tasks,'batches':batches,'sources':sources,'aliases':aliases,
                    'counts':{'tasks':len(tasks),'eligible':sum(t['validation']['ok'] and t['stage']=='material' for t in tasks),
                              'blocked':sum(not t['validation']['ok'] for t in tasks),'reported':sum(t['stage']=='reported' for t in tasks),'batches':len(batches)}}
