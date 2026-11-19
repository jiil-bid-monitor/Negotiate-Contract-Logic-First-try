import unittest

from webapp.postaward import selfcheck


class PostAwardTest(unittest.TestCase):
    def test_selfcheck(self):
        selfcheck()

    def test_start_role_label_formula(self):
        """착수 틀 v1.5 인력 이름표가 수식이면 업무 칸대로 조직도 이름을 보여 줌(11/19)."""
        import tempfile
        from pathlib import Path

        from openpyxl import Workbook

        from webapp.postaward import sheet_todo

        for work, want in (("물품", "3 · 디자인·설계"), ("용역", "3 · 기획")):
            wb = Workbook(); ws = wb.active; ws.title = "기본사항"
            ws["B4"] = work
            ws.cell(26, 1).value = '=IF($B$4="물품","3 · 디자인·설계","3 · 기획")'
            with tempfile.TemporaryDirectory() as d:
                p = Path(d) / "t.xlsx"; wb.save(p)
                todo, _ = sheet_todo(p, gong=False)
            self.assertTrue(any(t.startswith(want + ":") for t in todo), todo)


if __name__ == "__main__":
    unittest.main()
