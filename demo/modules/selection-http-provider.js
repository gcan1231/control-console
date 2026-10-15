// Same-origin local service only. Writes are never retried automatically.
export class SelectionHttpProvider {
  async init() { const health = await this.request('/api/health'); this.token = health.token; return health; }
  async request(path, body) {
    let response;
    try { response = await fetch(path, {method:body === undefined?'GET':'POST',cache:'no-store',headers:body === undefined?{}:{'Content-Type':'application/json','X-Console-Token':this.token || ''},...(body === undefined?{}:{body:JSON.stringify(body)})}); }
    catch { const error=new Error(body === undefined?'无法连接本机服务，请检查服务后刷新。':'请求结果未知。请刷新核对草稿和任务，勿直接重复提交。'); error.unknown=body!==undefined; throw error; }
    let data;
    try { data=await response.json(); } catch { throw new Error('响应无法读取，请刷新核对当前状态，勿重复提交。'); }
    if(!response.ok || data.ok===false) { const error=new Error(data.error?.message || `请求失败（${response.status}），请刷新后核对。`);error.code=data.error?.code;error.details=data.error?.details;throw error; }
    return data;
  }
  state(){return this.request('/api/selection/state');}
  products(filters){const query=new URLSearchParams({...filters,showOther:filters.showOther?'1':'0'});return this.request('/api/selection/products?'+query);}
  product(productId){return this.request('/api/selection/product?'+new URLSearchParams({productId}));}
  post(action,body){return this.request('/api/selection/'+action,body);}
  async download({date,accountId}) {
    const response=await fetch('/api/selection/export?'+new URLSearchParams({date,accountId}),{cache:'no-store'});
    if(!response.ok){let data;try{data=await response.json();}catch{}throw new Error(data?.error?.message||'导出失败，请核对已有任务。');}
    const blob=await response.blob(),url=URL.createObjectURL(blob),link=document.createElement('a');
    link.href=url;link.download=`任务表-${date}.xlsx`;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  }
}
