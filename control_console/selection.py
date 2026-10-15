"""Local selection drafts and append-only task generation (006A3)."""
import hashlib
import json
import re
import uuid
from collections import defaultdict
from datetime import date
from pathlib import Path
from urllib.parse import quote

from .importers import ImportFailure
from .store import dump, now
from .workbooks import task_workbook


def fail(message, code='selection'):
    raise ImportFailure(code, message)


def valid_date(value):
    try:
        if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
            raise ValueError()
    except ValueError:
        fail('请选择有效的完整日期。')
    return value


class SelectionService:
    def __init__(self, store, workbench):
        self.store, self.workbench = store, workbench
        with store.connection(write=True) as c:
            c.executescript('''
                CREATE TABLE IF NOT EXISTS selection_drafts(id TEXT PRIMARY KEY,version INTEGER NOT NULL,payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS selection_presets(id TEXT PRIMARY KEY,version INTEGER NOT NULL,payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS selection_targets(id TEXT PRIMARY KEY,version INTEGER NOT NULL,payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS selection_previews(token TEXT PRIMARY KEY,payload TEXT NOT NULL,result TEXT);
            ''')

    @staticmethod
    def _rows(c, table):
        return [{**json.loads(r['payload']), 'id':r['id'], 'version':r['version']} for r in c.execute('SELECT * FROM '+table+' ORDER BY rowid')]

    def _data(self, c, include_archived=False):
        sources = {r['kind']:dict(r) for r in c.execute("SELECT * FROM sources WHERE active=1 AND kind IN ('accounts','products')")}
        aliases = json.loads(c.execute("SELECT value FROM settings WHERE key='aliases'").fetchone()[0])
        canonical = lambda s: aliases.get(s.strip(), s.strip())
        accounts = []
        raw_accounts = json.loads(sources['accounts']['payload']) if 'accounts' in sources else []
        for a in raw_accounts:
            a = dict(a)
            a.update(id=a['accountId'], allowed=[canonical(v) for v in a.get('allowed',[])], verified=bool(a.get('verifiedAt') and a.get('evidence') and a.get('allowed') and not a.get('issues')))
            if sum(r['accountId']==a['accountId'] for r in raw_accounts)!=1:
                a['verified']=False
            accounts.append(a)
        raw = json.loads(sources['products']['payload']) if 'products' in sources else []
        by_product=defaultdict(list)
        for r in raw: by_product[r['productId']].append(r)
        products = []
        path=c.execute("SELECT * FROM reference_paths WHERE kind='products'").fetchone()
        source_problem=''
        if path:
            try:
                if path['source_id']!=sources.get('products',{}).get('id') or path['signature']!=self.workbench._signature(path['path']): source_problem='商品本地副本已变化，请刷新核对来源。'
            except OSError: source_problem='商品本地副本暂时不可读。'
            if path['error']: source_problem=path['error']
        for p in raw:
            p = dict(p)
            peers = by_product[p['productId']]
            categories = {canonical(v) for r in peers for v in r.get('categories',[])}
            groups = {r.get('groupO','').strip() for r in peers if r.get('categories')}
            links = {r.get('link','').strip() for r in peers if r.get('categories')}
            issues = list(p.get('issues',[]))
            if source_problem: issues.append(source_problem)
            if len(categories)!=1: issues.append('商品主分类缺失或冲突。')
            if len(groups)!=1 or not all(groups): issues.append('商品O列群名称缺失或冲突。')
            if len(links)!=1 or not all(links): issues.append('商品链接缺失或冲突。')
            if not p.get('categories'): issues.append('标签来源仅供补充，请选择主分类来源。')
            same_day=[r for r in peers if r.get('categories') and r.get('statisticDate')==p.get('statisticDate')]
            if len({dump({k:r.get(k,'') for k in ('title','link','groupO','requirement','groupC')}) for r in same_day})>1: issues.append('同商品同日期主来源字段冲突，请核对。')
            p['tags']=sorted({t for r in peers for t in r.get('tags',[])})
            if not p.get('catalogVerified') or not sources['products']['complete']: issues.append('商品来源表头或完整性尚未核对。')
            try: valid_date(p.get('statisticDate'))
            except ImportFailure: issues.append('商品统计日期缺失或未核对年份。')
            if not re.fullmatch(r'\d+', p['productId']): issues.append('商品ID必须是完整数字字符串。')
            p.update(id=f"{sources['products']['id']}:{p['sheet']}:{p['row']}", sourceId=sources['products']['id'], sourceSheet=p['sheet'], sourceRow=p['row'], category=next(iter(categories)) if len(categories)==1 else '未知分类', issues=issues)
            image=p.get('image')
            p['imageUrl']=f"/api/products/image?sourceId={quote(p['sourceId'])}&member={quote(image['member'],safe='')}" if isinstance(image,dict) and image.get('member') else None
            products.append(p)
        tasks = []
        # Display/export only active rows, but identity checks must also see
        # archived rows. Selection never recreates or restores archived work.
        task_query='SELECT * FROM tasks'+('' if include_archived else ' WHERE archived=0')+' ORDER BY rowid'
        for r in c.execute(task_query):
            f=json.loads(r['fields'])
            tasks.append({'id':r['id'],'fields':f,'accountId':r['account_id'],'date':f[0]})
        known={a['id'] for a in accounts}
        for t in tasks:
            if t['accountId'] not in known:
                accounts.append({'id':t['accountId'],'accountId':t['accountId'],'name':t['fields'][2],'operator':t['fields'][1],'allowed':[],'verified':False,'uid':'','cooperationCode':'','issues':['账号F范围尚未核对。']})
                known.add(t['accountId'])
        warnings=[r['error'] for r in c.execute('SELECT error FROM reference_paths WHERE error<>\'\'')]
        if not raw: warnings.append('尚未登记商品库本地副本，请先登记并核对来源。')
        elif any(p['issues'] for p in products): warnings.append('部分商品缺少可靠表头、完整日期或存在来源冲突；请在来源管理核对。')
        if not raw_accounts: warnings.append('尚未核对账号资料，请先登记账号并核对F列允许范围。')
        return sources, accounts, products, tasks, warnings

    def state(self):
        with self.store.connection() as c:
            _, accounts, products, tasks, warnings=self._data(c)
            return {'ok':True,'accounts':accounts,'tasks':tasks,'drafts':self._rows(c,'selection_drafts'), 'presets':self._rows(c,'selection_presets'),'targets':{r['id']:r['value'] for r in self._rows(c,'selection_targets')},'targetVersions':{r['id']:r['version'] for r in self._rows(c,'selection_targets')},'productDates':sorted({p['statisticDate'] for p in products if p.get('statisticDate')}),'warnings':warnings}

    @staticmethod
    def _account(accounts, account_id):
        matches=[a for a in accounts if a['id']==account_id]
        if len(matches)!=1: fail('请选择唯一有效账号。')
        return matches[0]

    @staticmethod
    def _reason(a,p):
        if not a['verified']: return '账号F列允许范围尚未核对。'
        if p['issues']: return '；'.join(str(i) for i in p['issues'])
        if p['category'] not in a['allowed']: return '商品品类不在账号允许范围。'
        return ''

    def catalog(self, body):
        sales_filters={key:body.get(key,'all') for key in ('totalSales','dailySales')}
        for value in sales_filters.values():
            if not isinstance(value,str) or (value not in ('all','missing') and not (value.startswith('raw:') and value[4:].strip())):
                fail('销量筛选必须为all、missing或raw:原文。')
        span=body.get('range','all')
        if span not in ('all','latest'): valid_date(span)
        with self.store.connection() as c:
            sources,accounts,products,tasks,warnings=self._data(c,include_archived=True)
            a=self._account(accounts,body.get('accountId')); drafts=self._rows(c,'selection_drafts')
            # Browsing remains available while F or another selection gate is
            # unresolved. A date needs its own evidence, not eligibility.
            candidates=[p for p in products if p.get('categories') and (body.get('showOther',False) or p['category'] in a['allowed'])]
            sales_text=lambda p,key: '' if p.get(key) is None else str(p[key]).strip()
            sales_options={}
            for key in sales_filters:
                values=sorted({sales_text(p,key) for p in candidates})
                sales_options[key]=[{'value':'raw:'+v if v else 'missing','label':v or '未提供'} for v in values]
            dates=[]
            for p in candidates:
                try: day=valid_date(p.get('statisticDate'))
                except ImportFailure: continue
                dates.append(day)
            dates=sorted(set(dates))
            wanted=dates[-1] if span=='latest' and dates else span
            rows=[]
            _,collab=self.workbench._collaboration(c)
            for p in candidates:
                if span!='all' and p.get('statisticDate')!=wanted: continue
                if any(value!='all' and sales_text(p,key)!=('' if value=='missing' else value[4:].strip()) for key,value in sales_filters.items()): continue
                if str(body.get('search','')).casefold() not in ' '.join(str(p.get(k,'')) for k in ('title','productId','category','groupO')).casefold(): continue
                selected=any(r['accountId']==a['id'] and r['date']==body.get('date') and r['fields'][5]==p['productId'] for r in drafts)
                already=any(r['accountId']==a['id'] and r['date']==body.get('date') and r['fields'][5]==p['productId'] for r in tasks)
                reason=self._reason(a,p) or ('已加入草稿。' if selected else '当天已有任务。' if already else '')
                before=body.get('date','9999')
                history={r['date'] for r in tasks if r['accountId']==a['id'] and r['fields'][5]==p['productId'] and r['date']<before}
                history.update(r['fields'][0] for r in collab if r.get('accountId',r['fields'][3]).strip()==a['id'] and r.get('productId',r['fields'][5]).strip()==p['productId'] and r['fields'][0]<before)
                rows.append({**p,'selected':selected,'alreadyTask':already,'selectable':not reason,'reason':reason,'history':sorted(history)})
            rows.sort(key=lambda p:p.get('statisticDate',''),reverse=True)
            try: page=max(1,int(body.get('page',1)))
            except (ValueError,TypeError): fail('页码无效。')
            return {'ok':True,'rows':rows[(page-1)*50:page*50],'total':len(rows),'page':page,'pageSize':50,'productDates':dates,'salesOptions':sales_options,'warnings':warnings,'sourceId':sources.get('products',{}).get('id')}

    def product(self, record_id):
        with self.store.connection() as c:
            matches=[p for p in self._data(c)[2] if p['id']==record_id]
            if len(matches)!=1: fail('商品来源已变化，请刷新。','selection_stale')
            return {'ok':True,'product':matches[0],**matches[0]}

    @staticmethod
    def _text(value):
        if not isinstance(value,str) or len(value)>4000 or re.search(r'[\x00-\x08\x0b\x0c\x0e-\x1f]',value): fail('字段必须为有效文本，最多4000字符。')
        return value.strip()

    def add(self, body):
        day=valid_date(body.get('date'))
        with self.workbench.guard,self.store.connection(write=True) as c:
            sources,accounts,products,tasks,_=self._data(c,include_archived=True); a=self._account(accounts,body.get('accountId'))
            p=next((p for p in products if p['id']==body.get('productId')),None)
            if not p: fail('商品来源已变化，请刷新。')
            reason=self._reason(a,p)
            if reason: fail(reason)
            for r in self._rows(c,'selection_drafts')+tasks:
                if r['accountId']==a['id'] and r['date']==day and r['fields'][5]==p['productId']: return {'ok':True,'reused':True,**r}
            op=body.get('operator','inherit'); op=a.get('operator','') if op=='inherit' else self._text(op)
            fields=[day,op,a['name'],a['accountId'],p.get('title',''),p['productId'],p['groupO'],p['link'],f"账号：{a['name']}\n抖音号：{a['accountId']}\nUID：{a.get('uid','')}\n合作码：{a.get('cooperationCode','')}",'','','','','','','']
            row={'id':str(uuid.uuid4()),'date':day,'accountId':a['id'],'productRecordId':p['id'],'fields':fields,'sources':{k:s['id'] for k,s in sources.items()}}
            c.execute('INSERT INTO selection_drafts VALUES (?,1,?)',(row['id'],dump(row)))
            return {'ok':True,**row,'version':1,'reused':False}

    def _draft(self,c,body):
        r=c.execute('SELECT * FROM selection_drafts WHERE id=?',(body.get('draftId'),)).fetchone()
        if not r or type(body.get('version')) is not int or r['version']!=body['version']: fail('草稿已变化，请刷新。','selection_stale')
        return json.loads(r['payload'])

    def update(self,body):
        with self.store.connection(write=True) as c:
            row=self._draft(c,body); changes=body.get('changes'); mapping={'title':4,'operator':1,'portHint':9,'shopName':10}
            if not isinstance(changes,dict) or any(k not in mapping for k in changes): fail('只能修改标题、运营人、端口提示和店铺。')
            for k,v in changes.items(): row['fields'][mapping[k]]=self._text(v)
            c.execute('UPDATE selection_drafts SET version=version+1,payload=? WHERE id=?',(dump(row),row['id']))
            return {'ok':True,**row,'version':body['version']+1}

    def remove(self,body):
        with self.store.connection(write=True) as c:
            row=self._draft(c,body); c.execute('DELETE FROM selection_drafts WHERE id=?',(row['id'],))
        return {'ok':True}

    @staticmethod
    def _quantity(value):
        if type(value) is not int or not 0<=value<=1000: fail('目标数量须为0—1000整数。')
        return value

    def target(self,body):
        with self.store.connection(write=True) as c:
            aid=body.get('accountId'); self._account(self._data(c)[1],aid); value=self._quantity(body.get('target',body.get('value')))
            return self._save_versioned(c,'selection_targets',aid,{'value':value},body.get('version'))

    @staticmethod
    def _save_versioned(c,table,rid,payload,version):
        old=c.execute('SELECT version FROM '+table+' WHERE id=?',(rid,)).fetchone()
        if old and (type(version) is not int or old[0]!=version): fail('记录已变化，请刷新。','selection_stale')
        v=old[0]+1 if old else 1
        c.execute('INSERT INTO '+table+' VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET version=excluded.version,payload=excluded.payload',(rid,v,dump(payload)))
        return {'ok':True,'id':rid,'version':v,**payload}

    def preset(self,body):
        name=self._text(body.get('name'))
        if not name or len(name)>40: fail('预设名称须为1—40字。')
        with self.store.connection(write=True) as c:
            targets=body.get('targets'); accounts=self._data(c)[1]
            if not isinstance(targets,dict) or set(targets)!={a['id'] for a in accounts}: fail('请填写全部账号目标。')
            checked={k:self._quantity(v) for k,v in targets.items()}; rid=body.get('id') or body.get('editId') or str(uuid.uuid4())
            if any(p['name']==name and p['id']!=rid for p in self._rows(c,'selection_presets')): fail('预设名称已存在。')
            return self._save_versioned(c,'selection_presets',rid,{'name':name,'targets':checked},body.get('version'))

    def apply_preset(self,body):
        with self.store.connection(write=True) as c:
            p=c.execute('SELECT * FROM selection_presets WHERE id=?',(body.get('id') or body.get('presetId'),)).fetchone()
            if not p: fail('预设不存在。')
            if type(body.get('version')) is not int or body['version']!=p['version']: fail('预设已变化，请刷新。','selection_stale')
            targets=json.loads(p['payload'])['targets']
            for aid,value in targets.items():
                self._account(self._data(c)[1],aid)
                old=c.execute('SELECT version FROM selection_targets WHERE id=?',(aid,)).fetchone()
                self._save_versioned(c,'selection_targets',aid,{'value':value},old[0] if old else None)
            return {'ok':True,'targets':targets}

    def _preview(self,c,body):
        sources,accounts,products,tasks,warnings=self._data(c,include_archived=True); a=self._account(accounts,body.get('accountId')); day=valid_date(body.get('date'))
        rows=[r for r in self._rows(c,'selection_drafts') if r['accountId']==a['id'] and r['date']==day]; issues=[]
        if not rows: issues.append('当天没有草稿。')
        for r in rows:
            p=next((p for p in products if p['id']==r['productRecordId']),None)
            if r['sources']!={k:s['id'] for k,s in sources.items()} or not p: issues.append('来源已变化，请移除草稿并重新核对选品。')
            elif self._reason(a,p): issues.append(self._reason(a,p))
            if not a.get('uid') or not a.get('cooperationCode'): issues.append('UID或合作码未补全。')
            if any(not r['fields'][i].strip() for i in range(9)): issues.append('任务必填字段缺失。')
            if any(t['accountId']==a['id'] and t['date']==day and t['fields'][5]==r['fields'][5] for t in tasks): issues.append('当日已有同账号商品任务。')
        # Include all task identities and protected bytes, not only this account.
        fingerprint=hashlib.sha256(dump({'sources':sources,'otherEvidence':[dict(r) for r in c.execute("SELECT id,sha256 FROM sources WHERE kind='collaboration' AND active=1")],'aliases':c.execute("SELECT value FROM settings WHERE key='aliases'").fetchone()[0],'drafts':rows,'tasks':[dict(r) for r in c.execute('SELECT * FROM tasks ORDER BY id')],'locks':[dict(r) for r in c.execute('SELECT * FROM merchant_locks ORDER BY task_id')]}).encode()).hexdigest()
        return {'rows':rows,'issues':list(dict.fromkeys(issues)),'warnings':warnings,'fingerprint':fingerprint,'body':body}

    def preview(self,body):
        with self.workbench.guard,self.store.connection(write=True) as c:
            p=self._preview(c,body); token=str(uuid.uuid4()); c.execute('INSERT INTO selection_previews VALUES (?,?,NULL)',(token,dump(p)))
            return {'ok':True,'token':token,**{k:p[k] for k in ('rows','issues','warnings')}}

    def generate(self,body):
        if body.get('confirmed') is not True: fail('请明确确认16列预览。')
        with self.workbench.guard,self.store.connection(write=True) as c:
            saved=c.execute('SELECT * FROM selection_previews WHERE token=?',(body.get('token'),)).fetchone()
            if not saved: fail('预览不存在，请重新预览。')
            if saved['result']: return {**json.loads(saved['result']),'reused':True}
            previous=json.loads(saved['payload']); current=self._preview(c,previous['body'])
            if previous['fingerprint']!=current['fingerprint']: fail('来源、草稿或任务已变化，请重新预览。','selection_stale')
            if current['issues']: fail('；'.join(current['issues']))
            existing_tasks=[dict(r) for r in c.execute('SELECT * FROM tasks')]
            content=task_workbook(current['rows']); filename,parsed,result=self.store._prepared('tasks','选品任务.xlsx',content,{})
            if parsed['issues'] or any(r['issues'] for r in parsed['records']): fail('生成工作表校验失败，请核对预览。')
            snapshot=self.store._path('sources',parsed['sha256']+'.xlsx'); self.store._write_once(snapshot,content)
            sid=self.workbench._save_source(c,'tasks',{**parsed,'filename':filename},snapshot); bid=result['batchId']
            if c.execute('SELECT 1 FROM batches WHERE id=?',(bid,)).fetchone(): fail('相同任务批次已存在。')
            copies={stage:str(self.store._path('work',bid,stage,'Sheet1.xlsx')) for stage in ('material','editing','publishing')}
            for path in copies.values(): self.store._write_once(Path(path),content)
            c.execute('INSERT INTO batches VALUES (?,?,?,?,?)',(bid,sid,filename,now(),dump(copies))); created=[]
            for record in parsed['records']:
                tid=str(uuid.uuid5(uuid.UUID(bid),record['sheet']+':'+str(record['row'])))
                c.execute('INSERT INTO tasks(id,batch_id,source_row,source_sheet,fields,cell_meta,account_id,stage,source_issues,events,validation) VALUES (?,?,?,?,?,?,?,?,?,?,?)',(tid,bid,record['row'],record['sheet'],dump(record['fields']),dump(record['cellMeta']),record['fields'][3],'material',dump(record['issues']),dump([{'time':now(),'text':'每日选品确认追加'}]),dump({'ok':False,'evidence':{}})))
                task=c.execute('SELECT * FROM tasks WHERE id=?',(tid,)).fetchone(); self.workbench._version(c,task,sid,record['row'],'selection-add')
                c.execute('INSERT INTO task_layout VALUES (?,?,?)',(tid,sid,record['row'])); created.append({'id':tid,'fields':record['fields'],'accountId':record['fields'][3],'date':record['fields'][0]})
            # Only new tasks are eligible for merchant synchronization. Preserve
            # every existing lock byte even when collaboration changed.
            old_locks=[dict(r) for r in c.execute('SELECT * FROM merchant_locks')]
            self.workbench._sync_locks(c)
            for lock in old_locks: c.execute('UPDATE merchant_locks SET payload=? WHERE task_id=?',(lock['payload'],lock['task_id']))
            locked_ids={r['task_id'] for r in old_locks}
            for old in existing_tasks:
                if old['id'] not in locked_ids: c.execute('DELETE FROM merchant_locks WHERE task_id=?',(old['id'],))
            self.store._revalidate(c)
            for old in existing_tasks:
                c.execute('UPDATE tasks SET validation=?,category=?,allowed=? WHERE id=?',(old['validation'],old['category'],old['allowed'],old['id']))
            for task in created:
                validation=json.loads(c.execute('SELECT validation FROM tasks WHERE id=?',(task['id'],)).fetchone()[0])
                if not validation['ok']: fail('新任务证据校验未通过：'+validation.get('reason',''))
            for row in current['rows']: c.execute('DELETE FROM selection_drafts WHERE id=?',(row['id'],))
            outcome={'ok':True,'batchId':bid,'generationId':bid,'taskIds':[t['id'] for t in created],'sourceId':sid,'tasks':created,'count':len(created),'reused':False}
            c.execute('UPDATE selection_previews SET result=? WHERE token=?',(dump(outcome),body['token']))
            self.workbench._audit(c,'selection_generate',bid,{'drafts':[r['id'] for r in current['rows']]})
            return outcome

    def export(self,body):
        day=valid_date(body.get('date'))
        with self.store.connection() as c:
            tasks=self._data(c)[3]
            return task_workbook([t for t in tasks if t['date']==day and (not body.get('accountId') or t['accountId']==body['accountId'])])
