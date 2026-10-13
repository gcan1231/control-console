"""004 contract tests: fictional OOXML only; all runtime files are temporary."""
import io
import tempfile
import unittest
import zipfile
import json
import base64
import threading
import http.client
import hashlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from xml.sax.saxutils import escape
from unittest.mock import patch

HEADERS = ['日期','运营人','抖音名称','抖音账号','作品描述/商品短标题','商品ID','企微群名称','商品链接','账号合作码/uid','视频链接','店铺名称','剪辑软件日志','发文软件日志','千川塞素材软件日志','检查软件日志','企微同步软件日志']
PID = '9000000000000000001'

def column(n):
    result = ''
    while n:
        n, r = divmod(n - 1, 26)
        result = chr(65 + r) + result
    return result

def workbook(rows, name='Sheet1', extra=False, formatted=False, hyperlink=True):
    ns = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
    parts = []
    for i, row in enumerate(rows, 1):
        cells = []
        for j, value in enumerate(row, 1):
            if value is None or value == '':
                continue
            ref = column(j) + str(i)
            if isinstance(value, (int, float)):
                cells.append(f'<c r="{ref}" t="n"><v>{value}</v></c>')
            else:
                cells.append(f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{escape(value)}</t></is></c>')
        if i == 2 and extra:
            cells.append('<c r="Q2" t="inlineStr"><is><t>unexpected</t></is></c>')
        if i == 2 and formatted:
            cells.append('<c r="XFD2" s="0"/>')
        parts.append(f'<row r="{i}">' + ''.join(cells) + '</row>')
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml', '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>')
        z.writestr('_rels/.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        z.writestr('xl/workbook.xml', f'<workbook xmlns="{ns}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="{escape(name)}" sheetId="1" r:id="rId1"/></sheets></workbook>')
        z.writestr('xl/_rels/workbook.xml.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
        link = '<hyperlinks><hyperlink ref="H2" r:id="rId1"/></hyperlinks>' if hyperlink else ''
        z.writestr('xl/worksheets/sheet1.xml', f'<worksheet xmlns="{ns}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><dimension ref="A1:XFD{len(rows)}"/><sheetData>{"".join(parts)}</sheetData>{link}</worksheet>')
        if hyperlink:
            z.writestr('xl/worksheets/_rels/sheet1.xml.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" Target="https://example.invalid/item" TargetMode="External"/></Relationships>')
    return stream.getvalue()

def task(account='demo-a', name='演示账号', product=PID, j=7001, reported=False):
    return ['2026-10-13','虚构运营',name,account,'虚构商品',product,'虚构群','https://example.invalid/item','UID\nDEMO',j,'虚构店','','已发布' if reported else '','','','']

def rewrite_package(content, transforms=None, additions=None):
    out=io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(content)) as source,zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as dest:
        for name in source.namelist():
            data=source.read(name)
            if name in (transforms or {}):data=transforms[name](data.decode()).encode()
            dest.writestr(name,data)
        for name,data in (additions or {}).items():dest.writestr(name,data)
    return out.getvalue()

class LocalImportTests(unittest.TestCase):
    def setUp(self):
        from control_console.store import ConsoleStore
        self.Store = ConsoleStore
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = ConsoleStore(Path(self.temp.name))

    def commit(self, kind, content, options=None):
        result = self.store.commit_import(kind, kind+'.xlsx', content, options or {})
        self.assertTrue(result['ok'], result)
        return result

    def evidence(self, category='食品', allowed='食品', complete=True):
        self.commit('products', workbook([['商品ID','类别'],[PID,category]], hyperlink=False), {'complete':complete,'sheets':[{'sheet':'Sheet1','headerRow':1,'idColumn':'A','categoryColumn':'B','categoryFromSheet':False}]})
        self.commit('accounts', workbook([['运营人','抖音账号','抖音名称','UID','合作码','允许类别'],['虚构运营','demo-a','演示账号','DEMO','DEMO',allowed]], hyperlink=False), {'sheet':'Sheet1','headerRow':1,'complete':complete})

    def test_preview_does_not_persist_and_structure_rejections(self):
        from control_console.reader import inspect_workbook
        content = workbook([HEADERS,task()], formatted=True)
        inspected = inspect_workbook(content)
        self.assertEqual(inspected['sheets'][0]['name'],'Sheet1')
        self.assertEqual([h['label'] for h in inspected['sheets'][0]['headers'][:16]],HEADERS)
        result = self.store.preview_import('tasks','fiction.xlsx',content,{})
        self.assertTrue(result['ok']); self.assertEqual(self.store.state()['tasks'], [])
        self.commit('tasks',content); self.assertEqual(len(self.store.state()['tasks']),1)
        for bad in [workbook([HEADERS[:-1],task()]),workbook([HEADERS,task()],extra=True),workbook([HEADERS,task()],name='Other')]:
            before = self.store.state()
            try:
                outcome = self.store.commit_import('tasks','bad.xlsx',bad,{})
            except Exception:
                pass
            else:
                self.assertFalse(outcome.get('ok'), outcome)
            self.assertEqual(self.store.state(),before)

    def test_metadata_long_ids_empty_rows_and_source_reports(self):
        rows=[HEADERS,task(),[],task('demo-b','另一个账号',j=''),['','','','','残缺行'],task(product=9000000000000000001,reported=True)]
        self.commit('tasks',workbook(rows)); ts=self.store.state()['tasks']
        self.assertEqual(len(ts),4);self.assertEqual([t['sourceRow'] for t in ts],[2,4,5,6])
        self.assertEqual(ts[0]['fields'][5],PID);self.assertEqual(ts[0]['cellMeta'][9]['value'],7001)
        self.assertEqual(ts[0]['cellMeta'][7]['hyperlink'],'https://example.invalid/item')
        self.assertEqual(ts[1]['fields'][9],'');self.assertFalse(ts[2]['validation']['ok']);self.assertFalse(ts[3]['validation']['ok'])
        self.assertEqual(ts[3]['stage'],'reported');self.assertEqual(ts[3]['sourceReports']['m'],'已发布')
        self.assertNotEqual(ts[0]['id'],ts[1]['id']);self.assertEqual(ts[0]['fields'][5],ts[1]['fields'][5])

    def test_idempotence_notes_restart_and_isolated_bytes(self):
        content=workbook([HEADERS,task()]);r=self.commit('tasks',content);t=self.store.state()['tasks'][0]
        self.store.review_task(t['id'],'独立备注');again=self.commit('tasks',content)
        self.assertTrue(again['reused']);self.assertEqual(r['batchId'],again['batchId'])
        reloaded=self.Store(Path(self.temp.name)).state();self.assertEqual(reloaded['tasks'][0]['id'],t['id']);self.assertEqual(reloaded['tasks'][0]['note'],'独立备注')
        source=Path(self.temp.name)/'sources'/(hashlib.sha256(content).hexdigest()+'.xlsx')
        self.assertEqual(source.read_bytes(),content)
        copies=reloaded['batches'][0]['workCopies'];self.assertEqual(set(copies),{'material','editing','publishing'})
        paths=[Path(v) for v in copies.values()];self.assertEqual(len(set(paths)),3)
        for p in paths:self.assertEqual(p.read_bytes(),content)
        paths[0].write_bytes(b'isolated fixture change')
        for p in paths[1:]:self.assertEqual(p.read_bytes(),content)
        self.commit('tasks',content);self.assertEqual(paths[0].read_bytes(),b'isolated fixture change')

    def test_duplicate_review_cannot_bypass_category(self):
        self.commit('tasks',workbook([HEADERS,task(),task()]));ts=self.store.state()['tasks'];self.assertEqual(len(ts),2)
        for t in ts:self.assertFalse(t['validation']['ok'])
        self.evidence()
        for t in self.store.state()['tasks']:self.assertFalse(t['validation']['ok'])
        for t in ts:self.store.review_task(t['id'],'已核对',duplicate_confirmed=True)
        self.assertTrue(all(t['validation']['ok'] for t in self.store.state()['tasks']))
        self.evidence(category='母婴')
        self.assertFalse(any(t['validation']['ok'] for t in self.store.state()['tasks']))

    def test_completeness_missing_conflicting_and_alias_evidence(self):
        self.commit('tasks',workbook([HEADERS,task()]));self.assertFalse(self.store.state()['tasks'][0]['validation']['ok'])
        self.evidence(complete=False);self.assertFalse(self.store.state()['tasks'][0]['validation']['ok'])
        self.evidence(category='食品来源',allowed='食品');self.assertFalse(self.store.state()['tasks'][0]['validation']['ok'])
        self.store.set_aliases({'食品来源':'食品'});self.assertTrue(self.store.state()['tasks'][0]['validation']['ok'])
        self.store.set_aliases({});self.assertFalse(self.store.state()['tasks'][0]['validation']['ok'])
        opts={'complete':True,'sheets':[{'sheet':'Sheet1','headerRow':1,'idColumn':'A','categoryColumn':'B','categoryFromSheet':False}]}
        self.commit('products',workbook([['商品ID','类别'],[PID,'食品'],[PID,'母婴']],hyperlink=False),opts)
        self.assertFalse(self.store.state()['tasks'][0]['validation']['ok'])

    def test_concurrent_same_import(self):
        content=workbook([HEADERS,task()])
        def run(_):return self.Store(Path(self.temp.name)).commit_import('tasks','same.xlsx',content,{})
        with ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(run,range(4)))
        self.assertTrue(all(r['ok'] for r in results));self.assertEqual(len({r['batchId'] for r in results}),1);self.assertEqual(len(self.store.state()['tasks']),1)

    def test_missing_product_account_name_and_account_conflict(self):
        self.commit('tasks',workbook([HEADERS,task()]));self.evidence()
        opts={'complete':True,'sheets':[{'sheet':'Sheet1','headerRow':1,'idColumn':'A','categoryColumn':'B','categoryFromSheet':False}]}
        self.commit('products',workbook([['商品ID','类别'],['other','食品']],hyperlink=False),opts)
        self.assertFalse(self.store.state()['tasks'][0]['validation']['ok'])
        self.evidence()
        account_opts={'sheet':'Sheet1','headerRow':1,'complete':True}
        hdr=['运营人','抖音账号','抖音名称','UID','合作码','允许类别']
        self.commit('accounts',workbook([hdr,['虚构运营','demo-a','错名','D','D','食品']],hyperlink=False),account_opts)
        self.assertFalse(self.store.state()['tasks'][0]['validation']['ok'])
        self.commit('accounts',workbook([hdr,['虚构运营','demo-a','演示账号','D','D','食品'],['虚构运营','demo-a','演示账号','D','D','母婴']],hyperlink=False),account_opts)
        self.assertFalse(self.store.state()['tasks'][0]['validation']['ok'])

    def test_category_sheet_requires_real_category_name(self):
        from control_console.importers import ImportFailure
        opts={'complete':True,'sheets':[{'sheet':'Sheet1','headerRow':1,'idColumn':'A','categoryColumn':'','categoryFromSheet':True}]}
        with self.assertRaises(ImportFailure):self.commit('products',workbook([['商品ID'],[PID]],hyperlink=False),opts)
        self.commit('tasks',workbook([HEADERS,task()]));self.evidence()
        opts['sheets'][0]['sheet']='食品'
        self.commit('products',workbook([['商品ID'],[PID]],name='食品',hyperlink=False),opts)
        self.assertTrue(self.store.state()['tasks'][0]['validation']['ok'])

    def test_shared_rich_text_excludes_phonetic_and_negative_index_rejected(self):
        from control_console.importers import ImportFailure
        base=workbook([HEADERS,task()])
        shared='<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><si><r><t>虚构</t></r><r><t>商品</t></r><rPh sb="0" eb="2"><t>PHONETIC</t></rPh></si></sst>'
        def transform(xml):return xml.replace('<c r="E2" t="inlineStr"><is><t xml:space="preserve">虚构商品</t></is></c>','<c r="E2" t="s"><v>0</v></c>')
        content=rewrite_package(base,{'xl/worksheets/sheet1.xml':transform},{'xl/sharedStrings.xml':shared})
        self.commit('tasks',content);self.assertEqual(self.store.state()['tasks'][0]['fields'][4],'虚构商品')
        bad=rewrite_package(content,{'xl/worksheets/sheet1.xml':lambda x:x.replace('<v>0</v>','<v>-1</v>')})
        with self.assertRaises(ImportFailure):self.store.preview_import('tasks','bad.xlsx',bad,{})

    def test_two_date_epochs_custom_format_and_formula_cache_not_trusted(self):
        styles='<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><numFmts count="1"><numFmt numFmtId="164" formatCode="m/d"/></numFmts><cellXfs count="2"><xf numFmtId="0"/><xf numFmtId="164"/></cellXfs></styleSheet>'
        for epoch,expected in [(False,'1900-01-01'),(True,'1904-01-02')]:
            base=workbook([HEADERS,task()])
            transforms={'xl/worksheets/sheet1.xml':lambda x:x.replace('<c r="A2" t="inlineStr"><is><t xml:space="preserve">2026-10-13</t></is></c>','<c r="A2" s="1"><v>1</v></c>').replace('<c r="F2" t="inlineStr"><is><t xml:space="preserve">'+PID+'</t></is></c>','<c r="F2"><f>1+1</f><v>'+PID+'</v></c>')}
            if epoch:transforms['xl/workbook.xml']=lambda x:x.replace('<sheets>','<workbookPr date1904="1"/><sheets>')
            self.commit('tasks',rewrite_package(base,transforms,{'xl/styles.xml':styles}))
            current=[t for t in self.store.state()['tasks'] if t['fields'][0]==expected]
            self.assertEqual(len(current),1);t=current[0];self.assertEqual(t['fields'][5],'=1+1');self.assertFalse(t['validation']['ok'])
            self.assertEqual(t['cellMeta'][0]['raw'],'1');self.assertEqual(t['cellMeta'][0]['style'],'1');self.assertEqual(t['cellMeta'][5]['type'],'formula')

    def test_cross_batch_same_day_identity_requires_review_keeps_uuid(self):
        first=task();second=task();second[4]='不同文件的虚构商品标题'
        a=self.commit('tasks',workbook([HEADERS,first]));self.evidence()
        original=self.store.state()['tasks'][0]['id'];self.assertTrue(self.store.state()['tasks'][0]['validation']['ok'])
        b=self.commit('tasks',workbook([HEADERS,second]));self.assertNotEqual(a['batchId'],b['batchId'])
        tasks=self.store.state()['tasks'];self.assertEqual(len(tasks),2);self.assertEqual(len({t['id'] for t in tasks}),2);self.assertIn(original,[t['id'] for t in tasks])
        self.assertTrue(all(not t['validation']['ok'] for t in tasks));self.assertTrue(all(any(i['code']=='duplicate' for i in t['validation']['issues']) for t in tasks))
        for t in tasks:self.store.review_task(t['id'],'跨批次已核对',duplicate_confirmed=True)
        self.assertTrue(all(t['validation']['ok'] for t in self.store.state()['tasks']))

    def test_failed_fsync_never_publishes_partial_snapshot_and_retry_succeeds(self):
        content=workbook([HEADERS,task()]);before=self.store.state()
        snapshot=Path(self.temp.name)/'sources'/(hashlib.sha256(content).hexdigest()+'.xlsx')
        with patch('control_console.store.os.fsync',side_effect=OSError('fictional disk failure')):
            with self.assertRaises(OSError):self.store.commit_import('tasks','atomic.xlsx',content,{})
        self.assertEqual(self.store.state(),before);self.assertFalse(snapshot.exists())
        self.assertEqual(list((Path(self.temp.name)/'sources').glob('*')),[])
        self.commit('tasks',content);self.assertEqual(len(self.store.state()['tasks']),1);self.assertEqual(len(self.store.state()['sources']),1)
        self.assertEqual(snapshot.read_bytes(),content)

    def test_different_dates_independent_and_unstyled_numeric_date_epoch(self):
        first=task();second=task();second[0]='2026-10-14'
        self.commit('tasks',workbook([HEADERS,first]));self.commit('tasks',workbook([HEADERS,second]));self.evidence()
        tasks=self.store.state()['tasks'];self.assertEqual(len(tasks),2);self.assertTrue(all(t['validation']['ok'] for t in tasks))
        numeric=task();numeric[0]=1
        for epoch,expected in [(False,'1900-01-01'),(True,'1904-01-02')]:
            content=workbook([HEADERS,numeric])
            if epoch:content=rewrite_package(content,{'xl/workbook.xml':lambda x:x.replace('<sheets>','<workbookPr date1904="1"/><sheets>')})
            self.commit('tasks',content)
            t=next(t for t in self.store.state()['tasks'] if t['fields'][0]==expected)
            self.assertTrue(t['validation']['ok']);self.assertEqual(t['cellMeta'][0]['raw'],'1');self.assertEqual(t['cellMeta'][0]['xlsxType'],'n');self.assertIsNone(t['cellMeta'][0]['style'])

class LocalHTTPTests(unittest.TestCase):
    def setUp(self):
        from tools.local_server import create_server
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.server=create_server(0,Path(self.temp.name))
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.addCleanup(self.stop)
        self.port=self.server.server_port
        self.token=self.request('GET','/api/health')[1]['token']

    def stop(self):
        self.server.shutdown();self.server.server_close();self.thread.join()

    def request(self,method,path,payload=None,headers=None):
        conn=http.client.HTTPConnection('127.0.0.1',self.port,timeout=5)
        data=json.dumps(payload).encode() if payload is not None else None
        conn.request(method,path,body=data,headers={'Content-Type':'application/json',**(headers or {})})
        r=conn.getresponse();body=r.read();status=r.status;conn.close()
        try:body=json.loads(body)
        except (ValueError,UnicodeError):pass
        return status,body

    def test_host_origin_token_and_atomic_error(self):
        payload={'kind':'tasks','filename':'fixture.xlsx','content':base64.b64encode(workbook([HEADERS,task()])).decode(),'options':{}}
        before=self.request('GET','/api/state')[1]
        for headers in [{},{'X-Console-Token':'wrong'},{'X-Console-Token':self.token,'Origin':'https://example.invalid'},{'X-Console-Token':self.token,'Host':'evil.invalid'}]:
            status,_=self.request('POST','/api/import/commit',payload,headers);self.assertEqual(status,403)
            self.assertEqual(self.request('GET','/api/state')[1],before)
        good={'X-Console-Token':self.token,'Origin':f'http://127.0.0.1:{self.port}'}
        self.assertEqual(self.request('POST','/api/import/preview',payload,good)[0],200)
        self.assertEqual(self.request('GET','/api/state')[1],before)
        bad=dict(payload,content=base64.b64encode(b'bad zip').decode())
        self.assertEqual(self.request('POST','/api/import/commit',bad,good)[0],422)
        self.assertEqual(self.request('GET','/api/state')[1],before)
        self.assertEqual(self.request('POST','/api/tasks/review',{'taskId':[],'note':'invalid'},good)[0],422)
        self.assertEqual(self.request('GET','/api/state')[1],before)
        self.assertEqual(self.request('POST','/api/import/commit',payload,good)[0],200)
        self.assertEqual(len(self.request('GET','/api/state')[1]['tasks']),1)
        self.assertEqual(self.request('GET','/api/health',headers={'Host':'evil.invalid'})[0],403)

    def test_runtime_and_repository_never_static(self):
        for route in ['/runtime/','/runtime/state.sqlite3','/.git/config','/../.git/config','/%2e%2e/.git/config','/%252e%252e/.git/config','/control_console/store.py']:
            self.assertEqual(self.request('GET',route)[0],404,route)
        self.assertEqual(self.request('GET','/')[0],200)

if __name__ == '__main__':unittest.main()
