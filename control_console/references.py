"""Bounded, read-only local XLSX reference adapters; media stays lazy."""
from __future__ import annotations
import hashlib
import os
from pathlib import Path
import posixpath
import re
import zipfile
from datetime import date, datetime
from xml.etree import ElementTree as ET
from .importers import ImportFailure, HEADERS, NS, RID, _text, _date, identifier_issue, col_index

XML_LIMIT = 32 * 1024 * 1024
TOTAL_LIMIT = 64 * 1024 * 1024
MAIN = ('食品-宠物', '百货个护家清', '母婴')
TAGS = ('精选商家', '本地上传产品')
PURPLE = {'9A38D7', '800080'}

class Package:
    def __init__(self, path):
        self.path = Path(path)
        if not self.path.is_absolute() or str(path).startswith(('\\\\', '//')) or self.path.suffix.lower() != '.xlsx':
            raise ImportFailure('path', '来源必须为本机绝对xlsx路径。')
        if any(p.is_symlink() or getattr(p, 'is_junction', lambda: False)() for p in (self.path, *self.path.parents)):
            raise ImportFailure('path', '来源路径不能包含链接或联接。')
        self.before = self.path.stat()
        if not self.path.is_file():
            raise ImportFailure('path', '来源必须为普通文件。')
        self.z = zipfile.ZipFile(self.path)
        infos = self.z.infolist()
        names = [i.filename for i in infos]
        if len(infos) > 10000 or len(names) != len(set(names)) or len(names) != len(set(n.casefold() for n in names)):
            self.z.close()
            raise ImportFailure('xlsx_limit', '压缩成员过多或重名。')
        if any('\\' in n or n.startswith('/') or '..' in n.split('/') or ':' in n for n in names):
            self.z.close()
            raise ImportFailure('xlsx_invalid', '压缩路径不合法。')
        self.names = set(names)
        self.cache = {}
        self.hashes = {}
        self.total = 0
    def xml(self, name):
        if name in self.cache:
            return self.cache[name]
        info = self.z.getinfo(name)
        if info.file_size > XML_LIMIT or self.total + info.file_size > TOTAL_LIMIT:
            raise ImportFailure('xlsx_limit', '相关XML超过读取上限。')
        data = self.z.read(name)
        if b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper() or b'\x00' in data:
            raise ImportFailure('xlsx_invalid', 'XML实体或非UTF8编码不受支持。')
        self.total += len(data)
        self.hashes[name] = hashlib.sha256(data).hexdigest()
        root = ET.fromstring(data)
        self.cache[name] = root
        return root
    def rels(self, source):
        name = posixpath.join(posixpath.dirname(source), '_rels', posixpath.basename(source) + '.rels')
        result = {}
        if name in self.names:
            for node in self.xml(name):
                if node.get('TargetMode') == 'External':
                    continue
                target = node.get('Target', '')
                dest = target.lstrip('/') if target.startswith('/') else posixpath.normpath(posixpath.join(posixpath.dirname(source), target))
                if not dest.startswith('xl/') or '\\' in dest or ':' in dest or not target:
                    raise ImportFailure('xlsx_invalid', '关系路径不合法。')
                key = node.get('Id')
                if key in result:
                    raise ImportFailure('xlsx_invalid', '关系ID重复。')
                result[key] = dest
        return result
    def close(self):
        self.z.close()
        try:
            after = self.path.stat()
        except OSError as error:
            raise ImportFailure('source_changed', '来源在读取期间被移走、删除或不可访问。') from error
        if (self.before.st_size, self.before.st_mtime_ns, self.before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
            raise ImportFailure('source_changed', '读取期间来源发生变化，请重新读取。')

def purple(color):
    return color is not None and color.get('rgb', '')[-6:].upper() in PURPLE

def visible_purple(node, base):
    if node is None:
        return False
    direct = node.find('s:t', NS)
    if direct is not None and (direct.text or '').strip() and base:
        return True
    for run in node.findall('s:r', NS):
        color = run.find('s:rPr/s:color', NS)
        if _text(run).strip() and (purple(color) if color is not None else base):
            return True
    return False

def images(p, sheet_path, root):
    output = {}
    rels = p.rels(sheet_path)
    for drawing in root.findall('s:drawing', NS):
        target = rels.get(drawing.get(RID))
        if not target:
            continue
        media = p.rels(target)
        for anchor in p.xml(target):
            origin = next((c for c in anchor if c.tag.rsplit('}', 1)[-1] == 'from'), None)
            if origin is None:
                continue
            coords = {c.tag.rsplit('}', 1)[-1]: c.text for c in origin}
            if coords.get('col') != '5':
                continue
            row = int(coords.get('row', '-1')) + 1
            for blip in anchor.iter():
                if blip.tag.rsplit('}', 1)[-1] != 'blip':
                    continue
                member = media.get(blip.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed'))
                mime = {'.png':'image/png', '.jpg':'image/jpeg', '.jpeg':'image/jpeg', '.webp':'image/webp'}.get(posixpath.splitext(member or '')[1].lower())
                if member in p.names and mime:
                    output.setdefault(row, {'member':member, 'mime':mime, 'row':row, 'column':'F'})
    return output

def _parse(p, kind):
    book = p.xml('xl/workbook.xml')
    rels = p.rels('xl/workbook.xml')
    epoch = book.find('s:workbookPr', NS)
    epoch = epoch is not None and epoch.get('date1904') in ('1', 'true')
    shared = list(p.xml('xl/sharedStrings.xml')) if 'xl/sharedStrings.xml' in p.names else []
    font_colors, styles = [], []
    if 'xl/styles.xml' in p.names:
        style = p.xml('xl/styles.xml')
        font_colors = [purple(f.find('s:color', NS)) for f in style.findall('s:fonts/s:font', NS)]
        styles = [int(x.get('fontId', '0')) for x in style.findall('s:cellXfs/s:xf', NS)]
    records, issues, mappings = [], [], []
    skipped = 0
    sheet_names = set()
    def issue(row, sheet, code, message):
        item = dict(row=row, sheet=sheet, code=code, message=message)
        issues.append(item)
        return item
    for sheet in book.findall('s:sheets/s:sheet', NS):
        name = sheet.get('name')
        if not name or name in sheet_names:
            raise ImportFailure('xlsx_invalid', '工作表名称缺失或重复。')
        sheet_names.add(name)
        if kind == 'products' and name not in MAIN + TAGS:
            continue
        target = rels.get(sheet.get(RID))
        if not target:
            raise ImportFailure('xlsx_invalid', '工作表关系缺失。')
        root = p.xml(target)
        cells = {}
        rows = set()
        for node in root.findall('s:sheetData/s:row/s:c', NS):
            ref = node.get('r', '')
            match = re.fullmatch(r'([A-Z]{1,3})([1-9][0-9]*)', ref)
            if not match:
                raise ImportFailure('xlsx_invalid', '单元格坐标无效。')
            col, row = match[1], int(match[2])
            key = (row, col)
            if key in cells:
                raise ImportFailure('xlsx_invalid', '重复单元格坐标。')
            raw = node.findtext('s:v', '', NS)
            typ = node.get('t', 'n')
            rich = None
            if node.find('s:f', NS) is not None:
                value, ctype = '=' + (node.findtext('s:f', '', NS)), 'formula'
            elif typ == 's' and raw:
                idx = int(raw)
                if not 0 <= idx < len(shared):
                    raise ImportFailure('xlsx_invalid', '共享字符串索引无效。')
                rich = shared[idx]
                value, ctype = _text(rich), 'string'
            elif typ == 's':
                value, ctype = '', 'blank'
            elif typ == 'inlineStr':
                rich = node.find('s:is', NS)
                value, ctype = _text(rich), 'string'
            else:
                value, ctype = raw, {'n':'number', 'str':'string', 'd':'date', 'e':'error', 'b':'boolean'}.get(typ, 'error')
            column = col_index(col)
            if value.strip() and column > 256:
                raise ImportFailure('xlsx_limit', '非空来源列超出256列读取预算。')
            if not value.strip() and column > 256:
                continue
            style_idx = int(node.get('s', '0'))
            fid = styles[style_idx] if 0 <= style_idx < len(styles) else -1
            base = font_colors[fid] if 0 <= fid < len(font_colors) else False
            cell = dict(text=value, type=ctype, value=raw, raw=raw, purple=visible_purple(rich, base) if rich is not None else bool(value.strip()) and base and ctype != 'formula')
            cells[key] = cell
            if value.strip():
                rows.add(row)
        if len(rows) > 50050:
            raise ImportFailure('row_limit', '有效行超过50000行。')
        blank = dict(text='', type='blank', value='', purple=False)
        at = lambda r, c: cells.get((r,c), blank)
        if kind == 'products':
            header = 3 if name == '母婴' else 1
            pictures = images(p, target, root)
        else:
            header = next((r for r in range(1, 51) if [at(r, chr(65+i))['text'] for i in range(16)] == HEADERS), None)
            if header is None:
                skipped += len(rows)
                continue
        mappings.append(dict(sheet=name, headerRow=header))
        extra_rows = {r for (r, c), cell in cells.items() if col_index(c) > 16 and cell['text'].strip()}
        for row in sorted(rows):
            if row <= header:
                skipped += 1
                continue
            pid_cell = at(row, 'G' if kind == 'products' else 'F')
            if not pid_cell['text'].strip():
                skipped += 1
                continue
            errors = []
            pid = pid_cell['text'].strip()
            problem = identifier_issue(pid_cell)
            if problem or not re.fullmatch(r'[0-9]+', pid):
                errors.append(issue(row, name, 'identifier', problem or '商品ID必须为完整数字文本。'))
                pid = ''
            if kind == 'products':
                if any(at(row, col)['type'] in ('formula', 'error') for col in ('C','E','G','H','L')):
                    errors.append(issue(row, name, 'formula', '商品行含公式或错误，不能用于可靠匹配。'))
                record = dict(sheet=name, row=row, productId=pid, categories=[name] if name in MAIN else [], tags=[name] if name in TAGS else [], title=at(row,'E')['text'], group=at(row,'C')['text'], link=at(row,'H')['text'], requirement=at(row,'L')['text'], image=pictures.get(row), issues=errors)
            else:
                if row in extra_rows:
                    errors.append(issue(row, name, 'extra_column', '协同业务行P列后包含额外业务值，不能可靠关联。'))
                fields = [at(row, chr(65+i))['text'] for i in range(16)]
                account = at(row, 'D')
                aid = account['text'].strip()
                if identifier_issue(account) or not aid:
                    errors.append(issue(row, name, 'account_identifier', '账号标识缺失或不可靠。'))
                    aid = ''
                dt = at(row, 'A')
                try:
                    if dt['type'] == 'number':
                        fields[0] = _date(dt['raw'], epoch)
                    elif dt['type'] in ('string', 'date'):
                        match_date = re.fullmatch(r'(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})', dt['text'].strip())
                        if match_date is None:
                            raise ValueError()
                        fields[0] = date(*(int(v) for v in match_date.groups())).isoformat()
                    else:
                        raise ValueError()
                    if 'T' in fields[0]:
                        raise ValueError()
                except Exception:
                    errors.append(issue(row, name, 'date_invalid', '日期无法可靠识别，不能匹配任务。'))
                if any(at(row, chr(65+i))['type'] in ('formula', 'error') for i in range(16)):
                    errors.append(issue(row, name, 'formula', '行含公式或错误，不使用缓存结果匹配。'))
                record = dict(sheet=name, row=row, fields=fields, accountId=aid, productId=pid, accountSent=at(row,'I')['purple'], productSent=at(row,'H')['purple'], issues=errors)
            records.append(record)
            if len(records) > 50000:
                raise ImportFailure('row_limit', '有效业务行超过50000行。')
    if not records:
        raise ImportFailure('no_rows', '来源没有可识别的业务行。')
    options = dict(sheets=mappings, skippedRows=skipped, adapterVersion='006A.1')
    if kind == 'products':
        options['mainCategories'] = list(MAIN)
        options['complete'] = all(n in sheet_names for n in MAIN)
    elif len(mappings) == 1:
        options.update(mappings[0])
    digest = hashlib.sha256()
    for name, value in sorted(p.hashes.items()):
        digest.update(name.encode() + b'\0' + value.encode() + b'\n')
    return dict(sha256=digest.hexdigest(), records=records, options=options, issues=issues, filename=p.path.name)

def parse_reference(path, kind):
    if kind not in ('products', 'collaboration'):
        raise ImportFailure('kind', '只支持商品库和协同参考。')
    p = None
    try:
        p = Package(path)
        return _parse(p, kind)
    except ImportFailure:
        raise
    except (OSError, ValueError, KeyError, ET.ParseError, zipfile.BadZipFile, RuntimeError, OverflowError) as error:
        raise ImportFailure('xlsx_invalid', '无法读取本机xlsx参考来源。') from error
    finally:
        if p is not None:
            p.close()

def read_product_image(path, member):
    p = None
    try:
        p = Package(path)
        book = p.xml('xl/workbook.xml')
        rels = p.rels('xl/workbook.xml')
        allowed = {}
        for sheet in book.findall('s:sheets/s:sheet', NS):
            if sheet.get('name') in MAIN + TAGS:
                target = rels.get(sheet.get(RID))
                if target:
                    for img in images(p, target, p.xml(target)).values():
                        allowed[img['member']] = img['mime']
        if member not in allowed or p.z.getinfo(member).file_size > 8 * 1024 * 1024:
            raise ImportFailure('image', '图片不是允许的F列引用或超过8MiB。')
        data = p.z.read(member)
        mime = allowed[member]
        valid = (mime == 'image/png' and data.startswith(b'\x89PNG\r\n\x1a\n')) or (mime == 'image/jpeg' and data.startswith(b'\xff\xd8\xff')) or (mime == 'image/webp' and data[:4] == b'RIFF' and data[8:12] == b'WEBP')
        if not valid:
            raise ImportFailure('image', '图片格式与签名不一致。')
        return data, mime
    except ImportFailure:
        raise
    except (OSError, ValueError, KeyError, ET.ParseError, zipfile.BadZipFile, RuntimeError) as error:
        raise ImportFailure('image', '无法读取来源图片。') from error
    finally:
        if p is not None:
            p.close()
