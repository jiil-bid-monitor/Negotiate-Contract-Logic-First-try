"""webapp/xlsxfit.py — 칸보다 긴 글은 줄 높이를 늘리고(수식으로 받은 값도), 가려지는 글은 줄바꿈을 켬."""
import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment

from webapp import xlsxfit


class XlsxFitTest(unittest.TestCase):
    def test_rows_grow_for_long_and_referenced_text(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "a.xlsx"
            wb = Workbook()
            base = wb.active
            base.title = "기본사항"
            base["A1"], base["B1"] = "사업명", "광양시 교통안전체험관 제작 및 설치 — 아주 긴 사업 이름이 들어가 칸을 넘는 경우"
            base["A2"], base["B2"], base["C2"] = "주소", "강원특별자치도 정선군 남면 무릉2로 64(공장1층) 아주 긴 주소", "안내"
            base.column_dimensions["B"].width = 12
            form = wb.create_sheet("청구서")
            form["A1"] = '=IF(기본사항!$B$1="","",기본사항!$B$1)'
            form["A1"].alignment = Alignment(wrap_text=True)
            form.column_dimensions["A"].width = 20
            form["A2"] = "짧음"
            for ws in (base, form):
                for r in (1, 2):
                    ws.row_dimensions[r].height = 15
            base["B1"].alignment = Alignment(wrap_text=True)
            wb.save(p)
            out = xlsxfit.fit(p)
            self.assertTrue(out)
            w = load_workbook(p)
            self.assertGreater(w["기본사항"].row_dimensions[1].height, 40)  # 줄바꿈 칸 — 여러 줄
            self.assertTrue(w["기본사항"]["B2"].alignment.wrap_text)  # 옆 칸(C2)에 가려지던 글 — 줄바꿈 켬
            self.assertGreater(w["기본사항"].row_dimensions[2].height, 15)
            self.assertGreater(w["청구서"].row_dimensions[1].height, 15)  # 수식으로 받은 값도 잼
            self.assertEqual(w["청구서"].row_dimensions[2].height, 15)  # 들어가는 글은 그대로
            self.assertEqual(xlsxfit.fit(p), [])  # 다시 해도 고칠 것 없음


if __name__ == "__main__":
    unittest.main()
