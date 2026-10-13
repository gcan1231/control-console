export const HEADERS = ['日期','运营人','抖音名称','抖音账号','作品描述/商品短标题','商品ID','企微群名称','商品链接','账号合作码/uid','视频链接','店铺名称','剪辑软件日志','发文软件日志','千川塞素材软件日志','检查软件日志','企微同步软件日志'];
export const ACCOUNTS = [
  {id:'demo-a01',name:'青禾日常',allowed:['食品/宠物'],port:'7001'},
  {id:'demo-a02',name:'初见生活',allowed:['百货/个护家清'],port:'7002'},
  {id:'demo-a03',name:'云朵好物',allowed:['食品/宠物'],port:'7003'},
  {id:'demo-a04',name:'木子母婴',allowed:['母婴'],port:'7004'},
  {id:'demo-a05',name:'小栗宠物',allowed:['食品/宠物'],port:'7005'},
  {id:'demo-a06',name:'柚子精选',allowed:['百货/个护家清'],port:'7006'}
];
export const SCENARIOS = {overview:'工作台概览',happy:'从导入开始',six:'六账号发文分批',categorywrong:'品类不符',categorymissing:'分类证据缺失',materialfail:'素材下载失败',regularmissing:'普通成片不足',speedmissing:'变速成片不足',reported:'发布结果待核验'};
const titles=['燕麦脆片组合','冻干水果小包','植物香氛洗衣液','厨房清洁套装','轻烘焙坚果','低糖米饼小盒','婴儿棉柔巾','儿童硅胶餐盘','宠物清洁湿巾','冻干零食组合','日常保温杯','便携收纳盒'];
export function createTasks(scenario='overview') {
  return ACCOUNTS.flatMap((a,index)=>[0,1].map((offset)=>{
    const i=index*2+offset;
    const productId='90000000000000000'+String(i+1).padStart(2,'0');
    const fields=['2026-10-13','演示运营',a.name,a.id,titles[i],productId,'演示商家群','https://example.invalid/product/'+productId,`账号：${a.name}\nUID：DEMO-UID-${i+1}\n合作码：DEMO-CODE`,offset===0?a.port:'',a.allowed[0],'','','','',''];
    const stages=['material','review','editing_wait','editing','ready','ready','material','material','failed','publishing','done','ready'];
    let stage=scenario==='overview'?stages[i]:'material';
    if (['six','categorywrong','categorymissing','regularmissing','speedmissing'].includes(scenario)) stage='ready';
    if(scenario==='materialfail')stage=i===0?'failed':'material';
    if(scenario==='reported')stage='reported';
    const hasOutput=['ready','publishing','reported','done'].includes(stage);
    const task={id:`demo-task-${i+1}`,batch:'DEMO-1013',sourceRow:i+2,accountId:a.id,fields,category:a.allowed[0],stage,normal:hasOutput?3:0,speed:hasOutput?1:0,target:3,error:stage==='failed'?'下载失败：演示网络中断':'',attempt:1,events:[{time:'09:00',text:'模拟导入 · 原始行 '+(i+2)}]};
    if(scenario==='categorywrong'&&i===0)task.category='母婴';
    if(scenario==='regularmissing'&&i===0)task.normal=0;
    if(scenario==='speedmissing'&&i===0)task.speed=0;
    if(stage==='reported'||stage==='done'){task.fields[12]='已发布';task.fields[13]='计划已发布';task.events.push({time:'09:40',text:'模拟回收软件报告 · 不是平台核验'});}
    return task;
  }));
}
