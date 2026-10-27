"""scripts/make_bid_targets.py — 공고번호 칸 나누기·표 읽기·조달청만 고르기(가짜 투찰 이력)."""

import csv
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import make_bid_targets as mt  # noqa: E402


def _row(n, notice):
    cells = [str(n), "공고", notice] + ["x"] * 22 + ["2026/10/23"]
    return "<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>"


class MakeBidTargetsTest(unittest.TestCase):
    def test_parse_notice_cell(self):
        self.assertEqual(mt.parse_notice_cell("조달청 시설 R26BK01772578-000 호"), ("조달청", "공사", "R26BK01772578-000"))
        self.assertEqual(mt.parse_notice_cell("조달청 물품 20141124968-00 호"), ("조달청", "물품", "20141124968-00"))
        self.assertEqual(mt.parse_notice_cell("한국토지주택공사 1600511-00 호"), ("한국토지주택공사", "", "1600511-00"))

    def test_main_g2b_only_and_dedupe(self):
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "2026"
            src.mkdir()
            rows = [_row(1, "조달청 시설 R26BK00000001-000 호"), _row(2, "국방부 공사 LGT01602018-19331-02 호"),
                    _row(3, "조달청 용역 R26BK00000001-000 호"), "<tr><td>번호</td></tr>"]
            (src / "1.xls").write_text("<html><table>" + "".join(rows) + "</table></html>", encoding="utf-8")
            out = Path(d) / "t.csv"
            self.assertEqual(mt.main(["--dir", d, "--out", str(out)]), 0)
            with out.open(encoding="utf-8-sig") as f:
                got = list(csv.reader(f))
            self.assertEqual(got, [["공고번호", "업무", "기관", "입력일"], ["R26BK00000001-000", "공사", "조달청", "2026/10/23"]])
            self.assertEqual(mt.main(["--dir", d, "--out", str(out), "--all"]), 0)
            with out.open(encoding="utf-8-sig") as f:
                self.assertEqual(len(list(csv.reader(f))), 3)


if __name__ == "__main__":
    unittest.main()
