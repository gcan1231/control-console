import {ACCOUNTS, SCENARIOS} from './data/fixtures.js';
import {HEADERS, categoryCheck, readiness, artifactPaths} from './modules/contract.js';
import {MockProvider, STAGES} from './modules/mock-provider.js';
import {escape, icon, badge} from './modules/ui.js';

const provider = new MockProvider();
const content = document.querySelector('#page-content');
const drawer = document.querySelector('#drawer');
let page = 'tasks', view = 'progress', mode = 'both';
let accountFilter = '', stageFilter = '', search = '', selected = null, diagram = 'architecture';
let drawerOpener = null, composing = false;
const portInputs = {};
const pageInfo = {
  tasks: ['任务总表', '查看每日任务、处理阻塞并跟踪交接进度。'],
  sources: ['数据快照', '核对商品分类与账号允许品类，在线来源始终只读。'],
  publish: ['发文交接', '按账号准备五槽交接，分别检查普通成片和变速成片。'],
  hosts: ['执行主机', '先完成本机流程，局域网分工在后续计划中接入。'],
  diagrams: ['流程与架构', '查看整体流程、人工确认环节和后续模块。'],
};
const account = id => ACCOUNTS.find(a => a.id === id);
const resultTone = task => !categoryCheck(task, provider.snapshots).ok || task.stage === 'failed' ? 'danger'
  : task.stage === 'done' ? 'green' : task.stage === 'reported' ? 'amber'
  : task.stage === 'ready' ? (readiness(task, mode, provider.snapshots).ok ? 'green' : 'amber') : 'blue';
const statusText = task => !categoryCheck(task, provider.snapshots).ok ? '品类校验阻塞'
  : task.stage === 'ready' && !readiness(task, mode, provider.snapshots).ok ? '成片不足' : STAGES[task.stage];

