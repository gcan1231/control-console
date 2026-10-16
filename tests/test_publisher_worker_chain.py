"""Opt-in independent worker + real CDP against entirely fictional routed DOM.

No platform request is allowed onto the network. This validates selector/coordinator
integration, never compatibility with a live platform or actual publication.
"""
import asyncio
import hashlib
import os
from pathlib import Path
import socket
import sys
import tempfile
import unittest

from control_console.store import ConsoleStore
from control_console.publisher_execution import PublishExecutionService


def fixture(platform):
    identity = 'UID：fiction-uid' if platform == 'douyin' else '广告账户ID：fiction-ad'
    common = '''<input type="file" accept="video/mp4" multiple id="upload">
<ul id="uploads"></ul><script>
window.submits=0;window.closedPlans=[];window.events=[];document.addEventListener('click',e=>window.events.push(e.target.outerHTML));document.addEventListener('input',e=>window.events.push(e.target.outerHTML));
upload.onchange=()=>{uploads.replaceChildren();for(const f of upload.files){
const li=document.createElement('li');li.textContent=f.name;const s=document.createElement('span');s.textContent='上传成功';li.append(s);uploads.append(li);}};
function submit(id){window.submits++;const a=document.createElement('div');a.role='alert';a.style.whiteSpace='pre';
a.textContent=(id==='fiction-work'?'作品ID：':'计划ID：')+id+'\\n发布成功';document.body.append(a);}
</script>'''
    if platform == 'douyin':
        controls = '''<label>作品简介<textarea></textarea></label><label>商品链接<input></label>
<button>添加链接</button><label>商品短标题<input></label>
<section><div class="recommendCover-vWWsHB">第一封面</div><div class="recommendCover-vWWsHB" onclick="window.coverChosen=true">第二封面</div></section>
<button onclick="submit('fiction-work')">发布</button>'''
    else:
        controls = '''<label>商品ID<input></label><button>搜索</button>
<div role="option">9000000000000000001</div><label>日预算<input></label>
<label>支付ROI目标<input></label><label>标题<input></label>'''
        for label in ('出价方式', '投放目标', '投放位置'):
            controls += f'<label>{label}<select aria-label="{label}"><option value="wrong">错误默认选择</option><option value="confirmed">虚构选择</option></select></label>'
        controls += '''<button onclick="submit('fiction-plan')">发布计划</button>
<table><tbody><tr><td>unrelated-plan</td><td><button role="switch" aria-checked="true" onclick="throw Error('wrong plan')">无关开关</button></td></tr>
<tr><td>fiction-plan</td><td id="status"><button role="switch" aria-checked="true" onclick="window.closedPlans.push('fiction-plan');this.parentElement.textContent='已关闭'">本次开关</button></td></tr></tbody></table>'''
    return '<!doctype html><meta charset="utf-8"><p>抖音号：fiction-account</p><p>'+identity+'</p>'+common+controls


