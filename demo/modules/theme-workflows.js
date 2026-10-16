/* Rearranges fictional view markup only. No files, business APIs or persistence. */
const markup = node => node ? node.outerHTML : '';
const contents = node => node ? node.innerHTML : '';
const heading = text => `<div class="workflow-region-heading"><h2>${text}</h2></div>`;
const panel = (name, title, body) => `<section class="panel ${name}">${heading(title)}${body}</section>`;

function transformMatches(result, mode) {
  const table = result.querySelector('.intake-table');
  if (!table) return;
  const list = document.createElement('div');
  list.className = mode === 'cards' ? 'workflow-match-cards' : 'workflow-inspector-records';
  for (const row of table.tBodies[0].rows) {
    const cells = [...row.cells].map(contents);
    const article = document.createElement('article');
    article.className = mode === 'cards' ? 'workflow-match-card' : 'workflow-inspector-record';
    article.innerHTML = mode === 'cards'
      ? `<div class="workflow-match-identity">${cells[0]}</div><div class="workflow-match-files"><h3>文件清单</h3>${cells[1]}</div><div class="workflow-match-evidence"><h3>来源证据</h3>${cells[2]}</div><div class="workflow-match-status">${cells[3]}</div>`
      : `<div class="workflow-record-summary">${cells[0]}<h3>文件清单</h3>${cells[1]}</div><div class="workflow-record-evidence"><h3>来源与核对</h3>${cells[2]}${cells[3]}</div>`;
    list.append(article);
  }
  table.closest('.table-scroll').replaceWith(list);
}

function candidateTable(result) {
  const candidates = [...result.querySelectorAll('.candidate')];
  if (!candidates.length) return;
  const wrap = document.createElement('div');
  wrap.className = 'table-scroll';
  wrap.innerHTML = `<table class="data-table workflow-candidate-table"><thead><tr><th scope="col">软件候选</th><th scope="col">运行状态</th><th scope="col">软件位置</th><th scope="col">数据目录</th></tr></thead><tbody></tbody></table>`;
  const body = wrap.querySelector('tbody');
  for (const candidate of candidates) {
    const values = [...candidate.querySelectorAll('dd')].map(contents);
    const row = document.createElement('tr');
    row.innerHTML = `<td>${contents(candidate.querySelector('.candidate-heading'))}</td>${values.map(value => `<td>${value}</td>`).join('')}`;
    body.append(row);
  }
  candidates[0].before(wrap);
  candidates.forEach(candidate => candidate.remove());
}

function inspectorList(items, selected) {
  const list = document.createElement('div');
  list.className = 'workflow-entry-list';
  list.setAttribute('aria-label', '选择要检视的项目');
  items.forEach((item, index) => {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'workflow-entry-button';
    button.dataset.workflowInspect = String(index);
    button.setAttribute('aria-pressed', String(index === selected));
    const title = document.createElement('strong');
    title.textContent = item[0];
    const detail = document.createElement('small');
    detail.textContent = item[1];
    button.append(title, detail);
    list.append(button);
  });
  return markup(list);
}

function inspectResult(result, view, selected) {
  const records = [...result.querySelectorAll(view === 'discovery' ? '.candidate' : '.intake-table tbody tr')];
  if (!records.length) return '';
  const index = Math.max(0, Math.min(records.length - 1, Number(selected) || 0));
  const entries = records.map(record => view === 'discovery'
    ? [record.querySelector('strong')?.textContent || '软件候选', record.querySelector('dd')?.textContent || '待核对']
    : [record.cells[0].querySelector('strong')?.textContent || '匹配任务', record.cells[1].firstChild?.textContent || '文件清单']);
  const list = inspectorList(entries, index);
  if (view === 'discovery') {
    const checked = records.find(record => record.querySelector('input[type="radio"]')?.checked);
    const selection = document.createElement('p');
    selection.className = 'workflow-selection-summary';
    selection.textContent = checked
      ? `当前保存对象：${checked.querySelector('strong')?.textContent || '已选候选'}。左侧检视只切换详情，不改变保存对象。`
      : '当前尚未选择保存对象。左侧检视只切换详情，请在候选详情中勾选后保存。';
    result.querySelector('#save-demo-connection')?.before(selection);
    records.forEach((record, position) => { if (position !== index) record.remove(); });
    records[index].classList.add('workflow-inspector-candidate');
  } else {
    const overview = document.createElement('section');
    overview.className = 'workflow-verification-overview';
    const title = document.createElement('h3');
    title.textContent = `本次统一核验全部 ${records.length} 个任务`;
    const explanation = document.createElement('p');
    explanation.textContent = '右侧详情每次检视一项；下方确认涵盖以下全部任务，请逐项核对文件、内容及归属。';
    const items = document.createElement('ul');
    for (const record of records) {
      const item = document.createElement('li');
      const name = record.cells[0].querySelector('strong')?.textContent || '匹配任务';
      const id = record.cells[0].querySelector('.id')?.textContent || '';
      const files = record.cells[1].firstChild?.textContent || '';
      const status = record.cells[3].querySelector('.badge')?.textContent || '待核对';
      item.textContent = `${name} / ${id} · ${files} · ${status}`;
      items.append(item);
    }
    overview.append(title, explanation, items);
    result.querySelector('.intake-confirm')?.prepend(overview);
    const cells = [...records[index].cells].map(contents);
    const article = document.createElement('article');
    article.className = 'workflow-inspector-record';
    article.innerHTML = `<div class="workflow-record-summary">${cells[0]}<h3>文件清单</h3>${cells[1]}</div><div class="workflow-record-evidence"><h3>来源与核对</h3>${cells[2]}${cells[3]}</div>`;
    const holder = document.createElement('div');
    holder.className = 'workflow-inspector-records';
    holder.append(article);
    result.querySelector('.table-scroll').replaceWith(holder);
  }
  return list;
}

