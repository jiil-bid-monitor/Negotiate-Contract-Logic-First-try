import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

import openpyxl

from webapp import docexpiry as D
from webapp import telegrambot as tb


def make_xlsx(folder: Path) -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["상태", "종류", "사유", "발급일", "유효기간(기준)", "대표자", "원본 위치"])
    ws.append(["", "직접생산(전시)", "", "2026-01-06", "2026-11-11", "", ""])
    ws.append(["", "신용평가", "", "2026-02-20", "2026-06-16", "", ""])
    ws.append(["", "등기", "", "2026-03-12", "발급 후 3개월", "", ""])
    ws.append(["", "조형물", "", "2026-02-19", datetime(2027, 6, 17), "", ""])
    wb.save(folder / "서류목록.xlsx")


class DocExpiryTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        make_xlsx(self.dir)

    def test_read_and_reply(self):
        docs = D.read_docs(self.dir)
        self.assertEqual([k for k, _ in docs], ["신용평가", "직접생산(전시)", "조형물"])
        text = D.docs_reply(docs, date(2026, 11, 9))
        self.assertIn("직접생산(전시) — 2026-11-11 (D-2)", text)
        self.assertIn("신용평가 — 2026-06-16 (지남)", text)
        self.assertEqual(D.read_docs(Path(tempfile.mkdtemp())), [])

    def test_alert_once(self):
        docs = D.read_docs(self.dir)
        text, keys = D.due_alert(docs, date(2026, 11, 9), set())
        self.assertIn("직접생산(전시)", text)
        self.assertNotIn("신용평가", text, "이미 지난 서류는 알리지 않는다")
        self.assertEqual(D.due_alert(docs, date(2026, 11, 9), set(keys)), (None, []))
        self.assertEqual(D.due_alert(docs, date(2026, 11, 1), set())[0], None, "8일 남으면 아직")

    def test_bot_checks_once_a_day_after_9(self):
        sent = []

        class Session:
            def post(self, url, json, timeout):
                sent.append(json)
                class R:
                    pass
                r = R()
                r.json = lambda: {"ok": True, "result": {}}
                return r

        import os
        from unittest import mock
        with mock.patch.dict(os.environ, {"COMPANY_DOCS_DIR": str(self.dir), "TELEGRAM_CHAT_ID": "-1"}):
            bot = tb.TelegramBot("T", {"-1"}, lambda: None, Session())
            bot.sent_path = Path(tempfile.mkdtemp()) / "sent.json"
            self.assertEqual(bot.check_docs(datetime(2026, 11, 9, 8, 0)), 0, "9시 전엔 안 봄")
            self.assertEqual(bot.check_docs(datetime(2026, 11, 9, 9, 5)), 1)
            self.assertEqual(sent[0]["chat_id"], "-1")
            bot.checked_day = None
            self.assertEqual(bot.check_docs(datetime(2026, 11, 9, 10, 0)), 0, "같은 서류는 한 번만")
            self.assertIn("회사 서류 유효기간", tb.reply("/docs", None, datetime(2026, 11, 9)))


if __name__ == "__main__":
    unittest.main()
