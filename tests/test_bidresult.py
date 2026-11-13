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

    def test_recorded_amount_differs_is_comparison(self):
        """11/13 사용자 요청: 외주 금액(미달)으로 투찰하고 계산기 추천 금액을 기록 → 비교 기록, 기록 금액이면 몇 등·하한가와 차이."""
        own_under = [Bidder(1, "가나건설", "111-11-11111", 173_200_000, 89.81, ""),
                     Bidder(2, "다라전기", "222-22-22222", 173_210_000, 89.82, ""),
                     Bidder(None, "(주)지일", "2068636338", 172_000_000, 89.0, "낙찰하한선미달"),
                     Bidder(None, "마바", "333-33-33333", 170_000_000, 88.0, "낙찰하한선미달")]
        entry = {"bid": 173_205_000, "lowerRate": 89.745, "aValue": 0}
        s = summarize(result(own_under), entry, OWN)
        sim = s["recorded"]
        floor = 173_162_978  # 192,950,000 × 89.745% = 173,162,977.5 → 올림
        self.assertEqual(sim["floor"], floor)
        self.assertEqual(sim["rank"], 2)                      # 가나건설(173,200,000)만 더 쌈
        self.assertEqual(sim["gapToFloor"], 173_205_000 - floor)
        self.assertEqual(sim["gapToWinner"], 5_000)
        self.assertEqual(sim["ownAmount"], 172_000_000)
        under = summarize(result(own_under), {**entry, "bid": 171_000_000}, OWN)["recorded"]
        self.assertEqual(under["rank"], -1)                   # 하한가 아래 — 지일 실제 줄은 빼고 마바(170,000,000)보다 위라 -1
        self.assertNotIn("recorded", summarize(result(own_under), {**entry, "bid": 172_000_000}, OWN))  # 같은 금액 = 실제 투찰 기록

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

    def test_times_saved_and_filled(self):
        """투찰 마감·개찰 일시(11/10) — 기록할 때 저장, 예전 기록은 '개찰 전'이면 공고에서 찾아 채움."""
        log = BidLog(Path(tempfile.mkdtemp()))
        new = log.add({"noticeNo": "R26BK01810177", "bid": 1, "workType": "공사",
                       "openingAt": "2026-11-10 11:00:00", "closingAt": "2026-11-10 10:00:00"})
        old = log.add({"noticeNo": "R26BK01799159", "bid": 1, "workType": "용역"})
        rows = log._read()
        for r in rows:   # 11/10 전 기록 흉내 — 두 칸이 아예 없음
            if r["id"] == old["id"]:
                r.pop("openingAt"); r.pop("closingAt")
        log._write(rows)
        asked = []

        def look(client, entry):
            asked.append(entry["noticeNo"])
            return {"openingAt": "2026-11-13 11:00:00", "closingAt": "2026-11-13 10:00:00"}

        fill_results(log, None, "u", OWN, fetch=lambda c, b, n: result([]), times=look)
        got = {r["id"]: r for r in log.entries()}
        self.assertEqual(got[new["id"]]["closingAt"], "2026-11-10 10:00:00")   # 이미 있으면 안 찾음
        self.assertEqual((got[old["id"]]["openingAt"], got[old["id"]]["closingAt"]), ("2026-11-13 11:00:00", "2026-11-13 10:00:00"))
        self.assertEqual(asked, ["R26BK01799159"])
        fill_results(log, None, "u", OWN, fetch=lambda c, b, n: result([]), times=look)
        self.assertEqual(asked, ["R26BK01799159"])   # 한 번 찾은 기록은 다시 안 찾음

    def test_times_filled_without_refetch(self):
        """결과를 다시 받을 때(1시간)가 아니어도 '개찰 전'인데 아직 안 찾아본 기록은 바로 채움(오늘 기록 — 개찰만 있음)."""
        log = BidLog(Path(tempfile.mkdtemp()))
        e = log.add({"noticeNo": "R26BK01799159", "bid": 1, "openingAt": "2026-11-13 11:00:00"})
        rows = log._read(); rows[0].pop("closingAt"); log._write(rows)
        log.set_result(e["id"], {"status": "개찰 전", "fetchedAt": datetime.now(KST).isoformat()})
        got = fill_results(log, None, "u", OWN, fetch=lambda c, b, n: result([]),
                           times=lambda c, x: {"openingAt": "", "closingAt": "2026-11-13 10:00:00"})
        self.assertEqual(got["fetched"], 0)
        r = log.entries()[0]
        self.assertEqual((r["openingAt"], r["closingAt"]), ("2026-11-13 11:00:00", "2026-11-13 10:00:00"))  # 빈 값으로 안 덮음
    def test_bidders_saved_apart(self):
        """투찰 업체 전부(11/10) — 기록 파일 밖에 따로 저장, 지일 줄 표시, 지우면 같이 지움."""
        log = BidLog(Path(tempfile.mkdtemp()))
        a = log.add({"noticeNo": "R26BK01810177", "bid": 1, "workType": "공사"})
        self.assertIsNone(log.bidders(a["id"]))
        fill_results(log, None, "u", OWN, fetch=lambda c, b, n: result(BIDDERS))
        res = log.entries()[0]["result"]
        self.assertNotIn("bidders", res)                      # 목록 응답은 가볍게
        self.assertEqual(res["bidderListCount"], 4)
        got = log.bidders(a["id"])
        self.assertEqual([b["name"] for b in got], ["가나건설", "다라전기", "(주)지일", "마바"])
        self.assertEqual([b.get("own", False) for b in got], [False, False, True, False])
        self.assertNotIn("bidders", log.path.read_text(encoding="utf-8"))
        log.delete(a["id"])
        self.assertIsNone(log.bidders(a["id"]))

    def test_own_under_rank(self):
        """지일이 미달이면 미달 업체 중 금액이 큰 순서로 -N등(11/10)."""
        bidders = BIDDERS[:2] + [Bidder(None, "갑", "9", 171_000_000, 88.5, "낙찰하한선미달"),
                                 Bidder(None, "(주)지일", "2068636338", 170_500_000, 88.4, "낙찰하한선미달"),
                                 Bidder(None, "을", "8", 169_000_000, 88.0, "낙찰하한선미달")]
        s = summarize(result(bidders), {}, OWN)
        self.assertEqual((s["own"]["rank"], s["own"]["underRank"]), (None, -2))
        self.assertIsNone(summarize(result(BIDDERS), {}, OWN)["own"]["underRank"])   # 순위 있으면 없음


