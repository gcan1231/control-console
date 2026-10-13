// Local-only HTTP boundary. Business data never enters browser persistence.
export class LocalProvider {
  async init() { const health = await this.request('/api/health'); this.token = health.token; return health; }
  async request(path, body, retried = false) {
    let response;
    try { response = await fetch(path, {method: body === undefined ? 'GET' : 'POST', headers: body === undefined ? {} : {'Content-Type':'application/json','X-Console-Token':this.token || ''}, ...(body === undefined ? {} : {body:JSON.stringify(body)}), cache:'no-store'}); }
    catch { throw new Error('无法连接本机服务。请确认本机服务正在运行，然后重试。'); }
    let data; try { data = await response.json(); } catch { throw new Error('本机服务返回了无法读取的响应。'); }
    if (response.status === 403 && data.error?.code === 'token' && !retried && path !== '/api/health') {
      await this.init();
      return this.request(path, body, true);
    }
    if (!response.ok || !data.ok) { const error = new Error(data.error?.message || `请求失败（${response.status}）`); error.details = data.error?.details; error.code = data.error?.code; throw error; }
    return data;
  }
  state() { return this.request('/api/state'); }
  inspect(file) { return this.request('/api/inspect',file); }
  preview(file) { return this.request('/api/import/preview',file); }
  commit(file) { return this.request('/api/import/commit',file); }
  aliases(aliases) { return this.request('/api/settings/aliases',{aliases}); }
  review(taskId,note,duplicateConfirmed) { return this.request('/api/tasks/review',{taskId,note,duplicateConfirmed}); }
}
