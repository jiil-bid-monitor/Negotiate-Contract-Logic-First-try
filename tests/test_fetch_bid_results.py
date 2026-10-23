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


if __name__ == "__main__":
    unittest.main()
