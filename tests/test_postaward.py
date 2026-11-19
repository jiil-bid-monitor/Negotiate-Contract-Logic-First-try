import unittest

from webapp.postaward import selfcheck


class PostAwardTest(unittest.TestCase):
    def test_selfcheck(self):
        selfcheck()

    def test_reissue_docs_found_and_sheet_added(self):
        """공고문이 새로 발급할 서류를 요구하면 보유본 날짜와 함께 찾고, 엑셀에 '⚠새로발급' 탭(11/19)."""
        import json
        import tempfile
        from pathlib import Path

        from openpyxl import Workbook, load_workbook

        from webapp.postaward import REISSUE_SHEET, add_reissue_sheet, reissue_needed

        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "_목록.json").write_text(json.dumps([{"종류": "국세 납세증명서", "발급일": "2022-09-23"}], ensure_ascii=False), encoding="utf-8")
            rows = reissue_needed(["제출서류: 국세 납세 증명서 1부, 법인 등기사항전부증명서 1부"], d)
            self.assertEqual(rows, [("국세 납세증명서", "2022-09-23"), ("법인등기사항전부증명서", "보유본 없음")])
            self.assertEqual(reissue_needed(["사업자등록증 사본"], d), [])
            both = [k for k, _ in reissue_needed(["국세 및 지방세 완납증명서, 4대보험 가입자명부"], d)]
            self.assertEqual(both, ["국세 납세증명서", "지방세 납세증명서", "4대보험 가입증명"])
            wb = Workbook(); wb.active.title = "기본사항"; wb.create_sheet("공문"); wb.save(d / "t.xlsx")
            add_reissue_sheet(d / "t.xlsx", rows)
            add_reissue_sheet(d / "t.xlsx", rows)  # 두 번 넣어도 탭 하나
            names = load_workbook(d / "t.xlsx").sheetnames
            self.assertEqual(names, ["기본사항", REISSUE_SHEET, "공문"])

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
