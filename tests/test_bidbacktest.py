"""추천 사정률 검증(`nego/bidbacktest.py`) — 만든 과거 개찰 자료로 채점 규칙만 확인한다."""

from __future__ import annotations

import csv
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nego import bidbacktest as bt  # noqa: E402
from nego import bidrecommend as br  # noqa: E402


def _past(no: str, day: str, s: float, gap: float = 0.3, lower: float = 87.745, own_rate: float | None = None,
          own_rank: int | None = None, count: int = 30) -> br.PastBid:
    basis = 100_000_000
    rec = br.PastBid(notice_no=no, work="용역", basis=basis, planned=basis * s / 100, lower_rate=lower,
                     winner_rate=lower + gap, bidder_count=count, range_width=3, a_value=0.0, opened_at=f"{day} 11:00")
    if own_rate is not None:
        rec.own_amount = br.bid_amount(basis, own_rate, lower)
        rec.own_rank = own_rank
    return rec


class ScoreTest(unittest.TestCase):
    def test_valid_and_win(self):
        rec = _past("1", "2025-01-01", 100.0, gap=0.255)  # 1순위 상한 ≈ 100.2906
        self.assertEqual(bt.score(99.99, rec), (False, False))
        self.assertEqual(bt.score(100.0, rec), (True, True))
        self.assertEqual(bt.score(100.29, rec), (True, True))
        self.assertEqual(bt.score(100.30, rec), (True, False))

    def test_own_rate_reverses_bid_amount(self):
        rec = _past("1", "2025-01-01", 100.0, own_rate=99.75, own_rank=None)
        self.assertAlmostEqual(bt.own_rate(rec), 99.75)
        rec.a_value = 5_000_000
        rec.own_amount = br.bid_amount(rec.basis, 100.2, rec.lower_rate, 5_000_000)
        self.assertAlmostEqual(bt.own_rate(rec), 100.2)

    def test_date_of(self):
        self.assertEqual(bt.date_of(_past("1", "2025-3-7", 100)), "2025-03-07")
        rec = _past("1", "x", 100)
        rec.opened_at = "20250307110000"
        self.assertEqual(bt.date_of(rec), "2025-03-07")
        rec.opened_at = ""
        self.assertIsNone(bt.date_of(rec))
        rec.notice_no = "20201217355"  # 옛 공고번호 = 공고일
        self.assertEqual(bt.date_of(rec), "2020-12-17")


