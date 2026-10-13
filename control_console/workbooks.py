"""Small dependency-free writer for isolated, text-preserving task workbooks."""
import io
import zipfile
from xml.sax.saxutils import escape

from .importers import HEADERS


def task_workbook(tasks):
    # Never carry source F hyperlinks: the material app treats these as an
    # output-directory hint, which can accidentally redirect account output.
    rows = [HEADERS] + [task['fields'] for task in tasks]
    data = []
    for number, values in enumerate(rows, 1):
        cells = ''.join(f'<c r="{chr(65+index)}{number}" t="inlineStr"><is><t xml:space="preserve">{escape(str(value))}</t></is></c>'
                        for index,value in enumerate(values))
        data.append(f'<row r="{number}">{cells}</row>')
    ns = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
    stream = io.BytesIO()
    with zipfile.ZipFile(stream,'w',zipfile.ZIP_DEFLATED) as archive:
        def write(name, content):
            entry = zipfile.ZipInfo(name, (1980,1,1,0,0,0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(entry,content)
        write('[Content_Types].xml','<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>')
        write('_rels/.rels','<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        write('xl/workbook.xml',f'<workbook xmlns="{ns}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>')
        write('xl/_rels/workbook.xml.rels','<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
        write('xl/worksheets/sheet1.xml',f'<worksheet xmlns="{ns}"><dimension ref="A1:P{len(rows)}"/><sheetData>{"".join(data)}</sheetData></worksheet>')
    return stream.getvalue()
