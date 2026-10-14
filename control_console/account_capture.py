"""006A: user supplied, local-only account evidence and explicit confirmation.

Original inputs and extraction versions live below runtime. OCR never verifies
an account, and neither OCR nor a clipboard value can rename an execution task.
"""
from __future__ import annotations

import base64
import binascii
import csv
import hashlib
import io
import json
import re
import uuid
from html.parser import HTMLParser
from pathlib import Path

from .importers import ImportFailure, split_categories
from .store import dump, now

CATEGORIES = ['食品/宠物', '百货/个护家清', '母婴']
CATEGORY_NAMES = {'食品-宠物': '食品/宠物', '食品/宠物': '食品/宠物',
                  '百货个护家清': '百货/个护家清', '百货/个护家清': '百货/个护家清', '母婴': '母婴'}
MAX_TEXT = 1024 * 1024
MAX_ROWS = 500
MAX_IMAGE = 12 * 1024 * 1024
LABELS = ['运营人', '抖音账号', '账号名称', 'UID', '合作码', '选品范围', '备注', '账号编号']


def fail(message, code='account_capture'):
    raise ImportFailure(code, message)


def fields8(values):
    if not isinstance(values, list) or len(values) != 8 or any(not isinstance(v, str) or len(v) > 4000 for v in values):
        fail('每行需提供 A:H 八列文本，每格最多4000字符。')
    return [v.strip() for v in values]


def row_issues(fields):
    issues = []
    if not fields[1] or not fields[2]:
        issues.append('请补全 B列账号和 C列来源名称。')
    for i in (1, 3, 4):
        value = fields[i]
        if value and (re.search(r'[\x00-\x1f\x7f]', value) or re.fullmatch(r'[+-]?\d+(?:\.\d+)?[eE][+-]?\d+', value)):
            issues.append(f'{chr(65+i)}列标识包含控制字符或科学计数法，请按原文修正。')
        elif value and re.fullmatch(r'[+-]?\d+\.\d+', value):
            issues.append(f'{chr(65+i)}列不能使用小数，请按原始标识文本修正。')
    if len(fields[1]) > 128 or len(fields[7]) > 100:
        issues.append('账号或公司编号过长，请核对列位置。')
    if not fields[5]:
        issues.append('F列选品范围缺失，不能从商品或其他账号推断。')
    if fields[7] and re.search(r'[\x00-\x1f\x7f]', fields[7]):
        issues.append('H列公司编号包含控制字符。')
    return issues


def suggested_categories(text):
    raw = split_categories(text)
    if not raw or any(v not in CATEGORY_NAMES for v in raw):
        return []
    return list(dict.fromkeys(CATEGORY_NAMES[v] for v in raw))