class BacktestTest(unittest.TestCase):
    def setUp(self):
        # 2024년 12건(사정률 100.0~100.2 — 추천이 100.2 근처로 모임) + 2025년 채점 2건
        self.train = [_past(f"t{i}", f"2024-0{1 + i % 9}-10", 100.0 + (i % 3) * 0.1) for i in range(12)]
        self.hit = _past("hit", "2025-02-01", 100.1, own_rate=99.7, own_rank=None)  # 외주는 낮게 잡아 하한 미달
        self.miss = _past("miss", "2025-02-02", 101.5)  # 추천보다 사정률이 높아 무효

    def test_uses_only_earlier_notices(self):
        rows, undated = bt.backtest(self.train + [self.hit, self.miss], start="2025-01-01")
        self.assertEqual(undated, 0)
        self.assertEqual([r["공고번호"] for r in rows], ["hit", "miss"])
        self.assertEqual(rows[0]["학습 공고"], 12)
        self.assertEqual(rows[1]["학습 공고"], 13, "앞날 공고(hit)까지만 — 자기 자신은 안 씀")
        rate, valid, win, _top = rows[0]["results"]["추천(과거 1순위 최다)"]
        self.assertTrue(valid and win, rate)
        self.assertEqual(rows[0]["results"][bt.OWN][1:], (False, False, False))
        self.assertFalse(rows[1]["results"]["추천(과거 1순위 최다)"][1])

    def test_end_date_limits_scoring(self):
        rows, _ = bt.backtest(self.train + [self.hit, self.miss], start="2025-01-01", end="2025-02-01")
        self.assertEqual([r["공고번호"] for r in rows], ["hit"])

    def test_same_day_notices_are_not_training(self):
        same = _past("same", "2025-02-01", 100.1)
        rows, _ = bt.backtest(self.train + [self.hit, same], start="2025-01-01")
        self.assertTrue(all(r["학습 공고"] == 12 for r in rows))

    def test_too_few_similar_means_no_recommendation(self):
        rows, _ = bt.backtest(self.train[:3] + [self.hit], start="2025-01-01", fixed=(100.55,))
        self.assertNotIn("추천(과거 1순위 최다)", rows[0]["results"])
        self.assertIn("고정 100.55%", rows[0]["results"])
        self.assertIn("추천 없음", bt.summarize(rows))

    def test_large_competition_gets_crowd_strategy(self):
        import random

        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from test_bidrecommend import _large

        rnd = random.Random(5)
        recs = [_large(f"L{i:03d}", f"2025-{1 + i // 28:02d}-{1 + i % 28:02d}", rnd.gauss(100, 0.5),
                       [rnd.gauss(100.2, 0.05) for _ in range(120)]) for i in range(40)]
        rows, _ = bt.backtest(recs, start="2025-02-01")
        self.assertTrue(rows and all(br.CROWD_NAME in r["results"] for r in rows))
        self.assertIn("변동폭 ±3% · 2025년", bt.summarize(rows))
        self.assertNotIn(br.CROWD_NAME, bt.backtest(self.train + [self.hit], start="2025-01-01")[0][0]["results"])  # 소규모엔 없음

    def test_baseline_line_and_error_column(self):
        rows, _ = bt.backtest(self.train + [self.hit], start="2025-01-01")
        self.assertEqual(rows[0]["results"][bt.BASELINE][0], 100.0)
        text = bt.summarize(rows)
        self.assertIn(bt.BASELINE, text)
        self.assertIn("오차", text)

    def test_tune_half_life(self):
        import random

        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from test_bidrecommend import _large

        rnd = random.Random(9)
        recs = [_large(f"L{i:03d}", f"2025-{1 + i // 28:02d}-{1 + i % 28:02d}", rnd.gauss(100, 0.5),
                       [rnd.gauss(100.2, 0.05) for _ in range(120)]) for i in range(40)]
        result = bt.tune_crowd(recs, start="2025-02-01")
        self.assertEqual(set(result), set(bt.HALF_LIVES))
        self.assertTrue(all(t.tried for t in result.values()))
        self.assertEqual(len({t.tried for t in result.values()}), 1)  # 같은 공고로 비교
        self.assertIn("무게 없음", bt.tune_summary(result))

    def test_rank_share_counts_fail_as_last(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from test_bidrecommend import _large

        rec = _large("R1", "2025-03-01", 100.0, [100.1, 100.2, 100.3, 99.5])
        self.assertEqual(bt.rank_share(99.9, rec), 1.0)  # 미달 = 꼴찌
        self.assertAlmostEqual(bt.rank_share(100.0, rec), 1 / 4)
        self.assertAlmostEqual(bt.rank_share(100.25, rec), 3 / 4)

    def test_pick_simulation_lines(self):
        import random

        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from test_bidrecommend import _large

        rnd = random.Random(4)
        recs = [_large(f"L{i:03d}", f"2025-{1 + i // 28:02d}-{1 + i % 28:02d}", rnd.gauss(100, 0.5),
                       [rnd.gauss(100.2, 0.3) for _ in range(120)]) for i in range(80)]
        rows, _ = bt.backtest(recs, fixed=(100.55,))
        bt.add_picks(rows, recs)
        late = [r for r in rows if r["개찰일"] >= "2025-03-01"]
        self.assertTrue(late and all(bt.PICK in r["results"] and bt.LEGACY in r["results"] for r in late))
        self.assertTrue(all(r["results"][bt.PICK] == r["results"][r["picked"][bt.PICK]] for r in late))
        self.assertFalse(any(r["picked"][label] in (bt.BASELINE, bt.OWN) for r in late for label in (bt.PICK, bt.LEGACY)))
        text = bt.summarize(late)
        self.assertIn(bt.PICK, text)
        self.assertIn("두 규칙이 다른 방식을 고른 공고", text)
        self.assertIn("미달=꼴찌", text)

    def test_summary_and_csv(self):
        rows, _ = bt.backtest(self.train + [self.hit, self.miss], start="2025-01-01")
        text = bt.summarize(rows)
        self.assertIn("채점 공고 2건", text)
        self.assertIn("지일이 실제로 투찰한 공고", text)
        self.assertIn("2025년", text)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "out.csv"
            bt.write_csv(rows, path)
            table = list(csv.reader(path.open(encoding="utf-8-sig")))
        self.assertEqual(table[0][0], "공고번호")
        self.assertIn(f"{bt.OWN} 1순위", table[0])
        self.assertEqual(len(table), 3)


class ResultRecordDateTest(unittest.TestCase):
    """fetch_bid_results 줄의 개찰일 — 참가업체 줄에 opengDt가 없으면 예비가격 줄의 다른 날짜 칸, 못 쓴 줄은 이유를 센다."""

    def _line(self, **extra):
        line = {"no": "R25BK00000001", "work": "용역",
                "prices": [{"bsisPlnprc": str(100_000_000 + i * 200_000), "drwtYn": "Y" if i < 4 else "N",
                            "plnprc": "100300000", "bssamt": "100000000", "rlOpengDt": "2025-03-04 11:00:00"}
                           for i in range(-7, 8)],
                "bidders": [{"opengRank": "1", "prcbdrNm": "갑", "prcbdrBizno": "1", "bidprcAmt": "88200000"}],
                "notice": {"lower_rate": "87.745"}}
        line.update(extra)
        return line

    def test_date_from_price_rows(self):
        rec = br.from_result_record(self._line())
        self.assertEqual(bt.date_of(rec), "2025-03-04")
        rec = br.from_result_record(self._line(notice={"lower_rate": "87.745", "opened_at": "2025-03-05 10:00"}))
        self.assertEqual(bt.date_of(rec), "2025-03-05")

    def test_skip_reasons(self):
        lines = [self._line(notice={}), {"input": "x", "error": "없음"}, self._line(bidders=[])]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bid_results.jsonl"
            path.write_text("\n".join(json.dumps(x) for x in lines), encoding="utf-8")
            history = br.load_history([path])
        self.assertEqual(dict(history.skip_reasons), {"하한율 없음": 1, "개찰 결과 없음(조회 오류)": 1, "참가업체 없음": 1})


class MainTest(unittest.TestCase):
    def test_reads_history_and_finds_own_bid(self):
        items = []
        for i in range(12):
            s = 100.0 + (i % 3) * 0.1
            planned = 100_000_000 * s / 100
            items.append({"notice_no": f"R{i:02d}", "work_type": "용역", "basis_amount": 100_000_000, "planned_price": planned,
                          "lower_limit_rate": 87.745, "range_lo": -3, "range_hi": 3, "a_value": 0,
                          "opened_at": f"2025-01-{10 + i:02d} 11:00",
                          "bidders": [{"rank": 1, "name": "갑", "bizno": "111", "amount": planned * 0.8805, "rate": 88.05,
                                       "remark": ""},
                                      {"rank": None, "name": "지일", "bizno": "206-86-36338", "amount": planned * 0.85,
                                       "rate": 85.0, "remark": "하한미달"}]})
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "bid_history_20250101_20250131.json").write_text(json.dumps(items), encoding="utf-8")
            out = io.StringIO()
            with redirect_stdout(out):
                code = bt.main(["--dir", str(d), "--from", "2025-01-20"])
            self.assertEqual(code, 0)
            self.assertTrue(list(d.glob("bid_backtest_*.csv")))
            summary = next(d.glob("bid_backtest_*.txt")).read_text(encoding="utf-8")
            self.assertIn("과거 개찰 12건", summary)
        text = out.getvalue()
        self.assertIn("과거 개찰 12건", text)
        self.assertIn(bt.OWN, text)

    def test_no_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(bt.main(["--dir", tmp]), 1)


if __name__ == "__main__":
    unittest.main()
