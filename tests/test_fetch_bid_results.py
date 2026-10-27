"""scripts/fetch_bid_results.py — 공고번호 나누기·이어 받기·차수 거르기(나라장터 호출은 가짜 클라이언트)."""

import json
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

import fetch_bid_results as fb  # noqa: E402
from nego import fields as F  # noqa: E402
from nego.http_client import ApiError, ApiResultError  # noqa: E402


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

    def test_not_found_is_error(self):
        rec = fb.fetch_one(FakeClient({}), "u", "R26BK00000001-000", "시설")
        self.assertEqual(rec["work"], "공사")
        self.assertIn("error", rec)

    def test_notice_award_method_and_work(self):
        notices = [{"bidNtceOrd": "000", "sucsfbidMthdNm": "적격심사", "bidNtceNm": "가"},
                   {"bidNtceOrd": "001", "sucsfbidMthdNm": "협상에의한계약", "cntrctCnclsMthdNm": "제한경쟁", "sucsfbidLwltRate": "0"}]
        client = FakeClient({F.BID_NOTICE_OPERATIONS["물품"]: notices,
                             fb.PRICE_OPS["물품"]: [{"bidNtceOrd": "001", "bsisPlnprc": "1"}]})
        rec = fb.fetch_one(client, "u", "R26BK00000001-001", "")
        self.assertEqual(rec["work"], "물품")  # 본공고에서 찾은 업무로 예비가격도 물품만 조회
        self.assertEqual(rec["notice"]["award_method"], "협상에의한계약")
        self.assertEqual(rec["notice"]["contract_method"], "제한경쟁")
        self.assertEqual(rec["notice"]["ord"], "001")
        self.assertNotIn(fb.PRICE_OPS["공사"], client.calls)

    def test_notice_only_skips_results(self):
        client = FakeClient({F.BID_NOTICE_OPERATIONS["공사"]: [{"bidNtceOrd": "00", "sucsfbidMthdNm": "적격심사"}]})
        rec = fb.fetch_one(client, "u", "20201217355-00", "시설", notice_only=True)
        self.assertEqual(rec["notice"]["award_method"], "적격심사")
        self.assertNotIn("prices", rec)
        self.assertEqual(client.calls, [F.BID_NOTICE_OPERATIONS["공사"]])
        missing = fb.fetch_one(FakeClient({}), "u", "20201217355-00", "", notice_only=True)
        self.assertIsNone(missing["notice"])
        self.assertIn("error", missing)

    def test_full_fetch_keeps_a_value(self):
        basis_op = fb.BASIS_OPERATIONS["공사"]
        client = FakeClient({F.BID_NOTICE_OPERATIONS["공사"]: [{"bidNtceOrd": "000", "sucsfbidLwltRate": "87.745"}],
                             fb.PRICE_OPS["공사"]: [{"bidNtceOrd": "000", "plnprc": "1"}],
                             basis_op: [{"bidNtceOrd": "000", "bssamt": "223692000", "npnInsrprm": "4174428",
                                         "mrfnHealthInsrprm": "3093715", "bidPrceCalclAYn": "Y"}]})
        rec = fb.fetch_one(client, "u", "R26BK00000001-000", "시설")
        self.assertTrue(rec["basis"]["a_applies"])
        self.assertEqual(rec["basis"]["a_value"], 4174428 + 3093715)
        self.assertEqual(rec["basis"]["basis"], 223692000)
        self.assertNotIn("error", rec)
        # 기초금액을 못 찾아도 개찰결과가 있으면 오류 아님
        client2 = FakeClient({fb.PRICE_OPS["물품"]: [{"plnprc": "1"}]})
        rec2 = fb.fetch_one(client2, "u", "R26BK00000002-000", "물품")
        self.assertIsNone(rec2["basis"])
        self.assertNotIn("error", rec2)

    def test_basis_only(self):
        basis_op = fb.BASIS_OPERATIONS["공사"]
        client = FakeClient({basis_op: [{"bidNtceOrd": "00", "bssamt": "100", "bidPrceCalclAYn": "N"}]})
        rec = fb.fetch_one(client, "u", "20201217355-00", "시설", basis_only=True)
        self.assertEqual(client.calls, [basis_op])  # 본공고·개찰결과는 안 부름
        self.assertFalse(rec["basis"]["a_applies"])
        self.assertNotIn("prices", rec)
        missing = fb.fetch_one(FakeClient({}), "u", "20201217355-00", "", basis_only=True)
        self.assertIn("error", missing)

    def test_results_only_skips_notice_and_basis(self):
        client = FakeClient({fb.PRICE_OPS["공사"]: [{"bidNtceOrd": "000", "plnprc": "1"}],
                             F.OPENING_BIDDERS_OPERATION: [{"bidNtceOrd": "000", "opengRank": "1"}]})
        rec = fb.fetch_one(client, "u", "R26BK00000001-000", "시설", results_only=True)
        self.assertEqual(client.calls, [fb.PRICE_OPS["공사"], F.OPENING_BIDDERS_OPERATION])  # 입찰공고정보서비스는 안 부름
        self.assertNotIn("notice", rec)  # 나중에 --notice-only 목록에 잡히게 칸 자체를 안 둠
        self.assertNotIn("basis", rec)
        self.assertEqual(rec["work"], "공사")
        self.assertNotIn("error", rec)

    def test_only_options_exclusive(self):
        with tempfile.TemporaryDirectory() as d:
            t = Path(d) / "t.csv"
            t.write_text("A-000,시설\n", encoding="utf-8-sig")
            self.assertEqual(fb.main([str(t), "--results-only", "--basis-only"]), 1)

    def test_quota_error_http_429(self):
        body = '{"OpenAPI_ServiceResponse": {"cmmMsgHeader": {"errMsg": "LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS_ERROR", "returnReasonCode": "22"'
        self.assertTrue(fb.is_quota_error(ApiError("참가업체 X", f"HTTP 429 (응답 앞부분: {body})")))
        self.assertTrue(fb.is_quota_error(ApiResultError("x", "22", "한도")))
        self.assertFalse(fb.is_quota_error(ApiError("x", "HTTP 500 (응답 앞부분: )")))
        self.assertFalse(fb.is_quota_error(ApiResultError("x", "03", "데이터 없음")))

    def test_main_stops_on_quota_without_writing(self):
        calls = []

        def fake_fetch(client, base_url, notice_input, work, notice_only=False, basis_only=False, results_only=False):
            calls.append(notice_input)
            if notice_input == "B-000":
                raise ApiError("참가업체 B", 'HTTP 429 (응답 앞부분: {"errMsg": "LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS_ERROR"})')
            return {"input": notice_input, "prices": [1], "bidders": [1]}

        with tempfile.TemporaryDirectory() as d:
            targets = Path(d) / "t.csv"
            targets.write_text("A-000,시설\nB-000,시설\nC-000,시설\n", encoding="utf-8-sig")
            out = Path(d) / "r.jsonl"
            with mock.patch.object(fb, "service_key", return_value="k"), mock.patch.object(fb, "fetch_one", fake_fetch), \
                    mock.patch.object(fb.time, "sleep"):
                self.assertEqual(fb.main([str(targets), "--out", str(out)]), 0)
            self.assertEqual(calls, ["A-000", "B-000"])  # 한도에 걸리면 C는 부르지 않음
            self.assertEqual(fb.done_numbers(out), {"A-000"})
            self.assertEqual(len(out.read_text(encoding="utf-8").splitlines()), 1)  # 한도 걸린 공고는 실패 줄도 안 남김

    def test_resume_skips_only_successes(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "r.jsonl"
            out.write_text(json.dumps({"input": "A-000"}) + "\n" + json.dumps({"input": "B-000", "error": "x"}) + "\n", encoding="utf-8")
            self.assertEqual(fb.done_numbers(out), {"A-000"})
            targets = Path(d) / "t.csv"
            targets.write_text("공고번호,업무\nA-000,시설\nB-000,용역\n", encoding="utf-8-sig")
            self.assertEqual(fb.read_targets(targets), [("A-000", "시설"), ("B-000", "용역")])


if __name__ == "__main__":
    unittest.main()
