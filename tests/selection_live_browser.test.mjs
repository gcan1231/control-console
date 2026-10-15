import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {spawn,execFileSync} from 'node:child_process';
import {mkdtemp,mkdir,rm,readFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import path from 'node:path';
const require=createRequire(import.meta.url);
let pw;try{pw=require('playwright');}catch{pw=require(process.env.PLAYWRIGHT_MODULE);}
const python=process.env.PYTHON||'python',runtime=await mkdtemp(path.join(tmpdir(),'selection-live-fiction-'));
await mkdir('test-results',{recursive:true});
execFileSync(python,['-B','-c',`import sys,shutil;sys.path.insert(0,'tests');from test_selection import SelectionTests;f=SelectionTests();f.setUp();f.sources('accounts',[f.account,{**f.account,'accountId':'fiction-b','name':'缺UID虚构账号','uid':''},{**f.account,'accountId':'fiction-c','name':'F未核对虚构账号','verifiedAt':''}]);f.add('2026-10-14');f.generate_day('2026-10-14');shutil.copytree(f.store.root,sys.argv[1],dirs_exist_ok=True);f.doCleanups()`,runtime]);
let server,browser;const errors=[],external=[],checks=[];
try{
 server=spawn(python,['-B','-u','-c',`import sys,threading;from tools.local_server import create_server;s=create_server(0,sys.argv[1]);t=threading.Thread(target=s.serve_forever,daemon=True);t.start();print(s.server_port,flush=True);sys.stdin.readline();s.shutdown();s.server_close();t.join()`,runtime],{stdio:['pipe','pipe','pipe']});
 const port=await new Promise((resolve,reject)=>{let s='';server.stdout.on('data',c=>{s+=c;if(s.includes('\n'))resolve(Number(s.split('\n')[0]));});server.on('error',reject);server.on('exit',c=>reject(new Error('server exit '+c)));});
 const base='http://127.0.0.1:'+port;browser=await pw.chromium.launch({headless:true,channel:process.env.PLAYWRIGHT_CHANNEL||'chrome'});
 const page=await browser.newPage({viewport:{width:1440,height:1000}});
 page.on('pageerror',e=>errors.push(e.message));page.on('console',m=>{if(m.type()==='error')errors.push(m.text());});
 await page.route('**/*',async r=>{if(new URL(r.request().url()).origin!==base){external.push(r.request().url());await r.abort();}else await r.continue();});
 const idle=()=>page.waitForFunction(()=>document.querySelector('.sel-app')?.getAttribute('aria-busy')==='false');
 const action=async name=>{await page.locator(`[data-action="${name}"]`).click();await idle();};
 const state=()=>page.request.get(base+'/api/state').then(r=>r.json());
 await page.goto(base+'/selection.html');await idle();
 await page.locator('[data-control="date"]').fill('2026-10-15');await page.locator('[data-control="date"]').dispatchEvent('change');await idle();
 const old=(await state()).tasks;
 await page.locator('[data-add]').first().click();await idle();assert.equal(await page.locator('[data-edit]').count(),1);
 await page.locator('[data-edit]').click();await page.locator('[name="title"]').fill('中文输入虚构短标题');await page.locator('[name="portHint"]').fill('3');await page.locator('[data-form="draft"] button[type="submit"]').click();await idle();
 await page.reload();await idle();assert.match(await page.locator('.sel-draft-list').innerText(),/中文输入虚构短标题/);
 await action('presets');await page.locator('[name="name"]').fill('虚构目标');await page.locator('[data-target-account]').first().fill('4');await page.locator('[data-form="preset"] button[type="submit"]').click();await idle();assert.equal(await page.locator('[data-edit]').count(),1);
 await page.locator('[data-control="presetId"]').selectOption('');await idle();await page.locator('[data-control="presetId"]').selectOption({label:'虚构目标'});await idle();assert.equal(await page.locator('[data-edit]').count(),1);
 checks.push('加入/编辑中文标题与J/刷新恢复/切换预设保留草稿');
 const search=page.locator('[data-control="search"]');await search.focus();await search.dispatchEvent('compositionstart');await search.fill('虚构');await search.dispatchEvent('compositionend');await page.waitForTimeout(400);await idle();assert.equal(await search.evaluate(e=>document.activeElement===e),true);await search.fill('');await page.waitForTimeout(400);await idle();
 await action('preview');assert.equal(await page.locator('.sel-contract th').count(),16);assert.equal(await page.locator('.sel-contract tbody td').count(),16);await page.locator('[data-close]').first().click();await page.waitForFunction(()=>document.activeElement?.matches('[data-action="preview"]'));
 for(const width of [1440,1920,800,390]){await page.setViewportSize({width,height:1000});const size=await page.evaluate(()=>[document.documentElement.scrollWidth,document.documentElement.clientWidth]);assert.ok(size[0]<=size[1]+1,'page overflow '+width+': '+size);await page.screenshot({path:`test-results/selection-live-${width}.png`,fullPage:true});}
 checks.push('键盘焦点/中文组合输入/四尺寸无全页横溢出');
 await action('preview');await page.locator('[data-confirm]').check();await page.locator('[data-confirm-generate]').click();await idle();assert.equal(await page.locator('[data-edit]').count(),0);assert.deepEqual((await state()).tasks.filter(t=>old.some(o=>o.id===t.id)),old);
 const downloadPromise=page.waitForEvent('download');await action('export');const download=await downloadPromise;const bytes=await readFile(await download.path());assert.ok(bytes.length>100);const parsed=JSON.parse(execFileSync(python,['-B','-c',`import sys,json;from control_console.importers import parse_import;print(json.dumps(parse_import('tasks',sys.stdin.buffer.read(),{})['records'][0]['fields']))`],{input:bytes,encoding:'utf8'}));assert.equal(parsed.length,16);assert.equal(parsed[5],'9000000000000000001');assert.equal(parsed[9],'3');checks.push('16列预览确认/追加保留旧任务/下载长ID');
 await page.locator('[data-account="fiction-b"]').click();await idle();await page.locator('[data-add]').first().click();await idle();await action('preview');assert.equal(await page.locator('[data-confirm]').isDisabled(),true);await page.locator('[data-close]').first().click();
 await page.locator('[data-account="fiction-c"]').click();await idle();assert.equal(await page.locator('[data-add]:enabled').count(),0);checks.push('缺UID生成阻断/F未核对加入阻断');
 await page.locator('[data-account="fiction-a"]').click();await idle();await page.locator('[data-control="date"]').fill('2026-10-16');await page.locator('[data-control="date"]').dispatchEvent('change');await idle();await page.locator('[data-add]').first().click();await idle();await action('preview');assert.equal(await page.locator('[data-confirm]').isDisabled(),false);await page.locator('[data-close]').first().click();
 execFileSync(python,['-B','-c',`import sys,json,hashlib;from control_console.store import ConsoleStore,dump;from control_console.workbench import WorkbenchService;s=ConsoleStore(sys.argv[1]);w=WorkbenchService(s)\nwith s.connection(write=True) as c:\n r=json.loads(c.execute("SELECT payload FROM sources WHERE kind='products' AND active=1").fetchone()[0]);r[0]['groupO']='变化后的虚构O群';w._save_source(c,'products',{'filename':'changed-fiction.json','sha256':hashlib.sha256(dump(r).encode()).hexdigest(),'records':r,'options':{'complete':True},'issues':[]})`,runtime]);
 await action('refresh');await action('preview');assert.equal(await page.locator('[data-confirm]').isDisabled(),true);assert.match(await page.locator('#selection-dialog').innerText(),/来源|变化/);await page.locator('[data-close]').first().click();checks.push('草稿来源变化重新核对阻断');
 assert.deepEqual(errors,[]);assert.deepEqual(external,[]);console.log(JSON.stringify({ok:true,checks},null,2));
}catch(error){console.error(JSON.stringify({ok:false,checks,errors,external,error:error.stack},null,2));process.exitCode=1;}
finally{await browser?.close();if(server)await new Promise(resolve=>{server.once('exit',resolve);server.stdin.end('\n');});await rm(runtime,{recursive:true,force:true});}
