import {ACCOUNTS,HEADERS} from '../data/fixtures.js';
export {HEADERS};
export const ROOT='E:\\演示数据\\DEMO-1013\\videos';
export function categoryCheck(task,snapshots){
  if(!snapshots.products||!snapshots.accounts)return {ok:false,reason:'分类证据缺失：需要商品类别和账号F列快照'};
  const account=ACCOUNTS.find(a=>a.id===task.accountId);
  if(!account)return {ok:false,reason:'账号不在导入的账号快照中'};
  if(!task.category)return {ok:false,reason:'商品类别未知，不能凭标题推断'};
  if(!account.allowed.includes(task.category))return {ok:false,reason:`品类不符：账号只允许 ${account.allowed.join('、')}`};
  return {ok:true,reason:'商品类别与账号允许品类一致'};
}
export function readiness(task,mode='both',snapshots={products:true,accounts:true}){
  const check=categoryCheck(task,snapshots);
  if(!check.ok)return check;
  if(!['douyin','qianchuan','both'].includes(mode))return {ok:false,reason:'请选择有效的交接目标'};
  if(!['ready','publishing','reported','done'].includes(task.stage))return {ok:false,reason:'任务尚未完成剪辑交接'};
  if((mode==='qianchuan'||mode==='both')&&task.normal<task.target)return {ok:false,reason:'普通成片不足，千川交接未就绪'};
  if((mode==='douyin'||mode==='both')&&task.speed<1)return {ok:false,reason:'变速成片不足，抖音交接未就绪'};
  return {ok:true,reason:'对应成片与分类证据齐全'};
}
export function artifactPaths(task){
  const dir=ROOT+'\\'+task.fields[2]+'\\'+task.fields[5]+'\\成片';
  return {normal:dir,speed:dir+'\\变速'};
}
export function buildBatches(tasks,mode='both',snapshots={products:true,accounts:true}){
  const groups=new Map();const excluded=[];
  // Only not-yet-handed-off tasks qualify; do not re-export a publishing or completed task.
  for(const task of tasks){
    if(!groups.has(task.accountId))groups.set(task.accountId,[]);
    const check=readiness(task,mode,snapshots);
    if(!check.ok||task.stage!=='ready'){excluded.push({taskId:task.id,reason:check.ok?'已交接或已完成，避免重复发布':check.reason});continue;}
    groups.get(task.accountId).push(task);
  }
  const entries=[...groups.entries()];const batches=[];
  for(let i=0;i<entries.length;i+=5){
    const number=i/5+1;
    const slots=Array.from({length:5},(_,index)=>{
      const entry=entries[i+index];if(!entry||entry[1].length===0)return {slot:index+1,account:null,tasks:[],table:null};
      const account=ACCOUNTS.find(a=>a.id===entry[0]);
      return {slot:index+1,account,tasks:entry[1],table:`E:\\演示数据\\DEMO-1013\\交接批次${number}\\槽${index+1}-${account.id}.xlsx`,sheet:'Sheet1',portHint:account.port};
    });
    if(slots.some(slot=>slot.account))batches.push({number,root:ROOT,mode,slots});
  }
  return {batches,excluded};
}
export function businessRows(tasks){return tasks.map(task=>[...task.fields]);}
