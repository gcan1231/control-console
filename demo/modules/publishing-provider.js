import {LocalProvider} from './local-provider.js';
export class PublishingProvider extends LocalProvider {
  state(){return this.request('/api/publishing/state');}
  perform(action,body){return this.request('/api/publishing/'+action,body);}
}