function wizard(view, step, blocks) {
  const labels = view === 'discovery' ? ['识别软件', '核对候选', '保存连接'] : ['选择资料', '匹配任务', '人工核验'];
  return `<nav class="workflow-wizard-nav" aria-label="${view === 'discovery' ? '连接' : '接入'}工作步骤">${labels.map((label, index) => `<button type="button" class="workflow-step-button" data-workflow-step="${index + 1}" ${step === index + 1 ? 'aria-current="step"' : ''}><span>${index + 1}</span><strong>${label}</strong></button>`).join('')}</nav><div class="workflow-wizard-body">${blocks.map((block, index) => `<section class="panel workflow-stage" data-workflow-stage="${index + 1}" ${step === index + 1 ? '' : 'hidden'}>${heading(labels[index])}${block}<div class="workflow-step-actions">${index ? `<button type="button" class="button" data-workflow-step="${index}">上一步</button>` : ''}${index < 2 ? `<button type="button" class="button" data-workflow-step="${index + 2}">下一步：${labels[index + 1]}</button>` : ''}</div></section>`).join('')}</div>`;
}

/** Preserve every original control and its IDs while changing the information structure. */
export function composeWorkflow(theme, view, html, step = 1, selected = 0) {
  if (!['discovery', 'intake'].includes(view)) return html;
  const template = document.createElement('template');
  template.innerHTML = html;
  const root = template.content;
  const variant = ['A', 'B', 'C', 'D', 'E'].includes(theme) ? theme : 'A';
  let intro = '', controls = '', output = '', support = '', footer = '', steps = '';
  let confirmation = '', entries = '';
  if (view === 'discovery') {
    intro = markup(root.querySelector('.setup-intro'));
    controls = markup(root.querySelector('.setup-controls'));
    const results = root.querySelector('.setup-result');
    if (variant === 'B') candidateTable(results);
    if (variant === 'E') entries = inspectResult(results, view, selected);
    if (variant === 'D') {
      const save = results.querySelector('#save-demo-connection');
      confirmation = save ? markup(save) : '<p class="muted">先识别并核对候选，确认可用后保存演示连接。</p>';
      save?.remove();
    }
    output = markup(results);
    support = markup(root.querySelector('.setup-help')) + markup(root.querySelector('details')) + markup(root.querySelector('.stack > .notice'));
  } else {
    steps = markup(root.querySelector('.stepper'));
    controls = markup(root.querySelector('.intake-header')) + markup(root.querySelector('.source-pickers'));
    footer = markup(root.querySelector('.panel-footer'));
    const results = root.querySelector('#intake-results');
    if (variant === 'A') transformMatches(results, 'cards');
    if (variant === 'E') entries = inspectResult(results, view, selected);
    if (variant === 'D') {
      const confirm = results.querySelector('.intake-confirm');
      confirmation = confirm ? markup(confirm) : '<p class="muted">选择资料并预览匹配结果后，可在此人工核验并接纳。</p>';
      confirm?.remove();
    }
    output = markup(results);
  }
  const content = variant === 'A'
    ? `${steps}<div class="workflow-card-grid">${panel('workflow-controls-card', view === 'discovery' ? '识别与连接' : '接入资料', intro + controls + footer)}${panel('workflow-results-card', view === 'discovery' ? '软件候选' : '成果核对', output)}${support ? `<div class="workflow-support-card">${support}</div>` : ''}</div>`
    : variant === 'B'
    ? `<section class="panel workflow-toolbar">${intro}${controls}${footer}</section><section class="workflow-table-region">${output}</section><div class="workflow-support-strip">${steps}${support}</div>`
    : variant === 'C'
    ? `<section class="panel workflow-command-deck">${intro}${controls}${footer}</section><section class="workflow-output-deck">${heading(view === 'discovery' ? '识别结果工作区' : '成果匹配工作区')}${output}</section><div class="workflow-reference-deck">${steps}${support}</div>`
    : variant === 'D'
    ? wizard(view, Math.max(1, Math.min(3, Number(step) || 1)), [intro + controls + footer, output, confirmation + `<div class="workflow-guidance">${support}</div>`])
    : `<div class="workflow-inspector-layout"><aside class="panel workflow-source-list">${heading(view === 'discovery' ? '软件来源' : '接入来源')}${intro}${controls}${footer}${entries}${steps}</aside><section class="workflow-evidence-pane">${heading(view === 'discovery' ? '候选与定位证据' : '文件与归属证据')}${output}${support ? `<div class="workflow-guidance">${support}</div>` : ''}</section></div>`;
  return `<div class="scheme-workflow scheme-${variant}" data-workflow-layout="${variant}" data-workflow-view="${view}">${content}</div>`;
}
