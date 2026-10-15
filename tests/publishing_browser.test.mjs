import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {createRequire} from 'node:module';
import {mkdir} from 'node:fs/promises';
const require=createRequire(import.meta.url);let pw;try{pw=require('playwright');}catch{pw=require(process.env.PLAYWRIGHT_MODULE);}
const script=`import sys,json,threading
sys.path.insert(0,'tests')
from test_publishing_http import PublishingHttpTests
from control_console.workbooks import task_workbook
from pathlib import Path
f=PublishingHttpTests();f.setUp();f.six_ready()
print(json.dumps({'port':f.server.server_port}),flush=True)
try:
 for line in sys.stdin:
  if line.strip()=='stop':break
  p=f.server.publishing.state()['publishingPackages'][0];slot=p['batches'][0]['slots'][0];fields=list(p['tasks'][slot['taskIds'][0]]['fields']);fields[12:14]=['已发布','计划已发布'];Path(slot['workbookPath']).write_bytes(task_workbook([{'fields':fields}]))
  print('ack',flush=True)
finally:f.doCleanups()
`;
const child=spawn(process.env.PYTHON||'python',['-B','-u','-c',script],{stdio:['pipe','pipe','pipe']});let pending='',stderr='';const q=[],w=[];
child.stderr.on('data',c=>stderr+=c);child.stdout.on('data',c=>{pending+=c;while(pending.includes('\n')){const i=pending.indexOf('\n'),v=pending.slice(0,i);pending=pending.slice(i+1);w.length?w.shift()(v):q.push(v);}});
const line=()=>q.length?Promise.resolve(q.shift()):new Promise((r,j)=>{const timer=setTimeout(()=>j(new Error('Fixture timeout: '+stderr)),30000);w.push(v=>{clearTimeout(timer);r(v);});});
let browser;const checks=[],errors=[],requests=[];
try{
 await mkdir('test-results',{recursive:true});const info=JSON.parse(await line());browser=await pw.chromium.launch({headless:true,channel:'chrome'});const page=await browser.newPage({viewport:{width:1440,height:1000}});
 page.on('pageerror',e=>errors.push(e.message));await page.route('**/*',r=>{const u=new URL(r.request().url());assert.equal(u.hostname,'127.0.0.1');requests.push([r.request().method(),u.pathname]);return r.continue();});
 await page.goto('http://127.0.0.1:'+info.port+'/publish.html');await page.locator('[data-pub-task]:enabled').first().waitFor();assert.equal(await page.locator('html').getAttribute('lang'),'zh-CN');
 await page.locator('#pub-select-all').check();assert.equal(await page.locator('[data-pub-port]').first().inputValue(),'');await page.locator('#pub-prepare').click();await page.getByRole('status').filter({hasText:'请填写每个已选账号'}).waitFor();checks.push('独立端口无J默认值，缺失明确报错');
 for(const [i,input] of (await page.locator('[data-pub-port]').all()).entries())await input.fill(String(9001+i));await page.locator('#pub-prepare').click();await page.locator('[data-pub-action="handoff"]').waitFor();assert.equal(await page.locator('.pub-unassigned').count(),4);assert.equal(await page.locator('.pub-batch').count(),2);checks.push('六账号生成两批五槽与四个未分配槽');
 await page.locator('[data-pub-action="handoff"]').click();await page.locator('#pub-confirm').click();await page.locator('#pub-dialog-error').waitFor();assert.equal(await page.locator('#pub-confirmed').evaluate(e=>e===document.activeElement),true);
 for(let i=0;i<8;i++){await page.keyboard.press('Tab');assert.equal(await page.evaluate(()=>document.querySelector('#pub-dialog').contains(document.activeElement)),true);}await page.keyboard.press('Escape');assert.equal(await page.locator('[data-pub-action="handoff"]').evaluate(e=>e===document.activeElement),true);
 await page.locator('[data-pub-action="handoff"]').click();await page.locator('#pub-confirmed').check();await page.locator('#pub-confirm').click();await page.locator('#pub-dialog').waitFor({state:'hidden'});checks.push('人工交接确认、中文错误、模态焦点约束与Escape还原');
 child.stdin.write('receipt\n');await line();await page.locator('[data-pub-action="collect"]').click();await page.locator('#pub-confirmed').check();await page.locator('#pub-confirm').click();await page.locator('#pub-dialog').waitFor({state:'hidden'});await page.locator('.pub-receipts summary').click();await page.getByText('软件有回执，待核验',{exact:true}).first().waitFor();
 await page.locator('[data-pub-action="verify"]').first().click();assert.equal(await page.locator('#pub-result').inputValue(),'unknown');await page.locator('#pub-confirmed').check();await page.locator('#pub-confirm').click();await page.locator('#pub-dialog-error').waitFor();assert.equal(await page.locator('#pub-note').evaluate(e=>e===document.activeElement),true);await page.locator('#pub-note').fill('虚构人工确认，仍不确定');await page.locator('#pub-confirm').click();await page.locator('#pub-dialog').waitFor({state:'hidden'});checks.push('M/N仅为软件回执；人工核验默认未知且需要独立说明');
 await page.waitForFunction(()=>Boolean(document.activeElement?.dataset.task),null,{timeout:3000});const focusedTask=await page.evaluate(()=>document.activeElement?.dataset.task);assert.ok(focusedTask,'unknown核验保存后恢复同任务操作按钮焦点');const sameTask=page.locator(`[data-pub-action="verify"][data-task="${focusedTask}"]`);await sameTask.click();await page.locator('#pub-result').selectOption('verified');await page.locator('#pub-note').fill('虚构补充人工核验，已确认通过');await page.locator('#pub-confirmed').check();await page.locator('#pub-confirm').click();await page.locator('#pub-dialog').waitFor({state:'hidden'});assert.equal(await sameTask.count(),0,'终态不显示再次修改按钮');
 const finalState=await (await page.request.get('http://127.0.0.1:'+info.port+'/api/publishing/state')).json();const finalRecord=finalState.publishingTasks[focusedTask];assert.equal(finalRecord.state,'verified');assert.ok(finalRecord.verificationHistory.some(v=>v.result==='unknown'));assert.equal(finalState.publishingPackages.length,1);assert.equal(finalState.publishingPackages[0].attempt,1);checks.push('unknown可继续人工核验通过、保留历史、终态隐藏改写按钮且不新增attempt');
 for(const v of [{width:1440,height:1000},{width:1920,height:1080},{width:800,height:1000},{width:390,height:844}]){await page.setViewportSize(v);await page.evaluate(()=>window.scrollTo(0,0));const d=await page.evaluate(()=>[document.documentElement.scrollWidth,document.documentElement.clientWidth]);assert.ok(d[0]<=d[1]+1,JSON.stringify({viewport:v,widths:d}));await page.screenshot({path:`test-results/publishing-007-${v.width}x${v.height}.png`,fullPage:true});}
 assert.deepEqual(errors,[]);assert.ok(requests.filter(([method])=>method==='POST').every(([,path])=>/^\/api\/publishing\/(prepare|handoff|collect|verify)$/.test(path)));console.log(JSON.stringify({ok:true,checks,viewports:[1440,1920,800,390]},null,2));
}catch(e){console.error(JSON.stringify({ok:false,checks,error:e.stack,stderr}));process.exitCode=1;}finally{await browser?.close();await new Promise(r=>{const timer=setTimeout(()=>child.kill(),3000);child.once('exit',()=>{clearTimeout(timer);r();});child.stdin.end('stop\n');});}
