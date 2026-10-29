"""논문 차용 세 방식(`nego/bidmodels.py`) 테스트 — 만든 과거 개찰 자료로 계산만 확인한다."""

from __future__ import annotations

import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nego import bidbacktest as bt  # noqa: E402
from nego import bidmodels as bm  # noqa: E402
from nego import bidrecommend as br  # noqa: E402


def _rec(no: str, day: str, s: float, gap: float = 0.3, *, field_title: str = "전시관 전시물 제작", work: str = "물품",
         width: int = 3, lower: float = 87.745, method: str = "행자부", inst: str = "강원특별자치도 정선군",
         count: int = 8, basis: float = 100_000_000, draws=None) -> br.PastBid:
    """사정률 s(%)에 열린 공고 — 1순위 구간 폭 gap(%p), A=0."""
    planned = basis * s / 100
    winner_rate = lower * (s + gap) / s  # 1순위 투찰률 → 1순위 상한 사정률이 s + gap
    rec = br.PastBid(notice_no=no, work=work, basis=basis, planned=planned, lower_rate=lower, winner_rate=winner_rate,
                     bidder_count=count, range_width=width, a_value=0.0, title=field_title, opened_at=day,
                     method=method, institution=inst)
    rec.draws = draws or []
    return rec


def _draws(rnd, favored=(1, 2, 3, 4), width=3):
    """번호 1~15 예비가격(번호가 클수록 비싸게) + 선택 횟수(favored 번호를 많이 고름)."""
    rates = sorted(rnd.uniform(100 - width, 100 + width) for _ in range(15))
    return [(i + 1, rates[i], (40 if i + 1 in favored else 5) + rnd.randint(0, 3)) for i in range(15)]


def _day(i: int) -> str:
    return f"2025-{1 + (i // 28) % 12:02d}-{1 + i % 28:02d}"


class BestWindowTest(unittest.TestCase):
    def test_window_inside_gap(self):
        # 실제 사정률이 모두 100.0, 1순위 구간 폭 0.5 → 100.0 ≤ r < 100.5 어디든 1순위, 가운데를 고름
        got = bm.best_window([100.0] * 20, None, [0.5], None)
        self.assertAlmostEqual(got[0], 100.25, delta=0.01)
        self.assertAlmostEqual(got[1], 1.0)

    def test_weights_pull_choice(self):
        # 무게가 큰 쪽(101.0) 근처를 고른다
        got = bm.best_window([99.0, 101.0], [0.1, 0.9], [0.2], None)
        self.assertTrue(101.0 <= got[0] < 101.2)


class DrawModelTest(unittest.TestCase):
    def test_pick_shares_and_simulation_follow_favored_numbers(self):
        rnd = random.Random(1)
        low = [_rec(f"A{i}", _day(i), 99.0, draws=_draws(rnd, favored=(1, 2, 3, 4))) for i in range(30)]
        high = [_rec(f"B{i}", _day(i), 101.0, draws=_draws(rnd, favored=(12, 13, 14, 15))) for i in range(30)]
        shares = bm.pick_shares(low)
        self.assertGreater(shares[0], shares[10])
        # 싼 번호(1~4)를 많이 고르는 시장 → 모의 추첨 사정률이 낮게, 비싼 번호를 고르는 시장 → 높게
        self.assertLess(bm.simulate_assessments(low).mean(), 99.0)
        self.assertGreater(bm.simulate_assessments(high).mean(), 101.0)
        got = bm.draw_rate(low)
        self.assertIsNotNone(got)
        self.assertLess(got["rate"], 100.0)

    def test_without_draw_counts(self):
        self.assertIsNone(bm.draw_rate([_rec(f"A{i}", _day(i), 100.0) for i in range(30)]))

    def test_parse_draws_from_rows(self):
        rows = [{"compnoRsrvtnPrceSno": str(i), "bsisPlnprc": str(100_000_000 + i * 100_000), "drwtNum": str(i)} for i in range(1, 16)]
        rows.append({"compnoRsrvtnPrceSno": "3", "bsisPlnprc": "100300000", "drwtNum": "9"})  # 재입찰 줄 — 마지막 값
        got = br.parse_draws(rows, 100_000_000)
        self.assertEqual(len(got), 15)
        self.assertEqual((got[2][0], got[2][2]), (3, 9))
        self.assertAlmostEqual(got[2][1], 100.3)


