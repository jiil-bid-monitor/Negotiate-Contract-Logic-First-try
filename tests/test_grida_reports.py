"""그리다도 정기 보고·판단 기록·피드백 시트·밤 투찰 이력 (2026-11-24 사용자 결정)."""

import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from nego import cli, feedback_sheet, report_log
from nego.config import company_tag
from nego.telegram import report_title
from webapp.bidlog import BidLog
from webapp.bidnightly import KST, LIVE_FILE, Nightly

from tests.test_bidnightly import cand, record, run_with


class CompanyReportTest(unittest.TestCase):
    def test_title_tag_and_report_file(self):
        with mock.patch.dict(os.environ, {"NEGO_COMPANY": "grida"}):
            self.assertEqual(company_tag(), "[그리다] ")
            self.assertTrue(report_title(["입찰"], False, None, None).startswith("[그리다] 입찰"))
            entry = report_log.build_entry("daily", [], datetime(2026, 11, 24, 1, tzinfo=timezone.utc), {})
            self.assertEqual(entry["company"], "grida")
            self.assertEqual(report_log.file_name(entry), "2026-11-24-daily-grida.json")
            self.assertEqual(report_log.parse(json.dumps(entry))["company"], "grida")
        with mock.patch.dict(os.environ, {"NEGO_COMPANY": ""}):
            self.assertEqual(company_tag(), "[지일] ")  # 지일 보고 제목에도 회사 이름(11/24), 보고 기록 파일은 예전 그대로
            entry = report_log.build_entry("daily", [], datetime(2026, 11, 24, 1, tzinfo=timezone.utc), {})
            self.assertNotIn("company", entry)
            self.assertEqual(report_log.file_name(entry), "2026-11-24-daily.json")

    def test_judge_url_gets_company(self):
        with mock.patch.dict(os.environ, {"TELEGRAM_JUDGE_URL": "http://h:1/?t=abc#live"}):
            cli._judge_url_for("grida")
            self.assertEqual(os.environ["TELEGRAM_JUDGE_URL"], "http://h:1/?t=abc&co=grida#live")

    def test_feedback_tab_per_company(self):
        env = {"FEEDBACK_SHEET_ID": "s", "GOOGLE_SERVICE_ACCOUNT_JSON": "{}", "FEEDBACK_SHEET_NAME": "", "NEGO_COMPANY": ""}
        with mock.patch.dict(os.environ, env):
            self.assertEqual(feedback_sheet.settings()[2], "피드백")
            self.assertEqual(feedback_sheet.settings("grida")[2], "피드백_그리다")
            self.assertEqual(feedback_sheet.settings("jiil")[2], "피드백")


class GridaNightlyTest(unittest.TestCase):
    def test_own_live_file_and_label(self):
        tmp = Path(tempfile.mkdtemp())
        night = Nightly(tmp / "data", BidLog(tmp / "data"), lambda: run_with(cand("R26BK00000001")),
                        lambda: (object(), "http://x"), "132-88-02939", history_dir=tmp / "out", archive_dir=tmp / "arch" / "그리다",
                        fetch_one=lambda c, b, n, w: record(n.split("-")[0], own=False),
                        fill_results=lambda *a, **k: {"fetched": 0, "left": 0}, company="grida")
        out = night.run_once(datetime(2026, 11, 9, 22, 0, tzinfo=KST))
        self.assertEqual(out["fetched"], 1)
        self.assertTrue((tmp / "out" / "bid_results_live_grida.jsonl").exists())
        self.assertFalse((tmp / "out" / LIVE_FILE).exists())  # 지일 파일엔 안 씀
        self.assertTrue((tmp / "arch" / "그리다" / "투찰이력.xlsx").exists())
        from openpyxl import load_workbook
        head = [c.value for c in load_workbook(tmp / "arch" / "그리다" / "투찰이력.xlsx")["개찰결과"][1]]
        self.assertIn("그리다 순위", head)


if __name__ == "__main__":
    unittest.main()
