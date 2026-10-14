"""006A service contracts; generated fictional XLSX and temporary runtime only."""
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from test_local_import import HEADERS, PID, task, workbook, column
from test_references import S, R, cell
from control_console.store import ConsoleStore
from control_console.workbench import WorkbenchService
from control_console.importers import ImportFailure


class WorkbenchTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.store=ConsoleStore(self.root/'runtime');self.service=WorkbenchService(self.store)

    def tasks(self, rows):
        content=workbook([HEADERS,*rows]);preview=self.service.preview_tasks('fiction.xlsx',content)
        return self.service.commit_tasks('fiction.xlsx',content,{},preview['revisionToken'])

    def state(self):return self.service.enrich(self.store.state())

    def reference(self,kind,rows,extras=None,name=None):
        path=self.root/(kind+'.xlsx')
        with zipfile.ZipFile(path,'w') as z:
            z.writestr('xl/workbook.xml',f'<workbook xmlns="{S}" xmlns:r="{R}"><sheets><sheet name="{name or ("协同" if kind=="collaboration" else "食品-宠物")}" r:id="s1"/></sheets></workbook>')
            z.writestr('xl/_rels/workbook.xml.rels','<Relationships><Relationship Id="s1" Target="worksheets/sheet1.xml"/></Relationships>')
            drawing='<drawing r:id="d"/>' if extras and 'xl/media/image.png' in extras else ''
            z.writestr('xl/worksheets/sheet1.xml',f'<worksheet xmlns="{S}" xmlns:r="{R}"><sheetData>{rows}</sheetData>{drawing}</worksheet>')
            for key,value in (extras or {}).items():z.writestr(key,value)
        return path

    def collaboration(self,fields,marker=None,empty=False):
        header='<row r="3">'+''.join(cell(column(i)+'3',h) for i,h in enumerate(HEADERS,1))+'</row>'
        values=[]
        for i,value in enumerate(fields,1):
            ref=column(i)+'4'
            if column(i)==marker:
                values.append(f'<c r="{ref}" s="1"/>' if empty else f'<c r="{ref}" t="inlineStr"><is><r><rPr><color rgb="FF9A38D7"/></rPr><t>紫色已发</t></r></is></c>')
            elif value:values.append(cell(ref,str(value)))
        extras={'xl/styles.xml':f'<styleSheet xmlns="{S}"><fonts><font/><font><color rgb="FF800080"/></font></fonts><cellXfs><xf fontId="0"/><xf fontId="1"/></cellXfs></styleSheet>'}
        path=self.reference('collaboration',header+'<row r="4">'+''.join(values)+'</row>',extras)
        return self.service.register('collaboration',str(path))

    def products(self,title='虚构商品',image=None):
        extras={}
        if image is not None:
            extras={'xl/worksheets/_rels/sheet1.xml.rels':'<Relationships><Relationship Id="d" Target="../drawings/drawing1.xml"/></Relationships>',
                    'xl/drawings/drawing1.xml':f'<wsDr xmlns:r="{R}"><twoCellAnchor><from><col>5</col><row>1</row></from><pic><blip r:embed="i"/></pic></twoCellAnchor></wsDr>',
                    'xl/drawings/_rels/drawing1.xml.rels':'<Relationships><Relationship Id="i" Target="../media/image.png"/></Relationships>',
                    'xl/media/image.png':b'\x89PNG\r\n\x1a\n'+image}
        path=self.reference('products','<row r="2">'+cell('G2',PID)+cell('E2',title)+'</row>',extras)
        with zipfile.ZipFile(path) as z:parts={n:z.read(n) for n in z.namelist()}
        parts['xl/workbook.xml']=parts['xl/workbook.xml'].replace(b'</sheets>', '<sheet name="百货个护家清" r:id="s2"/><sheet name="母婴" r:id="s3"/></sheets>'.encode())
        parts['xl/_rels/workbook.xml.rels']=parts['xl/_rels/workbook.xml.rels'].replace(b'</Relationships>',b'<Relationship Id="s2" Target="worksheets/sheet2.xml"/><Relationship Id="s3" Target="worksheets/sheet3.xml"/></Relationships>')
        for i in (2,3):parts[f'xl/worksheets/sheet{i}.xml']=f'<worksheet xmlns="{S}"><sheetData/></worksheet>'.encode()
        with zipfile.ZipFile(path,'w') as z:
            for n,data in parts.items():z.writestr(n,data)
        return self.service.register('products',str(path))

    def profile(self,allowed=None):
        return self.service.account_profiles([{'accountId':'demo-a','name':'演示账号','allowed':allowed or ['食品/宠物'],'evidence':'虚构F列人工核对'}],True)

    def merchant_lock(self,marker):
        self.tasks([task()]);tid=self.state()['tasks'][0]['id'];self.collaboration(task(),marker)
        current=self.state()['tasks'][0]['collaboration'];self.assertTrue(current['locked']);self.assertTrue(current['accountSent' if marker=='I' else 'productSent'])
        altered=task();altered[4]='修订标题';content=workbook([HEADERS,altered]);before=self.store.state()['tasks'][0]['fields']
        preview=self.service.preview_tasks('change.xlsx',content);self.assertEqual(preview['conflicts'],1)
        with self.assertRaises(ImportFailure):self.service.commit_tasks('change.xlsx',content,{},preview['revisionToken'])
        self.assertEqual(self.store.state()['tasks'][0]['fields'],before)
        self.collaboration(task());latest=self.state()['tasks'][0];self.assertEqual(latest['id'],tid);self.assertTrue(latest['collaboration']['locked']);self.assertTrue(latest['collaboration']['markerChanged'])


    def test_merchant_i_locks_a_to_i_without_downgrade(self):self.merchant_lock('I')
    def test_merchant_h_locks_a_to_i_without_downgrade(self):self.merchant_lock('H')

    def test_empty_purple_style_does_not_lock(self):
        self.tasks([task()]);self.collaboration(task(),'H',empty=True)
        value=self.state()['tasks'][0]['collaboration'];self.assertFalse(value['locked']);self.assertFalse(value['productSent'])

    def test_update_archive_restore_keep_identity_and_versions(self):
        other=task(product='9000000000000000002');self.tasks([task(),other]);original={t['fields'][5]:t['id'] for t in self.state()['tasks']}
        revised=task();revised[4]='虚构新版';self.tasks([revised]);state=self.state();by_product={t['fields'][5]:t for t in state['tasks']}
        self.assertEqual(by_product[PID]['id'],original[PID]);self.assertEqual(by_product[PID]['fields'][4],'虚构新版');self.assertTrue(by_product[other[5]]['archived'])
        self.assertEqual(state['counts']['archived'],1);self.tasks([revised,other]);self.assertFalse(next(t for t in self.state()['tasks'] if t['id']==original[other[5]])['archived'])
        with self.store.connection() as conn:self.assertGreaterEqual(conn.execute('SELECT COUNT(*) FROM task_versions').fetchone()[0],5)

    def test_inserted_first_row_reorders_unchanged_tasks_preserving_ids(self):
        first=task();second=task(account='demo-b',name='虚构账号乙',product='9000000000000000002')
        self.tasks([first,second]);before={t['accountId']:t for t in self.state()['tasks']}
        added=task(account='demo-c',name='虚构账号丙',product='9000000000000000003')
        self.tasks([added,first,second]);latest=self.state()['tasks']
        self.assertEqual([t['accountId'] for t in latest],['demo-c','demo-a','demo-b'])
        self.assertEqual([t['sourceRow'] for t in latest],[2,3,4])
        with self.store.connection() as conn:
            current_source=conn.execute("SELECT id FROM sources WHERE kind='tasks' AND active=1").fetchone()[0]
        for number,current in enumerate(latest,2):
            self.assertEqual(current['currentSource']['source_id'],current_source)
            self.assertEqual(current['currentSource']['row_index'],number)
            if current['accountId'] in before:
                old=before[current['accountId']]
                self.assertEqual(current['id'],old['id'])
                self.assertEqual(current['fields'],old['fields'])
                self.assertNotEqual(current['currentSource']['source_id'],old['currentSource']['source_id'])

    def test_same_day_other_operator_not_archived(self):
        other=task(product='9000000000000000002');other[1]='另一虚构运营'
        self.tasks([task(),other]);other_id=next(t['id'] for t in self.state()['tasks'] if t['fields'][1]=='另一虚构运营')
        revised=task();revised[4]='本运营修订';self.tasks([revised])
        protected=next(t for t in self.state()['tasks'] if t['id']==other_id)
        self.assertFalse(protected['archived']);self.assertEqual(protected['fields'][1],'另一虚构运营')

    def test_stale_revision_rejected(self):
        self.tasks([task()]);changed=task();changed[4]='虚构新版';content=workbook([HEADERS,changed]);preview=self.service.preview_tasks('change.xlsx',content)
        other=task();other[4]='其他修订';self.tasks([other])
        with self.assertRaises(ImportFailure) as caught:self.service.commit_tasks('change.xlsx',content,{},preview['revisionToken'])
        self.assertEqual(caught.exception.code,'revision_stale')

    def test_manual_accounts_and_frozen_sources_aliases(self):
        self.tasks([task()]);first=self.products();self.assertFalse(self.state()['tasks'][0]['validation']['ok'])
        with self.assertRaises(ImportFailure):self.service.account_profiles([{'accountId':'demo-a','name':'演示账号','allowed':['食品/宠物'],'evidence':'人工F列'}],False)
        self.profile();current=self.state()['tasks'][0];self.assertTrue(current['validation']['ok']);tid=current['id']
        with self.store.connection(write=True) as conn:self.store.freeze_evidence(conn,[tid])
        self.products('第二版');self.profile(['母婴']);self.store.set_aliases({'食品-宠物':'不匹配'})
        frozen=self.state()['tasks'][0];self.assertTrue(frozen['validation']['ok']);self.assertEqual(frozen['frozenEvidence']['products'],first['sourceId']);self.assertEqual(frozen['product']['title'],'虚构商品')

    def test_failed_refresh_keeps_valid_snapshot(self):
        self.tasks([task()]);first=self.products();self.profile();path=self.root/'products.xlsx';path.unlink()
        result=self.service.refresh_sources(True);self.assertTrue(any(r.get('error') for r in result['sources']));self.assertEqual(self.state()['tasks'][0]['product']['title'],'虚构商品')
        with self.store.connection() as conn:self.assertEqual(conn.execute("SELECT id FROM sources WHERE kind='products' AND active=1").fetchone()[0],first['sourceId'])

    def test_image_revision_never_reuses_other_version(self):
        first=self.products(image=b'old');member='xl/media/image.png';old=self.service.image(first['sourceId'],member)
        second=self.products(image=b'new-image');self.assertNotEqual(first['sourceId'],second['sourceId'])
        self.assertEqual(self.service.image(first['sourceId'],member),old);self.assertEqual(self.service.image(second['sourceId'],member)[0],b'\x89PNG\r\n\x1a\nnew-image')

    def test_old_uncached_image_cannot_read_new_version(self):
        first=self.products(image=b'uncached-old');second=self.products(image=b'new-version')
        with self.assertRaises(ImportFailure) as caught:self.service.image(first['sourceId'],'xl/media/image.png')
        self.assertEqual(caught.exception.code,'image_snapshot')
        self.assertEqual(self.service.image(second['sourceId'],'xl/media/image.png')[0],b'\x89PNG\r\n\x1a\nnew-version')

    def test_duplicate_accounts_normalized_and_atomic(self):
        profiles=[{'accountId':account,'name':'演示账号','allowed':['食品/宠物'],'evidence':'虚构F列'} for account in ('demo-a',' demo-a ')]
        with self.assertRaises(ImportFailure):self.service.account_profiles(profiles,True)
        self.assertEqual(self.state()['accountProfiles'],[])

    def test_merchant_source_conflict_blocks_local_task(self):
        self.tasks([task()]);self.products();self.profile();self.assertTrue(self.state()['tasks'][0]['validation']['ok'])
        changed=task();changed[4]='商家已发不同标题';self.collaboration(changed,'I')
        current=self.state()['tasks'][0];self.assertTrue(current['collaboration']['locked']);self.assertFalse(current['validation']['ok'])
        self.assertEqual(current['fields'][4],'虚构商品')

    def test_handed_task_cannot_update_or_remove(self):
        other=task(product='9000000000000000002');self.tasks([task(),other]);self.products();self.profile();tid=next(t['id'] for t in self.state()['tasks'] if t['fields'][5]==PID)
        with self.store.connection(write=True) as conn:self.store.freeze_evidence(conn,[tid])
        changed=task();changed[4]='交接后修改';content=workbook([HEADERS,changed,other]);preview=self.service.preview_tasks('fiction.xlsx',content)
        self.assertTrue(any(c['action']=='conflict' and c.get('taskId')==tid for c in preview['changes']))
        content=workbook([HEADERS,other]);preview=self.service.preview_tasks('fiction.xlsx',content)
        self.assertTrue(any(c['action']=='conflict' and c.get('taskId')==tid for c in preview['changes']))
        with self.assertRaises(ImportFailure):self.service.commit_tasks('fiction.xlsx',content,{},preview['revisionToken'])
        self.assertFalse(next(t for t in self.state()['tasks'] if t['id']==tid)['archived'])

    def test_source_change_invalidates_revision_token(self):
        self.tasks([task()]);self.products();content=workbook([HEADERS,task()]);preview=self.service.preview_tasks('same.xlsx',content)
        self.products('新来源版本')
        with self.assertRaises(ImportFailure) as caught:self.service.commit_tasks('same.xlsx',content,{},preview['revisionToken'])
        self.assertEqual(caught.exception.code,'revision_stale')


