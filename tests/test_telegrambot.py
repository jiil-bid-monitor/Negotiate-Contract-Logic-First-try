import unittest
from datetime import datetime

from webapp import telegrambot as tb

NOW = datetime(2026, 11, 9, 10, 0)
RUN = {
    "candidates": [
        {"notice_no": "R26BK01000001", "title": "체험관 조성", "detail_url": "https://x/1", "budget": 320000000,
         "budget_kind": "추정가격", "deadline": "2026-11-11T17:00", "deadline_label": "입찰 마감", "category": "협상",
         "demand_institution": "정선군", "qualification": {"checked": True, "missing": []},
         "sync": {"score": 0.71, "level": "높음"}},
        {"notice_no": "R26BK01000002", "title": "전시관 <리모델링>", "budget": 45000000,
         "deadline": "2026-11-20T10:00", "qualification": {"checked": True, "missing": ["건축공사업(0002)"]}},
        {"notice_no": "R26BK01000003", "title": "지난 공고", "deadline": "2026-11-01T10:00",
         "qualification": {"checked": False}},
    ],
    "rejected": [{"notice_no": "R26BK01000009", "title": "유지보수", "excluded_reason": "제외 키워드 [유지보수]"}],
}


class TelegramBotTest(unittest.TestCase):
    def test_notice(self):
        text = tb.reply("/notice@jiil_bid_bot r26bk01000001", RUN, NOW)
        self.assertIn('<a href="https://x/1">체험관 조성</a>', text)
        self.assertIn("3.2억 · D-2", text)
        self.assertIn("자격 충족", text)
        self.assertIn("싱크로율 71% (높음)", text)
        self.assertIn("제외된 공고", tb.reply("/notice R26BK01000009", RUN, NOW))
        self.assertIn("없는 공고", tb.reply("/notice R26BK09999999", RUN, NOW))
        self.assertIn("공고번호를 함께", tb.reply("/notice", RUN, NOW))

    def test_deadline(self):
        text = tb.reply("/deadline", RUN, NOW)
        self.assertIn("3일 안에 마감 1건", text)
        self.assertNotIn("지난 공고", text)
        text = tb.reply("/deadline 14", RUN, NOW)
        self.assertIn("2건", text)
        self.assertIn("전시관 &lt;리모델링&gt;", text)
        self.assertIn("자격 미달: 건축공사업(0002)", text)

    def test_other(self):
        self.assertIsNone(tb.reply("안녕하세요", RUN, NOW))
        self.assertIsNone(tb.reply("/unknown", RUN, NOW))
        self.assertIn("/deadline", tb.reply("/help", RUN, NOW))
        self.assertIn("수집 결과가 없습니다", tb.reply("/deadline", None, NOW))

    def test_poll_answers_only_known_chats(self):
        sent = []

        class Session:
            def post(self, url, json, timeout):
                method = url.rsplit("/", 1)[1]
                class R:
                    pass
                r = R()
                if method == "getUpdates":
                    result = [{"update_id": 5, "message": {"message_id": 1, "chat": {"id": -100}, "text": "/help"}},
                              {"update_id": 6, "message": {"message_id": 2, "chat": {"id": 777}, "text": "/help"}}]
                else:
                    sent.append(json)
                    result = {}
                r.json = lambda: {"ok": True, "result": result}
                return r

        bot = tb.TelegramBot("T", {"-100"}, lambda: RUN, Session())
        self.assertEqual(bot.poll_once(wait=0), 1)
        self.assertEqual(bot.offset, 7)
        self.assertEqual(sent[0]["chat_id"], "-100")


if __name__ == "__main__":
    unittest.main()
