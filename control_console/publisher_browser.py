"""Conservative CDP publishing adapter. No browser launch, profile changes or close.

Connects only to already open account browsers and navigates their publisher UI. Selectors are
fail-closed: unsupported platform revisions require a compatibility update.
Playwright is optional and imported only when attaching to an explicit port.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlparse, parse_qs
from urllib.request import build_opener, HTTPRedirectHandler, ProxyHandler


class BrowserFailure(RuntimeError):
    """Action blocked or outcome uncertain; never permission to retry submission."""


def _port(port):
    if type(port) is not int or not 1 <= port <= 65535:
        raise BrowserFailure("浏览器端口必须是1至65535的整数")
    return port


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise BrowserFailure("CDP探测不允许重定向")


def _endpoint(port):
    _port(port)
    try:
        opener = build_opener(ProxyHandler({}), _NoRedirect())
        with opener.open(f"http://127.0.0.1:{port}/json/version", timeout=5) as response:
            endpoint = json.loads(response.read(65536))["webSocketDebuggerUrl"]
        parsed = urlparse(endpoint)
        if (parsed.scheme != "ws" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
                or parsed.port != port or parsed.username or parsed.password
                or not parsed.path.startswith("/devtools/browser/")):
            raise BrowserFailure("CDP WebSocket必须指向同一本机端口")
        # Avoid localhost DNS resolution changing the actual target.
        return f"ws://127.0.0.1:{port}{parsed.path}"
    except BrowserFailure:
        raise
    except Exception as exc:
        raise BrowserFailure("无法读取本机CDP端口") from exc


@asynccontextmanager
async def _connection(port):
    _port(port)
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise BrowserFailure("浏览器驱动依赖缺失；便携版请重新完整解压，源码版请在项目虚拟环境安装requirements-publishing.txt") from exc
    endpoint = await asyncio.to_thread(_endpoint, port)
    driver = await async_playwright().start()
    try:
        try:
            browser = await driver.chromium.connect_over_cdp(endpoint, timeout=10000)
        except Exception as exc:
            raise BrowserFailure("连接已打开的账号浏览器失败") from exc
        yield browser
    finally:
        # Stopping our transport releases CDP. Browser.close/Context.close would
        # close the user's account environment and must never be called here.
        await driver.stop()


def validate_template(platform, parameters):
    if not isinstance(parameters, dict):
        raise BrowserFailure("缺少已确认的发文模板参数")
    required = ({"coverMode", "coverPath"} if parameters.get("coverMode") == "file" else {"coverMode"}) if platform == "douyin" else {
        "dailyBudget", "bidStrategy", "roi", "target", "placement"}
    if platform not in {"douyin", "qianchuan"}:
        raise BrowserFailure("未知发文平台")
    if set(parameters) != required or any(not isinstance(parameters[k], str) or not parameters[k].strip() for k in required):
        raise BrowserFailure("模板字段不完整或含未支持参数：" + ",".join(sorted(required)))
    if platform == "douyin" and parameters["coverMode"] not in {"recommended", "file"}:
        raise BrowserFailure("封面方式须明确选择recommended或file")
    if platform == "qianchuan":
        for key in ("dailyBudget", "roi"):
            try:
                number = Decimal(parameters[key])
                if not number.is_finite() or number <= 0:
                    raise InvalidOperation
            except InvalidOperation as exc:
                raise BrowserFailure("千川预算和ROI必须为明确正数") from exc


def _platform(url):
    host = urlparse(url).hostname
    return {"creator.douyin.com": "douyin", "qianchuan.jinritemai.com": "qianchuan"}.get(host)


def _label_value(text, labels):
    values = set()
    for label in labels:
        values.update(re.findall(r"(?m)^\s*" + re.escape(label) + r"\s*[:：]\s*([A-Za-z0-9_-]+)\s*$", text))
    return next(iter(values)) if len(values) == 1 else ""


async def _identity(page, platform):
    text = await page.locator("body").inner_text(timeout=5000)
    identity = {"accountId": _label_value(text, ["抖音号", "账号ID"]), "url": page.url}
    if platform == "douyin":
        identity["uid"] = _label_value(text, ["UID", "uid"])
    else:
        identity["advertiserId"] = _label_value(text, ["广告账户ID", "广告主ID"])
    # Platform hydration data is read-only evidence, never a submitted identity.
    # Only explicit ID fields under account/user objects; never nickname/title.
    if not all(identity.values()):
        try:
            records = await page.evaluate("""() => {
                const roots=[window.__INITIAL_STATE__, window.__NEXT_DATA__?.props?.pageProps,
                             window.__INITIAL_STATE__?.user, window.__INITIAL_STATE__?.userInfo];
                const out=[];
                const id=v => typeof v==='string' ? v : Number.isSafeInteger(v) ? String(v) : '';
                for(const r of roots.filter(Boolean)) {
                  for(const u of [r.user_info,r.userInfo,r.accountInfo,r].filter(Boolean)) {
                    out.push({accountId:id(u.short_id??u.shortId??u.douyin_id),
                              uid:id(u.uid??u.user_id),
                              advertiserId:id(u.advertiser_id??u.advertiserId)});
                  }
                }
                return out;
            }""")
            for key in set(identity) - {"url"}:
                values = {r.get(key) for r in records if isinstance(r, dict) and r.get(key)}
                if identity[key]: values.add(identity[key])
                identity[key] = next(iter(values)) if len(values) == 1 else ""
        except Exception:
            pass  # Missing hydration data is not proof of any identity.
    return identity


async def _one(locator, description):
    if await locator.count() != 1:
        raise BrowserFailure("页面缺少或存在多个" + description)
    return locator


async def _select_page(browser, platform, binding):
    matches = []
    for context in browser.contexts:
        for page in context.pages:
            if _platform(page.url) != platform:
                continue
            identity = await _identity(page, platform)
            keys = ("accountId", "uid") if platform == "douyin" else ("accountId", "advertiserId")
            if all(identity.get(k) and identity[k] == binding.get(k) for k in keys):
                matches.append((page, identity))
    if len(matches) != 1:
        raise BrowserFailure("无法唯一确认已打开页面的账号身份，请人工打开对应页面并显示稳定账号ID")
    return matches[0]


async def probe_browser(port: int) -> dict:
    _port(port)
    result = {"port": port, "douyin": {"accountId": "", "uid": "", "url": ""},
              "qianchuan": {"accountId": "", "advertiserId": "", "url": ""}, "capabilities": {}}
    async with _connection(port) as browser:
        for platform in ("douyin", "qianchuan"):
            found = []
            candidates = []
            for context in browser.contexts:
                for page in context.pages:
                    if _platform(page.url) == platform:
                        found.append(await _identity(page, platform))
                        candidates.append(page)
            if len(found) == 1:
                result[platform] = found[0]
            reliable = all(result[platform].values())
            form = False
            if reliable:
                page = candidates[0]
                form = (await page.locator('input[type="file"][accept*="video"]').count() == 1
                        and await page.get_by_role("button", name="发布" if platform == "douyin" else "发布计划", exact=True).count() == 1)
            result["capabilities"][platform] = {"supported": bool(reliable and form), "identityReliable": reliable,
                "reason": "身份已读取；具体上传表单与结果回执仍须逐项检查，真实平台版本尚未验收" if reliable else "未唯一读取稳定身份；不猜昵称、首卡片或URL账户参数"}
    return result


class _Publisher:
    """Actual page operations; mocks exercise these same methods."""
    def __init__(self, page, platform, identity, checkpoint):
        self.page, self.platform, self.identity, self.checkpoint = page, platform, identity, checkpoint

    async def navigate(self):
        self.checkpoint("checking")
        if self.platform == "douyin":
            if not self.page.url.startswith("https://creator.douyin.com/creator-micro/content/upload"):
                await self.page.goto("https://creator.douyin.com/creator-micro/content/upload", wait_until="domcontentloaded", timeout=30000)
        elif await self.page.locator('input[type="file"][accept*="video"]').count() == 0:
            button = self.page.get_by_role("button", name="投放商品", exact=True)
            if await button.count() == 0:
                button = self.page.locator("div.create-btn > div.oc-button-wrap > button.ovui-button.ovui-button--md")
            await self.action(button, "click")
            account = self.page.get_by_label("抖音账号", exact=True)
            if await account.count() == 1:
                await self.action(account, "click")
                await self.action(self.page.get_by_role("option", name=self.identity["accountId"], exact=True), "click")
                confirm = self.page.get_by_role("button", name="确认", exact=True)
                if await confirm.count() == 1:
                    await self.action(confirm, "click")
        self.checkpoint("checking")
        # Re-check stable IDs after navigation; redirects/login/account switches stop.
        if _platform(self.page.url) != self.platform:
            raise BrowserFailure("发布导航离开平台或进入登录页")
        current = await _identity(self.page, self.platform)
        if any(current.get(k) != v for k, v in self.identity.items() if k != "url"):
            raise BrowserFailure("发布导航后无法重新确认稳定账号身份")
        self.identity = current

    async def cover(self, parameters):
        if parameters["coverMode"] == "file":
            cover = Path(parameters["coverPath"])
            if not cover.is_file():
                raise BrowserFailure("已确认封面文件不存在")
            await self.action(self.page.get_by_label("上传封面", exact=True), "set_input_files", str(cover))
            await _one(self.page.get_by_text("封面上传成功", exact=True), "封面成功证据")
            return
        # Explicitly confirmed second recommendation, matching old software policy.
        recommended = self.page.locator("div.recommendCover-vWWsHB:nth-of-type(2)")
        if await recommended.count() == 1:
            await self.action(recommended, "click")
        else:
            cover = self.page.get_by_text("选择封面", exact=True)
            if await cover.count() == 0:
                cover = self.page.locator("div.coverControl-CjlzqC:nth-of-type(1) > div.cover-Jg3T4p:nth-of-type(1) > div.filter-k_CjvJ:nth-of-type(2) > div.title-wA45Xd")
            await self.action(cover, "click")
            await self.action(self.page.get_by_role("button", name="选择横封面", exact=True), "click")
            await self.action(self.page.get_by_role("button", name="完成", exact=True), "click")

    async def action(self, locator, method, *args, **kwargs):
        self.checkpoint("checking")
        await _one(locator, "必要控件")
        try:
            await getattr(locator, method)(*args, timeout=5000, **kwargs)
        except Exception as exc:
            raise BrowserFailure("页面操作失败，已停止且不会自动重提") from exc
        self.checkpoint("checking")

    async def field(self, label, value, fallback=None):
        loc = self.page.get_by_label(label, exact=True)
        if await loc.count() == 0 and fallback:
            loc = self.page.locator(fallback)
        await self.action(loc, "fill", value)
        observed = await loc.evaluate("element => element.isContentEditable ? element.innerText : element.value")
        if observed != value:
            raise BrowserFailure("页面未保留确认值：" + label)

    async def wait_uploads(self, files):
        # Count actual per-file completion, never configured total/old cards.
        for _ in range(120):
            self.checkpoint("uploading")
            completed = 0
            for file in files:
                row = self.page.get_by_role("listitem").filter(has_text=Path(file["path"]).name)
                if await row.count() == 1 and await row.get_by_text("上传成功", exact=True).count() == 1:
                    completed += 1
            if completed == len(files):
                return
            await asyncio.sleep(.5)
        raise BrowserFailure("上传完成数量无法逐文件确认")

    async def prepare(self, item, parameters):
        fields, files = item["fields"], item["files"]
        if self.platform == "qianchuan":
            add = self.page.get_by_role("button", name="添加商品", exact=True)
            if await add.count() == 1:
                await self.action(add, "click")
            await self.field("商品ID", fields[5], 'input[placeholder="请输入商品名称/ID搜索"]')
            await self.action(self.page.get_by_role("button", name="搜索", exact=True), "click")
            product = self.page.get_by_role("option", name=fields[5], exact=True)
            if await product.count() == 0:
                product = self.page.locator("div.card-product-item").filter(has=self.page.get_by_text(fields[5], exact=True))
            await self.action(product, "click")
            confirm = self.page.get_by_role("button", name="确定", exact=True)
            if await confirm.count() == 1:
                await self.action(confirm, "click")
            for _ in range(5):
                if await self.page.locator('input[type="file"][accept*="video"]').count() == 1:
                    break
                await self.action(self.page.get_by_role("button", name="下一步", exact=True), "click")
        upload = self.page.locator('input[type="file"][accept*="video"]')
        await self.action(upload, "set_input_files", [f["path"] for f in files])
        await self.wait_uploads(files)
        if self.platform == "douyin":
            await self.field("作品简介", fields[4], 'div.ace-line > div[contenteditable="true"]')
            await self.field("商品链接", fields[7], 'input[placeholder="粘贴商品链接"]')
            await self.action(self.page.get_by_role("button", name="添加链接", exact=True), "click")
            await self.field("商品短标题", fields[4], 'input[placeholder="请输入商品短标题"]')
            await self.cover(parameters)
        else:
            # Follow normal wizard only while exactly one next-step is present.
            # Missing necessary confirmed parameter fields always stops here.
            for _ in range(5):
                if await self.page.get_by_label("日预算", exact=True).count() == 1:
                    break
                await self.action(self.page.get_by_role("button", name="下一步", exact=True), "click")
            for key, label in (("dailyBudget", "日预算"), ("roi", "支付ROI目标")):
                await self.field(label, parameters[key])
            for key, label in (("bidStrategy", "出价方式"), ("target", "投放目标"), ("placement", "投放位置")):
                await self.action(self.page.get_by_label(label, exact=True), "select_option", label=parameters[key])
                selected = self.page.get_by_label(label, exact=True).locator("option:checked")
                if (await selected.inner_text()).strip() != parameters[key]:
                    raise BrowserFailure("未确认千川选择值：" + label)
            await self.field("标题", fields[4], 'input[placeholder="请输入标题或使用系统推荐标题"]')

    async def receipt(self, allow_network_fallback=False):
        alerts = self.page.get_by_role("alert")
        for _ in range(20):
            self.checkpoint("submitted", {"identity": self.identity})
            if await alerts.count():
                break
            await asyncio.sleep(.5)
        receipts = []
        for index in range(await alerts.count()):
            text = await alerts.nth(index).inner_text()
            key = "作品ID" if self.platform == "douyin" else "计划ID"
            identifier = _label_value(text, [key])
            if identifier:
                receipts.append((identifier, text))
        if len(receipts) != 1:
            if allow_network_fallback and not receipts:
                return None
            raise BrowserFailure("结果不明：提交后未取得唯一精确作品/计划ID，请人工核验，禁止重提")
        identifier, text = receipts[0]
        # Keep the exact ID even if a later identity/status/close check fails.
        # Losing it here would make a created plan impossible to reconcile.
        self.checkpoint("submitted", {"identity": self.identity, "platformId": identifier, "receipt": text})
        keys = ("accountId", "uid") if self.platform == "douyin" else ("accountId", "advertiserId")
        now = await _identity(self.page, self.platform)
        if any(now.get(k) != self.identity[k] for k in keys):
            raise BrowserFailure("结果不明：提交后账号身份改变")
        if not any(word in text for word in ("发布成功", "审核中", "创建成功")):
            raise BrowserFailure("结果不明：没有明确成功或审核中回执")
        return {"state": "pending_review" if "审核中" in text else "published", "platformId": identifier,
                "evidence": {**self.identity, "receipt": text}}

    async def close_plan(self, result):
        identifier = result["platformId"]
        self.checkpoint("closing", result)
        row = await self.exact_row(identifier)
        if await row.get_by_text("已关闭", exact=True).count() != 1:
            switch = await _one(row.get_by_role("switch"), "本次计划开关")
            if await switch.get_attribute("aria-checked") != "true":
                raise BrowserFailure("本次计划开关状态不明确，停止关停")
            self.checkpoint("closing", result)
            await switch.click(timeout=5000)
            self.checkpoint("closing", result)
            # Never select an unrelated global switch or first plan.
            for _ in range(20):
                self.checkpoint("closing", result)
                row = await self.exact_row(identifier)
                if await row.get_by_text("已关闭", exact=True).count() == 1:
                    break
                await asyncio.sleep(.5)
            else:
                raise BrowserFailure("本次计划已创建但未读到精确ID已关闭状态")
        result["state"] = "closed"
        result["evidence"]["closedPlanId"] = identifier
        return result

    async def exact_row(self, identifier):
        if not re.fullmatch(r"[A-Za-z0-9_-]+", identifier):
            raise BrowserFailure("非法平台ID")
        row = self.page.get_by_role("row").filter(has=self.page.get_by_text(identifier, exact=True))
        return await _one(row, "精确计划/作品ID行")


def _verify_item(item):
    if item.get("platform") not in {"douyin", "qianchuan"} or len(item.get("fields", [])) != 16 or any(not isinstance(v, str) for v in item["fields"]):
        raise BrowserFailure("发布项必须包含平台及原始16列文本")
    files = item.get("files", [])
    if not files or (item["platform"] == "douyin" and len(files) != 1):
        raise BrowserFailure("抖音必须逐视频执行；千川必须有素材")
    names = set()
    for file in files:
        path = Path(file["path"])
        if path.name in names or not path.is_file() or path.suffix.lower() != ".mp4":
            raise BrowserFailure("素材不存在、重名或不是MP4")
        names.add(path.name)
        if path.stat().st_size != file.get("bytes") or hashlib.sha256(path.read_bytes()).hexdigest() != file.get("sha256"):
            raise BrowserFailure("素材身份已改变，禁止上传")
    if not all(item["fields"][i].strip() for i in (4, 5, 7)):
        raise BrowserFailure("缺少简介、完整商品ID或商品链接")


class _SubmissionObserver:
    """Observe UI-generated requests only; never invoke a platform private API.

    A response is usable only with explicit account and exact uploaded-file
    correlation in its originating request. Missing correlation stays unknown.
    """
    def __init__(self, page, platform, identity, item):
        self.page, self.platform, self.identity, self.item = page, platform, identity, item
        self.armed = False
        self.tasks, self.candidates, self.upload_ids = set(), [], {}

    def callback(self, response):
        task = asyncio.create_task(self.read(response, self.armed))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def read(self, response, submitted=True):
        try:
            request = response.request
            url = response.url
            if (_platform(url) != self.platform or request.method != "POST"
                    or not re.search(r"(?:publish|create|submit|upload)", url, re.I)
                    or response.status != 200):
                return
            payload = request.post_data or ""
            # The UI request must carry this account and every exact video name.
            # No title/product-only match can claim success for another video.
            account = self.identity["uid" if self.platform == "douyin" else "advertiserId"]
            try:
                request_data = json.loads(payload)
            except ValueError:
                request_data = parse_qs(payload)
            def values(value):
                if isinstance(value, dict):
                    return [v for child in value.values() for v in values(child)]
                if isinstance(value, list):
                    return [v for child in value for v in values(child)]
                return [str(value)]
            atoms = values(request_data)
            data = await asyncio.wait_for(response.json(), timeout=5)
            if not isinstance(data, dict) or data.get("status_code", data.get("code")) != 0:
                return
            body = data.get("data", data)
            if not submitted and re.search("upload", url, re.I) and isinstance(body, dict):
                video_id = body.get("video_id") or body.get("vid")
                matched = [Path(f["path"]).name for f in self.item["files"]
                           if Path(f["path"]).name in atoms or ('filename="' + Path(f["path"]).name + '"') in payload]
                if video_id and len(matched) == 1:
                    self.upload_ids[matched[0]] = str(video_id)
                return
            if not submitted or account not in atoms or any(
                    Path(f["path"]).name not in atoms and self.upload_ids.get(Path(f["path"]).name) not in atoms
                    for f in self.item["files"]):
                return
            keys = ("aweme_id", "item_id") if self.platform == "douyin" else ("plan_id", "ad_id")
            ids = {str(body[key]) for key in keys if isinstance(body, dict) and body.get(key)}
            if len(ids) == 1:
                self.candidates.append({"platformId": ids.pop(), "responseUrl": url,
                                        "correlation": "ui-post-account-and-exact-files-or-upload-ids"})
        except Exception:
            return  # A malformed/unrelated network reply is never success.

    async def verified_result(self, adapter):
        for _ in range(20):
            adapter.checkpoint("submitted", {"identity": adapter.identity})
            if self.tasks:
                await asyncio.gather(*list(self.tasks))
            ids = {c["platformId"] for c in self.candidates}
            if len(ids) == 1:
                identifier = ids.pop()
                adapter.checkpoint("submitted", {"identity": adapter.identity, "platformId": identifier, "correlation": "ui-response"})
                # A normal response alone is insufficient: query exact UI row.
                row = await adapter.exact_row(identifier)
                for text, state in (("审核中", "pending_review"), ("发布成功", "published"), ("投放中", "published")):
                    if await row.get_by_text(text, exact=True).count() == 1:
                        return {"state": state, "platformId": identifier,
                                "evidence": {**adapter.identity, **self.candidates[0], "row": await row.inner_text()}}
            elif len(ids) > 1:
                raise BrowserFailure("结果不明：本次UI请求返回多个平台ID")
            await asyncio.sleep(.5)
        raise BrowserFailure("结果不明：未取得与本次账号及视频一致的精确平台ID和状态")


async def execute_item(port, binding, template, item, checkpoint) -> dict:
    _port(port)
    checkpoint("checking")
    if binding.get("port") != port or template.get("confirmed") is not True or template.get("platform") != item.get("platform"):
        raise BrowserFailure("端口绑定或已确认模板不匹配")
    if (template.get("accountId") not in (None, binding.get("accountId"))
            or item.get("accountId") not in (None, binding.get("accountId"))):
        raise BrowserFailure("发布项、模板与绑定账号不匹配")
    if item.get("platformId") or item.get("state") in {"submitted", "published", "pending_review", "closed", "unknown"}:
        raise BrowserFailure("已提交或结果不明项禁止重复提交")
    _verify_item(item)
    validate_template(item["platform"], template.get("parameters"))
    async with _connection(port) as browser:
        checkpoint("checking")
        page, identity = await _select_page(browser, item["platform"], binding)
        adapter = _Publisher(page, item["platform"], identity, checkpoint)
        await adapter.navigate()
        identity = adapter.identity
        # An old receipt must not later be mistaken for this submission.
        if await page.get_by_role("alert").count():
            raise BrowserFailure("页面存在旧回执，请人工处理后重新核对，禁止误判为本次成功")
        observer = _SubmissionObserver(page, item["platform"], identity, item)
        page.on("response", observer.callback)
        try:
            await adapter.prepare(item, template["parameters"])
            if observer.tasks:
                await asyncio.gather(*list(observer.tasks))
            submit = await _one(page.get_by_role("button", name="发布" if item["platform"] == "douyin" else "发布计划", exact=True), "最终提交按钮")
            current = await _identity(page, item["platform"])
            stable_keys = ("accountId", "uid") if item["platform"] == "douyin" else ("accountId", "advertiserId")
            if (_platform(page.url) != item["platform"]
                    or any(not identity.get(key) or current.get(key) != identity[key] for key in stable_keys)):
                raise BrowserFailure("最终提交前身份改变")
            # Upload wizards may navigate without changing the authenticated account.
            identity = current
            adapter.identity = current
            observer.identity = current
            _verify_item(item)
            observer.armed = True
            checkpoint("before_submit", {"identity": identity})
            # Nothing except the final click may intervene after durable intent.
            try:
                await submit.click(timeout=5000)
            except Exception as exc:
                raise BrowserFailure("结果不明：提交点击异常，禁止自动重提") from exc
            checkpoint("submitted", {"identity": identity})
            result = await adapter.receipt(allow_network_fallback=True)
            if result is None:
                result = await observer.verified_result(adapter)
        finally:
            page.remove_listener("response", observer.callback)
            for task in list(observer.tasks): task.cancel()
            if observer.tasks:
                await asyncio.gather(*list(observer.tasks), return_exceptions=True)
        checkpoint("submitted", result)
        return await adapter.close_plan(result) if item["platform"] == "qianchuan" else result


async def reconcile_item(port, binding, item) -> dict:
    _port(port)
    identifier = item.get("platformId") or item.get("priorEvidence", {}).get("platformId")
    if not identifier:
        return {"state": "unknown", "platformId": "", "evidence": {"reason": "无精确平台ID，不按商品或首条记录推断"}}
    async with _connection(port) as browser:
        page, identity = await _select_page(browser, item["platform"], binding)
        adapter = _Publisher(page, item["platform"], identity, lambda *args: None)
        try:
            row = await adapter.exact_row(identifier)
        except BrowserFailure:
            return {"state": "unknown", "platformId": identifier, "evidence": identity}
        for text, state in (("已关闭", "closed"), ("审核中", "pending_review"), ("发布成功", "published")):
            if await row.get_by_text(text, exact=True).count() == 1:
                if state == "closed" and item["platform"] != "qianchuan":
                    continue
                return {"state": state, "platformId": identifier, "evidence": {**identity, "row": await row.inner_text()}}
        return {"state": "unknown", "platformId": identifier, "evidence": identity}
