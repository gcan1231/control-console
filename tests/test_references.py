import tempfile
import unittest
import zipfile
from pathlib import Path
from control_console.references import parse_reference, read_product_image
from control_console.importers import HEADERS, ImportFailure

S = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
def cell(ref, text, extra=''):
    return f'<c r="{ref}" t="inlineStr" {extra}><is><t>{text}</t></is></c>'

class ReferencesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'reference.xlsx'
    def write(self, name, rows, extras=None):
        with zipfile.ZipFile(self.path, 'w') as z:
            z.writestr('xl/workbook.xml', f'<workbook xmlns="{S}" xmlns:r="{R}"><sheets><sheet name="{name}" r:id="s1"/></sheets></workbook>')
            z.writestr('xl/_rels/workbook.xml.rels', '<Relationships><Relationship Id="s1" Target="worksheets/sheet1.xml"/></Relationships>')
            z.writestr('xl/worksheets/sheet1.xml', f'<worksheet xmlns="{S}" xmlns:r="{R}"><sheetData>{rows}</sheetData></worksheet>')
            for key, value in (extras or {}).items():
                z.writestr(key, value)
    def test_products_categories_tags_and_numeric_id(self):
        self.write('精选商家', '<row r="2">' + cell('G2', '1234567890123456789') + cell('E2','虚构商品') + '</row>')
        result = parse_reference(self.path, 'products')
        self.assertEqual(result['records'][0]['categories'], [])
        self.assertEqual(result['records'][0]['tags'], ['精选商家'])
        self.assertEqual(result['records'][0]['productId'], '1234567890123456789')
        self.write('母婴', '<row r="2">'+cell('G2','123')+'</row><row r="4"><c r="G4"><v>1234567890123456789</v></c></row>')
        result = parse_reference(self.path, 'products')
        self.assertEqual(len(result['records']), 1)
        self.assertEqual(result['records'][0]['productId'], '')
        self.assertTrue(result['issues'])
    def test_header_excludes_preface_and_purple_rich_runs(self):
        header = '<row r="3">' + ''.join(cell(chr(65+i)+'3', t) for i,t in enumerate(HEADERS)) + '</row>'
        rich = '<c r="I4" t="inlineStr"><is><r><rPr><color rgb="FF9A38D7"/></rPr><t>已发</t></r></is></c>'
        rows = '<row r="1">'+cell('F1','999')+'</row>'+header+'<row r="4">'+cell('A4','2026-10-14')+cell('D4','虚构账号')+cell('F4','123')+rich+'<c r="H4" s="0"/></row>'
        self.write('协同', rows, {'xl/styles.xml':f'<styleSheet xmlns="{S}"><fonts><font><color rgb="FF800080"/></font></fonts><cellXfs><xf fontId="0"/></cellXfs></styleSheet>'})
        result = parse_reference(self.path, 'collaboration')
        self.assertEqual(len(result['records']), 1)
        self.assertTrue(result['records'][0]['accountSent'])
        self.assertFalse(result['records'][0]['productSent'])
        self.assertEqual(result['options']['headerRow'], 3)
        self.assertEqual(result['options']['adapterVersion'], '006A.1')
    def test_hash_ignores_media_and_source_is_readonly(self):
        rows = '<row r="2">'+cell('G2','123')+'</row>'
        self.write('食品-宠物', rows, {'xl/media/unused.png':b'first'})
        before = self.path.read_bytes()
        first = parse_reference(self.path, 'products')['sha256']
        self.assertEqual(before, self.path.read_bytes())
        self.write('食品-宠物', rows, {'xl/media/unused.png':b'second'})
        self.assertEqual(first, parse_reference(self.path, 'products')['sha256'])
        with self.assertRaises(ImportFailure):
            read_product_image(self.path, 'xl/media/unused.png')
    def test_lazy_image_reference_and_signature(self):
        rows = '<row r="2">'+cell('G2','123')+'</row>'
        self.write('食品-宠物', rows)
        with zipfile.ZipFile(self.path, 'a') as z:
            sheet = f'<worksheet xmlns="{S}" xmlns:r="{R}"><sheetData>{rows}</sheetData><drawing r:id="d"/></worksheet>'
            # Replace package once to avoid duplicate members.
        with zipfile.ZipFile(self.path) as z:
            parts = {i.filename:z.read(i.filename) for i in z.infolist()}
        parts['xl/worksheets/sheet1.xml'] = sheet
        parts['xl/worksheets/_rels/sheet1.xml.rels'] = '<Relationships><Relationship Id="d" Target="../drawings/drawing1.xml"/></Relationships>'
        parts['xl/drawings/drawing1.xml'] = f'<wsDr xmlns:r="{R}"><twoCellAnchor><from><col>5</col><row>1</row></from><pic><blip r:embed="i"/></pic></twoCellAnchor></wsDr>'
        parts['xl/drawings/_rels/drawing1.xml.rels'] = '<Relationships><Relationship Id="i" Target="../media/image.png"/></Relationships>'
        parts['xl/media/image.png'] = b'\x89PNG\r\n\x1a\nfictional'
        with zipfile.ZipFile(self.path, 'w') as z:
            for key,value in parts.items(): z.writestr(key,value)
        result = parse_reference(self.path, 'products')
        img = result['records'][0]['image']
        self.assertEqual(img['column'], 'F')
        data,mime = read_product_image(self.path,img['member'])
        self.assertEqual(mime,'image/png')
        self.assertEqual(data, parts['xl/media/image.png'])
    def test_xml_limit_and_source_change(self):
        from unittest.mock import patch
        from control_console.references import Package
        self.write('食品-宠物', '<row r="2">'+cell('G2','123')+'</row>')
        with patch('control_console.references.XML_LIMIT', 10):
            with self.assertRaises(ImportFailure): parse_reference(self.path,'products')
        package = Package(self.path)
        with self.path.open('ab') as stream: stream.write(b'changed')
        with self.assertRaises(ImportFailure) as caught: package.close()
        self.assertEqual(caught.exception.code,'source_changed')
    def test_explicit_year_mixed_date_separators(self):
        header = '<row r="3">' + ''.join(cell(chr(65+i)+'3', t) for i,t in enumerate(HEADERS)) + '</row>'
        rows = header
        values = ['2026-10.1', '2026-10.7', '2026-02.30', '10.1', '2026-10-14']
        for i, value in enumerate(values, 4):
            rows += f'<row r="{i}">'+cell(f'A{i}', value)+cell(f'D{i}','fictional')+cell(f'F{i}','123')+'</row>'
        self.write('collaboration', rows)
        result = parse_reference(self.path,'collaboration')
        self.assertEqual([r['fields'][0] for r in result['records']], ['2026-10-01','2026-10-07','2026-02.30','10.1','2026-10-14'])
        self.assertEqual(sum(i['code']=='date_invalid' for i in result['issues']),2)
    def test_source_removed_during_read_is_import_failure(self):
        from control_console.references import Package
        from unittest.mock import patch
        self.write('食品-宠物', '<row r="2">'+cell('G2','123')+'</row>')
        package = Package(self.path)
        with patch.object(Path, 'stat', side_effect=FileNotFoundError('removed')):
            with self.assertRaises(ImportFailure) as caught:
                package.close()
        self.assertEqual(caught.exception.code, 'source_changed')
        self.assertIsNone(package.z.fp)
    def test_collaboration_extra_columns_and_empty_far_style(self):
        header = '<row r="3">' + ''.join(cell(chr(65+i)+'3', t) for i,t in enumerate(HEADERS)) + '</row>'
        preface = '<row r="1">'+cell('Q1','account reference')+cell('AF1','reference')+'</row>'
        row = '<row r="4">'+cell('A4','2026-10-14')+cell('D4','fictional')+cell('F4','123')
        self.write('collaboration', preface+header+row+'<c r="XFD4" s="0"/></row>')
        result = parse_reference(self.path,'collaboration')
        self.assertFalse(result['records'][0]['issues'])
        self.write('collaboration', preface+header+row+cell('Q4','unexpected business')+'</row>')
        result = parse_reference(self.path,'collaboration')
        self.assertEqual([i['code'] for i in result['records'][0]['issues']], ['extra_column'])
        self.assertEqual([i['code'] for i in result['issues']], ['extra_column'])
        self.write('collaboration', preface+header+row+cell('IW4','beyond 256')+'</row>')
        with self.assertRaises(ImportFailure) as caught:
            parse_reference(self.path,'collaboration')
        self.assertEqual(caught.exception.code,'xlsx_limit')
    def test_duplicate_and_entities_are_rejected(self):
        self.write('食品-宠物', '<row r="2">'+cell('G2','123')+'</row>')
        with zipfile.ZipFile(self.path, 'a') as z:
            z.writestr('../bad.xml', '<bad/>')
        with self.assertRaises(ImportFailure):
            parse_reference(self.path, 'products')
        self.write('食品-宠物', '<row r="2">'+cell('G2','123')+'</row>')
        with zipfile.ZipFile(self.path, 'a') as z:
            z.writestr('xl/styles.xml', '<!DOCTYPE x [<!ENTITY a "x">]><x/>')
        with self.assertRaises(ImportFailure):
            parse_reference(self.path, 'products')

if __name__ == '__main__':
    unittest.main()
