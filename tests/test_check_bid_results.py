"""scripts/check_bid_results.py — 목록 대비 상태·낙찰방법 집계·이력 참여수 대조(가짜 자료), 업체명·금액이 출력에 안 나오는지."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import check_bid_results as cb  # noqa: E402


def _hist_row(n, notice, count):
    cells = [str(n), "공고", notice] + ["x"] * 9 + [count] + ["x"] * 12 + ["2026/10/23"]
    return "<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>"


class CheckBidResultsTest(unittest.TestCase):
    def test_report(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "t.csv").write_text("공고번호,업무,기관,입력일\nA-000,시설,조달청,2014/01/02\nB-000,물품,조달청,2015/03/04\nC-000,용역,조달청,2015/05/06\n",
                                     encoding="utf-8-sig")
            bidder = {"opengRank": "1", "prcbdrNm": "비밀업체", "prcbdrBizno": "1234567890", "bidprcAmt": "987654321"}
            recs = [{"input": "A-000", "prices": [{"plnprc": "1"}] * 15, "bidders": [bidder, bidder], "notice": {"award_method": "적격심사", "contract_method": "제한경쟁"}},
                    {"input": "B-000", "error": "나라장터에서 개찰결과를 찾지 못함"},
                    {"input": "X-000", "prices": [], "bidders": []}]
            (d / "r.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in recs) + "\n", encoding="utf-8")
            hist = d / "h" / "2014"
            hist.mkdir(parents=True)
            (hist / "1.xls").write_text("<table>" + _hist_row(1, "조달청 시설 A-000 호", "3") + "</table>", encoding="utf-8")
            out = d / "o.txt"
            self.assertEqual(cb.main([str(d / "t.csv"), "--results", str(d / "r.jsonl"), "--history", str(d / "h"), "--out", str(out)]), 0)
            text = out.read_text(encoding="utf-8")
            self.assertIn("목록 3건 — 받음 1 · 실패 1 · 안 받음 1", text)
            self.assertIn("목록에 없는 공고 1건", text)
            self.assertIn("2015: 2 / 0 / 1 / 1", text)
            self.assertIn("    1  적격심사", text)
            self.assertIn("prcbdrNm: 2/2", text)
            self.assertIn("같음 0 · 다름 1", text)
            self.assertIn("A-000 (이력 3 · 받음 2)", text)
            for secret in ("비밀업체", "1234567890", "987654321"):
                self.assertNotIn(secret, text)


if __name__ == "__main__":
    unittest.main()
