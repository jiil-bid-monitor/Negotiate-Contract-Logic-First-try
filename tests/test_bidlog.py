"""투찰 내역(2026-11-06 사용자 요청) — 투찰 계산기 '2. 투찰내역' 저장소."""

import json
import tempfile
import unittest
from pathlib import Path

from webapp.bidlog import BidLog, clean_entry

SAMPLE = {"noticeNo": "r26bk01810177", "noticeOrd": "000", "title": "태백 통합공공임대주택 건립사업(통신)",
          "institution": "강원특별자치도 태백시", "workType": "공사", "basis": "193,283,304", "lowerRate": 89.745,
          "bid": 174582000.4, "rate": 100.55, "method": "고정 100.55%", "recordedBy": "김", "memo": "외주와 같음",
          "unknown": "버림"}


class TestBidLog(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.log = BidLog(self.dir)

    def test_add_list_update_delete(self):
        e = self.log.add(SAMPLE)
        self.assertEqual(e["noticeNo"], "R26BK01810177")  # 대문자·공백 정리
        self.assertEqual(e["bid"], 174582000)              # 원 단위로
        self.assertEqual(e["basis"], 193283304)            # 쉼표 금액도 숫자로
        self.assertNotIn("unknown", e)
        self.assertIsNone(e["result"])                     # 개찰 결과 자리는 비워 둠
        second = self.log.add({**SAMPLE, "bid": 175000000})
        self.assertEqual([r["id"] for r in self.log.entries()][0], second["id"])  # 같은 공고도 줄이 하나 더(최근 것 먼저)
        self.log.update(e["id"], {"bid": "174,600,000", "memo": "금액 정정"})
        row = next(r for r in self.log.entries() if r["id"] == e["id"])
        self.assertEqual((row["bid"], row["memo"]), (174600000, "금액 정정"))
        self.log.delete(e["id"])
        self.assertEqual([r["id"] for r in self.log.entries()], [second["id"]])
        # 다시 열어도 그대로(파일에 저장)
        self.assertEqual(len(BidLog(self.dir).entries()), 1)

    def test_rejects_missing_notice_or_bid(self):
        with self.assertRaises(ValueError):
            clean_entry({**SAMPLE, "noticeNo": ""})
        with self.assertRaises(ValueError):
            clean_entry({**SAMPLE, "bid": ""})
        with self.assertRaises(ValueError):
            self.log.delete("없는id")

    def test_backup_and_restore(self):
        backup = Path(tempfile.mkdtemp())
        BidLog(self.dir, backup_dir=backup).add(SAMPLE)
        self.assertTrue((backup / "bid_log.json").exists())
        fresh = Path(tempfile.mkdtemp())  # 새 PC — 백업에서 되살림
        self.assertEqual(len(BidLog(fresh, backup_dir=backup).entries()), 1)

    def test_csv_has_korean_header_and_bom(self):
        self.log.add(SAMPLE)
        text = self.log.csv()
        self.assertTrue(text.startswith("﻿기록일시,공고번호"))
        self.assertIn("R26BK01810177", text)
        self.assertIn("174582000", text)

    def test_file_is_plain_json_list(self):
        self.log.add(SAMPLE)
        data = json.loads((self.dir / "bid_log.json").read_text(encoding="utf-8"))
        self.assertIsInstance(data, list)


if __name__ == "__main__":
    unittest.main()