class _Table(HTMLParser):
    """Read cell text only. Never render pasted markup or follow its URLs."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows = []
        self.cells = None
        self.parts = None
        self.ignore = 0
        self.spanned = False

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self.ignore += 1
        if self.ignore:
            return
        if tag == 'tr':
            self.cells = []
        elif tag in ('td', 'th') and self.cells is not None:
            self.parts = []
            values = dict(attrs)
            if values.get('colspan', '1') != '1' or values.get('rowspan', '1') != '1':
                self.spanned = True
        elif tag == 'br' and self.parts is not None:
            self.parts.append('\n')

    def handle_data(self, data):
        if not self.ignore and self.parts is not None:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if tag in ('script', 'style'):
            self.ignore = max(0, self.ignore - 1)
            return
        if self.ignore:
            return
        if tag in ('td', 'th') and self.parts is not None and self.cells is not None:
            self.cells.append(''.join(self.parts))
            self.parts = None
        elif tag == 'tr' and self.cells is not None:
            self.rows.append(self.cells)
            self.cells = None
            if len(self.rows) > MAX_ROWS + 20:
                fail('本次表格行数过多，请按运营人分批粘贴。')


def parse_text(text, format='tsv'):
    if not isinstance(text, str) or not text.strip() or len(text.encode('utf-8')) > MAX_TEXT:
        fail('请粘贴不超过1MiB的账号表格文本。')
    warnings = []
    if format == 'html':
        parser = _Table()
        parser.feed(text)
        if parser.spanned:
            fail('粘贴区域含合并单元格，无法可靠对应 A:H。请复制未合并的完整八列，或改用截图校准。')
        rows = parser.rows
    elif format == 'tsv':
        try:
            rows = list(csv.reader(io.StringIO(text), delimiter='\t', strict=True))
        except csv.Error:
            fail('粘贴表格引号不完整，请重新复制完整区域。')
    else:
        fail('仅接受 HTML表格或制表符分隔文本。')
    extracted = []
    for number, values in enumerate(rows, 1):
        if not any(v.strip() for v in values):
            continue
        if len(values) >= 6 and values[0].strip() in ('运营人', 'A') and values[1].strip() in ('抖音账号', '账号', 'B'):
            continue
        if not 6 <= len(values) <= 8:
            fail(f'第{number}行不是 A:H 完整区域：需6—8列，G/H为空可省略；不能自动猜测缺失列。')
        fields = fields8(values + [''] * (8-len(values)))
        extracted.append({'fields': fields, 'sourceRow': number, 'issues': row_issues(fields)})
    if not extracted:
        fail('没有找到账号行，请复制 A:H 区域。')
    if len(extracted) > MAX_ROWS:
        fail('每次最多识别500行，请分批处理。')
    return {'rows': extracted, 'warnings': warnings}


class AccountCaptureService:
    def __init__(self, store, workbench):
        self.store = store
        self.workbench = workbench
        with store.connection(write=True) as conn:
            conn.execute('''CREATE TABLE IF NOT EXISTS account_captures (
                id TEXT PRIMARY KEY,created_at TEXT NOT NULL,kind TEXT NOT NULL,
                filename TEXT NOT NULL,payload TEXT NOT NULL)''')
            conn.execute('''CREATE TABLE IF NOT EXISTS account_profile_versions (
                id INTEGER PRIMARY KEY,account_id TEXT NOT NULL,created_at TEXT NOT NULL,
                source_id TEXT NOT NULL,capture_id TEXT NOT NULL,payload TEXT NOT NULL)''')

    @staticmethod
    def _id(value):
        try:
            if not isinstance(value, str) or str(uuid.UUID(value)) != value:
                raise ValueError()
        except ValueError:
            fail('账号草稿不存在。', 'capture_missing')
        return value

    def _read(self, conn, capture_id):
        self._id(capture_id)
        row = conn.execute('SELECT * FROM account_captures WHERE id=?', (capture_id,)).fetchone()
        if not row:
            fail('账号草稿不存在。', 'capture_missing')
        return dict(row), json.loads(row['payload'])

    def _context(self, conn):
        account_source = conn.execute("SELECT id,payload FROM sources WHERE kind='accounts' AND active=1").fetchone()
        profiles = {p['accountId']: p for p in json.loads(account_source['payload'])} if account_source else {}
        tasks = list(conn.execute('SELECT id,fields,archived FROM tasks ORDER BY id'))
        names = {}
        for task in tasks:
            if not task['archived']:
                f = json.loads(task['fields'])
                names.setdefault(f[3].strip(), [])
                if f[2] not in names[f[3].strip()]:
                    names[f[3].strip()].append(f[2])
        signature = {'accountSource': account_source['id'] if account_source else '',
                     'tasks': [tuple(t) for t in tasks]}
        return profiles, names, signature

    def _response(self, conn, row, doc):
        profiles, names, context = self._context(conn)
        version = doc['versions'][-1]
        rows = []
        for item in version['rows']:
            fields = item['fields']
            profile = profiles.get(fields[1])
            allowed = suggested_categories(fields[5])
            issues = list(item.get('issues', []))
            if not allowed:
                issues.append('F列未精确对应固定类别，请人工选择允许类别并复核。')
            if names.get(fields[1]) and fields[2] not in names[fields[1]]:
                issues.append('来源名称与任务名称不同，请核对后绑定，执行名称保持原样。')
            code = fields[7].casefold()
            if code and any(p.get('companyCode', '').strip().casefold() == code and p['accountId'] != fields[1] for p in profiles.values()):
                issues.append('H列公司编号已属于其他账号，保存前必须解决。')
            current_fields = profile.get('rawFields', ['', profile['accountId'], profile.get('sourceName', profile['name']), '', '', '、'.join(profile['allowed']), '', profile.get('companyCode', '')]) if profile else None
            differences = [{'column': chr(65+i), 'before': current_fields[i], 'after': fields[i]} for i in range(8) if current_fields and fields[i] and current_fields[i] != fields[i]]
            rows.append({**item, 'accountId': fields[1], 'taskNames': names.get(fields[1], []),
                         'currentProfile': profile, 'selected': fields[1] in names and item['id'] not in doc.get('confirmed', []),
                         'saved': item['id'] in doc.get('confirmed', []), 'allowed': allowed,
                         'issues': list(dict.fromkeys(issues)), 'differences': differences})
        token = hashlib.sha256(dump({'captureId': row['id'], 'version': version['id'],
                                     'context': context, 'confirmed': doc.get('confirmed', [])}).encode()).hexdigest()
        return {'ok': True, 'captureId': row['id'], 'revisionToken': token, 'kind': row['kind'],
                'filename': row['filename'], 'text': doc.get('text', ''), 'createdAt': row['created_at'],
                'imageUrl': '/api/account-captures/image?captureId=' + row['id'] if row['kind'] == 'image' else None,
                'rows': rows, 'categoryOptions': CATEGORIES, **{k: version[k] for k in ('warnings', 'columns', 'width', 'height') if k in version}}

    def preview(self, body):
        if not isinstance(body, dict):
            fail('识别请求必须为对象。')
        options = body.get('options', {})
        if not isinstance(options, dict):
            fail('校准参数必须为对象。')
        old = None
        if body.get('captureId'):
            with self.store.connection() as conn:
                old, doc = self._read(conn, body['captureId'])
            if old['kind'] != 'image':
                fail('仅截图支持重新校准识别。')
            if doc.get('confirmed'):
                fail('该截图已有已保存行，请新增截图草稿重新核对，保留旧识别版本。')
            content = Path(doc['originalPath']).read_bytes()
            kind, filename = old['kind'], old['filename']
        else:
            kind = body.get('kind')
            doc = {'versions': [], 'confirmed': []}
            if kind == 'text':
                text, format = body.get('text'), body.get('format', 'tsv')
                extracted = parse_text(text, format)
                content = text.encode('utf-8')
                filename = '粘贴账号表.' + ('html' if format == 'html' else 'tsv')
                doc.update(text=text, format=format)
            elif kind == 'image':
                filename = body.get('filename', '账号截图.png')
                if not isinstance(filename, str) or len(filename) > 255:
                    fail('截图文件名不合法。')
                filename = filename.replace('\\', '/').split('/')[-1]
                encoded = body.get('content')
                if not isinstance(encoded, str) or len(encoded) > MAX_IMAGE * 4 // 3 + 4:
                    fail('截图请限制在12MiB以内。')
                try:
                    content = base64.b64decode(encoded, validate=True)
                except (binascii.Error, ValueError):
                    fail('截图内容编码不正确。')
                if not content or len(content) > MAX_IMAGE:
                    fail('截图内容为空或超过12MiB。')
            else:
                fail('请选择粘贴表格或上传截图。')
        if kind == 'image':
            from .account_ocr import recognize_image, reconstruct_rows
            try:
                ocr = recognize_image(content)
                extracted = reconstruct_rows(ocr, options)
            except (RuntimeError, ImportError) as error:
                fail(str(error) if isinstance(error, RuntimeError) else '本机OCR图像组件不可用，请使用配置好的Python环境或粘贴表格。', 'ocr_unavailable')
            except ValueError as error:
                fail(str(error), 'ocr_input')
            extracted.setdefault('width', ocr['width'])
            extracted.setdefault('height', ocr['height'])
            if len(extracted.get('rows', [])) > MAX_ROWS:
                fail('截图行数超过500，请裁剪后分批识别。')
        capture_id = old['id'] if old else str(uuid.uuid4())
        digest = hashlib.sha256(content).hexdigest()
        if not old:
            suffix = '.bin' if kind == 'image' else '.txt'
            path = self.store._path('account-captures', capture_id, 'original' + suffix)
            self.store._write_once(path, content)
            doc.update(originalPath=str(path), originalSha256=digest)
        version_id = str(uuid.uuid4())
        for index, item in enumerate(extracted['rows']):
            item['fields'] = fields8(item['fields'])
            item['issues'] = list(dict.fromkeys(item.get('issues', []) + row_issues(item['fields'])))
            item['id'] = version_id + ':' + str(index)
        version = {**extracted, 'id': version_id, 'createdAt': now(), 'options': options}
        doc['versions'].append(version)
        self.store._write_once(self.store._path('account-captures', capture_id, version_id + '.json'), dump(version).encode('utf-8'))
        with self.workbench.guard, self.store.connection(write=True) as conn:
            if old:
                _, current = self._read(conn, capture_id)
                if current['versions'][-1]['id'] != doc['versions'][-2]['id'] or current.get('confirmed'):
                    fail('草稿已发生变化，请重新打开。', 'capture_stale')
                conn.execute('UPDATE account_captures SET payload=? WHERE id=?', (dump(doc), capture_id))
            else:
                conn.execute('INSERT INTO account_captures VALUES (?,?,?,?,?)', (capture_id, now(), kind, filename, dump(doc)))
            self.workbench._audit(conn, 'account_capture_preview', capture_id, {'kind': kind, 'rows': len(extracted['rows']), 'version': version_id})
            row, doc = self._read(conn, capture_id)
            return self._response(conn, row, doc)

    def draft(self, capture_id):
        with self.store.connection() as conn:
            row, doc = self._read(conn, capture_id)
            return self._response(conn, row, doc)

    def list_drafts(self):
        with self.store.connection() as conn:
            result = []
            for row in conn.execute('SELECT * FROM account_captures ORDER BY created_at DESC,rowid DESC LIMIT 100'):
                doc = json.loads(row['payload'])
                count = len(doc['versions'][-1]['rows'])
                result.append({'captureId': row['id'], 'kind': row['kind'], 'filename': row['filename'],
                               'createdAt': row['created_at'], 'status': 'confirmed' if count and len(doc.get('confirmed', [])) == count else 'draft', 'rowCount': count})
            return result

    def image(self, capture_id):
        with self.store.connection() as conn:
            row, doc = self._read(conn, capture_id)
        if row['kind'] != 'image':
            fail('该草稿不是截图。')
        data = Path(doc['originalPath']).read_bytes()
        if hashlib.sha256(data).hexdigest() != doc['originalSha256']:
            fail('原始截图快照校验不符。')
        mime = 'image/png' if data.startswith(b'\x89PNG\r\n\x1a\n') else 'image/jpeg'
        return data, mime

    def confirm(self, body):
        if not isinstance(body, dict) or body.get('confirmed') is not True:
            fail('请明确确认已逐行核对账号身份、F列范围和名称绑定。')
        captures, rows = body.get('captures'), body.get('rows')
        if not isinstance(captures, list) or not 1 <= len(captures) <= 50 or not isinstance(rows, list) or not 1 <= len(rows) <= 200:
            fail('请选择1—200条已核对的账号行，单次最多50个来源。')
        with self.workbench.guard, self.store.connection(write=True) as conn:
            documents = {}
            for item in captures:
                if not isinstance(item, dict):
                    fail('草稿版本不合法。')
                row, doc = self._read(conn, item.get('captureId'))
                if row['id'] in documents:
                    fail('本次草稿来源重复。')
                response = self._response(conn, row, doc)
                if item.get('revisionToken') != response['revisionToken']:
                    fail('账号、任务或识别版本发生变化，请重新打开草稿核对差异。', 'capture_stale')
                documents[row['id']] = (row, doc)
            existing, names, _ = self._context(conn)
            profiles, seen, selected_rows = [], set(), set()
            for item in rows:
                if not isinstance(item, dict) or item.get('verified') is not True or item.get('captureId') not in documents:
                    fail('每条保存行必须明确勾选已核对并属于本次草稿。')
                capture_id, row_id = item['captureId'], item.get('rowId')
                doc = documents[capture_id][1]
                original = next((r for r in doc['versions'][-1]['rows'] if r['id'] == row_id), None)
                if not original or row_id in doc.get('confirmed', []) or (capture_id, row_id) in selected_rows:
                    fail('选择行已保存或版本无效，请重新打开草稿。')
                f = fields8(item.get('fields'))
                errors = row_issues(f)
                if errors:
                    fail('；'.join(errors), 'capture_row')
                account = f[1]
                if account in seen:
                    fail('同一账号存在多条候选，请只选择核对正确的一行；不会自动覆盖冲突。', 'capture_duplicate')
                allowed, task_names = item.get('allowed'), item.get('taskNames', [])
                if not isinstance(allowed, list) or not allowed or any(v not in CATEGORIES for v in allowed):
                    fail('请从固定类别中选择并核对 F列允许范围。', 'capture_category')
                if not isinstance(task_names, list) or any(not isinstance(n, str) or n not in names.get(account, []) for n in task_names) or len(set(task_names)) != len(task_names):
                    fail('名称绑定必须选择该 B列账号的现有任务名称，不能改名或跨账号绑定。', 'capture_binding')
                if names.get(account) and not task_names:
                    fail('请明确核对并绑定该账号的原任务名称。', 'capture_binding')
                old = existing.get(account, {})
                old_fields = old.get('rawFields', ['', account, old.get('sourceName', old.get('name', '')), old.get('uid', ''), old.get('cooperationCode', ''), '', old.get('remark', ''), old.get('companyCode', '')])
                merged = [value if value else old_fields[i] for i, value in enumerate(f)]
                profile = {'accountId': account, 'name': task_names[0] if task_names else old.get('name', f[2]),
                           'sourceName': merged[2], 'taskNames': task_names or old.get('taskNames', []),
                           'allowed': list(dict.fromkeys(allowed)), 'rawFields': merged,
                           'uid': merged[3], 'cooperationCode': merged[4], 'operator': merged[0],
                           'remark': merged[6], 'companyCode': merged[7],
                           'evidence': '人工逐行核对账号资料 A:H；来源草稿 ' + capture_id,
                           'captureId': capture_id, 'captureVersion': doc['versions'][-1]['id'], 'captureRowId': row_id}
                profiles.append(profile)
                seen.add(account)
                selected_rows.add((capture_id, row_id))
            result = self.workbench._account_profiles(conn, profiles)
            for profile in profiles:
                conn.execute('INSERT INTO account_profile_versions (account_id,created_at,source_id,capture_id,payload) VALUES (?,?,?,?,?)',
                             (profile['accountId'], now(), result['sourceId'], profile['captureId'], dump(profile)))
            for capture_id, (row, doc) in documents.items():
                saved = [row_id for cid, row_id in selected_rows if cid == capture_id]
                doc['confirmed'] = doc.get('confirmed', []) + saved
                doc.setdefault('confirmations', []).append({'time': now(), 'sourceId': result['sourceId'],
                    'rows': [p for p in profiles if p['captureId'] == capture_id]})
                conn.execute('UPDATE account_captures SET payload=? WHERE id=?', (dump(doc), capture_id))
            self.workbench._audit(conn, 'account_capture_confirm', result['sourceId'], {'captures': list(documents), 'accounts': list(seen)})
            remaining = sum(len(doc['versions'][-1]['rows']) - len(doc['confirmed']) for _, doc in documents.values())
            return {**result, 'savedCount': len(profiles), 'remainingCount': remaining}