@unittest.skipUnless(os.environ.get('PUBLISHER_BROWSER_INTEGRATION') == '1', '显式启用隔离Chrome集成测试')
class WorkerDOMChainTests(unittest.IsolatedAsyncioTestCase):
    async def test_independent_worker_upload_submit_exact_receipt_and_close(self):
        chrome = Path('C:/Program Files/Google/Chrome/Application/chrome.exe')
        if not chrome.is_file(): self.skipTest('无本机Chrome')
        from playwright.async_api import async_playwright
        with tempfile.TemporaryDirectory(prefix='fiction-worker-chain-') as temporary:
            root = Path(temporary)
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
            async with async_playwright() as driver:
                context = await driver.chromium.launch_persistent_context(
                    str(root/'chrome-profile'), executable_path=str(chrome), headless=True,
                    args=[f'--remote-debugging-port={port}', '--disable-background-networking',
                          '--disable-component-update', '--no-first-run', '--no-default-browser-check',
                          '--proxy-server=http://127.0.0.1:9', '--proxy-bypass-list=<-loopback>',
                          '--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1'])
                requests=[]
                async def route(request):
                    url=request.request.url; requests.append(url)
                    if url.startswith('https://creator.douyin.com/'):
                        await request.fulfill(status=200, content_type='text/html', body=fixture('douyin'))
                    elif url.startswith('https://qianchuan.jinritemai.com/'):
                        await request.fulfill(status=200, content_type='text/html', body=fixture('qianchuan'))
                    else: await request.abort()
                await context.route('**/*', route)
                try:
                    pages={}
                    for platform, url in [('douyin','https://creator.douyin.com/creator-micro/content/upload'),
                                          ('qianchuan','https://qianchuan.jinritemai.com/fiction')]:
                        page=await context.new_page();await page.goto(url);pages[platform]=page
                    store=ConsoleStore(root/'runtime');service=PublishExecutionService(store,None,launch_workers=False)
                    fields=['fiction']*16;fields[5]='9000000000000000001';fields[7]='https://fiction.invalid/product'
                    video=root/'fiction.mp4';video.write_bytes(b'\x00\x00\x00\x18ftypisomFICTION')
                    file={'path':str(video),'bytes':video.stat().st_size,'sha256':hashlib.sha256(video.read_bytes()).hexdigest()}
                    items=[]
                    for platform in pages:
                        params={'coverMode':'recommended'} if platform=='douyin' else {'dailyBudget':'100','roi':'2','bidStrategy':'虚构选择','target':'虚构选择','placement':'虚构选择'}
                        items.append({'id':platform,'taskId':'fiction-task','accountId':'fiction-account','platform':platform,
                                      'files':[file],'fields':fields,'state':'queued','platformId':None,
                                      'template':{'platform':platform,'confirmed':True,'parameters':params}})
                    run={'id':'fiction-run','state':'queued','packageId':'fiction-package','batchIndex':1,'items':items,
                         'accounts':[{'accountId':'fiction-account','port':port,'state':'queued','control':'resume',
                                      'binding':{'accountId':'fiction-account','uid':'fiction-uid','advertiserId':'fiction-ad','port':port}}]}
                    with store.connection(write=True) as conn:service._put(conn,'run',run['id'],run)
                    process=await asyncio.create_subprocess_exec(sys.executable,'-B','-m','tools.publisher_worker',
                        '--runtime',str(store.root),'--run-id',run['id'],'--account-id','fiction-account',
                        cwd=str(Path(__file__).resolve().parents[1]),stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
                    try: output,error=await asyncio.wait_for(process.communicate(),60)
                    except BaseException:
                        process.kill();await process.wait();raise
                    self.assertEqual(process.returncode,0,error.decode('utf8',errors='replace'))
                    with store.connection() as conn:result=service._run(conn,run['id'])
                    self.assertEqual([i['state'] for i in result['items']],['published','closed'],str(result)+str(await pages['qianchuan'].evaluate('window.events')))
                    self.assertEqual([i['platformId'] for i in result['items']],['fiction-work','fiction-plan'])
                    self.assertTrue(all(i['submitIntent'] for i in result['items']))
                    self.assertEqual(result['accounts'][0]['state'],'reported')
                    for platform,page in pages.items():
                        self.assertEqual(await page.evaluate('window.submits'),1)
                        self.assertEqual(await page.locator('#upload').evaluate('e=>e.files[0].name'),'fiction.mp4')
                        self.assertEqual(await page.get_by_label('作品简介' if platform=='douyin' else '标题',exact=True).input_value(),'fiction')
                    self.assertTrue(await pages['douyin'].evaluate('window.coverChosen'))
                    self.assertEqual(await pages['qianchuan'].get_by_label('日预算',exact=True).input_value(),'100')
                    self.assertEqual(await pages['qianchuan'].get_by_label('支付ROI目标',exact=True).input_value(),'2')
                    for label in ('出价方式','投放目标','投放位置'):
                        self.assertEqual(await pages['qianchuan'].get_by_label(label,exact=True).input_value(),'confirmed')
                    self.assertEqual(await pages['qianchuan'].evaluate('window.closedPlans'),['fiction-plan'])
                    self.assertEqual(await pages['qianchuan'].get_by_role('row').filter(has_text='unrelated-plan').get_by_role('switch').get_attribute('aria-checked'),'true')
                    self.assertTrue(requests)
                    self.assertEqual(await pages['douyin'].title(),'') # Worker detached without closing Chrome.
                finally: await context.close()


if __name__ == '__main__': unittest.main()
