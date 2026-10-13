import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {mkdir} from 'node:fs/promises';
import {HEADERS} from '../demo/data/fixtures.js';
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
const detail=name=>page.locator(`#drawer [data-detail="${name}"]`);
async function layoutShot(name){
 const size=await page.evaluate(()=>({scroll:document.documentElement.scrollWidth,client:document.documentElement.clientWidth}));
 assert.ok(size.scroll<=size.client+1,`${name} whole-page overflow ${JSON.stringify(size)}`);
 await page.screenshot({path:`test-results/${name}.png`,fullPage:true});
}
async function drawerKeyboard(){
 const trigger=row(1);await trigger.focus();await page.keyboard.press('Enter');
 await page.locator('#drawer').waitFor({state:'visible'});
 assert.equal(await page.locator('#drawer').getAttribute('role'),'dialog');assert.equal(await page.locator('#drawer').getAttribute('aria-modal'),'true');
 for(let i=0;i<20;i++){assert.equal(await page.evaluate(()=>document.querySelector('#drawer').contains(document.activeElement)),true,'drawer focus escaped');await page.keyboard.press('Tab');}
 for(let i=0;i<3;i++){await page.keyboard.press('Shift+Tab');assert.equal(await page.evaluate(()=>document.querySelector('#drawer').contains(document.activeElement)),true);}
 await page.keyboard.press('Escape');assert.equal(await page.locator('#drawer').isHidden(),true);assert.equal(await trigger.evaluate(el=>el===document.activeElement),true,'Escape did not restore trigger focus');
}
async function inputStability(){
 await page.locator('#clear-filters').click();const search=page.locator('#search');await search.focus();
 for(const [i,c] of [...'9000000000000000001'].entries()){await page.keyboard.type(c);assert.equal(await search.evaluate(el=>el===document.activeElement),true);assert.equal(await search.evaluate(el=>el.selectionStart),i+1);}
 await count(1);await search.evaluate(el=>el.setSelectionRange(3,3));await page.keyboard.type('X');assert.equal(await search.evaluate(el=>el.selectionStart),4);assert.equal(await search.inputValue(),'900X0000000000000001');
 await page.keyboard.press('Backspace');assert.equal(await search.evaluate(el=>el.selectionStart),3);await count(1);
 await search.fill('');await search.evaluate(el=>{el.dispatchEvent(new CompositionEvent('compositionstart',{bubbles:true,data:''}));el.value='青';el.setSelectionRange(1,1);el.dispatchEvent(new InputEvent('input',{bubbles:true,data:'青',inputType:'insertCompositionText',isComposing:true}));});
 assert.equal(await search.evaluate(el=>el===document.activeElement),true);assert.equal(await search.inputValue(),'青');assert.equal(await search.evaluate(el=>el.selectionStart),1);
 await search.evaluate(el=>{el.value='青禾';el.setSelectionRange(2,2);el.dispatchEvent(new InputEvent('input',{bubbles:true,data:'禾',inputType:'insertCompositionText',isComposing:true}));el.dispatchEvent(new CompositionEvent('compositionend',{bubbles:true,data:'青禾'}));el.dispatchEvent(new InputEvent('input',{bubbles:true,data:'青禾',inputType:'insertText'}));});
 assert.equal(await search.inputValue(),'青禾');assert.equal(await search.evaluate(el=>el===document.activeElement&&el.selectionStart===2),true);await count(2);await page.locator('#clear-filters').click();await count(12);
}
async function diagramLoad(kind){await page.locator(`[data-diagram="${kind}"]`).click();const iframe=page.locator('#page-content iframe');await iframe.waitFor();assert.match(await iframe.getAttribute('src'),new RegExp(`/diagrams/${kind}\\.html$`));const frame=await iframe.elementHandle().then(h=>h.contentFrame());await frame.waitForLoadState('load');await frame.waitForSelector('svg');assert.ok(await frame.locator('svg').count()>0);}
async function readinessMatrix(){
 for(const [s,allow] of [['categorywrong',[]],['categorymissing',[]],['six',['both','douyin','qianchuan']],['regularmissing',['douyin']],['speedmissing',['qianchuan']]])for(const m of ['both','douyin','qianchuan']){await scenario(s);await nav('publish');await page.locator('#publish-mode').selectOption(m);await nav('tasks');await open(1);assert.equal(await page.locator('#drawer [data-page="publish"]').isDisabled(),!allow.includes(m),`${s}/${m}`);await close();}
}
try {
 await page.goto(base+'?workspace=demo');await count(12);
 assert.equal(await page.locator('#task-table [data-cell="status"]').count(),12);
 const visibleRows=await page.locator('#task-table tbody tr[data-task]').evaluateAll(es=>es.filter(e=>{const r=e.getBoundingClientRect(),p=e.closest('table').parentElement.getBoundingClientRect();return r.top>=p.top&&r.bottom<=Math.min(innerHeight,p.bottom);}).length);if(visibleRows<8)findings.push(`1440 first screen only ${visibleRows} full rows`);
 await inputStability();await drawerKeyboard();checks.push('默认进度/首屏8行、搜索焦点光标composition与抽屉键盘');
 for(const p of ['sources','publish','hosts','diagrams','tasks']){await nav(p);assert.ok((await text(page.locator('#page-title'))).length);}
 checks.push('五页导航与图iframe加载');
 await nav('diagrams');await diagramLoad('architecture');await diagramLoad('workflow');await nav('tasks');checks.push('两图iframe实际加载与切换');
 await scenario('six');await page.locator('#account-filter').selectOption('demo-a01');await count(2);await page.locator('#account-filter').selectOption('');await page.locator('#stage-filter').selectOption('ready');await count(12);await page.locator('#search').fill('9000000000000000001');await count(1);await page.locator('#search').fill('');
 await page.locator('[data-view="original"]').click();assert.equal(await page.locator('th[data-field-index]').count(),16);assert.match(await text(page.locator('th[data-field-index="13"]')),/千川塞素材软件日志/);assert.equal((await row(1).locator('td[data-field-index="5"]').innerText()),'9000000000000000001');assert.equal(await row(1).locator('td[data-field-index="9"]').innerText(),'7001');assert.equal(await row(2).locator('td[data-field-index="9"]').innerText(),'—');await open(2);assert.match(await text(page.locator('#drawer')),/空白（保持原样）/);await close();checks.push('筛选搜索、16列、J与长ID显示');
 assert.deepEqual(await page.locator('th[data-field-index]').evaluateAll(es=>es.map(e=>e.textContent.trim().replace(/^[A-P]\s*/,''))),HEADERS);
 const last=page.locator('th[data-field-index="15"]');await last.evaluate(el=>{let p=el.parentElement;while(p&&p.scrollWidth<=p.clientWidth)p=p.parentElement; if(!p)throw new Error('Missing table horizontal scroll container');p.scrollLeft=p.scrollWidth;});
 assert.equal(await last.evaluate(el=>{const r=el.getBoundingClientRect();return r.left>=0&&r.right<=innerWidth;}),true,'P column unreachable');
 await open(1);assert.equal(await detail('product-id').innerText(),'9000000000000000001');assert.match(await detail('j-value').innerText(),/7001/);await close();
 await page.locator('[data-view="progress"]').click();await scenario('happy');await open(1);for(const s of ['material','review','editing_wait','editing'])await page.locator(`[data-advance][data-stage="${s}"]`).click();assert.match(await text(page.locator('#drawer')),/成片待交接/);await page.locator('#drawer [data-page="publish"]').click();await page.locator('[data-handoff="1"]').click();await nav('tasks');await open(1);await page.locator('[data-stage="publishing"]').click();assert.match(await text(page.locator('#drawer')),/软件报告待核验/);assert.match(await detail('verification').innerText(),/尚未核验/);assert.equal(await detail('report-m').innerText(),'已发布');assert.equal(await detail('report-n').innerText(),'计划已发布');await page.locator('[data-stage="reported"]').click();assert.match(await detail('verification').innerText(),/演示核验完成/);await close();checks.push('正常闭环与reported人工模拟核验');
 await scenario('categorywrong');await open(1);const wrongAction=page.locator('#drawer [data-page="publish"]');if(!await wrongAction.isDisabled())findings.push('分类不符任务抽屉“查看发文交接”未禁用');await close();await nav('publish');await page.locator('.excluded summary').click();assert.match(await text(page.locator('.excluded')),/品类不符/);
 await nav('tasks');await scenario('categorymissing');await nav('publish');assert.equal(await page.locator('[data-handoff]').count(),0);await nav('sources');await page.locator('[data-source="products"]').click();await nav('publish');assert.equal(await page.locator('[data-handoff]').count(),2);checks.push('分类错配排除、缺证据导入解除');
 await nav('tasks');await scenario('materialfail');await open(1);await page.locator('[data-stage="failed"]').click();assert.match(await text(page.locator('#drawer')),/待找素材/);await close();assert.equal(await row(2).locator('[data-cell="status"]').innerText(),'待找素材');await open(2);assert.equal(await page.locator('#drawer .timeline').innerText().then(t=>t.includes('重试')),false);await close();checks.push('下载失败仅选中行重试');
 for(const [s,allowed,blocked] of [['regularmissing','douyin','qianchuan'],['speedmissing','qianchuan','douyin']]){await scenario(s);await nav('publish');for(const m of [allowed,blocked,'both']){await page.locator('#publish-mode').selectOption(m);await page.locator('.excluded summary').click();const first=page.locator('.excluded div').filter({has:page.locator('strong',{hasText:'燕麦脆片组合'})});assert.equal(await first.count(),m===allowed?0:1);}}
 checks.push('普通/变速不足三模式分别阻止');
 await readinessMatrix();checks.push('15项ready按钮模式矩阵');
 await scenario('six');await nav('publish');await page.locator('#publish-mode').selectOption('both');assert.deepEqual(await page.locator('[data-batch]').evaluateAll(es=>es.map(e=>e.dataset.batch)),['1','2']);await page.locator('[data-handoff="1"]').click();assert.deepEqual(await page.locator('[data-batch]').evaluateAll(es=>es.map(e=>e.dataset.batch)),['2']);await page.locator('[data-handoff="2"]').click();assert.equal(await page.locator('[data-handoff]').count(),0);await nav('tasks');await count(12);await page.locator('#import-demo').click();await page.locator('#import-demo').click();await count(12);await page.locator('#reset').click();assert.match(await row(1).locator('[data-cell="status"]').innerText(),/成片待交接/);checks.push('稳定第二批与导入幂等、重置');
 for(const viewport of [{width:1440,height:1000},{width:1920,height:1080},{width:800,height:1000}]){
  await page.setViewportSize(viewport);await scenario('six');await nav('tasks');await page.locator('[data-view="progress"]').click();
  for(const pageName of ['tasks','sources','publish','hosts','diagrams']){await nav(pageName);await layoutShot(pageName+'-'+viewport.width+'x'+viewport.height);}
  await nav('tasks');await page.locator('[data-view="original"]').click();await layoutShot('original-'+viewport.width+'x'+viewport.height);
  await page.locator('[data-view="progress"]').click();await open(1);await layoutShot('drawer-'+viewport.width+'x'+viewport.height);await close();await drawerKeyboard();
 }
 checks.push('三尺寸五页面/原表/抽屉截图、全页overflow与键盘');
 const post=await page.request.post(base);assert.equal(post.status(),405);
 for(const route of ['/../.git/config','/%2e%2e/.git/config','/%252e%252e/.git/config','/%2e%2e%5c.git%5cconfig','/plan/README.md','/.git/config']){const r=await page.request.get(base+route);assert.equal(r.status(),404,route);}
 checks.push('HTTP写入拒绝与路径绕过404');assert.deepEqual(external,[]);assert.deepEqual(errors,[]);checks.push('无外部网络、console错误或pageerror');
 assert.deepEqual(findings,[]);console.log(JSON.stringify({ok:true,checks,errors,external},null,2));
} catch(e){await page.screenshot({path:'test-results/failure.png',fullPage:true});console.error(JSON.stringify({ok:false,checks,errors,external,error:e.stack},null,2));process.exitCode=1;}finally{await browser.close();}
