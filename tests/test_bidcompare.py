"""투찰 공고 방식별 순위 비교(`nego/bidcompare.py`) — 만든 개찰 자료로 순위 계산·CSV만 확인한다."""

from __future__ import annotations

import csv
import io
import json
import random
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from nego import bidcompare as bc  # noqa: E402
from nego import bidrecommend as br  # noqa: E402
from test_bidrecommend import _large  # noqa: E402


class RankTest(unittest.TestCase):
    def setUp(self):
        self.rec = _large("R1", "2025-03-01", 100.0, [100.1, 100.2, 100.3, 99.5])  # 99.5는 하한 미달
        self.rec.own_amount = self.rec.amounts[1]  # 지일 = 100.2

    def test_amount_round_trip(self):
        self.assertAlmostEqual(self.rec.rate_for(bc.amount_for(self.rec, 100.25)), 100.25)

    def test_rank_skips_own_and_below_floor(self):
        self.assertIsNone(bc.rank_of(self.rec, bc.amount_for(self.rec, 99.9)))
        # 100.15 — 지일(100.2)은 이 금액 대신이라 빼고, 100.1만 더 쌈 → 2위
        self.assertEqual(bc.rank_of(self.rec, bc.amount_for(self.rec, 100.15)), 2)
        self.assertEqual(bc.rank_of(self.rec, self.rec.own_amount, skip_own=False), 2)
        self.assertEqual(bc.rank_of(self.rec, bc.amount_for(self.rec, 100.0)), 1)


class MainTest(unittest.TestCase):
    def test_csv_and_summary(self):
        rnd = random.Random(3)
        recs = [_large(f"R26BK{i:08d}", f"2025-{1 + i // 28:02d}-{1 + i % 28:02d}", rnd.gauss(100, 0.5),
                       [rnd.gauss(100.2, 0.3) for _ in range(30)]) for i in range(60)]
        items = []
        for r in recs:
            bidders = sorted(r.amounts)
            items.append({"notice_no": r.notice_no, "work_type": "공사", "basis_amount": r.basis, "planned_price": r.planned,
                          "lower_limit_rate": r.lower_rate, "range_lo": -3, "range_hi": 3, "a_value": 0,
                          "opened_at": f"{r.opened_at} 11:00", "title": "○○ 실내건축 공사",
                          "bidders": [{"rank": None if a < r.planned * r.lower_rate / 100 else k + 1, "name": f"업체{k}",
                                       "bizno": "206-86-36338" if k == 3 else f"{k:03d}", "amount": a,
                                       "rate": a / r.planned * 100, "remark": ""} for k, a in enumerate(bidders)]})
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "bid_history_20250101_20250301.json").write_text(json.dumps(items), encoding="utf-8")
            targets = d / "목록.csv"
            targets.write_text("공고번호\nR26BK00000055-000\nR26BK00000058\nR26BK99999999\n", encoding="utf-8")
            out = io.StringIO()
            with redirect_stdout(out):
                code = bc.main([str(targets), "--dir", str(d), "--out", str(d / "cmp.csv")])
            self.assertEqual(code, 0)
            table = list(csv.reader((d / "cmp.csv").open(encoding="utf-8-sig")))
        head = table[0]
        self.assertIn("추천 순위", head)
        self.assertIn(f"{bc.OWN_COL} 순위", head)
        self.assertEqual([r[0] for r in table[1:]], ["R26BK00000055", "R26BK00000058", "R26BK99999999"])
        self.assertIn("채점 못 함", table[3][-1])
        text = out.getvalue()
        self.assertIn("채점 공고 2건", text)
        self.assertIn("추천 칸이 고른 방식", text)


if __name__ == "__main__":
    unittest.main()
