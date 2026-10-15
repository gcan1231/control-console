import {LogProvider} from './log-provider.js';
import {escape as e} from './ui.js';
const provider=new LogProvider(),$=s=>document.querySelector(s),names={material:'找素材',editing:'剪辑',publish:'发文'},levels={INFO:'信息',ERROR:'错误',WARN:'警告',DEBUG:'调试',CRITICAL:'严重错误',FATAL:'致命错误',UNKNOWN:'未标级别'};
const params=new URLSearchParams(location.search),taskId=params.get('taskId')||'';
const filters={software:params.get('software')||'',level:params.get('level')||'',search:params.get('search')||'',taskId,page:Math.max(1,Number(params.get('page'))||1)};
let configDirty=false;
let state={sources:[],events:[],total:0,pageSize:50},paused=false,loading=false,serial=0,composing=false,timer;
for(const key of ['software','level','search'])$('#logs-'+key).value=filters[key];
$('#logs-scope').value=taskId?'task':'';$('#logs-scope option[value=task]').disabled=!taskId;
function message(text,error=false){const n=$('#logs-message');n.hidden=false;n.textContent=text;n.dataset.error=String(error);}
function url(){const q=new URLSearchParams(Object.entries(filters).filter(([,v])=>v!==''));history.replaceState(null,'','/logs.html?'+q);}
function time(value){const d=new Date(value);return Number.isNaN(d.getTime())?String(value||'时间未提供'):new Intl.DateTimeFormat('zh-CN',{dateStyle:'short',timeStyle:'medium'}).format(d);}
function sources(){
 const focused=document.activeElement?.closest('[data-source]')?.dataset.source;
 $('#logs-sources').innerHTML=Object.entries(names).map(([key,name])=>{const found=state.sources.filter(s=>s.software===key);return `<article class="logs-source"><h3>${name}</h3>${found.length?found.map(s=>`<p><strong>${e(s.label)}</strong> · ${s.enabled?'已启用':'已停用'}${s.status?' · '+e(({waiting:'等待读取',waiting_file:'等待日志文件',disabled:'已停用',ok:'读取正常',limited:'分批读取',error:'读取失败'})[s.status]||s.status):''}</p><p>${e(s.error||s.note||s.lastReadAt&&'最近读取：'+time(s.lastReadAt)||'等待读取')}</p><button type="button" class="secondary" data-source="${e(s.id)}">查看 / 修改来源</button>`).join(''):'<p>尚未配置已确认的日志文件</p>'}</article>`;}).join('');
 if(focused)$('#logs-sources').querySelector(`[data-source="${CSS.escape(focused)}"]`)?.focus({preventScroll:true});
}
function rows(){
 const max=Math.max(1,Math.ceil(state.total/(state.pageSize||50)));
 $('#logs-count').textContent=`共 ${state.total} 条日志 · 当前 ${state.events.length} 条`;
 $('#logs-page').textContent=`第 ${filters.page} / ${max} 页`;
 $('#logs-prev').disabled=filters.page<=1;$('#logs-next').disabled=filters.page>=max;
 $('#logs-status').textContent=paused?'已暂停页面刷新':`每3秒刷新${state.updatedAt?' · 最近 '+time(state.updatedAt):''}`;
 $('#logs-rows').innerHTML=state.events.length?state.events.map(ev=>`<tr><td>${e(time(ev.time||ev.observedAt))}<small>${ev.time?'日志时间':'读取时间'}</small><span class="logs-level" data-level="${e(ev.level)}">${e(levels[ev.level]||'未标级别')}</span></td><td>${e(names[ev.software]||ev.software)}<small>${e(state.sources.find(s=>s.id===ev.sourceId)?.label||'来源未提供')}</small><small>${e(ev.file)}${ev.line?' · 行 '+e(ev.line):''}</small></td><td><div class="logs-event-message">${e(ev.message)}</div></td><td>${ev.taskId?`明确任务标记<small>${e(ev.taskId)}</small>`:'软件日志<small>无可靠任务关联</small>'}</td></tr>`).join(''):`<tr><td colspan="4"><div class="logs-empty">${state.sources.some(s=>s.enabled)?'当前范围没有日志。可清除筛选，或检查来源文件是否已有输出。':'尚未启用日志来源。展开下方设置，填写已确认的本地日志路径。'}</div></td></tr>`;
}
async function refresh(manual=false){if(loading)return;loading=true;const version=++serial,snapshot=JSON.stringify(filters);$('#logs-refresh').disabled=true;try{if(manual)await provider.poll();const next=await provider.state(filters);if(version!==serial)return;state=next;if(filters.page>1&&!state.events.length){filters.page=1;url();state=await provider.state(filters);}sources();rows();if(manual)message('日志副本已刷新；任务结果保持不变。');}catch(error){message(error.message,true);$('#logs-status').textContent='刷新失败，可点击立即刷新重试';}finally{loading=false;$('#logs-refresh').disabled=false;if(snapshot!==JSON.stringify(filters))refresh();}}
function change(){filters.page=1;url();refresh();}
for(const key of ['software','level'])$('#logs-'+key).addEventListener('change',ev=>{filters[key]=ev.target.value;change();});
$('#logs-scope').addEventListener('change',ev=>{filters.taskId=ev.target.value==='task'?taskId:'';change();});
$('#logs-search').addEventListener('compositionstart',()=>composing=true);
function search(){if(composing)return;clearTimeout(timer);timer=setTimeout(()=>{filters.search=$('#logs-search').value;change();},300);}
$('#logs-search').addEventListener('input',search);$('#logs-search').addEventListener('compositionend',()=>{composing=false;search();});
$('#logs-clear').addEventListener('click',()=>{clearTimeout(timer);Object.assign(filters,{software:'',level:'',search:'',taskId:'',page:1});for(const key of ['software','level','search'])$('#logs-'+key).value='';$('#logs-scope').value='';change();});
$('#logs-pause').addEventListener('click',ev=>{paused=!paused;ev.currentTarget.textContent=paused?'继续刷新':'暂停刷新';ev.currentTarget.setAttribute('aria-pressed',String(paused));rows();if(!paused)refresh();});
$('#logs-refresh').addEventListener('click',()=>refresh(true));
for(const [id,step] of [['prev',-1],['next',1]])$('#logs-'+id).addEventListener('click',()=>{filters.page+=step;url();refresh();});
$('#logs-sources').addEventListener('click',ev=>{const id=ev.target.closest('[data-source]')?.dataset.source,s=state.sources.find(s=>s.id===id);if(!s)return;for(const key of ['software','label','path','encoding'])$('#logs-config-'+key).value=s[key]||'';$('#logs-config-rotation').value=s.rotationGlob||'';$('#logs-config-enabled').checked=!!s.enabled;$('#logs-source-form').dataset.source=id;$('#logs-config').open=true;$('#logs-config').scrollIntoView({block:'start'});$('#logs-config-software').focus();});
$('#logs-source-form').addEventListener('submit',async ev=>{ev.preventDefault();const form=ev.currentTarget,button=$('#logs-save');if(button.disabled)return;const body=Object.fromEntries(new FormData(form));body.enabled=$('#logs-config-enabled').checked;if(form.dataset.source)body.id=form.dataset.source;button.disabled=true;$('#logs-config-error').hidden=true;try{const saved=await provider.configure(body);if(saved.source)form.dataset.source=saved.source.id;configDirty=false;message('来源已保存；启用的文件将每3秒只读采集。');await refresh();}catch(error){$('#logs-config-error').hidden=false;$('#logs-config-error').textContent=error.message;$('#logs-config-error').tabIndex=-1;$('#logs-config-error').focus();}finally{button.disabled=false;}});
try{await provider.init();await refresh();}catch(error){message(error.message,true);}
setInterval(()=>{if(!paused&&!document.hidden)refresh();},3000);

$('#logs-new').addEventListener('click',()=>{$('#logs-source-form').reset();delete $('#logs-source-form').dataset.source;$('#logs-config-error').hidden=true;$('#logs-config-label').focus();});

$('#logs-source-form').addEventListener('input',()=>configDirty=true);window.addEventListener('beforeunload',event=>{if(configDirty){event.preventDefault();event.returnValue='';}});
