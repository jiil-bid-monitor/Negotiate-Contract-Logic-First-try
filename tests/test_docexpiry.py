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
            # 상태 표시 버튼 (11/12 사용자 요청): 알림 버튼 → 상태 묻기 → 신청해야 함/신청 중 → 알림·/docs에 안내, 지우기
            button = sent[0]["reply_markup"]["inline_keyboard"][0][0]
            self.assertEqual(button["text"], "상태 표시: 직접생산(전시)")
            with mock.patch.object(D, "APPLY_PATH", Path(tempfile.mkdtemp()) / "applying.json"):
                def press(data):
                    self.assertTrue(bot.handle_apply({"id": "1", "data": data, "message": {"chat": {"id": -1}}}))
                    return sent[-1]

                need, doing, renew, clear = [b["callback_data"] for row in press(button["callback_data"])["reply_markup"]["inline_keyboard"]
                                      for b in row]
                docs = D.read_docs(self.dir)
                self.assertIn("'신청해야 함'으로 표시", press(need)["text"])
                self.assertIn("(D-2) · 신청해야 함", tb.reply("/docs", None, datetime(2026, 11, 9)))
                self.assertIn("갱신 신청을 해 주세요", D.due_alert(docs, date(2026, 11, 11), set(), D.load_states())[0])
                self.assertIn("/upload", press(doing)["text"])
                marked = D.load_states()
                self.assertIn(D.STATES["p"][1], D.due_alert(docs, date(2026, 11, 11), set(), marked)[0])
                self.assertIn("(신청 중)", D.apply_buttons(docs, date(2026, 11, 9), marked)["inline_keyboard"][0][0]["text"])
                # 만료 뒤에도 표시해 둔 서류는 7일마다 다시(표시 안 한 지난 서류는 그대로 안 알림), 버튼도 남음
                self.assertEqual(D.due_alert(docs, date(2026, 11, 17), set(), marked), (None, []))
                late, keys = D.due_alert(docs, date(2026, 11, 18), set(), marked)
                self.assertIn("만료 뒤 7일", late)
                self.assertNotIn("신용평가", late)
                self.assertEqual(D.due_alert(docs, date(2026, 11, 20), set(keys), marked), (None, []))
                self.assertIn("만료 뒤 14일", D.due_alert(docs, date(2026, 11, 25), set(keys), marked)[0])
                self.assertEqual(len(D.apply_buttons(docs, date(2026, 11, 18), marked)["inline_keyboard"]), 1)
                self.assertIn("지웠습니다", press(clear)["text"])
                self.assertEqual(D.load_states(), {})
                self.assertIsNone(D.apply_buttons(docs, date(2026, 11, 18), {}))
            self.assertFalse(bot.handle_apply({"id": "2", "data": "k:0", "message": {"chat": {"id": -1}}}), "/upload 버튼은 그대로")


if __name__ == "__main__":
    unittest.main()




