"""Fictional page doubles exercise production adapter operations, without CDP."""
import asyncio
from contextlib import asynccontextmanager
import hashlib
from pathlib import Path
import tempfile
import sys
import types
import os
import socket
import subprocess
import unittest
from unittest.mock import AsyncMock, Mock, patch

from control_console import publisher_browser as b


class Locator:
    def __init__(self, text="", count=1, children=None, attrs=None):
        self.text, self.n, self.children, self.attrs = text, count, children or {}, attrs or {}
        self.clicks = 0
    async def count(self): return self.n
    async def inner_text(self, **kwargs): return self.text
    async def get_attribute(self, name): return self.attrs.get(name)
    def get_by_text(self, name, **kwargs): return self.children.get(name, Locator(count=0))
    def get_by_role(self, name, **kwargs): return self.children.get(name, Locator(count=0))
    def filter(self, **kwargs): return self.children.get(kwargs.get("has_text"), Locator(count=0))
    def nth(self, index): return self.children[index]
    async def click(self, **kwargs):
        self.clicks += 1
        if "on_click" in self.children: self.children["on_click"]()


class Page:
    url = "https://creator.douyin.com/creator-micro/content/publish"
    def __init__(self, body="抖音号：fiction\nUID：uid-fiction", roles=None):
        self.body, self.roles = body, roles or {}
    def locator(self, css): return Locator(self.body) if css == "body" else Locator(count=0)
    def get_by_role(self, role, **kwargs): return self.roles.get(role, Locator(count=0))
    async def goto(self, url, **kwargs): self.url = url
    def on(self, *args): pass
    def remove_listener(self, *args): pass


