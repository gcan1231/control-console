"""006A3 uses fictional records and isolated temporary runtime only."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from control_console.importers import ImportFailure, parse_import
from control_console.selection import SelectionService
from control_console.store import ConsoleStore, dump
from control_console.workbench import WorkbenchService


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.store=ConsoleStore(Path(self.tmp.name)/'runtime'); self.wb=WorkbenchService(self.store)
        self.service=SelectionService(self.store,self.wb)
        self.account={'accountId':'fiction-a','name':'虚构账号','operator':'虚构运营','allowed':['食品/宠物'],'verifiedAt':'fiction','evidence':'虚构F核对','issues':[],'uid':'000123','cooperationCode':'000456'}
        self.goods={'productId':'9000000000000000001','sheet':'食品-宠物','row':2,'categories':['食品-宠物'],'tags':[],'issues':[],'catalogVerified':True,'statisticDate':'2026-10-15','groupO':'虚构O群','groupC':'虚构C商家','title':'虚构商品','link':'https://example.invalid/product/1'}
        self.sources('accounts',[self.account]); self.sources('products',[self.goods])

    def sources(self,kind,rows):
        parsed={'filename':'fiction.json','sha256':hashlib.sha256(dump(rows).encode()).hexdigest(),'records':rows,'options':{'complete':True},'issues':[]}
        with self.store.connection(write=True) as c: return self.wb._save_source(c,kind,parsed)

    def add(self,day='2026-10-15'):
        p=self.service.catalog({'accountId':'fiction-a','range':'all'})['rows'][0]
        return self.service.add({'accountId':'fiction-a','productId':p['id'],'date':day})

    def preview(self): return self.service.preview({'accountId':'fiction-a','date':'2026-10-15'})

    def generate(self):
        p=self.preview(); self.assertEqual(p['issues'],[])
        return self.service.generate({'token':p['token'],'confirmed':True})

    def test_persist_version_and_append_export_long_ids(self):
        first=self.add('2026-10-14'); self.generate_day('2026-10-14')
        with self.store.connection() as c: before=[dict(r) for r in c.execute('SELECT * FROM tasks')]
        draft=self.add(); self.assertEqual(draft['fields'][6],'虚构O群'); self.assertEqual(draft['fields'][9:],['']*7)
        draft=self.service.update({'draftId':draft['id'],'version':1,'changes':{'title':'短标题','portHint':'3'}})
        with self.assertRaises(ImportFailure): self.service.remove({'draftId':draft['id'],'version':1})
        reopened=SelectionService(ConsoleStore(self.store.root),self.wb)
        self.assertEqual(reopened.state()['drafts'][0]['version'],2)
        preview=self.preview(); result=self.service.generate({'token':preview['token'],'confirmed':True})
        self.assertEqual(result['count'],1)
        self.assertTrue(self.service.generate({'token':preview['token'],'confirmed':True})['reused'])
        with self.store.connection() as c: after=dict(c.execute('SELECT * FROM tasks WHERE id=?',(before[0]['id'],)).fetchone())
        self.assertEqual(before[0],after)
        parsed=parse_import('tasks',self.service.export({'date':'2026-10-15'}),{})
        f=parsed['records'][0]['fields']; self.assertEqual(len(f),16); self.assertEqual(f[5],self.goods['productId']); self.assertEqual(f[9],'3'); self.assertEqual(f[11:],['']*5)
        self.assertEqual(f[8],'账号：虚构账号\n抖音号：fiction-a\nUID：000123\n合作码：000456')

    def generate_day(self,day):
        p=self.service.preview({'accountId':'fiction-a','date':day})
        return self.service.generate({'token':p['token'],'confirmed':True})

    def test_source_and_draft_changes_invalidate_preview(self):
        draft=self.add(); p=self.preview()
        self.service.update({'draftId':draft['id'],'version':1,'changes':{'title':'改标题'}})
        with self.assertRaises(ImportFailure): self.service.generate({'token':p['token'],'confirmed':True})
        p=self.preview(); self.sources('products',[{**self.goods,'groupO':'另一个虚构群'}])
        with self.assertRaises(ImportFailure): self.service.generate({'token':p['token'],'confirmed':True})
        self.assertTrue(self.preview()['issues']); self.assertEqual(self.store.state()['tasks'],[])

    def test_uid_can_draft_f_unverified_cannot(self):
        self.sources('accounts',[{**self.account,'uid':''}]); self.add()
        self.assertTrue(self.preview()['issues'])
        self.sources('accounts',[{**self.account,'verifiedAt':''}])
        with self.assertRaises(ImportFailure): self.add('2026-10-16')

    def test_conflicts_no_first_row_and_tags_supplement_only(self):
        self.sources('products',[self.goods,{**self.goods,'row':3,'groupO':'冲突虚构群'}])
        rows=self.service.catalog({'accountId':'fiction-a','range':'all'})['rows']
        self.assertTrue(all(not r['selectable'] for r in rows))
        self.sources('products',[self.goods,{**self.goods,'row':3,'sheet':'精选商家','categories':[],'tags':['精选商家'],'groupO':''}])
        rows=self.service.catalog({'accountId':'fiction-a','range':'all'})['rows']
        self.assertEqual(len(rows),1); self.assertTrue(rows[0]['selectable']); self.assertEqual(rows[0]['tags'],['精选商家'])

    def test_confirmation_duplicates_and_presets(self):
        draft=self.add(); self.assertTrue(self.add()['reused'])
        p=self.preview()
        with self.assertRaises(ImportFailure): self.service.generate({'token':p['token']})
        self.service.generate({'token':p['token'],'confirmed':True})
        self.assertTrue(self.add()['reused']); self.assertEqual(self.service.state()['drafts'],[])
        preset=self.service.preset({'name':'虚构预设','targets':{'fiction-a':4}})
        self.service.apply_preset({'id':preset['id'],'version':preset['version']})
        state=self.service.state(); self.assertEqual(state['targets']['fiction-a'],4)
        with self.assertRaises(ImportFailure): self.service.target({'accountId':'fiction-a','value':6})
        self.service.target({'accountId':'fiction-a','value':6,'version':state['targetVersions']['fiction-a']})

    def test_generation_transaction_rolls_back(self):
        self.add(); p=self.preview(); original=self.store._revalidate
        def broken(c): raise OSError('fictional disk failure')
        self.store._revalidate=broken
        try:
            with self.assertRaises(OSError): self.service.generate({'token':p['token'],'confirmed':True})
        finally: self.store._revalidate=original
        self.assertEqual(self.store.state()['tasks'],[]); self.assertEqual(len(self.service.state()['drafts']),1)
        self.assertEqual(self.service.generate({'token':p['token'],'confirmed':True})['count'],1)

    def test_page_size_and_invalid_date(self):
        self.sources('products',[{**self.goods,'row':n+2,'productId':str(9000000000000000000+n)} for n in range(55)])
        page=self.service.catalog({'accountId':'fiction-a','range':'all','page':2})
        self.assertEqual(page['total'],55); self.assertEqual(len(page['rows']),5); self.assertEqual(page['pageSize'],50)
        with self.assertRaises(ImportFailure): self.add('2026-02-30')

    def test_protected_old_task_and_lock_bytes_remain_exact(self):
        self.add('2026-10-14'); result=self.generate_day('2026-10-14'); tid=result['taskIds'][0]
        with self.store.connection(write=True) as c:
            f=json.loads(c.execute('SELECT fields FROM tasks WHERE id=?',(tid,)).fetchone()[0])
            c.execute('INSERT INTO merchant_locks VALUES (?,?)',(tid,dump({'locked':True,'fields':f[:9],'evidence':[]})))
            c.execute('UPDATE tasks SET frozen_evidence=?,stage=? WHERE id=?',(dump({'products':'fiction-frozen'}),'editing',tid))
            before=dict(c.execute('SELECT * FROM tasks WHERE id=?',(tid,)).fetchone()); lock=c.execute('SELECT payload FROM merchant_locks WHERE task_id=?',(tid,)).fetchone()[0]
        self.add(); self.generate()
        with self.store.connection() as c:
            self.assertEqual(before,dict(c.execute('SELECT * FROM tasks WHERE id=?',(tid,)).fetchone()))
            self.assertEqual(lock,c.execute('SELECT payload FROM merchant_locks WHERE task_id=?',(tid,)).fetchone()[0])

    def test_alias_changes_require_new_preview_and_missing_profiles_visible(self):
        self.add(); p=self.preview()
        with self.store.connection(write=True) as c:
            c.execute("UPDATE settings SET value=? WHERE key='aliases'",(dump({'食品-宠物':'母婴'}),))
        with self.assertRaises(ImportFailure): self.service.generate({'token':p['token'],'confirmed':True})
        with self.store.connection(write=True) as c:
            c.execute("UPDATE settings SET value=? WHERE key='aliases'",(dump({'食品-宠物':'食品/宠物'}),))
        self.generate()
        with self.store.connection(write=True) as c: c.execute("UPDATE sources SET active=0 WHERE kind='accounts'")
        accounts=self.service.state()['accounts']; self.assertEqual(len(accounts),1); self.assertFalse(accounts[0]['verified']); self.assertEqual(accounts[0]['allowed'],[])

    def test_same_day_title_conflict_blocks_both_and_large_target(self):
        self.sources('products',[self.goods,{**self.goods,'row':3,'title':'另一标题'}])
        self.assertTrue(all(not r['selectable'] for r in self.service.catalog({'accountId':'fiction-a','range':'all'})['rows']))
        self.assertEqual(self.service.target({'accountId':'fiction-a','target':1000})['value'],1000)

    def test_unverified_accounts_and_invalid_products_remain_browsable(self):
        self.sources('accounts',[{**self.account,'verifiedAt':''}])
        self.sources('products',[{**self.goods,'catalogVerified':False}])
        page=self.service.catalog({'accountId':'fiction-a','range':'latest','date':'2026-10-15'})
        self.assertEqual(page['productDates'],['2026-10-15']); self.assertEqual(page['total'],1)
        self.assertFalse(page['rows'][0]['selectable']); self.assertTrue(page['rows'][0]['reason'])
        self.sources('accounts',[{**self.account,'verifiedAt':'','allowed':[]}])
        page=self.service.catalog({'accountId':'fiction-a','range':'latest','showOther':True})
        self.assertEqual(page['total'],1); self.assertFalse(page['rows'][0]['selectable'])

    def test_history_only_prior_dates_and_collaboration_fields_fallback(self):
        for day in ('2026-10-13','2026-10-15','2026-10-16'):
            self.add(day); self.generate_day(day)
        fields=['2026-10-12','','虚构账号','fiction-a','',self.goods['productId'],'','','','','','','','','','']
        self.sources('collaboration',[{'fields':fields,'sheet':'虚构协同','row':4,'issues':[]}])
        rows=self.service.catalog({'accountId':'fiction-a','range':'latest','date':'2026-10-15'})['rows']
        self.assertEqual(rows[0]['history'],['2026-10-12','2026-10-13'])

    def test_archived_identity_blocks_add_but_stays_out_of_state_and_export(self):
        self.add(); result=self.generate(); tid=result['taskIds'][0]
        with self.store.connection(write=True) as c:
            c.execute('UPDATE tasks SET archived=1 WHERE id=?',(tid,))
            before=dict(c.execute('SELECT * FROM tasks WHERE id=?',(tid,)).fetchone())
        repeated=self.add()
        self.assertTrue(repeated['reused']); self.assertEqual(repeated['id'],tid)
        self.assertEqual(self.service.state()['drafts'],[])
        self.assertEqual(self.service.state()['tasks'],[])
        catalog=self.service.catalog({'accountId':'fiction-a','range':'all','date':'2026-10-15'})
        self.assertTrue(catalog['rows'][0]['alreadyTask'])
        self.assertFalse(catalog['rows'][0]['selectable'])
        with self.assertRaises(ImportFailure) as empty_export:
            parse_import('tasks',self.service.export({'date':'2026-10-15'}),{})
        self.assertEqual(empty_export.exception.code,'no_rows')
        with self.store.connection() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM tasks').fetchone()[0],1)
            self.assertEqual(before,dict(c.execute('SELECT * FROM tasks WHERE id=?',(tid,)).fetchone()))

    def test_existing_draft_cannot_bypass_archived_identity_by_short_title(self):
        draft=self.add()
        # Model a saved selection draft surviving a separate task import:
        # generation supplies the task fixture, then restore the saved draft.
        result=self.generate(); tid=result['taskIds'][0]
        with self.store.connection(write=True) as c:
            c.execute('UPDATE tasks SET archived=1 WHERE id=?',(tid,))
            c.execute('INSERT INTO selection_drafts VALUES (?,1,?)',(draft['id'],dump(draft)))
        self.service.update({'draftId':draft['id'],'version':1,'changes':{'title':'另一个虚构短标题'}})
        preview=self.preview()
        self.assertIn('当日已有同账号商品任务。',preview['issues'])
        with self.assertRaises(ImportFailure):
            self.service.generate({'token':preview['token'],'confirmed':True})
        self.assertEqual(len(self.service.state()['drafts']),1)
        with self.store.connection() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM tasks').fetchone()[0],1)
            self.assertEqual(c.execute('SELECT archived FROM tasks WHERE id=?',(tid,)).fetchone()[0],1)


if __name__=='__main__': unittest.main()
