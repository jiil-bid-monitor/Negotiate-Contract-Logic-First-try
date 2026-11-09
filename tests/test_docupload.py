import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from webapp.docupload import Uploader, guess_kind, parse_expiry

CHAT = "-5"


class UploadFlowTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        latest = self.dir / "[최신 서류]"
        latest.mkdir()
        (latest / "신용평가등급확인서_2026-02-20.pdf").write_bytes(b"%PDF old")
        rows = [{"종류": "신용평가등급확인서", "파일": "신용평가등급확인서_2026-02-20.pdf", "발급일": "2026-02-20",
                 "유효기간": "2026-06-16", "대표자": "", "원본 위치": "옛"}]
        (self.dir / "_목록.json").write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
        self.calls = []
        self.next_id = 100

        def api(method, **body):
            self.calls.append((method, body))
            self.next_id += 1
            return {"message_id": self.next_id}

        self.up = Uploader(api, lambda fid: b"%PDF new " + fid.encode(), {CHAT}, folder=lambda: self.dir,
                           today=lambda: date(2026, 11, 9))

    def msg(self, mid, text="", reply_to=None, doc=None):
        m = {"message_id": mid, "chat": {"id": int(CHAT)}, "text": text}
        if reply_to:
            m["reply_to_message"] = {"message_id": reply_to}
        if doc:
            m["document"] = doc
        return m

    def last_id(self):
        return self.next_id

    def test_full_flow(self):
        self.assertTrue(self.up.handle_message(self.msg(1, "/upload@jiil_bid_monitor_bot")))
        ask = self.last_id()
        self.assertFalse(self.up.handle_message(self.msg(2, "다른 말")), "답장이 아니면 무시")
        self.assertTrue(self.up.handle_message(self.msg(3, reply_to=ask, doc={"file_id": "F1", "file_name": "스캔.pdf"})))
        kb = self.calls[-1][1]["reply_markup"]["inline_keyboard"]
        self.assertEqual(kb[0][0], {"text": "신용평가등급확인서", "callback_data": "k:0"})
        cb = {"id": "c", "data": "k:0", "message": {"message_id": self.last_id(), "chat": {"id": int(CHAT)}}}
        self.assertTrue(self.up.handle_callback(cb))
        sent = self.calls[-1][1]
        self.assertEqual((sent["reply_to_message_id"], sent["reply_markup"]["force_reply"]), (3, True),
                         "날짜 질문은 PDF 보낸 메시지에 답장 + 답장 칸 자동")
        ask = self.last_id()
        self.assertTrue(self.up.handle_message(self.msg(4, "몰라", reply_to=ask)))
        self.assertIn("못 읽었습니다", self.calls[-1][1]["text"])
        first = 101  # 맨 처음 "PDF를 보내 주세요" 메시지에 답장해도 받는다
        self.assertTrue(self.up.handle_message(self.msg(5, "2027.6.16", reply_to=first)))
        new = self.dir / "[최신 서류]" / "신용평가등급확인서_2026-11-09_유효2027-06-16.pdf"
        self.assertEqual(new.read_bytes(), b"%PDF new F1")
        self.assertTrue((self.dir / "[이전 서류]" / "신용평가등급확인서_2026-02-20.pdf").exists())
        self.assertIn("저장했습니다", self.calls[-1][1]["text"])
        self.assertEqual(self.up.pending, {})

    def test_other_chat_and_cancel(self):
        other = {"message_id": 1, "chat": {"id": 9}, "text": "/upload"}
        self.assertFalse(self.up.handle_message(other))
        self.up.handle_message(self.msg(1, "/upload"))
        self.assertTrue(self.up.handle_message(self.msg(2, "취소", reply_to=self.last_id())))
        self.assertEqual(self.up.pending, {})

    def test_guessed_kind_one_click(self):
        self.up.handle_message(self.msg(1, "/upload"))
        self.up.handle_message(self.msg(2, reply_to=self.last_id(),
                                        doc={"file_id": "F", "file_name": "신용평가 등급확인서 2027.pdf"}))
        sent = self.calls[-1][1]
        self.assertIn("<b>신용평가등급확인서</b> 맞나요?", sent["text"])
        self.assertEqual([b["text"] for b in sent["reply_markup"]["inline_keyboard"][0]], ["맞음", "다른 종류"])
        more = {"id": "c", "data": "k:more", "message": {"message_id": self.last_id(), "chat": {"id": int(CHAT)}}}
        self.assertTrue(self.up.handle_callback(more))
        self.assertEqual(self.calls[-1][1]["text"], "어떤 서류인가요?")

    def test_guess_kind(self):
        kinds = ["직접생산확인증명서", "직접생산확인증명서(조형물)", "직접생산확인증명서(실물모형및전시물)", "사업자등록증",
                 "사업자등록증명", "사용인감계", "중소기업·소상공인확인서"]
        self.assertEqual(guess_kind(kinds, "직접생산 확인증명서_조형물.pdf"), "직접생산확인증명서(조형물)")
        self.assertEqual(guess_kind(kinds, "scan001.pdf", "직접생산확인증명서 … 품명 조형물"), "직접생산확인증명서(조형물)")
        self.assertEqual(guess_kind(kinds, "직접생산확인증명서.pdf"), "직접생산확인증명서")
        self.assertEqual(guess_kind(kinds, "사업자등록증명원.pdf"), "사업자등록증명")
        self.assertEqual(guess_kind(kinds, "중소기업 소상공인 확인서.pdf"), "중소기업·소상공인확인서")
        self.assertIsNone(guess_kind(kinds, "스캔.pdf"))

    def test_parse_expiry(self):
        self.assertEqual(parse_expiry("2028-11-09"), date(2028, 11, 9))
        self.assertEqual(parse_expiry("20281109"), date(2028, 11, 9))
        self.assertIsNone(parse_expiry("없음"))
        self.assertEqual(parse_expiry("내년"), "")


if __name__ == "__main__":
    unittest.main()
