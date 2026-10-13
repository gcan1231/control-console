import {createTasks} from '../data/fixtures.js';
import {categoryCheck,readiness,buildBatches} from './contract.js';
export const STAGES={material:'待找素材',review:'素材待确认',editing_wait:'待剪辑交接',editing:'剪辑处理中',ready:'成片待交接',failed:'素材失败',publishing:'已交接待执行',reported:'软件报告待核验',done:'人工核验完成'};
export class MockProvider {
  constructor(scenario='overview'){this.reset(scenario);}
  reset(scenario='overview'){this.scenario=scenario;this.tasks=createTasks(scenario);this.snapshots={products:scenario!=='categorymissing',accounts:true,collaboration:true};this.imported=true;this.handoffKeys=new Set();return this.tasks;}
  list(){return this.tasks;}
  get(id){return this.tasks.find(t=>t.id===id);}
  log(task,text){task.events.push({time:new Intl.DateTimeFormat('zh-CN',{hour:'2-digit',minute:'2-digit',timeZone:'Asia/Shanghai'}).format(new Date()),text});}
  importDemo(){if(this.imported)return {ok:true,message:'该演示批次已经导入，没有创建重复任务'};this.imported=true;return {ok:true,message:'模拟导入完成'};}
  advance(id,expectedStage){
    const task=this.get(id);if(!task)return {ok:false,message:'任务不存在'};
    if(task.stage!==expectedStage)return {ok:false,message:'阶段已变化，本次重复操作已忽略'};
    const check=categoryCheck(task,this.snapshots);if(!check.ok)return {ok:false,message:check.reason};
    const next={material:'review',review:'editing_wait',editing_wait:'editing',editing:'ready',publishing:'reported',reported:'done'};
    if(task.stage==='failed'){task.stage='material';task.error='';task.attempt++;this.log(task,'模拟重试素材阶段，保留其他任务');return {ok:true,message:'已模拟重试失败阶段'};}
    if(!next[task.stage])return {ok:false,message:task.stage==='ready'?'请到发文交接页面生成交接预览':'该任务没有待执行的模拟操作'};
    const previous=task.stage;task.stage=next[previous];
    if(previous==='editing'){task.normal=task.target;task.speed=1;this.log(task,'模拟回收剪辑报告：普通3条 / 变速1条');}
    else if(previous==='publishing'){if(task.mode!=='qianchuan')task.fields[12]='已发布';if(task.mode!=='douyin')task.fields[13]='计划已发布';this.log(task,'模拟回收对应模式的软件回执，仍需人工核验');}
    else if(previous==='reported')this.log(task,'模拟人工核验，非真实平台验证');
    else this.log(task,'模拟阶段切换：'+STAGES[previous]+' → '+STAGES[task.stage]);
    return {ok:true,message:'模拟操作完成，没有启动真实软件'};
  }
  packages(mode){return buildBatches(this.tasks,mode,this.snapshots);}
  handoff(number,mode){
    const key=this.scenario+':'+number+':'+mode;
    if(this.handoffKeys.has(key))return {ok:true,message:'该交接已生成，本次没有重复创建'};
    const pack=this.packages(mode).batches.find(b=>b.number===number);
    if(!pack)return {ok:false,message:'当前批次没有可交接任务'};
    for(const slot of pack.slots)for(const task of slot.tasks){
      if(!readiness(task,mode,this.snapshots).ok)return {ok:false,message:'成片条件已变化，请重新检查'};
    }
    for(const slot of pack.slots)for(const task of slot.tasks){task.stage='publishing';task.mode=mode;task.events.push({time:'10:00',text:`模拟交接批次${number} / 槽${slot.slot} / ${mode}；未生成真实XLSX`});}
    this.handoffKeys.add(key);return {ok:true,message:'交接预览已模拟确认；没有生成真实表格或启动发文'};
  }
}
