import io
import subprocess
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw
from control_console.account_ocr import recognize_image, reconstruct_rows, detect_table_grid, MAX_BYTES


def word(text, x, y=40, width=15, height=10):
    return dict(text=text, x=x, y=y, width=width, height=height)


def result(words):
    return dict(width=800, height=200, lines=[dict(text="irrelevant", words=words)])


class AccountOcrTests(unittest.TestCase):
    def test_coordinates_override_reading_order(self):
        data = reconstruct_rows(result([word("末列",720),word("首列",20),word("第三列",220)]), {"columns": list(range(0,801,100))})
        self.assertEqual(data["rows"][0]["fields"], ["首列","","第三列","","","","","末列"])

    def test_unknown_columns_refuse(self):
        data = reconstruct_rows(result([word("账号",20),word("昵称",220)]))
        self.assertTrue(data["needsCalibration"])
        self.assertEqual(data["rows"], [])

    def test_header_detection(self):
        data = reconstruct_rows(result([word(c,43+i*100,10,14) for i,c in enumerate("ABCDEFGH")]+[word("虚构",20)]))
        self.assertTrue(data["needsCalibration"])

    def grid_fixture(self):
        image = Image.new("RGB",(800,200),"white")
        columns = [10,70,190,250,410,470,600,680,790]
        draw = ImageDraw.Draw(image)
        for x in columns:
            draw.line((x,30,x,180),fill=(230,230,230))
        for y in [30,90,180]:
            draw.line((10,y,790,y),fill=(230,230,230))
        headers = [word(c,(a+b)/2-5,10,10) for c,(a,b) in zip("ABCDEFGH",zip(columns,columns[1:]))]
        payload = result(headers+[word("虚构上",20,40),word("虚构下",20,60),word("末列",700,110)])
        return image, columns, payload

    def test_unequal_grid_and_wrapped_cell(self):
        image, columns, payload = self.grid_fixture()
        payload.update(detect_table_grid(image,payload))
        self.assertEqual(payload["gridColumns"],columns)
        self.assertEqual(payload["rowBoundaries"],[30,90,180])
        mapped = reconstruct_rows(payload)
        self.assertEqual(len(mapped["rows"]),2)
        self.assertEqual(mapped["rows"][0]["fields"][0],"虚构上 虚构下")
        self.assertEqual(mapped["rows"][1]["fields"][-1],"末列")

    def test_grid_requires_lines_and_header(self):
        image, columns, payload = self.grid_fixture()
        self.assertEqual(detect_table_grid(Image.new("RGB",image.size,"white"),payload),{})
        payload["lines"][0]["words"] = payload["lines"][0]["words"][1:]
        self.assertEqual(detect_table_grid(image,payload),{})

    def test_manual_columns_override_detected_grid(self):
        image, columns, payload = self.grid_fixture()
        payload.update(detect_table_grid(image,payload))
        manual = [0,80,200,300,400,500,600,700,800]
        mapped = reconstruct_rows(payload,{"columns":manual,"rowBoundaries":[30,180]})
        self.assertEqual(mapped["columns"],manual)
        self.assertEqual(len(mapped["rows"]),1)

    def test_grid_crop_uses_original_coordinates(self):
        image, columns, payload = self.grid_fixture()
        payload.update(detect_table_grid(image,payload))
        mapped = reconstruct_rows(payload,{"crop":{"x":5,"y":25,"width":790,"height":160}})
        self.assertEqual(mapped["columns"],columns)
        self.assertEqual(len(mapped["rows"]),2)
        self.assertTrue(reconstruct_rows(payload,{"crop":{"x":20,"y":25,"width":775,"height":160}})["needsCalibration"])

    def test_manual_rows_merge_wrapped_text(self):
        data = reconstruct_rows(result([word("上",20,40), word("下",20,60)]), {"columns":list(range(0,801,100)), "rowBoundaries":[30,90]})
        self.assertEqual(data["rows"][0]["fields"][0], "上 下")

    def test_boundary_issues(self):
        data = reconstruct_rows(result([word("截断",0,40,110)]), {"columns":list(range(0,801,100))})
        self.assertEqual(len(data["rows"][0]["issues"]), 2)

    def test_invalid_options(self):
        for options in [{"columns":[0]*9}, {"crop":{"x":0,"y":0,"width":900,"height":20}}, {"columns":[0,100,200,300,400,500,600,700,float("nan")]}]:
            with self.assertRaises(ValueError):
                reconstruct_rows(result([]),options)

    def test_format_and_size_limits(self):
        for data in [b"", b"invalid", b"x"*(MAX_BYTES+1)]:
            with self.assertRaises(ValueError):
                recognize_image(data)
        for fmt, size in [("GIF",(10,10)),("PNG",(10001,1)),("PNG",(7000,6000))]:
            buffer=io.BytesIO()
            Image.new("1",size).save(buffer,format=fmt)
            with self.assertRaises(ValueError):
                recognize_image(buffer.getvalue())

    def test_truncated_png(self):
        buffer=io.BytesIO()
        Image.new("RGB",(100,100)).save(buffer,format="PNG")
        with self.assertRaises(ValueError):
            recognize_image(buffer.getvalue()[:60])

    def test_timeout_uses_system_powershell_and_cleans_tempfile(self):
        from pathlib import Path
        buffer = io.BytesIO()
        Image.new("RGB", (100, 100)).save(buffer, format="PNG")
        paths = []
        def timeout(command, **kwargs):
            self.assertTrue(command[0].lower().endswith("system32\\windowspowershell\\v1.0\\powershell.exe"))
            self.assertEqual(kwargs["timeout"], 45)
            paths.append(Path(command[-1]))
            self.assertTrue(paths[0].exists())
            raise subprocess.TimeoutExpired(command, 45)
        with patch("control_console.account_ocr.subprocess.run", side_effect=timeout):
            with self.assertRaisesRegex(RuntimeError, "超时"):
                recognize_image(buffer.getvalue())
        self.assertFalse(paths[0].parent.exists())


if __name__ == "__main__":
    unittest.main()
