"""Read XLSX packages without evaluating formulas or changing source bytes.

Numeric identifiers are inspected from the XML lexeme, never through float.
Dimensions and styled empty cells do not determine business column/row counts.
"""
from __future__ import annotations

import hashlib
import io
import posixpath
import re
import zipfile
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from xml.etree import ElementTree as ET

HEADERS = ['日期','运营人','抖音名称','抖音账号','作品描述/商品短标题','商品ID','企微群名称','商品链接','账号合作码/uid','视频链接','店铺名称','剪辑软件日志','发文软件日志','千川塞素材软件日志','检查软件日志','企微同步软件日志']
MAX_BYTES = 20 * 1024 * 1024
MAX_UNPACKED = 128 * 1024 * 1024
MAX_ROWS = 50000
NS = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
RID = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id'


class ImportFailure(ValueError):
    def __init__(self, code, message, details=None):
        super().__init__(message)
        self.code, self.message, self.details = code, message, details or []

    def as_dict(self):
        return {'code': self.code, 'message': self.message, 'details': self.details}


def col_index(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z]{1,3}', value):
        raise ImportFailure('column', '列位置必须为Excel列字母，如G或F。')
    result = 0
    for char in value.upper():
        result = result * 26 + ord(char) - 64
    if result > 16384:
        raise ImportFailure('column', '列位置超出Excel范围。')
    return result


def col_name(number):
    text = ''
    while number:
        number, remainder = divmod(number - 1, 26)
        text = chr(65 + remainder) + text
    return text


def _xml(package, name):
    try:
        return ET.fromstring(package.read(name))
    except (KeyError, ET.ParseError, RuntimeError, zipfile.BadZipFile) as error:
        raise ImportFailure('xlsx_invalid', '无法读取完整的xlsx工作簿。') from error


def _text(element):
    if element is None:
        return ''
    parts = []
    for child in element:
        tag = child.tag.rsplit('}',1)[-1]
        if tag == 't':
            parts.append(child.text or '')
        elif tag == 'r':
            parts.extend(node.text or '' for node in child.findall('s:t',NS))
    return ''.join(parts)


def _date(raw, epoch1904):
    value = Decimal(raw)
    if not epoch1904 and 0 < value < 60:
        value += 1
    base = datetime(1904, 1, 1) if epoch1904 else datetime(1899, 12, 30)
    result = base + timedelta(seconds=float(value * 86400))
    return result.date().isoformat() if result.time().isoformat() == '00:00:00' else result.isoformat(timespec='seconds')


