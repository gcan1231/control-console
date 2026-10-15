// Fictional missing-log UI and recovery, ephemeral loopback server only.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {spawn} from 'node:child_process';
import {mkdtemp,writeFile,rm,mkdir} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import path from 'node:path';
const require=createRequire(import.meta.url);let pw;try{pw=require('playwright');}catch{pw=require(process.env.PLAYWRIGHT_MODULE);}
const root=await mkdtemp(path.join(tmpdir(),'console-log-waiting-'));let server,browser;
try{
 server=spawn(process.env.PYTHON||'python',['-B','-u','-c',`import sys,threading;from tools.local_server import create_server;s=create_server(0,sys.argv[1]);t=threading.Thread(target=s.serve_forever,daemon=True);t.start();print(s.server_port,flush=True);sys.stdin.readline();s.shutdown();s.server_close();t.join()`,path.join(root,'runtime')],{stdio:['pipe','pipe','pipe']});
 const base=await new Promise((resolve,reject)=>{let output='';server.stdout.on('data',chunk=>{output+=chunk;if(output.includes('\n'))resolve('http://127.0.0.1:'+Number(output.split('\n')[0]));});server.on('error',reject);server.on('exit',code=>reject(new Error('Temporary server exited '+code)));});
 browser=await pw.chromium.launch({headless:true,channel:process.env.PLAYWRIGHT_CHANNEL||'chrome'});
 const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[],external=[];
 page.on('pageerror',error=>errors.push(error.message));
 await page.route('**/*',route=>{if(new URL(route.request().url()).origin!==base){external.push(route.request().url());return route.abort();}return route.continue();});
 const health=await(await page.request.get(base+'/api/health')).json(),headers={'X-Console-Token':health.token};
 const file=path.join(root,'fiction-missing.log');
 await page.request.post(base+'/api/logs/configure',{headers,data:{software:'editing',label:'虚构等待来源',path:file,enabled:true}});
 await page.request.post(base+'/api/logs/poll',{headers,data:{}});
 await page.goto(base+'/logs.html');
 const source=page.locator('.logs-source').filter({hasText:'虚构等待来源'});
 await source.filter({hasText:'等待日志文件'}).waitFor();
 assert.match(await source.innerText(),/文件生成后将自动只读采集/);
 assert.doesNotMatch(await source.innerText(),/读取失败/);
 await mkdir('test-results',{recursive:true});await page.screenshot({path:'test-results/logs-006b-waiting-file.png',fullPage:true});
 await writeFile(file,'INFO 虚构自动恢复\n','utf8');
 await page.request.post(base+'/api/logs/poll',{headers,data:{}});
 await source.filter({hasText:'读取正常'}).waitFor({timeout:12000});
 await page.locator('#logs-rows').filter({hasText:'虚构自动恢复'}).waitFor();
 await page.waitForTimeout(3400);
 const result=await(await page.request.get(base+'/api/logs/state')).json();assert.equal(result.total,1);assert.equal(result.sources[0].error,'');assert.equal(result.sources[0].note,'');
 assert.deepEqual(errors,[]);assert.deepEqual(external,[]);
 console.log(JSON.stringify({ok:true,checks:['等待文件状态与说明','新文件采集后每3秒页面自动恢复','重复轮询无重复事件'],pageErrors:errors.length,externalRequests:external.length}));
}finally{await browser?.close();if(server)await new Promise(resolve=>{server.once('exit',resolve);server.stdin.end('\n');});await rm(root,{recursive:true,force:true});}
