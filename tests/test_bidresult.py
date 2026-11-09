"""투찰내역 개찰 결과(2026-11-06) — 지일 순위·1순위 업체·실제 사정률 요약과 받기 규칙."""

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from nego.bidprice import Bidder, BidResult
from webapp.bidlog import BidLog
from webapp.bidresult import fill_results, needs_fetch, summarize

KST = timezone(timedelta(hours=9))
OWN = "206-86-36338"


def result(bidders, planned=192_950_000, basis=193_283_304, error=None):
    r = BidResult("R26BK01810177", "000", "태백", "공사", "태백시", None, 89.745, basis_amount=basis, planned_price=planned)
    r.bidders, r.error = bidders, error
    return r


BIDDERS = [Bidder(1, "가나건설", "111-11-11111", 173_200_000, 89.81, ""),
           Bidder(2, "다라전기", "222-22-22222", 173_210_000, 89.82, ""),
           Bidder(3, "(주)지일", "2068636338", 173_500_000, 89.95, ""),
           Bidder(None, "마바", "333-33-33333", 170_000_000, 88.0, "낙찰하한선미달")]


class TestSummarize(unittest.TestCase):
    def test_complete(self):
        s = summarize(result(BIDDERS), {"rate": 100.55}, OWN)
        self.assertEqual(s["status"], "완료")
        self.assertEqual((s["own"]["rank"], s["bidderCount"]), (3, 4))      # 사업자번호는 숫자만 맞춰 찾음
        self.assertEqual(s["winner"]["name"], "가나건설")
        self.assertEqual(s["gapToWinner"], 300_000)
        self.assertAlmostEqual(s["assessmentRate"], 99.8275, places=3)
        self.assertAlmostEqual(s["rateDiff"], 100.55 - 99.8275, places=3)

    def test_not_opened_error_and_no_own(self):
        self.assertEqual(summarize(result([]), {}, OWN)["status"], "개찰 전")
        self.assertEqual(summarize(result([], error="조회 실패"), {}, OWN)["status"], "오류")
        s = summarize(result(BIDDERS[:2]), {}, OWN)
        self.assertIsNone(s["own"])
        self.assertNotIn("rateDiff", s)

    def test_needs_fetch(self):
        now = datetime(2026, 11, 6, 12, tzinfo=KST)
        self.assertTrue(needs_fetch({}, now))
        self.assertFalse(needs_fetch({"result": {"status": "완료", "fetchedAt": now.isoformat()}}, now))
        self.assertFalse(needs_fetch({"result": {"status": "개찰 전", "fetchedAt": (now - timedelta(minutes=30)).isoformat()}}, now))
        self.assertTrue(needs_fetch({"result": {"status": "개찰 전", "fetchedAt": (now - timedelta(hours=2)).isoformat()}}, now))


class TestFill(unittest.TestCase):
    def test_fill_saves_and_skips_done(self):
        log = BidLog(Path(tempfile.mkdtemp()))
        a = log.add({"noticeNo": "R26BK01810177", "bid": 173_500_000, "rate": 100.55, "workType": "공사"})
        b = log.add({"noticeNo": "R26BK01799159", "bid": 450_000_000})  # 업무 모름 — 공사·용역·물품 순으로
        calls = []

        def fake(client, base, notice):
            calls.append((notice.notice_no, notice.work_type))
            if notice.notice_no == "R26BK01799159" and notice.work_type != "용역":
                return result([])
            return result(BIDDERS)

        self.assertEqual(fill_results(log, None, "u", OWN, fetch=fake), {"fetched": 2, "left": 0})
        self.assertIn(("R26BK01799159", "공사"), calls)
        self.assertIn(("R26BK01799159", "용역"), calls)
        rows = {r["id"]: r for r in log.entries()}
        self.assertEqual(rows[a["id"]]["result"]["own"]["rank"], 3)
        calls.clear()
        self.assertEqual(fill_results(log, None, "u", OWN, fetch=fake)["fetched"], 0)   # 완료는 다시 안 받음
        self.assertEqual(fill_results(log, None, "u", OWN, ids=[a["id"]], force=True, fetch=fake)["fetched"], 1)
        self.assertIn("1순위 업체", log.csv())


if __name__ == "__main__":
    unittest.main()
