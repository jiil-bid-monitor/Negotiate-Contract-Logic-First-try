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

    def test_floor_estimated_from_bidders_when_a_unknown(self):
        # 실제 A = 5백만이지만 공고 값은 모름 — 순위 있는 최저 금액과 하한 미달 최고 금액 사이로 하한선을 가늠한다
        from nego.bidprice import Bidder

        basis, planned, lower, a = 100_000_000, 100_200_000, 87.745, 5_000_000
        floor = a + (planned - a) * lower / 100
        bidders = [Bidder(1, "갑", "1", floor + 300, None, ""), Bidder(2, "을", "2", floor + 900, None, ""),
                   Bidder(None, "병", "3", floor - 500, None, "하한미달"), Bidder(None, "정", "4", floor + 50, None, "서류 미비"),
                   Bidder(None, "지일", "206-86-36338", br.bid_amount(basis, 99.9, lower, a), None, "하한미달")]
        rec = br._make("1", "공사", basis, planned, lower, bidders, [], own_bizno="2068636338", a_value=None)
        self.assertEqual(rec.floor_source, "추정")
        self.assertAlmostEqual(rec.floor, floor - 100)  # '미달' 표시 업체만 — 서류 미비(정)는 안 씀
        self.assertLess(rec.win_ceiling - rec.assessment, 0.001)  # 대규모 경쟁처럼 1순위 구간이 아주 좁다
        self.assertAlmostEqual(rec.rate_for(rec.own_amount), 99.9, places=3)  # A를 몰라도 지일 가정 사정률이 맞음
        # A=0으로 계산하던 예전 방식은 구간을 크게 부풀렸다
        rec.floor_est = None
        self.assertGreater(rec.win_ceiling - rec.assessment, 0.5)

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

    def test_track_record_fixed_row_and_outsourced(self):
        # 날짜가 있는 과거 공고 24건 — 공고마다 그 전 공고만으로 정한 사정률로 채점(검증 성적), 고정 100.55%·외주 줄 함께
        records = []
        for i in range(24):
            rec = _past(f"N{i:02d}", 100.0 + (i % 3) * 0.1)
            rec.opened_at = f"2025-{1 + i // 12:02d}-{1 + i % 12:02d}"
            rec.own_amount = br.bid_amount(rec.basis, 99.7, rec.lower_rate)  # 외주: 실제보다 낮게 써서 늘 하한 미달
            rec.own_rank = None
            records.append(rec)
        out = br.recommend(br.History(records), work="용역", lo=-3, hi=3)
        names = [o["name"] for o in out["options"]]
        self.assertIn(f"고정 {br.FIXED_RATE:g}%", names)
        best = out["options"][0]["track"]
        self.assertEqual(best["tried"], 24 - br.MIN_SAMPLES)  # 앞의 10건은 그 전 비슷한 공고가 모자라 추천 없음
        self.assertGreater(best["win"], 0)
        fixed = next(o for o in out["options"] if o["name"].startswith("고정"))["track"]
        self.assertEqual(fixed["tried"], 24)  # 고정값은 자료가 없어도 채점
        own = out["outsourced"]
        self.assertAlmostEqual(own["rate"], 99.7, places=3)
        self.assertEqual((own["track"]["tried"], own["track"]["valid"], own["track"]["win"]), (24, 0, 0))

    def test_pick_uses_best_track_and_compares_outsourced(self):
        records = []
        for i in range(36):  # 앞 10건 뒤 26건이 검증돼 MIN_PICK_TRIED(20)를 넘김
            rec = _past(f"N{i:02d}", 100.0 + (i % 3) * 0.1)
            rec.opened_at = f"2025-{1 + i // 12:02d}-{1 + i % 12:02d}"
            rec.own_amount = br.bid_amount(rec.basis, 99.7, rec.lower_rate)
            records.append(rec)
        out = br.recommend(br.History(records), work="용역", lo=-3, hi=3, basis=100_000_000, lower_rate=87.745)
        pick = out["pick"]
        self.assertFalse(pick["weak"])
        best = max(br._ratio(o["track"]) for o in out["options"] if o["track"]["tried"] >= br.MIN_PICK_TRIED)
        self.assertAlmostEqual(br._ratio(pick["track"]), best)
        self.assertTrue(pick["beats_outsourced"])  # 외주는 늘 하한 미달

    def test_pick_falls_back_to_fixed_when_few(self):
        options = [{"name": "추천(과거 1순위 최다)", "rate": 100.1, "track": {"tried": 5, "valid": 5, "win": 5}},
                   {"name": "고정 100.55%", "rate": 100.55, "track": {"tried": 5, "valid": 3, "win": 0}}]
        pick = br.pick_option(options, {"track": {"tried": 5, "valid": 1, "win": 1}})
        self.assertTrue(pick["weak"])
        self.assertEqual(pick["name"], "고정 100.55%")
        self.assertFalse(pick["beats_outsourced"])
        self.assertIsNone(br.pick_option([]))

    def test_auto_scale_from_similar_bidder_count(self):
        small = [_past(f"s{i}", 100.0 + i * 0.01, count=20) for i in range(12)]
        out = br.recommend(br.History(small), work="용역", scale="auto")
        self.assertEqual(out["scale"], br.SCALES["small"])
        self.assertIn("자동", out["auto_scale"])
        large = [_past(f"l{i}", 100.0 + i * 0.01, count=300) for i in range(12)]
        self.assertEqual(br.recommend(br.History(large), work="용역", scale="auto")["scale"], br.SCALES["large"])

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
            (d / "bid_results_채우기전.jsonl").write_text(json.dumps(result_line) + "\n", encoding="utf-8")  # 원본 사본은 안 읽음
            self.assertEqual([p.name for p in br.history_paths(d)], ["bid_history_20260701_20260930.json", "bid_results.jsonl"])
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
