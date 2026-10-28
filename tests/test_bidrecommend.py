"""추천 사정률(`nego/bidrecommend.py`) 테스트 — 만든 과거 개찰 자료로 계산만 확인한다."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nego import bidrecommend as br  # noqa: E402


def _past(no: str, s: float, winner_gap: float = 0.3, lower: float = 87.745, work: str = "용역",
          width: int | None = 3, count: int = 30, a: float | None = 0.0, basis: float = 100_000_000) -> br.PastBid:
    """사정률 s(%)에 열린 공고 — 1순위 투찰률 = 하한율 + winner_gap(%p)."""
    return br.PastBid(notice_no=no, work=work, basis=basis, planned=basis * s / 100, lower_rate=lower,
                      winner_rate=lower + winner_gap, bidder_count=count, range_width=width, a_value=a)


class PastBidTest(unittest.TestCase):
    def test_win_ceiling_without_a(self):
        rec = _past("1", 100.0, winner_gap=0.255)  # 1순위 88.000%
        # 우리 투찰률 = 하한율 × r/s → 88.000% 미만이 되는 r < 100 × 88 / 87.745
        self.assertAlmostEqual(rec.win_ceiling, 100 * 88.0 / 87.745, places=6)

    def test_win_ceiling_with_a_is_narrower(self):
        # 같은 개찰 결과(1순위 88.645%)를 A값 모름(0)과 A값 5백만(하한선 88.358%)으로 계산 — A를 알면 구간이 좁아진다
        no_a = _past("1", 100.0, winner_gap=0.9, a=None)
        with_a = _past("1", 100.0, winner_gap=0.9, a=5_000_000)
        self.assertLess(with_a.win_ceiling, no_a.win_ceiling)
        # A가 있어도 r = s면 투찰금액 = 하한선(유효), 그보다 아주 조금 위까지 1순위
        self.assertGreater(with_a.win_ceiling, with_a.assessment)

    def test_bid_amount_matches_formula(self):
        self.assertAlmostEqual(br.bid_amount(100_000_000, 100.5, 87.745, 3_000_000),
                               3_000_000 + (100_500_000 - 3_000_000) * 0.87745)


class CoverageTest(unittest.TestCase):
    def test_counts_win_and_valid_ranges(self):
        rec = _past("1", 100.0, winner_gap=0.255)
        wins, valid = br.coverage([rec])
        at = lambda r: br._index(r)  # noqa: E731
        self.assertEqual(valid[at(99.999)], 0)
        self.assertEqual(valid[at(100.0)], 1)  # r = s면 하한선과 같아 유효
        self.assertEqual(wins[at(100.0)], 1)
        self.assertEqual(wins[at(100.290)], 1)  # 상한 100.2906 미만
        self.assertEqual(wins[at(100.291)], 0)
        self.assertEqual(valid[at(103.0)], 1)  # 1순위는 아니어도 유효

    def test_recommend_picks_most_overlapping_rate(self):
        # 사정률 99.8 · 100.0 · 100.1 공고에서 1순위 구간(각 ~+0.34%p)이 모두 겹치는 곳은 100.1 근처
        records = [_past(str(i), s) for i, s in enumerate([99.8, 100.0, 100.1] * 4)]
        out = br.recommend(br.History(records), work="용역", lo=-3, hi=3, basis=200_000_000, lower_rate=87.745)
        best = out["best"]
        self.assertEqual(best["wins"], 12)
        self.assertGreaterEqual(best["rate"], 100.1)
        self.assertLess(best["rate"], 99.8 * (87.745 + 0.3) / 87.745)
        self.assertAlmostEqual(best["bid"], br.bid_amount(200_000_000, best["rate"], 87.745))
        self.assertEqual(out["samples"], 12)
        self.assertEqual(out["conditions"], ["업무 용역", "변동폭 ±3%"])

    def test_large_competition_note_and_no_win(self):
        # 1순위가 하한선 바로 위(대규모 경쟁) — 서로 겹치지 않는 아주 좁은 구간뿐
        records = [_past(str(i), 99.0 + i * 0.1, winner_gap=0.004, count=300) for i in range(12)]
        out = br.recommend(br.History(records), work="용역")
        self.assertEqual(out["best"]["wins"], 1)
        self.assertTrue(any("대규모 경쟁" in n for n in out["notes"]))


class SimilarTest(unittest.TestCase):
    def test_relaxes_range_then_work_when_few(self):
        records = [_past(f"a{i}", 100.0, width=3) for i in range(4)] + [_past(f"b{i}", 100.0, width=2) for i in range(8)]
        records += [_past(f"c{i}", 100.0, work="공사", width=3) for i in range(20)]
        picked, labels = br.similar(records, "용역", 3)
        self.assertEqual(len(picked), 12)  # ±3 용역 4건뿐 → 변동폭 조건을 풀어 용역 12건
        self.assertEqual(labels, ["업무 용역"])
        picked, labels = br.similar(records, "공사", 3)
        self.assertEqual(len(picked), 20)
        self.assertEqual(labels, ["업무 공사", "변동폭 ±3%"])

    def test_scale_filter_is_kept(self):
        records = [_past(str(i), 100.0, count=50 if i % 2 else 200) for i in range(30)]
        picked, labels = br.similar(records, "용역", 3, "small")
        self.assertTrue(all(r.bidder_count < 100 for r in picked))
        self.assertIn("참가 100곳 미만", labels)

    def test_range_width_from_prices(self):
        basis = 100_000_000
        self.assertEqual(br.range_width([basis * (1 + d / 100) for d in (-2.9, 0.1, 1.5)], basis), 3)
        self.assertEqual(br.range_width([basis * (1 + d / 1000) for d in range(-15, 15, 2)], basis), 2)
        self.assertIsNone(br.range_width([basis * 1.01] * 4, basis))  # 추첨된 4개만으로는 모름


class LoadHistoryTest(unittest.TestCase):
    def test_reads_both_formats_and_dedupes(self):
        bid_history = [{
            "notice_no": "R26BK00000001", "work_type": "공사", "title": "가", "basis_amount": 100_000_000,
            "planned_price": 100_200_000, "lower_limit_rate": 87.745, "range_lo": -3, "range_hi": 3, "a_value": 0,
            "drawn_prices": [], "bidders": [
                {"rank": 1, "name": "갑", "bizno": "1", "amount": 88_100_000, "rate": 87.92, "remark": ""},
                {"rank": None, "name": "을", "bizno": "2", "amount": 87_000_000, "rate": 86.8, "remark": "하한미달"}],
        }, {"notice_no": "R26BK00000009", "work_type": "공사", "basis_amount": None, "bidders": []}]
        result_line = {
            "input": "R26BK00000002-000", "no": "R26BK00000002", "ord": "000", "work": "용역",
            "prices": [{"bsisPlnprc": str(100_000_000 + i * 200_000), "drwtYn": "Y" if i < 4 else "N",
                        "plnprc": "100300000", "bssamt": "100000000"} for i in range(-7, 8)],
            "bidders": [{"opengRank": "1", "prcbdrNm": "병", "prcbdrBizno": "3", "bidprcAmt": "88200000", "bidprcrt": "87.94"}],
            "notice": {"lower_rate": "87.745", "range_lo": -2, "range_hi": 2, "a_value": 1_000_000, "a_applies": True},
        }
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "bid_history_20260701_20260930.json").write_text(json.dumps(bid_history), encoding="utf-8")
            (d / "bid_results.jsonl").write_text(json.dumps(result_line) + "\n" + json.dumps({"input": "x", "error": "없음"}) + "\n",
                                                 encoding="utf-8")
            history = br.load_history(br.history_paths(d))
        by_no = {r.notice_no: r for r in history.records}
        self.assertEqual(set(by_no), {"R26BK00000001", "R26BK00000002"})
        self.assertEqual(history.skipped, 2)  # 금액 없는 공고 · 오류 줄
        first = by_no["R26BK00000001"]
        self.assertAlmostEqual(first.winner_rate, 88_100_000 / 100_200_000 * 100)
        self.assertEqual(first.range_width, 3)
        second = by_no["R26BK00000002"]
        self.assertEqual((second.range_width, second.a_value, second.work), (2, 1_000_000, "용역"))
        self.assertAlmostEqual(second.assessment, 100.3)

    def test_empty_history_explains(self):
        out = br.recommend(br.History())
        self.assertEqual(out["samples"], 0)
        self.assertIn("과거 개찰 자료가 없습니다", out["notes"][0])


if __name__ == "__main__":
    unittest.main()
