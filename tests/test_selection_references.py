import unittest
import test_references as fixtures
cell, S, R = fixtures.cell, fixtures.S, fixtures.R
from control_console.references import parse_reference, statistic_date
from control_console.importers import ImportFailure


class SelectionReferenceTests(unittest.TestCase):
    setUp = fixtures.ReferencesTests.setUp
    write = fixtures.ReferencesTests.write
    def product(self, date='10.14', header_row=4, name='食品-宠物', hyperlink=None):
        header = f'<row r="{header_row}">' + ''.join(cell(f'{c}{header_row}', t) for c, t in [('E','商品名称'), ('G','商品ID'), ('H','商品链接'), ('O','视频链接协同群')]) + '</row>'
        row = header_row + 1
        values = {'A':date,'B':'未开始','C':'C群','D':'原文投放','E':'虚构商品','G':'1234567890123456789','H':'查看商品','I':'20%','J':'1.2万','K':'未知','L':'原文要求','M':'虚构负责人','N':'待结算','O':'O群','P':'虚构助理'}
        rows = header + f'<row r="{row}">' + ''.join(cell(f'{c}{row}', t) for c, t in values.items()) + '</row>'
        self.write(name, rows)
        if hyperlink:
            import zipfile
            with zipfile.ZipFile(self.path) as z:
                parts = {n:z.read(n) for n in z.namelist()}
            parts['xl/worksheets/sheet1.xml'] = f'<worksheet xmlns="{S}" xmlns:r="{R}"><sheetData>{rows}</sheetData><hyperlinks><hyperlink ref="H{row}" r:id="h"/></hyperlinks></worksheet>'
            parts['xl/worksheets/_rels/sheet1.xml.rels'] = f'<Relationships><Relationship Id="h" Type="{R}/hyperlink" TargetMode="External" Target="{hyperlink}"/></Relationships>'
            with zipfile.ZipFile(self.path, 'w') as z:
                for n,v in parts.items(): z.writestr(n,v)

    def test_selection_fields_date_and_confirmed_year(self):
        self.product()
        before = self.path.read_bytes()
        result = parse_reference(self.path, 'products', {'statisticYear':None})
        row = result['records'][0]
        self.assertTrue(row['catalogVerified'])
        self.assertEqual((row['group'],row['groupC'],row['groupO']), ('C群','C群','O群'))
        self.assertEqual((row['statisticDate'],row['statisticDateRaw'],row['dateNeedsYear']), ('','10.14',True))
        self.assertEqual([row[k] for k in ('progress','investment','commission','totalSales','dailySales','responsible','settlement','assistant')], ['未开始','原文投放','20%','1.2万','未知','虚构负责人','待结算','虚构助理'])
        self.assertEqual(result['options']['adapterVersion'],'006A3.1')
        self.assertEqual(parse_reference(self.path,'products',{'statisticYear':2026})['records'][0]['statisticDate'], '2026-10-14')
        self.assertEqual(before,self.path.read_bytes())
        for year in (1999,2101,True,'2026',2026.0):
            with self.assertRaises(ImportFailure): parse_reference(self.path,'products',{'statisticYear':year})

    def test_full_date_and_safe_hyperlink(self):
        self.product(date='2026-10.14', hyperlink='https://example.invalid/product/123')
        row = parse_reference(self.path,'products')['records'][0]
        self.assertEqual(row['statisticDate'],'2026-10-14')
        self.assertFalse(row['dateNeedsYear'])
        self.assertEqual(row['link'],'https://example.invalid/product/123')
        self.assertEqual(row['linkText'],'查看商品')
        self.product(hyperlink='file:///C:/secret')
        self.assertEqual(parse_reference(self.path,'products')['records'][0]['link'],'')

    def test_nonstandard_sheet_and_header_evidence(self):
        self.product(name='虚构专题')
        row = parse_reference(self.path,'products')['records'][0]
        self.assertTrue(row['catalogVerified'])
        self.assertEqual(row['categories'],[])
        self.assertEqual(row['tags'],['虚构专题'])
        self.write('食品-宠物','<row r="2">'+cell('G2','123')+'</row>')
        self.assertFalse(parse_reference(self.path,'products')['records'][0]['catalogVerified'])
        self.product(header_row=51)
        self.assertFalse(any(r['catalogVerified'] for r in parse_reference(self.path,'products')['records']))

    def test_excel_epoch_and_invalid_dates(self):
        self.assertEqual(statistic_date({'type':'number','text':'1','raw':'1','dateStyle':True}, True, None), ('1904-01-02',False))
        self.assertEqual(statistic_date({'type':'number','text':'61','raw':'61','dateStyle':True}, False, None), ('1900-03-01',False))
        self.assertEqual(statistic_date({'type':'number','text':'61','raw':'61'}, False, None), ('',False))
        self.assertEqual(statistic_date({'type':'string','text':'2.29'}, False, None), ('',True))
        self.assertEqual(statistic_date({'type':'string','text':'2.29'}, False, 2026), ('',False))
        self.assertEqual(statistic_date({'type':'string','text':'2026-02-30'}, False, None), ('',False))


if __name__ == '__main__':
    unittest.main()