class TestBulk(unittest.TestCase):
    """버튼만으로 한꺼번에 상태 표시 + '갱신 완료' = 새 PDF를 받아 유효기간 확인 (2026-11-23 사용자 요청 — 그리다 서류 5건)."""

    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        (self.folder / "[최신 서류]").mkdir()
        wb = openpyxl.Workbook()
        for row in [("지일", "만료일", "그리다", "만료일"), ("조형물", "27.6.17", "가", "26.11.30"), ("책장", "27.1.2", "나", "26.12.1")]:
            wb.active.append(row)
        wb.save(self.folder / D.CERT_FILE)

    def test_all_buttons(self):
        docs = D.read_docs(self.folder)
        today = date(2026, 11, 23)
        kb = D.apply_buttons(docs, today, {})["inline_keyboard"]
        self.assertEqual([b["callback_data"] for b in kb[-2] + kb[-1]], ["s:all:n", "s:all:p", "e:all", "s:all:x"])
        self.assertEqual(len(D.apply_buttons(docs[:1], today, {})["inline_keyboard"]), 1, "1건이면 '모두' 없음")
        path = Path(tempfile.mkdtemp()) / "applying.json"
        D.apply_callback(docs, "s:all:p", path, today)
        self.assertEqual(len(D.load_states(path)), 2, "30일 밖 서류(조형물 27.6.17)는 안 건드림")
        self.assertEqual(D.renew_kinds(docs, "e:all", today, path), ["(그리다) 가", "(그리다) 나"])

    def test_renew_uploads_pdf_and_confirms_date(self):
        import os
        from unittest import mock

        from webapp import docupload

        sent = []

        class Session:
            def post(self, url, json, timeout):
                sent.append(json)
                r = type("R", (), {})()
                r.json = lambda: {"ok": True, "result": {"message_id": len(sent)}}
                return r

        texts = iter(["유효기간 : 2025.11.30 ~ 2027.11.30", "스캔본"])
        with mock.patch.dict(os.environ, {"COMPANY_DOCS_DIR": str(self.folder), "TELEGRAM_CHAT_ID": "-1"}),                 mock.patch.object(docupload, "pdf_text", lambda data: next(texts)),                 mock.patch.object(D, "APPLY_PATH", Path(tempfile.mkdtemp()) / "a.json"):
            bot = tb.TelegramBot("T", {"-1"}, lambda: None, Session())
            bot.uploader.download = lambda file_id: b"%PDF-1.4"
            chat = {"chat": {"id": -1}}
            self.assertTrue(bot.handle_apply({"id": "1", "data": "e:all", "message": chat}))
            self.assertIn("1건 더", sent[-1]["text"])
            pdf = {"file_name": "a.pdf", "mime_type": "application/pdf", "file_id": "f"}
            # 첫 서류: PDF 글자에서 날짜를 읽어 '맞음' 버튼
            bot.uploader.handle_message({**chat, "document": pdf, "message_id": 50, "reply_to_message": {"message_id": len(sent)}})
            self.assertIn("2027-11-30", sent[-1]["text"])
            self.assertTrue(bot.uploader.handle_callback({"id": "2", "data": "x:ok", "message": {**chat, "message_id": len(sent)}}))
            self.assertIn("(그리다) 나", sent[-1]["text"], "다음 서류를 이어서 묻는다")
            # 둘째 서류: 못 읽으면 답장으로 날짜
            bot.uploader.handle_message({**chat, "document": pdf, "message_id": 51, "reply_to_message": {"message_id": len(sent)}})
            self.assertIn("못 읽었습니다", sent[-1]["text"])
            self.assertEqual(sent[-1]["reply_markup"]["inline_keyboard"][0][0]["text"], "1년 → 2027-12-01", "지금 만료일에서 1년 뒤")
            self.assertTrue(bot.uploader.handle_callback({"id": "3", "data": "x:y1", "message": {**chat, "message_id": len(sent)}}))
        got = dict(D.read_cert(self.folder / D.CERT_FILE))
        self.assertEqual((got["(그리다) 가"], got["(그리다) 나"]), (date(2027, 11, 30), date(2027, 12, 1)))
        self.assertEqual(len(list((self.folder / "[최신 서류]").glob("*.pdf"))), 2)
        self.assertEqual(docupload.found_expiry("유 효 기 간: 2026-04-01 ~ 2027-03-31"), date(2027, 3, 31))

    def test_web_alert(self):
        import os
        from unittest import mock

        with mock.patch.dict(os.environ, {"COMPANY_DOCS_DIR": str(self.folder)}), \
                mock.patch.object(D, "APPLY_PATH", Path(tempfile.mkdtemp()) / "a.json"):
            got = D.web_alert(date(2026, 11, 23))
        self.assertEqual([(d["kind"], d["left"]) for d in got], [("(그리다) 가", 7), ("(그리다) 나", 8)])
        with mock.patch.dict(os.environ, {"COMPANY_DOCS_DIR": str(self.folder / "없음")}):
            self.assertEqual(D.web_alert(date(2026, 11, 23)), [], "서류 폴더가 없으면(서버 PC) 종 없음")

    def test_web_upload_read_and_save(self):
        """웹앱 종 창에서 PDF 올리기(2026-11-23) — 읽기만 → 저장(표 만료일), 모르는 종류·PDF 아님은 거절."""
        import base64
        import os
        from unittest import mock

        from webapp import docupload, server

        pdf = base64.b64encode(b"%PDF-1.4 test").decode()
        with mock.patch.dict(os.environ, {"COMPANY_DOCS_DIR": str(self.folder)}), \
                mock.patch.object(D, "APPLY_PATH", Path(tempfile.mkdtemp()) / "a.json"), \
                mock.patch.object(docupload, "pdf_text", lambda data: "유효기간 : 2025.12.01 ~ 2027.12.01"):
            got = server.docs_upload({"action": "read", "kind": "(그리다) 나", "pdf": pdf})
            self.assertEqual(got, {"found": "2027-12-01", "cands": ["2027-12-01", "2028-12-01"]})
            saved = server.docs_upload({"action": "save", "kind": "(그리다) 나", "pdf": pdf, "end": "2027-12-01"})
            self.assertIn("2027-12-01로 고쳤습니다", saved["cert"])
            for bad in ({"kind": "../../x", "pdf": pdf}, {"kind": "(그리다) 나", "pdf": base64.b64encode(b"hello").decode()}):
                with self.assertRaises(ValueError):
                    server.docs_upload({"action": "read", **bad})
        self.assertEqual(dict(D.read_cert(self.folder / D.CERT_FILE))["(그리다) 나"], date(2027, 12, 1))
        self.assertTrue(any(p.name.startswith("(그리다) 나_") for p in (self.folder / "[최신 서류]").glob("*.pdf")))
