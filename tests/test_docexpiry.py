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

    def test_cert_file_wins(self):
        wb = openpyxl.Workbook()
        ws = wb.active
        for row in [("인증서갱신", None, None, None), (None, "만료일", "갱신주기", None), ("중소기업확인서", "27.3.31", "1년", None),
                    ("기업부설연구소", 4.3, "매년", "연구개발 실적제출"), ("실적신고", "2월,4월", "1년", None),
                    ("여성기업", "26.10.22", "3년", "그리다"), ("지일", "만료일", "그리다", "만료일"),
                    ("조형물", "27.6.17", "조형물", "27.06.01"), ("데이터처리서비스", "27.01.02", "가구-책장", "26.11.20")]:
            ws.append(row)
        wb.save(self.dir / D.CERT_FILE)
        # 서류목록의 '직접생산(전시)'·'신용평가'는 표에 없어 그대로, '조형물'(2027-06-17)은 표 날짜가 우선
        # 그리다(지일 직원 자회사)는 '(그리다) 이름'으로 함께
        self.assertEqual(D.read_docs(self.dir), [("신용평가", date(2026, 6, 16)), ("(그리다) 여성기업", date(2026, 10, 22)),
                                                 ("직접생산(전시)", date(2026, 11, 11)), ("(그리다) 가구-책장", date(2026, 11, 20)),
                                                 ("데이터처리서비스", date(2027, 1, 2)), ("중소기업확인서", date(2027, 3, 31)),
                                                 ("(그리다) 조형물", date(2027, 6, 1)), ("조형물", date(2027, 6, 17))])
        # /upload·/remove가 표 만료일 칸을 고친다 — 지일 조형물만, 그리다 조형물은 그대로
        self.assertTrue(D.set_cert(self.dir / D.CERT_FILE, "직접생산확인증명서(조형물)", date(2029, 1, 5)))
        self.assertTrue(D.set_cert(self.dir / D.CERT_FILE, "(그리다) 여성기업", "사용 안 함"))
        self.assertFalse(D.set_cert(self.dir / D.CERT_FILE, "없는서류", date(2029, 1, 5)))
        got = dict(D.read_cert(self.dir / D.CERT_FILE))
        self.assertEqual((got["조형물"], got["(그리다) 조형물"]), (date(2029, 1, 5), date(2027, 6, 1)))
        self.assertNotIn("(그리다) 여성기업", got)

    def test_alert_once(self):
        docs = D.read_docs(self.dir)
        text, keys = D.due_alert(docs, date(2026, 11, 9), set())
        self.assertIn("직접생산(전시)", text)
        self.assertNotIn("신용평가", text, "이미 지난 서류는 알리지 않는다")
        self.assertEqual(D.due_alert(docs, date(2026, 11, 9), set(keys)), (None, []))
        # 한 달 전에 한 번, 7일 전에 또 한 번 (11/12 사용자 요청)
        month, mkeys = D.due_alert(docs, date(2026, 11, 1), set())
        self.assertIn("30일 안에", month)
        self.assertIn("직접생산(전시)", month)
        self.assertNotIn("직접생산(전시)", D.due_alert(docs, date(2026, 11, 2), set(mkeys))[0] or "", "한 달 전 알림은 한 번만")
        self.assertIn("직접생산(전시)", D.due_alert(docs, date(2026, 11, 9), set(mkeys))[0], "7일 전에 다시")
        text, again = D.due_alert(docs, date(2026, 11, 11), set(keys))
        self.assertIn("2026-11-11 · 오늘 만료", text, "당일에도 그대로면 한 번 더")
        self.assertIn("/remove", text)
        self.assertEqual(D.due_alert(docs, date(2026, 11, 11), set(keys) | set(again)), (None, []))

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
            # '신청 중' 버튼 (11/12 사용자 요청): 알림에 버튼 → 누르면 표시 → 다음 알림·/docs에 안내, 다시 누르면 풀림
            button = sent[0]["reply_markup"]["inline_keyboard"][0][0]
            self.assertIn("직접생산(전시)", button["text"])
            with mock.patch.object(D, "APPLY_PATH", Path(tempfile.mkdtemp()) / "applying.json"):
                cb = {"id": "1", "data": button["callback_data"], "message": {"chat": {"id": -1}}}
                self.assertTrue(bot.handle_apply(cb))
                self.assertIn("신청 중으로 표시했습니다", sent[-1]["text"])
                self.assertIn("(D-2) · 신청 중", tb.reply("/docs", None, datetime(2026, 11, 9)))
                docs, marked = D.read_docs(self.dir), D.load_sent(D.APPLY_PATH)
                self.assertIn(D.APPLY_NOTE, D.due_alert(docs, date(2026, 11, 11), set(), marked)[0])
                self.assertIsNone(D.apply_buttons(docs, date(2026, 11, 9), marked), "표시한 서류는 버튼에서 빠짐")
                self.assertTrue(bot.handle_apply(cb))
                self.assertIn("풀었습니다", sent[-1]["text"])
                self.assertEqual(D.load_sent(D.APPLY_PATH), set())
            self.assertFalse(bot.handle_apply({"id": "2", "data": "k:0", "message": {"chat": {"id": -1}}}), "/upload 버튼은 그대로")


if __name__ == "__main__":
    unittest.main()
