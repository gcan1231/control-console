// 006A2 fictional data only. This module has no HTTP, storage or executor access.
export const HEADERS=['日期','运营人','抖音名称','抖音账号','作品描述/商品短标题','商品ID','企微群名称','商品链接','账号合作码/uid','视频链接','店铺名称','剪辑软件日志','发文软件日志','千川塞素材软件日志','检查软件日志','企微同步软件日志'];
export const DEMO_DATE='2026-10-14';
const copy=value=>structuredClone(value);
const CATEGORIES=['食品/宠物','百货/个护家清','母婴'];
function fixture(){
  const accounts=[
    {id:'a1',accountId:'demo-food01',name:'演示食光小铺',sourceName:'演示食光小铺',operator:'演示运营甲',companyCode:'DEMO-A01',allowed:[CATEGORIES[0]],verified:true,uid:'000900000000000001',cooperationCode:'000810001'},
    {id:'a2',accountId:'demo-home02',name:'演示生活选物',sourceName:'演示生活选物（虚构门店）',operator:'演示运营甲',companyCode:'DEMO-A02',allowed:[CATEGORIES[1]],verified:true,uid:'000900000000000002',cooperationCode:'000810002'},
    {id:'a3',accountId:'demo-baby03',name:'演示成长日记',sourceName:'演示成长日记',operator:'演示运营乙',companyCode:'DEMO-A03',allowed:[CATEGORIES[2]],verified:true,uid:'000900000000000003',cooperationCode:'000810003'},
    {id:'a4',accountId:'demo-pending04',name:'演示待核对账号',sourceName:'演示待核对账号',operator:'演示运营乙',companyCode:'',allowed:[],verified:false,uid:'',cooperationCode:''},
    {id:'a5',accountId:'demo-info05',name:'演示资料待补账号',sourceName:'演示资料待补账号',operator:'演示运营乙',companyCode:'DEMO-A05',allowed:[CATEGORIES[0]],verified:true,uid:'',cooperationCode:''}
  ];
  const groups=[
    {category:CATEGORIES[0],names:['燕麦坚果脆小方','低糖酸奶小饼干','香焙海苔夹心脆','山野水果干组合','醇香早餐谷物包','每日坚果分享装','清爽柚子气泡饮','桂花乌龙茶饮','鲜肉冻干宠物零食','宠物营养小鱼干','烘焙芝士薄脆','果香茶点礼盒'],images:['snack','snack','snack','snack','snack','snack','drink','drink','pet','pet','snack','snack']},
    {category:CATEGORIES[1],names:['厨房去油泡沫清洁剂','柔软珊瑚绒擦手巾','多效洗衣凝珠','浴室水垢清洁喷雾','轻柔棉感洗脸巾','桌面收纳清洁套装','便携除渍清洁笔','蓬松吸水浴巾'],images:['clean','towel','clean','clean','towel','clean','clean','towel']},
    {category:CATEGORIES[2],names:['婴童棉柔护理巾','宝宝硅胶辅食碗','儿童柔软小方巾','宝宝外出护理套装','婴童便携水杯','亲子柔棉围兜'],images:['baby','baby','towel','baby','baby','baby']}
  ];
  let n=0;const products=[];
  for(const group of groups)for(let i=0;i<group.names.length;i++){
    const index=++n,productId='9000000000000000'+String(index).padStart(3,'0');
    products.push({id:'p'+index,productId,title:'演示'+group.names[i],category:group.category,
      statisticDate:i===group.names.length-1?'2026-10-12':DEMO_DATE,progress:i%4?'进行中':'新入库',investment:i%3?'常规':'优先关注',
      commission:i%3?'5+1':'8',totalSales:i%2?'大于10万':'大于1万',dailySales:i%3?'大于5000':'低销量（准新）',
      requirement:i%3?'突出使用场景，画面简洁':'尽量覆盖多种素材视角',groupC:'虚构商家名称'+index,
      groupO:'演示商家协同群'+index,sourceSheet:group.category==='食品/宠物'?'食品-宠物':group.category==='母婴'?'母婴':'百货个护家清',
      sourceRow:i+2,tags:i%4===0?['精选商家']:[],link:'https://example.invalid/product/'+productId,
      image:'/assets/selection/'+group.images[i]+'.svg',issues:[],history:i===0?{a1:['2026-10-10'],a2:['2026-10-11']}: {}});
  }
  products.push({...copy(products[1]),id:'p-conflict',productId:'9000000000000000998',title:'演示来源冲突商品',issues:['同一商品两份来源的O列群名称不一致，需核对来源。'],conflictingGroups:['演示协同群甲','演示协同群乙']});
  products.push({...copy(products[2]),id:'p-unknown',productId:'9000000000000000999',title:'演示分类待核对商品',category:'未知分类',issues:['缺少可靠主分类，不能凭商品名称判断。']});
  const presets=[{id:'daily',name:'日常方案',targets:{a1:5,a2:4,a3:3,a4:3,a5:2}},{id:'more',name:'加量方案',targets:{a1:8,a2:6,a3:5,a4:4,a5:3}}];
  return {accounts,products,presets,targets:copy(presets[0].targets),drafts:[],tasks:[]};
}
function validDate(date){if(typeof date!=='string'||!/^\d{4}-\d{2}-\d{2}$/.test(date)||Number.isNaN(Date.parse(date))||new Date(date+'T00:00:00Z').toISOString().slice(0,10)!==date)throw new Error('请选择有效的任务日期。');}
function targetValue(value){if(!Number.isInteger(value)||value<0||value>100)throw new Error('目标数量须为0—100的整数。');return value;}
export class SelectionDemoProvider {
  constructor(){this.reset();}
  reset(){this.data=fixture();this.version=0;this.serial=1;this.previews=new Map();this.generated=new Map();
    // A fictional prior task proves that new rows append without archiving old work.
    const p=this.data.products.find(p=>p.category===CATEGORIES[1]);const a=this.account('a2');
    this.data.tasks.push({id:'existing-demo-task',date:DEMO_DATE,accountId:a.id,productRecordId:p.id,fields:this.fields(a,p,DEMO_DATE,'inherit'),status:'模拟既有任务',locked:true});
  }
  state(){return copy(this.data);}
  account(id){const a=this.data.accounts.find(a=>a.id===id);if(!a)throw new Error('请选择演示账号。');return a;}
  fields(a,p,date,operator){return [date,operator==='inherit'?a.operator:operator,a.name,a.accountId,p.title,p.productId,p.groupO,p.link,`账号：${a.name}\n抖音号：${a.accountId}\nUID：${a.uid}\n合作码：${a.cooperationCode}`,'','','','','','',''];}
  reason(a,p){if(!a.verified||!a.allowed.length)return '账号F列允许范围尚未核对，请先核对账号资料。';if(p.issues.length)return p.issues.join('；');if(!a.allowed.includes(p.category))return '品类不符：本账号允许 '+a.allowed.join('、');return '';}
  catalog({accountId,date=DEMO_DATE,range='latest',search='',showOther=false}){
    const a=this.account(accountId);const eligible=this.data.products.filter(p=>!p.issues.length&&a.allowed.includes(p.category));
    const latest=eligible.map(p=>p.statisticDate).sort().at(-1)||DEMO_DATE;
    return this.data.products.filter(p=>(showOther||a.allowed.includes(p.category))&&(range==='all'||p.statisticDate===(range==='latest'?latest:range))&&(!search||[p.title,p.productId,p.category,p.groupO].join(' ').toLowerCase().includes(search.toLowerCase())))
      .sort((a,b)=>b.statisticDate.localeCompare(a.statisticDate)).map(p=>{
        const selected=this.data.drafts.some(r=>r.accountId===accountId&&r.date===date&&r.fields[5]===p.productId);
        const alreadyTask=this.data.tasks.some(r=>r.accountId===accountId&&r.date===date&&r.fields[5]===p.productId);
        const reason=this.reason(a,p)||(selected?'已加入本账号当日草稿':alreadyTask?'本账号当天已有任务':'');
        return {...copy(p),selected,alreadyTask,selectable:!reason,reason,history:copy(p.history[accountId]||[])};
      });
  }
  add({accountId,productId,date=DEMO_DATE,operator='inherit'}){
    validDate(date);const a=this.account(accountId),p=this.data.products.find(p=>p.id===productId);
    if(!p)throw new Error('找不到演示商品。');const problem=this.reason(a,p);if(problem)throw new Error(problem);
    if(operator!=='inherit'&&!this.data.accounts.some(a=>a.operator===operator))throw new Error('请选择明确的演示运营人。');
    const duplicate=[...this.data.drafts,...this.data.tasks].find(r=>r.date===date&&r.accountId===accountId&&r.fields[5]===p.productId);
    if(duplicate)return {reused:true,id:duplicate.id};
    const row={id:'draft-'+this.serial++,date,accountId,productRecordId:p.id,fields:this.fields(a,p,date,operator)};
    this.data.drafts.push(row);this.version++;return copy(row);
  }
  remove(id){const i=this.data.drafts.findIndex(r=>r.id===id);if(i<0)throw new Error('草稿行已变化，请重新查看。');this.data.drafts.splice(i,1);this.version++;}
  updateDraft(id,changes){const row=this.data.drafts.find(r=>r.id===id);if(!row)throw new Error('草稿行已变化，请重新查看。');
    const mapping={title:4,operator:1,portHint:9,shopName:10};for(const [key,value] of Object.entries(changes)){
      if(!(key in mapping)||typeof value!=='string'||value.length>4000||/[\x00-\x08\x0b\x0c\x0e-\x1f]/.test(value))throw new Error('草稿字段需为有效文本，每格最多4000字符。');
    }
    for(const [key,value] of Object.entries(changes))row.fields[mapping[key]]=value.trim();this.version++;
  }
  setTarget(accountId,value){this.account(accountId);this.data.targets[accountId]=targetValue(value);this.version++;}
  applyPreset(id){const preset=this.data.presets.find(p=>p.id===id);if(!preset)throw new Error('找不到目标预设。');this.data.targets=copy(preset.targets);this.version++;}
  savePreset(name,targets,editId){if(typeof name!=='string'||!name.trim()||name.length>40)throw new Error('请填写1—40字的预设名称。');
    if(!targets||typeof targets!=='object'||Array.isArray(targets))throw new Error('请核对各账号目标数量。');
    const checked={};for(const a of this.data.accounts)checked[a.id]=targetValue(targets[a.id]);
    let preset=this.data.presets.find(p=>p.id===editId);if(editId&&!preset)throw new Error('预设已变化。');
    if(this.data.presets.some(p=>p.id!==editId&&p.name===name.trim()))throw new Error('预设名称已存在，请换个名称或编辑已有预设。');
    if(!preset){preset={id:'preset-'+this.serial++};this.data.presets.push(preset);}Object.assign(preset,{name:name.trim(),targets:checked});this.version++;return copy(preset);
  }
  counts(accountId,date){const draft=this.data.drafts.filter(r=>r.accountId===accountId&&r.date===date).length,generated=this.data.tasks.filter(r=>r.accountId===accountId&&r.date===date).length;return {draft,generated,total:draft+generated,target:this.data.targets[accountId]||0};}
  preview({accountId,date}){this.account(accountId);validDate(date);const rows=copy(this.data.drafts.filter(r=>r.accountId===accountId&&r.date===date));const issues=[];
    if(!rows.length)issues.push('本账号当天没有草稿，请先选择商品。');
    for(const row of rows){const a=this.account(row.accountId);if(!a.uid||!a.cooperationCode)issues.push(`${a.name}：UID或合作码尚未补全，请先核对账号资料。`);
      for(const i of [0,1,2,3,4,5,6,7])if(!row.fields[i].trim())issues.push(`${row.fields[4]||'草稿行'}：${HEADERS[i]}缺失。`);
      if(!/^\d+$/.test(row.fields[5]))issues.push('商品ID必须是完整数字字符串。');
    }
    const token='preview-'+this.serial++;this.previews.set(token,{version:this.version,rows,issues});return {token,rows,issues:[...new Set(issues)]};
  }
  generate({token}){if(this.generated.has(token))return {...copy(this.generated.get(token)),reused:true};const preview=this.previews.get(token);
    if(!preview||preview.version!==this.version)throw new Error('草稿或预设已变化，请重新预览16列。');if(preview.issues.length)throw new Error(preview.issues.join('；'));
    const ids=new Set(preview.rows.map(r=>r.id));const created=preview.rows.map(r=>({...copy(r),id:'simulated-task-'+this.serial++,status:'模拟待准备素材'}));
    this.data.tasks.push(...created);this.data.drafts=this.data.drafts.filter(r=>!ids.has(r.id));this.version++;
    const result={tasks:created,count:created.length,reused:false};this.generated.set(token,copy(result));return copy(result);
  }
}
