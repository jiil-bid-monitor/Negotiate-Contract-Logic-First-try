import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from openpyxl import load_workbook

from webapp.bidlog import BidLog
from webapp.bidnightly import KST, LIVE_FILE, Nightly, watch_items_from_run

OWN = "206-86-36338"


def run_with(*cands):
    return {"candidates": list(cands), "rejected": []}


def cand(no, category="입찰", opening="2026-11-03T11:00:00", kind="본공고"):
    return {"notice_no": no, "notice_ord": "000", "category": category, "kind": kind, "work_type": "공사", "title": f"공고 {no}",
            "demand_institution": "강원도", "deadlines": [["입찰 마감", "2026-11-03T10:00:00"], ["개찰", opening]]}


def record(no, own=True):
    bidders = [{"opengRank": "1", "prcbdrNm": "1등건설", "prcbdrBizno": "111", "bidprcAmt": "88000000", "bidprcrt": "88.1"}]
    if own:
        bidders.append({"opengRank": "3", "prcbdrNm": "(주)지일", "prcbdrBizno": "2068636338", "bidprcAmt": "88100000", "bidprcrt": "88.2"})
    prices = [{"plnprc": "99900000", "bssamt": "100000000", "bsisPlnprc": "99800000", "drwtYn": "Y", "compnoRsrvtnPrceSno": "1"}]
    return {"input": f"{no}-000", "no": no, "ord": "000", "work": "공사", "fetched_at": "2026-11-06T22:00:00",
            "prices": prices, "bidders": bidders, "notice": {"title": f"공고 {no}", "lower_rate": 87.745, "opened_at": "2026-11-03 11:00"}}


class NightlyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.log = BidLog(self.tmp / "data")
        self.run = run_with(cand("R26BK00000001"), cand("R26BK00000002", opening="2026-12-01T11:00:00"),
                            cand("R26BK00000003", category="협상"))
        self.calls = []

        def fetch(client, base, no_input, work):
            self.calls.append(no_input)
            no = no_input.split("-")[0]
            return record(no) if no != "R26BK00000009" else {"input": no_input, "error": "나라장터에서 개찰결과를 찾지 못함"}

        self.night = Nightly(self.tmp / "data", self.log, lambda: self.run, lambda: (object(), "http://x"), OWN,
                             history_dir=self.tmp / "out", archive_dir=self.tmp / "arch", fetch_one=fetch,
                             fill_results=lambda *a, **k: {"fetched": 0, "left": 0})

    def test_watch_only_bid_notices(self):
        nos = [w["no"] for w in watch_items_from_run(self.run)]
        self.assertEqual(nos, ["R26BK00000001", "R26BK00000002"])
        self.assertEqual(watch_items_from_run(self.run)[0]["opening"], "2026-11-03T11:00:00")

    def test_run_collects_opened_only_and_writes_files(self):
        now = datetime(2026, 11, 9, 22, 0, tzinfo=KST)  # 월요일 → 성적표
        out = self.night.run_once(now)
        self.assertEqual(self.calls, ["R26BK00000001-000"])  # 개찰 전(12/01)·협상은 안 받음
        self.assertEqual(out["fetched"], 1)
        lines = (self.tmp / "out" / LIVE_FILE).read_text(encoding="utf-8").splitlines()
        self.assertEqual(json.loads(lines[0])["source"], "후보")
        wb = load_workbook(self.tmp / "arch" / "투찰이력.xlsx")
        self.assertEqual(wb.sheetnames, ["투찰내역", "개찰결과", "대기 중"])
        row = [c.value for c in wb["개찰결과"][2]]
        self.assertIn("1등건설", row)
        self.assertIn(3, row)  # 지일 순위
        self.assertTrue((self.tmp / "arch" / "성적표" / "성적표_2026-11-09.txt").exists())
        # 같은 날 다시는 안 돌고, 받은 공고는 다시 안 받음
        self.assertFalse(self.night.due(now))
        self.night.run_once(datetime(2026, 11, 10, 22, 0, tzinfo=KST))
        self.assertEqual(self.calls, ["R26BK00000001-000"])

    def test_gives_up_after_days(self):
        self.run = run_with(cand("R26BK00000009"))
        self.night.run_once(datetime(2026, 11, 6, 22, 0, tzinfo=KST))
        self.assertEqual(self.night.state()["watch"]["R26BK00000009-000"]["status"], "대기")
        self.night.run_once(datetime(2026, 11, 30, 22, 0, tzinfo=KST))
        self.assertEqual(self.night.state()["watch"]["R26BK00000009-000"]["status"], "결과 없음")

    def test_due(self):
        self.assertFalse(self.night.due(datetime(2026, 11, 6, 21, 59, tzinfo=KST)))  # 처음엔 22시까지 기다림
        self.assertTrue(self.night.due(datetime(2026, 11, 6, 22, 0, tzinfo=KST)))
        self.night.run_once(datetime(2026, 11, 6, 22, 0, tzinfo=KST))
        self.assertFalse(self.night.due(datetime(2026, 11, 7, 9, 0, tzinfo=KST)))
        self.assertTrue(self.night.due(datetime(2026, 11, 8, 9, 0, tzinfo=KST)))  # 11/07 밤을 놓침 → 따라잡기

    def test_bidlog_notices_watched(self):
        self.run = run_with()
        self.log.add({"noticeNo": "R26BK00000005", "bid": 100000000, "recordedAt": "x"})
        self.night.run_once(datetime(2026, 11, 20, 22, 0, tzinfo=KST))
        self.assertEqual(self.calls, ["R26BK00000005-000"])
        self.assertEqual(json.loads((self.tmp / "out" / LIVE_FILE).read_text(encoding="utf-8"))["source"], "투찰")


if __name__ == "__main__":
    unittest.main()