class WorkbenchHTTPTests(unittest.TestCase):
    def setUp(self):
        import test_local_import as fixtures
        self.request=fixtures.LocalHTTPTests.request.__get__(self)
        self.stop=fixtures.LocalHTTPTests.stop.__get__(self)
        fixtures.LocalHTTPTests.setUp(self)
        self.good={'X-Console-Token':self.token,'Origin':f'http://127.0.0.1:{self.port}'}

    def test_new_mutations_require_host_origin_token(self):
        routes={'/api/reference/register':{'kind':'products','path':'relative.xlsx'},
                '/api/reference/refresh':{},'/api/accounts/verify':{'profiles':[],'confirmed':True},
                '/api/tasks/import/preview':{},'/api/tasks/import/commit':{},
                '/api/editing/verify-batch':{'taskIds':['fiction'],'kind':'normal','confirmed':True}}
        before=self.request('GET','/api/state')[1]
        for route,payload in routes.items():
            for headers in ({},{'X-Console-Token':'wrong'},dict(self.good,Origin='https://example.invalid'),dict(self.good,Host='evil.invalid')):
                with self.subTest(route=route,headers=headers):self.assertEqual(self.request('POST',route,None,headers)[0],403)
        self.assertEqual(self.request('GET','/api/state')[1],before)

    def test_register_rejects_nonlocal_paths_and_unlisted_kinds(self):
        before=self.request('GET','/api/state')[1]
        for kind,path in [('accounts','relative.xlsx'),('products','relative.xlsx'),('products','https://example.invalid/fiction.xlsx'),('products',r'\\server\fiction.xlsx'),('collaboration',42)]:
            with self.subTest(kind=kind,path=path):self.assertEqual(self.request('POST','/api/reference/register',{'kind':kind,'path':path},self.good)[0],422)
        self.assertEqual(self.request('GET','/api/state')[1],before)
        for url in ('/api/products/image','/api/products/image?sourceId=fiction&member=../../secrets','/api/products/image?sourceId=fiction&member=xl/media/fiction.png'):
            self.assertEqual(self.request('GET',url)[0],404)

    def test_reference_register_and_refresh_read_local_copy_only(self):
        path=Path(self.temp.name)/'fiction-products.xlsx'
        content=workbook([[],['','','','','虚构商品','',PID]],name='食品-宠物',hyperlink=False);path.write_bytes(content)
        status,result=self.request('POST','/api/reference/register',{'kind':'products','path':str(path)},self.good)
        self.assertEqual(status,200);self.assertEqual(result['rowCount'],1);self.assertEqual(path.read_bytes(),content)
        self.assertEqual(self.request('POST','/api/reference/refresh',{},self.good)[0],200)
        state=self.request('GET','/api/state')[1];self.assertEqual(state['referencePaths'][0]['source_id'],result['sourceId']);self.assertEqual(path.read_bytes(),content)

    def test_tasks_http_preview_token_and_account_confirmation(self):
        import base64
        body={'filename':'fiction.xlsx','content':base64.b64encode(workbook([HEADERS,task()])).decode(),'options':{}}
        status,preview=self.request('POST','/api/tasks/import/preview',body,self.good);self.assertEqual(status,200)
        self.assertEqual(self.request('GET','/api/state')[1]['tasks'],[])
        self.assertEqual(self.request('POST','/api/tasks/import/commit',dict(body,revisionToken='stale'),self.good)[0],422)
        self.assertEqual(self.request('POST','/api/tasks/import/commit',dict(body,revisionToken=preview['revisionToken']),self.good)[0],200)
        profile={'accountId':'demo-a','name':'演示账号','allowed':['食品/宠物'],'evidence':'虚构F列人工核对'}
        self.assertEqual(self.request('POST','/api/accounts/verify',{'profiles':[profile]},self.good)[0],422)
        self.assertEqual(self.request('POST','/api/accounts/verify',{'profiles':[profile],'confirmed':True},self.good)[0],200)
        self.assertEqual(len(self.request('GET','/api/state')[1]['accountProfiles']),1)

    def test_batch_confirmation_strict_and_upload_limits_unchanged(self):
        import http.client
        from control_console.importers import MAX_BYTES
        self.assertEqual(MAX_BYTES,20*1024*1024)
        for confirmed in (None,False,1,'true'):
            status,body=self.request('POST','/api/editing/verify-batch',{'taskIds':['fiction'],'kind':'normal','confirmed':confirmed},self.good)
            self.assertEqual(status,422);self.assertEqual(body['error']['code'],'editing_verify')
        status,body=self.request('POST','/api/editing/verify-batch',{'taskIds':['fiction'],'kind':'normal','confirmed':True},self.good)
        self.assertEqual(status,422);self.assertEqual(body['error']['code'],'editing_gate')
        # Header rejection exercises the HTTP cap without allocating or uploading 32MB.
        conn=http.client.HTTPConnection('127.0.0.1',self.port,timeout=5)
        conn.request('POST','/api/tasks/import/preview',body=b'',headers={**self.good,'Content-Type':'application/json','Content-Length':str(32*1024*1024+1)})
        response=conn.getresponse();self.assertEqual(response.status,413);response.read();conn.close()
        # Each XLSX route keeps the exact shared encoded-file guard.
        from unittest.mock import patch
        with patch('tools.local_server.MAX_BYTES',3):
            for route in ('/api/import/preview','/api/tasks/import/preview'):
                status,body=self.request('POST',route,{'kind':'tasks','filename':'fiction.xlsx','content':'A'*9},self.good)
                self.assertEqual(status,422);self.assertEqual(body['error']['code'],'file_size')

if __name__=='__main__':unittest.main()
