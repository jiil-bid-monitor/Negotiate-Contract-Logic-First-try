"""회사 서류 정리·갱신 알림 — 옮기기 규칙, 만료 계산, 알림을 한 번만."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from openpyxl import load_workbook

from under100m.docs import LATEST, LIST_JSON, LIST_XLSX, OLD, add_months, expiry_of, parse_name, run, stage_of

TODAY = date(2026, 11, 5)


class NameTest(unittest.TestCase):
    def test_parse(self):
        doc = parse_name("직접생산확인증명서(조형물)_2026-02-19_유효2027-06-17.pdf")
        self.assertEqual((doc.kind, doc.issued, doc.expires), ("직접생산확인증명서(조형물)", date(2026, 2, 19), date(2027, 6, 17)))
        self.assertEqual(parse_name("사업자등록증_2025-08-18.PDF").kind, "사업자등록증")
        self.assertIsNone(parse_name("사업자등록증.pdf"))
        self.assertIsNone(parse_name("사업자등록증_2025-13-40.pdf"))

    def test_add_months_clamps(self):
        self.assertEqual(add_months(date(2026, 11, 30), 3), date(2027, 2, 28))


class ExpiryTest(unittest.TestCase):
    def test_rules(self):
        self.assertEqual(expiry_of({"종류": "무엇", "발급일": "2026-01-01", "유효기간": "2026-11-11"}), date(2026, 11, 11))
        self.assertEqual(expiry_of({"종류": "법인등기사항전부증명서", "발급일": "2026-03-12", "유효기간": ""}), date(2026, 6, 12))
        self.assertEqual(expiry_of({"종류": "재무제표", "발급일": "2025-03-31", "유효기간": ""}), date(2026, 3, 31))
        self.assertIsNone(expiry_of({"종류": "사업자등록증", "발급일": "2025-08-18", "유효기간": ""}))

    def test_stages(self):
        def stage(end, kind="무엇"):
            return stage_of({"종류": kind, "발급일": "2026-01-01", "유효기간": end}, TODAY)

        self.assertEqual(stage("2026-11-11"), ("7일", 6))
        self.assertEqual(stage("2026-12-01"), ("30일", 26))
        self.assertEqual(stage("2026-11-01"), ("지남", -4))
        self.assertIsNone(stage("2027-06-17"))
        self.assertIsNone(stage_of({"종류": "사업자등록증명", "발급일": "2026-09-20", "유효기간": ""}, TODAY))  # 12-20 만료, 30일 알림 없음


class RunTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.latest = self.dir / LATEST
        self.latest.mkdir()
        self.sent = []

    def put(self, name):
        (self.latest / name).write_bytes(b"%PDF-1.4")

    def records(self, *rows):
        data = [{"종류": k, "파일": f, "발급일": i, "유효기간": e, "대표자": "", "원본 위치": "옛 위치"} for k, f, i, e in rows]
        for row in data:
            self.put(row["파일"])
        (self.dir / LIST_JSON).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def go(self, today=TODAY):
        return run(self.dir, today, self.sent.append, settle=0)

    def test_new_file_moves_old_and_updates_list(self):
        self.records(("법인등기사항전부증명서", "법인등기사항전부증명서_2026-03-12.pdf", "2026-03-12", ""))
        self.put("법인등기사항전부증명서_2026-11-05.pdf")
        result = self.go()
        self.assertTrue((self.dir / OLD / "법인등기사항전부증명서_2026-03-12.pdf").exists())
        self.assertTrue((self.latest / "법인등기사항전부증명서_2026-11-05.pdf").exists())
        self.assertEqual(result["added"], ["법인등기사항전부증명서 (2026-11-05)"])
        rows = json.loads((self.dir / LIST_JSON).read_text(encoding="utf-8"))
        self.assertEqual([r["파일"] for r in rows], ["법인등기사항전부증명서_2026-11-05.pdf"])
        sheet = load_workbook(self.dir / LIST_XLSX).worksheets[0]
        self.assertEqual(sheet.cell(2, 1).value, "그대로 사용")
        self.assertIn("옮김 1건", self.sent[0])

    def test_never_overwrites_old_file(self):
        (self.dir / OLD).mkdir()
        (self.dir / OLD / "사업자등록증_2025-08-18.pdf").write_bytes(b"before")
        self.records(("사업자등록증", "사업자등록증_2025-08-18.pdf", "2025-08-18", ""))
        self.put("사업자등록증_2026-11-05.pdf")
        self.go()
        self.assertEqual((self.dir / OLD / "사업자등록증_2025-08-18.pdf").read_bytes(), b"before")
        self.assertTrue((self.dir / OLD / "사업자등록증_2025-08-18_2.pdf").exists())

    def test_older_new_file_is_not_moved(self):
        self.records(("사업자등록증", "사업자등록증_2026-08-18.pdf", "2026-08-18", ""))
        self.put("사업자등록증_2025-01-01.pdf")
        result = self.go()
        self.assertTrue((self.latest / "사업자등록증_2026-08-18.pdf").exists())
        self.assertTrue((self.latest / "사업자등록증_2025-01-01.pdf").exists())
        self.assertFalse((self.dir / OLD).exists())
        self.assertIn("오래됨", result["issues"][0])

    def test_same_date_is_not_moved(self):
        self.put("사업자등록증_2026-08-18.pdf")
        self.put("사업자등록증 _2026-08-18.pdf")
        result = self.go()
        self.assertFalse((self.dir / OLD).exists())
        self.assertIn("발급일이 같은", result["issues"][0])

    def test_no_move_kinds_and_item_names_are_separate(self):
        self.records(("실적증명서", "실적증명서_2026-01-06.pdf", "2026-01-06", ""),
                     ("직접생산확인증명서(조형물)", "직접생산확인증명서(조형물)_2026-02-19.pdf", "2026-02-19", "2027-06-17"))
        self.put("실적증명서_2026-11-01.pdf")
        self.put("직접생산확인증명서(실물모형)_2026-03-25_유효2027-07-26.pdf")
        result = self.go()
        self.assertFalse((self.dir / OLD).exists())
        self.assertIn("옮기지 않음", result["issues"][0])

    def test_bad_name_is_left_alone(self):
        self.put("스캔001.pdf")
        result = self.go()
        self.assertTrue((self.latest / "스캔001.pdf").exists())
        self.assertIn("규칙과 다름", result["issues"][0])

    def test_file_being_copied_is_skipped(self):
        self.put("스캔001.pdf")
        result = run(self.dir, TODAY, self.sent.append)  # 방금 만든 파일 — 2분 기다림
        self.assertEqual(result["issues"], [])
        self.assertEqual(self.sent, [])

    def test_missing_expiry_is_asked(self):
        self.records(("신용평가등급확인서", "신용평가등급확인서_2026-02-20.pdf", "2026-02-20", "2027-02-19"))
        self.put("신용평가등급확인서_2026-11-05.pdf")
        result = self.go()
        self.assertIn("_유효YYYY-MM-DD", result["issues"][0])

    def test_alert_once_per_stage(self):
        self.records(("신용평가등급확인서", "신용평가등급확인서_2026-02-20.pdf", "2026-02-20", "2026-11-11"))
        self.assertEqual(len(self.go()["due"]), 1)
        self.assertEqual(self.go()["due"], [])
        self.assertEqual(len(self.go(date(2026, 11, 12))["due"]), 1)  # 지남은 새 단계
        self.assertEqual(len(self.sent), 2)

    def test_memo_keeps_reissue_status(self):
        rows = [{"종류": "사업자등록증", "파일": "사업자등록증_2025-08-18.pdf", "발급일": "2025-08-18", "유효기간": "", "메모": "확인 메모"}]
        self.put(rows[0]["파일"])
        (self.dir / LIST_JSON).write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
        self.go()
        sheet = load_workbook(self.dir / LIST_XLSX).worksheets[0]
        self.assertEqual((sheet.cell(2, 1).value, sheet.cell(2, 3).value), ("재발급 필요", "확인 메모"))

    def test_missing_folder_is_an_error(self):
        self.latest.rmdir()
        with self.assertRaises(FileNotFoundError):
            self.go()


if __name__ == "__main__":
    unittest.main()