class KnnTest(unittest.TestCase):
    def test_similar_notices_weigh_more(self):
        # 같은 분야·업무·방식 공고는 사정률 101 근처, 다른 공고는 99 근처 → 닮은 쪽을 따라감
        same = [_rec(f"S{i}", _day(i), 101.0 + (i % 5) * 0.01) for i in range(40)]
        other = [_rec(f"O{i}", _day(i), 99.0 + (i % 5) * 0.01, field_title="도로 안전시설 공사", work="공사",
                      method="조달청", inst="부산광역시", width=2) for i in range(40)]
        target = {"field": same[0].field, "work": "물품", "width": 3, "lower_rate": 87.745, "method": "행자부",
                  "region": "강원특별자치도", "basis": 100_000_000}
        got = bm.knn_rate(same + other, target)
        self.assertGreater(got["rate"], 100.5)
        weights = dict((r.notice_no, w) for r, w in bm.knn_weights(same + other, target, "2025-12-31"))
        self.assertGreater(weights["S0"], weights["O0"] * 10)


class GapModelTest(unittest.TestCase):
    def test_learns_gap_by_bidder_count(self):
        rnd = random.Random(3)
        recs = []
        for i in range(160):
            count = rnd.choice([3, 5, 400, 600])
            gap = 2.0 if count < 100 else 0.005
            recs.append(_rec(f"G{i}", _day(i), rnd.gauss(100, 0.5), gap=gap * rnd.uniform(0.8, 1.2), count=count))
        model = bm.gap_model(recs)
        self.assertIsNotNone(model)
        small = bm.predict_gap(model, {**bm._as_target(recs[0]), "bidders": 4})
        large = bm.predict_gap(model, {**bm._as_target(recs[0]), "bidders": 500})
        self.assertGreater(small, 0.5)
        self.assertLess(large, 0.05)
        got = bm.gap_rate(recs[:60], model, {**bm._as_target(recs[0]), "bidders": 4})
        self.assertIsNotNone(got)

    def test_many_regions(self):
        """지역이 255개를 넘어도 학습됨(10/29 서버 PC 오류 'cardinality <= 255') — 드문 값은 '기타'로."""
        recs = [_rec(f"G{i}", _day(i % 300), 100.0, inst=f"지역{i} 군청") for i in range(400)]
        model = bm.gap_model(recs)
        self.assertIsNotNone(model)
        self.assertIsNotNone(bm.predict_gap(model, {**bm._as_target(recs[0]), "region": "처음 보는 곳"}))

    def test_estimate_bidders_by_institution(self):
        """같은 분야·업무라도 발주기관이 소규모 공고를 내던 곳이면 소규모로 가늠(10/29 — 참가 18~19곳 공사가 대규모로 잡히던 것)."""
        recs = [_rec(f"B{i}", _day(i), 100.0, work="공사", count=400, inst="강원특별자치도 원주시") for i in range(60)]
        recs += [_rec(f"S{i}", _day(i), 100.0, work="공사", count=19, inst="강원특별자치도 정선군") for i in range(20)]
        target = {**bm._as_target(recs[-1]), "institution": "강원특별자치도 정선군"}
        self.assertLess(bm.estimate_bidders(recs, target), 100)
        other = {**bm._as_target(recs[0]), "institution": "강원특별자치도 원주시"}
        self.assertGreater(bm.estimate_bidders(recs, other), 100)
        scale, note = br.auto_scale(recs, "공사", 3, None, 87.745, 100_000_000, "강원특별자치도 정선군", "행자부")
        self.assertEqual(scale, "small")
        self.assertIn("가늠한 참가", note)

    def test_too_few_records(self):
        self.assertIsNone(bm.gap_model([_rec(f"G{i}", _day(i), 100.0) for i in range(10)]))


class WiringTest(unittest.TestCase):
    def test_backtest_and_recommend_include_model_rows(self):
        rnd = random.Random(5)
        recs = [_rec(f"W{i:03d}", _day(i), rnd.gauss(100, 0.6), gap=rnd.uniform(0.2, 1.5), draws=_draws(rnd))
                for i in range(120)]
        rows, _ = bt.backtest(recs, start="2025-04-01")
        names = set().union(*(r["results"] for r in rows))
        for name in bm.MODEL_NAMES:
            self.assertIn(name, names)
        out = br.recommend(br.History(records=recs), work="물품", lo=-3, hi=3, scale="small", basis=100_000_000,
                           lower_rate=87.745, title="전시관 전시물 제작", institution="강원특별자치도 정선군", method="행자부")
        self.assertTrue(set(bm.MODEL_NAMES) <= {o["name"] for o in out["options"]})


if __name__ == "__main__":
    unittest.main()
