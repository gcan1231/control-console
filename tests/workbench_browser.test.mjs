// Full UI acceptance uses a disposable loopback server and generated fictional XLSX only.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {spawn,execFileSync} from 'node:child_process';
import {mkdtemp,mkdir,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import path from 'node:path';
const require=createRequire(import.meta.url);
let pw;try{pw=require('playwright');}catch{pw=require(process.env.PLAYWRIGHT_MODULE);}
const python=process.env.PYTHON||'python';
const runtime=await mkdtemp(path.join(tmpdir(),'console-test-006a-'));
const fixture=JSON.parse(execFileSync(python,['-c',`
import sys,json,base64,zipfile
from pathlib import Path
sys.path.insert(0,'tests')
from test_workbench import WorkbenchTests
from test_local_import import task,workbook,HEADERS,PID,column
from test_references import S,R,cell
from control_console.store import ConsoleStore
from control_console.workbench import WorkbenchService
t=WorkbenchTests();t.root=Path(sys.argv[1])/'fixtures';t.root.mkdir();t.store=ConsoleStore(Path(sys.argv[1]));t.service=WorkbenchService(t.store)
rows=[task(),task('demo-b','虚构第二账号'),task(product='9000000000000000002'),task()]
for row in rows[:3]:row[0]='2026-10-14'
rows[2][4]='虚构第二商品';rows[1][9]=''
t.tasks(rows)
t.products()
p=t.root/'products.xlsx'
with zipfile.ZipFile(p) as z:parts={n:z.read(n) for n in z.namelist()}
parts['xl/worksheets/sheet1.xml']=parts['xl/worksheets/sheet1.xml'].replace(b'</sheetData>',('<row r="3">'+cell('G3','9000000000000000002')+cell('E3','虚构第二商品')+'</row></sheetData>').encode())
with zipfile.ZipFile(p,'w') as z:
 for n,data in parts.items():z.writestr(n,data)
header='<row r="3">'+''.join(cell(column(i)+'3',h) for i,h in enumerate(HEADERS,1))+'</row>'
row=rows[0];values=[]
for i,value in enumerate(row,1):
 ref=column(i)+'4'
 if i==9:values.append(f'<c r="{ref}" t="inlineStr"><is><r><rPr><color rgb="FF9A38D7"/></rPr><t>{value}</t></r></is></c>')
 elif value:values.append(cell(ref,str(value)))
c=t.reference('collaboration',header+'<row r="4">'+''.join(values)+'</row>')
changed=[r[:] for r in rows];changed[0][4]='虚构锁定后修订'
print(json.dumps({'products':str(p),'collaboration':str(c),'changed':base64.b64encode(workbook([HEADERS,*changed])).decode()}))
`,runtime],{encoding:'utf8'}));
let server,browser,base;
const checks=[],errors=[],external=[],mutations=[];
async function start(){server=spawn(python,['-u','-c',`import sys,threading;from tools.local_server import create_server;s=create_server(0,sys.argv[1]);t=threading.Thread(target=s.serve_forever,daemon=True);t.start();print(s.server_port,flush=True);sys.stdin.readline();s.shutdown();s.server_close();t.join()`,runtime],{stdio:['pipe','pipe','pipe']});return new Promise((resolve,reject)=>{let buf='';server.stdout.on('data',c=>{buf+=c;if(buf.includes('\n'))resolve('http://127.0.0.1:'+Number(buf.split('\n')[0]));});server.on('error',reject);server.on('exit',code=>{if(!buf)reject(new Error('server exit '+code));});});}
try{
 await mkdir('test-results',{recursive:true});base=await start();browser=await pw.chromium.launch({headless:true,channel:process.env.PLAYWRIGHT_CHANNEL||'chrome'});const page=await browser.newPage({viewport:{width:1440,height:1000}});page.setDefaultTimeout(10000);
 page.on('pageerror',error=>errors.push(error.message));page.on('request',r=>{if(r.method()==='POST')mutations.push(new URL(r.url()).pathname);});await page.route('**/*',async r=>{const u=new URL(r.request().url());if(u.origin!==base){external.push(u.href);await r.abort();}else await r.continue();});
 const state=()=>page.request.get(base+'/api/state').then(r=>r.json());const nav=kind=>page.locator(`nav [data-page="${kind}"]`).click();
 await page.goto(base);await page.locator('.wb-group').first().waitFor();assert.equal(await page.locator('[data-wb-filter="date"]').inputValue(),'2026-10-14');assert.equal(await page.locator('.wb-group').count(),2);assert.equal(await page.locator('[data-wb-select]').count(),3);checks.push('最新日期默认、两账号三商品分组');
 const search=page.locator('[data-wb-filter="search"]');await search.focus();await search.pressSequentially('900000');assert.equal(await search.inputValue(),'900000');assert.equal(await search.evaluate(n=>n===document.activeElement),true);await search.fill('');await search.evaluate(n=>{n.dispatchEvent(new CompositionEvent('compositionstart',{bubbles:true}));n.value='虚构';n.dispatchEvent(new InputEvent('input',{bubbles:true,isComposing:true}));});assert.equal(await page.locator('[data-wb-select]').count(),3);await search.evaluate(n=>n.dispatchEvent(new CompositionEvent('compositionend',{bubbles:true,data:'虚构'})));assert.equal(await search.evaluate(n=>n===document.activeElement),true);await search.fill('');checks.push('连续搜索保焦点、中文composition不打断');
 await page.locator('[data-wb-full]').check();assert.equal(await page.locator('.wb-group').first().locator('th').count(),18);assert.match(await page.locator('.wb-group').first().innerText(),/J 视频链接（仅端口记忆提示）/);await page.locator('[data-wb-full]').uncheck();const detail=page.locator('[data-wb-detail]').first();const id=await detail.getAttribute('data-wb-detail');await detail.click();await page.locator('#drawer').waitFor({state:'visible'});await page.keyboard.press('Escape');await page.locator('#drawer').waitFor({state:'hidden'});assert.equal(await page.evaluate(id=>document.activeElement?.dataset.wbDetail===id,id),true);checks.push('完整16列/J提示、详情Escape恢复焦点');
 await page.locator('[data-wb-sources]').first().click();await page.locator('.wbs').waitFor();assert.equal(await page.locator('[data-wbs-account]').count(),2);assert.equal(await page.locator('[data-wbs-field="allowed"]').first().inputValue(),'');
 assert.equal(await page.locator('.wbs-manual-accounts').evaluate(n=>n.open),false);await page.locator('.wbs-manual-accounts > summary').click();
 for(const kind of ['products','collaboration']){const form=page.locator(`[data-wbs-path-form="${kind}"]`);await form.locator('[data-wbs-path]').fill(fixture[kind]);if(kind==='products')assert.equal(await form.locator('[data-wbs-statistic-year]').isVisible(),true);await form.locator('button[type=submit]').click();await page.waitForFunction(kind=>document.querySelector(`[data-wbs-path-form="${kind}"] [role=status]`)?.textContent.includes('已更新'),kind);}
 await page.locator('[data-wbs-refresh]').click();await page.waitForFunction(()=>document.querySelector('.wbs-intro [role=status]')?.textContent.includes('已更新'));
 assert.equal(await page.locator('.wbs-manual-accounts').evaluate(n=>n.open),true);const manualPoll=Date.now();await page.waitForFunction(start=>Date.now()-start>3500,manualPoll);assert.equal(await page.locator('.wbs-manual-accounts').evaluate(n=>n.open),true);
 for(const account of ['demo-a','demo-b']){const group=page.locator(`[data-wbs-account="${account}"]`);await group.locator('[data-wbs-field="allowed"]').fill('食品/宠物');await group.locator('[data-wbs-field="evidence"]').fill('虚构账号副本F列人工核对');await group.locator('[data-wbs-field="confirmed"]').check();}
 await page.locator('[data-wbs-profiles] button[type=submit]').click();await page.waitForFunction(()=>document.querySelector('[data-wbs-profiles] [role=status]')?.textContent.includes('已更新'));assert.ok((await state()).tasks.every(t=>t.validation.ok));checks.push('路径登记/刷新、集中F证据保存并通过校验');
 await nav('tasks');assert.match(await page.locator('.wb').innerText(),/账号资料已发送/);assert.match(await page.locator('.wb').innerText(),/A:I 已锁定/);
 await page.locator('#local-import').click();await page.locator('#local-import-file').setInputFiles({name:'fiction-revised.xlsx',mimeType:'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',buffer:Buffer.from(fixture.changed,'base64')});await page.waitForFunction(()=>!document.querySelector('#local-import-preview').disabled);await page.locator('#local-import-preview').click();await page.waitForFunction(()=>document.querySelector('#local-import-result')?.textContent.includes('修订差异'));assert.match(await page.locator('#local-import-result').innerText(),/冲突/);assert.equal(await page.locator('#local-import-commit').isDisabled(),true);await page.keyboard.press('Escape');assert.equal((await state()).tasks.find(t=>t.collaboration.locked).fields[4],'虚构商品');checks.push('紫色协同锁与重导差异冲突禁止确认');
 let preparedIds;await page.route('**/api/material/prepare',async route=>{preparedIds=route.request().postDataJSON().taskIds;await route.fulfill({status:409,contentType:'application/json',body:JSON.stringify({ok:false,error:{message:'虚构测试拦截，未调用业务软件'}})});});await page.locator('[data-wb-all]').first().check();await search.fill('虚构第二商品');const visibleId=await page.locator('[data-wb-select]').getAttribute('data-wb-select');await page.locator('.wb-group').first().locator('[data-wb-action="materialPrepare"]').click();await page.waitForFunction(()=>document.querySelector('.wb-message')?.textContent.includes('虚构测试拦截'));assert.deepEqual(preparedIds,[visibleId]);assert.equal((await state()).materialPackages.length,0);await search.fill('');checks.push('组内批量请求仅包含可见选择，路由测试拦截，不调用软件');
 for(const width of [1440,1920,800,390]){await page.setViewportSize({width,height:1000});for(const kind of ['tasks','sources']){await nav(kind);if(kind==='tasks')await page.locator('[data-wb-full]').check();const d=await page.evaluate(()=>({scroll:document.documentElement.scrollWidth,client:document.documentElement.clientWidth}));assert.ok(d.scroll<=d.client+1,`${kind} ${width} body overflow ${JSON.stringify(d)}`);if(kind==='tasks'){assert.ok(await page.locator('.wb-scroll').first().evaluate(n=>n.scrollWidth>n.clientWidth));await page.locator('[data-wb-full]').uncheck();}await page.screenshot({path:`test-results/workbench-006a-${kind}-${width}.png`,fullPage:true});}}
 checks.push('四尺寸任务/来源body无横向溢出、原表容器横滚、8张虚构截图');
 // Batch verification exercises the real UI/provider wire using synthetic API state only.
 const batchPage=await browser.newPage({viewport:{width:1440,height:1000}}),batchBodies=[];
 const batchState=structuredClone(await state());for(const t of batchState.tasks)t.editing={packageId:'fiction-editing-package',state:'review',normal:{available:true,ready:false},speed:{available:true,ready:false}};
 batchPage.on('pageerror',error=>errors.push(error.message));await batchPage.route('**/*',async route=>{const url=new URL(route.request().url());if(url.origin!==base){external.push(url.href);await route.abort();return;}if(url.pathname==='/api/state'){await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(batchState)});return;}if(url.pathname==='/api/editing/verify-batch'){const body=route.request().postDataJSON();batchBodies.push(body);for(const t of batchState.tasks)if(body.taskIds.includes(t.id))t.editing[body.kind].ready=true;await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({ok:true})});return;}if(route.request().method()!=='GET')throw new Error('Unexpected batch-page mutation '+url.pathname);await route.continue();});
 await batchPage.goto(base);await batchPage.locator('.wb-group').first().waitFor();
 // Select both accounts; each account's action must still exclude the other account.
 await batchPage.locator('[data-wb-all]').first().check();await batchPage.locator('[data-wb-all]').nth(1).check();
 for(const [kind,action] of [['normal','verifyNormal'],['speed','verifySpeed']]){
  if(kind==='speed')await batchPage.locator('[data-wb-filter="search"]').fill('虚构第二商品');
  const group=batchPage.locator('.wb-group').first();const expectedIds=await group.locator('[data-wb-select]:checked').evaluateAll(nodes=>nodes.map(n=>n.dataset.wbSelect));assert.equal(expectedIds.length,kind==='normal'?2:1);assert.ok(expectedIds.every(id=>batchState.tasks.find(t=>t.id===id).accountId==='demo-a'));
  await group.locator(`[data-wb-action="${action}"]`).click();await batchPage.locator('.wb-confirm').waitFor();assert.equal(await batchPage.locator('[data-wb-confirm]').isDisabled(),true);assert.equal(batchBodies.length,kind==='normal'?0:1);assert.equal(await batchPage.locator('[data-wb-watched]').isChecked(),false);assert.match(await group.locator('.wb-progress').first().innerText(),kind==='normal'?/普通：待观看/:/变速：待观看/);assert.ok(expectedIds.every(id=>!batchState.tasks.find(t=>t.id===id).editing[kind].ready));
  await batchPage.locator('[data-wb-watched]').check();assert.equal(await batchPage.locator('[data-wb-confirm]').isEnabled(),true);await batchPage.locator('[data-wb-confirm]').click();await batchPage.locator('.wb-confirm').waitFor({state:'hidden'});assert.deepEqual(batchBodies.at(-1),{taskIds:expectedIds,kind,confirmed:true,note:''});assert.match(await batchPage.locator('.wb-group').first().innerText(),kind==='normal'?/普通：已观看复核/:/变速：已观看复核/);
 }
 assert.equal(batchBodies.length,2);assert.equal(batchState.tasks.find(t=>t.accountId==='demo-b'&&t.fields[0]==='2026-10-14').editing.normal.ready,false);await batchPage.screenshot({path:'test-results/workbench-006a-batch-verification.png',fullPage:true});await batchPage.close();checks.push('普通/变速批量观看wire：未勾选禁提交、确认前不ready、confirmed=true、默认空备注、仅同账号可见选中ID；全程虚构API拦截');
 assert.deepEqual(errors,[]);assert.deepEqual(external,[]);assert.ok(!mutations.some(p=>/material\/send|material\/collect|editing\/collect|editing\/gui/.test(p)));console.log(JSON.stringify({ok:true,checks,mutations,batchBodies},null,2));
}catch(error){console.error(JSON.stringify({ok:false,checks,errors,external,error:error.stack},null,2));process.exitCode=1;}finally{await browser?.close();if(server)await new Promise(resolve=>{server.once('exit',resolve);server.stdin.end('\n');});assert.ok(path.basename(runtime).startsWith('console-test-006a-'));await rm(runtime,{recursive:true,force:true});}
