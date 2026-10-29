"""투찰 경향(`nego/bidtrend.py`)·분야(`nego/bidfield.py`) — 만든 과거 개찰 자료로 1~5위·지일 사정률 차와 분야 나누기를 확인한다."""

from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nego import bidbacktest as bt  # noqa: E402
from nego import bidfield  # noqa: E402
from nego import bidrecommend as br  # noqa: E402
from nego import bidtrend  # noqa: E402

BASIS, LOWER = 100_000_000, 87.745


def _past(no: str, s: float, title: str, steps=(0.01, 0.02, 0.03, 0.04, 0.05), own: float | None = None,
          own_rank: int | None = None, day: str = "2025-01-01") -> br.PastBid:
    """사정률 s에 열린 공고 — k위 업체는 실제 사정률 + steps[k-1]%p로 가정한 금액(A=0)."""
    tops = [br.bid_amount(BASIS, s + d, LOWER) for d in steps]
    rec = br.PastBid(notice_no=no, work="용역", basis=BASIS, planned=BASIS * s / 100, lower_rate=LOWER,
                     winner_rate=tops[0] / (BASIS * s / 100) * 100, bidder_count=30, range_width=3, a_value=0.0,
                     title=title, opened_at=day, top_amounts=tops)
    if own is not None:
        rec.own_amount, rec.own_rank = br.bid_amount(BASIS, s + own, LOWER), own_rank
    return rec


class FieldTest(unittest.TestCase):
    def test_classify_by_title_keyword(self):
        self.assertEqual(bidfield.classify("○○박물관 전시물 제작 설치"), "전시·체험")
        self.assertEqual(bidfield.classify("어린이 놀이 터 조성"), "놀이·조경")  # 띄어쓰기 무시
        self.assertEqual(bidfield.classify("○○로 차선도색 공사"), "도로·안전시설")
        self.assertEqual(bidfield.classify("청사 청소 용역"), bidfield.OTHER)
        self.assertEqual(bidfield.classify(""), bidfield.NO_TITLE)

    def test_exceptions_and_trade_mark(self):
        # 수집 로직의 제외 키워드 예외와 같은 방식 — '안전시설' 속 '전시', '추가 구축' 속 '가구'는 안 침
        self.assertEqual(bidfield.classify("국도38호선 도로안전시설물 보수공사"), "도로·안전시설")
        self.assertEqual(bidfield.classify("통합홈페이지 신규사이트 추가 구축"), bidfield.OTHER)
        self.assertEqual(bidfield.classify("○○박물관 안전시설 및 전시물 교체"), "전시·체험")  # 다른 곳에 '전시'가 또 있으면 맞음
        self.assertEqual(bidfield.classify("문화체육공원 조성사업(정보통신)"), "통신·전기")  # 공종 표시가 먼저
        self.assertEqual(bidfield.classify("○○초 교사동 천장교체공사"), "실내건축·천장바닥")
        self.assertEqual(bidfield.classify("○○초 교사동 환경개선공사"), "실내건축·학교")
        self.assertEqual(bidfield.classify("○○센터 인테리어 공사"), "실내건축·인테리어")
        self.assertEqual(bidfield.classify("○○초 교사동 창호교체공사"), "창호·보수")

    def test_similar_prefers_same_field_then_relaxes(self):
        recs = [_past(f"e{i}", 100, "전시관 조성") for i in range(10)] + [_past(f"r{i}", 100, "도로 정비") for i in range(10)]
        picked, labels = br.similar(recs, "용역", 3, "all", "전시·체험")
        self.assertEqual({r.field for r in picked}, {"전시·체험"})
        self.assertIn("분야 전시·체험", labels)
        picked, labels = br.similar(recs[:3], "용역", 3, "all", "전시·체험")  # 3건뿐 → 분야까지 풂
        self.assertEqual(len(picked), 3)
        self.assertNotIn("분야 전시·체험", labels)


class TrendTest(unittest.TestCase):
    def test_rank_gaps_and_own(self):
        recs = [_past(f"N{i}", 99.5 + i * 0.1, "전시관", own=-0.2 if i % 2 else 0.3, own_rank=None if i % 2 else 40)
                for i in range(6)]
        t = bidtrend.trend(recs)
        self.assertEqual([round(r["gap"]["p50"], 4) for r in t["ranks"]], [0.01, 0.02, 0.03, 0.04, 0.05])
        own = t["own"]
        self.assertEqual((own["tried"], own["valid"], own["top"], own["win"]), (6, 3, 0, 0))
        self.assertAlmostEqual(own["vs_first"]["p25"], -0.21, places=4)

    def test_rank_ceiling_and_top5_score(self):
        rec = _past("1", 100.0, "전시관")
        self.assertAlmostEqual(rec.rank_ceiling(5), 100.05, places=6)
        self.assertTrue(bt.in_top(100.04, rec))
        self.assertFalse(bt.in_top(100.06, rec))
        self.assertFalse(bt.in_top(99.9, rec))  # 하한 미달
        rec.top_amounts = rec.top_amounts[:2]  # 순위 있는 업체가 2곳뿐 → 유효면 5위 안
        self.assertTrue(bt.in_top(101.0, rec))

    def test_summary_and_main(self):
        recs = [_past(f"N{i}", 100, "전시관 조성", own=0.1, own_rank=3) for i in range(6)]
        text = bidtrend.summarize(recs)
        self.assertIn("[전체] 공고 6건", text)
        self.assertIn("[전시·체험] 공고 6건", text)
        self.assertIn("지일", text)
        items = [{"notice_no": f"R{i:02d}", "work_type": "용역", "title": "전시관 조성", "basis_amount": BASIS,
                  "planned_price": BASIS, "lower_limit_rate": LOWER, "range_lo": -3, "range_hi": 3, "a_value": 0,
                  "opened_at": f"2025-01-{10 + i:02d}",
                  "bidders": [{"rank": k, "name": f"업체{k}", "bizno": str(k), "amount": BASIS * (0.8775 + k / 10000),
                               "rate": None, "remark": ""} for k in range(1, 8)]}
                 for i in range(6)]
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "bid_history_x.json").write_text(json.dumps(items), encoding="utf-8")
            with redirect_stdout(io.StringIO()) as out:
                self.assertEqual(bidtrend.main(["--dir", str(d)]), 0)
            self.assertIn("[전시·체험] 공고 6건", out.getvalue())
            self.assertTrue(list(d.glob("bid_trend_*.csv")))
            self.assertNotIn("업체1", next(d.glob("bid_trend_*.txt")).read_text(encoding="utf-8"))


class RecommendFieldTest(unittest.TestCase):
    def test_recommend_reports_field_trend_and_top5_option(self):
        recs = [_past(f"N{i:02d}", 100.0 + (i % 3) * 0.1, "박물관 전시 개선", own=-0.3,
                      day=f"2025-{1 + i // 12:02d}-{1 + i % 12:02d}") for i in range(24)]
        out = br.recommend(br.History(recs), work="용역", lo=-3, hi=3, title="○○과학관 전시물 제작")
        self.assertEqual(out["field"], "전시·체험")
        self.assertIn("분야 전시·체험", out["conditions"])
        self.assertEqual(out["trend"]["n"], 24)
        names = [o["name"] for o in out["options"]]
        self.assertIn("추천(과거 5위 안 최다)", names)
        top = next(o for o in out["options"] if o["name"] == "추천(과거 5위 안 최다)")["track"]
        self.assertGreater(top["top"], 0)


if __name__ == "__main__":
    unittest.main()
