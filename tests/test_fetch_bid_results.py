"""scripts/fetch_bid_results.py — 공고번호 나누기·이어 받기·차수 거르기(나라장터 호출은 가짜 클라이언트)."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

import fetch_bid_results as fb  # noqa: E402
from nego import fields as F  # noqa: E402


class FakeClient:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def fetch_all_pages(self, base_url, operation, params, label):
        self.calls.append(operation)
        return self.responses.get(operation, [])


class FetchBidResultsTest(unittest.TestCase):
    def test_split_notice_no(self):
        self.assertEqual(fb.split_notice_no("R26BK01758791-000"), ("R26BK01758791", "000"))
        self.assertEqual(fb.split_notice_no("20201217355-00"), ("20201217355", "00"))
        self.assertEqual(fb.split_notice_no("R26BK01758791"), ("R26BK01758791", ""))

    def test_fetch_one_tries_works_and_filters_ord(self):
        price_rows = [{"bidNtceOrd": "00", "bsisPlnprc": "100"}, {"bidNtceOrd": "01", "bsisPlnprc": "200"}]
        client = FakeClient({F.SCSBID_OPERATIONS["예비가격/용역"]: price_rows,
                             F.OPENING_BIDDERS_OPERATION: [{"bidNtceOrd": "00", "opengRank": "1"}]})
        rec = fb.fetch_one(client, "u", "20201217355-00", "")
        self.assertEqual(rec["work"], "용역")  # 공사에서 못 찾고 용역에서 찾음
        self.assertEqual([r["bsisPlnprc"] for r in rec["prices"]], ["100"])
        self.assertEqual(len(rec["bidders"]), 1)
        self.assertNotIn("error", rec)

    def test_notice_info_for_recommend(self):
        # 추천 사정률용 — 낙찰하한율(입찰공고)·변동폭·A값(기초금액)을 함께 저장(2026-10-28)
        client = FakeClient({
            F.SCSBID_OPERATIONS["예비가격/용역"]: [{"bidNtceOrd": "000", "bsisPlnprc": "100"}],
            F.OPENING_BIDDERS_OPERATION: [{"bidNtceOrd": "000", "opengRank": "1"}],
            F.BID_NOTICE_OPERATIONS["용역"]: [{"bidNtceNo": "R26BK00000001", "bidNtceOrd": "000", "bidNtceNm": "가",
                                               "sucsfbidLwltRate": "87.745"}],
            F.BASIS_AMOUNT_OPERATIONS["용역"]: [{"bidNtceNo": "R26BK00000001", "bidNtceOrd": "000", "bssamt": "100000000",
                                                 "rsrvtnPrceRngBgnRate": "-2", "rsrvtnPrceRngEndRate": "+2",
                                                 "npnInsrprm": "1000000", "bidPrceCalclAYn": "Y"}],
        })
        rec = fb.fetch_one(client, "u", "R26BK00000001-000", "용역")
        self.assertEqual(rec["notice"]["lower_rate"], "87.745")
        self.assertEqual((rec["notice"]["range_lo"], rec["notice"]["range_hi"]), (-2.0, 2.0))
        self.assertEqual((rec["notice"]["a_value"], rec["notice"]["a_applies"]), (1_000_000.0, True))

    def test_notice_info_quota_stops(self):
        class Quota(FakeClient):
            def fetch_all_pages(self, base_url, operation, params, label):
                if operation == F.BID_NOTICE_OPERATIONS["공사"]:
                    raise fb.ApiResultError("공고", "22", "LIMITED NUMBER OF SERVICE REQUESTS EXCEEDS ERROR")
                return super().fetch_all_pages(base_url, operation, params, label)

        with self.assertRaises(fb.ApiResultError):
            fb.notice_info(Quota({}), "R26BK00000001", "공사")

    def test_not_found_is_error(self):
        rec = fb.fetch_one(FakeClient({}), "u", "R26BK00000001-000", "시설")
        self.assertEqual(rec["work"], "공사")
        self.assertIn("error", rec)

    def test_resume_skips_only_successes(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "r.jsonl"
            out.write_text(json.dumps({"input": "A-000"}) + "\n" + json.dumps({"input": "B-000", "error": "x"}) + "\n", encoding="utf-8")
            self.assertEqual(fb.done_numbers(out), {"A-000"})
            targets = Path(d) / "t.csv"
            targets.write_text("공고번호,업무\nA-000,시설\nB-000,용역\n", encoding="utf-8-sig")
            self.assertEqual(fb.read_targets(targets), [("A-000", "시설"), ("B-000", "용역")])


class FillNoticeTest(unittest.TestCase):
    """이미 받은 줄에 공고 정보만 채우기(--fill-notice, 2026-10-28) — 개찰 결과는 다시 안 받는다."""

    LINES = [
        {"input": "A-000", "no": "A", "work": "공사", "prices": [{"x": 1}], "bidders": [{"y": 1}]},  # 10/27에 받은 줄
        {"input": "B-000", "no": "B", "work": "", "prices": [{"x": 1}], "bidders": []},  # 업무 모름
        {"input": "C-000", "error": "없음"},  # 개찰 결과 없음 — 건너뜀
        {"input": "D-000", "no": "D", "work": "공사", "prices": [{"x": 1}], "bidders": [{"y": 1}],
         "notice": {"lower_rate": "87.745"}},  # 이미 채워짐
    ]

    def _client(self):
        return FakeClient({
            F.BID_NOTICE_OPERATIONS["공사"]: [{"bidNtceNo": "A", "bidNtceOrd": "000", "bidNtceNm": "가", "sucsfbidLwltRate": "87.745",
                                               "opengDt": "2025-03-04 11:00:00"}],
            F.BID_NOTICE_OPERATIONS["물품"]: [{"bidNtceNo": "B", "bidNtceOrd": "000", "bidNtceNm": "나", "sucsfbidLwltRate": "84.245"}],
            F.BASIS_AMOUNT_OPERATIONS["공사"]: [{"bidNtceNo": "A", "bidNtceOrd": "000", "bssamt": "100000000",
                                                 "rsrvtnPrceRngBgnRate": "-3", "rsrvtnPrceRngEndRate": "+3"}],
        })

    def test_fills_only_missing_and_keeps_backup(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "bid_results.jsonl"
            original = "".join(json.dumps(x, ensure_ascii=False) + "\n" for x in self.LINES)
            path.write_text(original, encoding="utf-8")
            client = self._client()
            filled, tried, stopped = fb.fill_notices(client, path, sleep=0)
            self.assertEqual((filled, tried, stopped), (2, 2, False))
            recs = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(recs[0]["notice"]["lower_rate"], "87.745")
            self.assertEqual(recs[0]["notice"]["opened_at"], "2025-03-04 11:00:00")
            self.assertEqual(recs[0]["notice"]["range_hi"], 3.0)
            self.assertEqual((recs[1]["work"], recs[1]["notice"]["lower_rate"]), ("물품", "84.245"))
            self.assertEqual(recs[2:], self.LINES[2:])
            self.assertEqual((Path(d) / "bid_results_채우기전.jsonl").read_text(encoding="utf-8"), original)
            # 다시 실행하면 찾아본 줄은 건너뜀
            self.assertEqual(fb.fill_notices(self._client(), path, sleep=0)[1], 0)

    def test_fill_a_only_missing_a(self):
        """--fill-a(10/29): A값이 빈 줄만 다시 찾아보고, 한 번 찾아본 줄은 건너뜀."""
        responses = self._client().responses
        responses[F.BASIS_AMOUNT_OPERATIONS["공사"]] = [{"bidNtceNo": "D", "bidNtceOrd": "000", "bssamt": "100000000",
                                                         "npnInsrprm": "1000000", "bidPrceCalclAYn": "Y"}]
        responses[F.BID_NOTICE_OPERATIONS["공사"]] = [{"bidNtceNo": "D", "bidNtceOrd": "000", "sucsfbidLwltRate": "87.745"}]
        lines = [self.LINES[3], {**self.LINES[3], "no": "E", "notice": {"lower_rate": "87.745", "a_value": 0}}]
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "bid_results.jsonl"
            path.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in lines), encoding="utf-8")
            filled, tried, _ = fb.fill_notices(FakeClient(responses), path, sleep=0, only_a=True)
            self.assertEqual((filled, tried), (1, 1))
            recs = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(recs[0]["notice"]["a_value"], 1_000_000.0)
            self.assertEqual(recs[1], lines[1])
            self.assertEqual(fb.fill_notices(FakeClient(responses), path, sleep=0, only_a=True)[1], 0)

    def test_quota_stops_and_saves(self):
        class Quota(FakeClient):
            def fetch_all_pages(self, base_url, operation, params, label):
                if params.get("bidNtceNo") == "B":
                    raise fb.ApiResultError("공고", "22", "LIMITED")
                return super().fetch_all_pages(base_url, operation, params, label)

        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "bid_results.jsonl"
            path.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in self.LINES), encoding="utf-8")
            filled, tried, stopped = fb.fill_notices(Quota(self._client().responses), path, sleep=0)
            self.assertEqual((filled, tried, stopped), (1, 1, True))
            recs = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]
            self.assertIn("notice", recs[0])
            self.assertNotIn("notice", recs[1])  # 다음 실행에서 이어서


if __name__ == "__main__":
    unittest.main()
