"""추천 사정률(`nego/bidrecommend.py`) 테스트 — 만든 과거 개찰 자료로 계산만 확인한다."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from collections import Counter
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

    def test_pick_uses_rank_when_no_method_wins_often(self):
        # 10/29 사용자 결정(2안): 어느 방식도 1순위 비율이 PICK_MIN_WIN(10%) 미만이면 통과했을 때 평균 순위가 가장 좋은 방식
        options = [{"name": "고정 100.55%", "rate": 100.55, "track": {"tried": 300, "valid": 250, "win": 6, "top": 10, "rank": 0.45}},
                   {"name": br.TREND_NAME, "rate": 100.1, "base": 100.0, "gap": 0.1,
                    "track": {"tried": 300, "valid": 200, "win": 3, "top": 8, "rank": 0.25}}]
        pick = br.pick_option(options)
        self.assertEqual((pick["mode"], pick["name"]), ("rank", br.TREND_NAME))
        options[0]["track"]["win"] = 60  # 20% — 검증 성적이 확실하면 그 방식
        self.assertEqual(br.pick_option(options)["mode"], "track")

    def test_pick_requires_floor_pass_ratio(self):
        # 10/29 제보: 하한선 통과 31%인 '과거 5위 안 최다'가 1순위 비율만으로 뽑혀 하한선 근처 금액이 추천됨 → 통과 60% 이상만 고름
        options = [{"name": "추천(과거 5위 안 최다)", "rate": 98.413, "track": {"tried": 400, "valid": 124, "win": 60, "top": 90}},
                   {"name": "유효 70%", "rate": 100.3, "track": {"tried": 400, "valid": 290, "win": 20, "top": 40}},
                   {"name": br.TREND_NAME, "rate": 100.1, "track": {"tried": 400, "valid": 210, "win": 8, "top": 20}}]
        pick = br.pick_option(options)
        self.assertNotEqual(pick["name"], "추천(과거 5위 안 최다)")
        self.assertGreaterEqual(pick["track"]["valid"] / pick["track"]["tried"], br.PICK_MIN_VALID)
        # 조건을 만족하는 방식이 없으면 하한선 통과 비율이 가장 높은 방식
        for o in options:
            o["track"]["valid"] = 100
        options[1]["track"]["valid"] = 180
        self.assertEqual((br.pick_option(options)["name"], br.pick_option(options)["mode"]), ("유효 70%", "valid"))

    def test_pick_order_lists_chosen_then_passing_then_rest(self):
        options = [{"name": "A", "rate": 98.4, "track": {"tried": 400, "valid": 124, "win": 60, "top": 90}},
                   {"name": "B", "rate": 100.3, "track": {"tried": 400, "valid": 290, "win": 20, "top": 40}},
                   {"name": "C", "rate": 100.7, "track": {"tried": 400, "valid": 360, "win": 4, "top": 18}},
                   {"name": br.CROWD_NAME, "rate": 100.2, "track": {"tried": 400, "valid": 250, "win": 9, "top": 37}}]
        for o, rank in zip(options, (0.05, 0.2, 0.4, 0.1)):
            o["track"]["rank"] = rank
        pick = br.pick_option(options)  # 1순위 최고가 B 5%(10% 미만) → 통과했을 때 순위로
        self.assertEqual(pick["mode"], "rank")
        self.assertEqual(pick["order"], [br.CROWD_NAME, "B", "C", "A"])  # 고른 것 → 통과 조건 만족(같은 기준 순) → 미달

    def test_trend_rate_is_median_rate_plus_top_gap(self):
        recs = []
        for i, s in enumerate((99.0, 100.0, 101.0)):
            rec = _past(str(i), s)
            rec.top_amounts = [br.bid_amount(rec.basis, s + d, rec.lower_rate) for d in (0.01, 0.02, 0.03)]
            recs.append(rec)
        base, gap = br.trend_rate(recs)
        self.assertAlmostEqual(base, 100.0)
        self.assertAlmostEqual(gap, 0.02, places=6)
        self.assertIsNone(br.trend_rate([]))

    def test_auto_scale_from_similar_bidder_count(self):
        small = [_past(f"s{i}", 100.0 + i * 0.01, count=20) for i in range(12)]
        out = br.recommend(br.History(small), work="용역", scale="auto")
        self.assertEqual(out["scale"], br.SCALES["small"])
        self.assertIn("자동", out["auto_scale"])
        large = [_past(f"l{i}", 100.0 + i * 0.01, count=300) for i in range(12)]
        self.assertEqual(br.recommend(br.History(large), work="용역", scale="auto")["scale"], br.SCALES["large"])

    def test_field_from_notice_when_title_has_none(self):
        # 공고명 '기타'면 공고문 사업개요로 분야를 정하고 근거를 남김(10/30) — 과거 공고는 공고명으로 나눔
        records = [_past(f"i{i}", 100.0 + i * 0.01, work="공사") for i in range(12)]
        for rec in records:
            rec.title = "○○센터 실내 리모델링 공사"
        out = br.recommend(br.History(records), work="공사", title="삼탄아트마인 본관 라운지 조성사업",
                           summary="라운지 조성(실내 리모델링) 공사 1식", licenses=["실내건축공사업"])
        self.assertEqual(out["field"], "실내건축·인테리어")
        self.assertIn("사업 내용", out["field_note"])
        self.assertIn("분야 실내건축·인테리어", out["conditions"])
        plain = br.recommend(br.History(records), work="공사", title="삼탄아트마인 본관 라운지 조성사업")
        self.assertEqual(plain["field"], "기타")
        self.assertNotIn("field_note", plain)

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


class OutlierTest(unittest.TestCase):
    """10/29: 하한율 0.01 같은 값(하한선 없는 입찰)과 변동폭 밖 사정률(입력 오류)은 못 쓴 공고로."""

    def _bidders(self):
        from nego.bidprice import Bidder

        return [Bidder(1, "갑", "1", 88_000_000, None, "")]

    def test_tiny_lower_rate_is_skipped(self):
        self.assertIsNone(br._make("1", "물품", 100_000_000, 100_000_000, 0.01, self._bidders(), []))
        line = {"no": "1", "prices": [{"plnprc": "100000000", "bssamt": "100000000"}],
                "bidders": [{"opengRank": "1", "bidprcAmt": "88000000"}], "notice": {"lower_rate": "0.1"}}
        self.assertIsNone(br.from_result_record(line))
        self.assertEqual(br.skip_reason(line), "하한율 이상값(하한선 없는 입찰)")

    def test_assessment_outside_range_is_skipped(self):
        # ±2% 공고인데 사정률 103% — 변동폭 + 0.5%p 밖
        self.assertIsNone(br._make("1", "공사", 100_000_000, 103_000_000, 87.745, self._bidders(), [], range_width=2))
        self.assertIsNotNone(br._make("1", "공사", 100_000_000, 102_300_000, 87.745, self._bidders(), [], range_width=2))
        line = {"no": "1", "prices": [{"plnprc": "150000000", "bssamt": "100000000"}],
                "bidders": [{"opengRank": "1", "bidprcAmt": "132000000"}], "notice": {"lower_rate": "87.745"}}
        self.assertEqual(br.skip_reason(line), "사정률 이상치(변동폭 밖)")


def _large(no: str, day: str, s: float, competitors: list[float], lower: float = 87.745, basis: float = 100_000_000) -> br.PastBid:
    """대규모 경쟁 공고 — 경쟁업체가 competitors(가정 사정률 %)로 투찰, A=0."""
    planned = basis * s / 100
    amounts = [br.bid_amount(basis, c, lower) for c in competitors]
    above = [a for a in amounts if a >= planned * lower / 100]
    winner = min(above) if above else max(amounts)
    rec = br.PastBid(notice_no=no, work="공사", basis=basis, planned=planned, lower_rate=lower,
                     winner_rate=winner / planned * 100, bidder_count=len(competitors), range_width=3, a_value=0.0,
                     opened_at=day)
    rec.amounts = amounts
    rec.top_amounts = sorted(above)[:br.TOP_N]
    return rec


class CrowdRateTest(unittest.TestCase):
    def _records(self, crowd_at: float, n: int = 60):
        import random

        rnd = random.Random(7)
        out = []
        for i in range(n):
            s = rnd.gauss(100.0, 0.5)
            comp = [rnd.gauss(crowd_at, 0.01) for _ in range(150)] + [rnd.uniform(98.5, 101.5) for _ in range(30)]
            out.append(_large(f"N{i:03d}", f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}", s, comp))
        return out

    def test_rate_reverses_competitor_amounts(self):
        rec = _large("1", "2026-01-01", 100.2, [99.5, 100.4])
        self.assertAlmostEqual(float(br._competitor_rates(rec)[0]), 99.5, places=6)

    def test_avoids_crowded_spot(self):
        # 경쟁업체 대부분이 100.60%에 몰려 있으면 그 바로 아래(아직 덜 붐비는 쪽)를 고른다 — 몰린 곳 위는 늘 그 업체들에 진다
        got = br.crowd_rate(self._records(100.60))
        self.assertIsNotNone(got)
        self.assertLess(got["rate"], 100.60)
        self.assertGreater(got["rate"], 99.5)
        self.assertGreater(got["win_p"], 0)
        self.assertLess(got["win_p"], got["valid_p"])  # 1순위면 늘 유효

    def test_keeps_floor_pass_chance(self):
        # 10/29 R 공고 63건: 제약 없이 경쟁업체가 없는 낮은 끝을 골라 대부분 미달 — 이제 통과 확률 PICK_MIN_VALID 이상에서만
        for at in (99.70, 100.30, 100.60):
            got = br.crowd_rate(self._records(at))
            self.assertGreaterEqual(got["valid_p"], br.PICK_MIN_VALID - 0.01)

    def test_follows_where_actual_rates_fall(self):
        # 몰린 곳을 옮기면 추천도 따라 움직인다(고정값이 아님)
        a = br.crowd_rate(self._records(100.60))["rate"]
        b = br.crowd_rate(self._records(100.30))["rate"]
        self.assertNotAlmostEqual(a, b, places=2)

    def test_few_widely_spread_records(self):
        # 10/29 서버 오류 재현: 공고가 적고 사정률이 넓게 퍼지면 매끄럽게 하는 곡선이 격자보다 길어져 IndexError가 났다
        import random

        rnd = random.Random(2)
        for n, spread in ((10, 2.9), (12, 2.5), (10, 1.5)):  # ±3% 공고처럼 넓게 — 곡선이 격자보다 길어지는 경우
            recs = [_large(f"N{i}", f"2026-01-{1 + i % 28:02d}", 100 + rnd.uniform(-spread, spread),
                           [rnd.gauss(100, 0.5) for _ in range(150)]) for i in range(n)]
            got = br.crowd_rate(recs)
            self.assertIsNotNone(got)
            self.assertLessEqual(got["valid_p"], 1.0)

    def test_too_few_records(self):
        self.assertIsNone(br.crowd_rate(self._records(100.0, n=5)))

    def test_recommend_adds_crowd_option_for_large_competition(self):
        out = br.recommend(br.History(records=self._records(100.30)), work="공사", lo=-3, hi=3, scale="large")
        names = [o["name"] for o in out["options"]]
        self.assertIn(br.CROWD_NAME, names)
        small = br.recommend(br.History(records=[_past(f"S{i}", 100 + i / 100, count=10) for i in range(20)]), scale="small")
        self.assertNotIn(br.CROWD_NAME, [o["name"] for o in small["options"]])

    def test_same_rule_regardless_of_scale(self):
        # 10/29 사용자 결정(2안): 규모와 관계없이 — 1순위가 가려지면 1순위, 아니면 통과했을 때 순위(대규모 추천 줄 유무와 무관)
        track = lambda win, rank, top=0: {"tried": 100, "win": win, "valid": 70, "top": top, "rank": rank}  # noqa: E731
        opts = [{"name": "추천(과거 1순위 최다)", "rate": 100.3, "track": track(3, 0.4, 1)},
                {"name": br.TREND_NAME, "rate": 100.1, "track": track(2, 0.3, 3)},
                {"name": br.CROWD_NAME, "rate": 100.05, "track": track(2, 0.2, 4)}]
        self.assertEqual((br.pick_option(opts)["name"], br.pick_option(opts)["mode"]), (br.CROWD_NAME, "rank"))
        self.assertEqual(br.pick_option(opts[:2])["name"], br.TREND_NAME)  # 대규모 추천 줄이 없어도 같은 규칙
        opts[0]["track"] = track(15, 0.4, 20)  # 1순위 15% — 1순위로 가려짐
        self.assertEqual((br.pick_option(opts)["name"], br.pick_option(opts)["mode"]), ("추천(과거 1순위 최다)", "track"))

class NetCostLineTest(unittest.TestCase):
    """순공사원가 기준선 — 개찰 결과에서 '순공사원가'로 무효된 업체로 정함(비율을 고정하지 않음, 10/29 제보 R25BK00645409)."""

    def _rec(self):
        from nego.bidprice import Bidder

        basis, s, lower = 100_000_000, 100.0, 87.745
        planned = basis * s / 100
        floor = planned * lower / 100  # A=0
        amounts = [floor - 50_000, floor + 100_000, floor + 200_000, floor + 400_000, floor + 500_000]
        bidders = [Bidder(None, "미달", "1", amounts[0], None, "낙찰하한선 미달"),
                   Bidder(None, "가", "2", amounts[1], None, "순공사원가 98%미만"),
                   Bidder(None, "나", "3", amounts[2], None, "순공사원가 98%미만"),
                   Bidder(1, "다", "4", amounts[3], None, ""),
                   Bidder(2, "라", "5", amounts[4], None, "")]
        return br._make("N1", "공사", basis, planned, lower, bidders, [], range_width=3, a_value=0.0), floor

    def test_line_from_remarks(self):
        rec, floor = self._rec()
        self.assertAlmostEqual(rec.net_line, floor + 300_000)
        from nego import bidbacktest as bb

        # 실제 사정률로 낸 금액 = 기준선(하한선이 아니라) — 그보다 낮은 사정률은 미달, 하한선만 넘는 금액은 순위 없음
        s = rec.assessment
        self.assertAlmostEqual(rec.amount_at(s), floor + 300_000)
        self.assertAlmostEqual(rec.rate_for(rec.amount_at(s + 0.1)), s + 0.1)
        self.assertEqual(bb.score(s - 0.01, rec), (False, False))
        self.assertIsNone(bb.rank_of(rec, floor + 150_000))
        # 기준선을 개찰 결과로 가늠했으면 1위 금액이 곧 가늠 구간의 끝 — 통과는 맞지만 1순위는 확정 못 함(보수적)
        self.assertEqual(bb.score(s + 0.01, rec), (True, False))
        self.assertEqual(rec.net_err, 100_000)
        self.assertLess(rec.win_ceiling - s, 0.5)
        self.assertEqual(rec.odd_remarks, Counter())

    def test_estimated_floor_cannot_claim_first(self):
        """A값을 모르는 공고(10/29 R25BK00715689): 가늠한 하한선이 실제보다 낮으면 1위보다 비싼 금액도 1순위로 보였음."""
        from nego import bidbacktest as bb
        from nego.bidprice import Bidder

        basis, planned, lower = 157_000_000, 157_861_575, 87.745
        bidders = [Bidder(None, "미달", "1", 139_866_000, None, "낙찰하한선 미달"),
                   Bidder(1, "1위", "2", 139_869_400, None, ""), Bidder(2, "2위", "3", 139_870_450, None, "")]
        rec = br._make("R25BK00715689", "공사", basis, planned, lower, bidders, [], range_width=2)
        self.assertEqual(rec.floor_source, "추정")
        self.assertGreater(rec.win_ceiling, 100.55)  # 가늠값으로는 100.55%가 1순위처럼 보이지만
        self.assertEqual(bb.score(100.55, rec), (True, False))  # 오차를 감안하면 1순위 아님
        self.assertGreaterEqual(bb.rank_of(rec, bb.amount_for(rec, 100.55)), 2)  # 실제 2위 — 오차 안 업체는 앞으로 세서 보수적

    def test_a_value_checked_against_bidders(self):
        """A값으로 계산한 하한선이 개찰 결과와 어긋나면(미달 업체가 그 위) 참가업체로 가늠한 하한선을 씀(10/29 '낙찰하한선 미달' 8,515곳)."""
        from nego.bidprice import Bidder

        basis, planned, lower = 157_000_000, 157_861_575, 87.745
        bidders = [Bidder(None, "미달", "1", 139_866_000, None, "낙찰하한선 미달"),
                   Bidder(1, "1위", "2", 139_869_400, None, ""), Bidder(2, "2위", "3", 139_870_450, None, "")]
        ok = br._make("N1", "공사", basis, planned, lower, bidders, [], range_width=2, a_value=11_038_000.0)
        self.assertEqual(ok.floor_source, "A값")
        self.assertAlmostEqual(ok.floor, 139_868_345.88375, places=3)
        bad = br._make("N2", "공사", basis, planned, lower, bidders, [], range_width=2, a_value=1_000_000.0)
        self.assertEqual(bad.floor_source, "추정(A값 불일치)")
        self.assertEqual(bad.floor, bad.floor_est)
        self.assertGreater(bad.judge_err, 0)

    def test_target_bid_uses_net_rule(self):
        # 10/29 제보 공고 값: 기초 563,530,000 · 하한율 87.745 · A 25,053,044 · 순공사원가 519,482,258(공고문 98%)
        plain = br.bid_amount(563_530_000, 101.173, 87.745, 25_053_044)
        with_net = br.bid_amount(563_530_000, 101.173, 87.745, 25_053_044, 519_482_258, 98)
        self.assertAlmostEqual(with_net, 519_482_258 * 1.01173 * 0.98, places=2)
        self.assertGreater(with_net, plain)
        self.assertEqual(br.bid_amount(563_530_000, 101.173, 87.745, 25_053_044, 519_482_258, None), plain)

    def test_no_rule_without_remarks(self):
        rec = _large("N2", "2026-01-01", 100.0, [100.1, 100.2])
        self.assertIsNone(rec.net_line)
        self.assertAlmostEqual(rec.amount_at(100.0), rec.floor)


class WindowOkTest(unittest.TestCase):
    def test_wide_window_in_large_competition_not_counted(self):
        # 10/29 R 공고 63건: 하한선이 틀려 1순위 구간이 넓게 잡힌 대규모 공고 몇 건이 겹쳐 '과거 1순위 최다'를 98.595%에 묶어 둠
        good = _large("G", "2026-01-01", 100.0, [100.002] + [99.0] * 120)  # 구간 0.002%p
        bad = _large("B", "2026-01-02", 98.5, [99.5] + [98.0] * 120)  # 구간 약 1%p — 하한선 틀린 공고처럼
        self.assertTrue(good.window_ok)
        self.assertFalse(bad.window_ok)
        wins, valid = br.coverage([good, bad])
        self.assertEqual(wins[br._index(99.0)], 0)  # 넓은 구간은 1순위로 안 셈
        self.assertEqual(valid[br._index(99.0)], 1)  # 유효(실제 사정률)는 그대로 셈
        self.assertEqual(wins[br._index(100.001)], 1)


if __name__ == "__main__":
    unittest.main()
