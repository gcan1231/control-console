import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {mkdir} from 'node:fs/promises';
import path from 'node:path';
const require=createRequire(import.meta.url);
let playwright;
try {playwright=require('playwright');} catch {assert.ok(process.env.PLAYWRIGHT_MODULE,'Set PLAYWRIGHT_MODULE to the bundled playwright package directory');playwright=require(process.env.PLAYWRIGHT_MODULE);}
const base=process.env.DEMO_URL||'http://127.0.0.1:8860';
assert.equal(new URL(base).hostname,'127.0.0.1');
await mkdir('test-results',{recursive:true});
const browser=await playwright.chromium.launch({headless:true,channel:process.env.PLAYWRIGHT_CHANNEL||'chrome'});
const page=await browser.newPage({viewport:{width:1440,height:1000}});
const errors=[],external=[],checks=[],findings=[];
page.on('pageerror',e=>errors.push(e.message));page.on('console',m=>{if(m.type()==='error')errors.push(m.text());});
await page.route('**/*',async route=>{const u=new URL(route.request().url());if(!['127.0.0.1','localhost'].includes(u.hostname)){external.push(u.href);await route.abort();}else await route.continue();});
const nav=async p=>page.locator(`nav [data-page="${p}"]`).click();
const scenario=async s=>{await page.locator('#scenario').selectOption(s);};
const row=id=>page.locator(`#task-table tbody tr[data-task="demo-task-${id}"]`);
const open=async id=>{await row(id).click();await page.locator('#drawer').waitFor({state:'visible'});};
const close=async()=>page.locator('#close-drawer').click();
const text=async l=>l.innerText();
const count=async n=>assert.equal(await page.locator('#task-table tbody tr[data-task]').count(),n);
try {
 await page.goto(base);await count(12);
 for(const p of ['sources','publish','hosts','diagrams','tasks']){await nav(p);assert.ok((await text(page.locator('#page-title'))).length);}
 checks.push('五页导航与图iframe加载');
 await scenario('six');await page.locator('#account-filter').selectOption('demo-a01');await count(2);await page.locator('#account-filter').selectOption('');await page.locator('#stage-filter').selectOption('ready');await count(12);await page.locator('#search').fill('9000000000000000001');await count(1);await page.locator('#search').fill('');
 await page.locator('[data-view="original"]').click();assert.equal(await page.locator('th[data-field-index]').count(),16);assert.match(await text(page.locator('th[data-field-index="13"]')),/千川塞素材软件日志/);assert.equal((await row(1).locator('td').nth(6).innerText()),'9000000000000000001');assert.equal(await row(1).locator('td').nth(10).innerText(),'7001');assert.equal(await row(2).locator('td').nth(10).innerText(),'—');await open(2);assert.match(await text(page.locator('#drawer')),/空白（保持原样）/);await close();checks.push('筛选搜索、16列、J与长ID显示');
 await page.locator('[data-view="progress"]').click();await scenario('happy');await open(1);for(const s of ['material','review','editing_wait','editing'])await page.locator(`[data-advance][data-stage="${s}"]`).click();assert.match(await text(page.locator('#drawer')),/成片待交接/);await page.locator('#drawer [data-page="publish"]').click();await page.locator('[data-handoff="1"]').click();await nav('tasks');await open(1);await page.locator('[data-stage="publishing"]').click();assert.match(await text(page.locator('#drawer')),/软件报告待核验/);assert.match(await text(page.locator('#drawer')),/尚未核验/);await page.locator('[data-stage="reported"]').click();assert.match(await text(page.locator('#drawer')),/演示核验完成/);await close();checks.push('正常闭环与reported人工模拟核验');
 await scenario('categorywrong');await open(1);const wrongAction=page.locator('#drawer .drawer-action');if(!await wrongAction.isDisabled())findings.push('分类不符任务抽屉“查看发文交接”未禁用');await close();await nav('publish');await page.locator('.excluded summary').click();assert.match(await text(page.locator('.excluded')),/品类不符/);
 await nav('tasks');await scenario('categorymissing');await nav('publish');assert.equal(await page.locator('[data-handoff]').count(),0);await nav('sources');await page.locator('[data-source="products"]').click();await nav('publish');assert.equal(await page.locator('[data-handoff]').count(),2);checks.push('分类错配排除、缺证据导入解除');
 await nav('tasks');await scenario('materialfail');await open(1);await page.locator('[data-stage="failed"]').click();assert.match(await text(page.locator('#drawer')),/待找素材/);await close();assert.equal(await row(2).locator('td').nth(5).locator('.badge').innerText(),'待找素材');await open(2);assert.equal(await page.locator('#drawer .timeline').innerText().then(t=>t.includes('重试')),false);await close();checks.push('下载失败仅选中行重试');
 for(const [s,allowed,blocked] of [['regularmissing','douyin','qianchuan'],['speedmissing','qianchuan','douyin']]){await scenario(s);await nav('publish');for(const m of [allowed,blocked,'both']){await page.locator('#publish-mode').selectOption(m);await page.locator('.excluded summary').click();const first=page.locator('.excluded div').filter({has:page.locator('strong',{hasText:'燕麦脆片组合'})});assert.equal(await first.count(),m===allowed?0:1);}}
 checks.push('普通/变速不足三模式分别阻止');
 await scenario('six');await nav('publish');await page.locator('#publish-mode').selectOption('both');assert.deepEqual(await page.locator('[data-batch]').evaluateAll(es=>es.map(e=>e.dataset.batch)),['1','2']);await page.locator('[data-handoff="1"]').click();assert.deepEqual(await page.locator('[data-batch]').evaluateAll(es=>es.map(e=>e.dataset.batch)),['2']);await page.locator('[data-handoff="2"]').click();assert.equal(await page.locator('[data-handoff]').count(),0);await nav('tasks');await count(12);await page.locator('#import-demo').click();await page.locator('#import-demo').click();await count(12);await page.locator('#reset').click();assert.equal(await page.locator('tbody .badge.green').count()>0,true);checks.push('稳定第二批与导入幂等、重置');
 for(const viewport of [{width:1440,height:1000},{width:1920,height:1080},{width:800,height:1000}]){await page.setViewportSize(viewport);await nav('tasks');await page.locator('[data-view="progress"]').click();for(const kind of ['tasks','original','publish']){if(kind==='original')await page.locator('[data-view="original"]').click();if(kind==='publish')await nav('publish');await page.screenshot({path:`test-results/${kind}-${viewport.width}x${viewport.height}.png`,fullPage:true});const size=await page.evaluate(()=>({scroll:document.documentElement.scrollWidth,client:document.documentElement.clientWidth}));assert.ok(size.scroll<=size.client+1,`${kind} ${viewport.width} whole-page overflow ${JSON.stringify(size)}`);}}
 checks.push('三尺寸首页/原表/五槽截图与全页overflow');
 const post=await page.request.post(base);assert.equal(post.status(),405);
 for(const route of ['/../.git/config','/%2e%2e/.git/config','/%252e%252e/.git/config','/%2e%2e%5c.git%5cconfig','/plan/README.md','/.git/config']){const r=await page.request.get(base+route);assert.equal(r.status(),404,route);}
 checks.push('HTTP写入拒绝与路径绕过404');assert.deepEqual(external,[]);assert.deepEqual(errors,[]);checks.push('无外部网络、console错误或pageerror');
 assert.deepEqual(findings,[]);console.log(JSON.stringify({ok:true,checks,errors,external},null,2));
} catch(e){await page.screenshot({path:'test-results/failure.png',fullPage:true});console.error(JSON.stringify({ok:false,checks,errors,external,error:e.stack},null,2));process.exitCode=1;}finally{await browser.close();}
