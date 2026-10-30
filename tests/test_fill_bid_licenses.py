"""scripts/fill_bid_licenses.py — 과거 개찰 자료에 면허제한 업종 채우기(나라장터 호출은 가짜 클라이언트)."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

import fill_bid_licenses as fl  # noqa: E402
from nego.http_client import ApiResultError  # noqa: E402


class FakeClient:
    """등록일 기간별 면허제한 행 — spans {(시작 8자리, 끝 8자리): 행 목록}."""

    def __init__(self, spans=None, quota_after=None):
        self.spans = spans or {}
        self.calls = []
        self.quota_after = quota_after

    def fetch_all_pages(self, base_url, operation, params, label):
        if self.quota_after is not None and len(self.calls) >= self.quota_after:
            raise ApiResultError(label, "22", "LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS_ERROR")
        span = (params["inqryBgnDt"][:8], params["inqryEndDt"][:8])
        self.calls.append(span)
        return self.spans.get(span, [])


def _rec(no, opened, title="○○ 공사"):
    return {"no": no, "work": "공사", "bidders": [{"opengRank": "1"}], "prices": [],
            "notice": {"title": title, "opened_at": opened}}


ROW = {"bidNtceNo": "R25BK00000001", "bidNtceOrd": "000", "lmtGrpNo": "1", "lmtSno": "1", "lcnsLmtNm": "실내건축공사업/4990"}


class FillLicensesTest(unittest.TestCase):
    def test_halves_cover_month_and_two_before(self):
        spans = fl.halves("2025-01")
        self.assertEqual(len(spans), 6)
        self.assertEqual(spans[0], ("202411010000", "202411152359"))  # 해를 넘어감
        self.assertEqual(spans[-1], ("202501160000", "202501312359"))

    def test_month_test_fills_only_that_month(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bid_results.jsonl"
            recs = [_rec("R25BK00000001", "2025-09-10 11:00:00"), _rec("R25BK00000002", "2025-09-20 11:00:00"),
                    _rec("R25BK00000003", "2025-10-02 11:00:00"), {"input": "X-000", "error": "못 찾음"}]
            path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in recs), encoding="utf-8")
            client = FakeClient({("20250801", "20250815"): [ROW]})
            out = fl.fill_licenses(client, [path], {"2025-09"}, sleep=0)
            self.assertEqual((out["total"], out["found"], out["stopped"]), (2, 1, False))
            self.assertEqual(len(client.calls), 6)  # 7·8·9월 반달씩
            got = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(got[0]["notice"]["licenses"], [[["실내건축공사업/4990"]]])
            self.assertTrue(got[0]["notice"]["license_found"])
            self.assertFalse(got[1]["notice"]["license_found"])  # 찾아봤지만 없음 — 다시 안 찾음
            self.assertIn("license_checked", got[1]["notice"])
            self.assertNotIn("license_checked", got[2]["notice"])  # 10월 개찰은 이번 시험 밖
            self.assertTrue((Path(tmp) / "bid_results_채우기전.jsonl").exists())
            # 다시 실행하면 찾아본 공고는 건너뜀
            self.assertEqual(fl.fill_licenses(FakeClient(), [path], {"2025-09"}, sleep=0)["total"], 0)

    def test_all_reuses_shared_half_months(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bid_results.jsonl"
            recs = [_rec("A1", "2025-09-10"), _rec("A2", "2025-10-10")]
            path.write_text("".join(json.dumps(r) + "\n" for r in recs), encoding="utf-8")
            client = FakeClient()
            fl.fill_licenses(client, [path], None, sleep=0)
            self.assertEqual(len(client.calls), 8)  # 9월 6번 + 10월은 새 반달 2번만
            self.assertEqual(len(set(client.calls)), 8)

    def test_quota_stops_and_keeps_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bid_results.jsonl"
            path.write_text(json.dumps(_rec("A1", "2025-09-10")) + "\n", encoding="utf-8")
            out = fl.fill_licenses(FakeClient(quota_after=2), [path], None, sleep=0)
            self.assertTrue(out["stopped"])
            self.assertNotIn("license_checked", json.loads(path.read_text(encoding="utf-8"))["notice"])


if __name__ == "__main__":
    unittest.main()
