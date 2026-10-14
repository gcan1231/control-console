import {SelectionDemoProvider,HEADERS,DEMO_DATE} from './selection-provider.js';
import {renderSelectionPage} from './selection-views.js';
import {escape as e,icon} from './ui.js';

const provider=new SelectionDemoProvider(),root=document.querySelector('#selection-root'),dialog=document.querySelector('#selection-dialog'),notice=document.querySelector('#selection-notice');
const variant=new URL(location.href).searchParams.get('variant');
const ui={variant:['A','B','C'].includes(variant)?variant:'A',accountId:'a1',date:DEMO_DATE,operator:'inherit',range:'latest',search:'',showOther:false,presetId:'daily',message:''};
let composing=false,returnSelector='',previewToken='',toastTimer;
function selectorFor(node){if(!node)return '';for(const key of ['data-sel-control','data-sel-variant','data-sel-add','data-sel-edit','data-sel-remove','data-sel-detail','data-sel-action'])if(node.hasAttribute(key))return `[${key}="${CSS.escape(node.getAttribute(key))}"]`;return '';}
function render(){const active=document.activeElement,selector=selectorFor(active),position=active?.selectionStart;
  root.innerHTML=renderSelectionPage({data:provider.state(),ui:{...ui},catalog:provider.catalog({accountId:ui.accountId,date:ui.date,range:ui.range,search:ui.search,showOther:ui.showOther}),counts:provider.counts(ui.accountId,ui.date)});
  const next=selector?root.querySelector(selector):null;if(next&&!next.disabled){next.focus({preventScroll:true});if(typeof position==='number'&&['text','search'].includes(next.type))next.setSelectionRange(position,position);}
}
function notify(text,error=false){notice.textContent=text;notice.dataset.error=String(error);notice.setAttribute('role',error?'alert':'status');notice.hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>notice.hidden=true,6500);}
function openDialog(title,body,footer=''){returnSelector=selectorFor(document.activeElement);dialog.innerHTML=`<div class="sel-dialog-head"><h2 id="selection-dialog-title">${e(title)}</h2><button type="button" data-dialog-close aria-label="关闭对话框">${icon('close')}</button></div><div class="sel-dialog-body">${body}<p class="sel-dialog-message" role="alert" tabindex="-1" hidden></p></div>${footer?`<div class="sel-dialog-footer">${footer}</div>`:''}`;dialog.showModal();}
function dialogError(error){const n=dialog.querySelector('.sel-dialog-message');if(n){n.textContent=error.message;n.hidden=false;n.focus();}else notify(error.message,true);}
function contractTable(rows){return `<div class="sel-scroll" tabindex="0" role="region" aria-label="固定16列任务表，可横向滚动"><table class="sel-contract"><thead><tr>${HEADERS.map((h,i)=>`<th scope="col">${String.fromCharCode(65+i)} ${e(h)}</th>`).join('')}</tr></thead><tbody>${rows.map(r=>`<tr>${r.fields.map(f=>`<td>${e(f)||'<span class="sel-muted">空</span>'}</td>`).join('')}</tr>`).join('')||'<tr><td colspan="16">暂无演示任务。</td></tr>'}</tbody></table></div>`;}
function preview(){const result=provider.preview({accountId:ui.accountId,date:ui.date});previewToken=result.token;
  openDialog('核对固定16列任务表',`<p>仅预览虚构数据。群名称取商品库O列，J是个人可选提示，L:P保持空白。</p>${result.issues.length?`<ul class="sel-error">${result.issues.map(i=>`<li>${e(i)}</li>`).join('')}</ul>`:''}${contractTable(result.rows)}<label class="sel-confirm"><input type="checkbox" data-confirm-preview ${result.issues.length?'disabled':''}>我已核对以上演示草稿，模拟追加为任务；不替换其他既有任务。</label>`,`<button type="button" data-dialog-close>继续修改草稿</button><button type="button" class="sel-primary" data-dialog-action="generate" disabled>模拟生成任务</button>`);
}
function editDraft(id){const data=provider.state(),row=data.drafts.find(r=>r.id===id);if(!row)throw new Error('该草稿已变化。');const operators=[...new Set([...data.accounts.map(a=>a.operator),row.fields[1]])];
  openDialog('核对与修改草稿',`<p>${e(row.fields[2])} · ${e(row.fields[5])}</p><form data-dialog-form="draft" data-row="${e(id)}" class="sel-form"><label>E 作品描述/商品短标题<input name="title" value="${e(row.fields[4])}" maxlength="4000" autocomplete="off" required></label><label>B 当天运营人<select name="operator">${operators.map(op=>`<option ${op===row.fields[1]?'selected':''}>${e(op)}</option>`).join('')}</select></label><label>J 视频链接（仅个人记忆提示，可空）<input name="portHint" value="${e(row.fields[9])}" autocomplete="off" maxlength="4000"></label><label>K 店铺名称（可空）<input name="shopName" value="${e(row.fields[10])}" autocomplete="off" maxlength="4000"></label><p>账号身份、完整商品ID及来源信息保留；临时改派只影响本行。</p><button class="sel-primary" type="submit">保存演示草稿</button></form>`);
}
function presets(){const data=provider.state();openDialog('编辑账号目标预设',`<p>切换预设只调整目标数量，保留已选商品和模拟任务。</p><form data-dialog-form="preset" class="sel-form"><label>保存方式<select name="editId"><option value="">另存为新预设</option>${data.presets.map(p=>`<option value="${e(p.id)}">编辑：${e(p.name)}</option>`).join('')}</select></label><label>预设名称<input name="name" maxlength="40" required autocomplete="off" placeholder="例如 周末方案…"></label><div class="sel-preset-grid">${data.accounts.map(a=>`<label>${e(a.name)}<input type="number" name="target-${e(a.id)}" min="0" max="100" value="${data.targets[a.id]}" required></label>`).join('')}</div><button type="submit" class="sel-primary">保存并应用演示预设</button></form>`);}
function details(id){const p=provider.state().products.find(p=>p.id===id);if(!p)throw new Error('商品不存在。');openDialog('商品与来源详情',`<div class="sel-product-detail"><img src="${e(p.image)}" width="240" height="200" alt="${e(p.title)}，虚构商品插画"><div><h3>${e(p.title)}</h3><p class="sel-mono">${e(p.productId)}</p><p>${e(p.category)} · ${e(p.statisticDate)}</p><p>佣金：${e(p.commission)}　日销量：${e(p.dailySales)}</p></div></div>${p.issues.length?`<p class="sel-error">${e(p.issues.join('；'))}</p>`:''}<dl class="sel-source-facts"><dt>商品库来源</dt><dd>${e(p.sourceSheet)}，第${e(p.sourceRow)}行（虚构）</dd><dt>C列原始字段</dt><dd>${e(p.groupC)}</dd><dt>O列协同群 → 任务G</dt><dd>${e(p.groupO)}${p.conflictingGroups?`；冲突来源：${e(p.conflictingGroups.join(' / '))}`:''}</dd><dt>剪辑要求</dt><dd>${e(p.requirement)}</dd><dt>来源标签</dt><dd>${e(p.tags.join('、')||'无')}</dd><dt>商品链接（演示，不访问）</dt><dd>${e(p.link)}</dd></dl>`);}
function compare(){openDialog('三版布局对照',`<p>同一套账号、商品、草稿和动作；只比较你每天操作时的布局。</p><div class="sel-scroll"><table><thead><tr><th>方案</th><th>账号与目标</th><th>商品呈现</th><th>草稿位置</th></tr></thead><tbody><tr><td>A 账号工作台</td><td>左侧账号列表</td><td>带商品图的紧凑列表</td><td>右侧持续显示</td></tr><tr><td>B 图片选品台</td><td>顶部选择账号</td><td>大图卡片</td><td>右侧持续显示</td></tr><tr><td>C 表格选品台</td><td>顶部选择账号</td><td>更多原商品库字段</td><td>下方表格</td></tr></tbody></table></div><p>选定版式后再接真实库、持久保存和XLSX导出。本页不会生成正式业务任务。</p>`);}
root.addEventListener('click',event=>{const b=event.target.closest('button');if(!b)return;try{
  if(b.dataset.selVariant){ui.variant=b.dataset.selVariant;const url=new URL(location.href);url.searchParams.set('variant',ui.variant);history.replaceState(null,'',url);render();}
  else if(b.dataset.selAccount){ui.accountId=b.dataset.selAccount;render();}
  else if(b.dataset.selAdd){const r=provider.add({accountId:ui.accountId,productId:b.dataset.selAdd,date:ui.date,operator:ui.operator});render();notify(r.reused?'当天已有该商品，未重复添加。':'已加入演示草稿。');}
  else if(b.dataset.selDetail)details(b.dataset.selDetail);
  else if(b.dataset.selRemove){provider.remove(b.dataset.selRemove);render();notify('已从演示草稿移除。');}
  else if(b.dataset.selEdit)editDraft(b.dataset.selEdit);
  else if(b.dataset.selAction==='presets')presets();
  else if(b.dataset.selAction==='compare')compare();
  else if(b.dataset.selAction==='preview')preview();
  else if(b.dataset.selAction==='tasks'){const rows=provider.state().tasks.filter(r=>r.date===ui.date);openDialog('当天模拟任务',`<p>共${rows.length}行；包含既有演示任务。新生成只追加，不自动登记公司协同，也不执行任何软件。</p>${contractTable(rows)}`);}
  else if(b.dataset.selAction==='reset')openDialog('重置演示',`<p>清空本页内存中的演示修改，恢复虚构初始数据。正式工作台保持不变。</p>`,`<button type="button" data-dialog-close>保留当前演示</button><button class="sel-primary" type="button" data-dialog-action="reset">确认重置演示</button>`);
}catch(error){notify(error.message,true);}});
root.addEventListener('input',event=>{const n=event.target;if(n.dataset.selControl==='search'&&!composing&&!event.isComposing){ui.search=n.value;render();}});
root.addEventListener('compositionstart',()=>composing=true);root.addEventListener('compositionend',event=>{composing=false;if(event.target.dataset.selControl==='search'){ui.search=event.target.value;render();}});
root.addEventListener('change',event=>{const n=event.target,key=n.dataset.selControl;if(!key||key==='search')return;try{
  if(key==='target'){provider.setTarget(ui.accountId,Number(n.value));ui.presetId='';}
  else if(key==='presetId'){if(n.value)provider.applyPreset(n.value);ui.presetId=n.value;}
  else if(['date','operator','range','accountId'].includes(key))ui[key]=n.value;
  else if(key==='showOther')ui.showOther=n.checked;
  render();
}catch(error){notify(error.message,true);}});
dialog.addEventListener('close',()=>{const target=returnSelector?root.querySelector(returnSelector):null;if(target&&!target.disabled)target.focus({preventScroll:true});else root.querySelector('#selection-main')?.focus({preventScroll:true});});
dialog.addEventListener('change',event=>{const n=event.target;if(n.hasAttribute('data-confirm-preview'))dialog.querySelector('[data-dialog-action="generate"]').disabled=!n.checked;
  if(n.name==='editId'){const preset=provider.state().presets.find(p=>p.id===n.value);if(preset){const form=n.form;form.elements.name.value=preset.name;for(const [id,value] of Object.entries(preset.targets))form.elements['target-'+id].value=value;}}
});
dialog.addEventListener('click',event=>{const b=event.target.closest('button');if(!b)return;try{if(b.hasAttribute('data-dialog-close'))dialog.close();
  else if(b.dataset.dialogAction==='generate'){if(!dialog.querySelector('[data-confirm-preview]')?.checked)throw new Error('请先核对并勾选演示草稿。');const result=provider.generate({token:previewToken});dialog.close();render();notify(`已模拟追加${result.count}条任务；仍需人工登记公司协同，未调用业务软件。`);}
  else if(b.dataset.dialogAction==='reset'){provider.reset();Object.assign(ui,{accountId:'a1',date:DEMO_DATE,operator:'inherit',range:'latest',search:'',showOther:false,presetId:'daily'});dialog.close();render();notify('演示已重置。');}
}catch(error){dialogError(error);}});
dialog.addEventListener('submit',event=>{event.preventDefault();const form=event.target;try{
  if(form.dataset.dialogForm==='draft'){provider.updateDraft(form.dataset.row,Object.fromEntries(new FormData(form)));dialog.close();render();notify('演示草稿已更新。');}
  else if(form.dataset.dialogForm==='preset'){const data=provider.state(),targets={};for(const a of data.accounts)targets[a.id]=Number(form.elements['target-'+a.id].value);const p=provider.savePreset(form.elements.name.value,targets,form.elements.editId.value||undefined);provider.applyPreset(p.id);ui.presetId=p.id;dialog.close();render();notify('演示数量预设已保存并应用。');}
}catch(error){dialogError(error);}});
render();
