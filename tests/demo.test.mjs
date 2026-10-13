import test from 'node:test';
import assert from 'node:assert/strict';
import {HEADERS,createTasks} from '../demo/data/fixtures.js';
import {buildBatches,businessRows,readiness} from '../demo/modules/contract.js';
import {MockProvider} from '../demo/modules/mock-provider.js';

test('固定16列表头、19位文本ID和J提示/空白保真',()=>{
  assert.deepEqual(HEADERS,['日期','运营人','抖音名称','抖音账号','作品描述/商品短标题','商品ID','企微群名称','商品链接','账号合作码/uid','视频链接','店铺名称','剪辑软件日志','发文软件日志','千川塞素材软件日志','检查软件日志','企微同步软件日志']);
  const tasks=createTasks('six');const rows=businessRows(tasks);
  assert.equal(rows.length,12);
  for(let i=0;i<12;i++){assert.equal(rows[i].length,16);assert.equal(typeof rows[i][5],'string');assert.equal(rows[i][5],'90000000000000000'+String(i+1).padStart(2,'0'));assert.equal(rows[i][5].length,19);assert.equal(rows[i][9],i%2===0?'700'+(Math.floor(i/2)+1):'');}
  rows[0][9]='changed';assert.equal(tasks[0].fields[9],'7001');
});
test('不同账号同商品保持独立任务',()=>{
  const tasks=createTasks('six');tasks[2].fields[5]=tasks[0].fields[5];
  const slots=buildBatches(tasks).batches.flatMap(b=>b.slots);assert.equal(slots.flatMap(s=>s.tasks).length,12);
  assert.notEqual(slots[0].tasks[0].id,slots[1].tasks[0].id);assert.equal(slots[0].tasks[0].fields[5],slots[1].tasks[0].fields[5]);
});
for(const scenario of ['categorymissing','categorywrong'])test(`${scenario}阻止阶段推进与该行交接`,()=>{
  const p=new MockProvider(scenario);const t=p.tasks[0];t.stage='material';assert.equal(p.advance(t.id,'material').ok,false);assert.equal(t.stage,'material');t.stage='ready';
  assert.equal(readiness(t,'both',p.snapshots).ok,false);assert.ok(p.packages('both').excluded.some(e=>e.taskId===t.id));p.handoff(1,'both');assert.equal(t.stage,'ready');
});
for(const [scenario,allowed,blocked] of [['regularmissing','douyin','qianchuan'],['speedmissing','qianchuan','douyin']])test(`${scenario}按目标模式检查对应产物`,()=>{
  const p=new MockProvider(scenario),t=p.tasks[0];assert.equal(readiness(t,allowed,p.snapshots).ok,true);for(const mode of [blocked,'both'])assert.equal(readiness(t,mode,p.snapshots).ok,false);
  assert.ok(p.packages(allowed).batches[0].slots[0].tasks.includes(t));assert.ok(p.packages(blocked).excluded.some(e=>e.taskId===t.id));
});
test('六账号12行形成5+1槽并保持原行顺序、稳定批号及J',()=>{
  const p=new MockProvider('six');const originalJ=p.tasks.map(t=>t.fields[9]);const batches=p.packages('both').batches;
  assert.deepEqual(batches.map(b=>b.number),[1,2]);assert.deepEqual(batches.map(b=>b.slots.filter(s=>s.account).length),[5,1]);
  for(const b of batches){assert.equal(b.slots.length,5);for(const s of b.slots){if(!s.account){assert.deepEqual(s.tasks,[]);assert.equal(s.table,null);continue;}assert.equal(s.sheet,'Sheet1');assert.ok(s.tasks.every(t=>t.accountId===s.account.id));assert.deepEqual(s.tasks.map(t=>t.sourceRow),p.tasks.filter(t=>t.accountId===s.account.id).map(t=>t.sourceRow));}}
  assert.equal(new Set(batches.flatMap(b=>b.slots.filter(s=>s.account).map(s=>s.table))).size,6);
  assert.equal(p.handoff(1,'both').ok,true);assert.deepEqual(p.packages('both').batches.map(b=>b.number),[2]);assert.equal(p.handoff(2,'both').ok,true);assert.equal(p.tasks.filter(t=>t.stage==='publishing').length,12);assert.deepEqual(p.tasks.map(t=>t.fields[9]),originalJ);
});
test('publishing/reported/done均不能再次导出',()=>{
  const p=new MockProvider('six');p.handoff(1,'both');const t=p.tasks[0];for(const stage of ['publishing','reported','done']){t.stage=stage;assert.ok(p.packages('both').excluded.some(e=>e.taskId===t.id));assert.ok(!p.packages('both').batches.flatMap(b=>b.slots).flatMap(s=>s.tasks).includes(t));}const events=t.events.length;p.handoff(1,'both');assert.equal(t.events.length,events);
});
test('expectedStage忽略重复推进，失败重试只影响失败行',()=>{
  const p=new MockProvider('happy'),t=p.tasks[0];assert.equal(p.advance(t.id,'material').ok,true);const snapshot=structuredClone(t);assert.equal(p.advance(t.id,'material').ok,false);assert.deepEqual(t,snapshot);
  p.reset('materialfail');const others=structuredClone(p.tasks.slice(1));assert.equal(p.advance(p.tasks[0].id,'failed').ok,true);assert.equal(p.tasks[0].stage,'material');assert.equal(p.tasks[0].attempt,2);assert.equal(p.tasks[0].error,'');assert.deepEqual(p.tasks.slice(1),others);
});
for(const mode of ['douyin','qianchuan'])test(`${mode}回执仅写对应列，reported仍需人工模拟核验`,()=>{
  const p=new MockProvider('six');p.handoff(1,mode);const t=p.tasks[0];assert.equal(p.advance(t.id,'publishing').ok,true);assert.equal(t.stage,'reported');assert.equal(t.fields[12],mode==='douyin'?'已发布':'');assert.equal(t.fields[13],mode==='qianchuan'?'计划已发布':'');assert.equal(p.advance(t.id,'publishing').ok,false);assert.equal(t.stage,'reported');assert.equal(p.advance(t.id,'reported').ok,true);assert.equal(t.stage,'done');assert.match(t.events.at(-1).text,/模拟人工核验/);
});
test('重复导入不创建任务',()=>{const p=new MockProvider('happy');const before=structuredClone(p.tasks);for(let i=0;i<3;i++)assert.equal(p.importDemo().ok,true);assert.deepEqual(p.tasks,before);});