if __name__ == "__main__":
    unittest.main()


class RealFetchNoticeTest(unittest.TestCase):
    """11/11 서버 오류: 투찰내역 결과 받기가 넘기는 SimpleNamespace엔 contract_method가 없어 fetch_result가 멈췄음."""

    def test_fetch_result_accepts_minimal_notice(self):
        from types import SimpleNamespace

        from nego.bidprice import fetch_result

        class Client:
            def fetch_all_pages(self, *a, **k):
                return []

        notice = SimpleNamespace(notice_no="R26BK00000001", notice_ord="000", title="가상", work_type="공사",
                                 demand_institution="", notice_institution="", opening_at=None, raw={})
        res = fetch_result(Client(), "u", notice)
        self.assertEqual((res.notice_no, res.contract_method), ("R26BK00000001", ""))


class TestEffectiveOwn(unittest.TestCase):
    def test_comparison_record_uses_recorded_amount(self):
        """11/13 사용자 요청: 비교 기록은 등수·투찰률도 웹앱 기록 금액 기준(성적표 등수도)."""
        from webapp.bidnightly import _rank_text
        from webapp.bidresult import effective_own
        res = {"status": "완료", "bidderCount": 445, "own": {"rank": None, "underRank": -369, "amount": 104_106_058},
               "recorded": {"amount": 105_425_350, "rank": -12, "bidRate": 89.96, "gapToWinner": -630_650}}
        own = effective_own(res)
        self.assertEqual((own["rank"], own["underRank"], own["amount"]), (None, -12, 105_425_350))
        self.assertIn("웹앱 기록 금액 기준", _rank_text({"result": res}))
        res["recorded"]["rank"] = 3
        self.assertEqual(_rank_text({"result": res}), "3위 / 445곳 · 웹앱 기록 금액 기준")
        self.assertEqual(effective_own({"own": {"rank": 2}})["rank"], 2)   # 비교 기록이 아니면 실제 투찰