class BrowserTests(unittest.IsolatedAsyncioTestCase):
    async def test_ports_reject_bool_float_and_external_endpoint(self):
        for value in (True, 1.0, "9222", 0, 65536):
            with self.assertRaises(b.BrowserFailure): b._port(value)
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock()
        response.read.return_value = b'{"webSocketDebuggerUrl":"ws://example.com:9222/devtools/browser/a"}'
        with patch.object(b, "build_opener") as opener:
            opener.return_value.open.return_value = response
            with self.assertRaisesRegex(b.BrowserFailure, "本机"): b._endpoint(9222)

    async def test_probe_does_not_guess_nickname_or_first_tab(self):
        page = Page("昵称：fiction\n粉丝：123")
        browser = Mock(contexts=[Mock(pages=[page])])
        @asynccontextmanager
        async def connection(port): yield browser
        with patch.object(b, "_connection", connection):
            result = await b.probe_browser(9222)
        self.assertEqual(result["douyin"]["uid"], "")
        self.assertFalse(result["capabilities"]["douyin"]["supported"])
        browser.close.assert_not_called()

    async def test_identity_conflicts_are_unknown(self):
        identity = await b._identity(Page("抖音号：fiction\nUID：one\nUID：two"), "douyin")
        self.assertEqual(identity["uid"], "")

    async def test_upload_count_must_match_each_file(self):
        uploaded = Locator(children={"a.mp4": Locator(children={"上传成功": Locator()})})
        page = Page(roles={"listitem": uploaded})
        adapter = b._Publisher(page, "qianchuan", {}, lambda *args: None)
        with patch.object(b.asyncio, "sleep", new=AsyncMock()):
            with self.assertRaisesRegex(b.BrowserFailure, "逐文件"):
                await adapter.wait_uploads([{"path": "a.mp4"}, {"path": "b.mp4"}])
        await adapter.wait_uploads([{"path": "a.mp4"}])

    async def test_receipt_does_not_accept_first_card_or_button_disappearance(self):
        adapter = b._Publisher(Page(roles={"listitem": Locator("旧作品 发布成功")}), "douyin", {}, lambda *args: None)
        with patch.object(b.asyncio, "sleep", new=AsyncMock()):
            with self.assertRaisesRegex(b.BrowserFailure, "唯一精确"):
                await adapter.receipt()

    async def test_receipt_requires_id_and_stable_identity(self):
        receipt = Locator(count=1, children={0: Locator("作品ID：video-123\n审核中")})
        page = Page(roles={"alert": receipt})
        identity = await b._identity(page, "douyin")
        result = await b._Publisher(page, "douyin", identity, lambda *args: None).receipt()
        self.assertEqual(result["platformId"], "video-123")
        self.assertEqual(result["state"], "pending_review")
        page.body = "抖音号：other\nUID：other"
        with self.assertRaisesRegex(b.BrowserFailure, "身份改变"):
            await b._Publisher(page, "douyin", identity, lambda *args: None).receipt()

    async def test_close_only_exact_created_plan_and_verify_state(self):
        closed = Locator(count=0)
        target_switch = Locator(attrs={"aria-checked": "true"})
        target_switch.children["on_click"] = lambda: setattr(closed, "n", 1)
        row = Locator(children={"switch": target_switch, "已关闭": closed})
        unrelated = Locator(attrs={"aria-checked": "true"})
        adapter = b._Publisher(Page(roles={"switch": unrelated}), "qianchuan", {}, lambda *args: None)
        adapter.exact_row = AsyncMock(return_value=row)
        result = await adapter.close_plan({"platformId": "plan-456", "evidence": {}})
        self.assertEqual(result["state"], "closed")
        self.assertEqual(target_switch.clicks, 1)
        self.assertEqual(unrelated.clicks, 0)
        self.assertTrue(all(call.args == ("plan-456",) for call in adapter.exact_row.call_args_list))

    async def test_qianchuan_receipt_checkpoints_id_before_identity_or_status_failure(self):
        identity = {"accountId": "fiction", "advertiserId": "fiction-advertiser"}
        for failure in ("identity", "status"):
            with self.subTest(failure=failure):
                checkpoints = []
                receipt = Locator(children={0: Locator("计划ID：plan-fiction\n等待处理")})
                adapter = b._Publisher(Page(roles={"alert": receipt}), "qianchuan", identity,
                                       lambda stage, evidence=None: checkpoints.append((stage, evidence)))
                async def read_identity(*args):
                    self.assertEqual(checkpoints[-1][0], "submitted")
                    self.assertEqual(checkpoints[-1][1]["platformId"], "plan-fiction")
                    return {**identity, "advertiserId": "changed"} if failure == "identity" else identity
                with patch.object(b, "_identity", new=read_identity):
                    with self.assertRaisesRegex(b.BrowserFailure, "身份改变" if failure == "identity" else "没有明确成功"):
                        await adapter.receipt()
                self.assertEqual(checkpoints[-1][1]["platformId"], "plan-fiction")

    async def test_qianchuan_response_checkpoints_id_before_exact_row_failure(self):
        checkpoints = []
        identity = {"accountId": "fiction", "advertiserId": "fiction-advertiser"}
        observer = b._SubmissionObserver(Page(), "qianchuan", identity, {"files": [{"path": "fiction.mp4"}]})
        observer.candidates = [{"platformId": "plan-fiction"}]
        adapter = b._Publisher(Page(), "qianchuan", identity,
                               lambda stage, evidence=None: checkpoints.append((stage, evidence)))
        async def missing_row(identifier):
            self.assertEqual(identifier, "plan-fiction")
            self.assertEqual(checkpoints[-1][0], "submitted")
            self.assertEqual(checkpoints[-1][1]["platformId"], identifier)
            raise b.BrowserFailure("精确计划行无法定位")
        adapter.exact_row = AsyncMock(side_effect=missing_row)
        with self.assertRaisesRegex(b.BrowserFailure, "无法定位"):
            await observer.verified_result(adapter)
        adapter.exact_row.assert_awaited_once_with("plan-fiction")
        self.assertEqual(checkpoints[-1][1]["platformId"], "plan-fiction")

    async def test_file_changed_during_prepare_blocks_final_submission(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fiction.mp4"
            path.write_bytes(b"fiction")
            item = {"platform": "douyin", "fields": ["fiction"] * 16, "files": [
                {"path": str(path), "bytes": 7, "sha256": hashlib.sha256(b"fiction").hexdigest()}]}
            submit = Locator()
            page = Page(roles={"button": submit})
            identity = await b._identity(page, "douyin")
            checkpoints = []
            @asynccontextmanager
            async def connection(port): yield Mock()
            async def change_file(*args): path.write_bytes(b"changed")
            with patch.object(b, "_connection", connection), patch.object(b, "_select_page", new=AsyncMock(return_value=(page, identity))), patch.object(b._Publisher, "prepare", new=AsyncMock(side_effect=change_file)), patch.object(b._Publisher, "receipt", new=AsyncMock()) as receipt:
                with self.assertRaisesRegex(b.BrowserFailure, "身份已改变"):
                    await b.execute_item(9222, {"port": 9222}, {"platform": "douyin", "confirmed": True, "parameters": {"coverMode": "recommended"}}, item,
                                         lambda stage, evidence=None: checkpoints.append(stage))
            self.assertEqual(submit.clicks, 0)
            self.assertNotIn("before_submit", checkpoints)
            self.assertNotIn("submitted", checkpoints)
            receipt.assert_not_awaited()

    async def test_checkpoint_exception_propagates_unchanged(self):
        stop = RuntimeError("paused")
        def checkpoint(*args): raise stop
        adapter = b._Publisher(Page(), "douyin", {}, checkpoint)
        with self.assertRaises(RuntimeError) as caught:
            await adapter.action(Locator(), "click")
        self.assertIs(caught.exception, stop)
        browser_stop = b.BrowserFailure("paused after submit")
        adapter.checkpoint = Mock(side_effect=browser_stop)
        with self.assertRaises(b.BrowserFailure) as caught:
            await adapter.receipt(allow_network_fallback=True)
        self.assertIs(caught.exception, browser_stop)

    async def test_template_has_no_budget_defaults(self):
        for parameters in ({}, {"dailyBudget": "500"}, {"dailyBudget": "NaN", "roi": "1", "bidStrategy": "fiction", "target": "fiction", "placement": "fiction"}):
            with self.assertRaises(b.BrowserFailure): b.validate_template("qianchuan", parameters)
        b.validate_template("qianchuan", dict(dailyBudget="1", roi="1", bidStrategy="fiction", target="fiction", placement="fiction"))

    async def test_changed_file_and_multi_video_douyin_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fiction.mp4"
            path.write_bytes(b"fiction")
            file = {"path": str(path), "bytes": 7, "sha256": hashlib.sha256(b"fiction").hexdigest()}
            item = {"platform": "douyin", "fields": ["fiction"] * 16, "files": [file]}
            b._verify_item(item)
            item["files"] = [file, file]
            with self.assertRaises(b.BrowserFailure): b._verify_item(item)
            item["files"] = [file]
            path.write_bytes(b"changed")
            with self.assertRaisesRegex(b.BrowserFailure, "身份"): b._verify_item(item)

    async def test_reconcile_without_precise_id_never_connects_or_submits(self):
        with patch.object(b, "_connection") as connection:
            result = await b.reconcile_item(9222, {}, {"platform": "douyin"})
        self.assertEqual(result["state"], "unknown")
        connection.assert_not_called()

    async def test_disconnect_stops_driver_never_closes_user_browser(self):
        browser = Mock()
        driver = Mock()
        driver.chromium.connect_over_cdp = AsyncMock(return_value=browser)
        driver.stop = AsyncMock()
        manager = Mock(start=AsyncMock(return_value=driver))
        module = types.ModuleType("playwright.async_api")
        module.async_playwright = Mock(return_value=manager)
        with patch.dict(sys.modules, {"playwright.async_api": module}), patch.object(b, "_endpoint", return_value="ws://127.0.0.1:9222/devtools/browser/fiction"):
            with self.assertRaisesRegex(RuntimeError, "stop"):
                async with b._connection(9222):
                    raise RuntimeError("stop")
        driver.stop.assert_awaited_once()
        browser.close.assert_not_called()

    async def test_final_checkpoint_is_immediately_before_single_submit(self):
        order = []
        submit = Locator()
        submit.children["on_click"] = lambda: order.append("click")
        page = Page(roles={"button": submit})
        identity = await b._identity(page, "douyin")
        @asynccontextmanager
        async def connection(port): yield Mock()
        def checkpoint(stage, evidence=None): order.append(stage)
        item = {"platform": "douyin", "fields": ["fiction"] * 16, "files": [{"path": "fiction.mp4"}]}
        with patch.object(b, "_connection", connection), patch.object(b, "_verify_item"), patch.object(b, "_select_page", new=AsyncMock(return_value=(page, identity))), patch.object(b._Publisher, "prepare", new=AsyncMock()), patch.object(b._Publisher, "receipt", new=AsyncMock(return_value={"state": "published", "platformId": "fiction-video", "evidence": {}})):
            result = await b.execute_item(9222, {"port": 9222}, {"platform": "douyin", "confirmed": True, "parameters": {"coverMode": "recommended"}}, item, checkpoint)
        self.assertEqual(order[order.index("click") - 1], "before_submit")
        self.assertEqual(order[order.index("click") + 1], "submitted")
        self.assertEqual(submit.clicks, 1)
        self.assertEqual(result["platformId"], "fiction-video")

    async def test_navigation_opens_creator_upload_in_existing_page(self):
        page = Page()
        identity = await b._identity(page, "douyin")
        adapter = b._Publisher(page, "douyin", identity, lambda *args: None)
        await adapter.navigate()
        self.assertEqual(page.url, "https://creator.douyin.com/creator-micro/content/upload")

    async def test_prepare_url_navigation_preserves_stable_identity_and_allows_submit(self):
        submit = Locator()
        page = Page(roles={"button": submit})
        identity = await b._identity(page, "douyin")
        destination = "https://creator.douyin.com/creator-micro/content/publish?fiction=uploaded"
        @asynccontextmanager
        async def connection(port): yield Mock()
        async def prepare(*args): page.url = destination
        checkpoints = []
        async def receipt(adapter, **kwargs):
            self.assertEqual(adapter.identity["url"], destination)
            return {"state": "published", "platformId": "fiction-video", "evidence": {}}
        item = {"platform": "douyin", "fields": ["fiction"] * 16, "files": [{"path": "fiction.mp4"}]}
        with patch.object(b, "_connection", connection), patch.object(b, "_verify_item"), patch.object(b, "_select_page", new=AsyncMock(return_value=(page, identity))), patch.object(b._Publisher, "prepare", new=AsyncMock(side_effect=prepare)), patch.object(b._Publisher, "receipt", new=receipt):
            result = await b.execute_item(9222, {"port": 9222}, {"platform": "douyin", "confirmed": True, "parameters": {"coverMode": "recommended"}}, item,
                                          lambda stage, evidence=None: checkpoints.append((stage, evidence)))
        self.assertEqual(submit.clicks, 1)
        self.assertEqual(result["platformId"], "fiction-video")
        intent = next(evidence for stage, evidence in checkpoints if stage == "before_submit")
        self.assertEqual(intent["identity"]["url"], destination)
        self.assertEqual(intent["identity"]["uid"], "uid-fiction")

    async def test_prepare_changed_stable_identity_or_platform_blocks_submit(self):
        for changed in ("accountId", "uid", "platform"):
            with self.subTest(changed=changed):
                submit = Locator()
                page = Page(roles={"button": submit})
                identity = await b._identity(page, "douyin")
                @asynccontextmanager
                async def connection(port): yield Mock()
                async def prepare(*args):
                    if changed == "accountId": page.body = "抖音号：other\nUID：uid-fiction"
                    elif changed == "uid": page.body = "抖音号：fiction\nUID：uid-other"
                    else: page.url = "https://qianchuan.jinritemai.com/fiction"
                stages = []
                item = {"platform": "douyin", "fields": ["fiction"] * 16, "files": [{"path": "fiction.mp4"}]}
                with patch.object(b, "_connection", connection), patch.object(b, "_verify_item"), patch.object(b, "_select_page", new=AsyncMock(return_value=(page, identity))), patch.object(b._Publisher, "prepare", new=AsyncMock(side_effect=prepare)), patch.object(b._Publisher, "receipt", new=AsyncMock()) as receipt:
                    with self.assertRaisesRegex(b.BrowserFailure, "最终提交前"):
                        await b.execute_item(9222, {"port": 9222}, {"platform": "douyin", "confirmed": True, "parameters": {"coverMode": "recommended"}}, item,
                                             lambda stage, evidence=None: stages.append(stage))
                self.assertEqual(submit.clicks, 0)
                self.assertNotIn("before_submit", stages)
                receipt.assert_not_awaited()

    async def test_recommended_cover_explicitly_uses_second_old_selector(self):
        recommended = Locator()
        page = Page()
        page.locator = lambda css: recommended if css == "div.recommendCover-vWWsHB:nth-of-type(2)" else Locator(count=0)
        await b._Publisher(page, "douyin", {}, lambda *args: None).cover({"coverMode": "recommended"})
        self.assertEqual(recommended.clicks, 1)
        b.validate_template("douyin", {"coverMode": "recommended"})
        with self.assertRaises(b.BrowserFailure): b.validate_template("douyin", {"coverMode": "file"})

    async def test_normal_ui_response_requires_exact_account_and_each_filename(self):
        identity = {"accountId": "fiction", "uid": "uid-fiction", "url": Page.url}
        observer = b._SubmissionObserver(Page(), "douyin", identity, {"files": [{"path": "fiction.mp4"}]})
        response = Mock(url="https://creator.douyin.com/ui/publish", status=200)
        response.request = Mock(method="POST", post_data='{"uid":"uid-fiction","filename":"fiction.mp4"}')
        response.json = AsyncMock(return_value={"status_code": 0, "data": {"aweme_id": "new-video"}})
        await observer.read(response)
        self.assertEqual(observer.candidates[0]["platformId"], "new-video")
        response.request.post_data = '{"uid":"other","filename":"fiction.mp4"}'
        await observer.read(response)
        self.assertEqual(len(observer.candidates), 1)
        row = Locator("new-video 审核中", children={"审核中": Locator()})
        adapter = b._Publisher(Page(), "douyin", identity, lambda *args: None)
        adapter.exact_row = AsyncMock(return_value=row)
        result = await observer.verified_result(adapter)
        self.assertEqual(result["platformId"], "new-video")
        adapter.exact_row.assert_awaited_once_with("new-video")

    async def test_ui_publish_can_correlate_exact_uploaded_video_id(self):
        observer = b._SubmissionObserver(Page(), "douyin", {"uid": "uid-fiction"}, {"files": [{"path": "fiction.mp4"}]})
        upload = Mock(url="https://creator.douyin.com/ui/upload", status=200,
                      request=Mock(method="POST", post_data='{"filename":"fiction.mp4"}'))
        upload.json = AsyncMock(return_value={"status_code": 0, "data": {"video_id": "uploaded-fiction"}})
        await observer.read(upload, submitted=False)
        self.assertEqual(observer.upload_ids, {"fiction.mp4": "uploaded-fiction"})
        publish = Mock(url="https://creator.douyin.com/ui/publish", status=200,
                       request=Mock(method="POST", post_data='{"uid":"uid-fiction","video_id":"uploaded-fiction"}'))
        publish.json = AsyncMock(return_value={"status_code": 0, "data": {"aweme_id": "published-fiction"}})
        await observer.read(publish)
        self.assertEqual(observer.candidates[0]["platformId"], "published-fiction")

    @asynccontextmanager
    async def isolated_field_page(self):
        chrome = Path("C:/Program Files/Google/Chrome/Application/chrome.exe")
        if not chrome.is_file(): self.skipTest("无本机Chrome")
        from playwright.async_api import async_playwright
        async with async_playwright() as driver:
            browser = await driver.chromium.launch(executable_path=str(chrome), headless=True,
                                                   args=["--disable-background-networking"])
            try:
                page = await browser.new_page()
                await page.set_content('<label>虚构标题<input id="title"></label>'
                                       '<div id="caption" role="textbox" aria-label="作品简介" contenteditable="true"></div>')
                yield page
            finally:
                await browser.close()

    @unittest.skipUnless(os.environ.get("PUBLISHER_BROWSER_INTEGRATION") == "1", "显式启用隔离Chrome集成测试")
    async def test_hydration_preserves_long_string_uid_and_rejects_unsafe_number(self):
        async with self.isolated_field_page() as page:
            await page.set_content("<p>虚构账号页面，无身份文本</p>")
            await page.evaluate("window.__INITIAL_STATE__ = {userInfo: {short_id: 'fiction', uid: '17519866675268001'}}")
            identity = await b._identity(page, "douyin")
            self.assertEqual(identity["accountId"], "fiction")
            self.assertEqual(identity["uid"], "17519866675268001")
            browser = Mock(contexts=[Mock(pages=[page])])
            with patch.object(b, "_platform", return_value="douyin"):
                selected, _ = await b._select_page(browser, "douyin", identity)
                self.assertIs(selected, page)
                for expression in ("Number('17519866675268001')", "true"):
                    with self.subTest(uid=expression):
                        await page.evaluate(f"window.__INITIAL_STATE__.userInfo.uid = {expression}")
                        unsafe = await b._identity(page, "douyin")
                        self.assertEqual(unsafe["accountId"], "fiction")
                        self.assertEqual(unsafe["uid"], "")
                        with self.assertRaisesRegex(b.BrowserFailure, "无法唯一确认"):
                            await b._select_page(browser, "douyin", identity)

    @unittest.skipUnless(os.environ.get("PUBLISHER_BROWSER_INTEGRATION") == "1", "显式启用隔离Chrome集成测试")
    async def test_field_reads_back_input_and_chinese_contenteditable(self):
        async with self.isolated_field_page() as page:
            adapter = b._Publisher(page, "douyin", {}, lambda *args: None)
            for label, selector, value, property_name in (
                ("虚构标题", "#title", "虚构商品标题", "value"),
                ("作品简介", "#caption", "虚构中文作品简介，仅供测试", "innerText"),
            ):
                with self.subTest(label=label):
                    await adapter.field(label, value)
                    self.assertEqual(await page.locator(selector).evaluate(f"element => element.{property_name}"), value)

    @unittest.skipUnless(os.environ.get("PUBLISHER_BROWSER_INTEGRATION") == "1", "显式启用隔离Chrome集成测试")
    async def test_field_rejects_page_mutated_value_after_fill(self):
        async with self.isolated_field_page() as page:
            adapter = b._Publisher(page, "douyin", {}, lambda *args: None)
            await page.evaluate('''() => {
                document.querySelector('#title').addEventListener('input', event => event.target.value = '页面改写');
                document.querySelector('#caption').addEventListener('input', event => event.target.innerText = '页面改写');
            }''')
            for label in ("虚构标题", "作品简介"):
                with self.subTest(label=label):
                    with self.assertRaisesRegex(b.BrowserFailure, "页面未保留确认值"):
                        await adapter.field(label, "虚构确认内容")

    @unittest.skipUnless(os.environ.get("PUBLISHER_BROWSER_INTEGRATION") == "1", "显式启用隔离Chrome集成测试")
    async def test_isolated_real_cdp_disconnect_keeps_chrome_alive(self):
        chrome = Path("C:/Program Files/Google/Chrome/Application/chrome.exe")
        if not chrome.is_file(): self.skipTest("无本机Chrome")
        with tempfile.TemporaryDirectory(prefix="fiction-publisher-") as directory:
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            process = subprocess.Popen([str(chrome), "--headless=new", "--no-first-run", "--no-default-browser-check", "--disable-background-networking", f"--user-data-dir={directory}", f"--remote-debugging-port={port}", "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            try:
                for _ in range(60):
                    try:
                        await asyncio.to_thread(b._endpoint, port)
                        break
                    except b.BrowserFailure:
                        await asyncio.sleep(.2)
                async with b._connection(port) as browser:
                    page = browser.contexts[0].pages[0]
                    await page.set_content("<title>fictional isolated CDP</title><p>虚构测试</p>")
                    self.assertEqual(await page.title(), "fictional isolated CDP")
                self.assertIsNone(process.poll())
                self.assertTrue((await asyncio.to_thread(b._endpoint, port)).startswith("ws://127.0.0.1:"))
                async with b._connection(port) as browser:
                    self.assertEqual(await browser.contexts[0].pages[0].title(), "fictional isolated CDP")
            finally:
                process.terminate()
                await asyncio.to_thread(process.wait, 10)


if __name__ == "__main__": unittest.main()