function toast(message) {
  const el = document.querySelector('#toast');
  el.textContent = message;
  el.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { el.hidden = true; }, 4200);
}
function progress(task) {
  const level = {material:0, review:0, failed:0, editing_wait:1, editing:1, ready:2, publishing:3, reported:3, done:4}[task.stage];
  return `<div class="mini-flow" aria-label="素材、剪辑、交接、核验">${['素材','剪辑','交接','核验'].map((label, i) =>
    `${i ? icon('chevron').replace('class="icon"', 'class="icon flow-separator"') : ''}<span class="${i < level ? 'complete' : i === level ? 'current' : ''}">${i < level ? icon('check') : ''}<small>${label}</small></span>`
  ).join('')}</div>`;
}
function stats() {
  const tasks = provider.list();
  const blocked = tasks.filter(t => !categoryCheck(t, provider.snapshots).ok || t.stage === 'failed').length;
  const items = [
    ['今日任务', tasks.length, `${ACCOUNTS.length} 个账号`, ''],
    ['素材与剪辑', tasks.filter(t => ['material','review','editing_wait','editing'].includes(t.stage) && categoryCheck(t, provider.snapshots).ok).length, '条待推进', ''],
    ['交接就绪', tasks.filter(t => t.stage === 'ready' && readiness(t, mode, provider.snapshots).ok).length, '条成片齐全', ''],
    ['需要处理', blocked, '条阻塞或失败', 'attention'],
  ];
  return `<div class="stats" aria-label="任务统计">${items.map(([label, number, note, cls]) => `<article class="${cls}"><p>${label}</p><strong>${number}<small>${note}</small></strong></article>`).join('')}</div>`;
}
function filteredTasks() {
  return provider.list().filter(t =>
    (!accountFilter || t.accountId === accountFilter) &&
    (!stageFilter || (stageFilter === 'blocked' ? !categoryCheck(t, provider.snapshots).ok : t.stage === stageFilter)) &&
    (!search || [t.fields[2], t.fields[4], t.fields[5]].some(v => v.includes(search)))
  );
}
function taskRows(tasks) {
  if (!tasks.length) return `<tr><td colspan="${view === 'original' ? 17 : 7}" class="empty">当前筛选没有任务。清除筛选后查看全部任务。</td></tr>`;
  return tasks.map(t => {
    const check = categoryCheck(t, provider.snapshots);
    const row = `data-task="${t.id}" tabindex="0" aria-label="查看${escape(t.fields[4])}详情"`;
    if (view === 'original') return `<tr ${row}><td class="sticky-status" data-cell="status">${badge(statusText(t), resultTone(t))}</td>${t.fields.map((value, i) => `<td data-field-index="${i}" class="${[3,5,9].includes(i) ? 'mono' : ''}" translate="no">${escape(value || '—').replace(/\n/g, '<br>')}</td>`).join('')}</tr>`;
    return `<tr ${row}>
      <td><strong>${escape(t.fields[0])}</strong><small>${escape(t.fields[1])}</small></td>
      <td><div class="account-cell"><span class="account-avatar" aria-hidden="true">${escape(t.fields[2][0])}</span><div><strong>${escape(t.fields[2])}</strong><small class="mono" translate="no">${escape(t.fields[3])}</small></div></div></td>
      <td><strong class="product-title" title="${escape(t.fields[4])}">${escape(t.fields[4])}</strong><small class="mono product-id" translate="no">${escape(t.fields[5])}</small></td>
      <td>${badge(t.category, check.ok ? 'neutral' : 'danger')}<small>${check.ok ? 'F 列范围内' : '需处理分类证据'}</small></td>
      <td>${progress(t)}</td>
      <td data-cell="status">${badge(statusText(t), resultTone(t))}${t.error ? `<small class="error-text">${escape(t.error)}</small>` : ''}</td>
      <td><button class="text-button" data-task="${t.id}" aria-label="查看${escape(t.fields[4])}详情">详情${icon('chevron')}</button></td>
    </tr>`;
  }).join('');
}
function renderTasks() {
  const tasks = filteredTasks();
  const header = view === 'original'
    ? `<th class="sticky-status" scope="col">独立运行状态</th>${HEADERS.map((label, i) => `<th scope="col" data-field-index="${i}"><small>${String.fromCharCode(65 + i)}</small>${label}</th>`).join('')}`
    : '<th scope="col">日期 / 运营人</th><th scope="col">抖音账号</th><th scope="col">作品描述 / 商品短标题</th><th scope="col">品类核验</th><th scope="col">协同进度</th><th scope="col">当前状态</th><th scope="col"><span class="sr-only">操作</span></th>';
  return `${stats()}<section class="panel">
    <div class="panel-heading"><div><h2>每日运营任务<span class="muted-label">10 月 13 日 · 演示</span></h2></div><div class="segmented" aria-label="任务视图"><button data-view="progress" aria-pressed="${view === 'progress'}" class="${view === 'progress' ? 'selected' : ''}">协同进度</button><button data-view="original" aria-pressed="${view === 'original'}" class="${view === 'original' ? 'selected' : ''}">原表 16 列</button></div></div>
    <div class="filters"><label class="search-box" for="search">${icon('search')}<span class="sr-only">搜索账号、商品或完整 ID</span><input id="search" name="task-search" type="search" autocomplete="off" spellcheck="false" placeholder="搜索账号、商品或完整 ID…" value="${escape(search)}"></label>
      <select id="account-filter" name="account-filter" aria-label="筛选账号"><option value="">全部账号</option>${ACCOUNTS.map(a => `<option value="${a.id}" ${accountFilter === a.id ? 'selected' : ''}>${escape(a.name)}</option>`).join('')}</select>
      <select id="stage-filter" name="stage-filter" aria-label="筛选阶段"><option value="">全部阶段</option>${Object.entries({...STAGES, blocked:'品类校验阻塞'}).map(([value, label]) => `<option value="${value}" ${stageFilter === value ? 'selected' : ''}>${label}</option>`).join('')}</select>
      <button id="clear-filters" class="text-button">${icon('reset')}清除筛选</button><span class="filtered-count" aria-live="polite">${tasks.length} 条任务</span>
    </div>
    <div class="table-scroll ${view === 'original' ? 'wide-sheet' : ''}" tabindex="0" aria-label="${view === 'original' ? '原表完整16列，可横向滚动' : '协同进度任务表'}"><table id="task-table" class="${view === 'progress' ? 'progress-table' : ''}"><thead><tr>${header}</tr></thead><tbody>${taskRows(tasks)}</tbody></table></div>
    <div class="panel-footer"><span>原始表保留，工作副本隔离</span><span>点击任务查看详情 · J 列提示原值不变</span></div>
  </section>`;
}
// Keep the input node intact so typing and IME composition never lose their caret.
function updateTaskRows() {
  const table = document.querySelector('#task-table');
  if (!table) return;
  const tasks = filteredTasks();
  table.tBodies[0].innerHTML = taskRows(tasks);
  document.querySelector('.filtered-count').textContent = `${tasks.length} 条任务`;
}
function renderSources() {
  const sources = [
    ['products', '公司商品库', '商品 ID 对应有来源的商品类别', '完整分类副本', 'database'],
    ['collaboration', '每日协同表', '每日任务的外部参考，不写回运行日志', '人工取得副本', 'table'],
    ['accounts', '飞书账号资料', '账号身份、UID、合作码和 F 列允许品类', '账号允许品类副本', 'people'],
  ];
  return `<div class="info-callout">${icon('info')}<div><strong>在线来源只读，使用人工取得的本地副本。</strong><p>缺少商品分类或账号 F 列证据时，后续任务会被阻止。</p></div></div>
    <div class="source-grid">${sources.map(([key, title, desc, type, symbol]) => {
      const ready = provider.snapshots[key];
      return `<article class="panel source-card"><span class="source-symbol">${icon(symbol)}</span><div class="source-top"><h2>${title}</h2>${badge('只读')}</div><p>${desc}</p><dl><dt>数据来源</dt><dd>${type}（演示）</dd><dt>快照时间</dt><dd>2026-10-13 09:00 · 虚构</dd><dt>导入状态</dt><dd>${badge(ready ? '证据已齐全' : '分类证据缺失', ready ? 'green' : 'danger')}</dd></dl><button class="secondary" data-source="${key}">${icon('upload')}模拟导入完整副本</button></article>`;
    }).join('')}</div>
    <section class="panel category-panel"><div class="panel-heading"><div><h2>账号允许品类</h2><p>对应账号表 F 列，以下均为虚构示例。</p></div>${badge('缺证据或错配即阻止', 'amber')}</div><div class="category-scroll"><table><thead><tr><th scope="col">抖音名称</th><th scope="col">账号标识</th><th scope="col">F 列允许品类</th><th scope="col">品类规则</th></tr></thead><tbody>${ACCOUNTS.map(a => `<tr><td><strong>${escape(a.name)}</strong></td><td class="mono" translate="no">${a.id}</td><td>${badge(a.allowed.join('、'))}</td><td>只接受有来源的对应类别</td></tr>`).join('')}</tbody></table></div></section>`;
}
function renderPublish() {
  const {batches, excluded} = provider.packages(mode);
  const packs = batches.map(pack => `<section class="panel handoff-panel" data-batch="${pack.number}">
    <div class="panel-heading"><div><h2>交接批次 ${String(pack.number).padStart(2, '0')}<span class="muted-label">${pack.slots.filter(s => s.account).length} 个账号</span></h2><p class="mono" translate="no">视频根：${escape(pack.root)}</p></div><button class="primary" data-handoff="${pack.number}">${icon('check')}模拟确认本批交接</button></div>
    <div class="slot-grid">${pack.slots.map(slot => slot.account ? `<article class="slot"><div class="slot-heading"><span>槽 ${slot.slot}</span>${badge('就绪', 'green')}</div><h3>${escape(slot.account.name)}</h3><p>${slot.tasks.length} 条任务 · ${slot.sheet}</p>
      <label>实际端口（独立配置）<input class="port-input" name="port-${slot.account.id}" data-port="${slot.account.id}" aria-label="${escape(slot.account.name)}实际端口" value="${escape(portInputs[slot.account.id] || '')}" placeholder="待人工确认…" inputmode="numeric" autocomplete="off" spellcheck="false"></label>
      <small>J 列记忆提示 ${escape(slot.portHint)} · 原值保留</small><div class="slot-files"><span>普通<strong>${slot.tasks.reduce((n, t) => n + t.normal, 0)}</strong></span><span>变速<strong>${slot.tasks.reduce((n, t) => n + t.speed, 0)}</strong></span></div>
      <details><summary>查看交接路径</summary><p class="mono break" translate="no">${escape(slot.table)}</p><p class="mono break" translate="no">${escape(artifactPaths(slot.tasks[0]).normal)}<br>${escape(artifactPaths(slot.tasks[0]).speed)}</p></details></article>`
      : `<article class="slot inactive"><div class="slot-heading">槽 ${slot.slot}</div><span class="unused-icon">${icon('plus')}</span><h3>未分配</h3><p>没有就绪任务<br>不生成空任务文件</p></article>`).join('')}</div>
    <div class="panel-footer"><span>核对账号和端口后，在原发文软件设置并启动</span><span>仅预览，不生成真实 XLSX</span></div></section>`).join('');
  return `<div class="publish-toolbar"><div><h2>交接目标</h2><p>普通成片给千川，变速成片给抖音。</p></div><select id="publish-mode" name="publish-mode" aria-label="交接模式"><option value="both" ${mode === 'both' ? 'selected' : ''}>抖音 + 千川</option><option value="douyin" ${mode === 'douyin' ? 'selected' : ''}>仅抖音（变速）</option><option value="qianchuan" ${mode === 'qianchuan' ? 'selected' : ''}>仅千川（普通）</option></select></div>
    <div class="info-callout">${icon('info')}<div><strong>每槽一个账号，一份独立 XLSX。</strong><p>五槽共享视频根目录；J 列仅提供记忆提示，实际端口由人工独立配置。</p></div></div>
    ${packs || `<section class="panel empty-state">${icon('upload')}<h2>当前没有可交接的任务</h2><p>检查分类证据、对应成片及任务状态。已交接任务不会再次导出。</p><button class="secondary" data-page="tasks">返回任务总表</button></section>`}
    <details class="excluded panel"><summary>未进入交接的任务 · ${excluded.length} 条</summary>${excluded.map(e => `<div data-excluded-task="${e.taskId}"><strong>${escape(provider.get(e.taskId).fields[4])}</strong><span>${escape(e.reason)}</span></div>`).join('')}</details>
    <section class="panel path-example"><h2>成片目录</h2><div><pre translate="no">视频根 / 抖音名称 / 完整商品ID / 成片 / *.mp4</pre>${badge('千川 · 普通成片', 'blue')}</div><div><pre translate="no">视频根 / 抖音名称 / 完整商品ID / 成片 / 变速 / *.mp4</pre>${badge('抖音 · 变速成片', 'green')}</div></section>`;
}
function renderHosts() {
  return `<div class="host-grid"><article class="panel host-card"><span class="host-icon">${icon('monitor')}</span><div><div class="source-top"><h2>本机演示主机</h2>${badge('模拟', 'blue')}</div><p>LOCAL-DEMO · 仅本机网页</p><div class="capabilities">${badge('任务工作台')}${badge('素材交接规划')}${badge('剪辑交接规划')}</div><p>当前没有注册执行端；真实软件仍由操作人控制。</p></div></article>
    <article class="panel host-card future"><span class="host-icon">${icon('diagram')}</span><div><h2>后续局域网主机</h2><p>计划 008，单机 MVP 验收后实施。</p><div class="capabilities">${badge('主机能力')}${badge('任务领取')}${badge('心跳与租约')}${badge('文件传输')}</div><button class="secondary" disabled>主机接入尚未实现</button></div></article></div>
    <div class="info-callout">${icon('info')}<div><strong>主机与发文账号端口分别管理。</strong><p>J 列不作为主机标识、服务端口或任务 ID。</p></div></div>`;
}
function renderDiagrams() {
  return `<div class="workflow-strip" aria-label="业务步骤"><div><span class="step-circle">1</span><span>固定任务表<small>运营每日选品</small></span></div>${icon('chevron')}<div><span class="step-circle">2</span><span>找素材<small>人工预览确认</small></span></div>${icon('chevron')}<div><span class="step-circle">3</span><span>剪辑<small>原软件确认启动</small></span></div>${icon('chevron')}<div><span class="step-circle">4</span><span>发文交接<small>五槽单账号</small></span></div></div>
    <section class="panel diagram-panel"><div class="panel-heading"><div><h2>目标设计</h2><p>包含人工确认环节和后续能力，可独立打开、切换主题及导出。</p></div><div class="segmented" aria-label="图类型"><button data-diagram="architecture" aria-pressed="${diagram === 'architecture'}" class="${diagram === 'architecture' ? 'selected' : ''}">目标架构</button><button data-diagram="workflow" aria-pressed="${diagram === 'workflow'}" class="${diagram === 'workflow' ? 'selected' : ''}">业务流程</button></div><a class="secondary" href="/diagrams/${diagram}.html" target="_blank" rel="noopener">独立打开${icon('external')}</a></div><iframe title="${diagram === 'architecture' ? '目标架构图' : '业务流程图'}" src="/diagrams/${diagram}.html"></iframe></section>`;
}
function render() {
  const active = document.activeElement;
  const focus = active?.id;
  const caret = active instanceof HTMLInputElement ? [active.selectionStart, active.selectionEnd, active.selectionDirection] : null;
  const [title, subtitle] = pageInfo[page];
  document.querySelector('#page-label').textContent = title;
  document.querySelector('#page-title').textContent = title;
  document.querySelector('#page-subtitle').textContent = subtitle;
  document.querySelectorAll('nav [data-page]').forEach(button => {
    const current = button.dataset.page === page;
    button.classList.toggle('active', current);
    if (current) button.setAttribute('aria-current', 'page');
    else button.removeAttribute('aria-current');
  });
  content.innerHTML = ({tasks:renderTasks, sources:renderSources, publish:renderPublish, hosts:renderHosts, diagrams:renderDiagrams}[page])();
  if (focus && !drawer.contains(active) && document.getElementById(focus)) {
    const el = document.getElementById(focus);
    el.focus({preventScroll:true});
    if (caret?.[0] !== null && el instanceof HTMLInputElement && caret) el.setSelectionRange(...caret);
  }
  if (selected) renderDrawer();
}
const actionLabels = {
  material:'模拟搜索并等待确认', review:'模拟确认素材', editing_wait:'模拟在原剪辑软件启动',
  editing:'模拟回收剪辑报告', failed:'模拟重试素材', publishing:'模拟回收发文报告', reported:'模拟人工核验完成',
};
function renderDrawer() {
  const task = provider.get(selected);
  if (!task) { closeDrawer(); return; }
  const opening = drawer.hidden;
  const focusedAction = drawer.contains(document.activeElement) && document.activeElement.matches('.drawer-action');
  const bodyScroll = drawer.querySelector('.drawer-body')?.scrollTop || 0;
  const check = categoryCheck(task, provider.snapshots), paths = artifactPaths(task);
  drawer.hidden = false;
  document.querySelector('#drawer-backdrop').hidden = false;
  document.querySelector('main').inert = true;
  document.querySelector('.sidebar').inert = true;
  document.body.classList.add('drawer-open');
  const action = actionLabels[task.stage] ? `<button class="primary drawer-action" data-advance="${task.id}" data-stage="${task.stage}" ${!check.ok ? 'disabled' : ''}>${actionLabels[task.stage]}</button>`
    : task.stage === 'ready' ? `<button class="primary drawer-action" data-page="publish" ${!readiness(task, mode, provider.snapshots).ok ? 'disabled' : ''}>查看发文交接</button>` : '';
  drawer.innerHTML = `<div class="drawer-top"><div><h2 id="drawer-title">任务详情</h2><p>原始行 ${task.sourceRow}</p></div><button id="close-drawer" aria-label="关闭详情">${icon('close')}</button></div>
    <div class="drawer-body"><h2>${escape(task.fields[4])}</h2><p>${escape(task.fields[2])} · ${escape(task.fields[0])}</p>${badge(statusText(task), resultTone(task))}
      <div class="detail-progress">${progress(task)}</div>
      <h3>品类与账号</h3><dl><dt>商品类别</dt><dd>${escape(task.category)}</dd><dt>F 列允许品类</dt><dd>${escape(account(task.accountId).allowed.join('、'))}</dd><dt>核验结果</dt><dd class="${check.ok ? 'success-text' : 'error-text'}">${escape(check.reason)}</dd><dt>完整商品 ID</dt><dd class="mono" data-detail="product-id" translate="no">${escape(task.fields[5])}</dd><dt>J 列原值</dt><dd data-detail="j-value">${task.fields[9] ? escape(task.fields[9]) + '（仅记忆提示）' : '空白（保持原样）'}</dd></dl>
      <h3>成片与文件归属</h3><div class="artifact-cards"><div><span>普通成片 · 千川</span><strong>${task.normal} / ${task.target}<small>条</small></strong></div><div><span>变速成片 · 抖音</span><strong>${task.speed} / 1<small>条</small></strong></div></div>
      <p class="file-path mono" translate="no">${escape(paths.normal)}<br>${escape(paths.speed)}</p><small>演示文件清单，不代表本机存在这些文件。</small>
      <h3>执行记录</h3><div class="timeline">${task.events.map(e => `<div><span>${escape(e.time)}</span><p>${escape(e.text)}</p></div>`).join('')}</div>
      <h3>软件报告与核验</h3><dl><dt>M 列发文回执</dt><dd data-detail="report-m">${escape(task.fields[12] || '尚无软件回执')}</dd><dt>N 列千川回执</dt><dd data-detail="report-n">${escape(task.fields[13] || '尚无软件回执')}</dd><dt>人工核验</dt><dd data-detail="verification">${task.stage === 'done' ? '演示核验完成' : '尚未核验'}</dd></dl>
    </div><div class="drawer-footer">${action}<p class="drawer-note">所有操作为模拟。素材确认、剪辑参数与发文启动，在真实 MVP 中由现有软件和操作人完成。</p></div>`;
  if (!opening) drawer.querySelector('.drawer-body').scrollTop = bodyScroll;
  if (opening || !drawer.contains(document.activeElement)) (focusedAction ? drawer.querySelector('.drawer-action:not(:disabled)') : null)?.focus({preventScroll:true});
  if (!drawer.contains(document.activeElement)) document.querySelector('#close-drawer').focus({preventScroll:true});
}
function openDrawer(id, trigger) {
  const isRow = trigger.matches('tr');
  drawerOpener = `${isRow ? 'tr' : 'button'}[data-task="${CSS.escape(id)}"]`;
  selected = id;
  renderDrawer();
}
function closeDrawer() {
  const wasOpen = !drawer.hidden;
  selected = null;
  drawer.hidden = true;
  document.querySelector('#drawer-backdrop').hidden = true;
  document.querySelector('main').inert = false;
  document.querySelector('.sidebar').inert = false;
  document.body.classList.remove('drawer-open');
  if (wasOpen && drawerOpener) document.querySelector(drawerOpener)?.focus({preventScroll:true});
  drawerOpener = null;
}
function clearFilters() {
  accountFilter = ''; stageFilter = ''; search = ''; composing = false;
  render();
  toast('已清除筛选');
}

