"""scripts/analyze_bid_results.py — 투찰률−하한율·개찰 1위·지일 집계(가짜 자료), 업체명·금액이 출력에 안 나오는지."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import analyze_bid_results as ab  # noqa: E402


def _b(rank, rate, bizno="1112233333", name="비밀업체", amount="555444333"):
    return {"opengRank": str(rank), "bidprcrt": str(rate), "prcbdrBizno": bizno, "prcbdrNm": name, "bidprcAmt": amount}


class AnalyzeBidResultsTest(unittest.TestCase):
    def test_notice_stats(self):
        rec = {"prices": [{"plnprc": "101", "bssamt": "100"}],
               "bidders": [_b(1, 87.80), _b(2, 87.90), _b("", 87.70), _b(3, 88.00, bizno="206-86-36338")]}
        s = ab.notice_stats(rec, 87.745, "2068636338")
        self.assertAlmostEqual(s["act"], 1.0)
        self.assertAlmostEqual(s["first"], 0.055)
        self.assertAlmostEqual(s["lowest_ok"], 0.055)
        self.assertEqual(s["under"], 0.25)
        self.assertEqual(s["near"], 0.25)  # 0~0.1 구간은 87.80 하나
        self.assertAlmostEqual(s["own_gap"], 0.255)
        self.assertFalse(s["own_first"])

    def test_method_group(self):
        self.assertEqual(ab.method_group({"notice": {"award_method": "협상에의한계약"}}), "협상")
        self.assertEqual(ab.method_group({"notice": {"award_method": "공고서참조"}}), "모름")
        self.assertEqual(ab.method_group({}), "모름")
        self.assertEqual(ab.method_group({"notice": {"award_method": "적격심사"}}), "그 외")

    def test_main_report(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "t.csv").write_text("공고번호,업무,기관,입력일\nA-000,시설,조달청,2024/01/02\nB-000,물품,조달청,2015/01/01\n", encoding="utf-8-sig")
            recs = [{"input": "A-000", "prices": [{"plnprc": "633000000", "bssamt": "633000000"}],
                     "bidders": [_b(1, 87.80), _b(2, 87.90, amount="556000000"), _b("", 85.0, bizno="2068636338", amount="538000000")],
                     "notice": {"award_method": "적격심사", "lower_rate": "87.745"}},
                    {"input": "B-000", "prices": [], "bidders": [_b(1, 90)]}]
            (d / "r.jsonl").write_text("\n".join(json.dumps(r) for r in recs) + "\n", encoding="utf-8")
            out = d / "o.txt"
            self.assertEqual(ab.main([str(d / "t.csv"), "--results", str(d / "r.jsonl"), "--out", str(out)]), 0)
            text = out.read_text(encoding="utf-8")
            self.assertIn("성공 공고 2건 중 분석 2건", text)  # 1부: 하한율 없어도 분석
            self.assertIn("분석 1건", text)  # 2부: 하한율 있는 공고만
            self.assertIn("하한율 없음", text)
            self.assertIn("시설 | 1 | 3 | +0.000 | +0.055", text)
            self.assertIn("시설 | 1 | 3 | +0.000 | 87.748 | 87.748 | 87.800 | 0.100 | 33%", text)
            self.assertIn("지일 — 참가 확인 1건", text)
            self.assertIn("1~9곳 | 1 | 84.992 | -2.756 | - | 100% | 0", text)
            for secret in ("비밀업체", "1112233333", "555444333"):
                self.assertNotIn(secret, text)


if __name__ == "__main__":
    unittest.main()
