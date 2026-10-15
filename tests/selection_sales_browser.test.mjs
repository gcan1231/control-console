import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {spawn,execFileSync} from 'node:child_process';
import {mkdtemp,mkdir,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import path from 'node:path';
const require=createRequire(import.meta.url);
let pw;try{pw=require('playwright');}catch{pw=require(process.env.PLAYWRIGHT_MODULE);}
const python=process.env.PYTHON||'python',runtime=await mkdtemp(path.join(tmpdir(),'selection-sales-fiction-'));
await mkdir('test-results',{recursive:true});
execFileSync(python,['-B','-c',`import sys,shutil;sys.path.insert(0,'tests');from test_selection import SelectionTests;f=SelectionTests();f.setUp();f.sources('accounts',[f.account,{**f.account,'accountId':'fiction-b','name':'第二虚构账号'}]);f.sources('products',[{**f.goods,'row':n+2,'productId':str(9000000000000000000+n),'title':'虚构商品'+str(n),'statisticDate':'2026-10-15' if n<30 else '2026-10-14','totalSales':'大于1万' if n<54 else 'all','dailySales':'100~200' if n<30 else ''} for n in range(55)]);shutil.copytree(f.store.root,sys.argv[1],dirs_exist_ok=True);f.doCleanups()`,runtime]);
let server,browser;const errors=[],external=[],checks=[];
try{
 server=spawn(python,['-B','-u','-c',`import sys,threading;from tools.local_server import create_server;s=create_server(0,sys.argv[1]);t=threading.Thread(target=s.serve_forever,daemon=True);t.start();print(s.server_port,flush=True);sys.stdin.readline();s.shutdown();s.server_close();t.join()`,runtime],{stdio:['pipe','pipe','pipe']});
 const port=await new Promise((resolve,reject)=>{let s='';server.stdout.on('data',c=>{s+=c;if(s.includes('\n'))resolve(Number(s.split('\n')[0]));});server.on('error',reject);server.on('exit',c=>reject(new Error('server exit '+c)));});
 const base='http://127.0.0.1:'+port;browser=await pw.chromium.launch({headless:true,channel:process.env.PLAYWRIGHT_CHANNEL||'chrome'});
 const page=await browser.newPage({viewport:{width:1440,height:1000}});
 page.on('pageerror',e=>errors.push(e.message));page.on('console',m=>{if(m.type()==='error')errors.push(m.text());});
 await page.route('**/*',async r=>{if(new URL(r.request().url()).origin!==base){external.push(r.request().url());await r.abort();}else await r.continue();});
 const idle=()=>page.waitForFunction(()=>document.querySelector('.sel-app')?.getAttribute('aria-busy')==='false');
 const ctl=k=>page.locator(`[data-control="${k}"]`),rows=()=>page.locator('.sel-product-table tbody tr');
 const action=async k=>{await page.locator(`[data-action="${k}"]`).click();await idle();};
 const select=async(k,v)=>{await ctl(k).selectOption(v);await idle();};
 const total=async n=>assert.match(await page.locator('.sel-filter-summary p').innerText(),new RegExp(`共 ${n} 件`));
 const search=async s=>{await ctl('search').fill(s);await page.waitForTimeout(400);await idle();};
 await page.goto(base+'/selection.html');await idle();
 for(const k of ['range','totalSales','dailySales'])assert.equal(await ctl(k).inputValue(),'all');
 await total(55);assert.equal(await rows().count(),50);
 const dates=await rows().locator('td:nth-child(2) span').allTextContents();assert.deepEqual(dates,Array(30).fill('2026-10-15').concat(Array(20).fill('2026-10-14')));
 const firstIds=await rows().locator('.sel-id').allTextContents();
 assert.ok(await ctl('totalSales').locator('option[value="raw:all"]').count());
 await action('next');assert.equal(await rows().count(),5);assert.ok((await rows().locator('.sel-id').allTextContents()).every(id=>!firstIds.includes(id)));
 await select('totalSales','raw:大于1万');assert.match(await page.locator('.sel-pagination').innerText(),/第 1 \/ 2 页/);await total(54);
 await page.locator('[data-add]').first().click();await idle();assert.equal(await page.locator('[data-edit]').count(),1);
 await select('dailySales','missing');await total(24);await select('range','2026-10-14');await total(24);
 await search('虚构商品53');await total(1);assert.match(await rows().innerText(),/虚构商品53/);
 await select('dailySales','raw:100~200');await total(0);assert.equal(await rows().count(),0);
 assert.equal(await ctl('dailySales').locator('option[value="missing"]').count(),1);assert.equal(await ctl('totalSales').locator('option[value="raw:all"]').count(),1);
 await action('clear-filters');await total(55);assert.equal(await page.locator('[data-edit]').count(),1);
 await select('totalSales','raw:all');await total(1);assert.match(await rows().innerText(),/虚构商品54/);
 await page.locator('[data-account="fiction-b"]').click();await idle();await total(1);assert.equal(await page.locator('[data-edit]').count(),0);
 await page.locator('[data-account="fiction-a"]').click();await idle();assert.equal(await page.locator('[data-edit]').count(),1);
 await action('clear-filters');await select('range','latest');await total(30);await action('clear-filters');
 checks.push('默认55件两页日期降序/翻页无重叠/总日AND及日期搜索交集/空结果保留全范围选项/raw:all及空白/清筛选与跨账号保草稿');
 await ctl('totalSales').focus();await page.keyboard.press('ArrowDown');await page.keyboard.press('Enter');await idle();
 assert.equal(await ctl('totalSales').evaluate(e=>document.activeElement===e),true);await action('clear-filters');
 await ctl('search').focus();await ctl('search').dispatchEvent('compositionstart');await ctl('search').fill('虚构');await ctl('search').dispatchEvent('compositionend');await page.waitForTimeout(400);await idle();assert.equal(await ctl('search').evaluate(e=>document.activeElement===e),true);await action('clear-filters');
 for(const width of [1440,1920,800,390]){await page.setViewportSize({width,height:1000});const size=await page.evaluate(()=>[document.documentElement.scrollWidth,document.documentElement.clientWidth]);assert.ok(size[0]<=size[1]+1,'page overflow '+width+': '+size);await page.screenshot({path:`test-results/sales-filter-${width}.png`,fullPage:true});await page.locator(".sel-catalog").evaluate(e=>window.scrollTo(0,e.getBoundingClientRect().top+window.scrollY));await page.screenshot({path:`test-results/sales-filter-${width}-viewport.png`});await page.evaluate(()=>window.scrollTo(0,0));}
 checks.push('键盘下拉与中文输入后焦点/1440、1920、800、390无全页横溢出截图');
 assert.deepEqual(errors,[]);assert.deepEqual(external,[]);console.log(JSON.stringify({ok:true,checks},null,2));
}catch(error){console.error(JSON.stringify({ok:false,checks,errors,external,error:error.stack},null,2));process.exitCode=1;}
finally{await browser?.close();if(server)await new Promise(resolve=>{server.once('exit',resolve);server.stdin.end('\n');});await rm(runtime,{recursive:true,force:true});}
