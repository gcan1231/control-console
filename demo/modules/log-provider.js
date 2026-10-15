import {LocalProvider} from './local-provider.js';
export class LogProvider extends LocalProvider {
  state(filters){return this.request('/api/logs/state?'+new URLSearchParams(Object.entries(filters).filter(([,v])=>v!=='')));}
  configure(body){return this.request('/api/logs/configure',body);}
  poll(){return this.request('/api/logs/poll',{});}
}
