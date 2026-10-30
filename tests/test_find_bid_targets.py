"""개찰 결과 목록에서 지일 분야·참가 적은 공고 고르기(`scripts/find_bid_targets.py`) — 가짜 클라이언트로 확인한다."""

from __future__ import annotations

import csv
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import date
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import find_bid_targets as ft  # noqa: E402
from nego.http_client import ApiResultError  # noqa: E402


def _row(no, title, count, ord_="000", day="2025-05-01 11:00"):
    return {"bidNtceNo": no, "bidNtceOrd": ord_, "bidNtceNm": title, "prtcptCnum": str(count), "opengDt": day}


class PickTest(unittest.TestCase):
    def test_filters_field_and_bidders_and_keeps_last_ord(self):
        rows = [_row("A", "○○박물관 전시물 제작 설치", 5), _row("A", "○○박물관 전시물 제작 설치", 6, "001"),
                _row("B", "국도 방호울타리 정비공사", 400),          # 참가 많음
                _row("C", "청사 청소 용역", 3),                     # 분야 기타
                _row("D", "○○초 교사동 천장교체공사", 0),           # 참가 0(유찰 등)
                _row("E", "어린이 놀이터 조성", 12)]
        got = ft.pick(rows, "물품", 99, None)
        self.assertEqual(sorted(got), ["A", "E"])
        self.assertEqual((got["A"]["공고번호"], got["A"]["참가"], got["A"]["분야"]), ("A-001", 6, "전시·체험"))
        self.assertEqual(sorted(ft.pick(rows, "물품", 99, {"놀이·조경"})), ["E"])

    def test_windows(self):
        self.assertEqual(ft.windows(date(2025, 1, 1), date(2025, 1, 10), 7),
                         [("202501010000", "202501072359"), ("202501080000", "202501102359")])


class MainTest(unittest.TestCase):
    def test_writes_csv_without_already_fetched(self):
        calls = []

        def fake_fetch(self, base_url, op, params, label):
            calls.append((op, params["inqryBgnDt"]))
            if op == ft.LIST_OPS["물품"]:
                return [_row("A", "전시물 제작", 5), _row("B", "체험관 전시물 제작", 7)]
            return []

        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "bid_results.jsonl").write_text(json.dumps({"no": "B", "prices": []}) + "\n", encoding="utf-8")
            with mock.patch.object(ft, "service_key", return_value="k"), \
                    mock.patch.object(ft.DataGoKrClient, "fetch_all_pages", fake_fetch), redirect_stdout(io.StringIO()):
                code = ft.main(["--from", "2025-05-01", "--to", "2025-05-03", "--dir", str(d), "--all-methods"])
            self.assertEqual(code, 0)
            table = list(csv.reader((d / "bid_targets_others.csv").open(encoding="utf-8-sig")))
        self.assertEqual(table[0], ft.HEAD)
        self.assertEqual([r[0] for r in table[1:]], ["A-000"])  # B는 이미 받음
        self.assertEqual(len(calls), 3)  # 공사·용역·물품 × 1주

    def test_bid_only_drops_negotiated_and_no_floor(self):
        # 기본(입찰만) — 입찰공고 목록으로 협상·하한율 없음·복수예가 아님·공고 못 찾음을 뺌(10/30)
        notices = [
            {"bidNtceNo": "A", "bidNtceOrd": "000", "sucsfbidMthdNm": "적격심사", "sucsfbidLwltRate": "87.745", "prearngPrceDcsnMthdNm": "복수예가"},
            {"bidNtceNo": "B", "bidNtceOrd": "000", "sucsfbidMthdNm": "협상에의한계약", "sucsfbidLwltRate": ""},
            {"bidNtceNo": "C", "bidNtceOrd": "000", "sucsfbidMthdNm": "최저가", "sucsfbidLwltRate": ""},
            {"bidNtceNo": "D", "bidNtceOrd": "000", "sucsfbidMthdNm": "적격심사", "sucsfbidLwltRate": "88", "prearngPrceDcsnMthdNm": "단일예가"},
        ]

        def fake_fetch(self, base_url, op, params, label):
            if op == ft.LIST_OPS["물품"]:
                return [_row(n, "전시물 제작", 5) for n in "ABCDE"]
            if op == ft.F.BID_NOTICE_OPERATIONS["물품"]:
                return notices
            return []

        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            with mock.patch.object(ft, "service_key", return_value="k"), \
                    mock.patch.object(ft.DataGoKrClient, "fetch_all_pages", fake_fetch), redirect_stdout(io.StringIO()) as out:
                self.assertEqual(ft.main(["--from", "2025-05-01", "--to", "2025-05-03", "--dir", str(d), "--works", "물품"]), 0)
            table = list(csv.reader((d / "bid_targets_others.csv").open(encoding="utf-8-sig")))
        self.assertEqual([r[0] for r in table[1:]], ["A-000"])
        self.assertIn("협상 1", out.getvalue())
        self.assertIn("입찰공고 못 찾음 1", out.getvalue())

    def test_bid_only_reason(self):
        self.assertIsNone(ft.bid_only_reason({"sucsfbidMthdNm": "적격심사", "sucsfbidLwltRate": "89.745"}))
        self.assertEqual(ft.bid_only_reason({"sucsfbidMthdNm": "규격가격동시입찰", "sucsfbidLwltRate": "88"}), "규격가격동시입찰")
        self.assertEqual(ft.bid_only_reason({"sucsfbidMthdNm": "적격심사", "sucsfbidLwltRate": "0.1"}), "하한율 없음")
        self.assertEqual(ft.bid_only_reason(None), "입찰공고 못 찾음")

    def test_stops_on_quota(self):
        def quota(self, *a, **k):
            raise ApiResultError("한도", "22", "LIMITED")

        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(ft, "service_key", return_value="k"), \
                mock.patch.object(ft.DataGoKrClient, "fetch_all_pages", quota), redirect_stdout(io.StringIO()) as out:
            self.assertEqual(ft.main(["--from", "2025-05-01", "--to", "2025-05-03", "--dir", tmp]), 0)
        self.assertIn("한도", out.getvalue())


if __name__ == "__main__":
    unittest.main()
