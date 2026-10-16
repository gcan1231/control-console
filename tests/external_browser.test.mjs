// Fictional external outputs, ephemeral loopback HTTP, no native picker or business launch.
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {createRequire} from 'node:module';
import {mkdir,writeFile} from 'node:fs/promises';
const require=createRequire(import.meta.url);let pw;try{pw=require('playwright');}catch{pw=require(process.env.PLAYWRIGHT_MODULE);}
const script=`import sys,json,threading
sys.path.insert(0,'tests')
from test_external_intake import ExternalIntakeTests
from test_local_import import task,workbook,HEADERS
from tools.local_server import create_server
f=ExternalIntakeTests();f.setUp()
later=task();later[0]='2026-10-14'
f.store.commit_import('tasks','history-fiction.xlsx',workbook([HEADERS,later]),{})
f.video(f.product/'raw.mp4');f.video(f.product/'成片'/'normal.mp4');f.video(f.product/'成片'/'变速'/'speed.mp4')
upload=f.f.root/'upload-fiction.xlsx';upload.write_bytes(workbook([HEADERS,*[t['fields'] for t in f.store.state()['tasks']],task('demo-b','另一账号',product='9000000000000000002')]))
s=create_server(0,f.store.root);t=threading.Thread(target=s.serve_forever,daemon=True);t.start()
print(json.dumps({'port':s.server_port,'root':str(f.root),'upload':str(upload),'taskId':f.task['id'],'fields':f.task['fields']},ensure_ascii=True),flush=True)
try:sys.stdin.readline()
finally:s.shutdown();s.server_close();t.join();f.doCleanups()
`;
const child=spawn(process.env.PYTHON||'python',['-B','-u','-c',script],{stdio:['pipe','pipe','pipe']});let stderr='';child.stderr.on('data',c=>stderr+=c);
const info=await new Promise((resolve,reject)=>{let text='';const timer=setTimeout(()=>reject(Error('fixture timeout '+stderr)),30000);child.stdout.on('data',c=>{text+=c;if(text.includes('\n')){clearTimeout(timer);resolve(JSON.parse(text.split('\n')[0]));}});child.on('error',reject);});
const checks=[],errors=[],requests=[],contrasts=[];let browser;
const out='test-results/external-007';
try{
 assert.notEqual(info.port,8861);await mkdir(out,{recursive:true});browser=await pw.chromium.launch({headless:true,channel:'chrome'});
 const page=await browser.newPage({viewport:{width:1440,height:1000}}),base=`http://127.0.0.1:${info.port}`;
 page.on('pageerror',e=>errors.push(e.message));
 await page.route('**/*',route=>{const u=new URL(route.request().url());assert.equal(u.origin,base);requests.push([route.request().method(),u.pathname]);return route.continue();});
 await page.route('**/api/local/pick',route=>route.fulfill({json:{ok:true,path:'',cancelled:true}}));
 await page.goto(base+'/external.html');await page.getByRole('status').filter({hasText:'已读取本机任务'}).waitFor();
 const state=async()=>await(await page.request.get(base+'/api/state')).json();
 const before=await state(),choose=()=>page.locator(`[data-task="${info.taskId}"]`);
 await page.locator('#ext-task-file').setInputFiles(info.upload);await page.locator('#ext-import-preview').waitFor({state:'visible'});
 await page.waitForFunction(()=>!document.querySelector('#ext-import-preview').disabled);
 assert.equal(await page.locator('#ext-import-commit').isDisabled(),true);assert.equal((await state()).tasks.length,before.tasks.length);
 await page.locator('#ext-import-preview').click();await page.waitForFunction(()=>!document.querySelector('#ext-import-commit').disabled);
 assert.equal((await state()).tasks.length,before.tasks.length);assert.equal(await page.locator('.ext-import-table thead th').count(),17);
 await page.locator('#ext-import-commit').click();await page.getByRole('status').filter({hasText:'已导入'}).waitFor();assert.equal((await state()).tasks.length,before.tasks.length+1);checks.push('任务文件读取/预览不提交，16列表预览后明确导入');
 await choose().check();await page.locator('#ext-root').fill(info.root);
 const preview=async()=>{await page.locator('#ext-preview').click();await page.locator('#ext-accept-form').waitFor({state:'visible'});};
 const verify=async()=>{await page.locator('#ext-note').fill('虚构逐项观看核对');await page.locator('#ext-confirm').check();assert.equal(await page.locator('#ext-accept').isEnabled(),true);};
 const invalid=async()=>{assert.equal(await page.locator('#ext-accept-form').isHidden(),true);assert.equal(await page.locator('#ext-confirm').isChecked(),false);assert.equal(await page.locator('#ext-note').inputValue(),'');};
 await preview();assert.match(await page.locator('#ext-preview-result').innerText(),/历史任务/);await verify();
 await page.locator('#ext-root-pick').click();await page.waitForFunction(()=>!document.querySelector('main').hasAttribute('aria-busy'));assert.equal(await page.locator('#ext-root').inputValue(),info.root);assert.equal(await page.locator('#ext-confirm').isChecked(),true);checks.push('历史日期提示及取消原生目录选择保留原root与核验');
 await page.locator('#ext-root').fill(info.root+'-missing');await invalid();await page.locator('#ext-preview').click();await page.getByRole('alert').waitFor();assert.equal(await page.locator('#ext-root').inputValue(),info.root+'-missing');assert.equal(await choose().isChecked(),true);
 await page.locator('#ext-root').fill(info.root);await preview();await verify();await choose().uncheck();await invalid();await choose().check();
 await preview();await verify();await page.locator('#ext-kind').selectOption('finished');await invalid();await page.locator('#ext-kind').selectOption('materials');
 await preview();await verify();await page.locator('#ext-account').selectOption({label:'演示账号（demo-a）'});await invalid();assert.equal(await choose().isChecked(),false);await choose().check();checks.push('账号/类型/目录/任务变化清空核验；错误保留路径和任务选择');
 await preview();await verify();const previewBody=requests.length;
 const acceptedResponse=page.waitForResponse(r=>r.url().endsWith('/api/external/accept')&&r.request().method()==='POST');
 await page.locator('#ext-accept').dblclick();const accepted=await(await acceptedResponse).json();await page.getByRole('status').filter({hasText:'已记录人工接纳'}).waitFor();
 assert.equal(requests.slice(previewBody).filter(([m,p])=>m==='POST'&&p==='/api/external/accept').length,1);
 let current=await state();assert.equal(current.externalAcceptances.length,1);assert.equal(current.materialTasks[info.taskId],undefined);assert.equal(current.externalTasks[info.taskId].materials.manual,true);assert.deepEqual(current.tasks.find(t=>t.id===info.taskId).fields,info.fields);
 // Replay the real request via its browser-captured headers; no synthetic credentials.
 const acceptRequest=(await acceptedResponse).request();const replay=await page.request.post(base+'/api/external/accept',{headers:await acceptRequest.allHeaders(),data:acceptRequest.postData()});assert.equal((await replay.json()).reused,true);
 await page.locator('#ext-kind').selectOption('finished');await choose().check();await preview();await verify();await page.locator('#ext-accept').click();await page.getByRole('status').filter({hasText:'已记录人工接纳'}).waitFor();
 current=await state();assert.equal(current.externalAcceptances.length,2);assert.equal(current.externalTasks[info.taskId].finished.normal.ready,true);assert.equal(current.externalTasks[info.taskId].finished.speed.ready,true);assert.equal(current.editingTasks[info.taskId],undefined);assert.deepEqual(current.tasks.find(t=>t.id===info.taskId).fields,info.fields);checks.push('无报告人工素材→成片接纳、双击及重复请求幂等、未伪造上游成功、原字段/J不变');
 for(const v of [{width:1440,height:1000},{width:1920,height:1080},{width:800,height:1000},{width:390,height:844}]){
  await page.setViewportSize(v);await page.evaluate(()=>window.scrollTo(0,0));const widths=await page.evaluate(()=>[document.documentElement.scrollWidth,document.documentElement.clientWidth]);assert.ok(widths[0]<=widths[1]+1,JSON.stringify({v,widths}));
  await page.screenshot({path:`${out}/external-${v.width}.png`,fullPage:true});
  const failures=await page.evaluate(()=>{const lum=c=>{const a=c.match(/[\d.]+/g)?.slice(0,3).map(Number);if(!a)return 0;return a.map(x=>x/255).map(x=>x<=.04045?x/12.92:((x+.055)/1.055)**2.4).reduce((s,x,i)=>s+x*[.2126,.7152,.0722][i],0);};return [...document.querySelectorAll('h1,h2,p,label,button,a')].filter(n=>n.getBoundingClientRect().width&&n.textContent.trim()&&!n.disabled).map(n=>{const s=getComputedStyle(n);let b=n;while(b&&getComputedStyle(b).backgroundColor==='rgba(0, 0, 0, 0)')b=b.parentElement;const bg=b?getComputedStyle(b).backgroundColor:'rgb(255,255,255)',x=lum(s.color),y=lum(bg),ratio=(Math.max(x,y)+.05)/(Math.min(x,y)+.05);return {text:n.textContent.trim().slice(0,30),ratio};}).filter(x=>x.ratio<4.5);});contrasts.push({width:v.width,failures});assert.deepEqual(failures,[]);
 }
 await page.setViewportSize({width:1440,height:1000});await page.goto(base+'/external.html');await page.getByRole('status').filter({hasText:'已读取本机任务'}).waitFor();await page.keyboard.press('Tab');assert.equal(await page.locator('.skip-link').evaluate(n=>n===document.activeElement),true);await page.keyboard.press('Enter');assert.equal(await page.locator('#main-content').evaluate(n=>n===document.activeElement),true);
 for(let i=0;i<12;i++){await page.keyboard.press('Tab');assert.equal(await page.evaluate(()=>!!document.activeElement&&document.activeElement!==document.body),true);}checks.push('外部页四尺寸无横向溢出、文本对比度及键盘跳转');
 for(const [name,url,core] of [['selection','/selection.html',null],['software','/external.html','#ext-preview'],['tasks','/',null],['publish','/publish.html','#pub-refresh'],['logs','/logs.html','#logs-refresh']])for(const width of [1440,390]){
  await page.setViewportSize({width,height:width===390?844:1000});await page.goto(base+url);await page.waitForLoadState('networkidle');assert.ok(await page.locator('link[href="/graphite.css"]').count());
  const widths=await page.evaluate(()=>[document.documentElement.scrollWidth,document.documentElement.clientWidth]);assert.ok(widths[0]<=widths[1]+1,JSON.stringify({name,width,widths}));
  if(core){const button=page.locator(core);await button.scrollIntoViewIfNeeded();assert.equal(await button.isVisible(),true);assert.equal(await button.isEnabled(),true);await button.focus();assert.equal(await button.evaluate(n=>n===document.activeElement),true);const box=await button.boundingBox();assert.ok(box.x>=0&&box.x+box.width<=width+1,JSON.stringify({name,width,box}));}
  await page.evaluate(()=>window.scrollTo(0,0));await page.screenshot({path:`${out}/graphite-${name}-${width}.png`,fullPage:true});
 }
 assert.deepEqual(errors,[]);assert.ok(requests.filter(([m])=>m==='POST').every(([,p])=>/^\/api\/(inspect|tasks\/import\/(preview|commit)|external\/(preview|accept)|local\/pick)$/.test(p)),JSON.stringify(requests));
 checks.push('正式每日选品/外部软件/任务总表/发文/日志石墨1440与390截图，无横向溢出且核心操作可聚焦');
 await writeFile(`${out}/report.json`,JSON.stringify({ok:true,checks,contrasts,requests,errors},null,2));console.log(JSON.stringify({ok:true,checks},null,2));
}catch(error){await mkdir(out,{recursive:true});await writeFile(`${out}/report.json`,JSON.stringify({ok:false,checks,contrasts,requests,errors,error:error.stack,stderr},null,2));console.error(JSON.stringify({ok:false,checks,error:error.stack,stderr}));process.exitCode=1;
}finally{await browser?.close();await new Promise(resolve=>{const timer=setTimeout(()=>child.kill(),5000);child.once('exit',()=>{clearTimeout(timer);resolve();});child.stdin.end('\n');});}