def read_workbook(content):
    if not isinstance(content, bytes) or not content or len(content) > MAX_BYTES:
        raise ImportFailure('file_size', '请选择不超过20MiB的xlsx文件。')
    try:
        package = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as error:
        raise ImportFailure('xlsx_invalid', '文件不是有效的xlsx工作簿。') from error
    with package:
        entries = package.infolist()
        if sum(e.file_size for e in entries) > MAX_UNPACKED or len(entries) > 10000:
            raise ImportFailure('xlsx_limit', '工作簿解压内容超过读取限制。')
        names = [e.filename for e in entries]
        if len(names) != len(set(names)):
            raise ImportFailure('xlsx_invalid', '工作簿包含重复的压缩成员。')
        for name in names:
            if '\\' in name or name.startswith('/') or '..' in name.split('/'):
                raise ImportFailure('xlsx_invalid', '工作簿压缩路径不合法。')
        book = _xml(package, 'xl/workbook.xml')
        epoch = book.find('s:workbookPr', NS)
        epoch1904 = epoch is not None and epoch.get('date1904') in ('1', 'true')
        rels = {e.get('Id'): e.get('Target') for e in _xml(package, 'xl/_rels/workbook.xml.rels') if e.get('TargetMode') != 'External'}
        shared = []
        if 'xl/sharedStrings.xml' in names:
            shared = [_text(e) for e in _xml(package, 'xl/sharedStrings.xml').findall('s:si', NS)]
        date_styles = set()
        if 'xl/styles.xml' in names:
            styles = _xml(package, 'xl/styles.xml')
            formats = {int(e.get('numFmtId')): e.get('formatCode', '') for e in styles.findall('s:numFmts/s:numFmt', NS)}
            for index, xf in enumerate(styles.findall('s:cellXfs/s:xf', NS)):
                fmt = int(xf.get('numFmtId', '0'))
                pattern = re.sub(r'"[^"]*"|\[[^\]]*\]|\\.', '', formats.get(fmt, '')).lower()
                if fmt in set(range(14, 23)) | set(range(27, 37)) | set(range(45, 48)) | set(range(50, 59)) or re.search(r'[ymdhs]', pattern):
                    date_styles.add(index)
        sheets = []
        for sheet in book.findall('s:sheets/s:sheet', NS):
            target = rels.get(sheet.get(RID), '')
            path = target.lstrip('/') if target.startswith('/') else posixpath.normpath(posixpath.join('xl', target))
            if not path.startswith('xl/') or not target:
                raise ImportFailure('xlsx_invalid', '工作表路径不合法。')
            xml = _xml(package, path)
            cells = {}
            for node in xml.findall('s:sheetData/s:row/s:c', NS):
                ref = node.get('r', '')
                match = re.fullmatch(r'([A-Z]+)([1-9][0-9]*)', ref)
                if not match:
                    raise ImportFailure('xlsx_invalid', '工作簿单元格坐标不合法。')
                column, row = col_index(match[1]), int(match[2])
                raw = node.findtext('s:v', default='', namespaces=NS)
                kind = node.get('t', 'n')
                formula = node.find('s:f', NS)
                if formula is not None:
                    value, cell_type = '=' + (formula.text or ''), 'formula'
                elif kind == 's':
                    try:
                        index = int(raw)
                        if not 0 <= index < len(shared):
                            raise IndexError
                        value = shared[index]
                    except (ValueError, IndexError) as error:
                        raise ImportFailure('xlsx_invalid', '共享字符串索引不合法。') from error
                    cell_type = 'string'
                elif kind == 'inlineStr':
                    value, cell_type = _text(node.find('s:is', NS)), 'string'
                elif kind in ('str', 'd', 'e', 'b'):
                    value, cell_type = raw, {'str':'string', 'd':'date', 'e':'error', 'b':'boolean'}[kind]
                elif raw:
                    cell_type, value = 'number', raw
                    if int(node.get('s', '0')) in date_styles:
                        try:
                            value, cell_type = _date(raw, epoch1904), 'date'
                        except (InvalidOperation, OverflowError, ValueError) as error:
                            raise ImportFailure('date_invalid', '工作簿日期数值不合法。') from error
                    else:
                        try:
                            number = Decimal(raw)
                            if not number.is_finite() or not -324 <= number.adjusted() <= 308:
                                raise InvalidOperation
                            value = format(number, 'f').rstrip('0').rstrip('.') if '.' in format(number, 'f') else format(number, 'f')
                        except InvalidOperation as error:
                            raise ImportFailure('xlsx_invalid', '工作簿数值不合法。') from error
                else:
                    value, cell_type = '', 'blank'
                if value == '' and cell_type in ('blank','string'):
                    continue
                if row > MAX_ROWS + 1:
                    raise ImportFailure('row_limit', '工作簿有效行超过50000行限制。')
                stored_value = raw if cell_type in ('number','date') else value
                if cell_type == 'number' and number == number.to_integral_value() and abs(number) <= 9007199254740991:
                    stored_value = int(number)
                if (row,column) in cells:
                    raise ImportFailure('xlsx_invalid','工作表含重复单元格坐标，无法确定来源值。')
                cells[(row, column)] = {'text': value, 'type': cell_type, 'value': stored_value, 'raw':raw, 'xlsxType':kind, 'style':node.get('s'), 'hyperlink': None}
            rel_file = posixpath.join(posixpath.dirname(path), '_rels', posixpath.basename(path) + '.rels')
            link_rels = {e.get('Id'): e.get('Target') for e in _xml(package, rel_file)} if rel_file in names else {}
            for link in xml.findall('s:hyperlinks/s:hyperlink', NS):
                ref = link.get('ref', '').split(':')[0]
                match = re.fullmatch(r'([A-Z]+)([1-9][0-9]*)', ref)
                if match:
                    key = (int(match[2]), col_index(match[1]))
                    cell = cells.setdefault(key, {'text':'', 'type':'blank', 'value':None, 'hyperlink':None})
                    cell['hyperlink'] = link_rels.get(link.get(RID)) or ('#' + link.get('location') if link.get('location') else None)
            sheets.append({'name': sheet.get('name'), 'cells': cells, 'rowCount': max((r for r, _ in cells), default=0)})
        if not sheets:
            raise ImportFailure('xlsx_empty', '工作簿没有可读取的工作表。')
        if any(not s['name'] for s in sheets) or len({s['name'] for s in sheets}) != len(sheets):
            raise ImportFailure('xlsx_invalid','工作表名称缺失或重复，无法确定来源。')
        return {'sha256': hashlib.sha256(content).hexdigest(), 'sheets': sheets, 'epoch1904':epoch1904}