document.querySelectorAll('[data-icon]').forEach(el => { el.innerHTML = icon(el.dataset.icon); });
document.querySelector('#scenario').innerHTML = Object.entries(SCENARIOS).map(([key, label]) => `<option value="${key}">${label}</option>`).join('');
document.querySelector('#scenario').addEventListener('change', event => {
  provider.reset(event.target.value);
  accountFilter = ''; stageFilter = ''; search = ''; composing = false;
  closeDrawer(); render(); toast('已切换演示场景');
});
document.querySelector('#reset').addEventListener('click', () => {
  provider.reset(document.querySelector('#scenario').value);
  accountFilter = ''; stageFilter = ''; search = ''; composing = false;
  closeDrawer(); render(); toast('演示已重置');
});
document.querySelector('#import-demo').addEventListener('click', () => toast(provider.importDemo().message));
document.querySelector('#drawer-backdrop').addEventListener('click', closeDrawer);
document.addEventListener('keydown', event => {
  if (!drawer.hidden) {
    if (event.key === 'Escape') { event.preventDefault(); closeDrawer(); return; }
    if (event.key === 'Tab') {
      const stops = [...drawer.querySelectorAll('button:not(:disabled), a[href], input:not(:disabled), select:not(:disabled), summary, [tabindex="0"]')].filter(el => el.getClientRects().length);
      const first = stops[0], last = stops.at(-1);
      if (!stops.length) { event.preventDefault(); drawer.focus(); }
      else if (event.shiftKey && (document.activeElement === first || !drawer.contains(document.activeElement))) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && (document.activeElement === last || !drawer.contains(document.activeElement))) { event.preventDefault(); first.focus(); }
    }
    return;
  }
  if (event.key === 'Enter' && event.target.matches('tr[data-task]')) {
    event.preventDefault(); openDrawer(event.target.dataset.task, event.target);
  }
});
document.addEventListener('click', event => {
  const button = event.target.closest('[data-page],[data-view],[data-task],[data-advance],[data-handoff],[data-source],[data-diagram],#close-drawer,#clear-filters');
  if (!button || button.disabled) return;
  if (button.id === 'close-drawer') { closeDrawer(); return; }
  if (button.id === 'clear-filters') { clearFilters(); return; }
  if (button.dataset.page) { page = button.dataset.page; closeDrawer(); render(); return; }
  if (button.dataset.view) { view = button.dataset.view; render(); return; }
  if (button.dataset.advance) { toast(provider.advance(button.dataset.advance, button.dataset.stage).message); render(); return; }
  if (button.dataset.handoff) { toast(provider.handoff(Number(button.dataset.handoff), mode).message); render(); return; }
  if (button.dataset.source) { provider.snapshots[button.dataset.source] = true; render(); toast('完整副本已模拟导入，在线文档未改变'); return; }
  if (button.dataset.diagram) { diagram = button.dataset.diagram; render(); return; }
  if (button.dataset.task) openDrawer(button.dataset.task, button);
});
content.addEventListener('compositionstart', event => { if (event.target.id === 'search') composing = true; });
content.addEventListener('compositionend', event => {
  if (event.target.id === 'search') { composing = false; search = event.target.value; updateTaskRows(); }
});
content.addEventListener('input', event => {
  if (event.target.id === 'search') {
    search = event.target.value;
    if (!composing && !event.isComposing) updateTaskRows();
  }
  if (event.target.dataset.port) portInputs[event.target.dataset.port] = event.target.value;
});
content.addEventListener('change', event => {
  if (event.target.id === 'account-filter') accountFilter = event.target.value;
  else if (event.target.id === 'stage-filter') stageFilter = event.target.value;
  else if (event.target.id === 'publish-mode') mode = event.target.value;
  else return;
  render();
});
render();
