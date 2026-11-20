"""낙찰 후 서류 — 서류 날짜로 단계(착수·납품·청구), 변경계약 따라가기(webapp/postaward.py, 11/20)."""
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook

from webapp import postaward
from webapp.postaward import PostAward


def basic(path: Path, label: str, value) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "기본사항"
    ws["A5"], ws["B5"] = label, value
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


class AwardProgressTest(unittest.TestCase):
    def test_progress_from_document_dates(self):
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d) / "물품·용역" / "R1-000_광양"
            pa = PostAward(Path(d))
            pa._write({"R1-000": {"no": "R1", "ord": "000", "status": "낙찰", "work": "물품", "folder": str(folder),
                                  "contract": {"date": "2025-11-26"}}})
            basic(folder / "2_착수" / "착수서류.xlsx", "제출일", datetime(2025, 12, 26))
            basic(folder / "3_납품" / "납품계.xlsx", "제출일", '=IF(A1="","",A1)')  # 수식 = 아직 안 냄
            basic(folder / "4_청구" / "청구서.xlsx", "청구일", None)
            row = pa.items()[0]
            self.assertEqual(row["progress"], {"start": "2025-12-26"})
            basic(folder / "3_납품" / "납품계.xlsx", "제출일", "2026-03-16")
            self.assertEqual(pa.items()[0]["progress"], {"start": "2025-12-26", "done": "2026-03-16"})

    def test_contract_change_is_recorded(self):
        with tempfile.TemporaryDirectory() as d:
            pa = PostAward(Path(d))
            pa._write({"R1-000": {"no": "R1", "ord": "000", "status": "낙찰", "work": "물품",
                                  "contract": {"no": "C1", "date": "2025-11-26", "due": "2026-02-24", "amount": "390144500"}}})
            row = {"untyCntrctNo": "C1", "cntrctDate": "2025-11-26", "thtmScmpltDate": "2026-03-16", "thtmCntrctAmt": "405880000"}
            self.assertEqual(pa.check_contracts(lambda v: row), 0)
            v = json.loads((Path(d) / postaward.STATE_FILE).read_text(encoding="utf-8"))["R1-000"]
            self.assertEqual((v["contract"]["amount"], v["contract"]["due"]), ("405880000", "2026-03-16"))
            self.assertEqual(v["changes"][-1], {"at": datetime.now().strftime("%Y-%m-%d"), "amount": "405880000", "due": "2026-03-16"})
            pa.check_contracts(lambda v: row)  # 같으면 다시 안 남김
            v = json.loads((Path(d) / postaward.STATE_FILE).read_text(encoding="utf-8"))["R1-000"]
            self.assertEqual(len(v["changes"]), 1)


if __name__ == "__main__":
    unittest.main()