def inspect_workbook(content):
    book = read_workbook(content)
    return {'ok':True, 'sha256':book['sha256'], 'sheets':[
        {'name':s['name'], 'rowCount':s['rowCount'], 'headers':[
            {'column':col_name(c), 'label':cell['text']} for (r,c),cell in sorted(s['cells'].items()) if r == 1 and cell['text'] and c <= 256
        ]} for s in book['sheets']], 'limits':{'fileBytes':MAX_BYTES, 'rows':MAX_ROWS}}


def get_sheet(book, name):
    sheet = next((s for s in book['sheets'] if s['name'] == name), None)
    if sheet is None:
        raise ImportFailure('sheet_missing', '找不到所选工作表。')
    return sheet


def cell_at(sheet, row, column):
    return sheet['cells'].get((row, column), {'text':'','type':'blank','value':None,'hyperlink':None})


def identifier_issue(cell):
    if cell['type'] in ('formula', 'error', 'date', 'boolean'):
        return '标识字段必须是完整文本或安全的短整数。'
    if cell['type'] == 'number':
        number = Decimal(cell['value'])
        if number != number.to_integral_value() or abs(number) >= 10 ** 15:
            return '数值型长ID可能已丢失精度，不能猜回；请从正确来源以文本重新取得。'
    return None


def split_categories(text):
    return list(dict.fromkeys(v.strip() for v in re.split(r'[、,，;；\r\n]+', text) if v.strip()))


def parse_import(kind, content, options=None):
    options = options or {}
    if not isinstance(options, dict):
        raise ImportFailure('options', '导入选项必须是对象。')
    book = read_workbook(content)
    issues, records = [], []
    def issue(row, code, message, sheet=None):
        item = {'row':row, 'code':code, 'message':message}
        if sheet:
            item['sheet'] = sheet
        issues.append(item)
        return item
    if kind == 'tasks':
        sheet = get_sheet(book, 'Sheet1')
        actual = [cell_at(sheet, 1, c)['text'] for c in range(1, 17)]
        if actual != HEADERS or any(r == 1 and c > 16 and v['text'] for (r,c), v in sheet['cells'].items()):
            raise ImportFailure('headers', '任务Sheet1第1行必须精确为固定A:P共16列表头。', [{'expected':HEADERS, 'actual':actual}])
        if any(c > 16 and v['text'] for (r,c),v in sheet['cells'].items()):
            raise ImportFailure('extra_columns','任务表P列后含额外业务值，固定任务表只能使用A:P共16列。')
        extra_rows = {r for (r,c),v in sheet['cells'].items() if c > 16 and v['text']}
        for row in sorted({r for r,c in sheet['cells'] if r > 1 and c <= 16}):
            cells = [cell_at(sheet,row,c) for c in range(1,17)]
            fields = [c['text'] for c in cells]
            if not any(v.strip() for v in fields) and not any(c['hyperlink'] for c in cells):
                continue
            row_issues = []
            if cells[0]['type'] == 'number':
                try:
                    fields[0] = _date(str(cells[0].get('raw') or cells[0]['value']),book['epoch1904'])
                except (InvalidOperation,OverflowError,ValueError):
                    row_issues.append(issue(row,'date_invalid','日期数值超出Excel日期范围，保留原值并等待核对。'))
            for index in (2,3,4,5,7):
                if not fields[index].strip() and not (index == 7 and cells[index]['hyperlink']):
                    row_issues.append(issue(row, 'required', HEADERS[index] + '缺失，不能继承上一行。'))
            for index in (3,5):
                problem = identifier_issue(cells[index])
                if problem:
                    row_issues.append(issue(row,'identifier', HEADERS[index] + '：' + problem))
            if fields[5] and not re.fullmatch(r'[0-9]+', fields[5]):
                row_issues.append(issue(row,'identifier','商品ID必须是完整数字文本，不能使用科学计数或截短值。'))
            if any(c['type'] in ('formula','error') for c in cells):
                row_issues.append(issue(row,'formula','任务行含公式或错误值，不执行或使用缓存结果代替。'))
            if row in extra_rows:
                row_issues.append(issue(row,'extra_column','任务行在P列后含额外业务值。'))
            records.append({'row':row,'sheet':'Sheet1','fields':fields,'cellMeta':[{k:c.get(k) for k in ('type','value','hyperlink','raw','xlsxType','style')} for c in cells], 'issues':row_issues})
        groups = {}
        for record in records:
            key = tuple(record['fields'][i] for i in (0,3,5))
            if all(key):
                groups.setdefault(key, []).append(record)
        for group in groups.values():
            if len(group) > 1:
                for record in group:
                    record['issues'].append(issue(record['row'],'duplicate','同账号同商品存在重复行，保留所有原行并等待人工核对。'))
        normalized = {}
    elif kind == 'accounts':
        sheet = get_sheet(book, options.get('sheet') or book['sheets'][0]['name'])
        header = options.get('headerRow',1)
        if type(header) is not int or not 0 <= header <= 50:
            raise ImportFailure('header_row','表头行必须为0到50的整数。')
        for row in sorted({r for r,c in sheet['cells'] if r > header and c <= 6}):
            cells = [cell_at(sheet,row,c) for c in range(1,7)]
            if not any(c['text'].strip() for c in cells):
                continue
            errors = []
            for index in (1,2,5):
                if not cells[index]['text'].strip():
                    errors.append(issue(row,'account_missing','账号B列、名称C列和允许类别F列均须有来源值。',sheet['name']))
            for index in (1,3,4):
                problem = identifier_issue(cells[index])
                if problem:
                    errors.append(issue(row,'identifier',problem,sheet['name']))
            if any(c['type'] in ('formula','error') for c in cells):
                errors.append(issue(row,'formula','账号证据包含公式或错误值。',sheet['name']))
            records.append({'row':row,'sheet':sheet['name'],'accountId':cells[1]['text'].strip(),'name':cells[2]['text'],'allowed':split_categories(cells[5]['text']),'issues':errors})
        normalized = {'sheet':sheet['name'],'headerRow':header,'complete':options.get('complete') is True}
    elif kind == 'products':
        selections = options.get('sheets')
        if not isinstance(selections,list) or not selections or len(selections) > len(book['sheets']):
            raise ImportFailure('product_mapping','请选择商品分类工作表及ID/类别列。')
        normalized_sheets = []
        seen = set()
        for mapping in selections:
            if not isinstance(mapping,dict):
                raise ImportFailure('product_mapping','商品映射必须是对象。')
            sheet = get_sheet(book,mapping.get('sheet'))
            if sheet['name'] in seen:
                raise ImportFailure('product_mapping','同一工作表不能重复选择。')
            seen.add(sheet['name'])
            header = mapping.get('headerRow',1)
            if type(header) is not int or not 0 <= header <= 50:
                raise ImportFailure('header_row','表头行必须为0到50的整数。')
            id_column = col_index(mapping.get('idColumn','G'))
            from_sheet = mapping.get('categoryFromSheet') is True
            if from_sheet and re.fullmatch(r'(?:sheet\d*|工作表\d*)',sheet['name'].strip(),re.I):
                raise ImportFailure('category_evidence','Sheet1等通用工作表名称不能构成商品类别证据，请选有类别列或分类名称的来源副本。')
            category_column = None if from_sheet else col_index(mapping.get('categoryColumn',''))
            normalized_sheets.append({'sheet':sheet['name'],'headerRow':header,'idColumn':col_name(id_column),'categoryColumn':col_name(category_column) if category_column else '', 'categoryFromSheet':from_sheet})
            for row in sorted({r for r,c in sheet['cells'] if r > header and c == id_column}):
                product = cell_at(sheet,row,id_column)
                if not product['text'].strip():
                    continue
                category = {'text':sheet['name'],'type':'string'} if from_sheet else cell_at(sheet,row,category_column)
                errors = []
                problem = identifier_issue(product)
                if problem or not re.fullmatch(r'[0-9]+',product['text']):
                    errors.append(issue(row,'identifier',problem or '商品ID不是完整数字文本。',sheet['name']))
                if not category['text'].strip() or category['type'] in ('formula','error'):
                    errors.append(issue(row,'category_missing','类别字段缺失或不是可靠来源值。',sheet['name']))
                records.append({'row':row,'sheet':sheet['name'],'productId':product['text'].strip(),'categories':split_categories(category['text']),'issues':errors})
        normalized = {'complete':options.get('complete') is True,'sheets':normalized_sheets}
    elif kind == 'collaboration':
        sheet = get_sheet(book,options.get('sheet') or book['sheets'][0]['name'])
        header = options.get('headerRow',1)
        if type(header) is not int or not 0 <= header <= 50:
            raise ImportFailure('header_row','表头行必须为0到50的整数。')
        records = [{'row':row,'sheet':sheet['name']} for row in sorted({r for r,c in sheet['cells'] if r > header and sheet['cells'][(r,c)]['text']})]
        normalized = {'sheet':sheet['name'],'headerRow':header}
    else:
        raise ImportFailure('kind','请选择任务、商品分类、账号或每日协同副本。')
    if not records:
        raise ImportFailure('no_rows','所选工作表没有可导入的有效业务行。')
    return {'sha256':book['sha256'],'options':normalized,'records':records,'issues':issues}
