"""텔레그램 발송(`nego/telegram.py`, 웹앱 `webapp/notify.py`) 테스트. 실제 텔레그램 API는 부르지 않는다.

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nego import telegram as T  # noqa: E402
from nego.config import load_config  # noqa: E402
from nego.models import notice_from_raw  # noqa: E402
from nego.pipeline import RunStats, build_candidates  # noqa: E402
from tests import fixtures  # noqa: E402
from webapp import notify  # noqa: E402

NOW = datetime(2026, 9, 1, 8, 5)


def _candidates(n: int, award: str = fixtures.QUALIFY):
    config = load_config()
    config.screen.keywords = ["전시관"]
    raws = [fixtures.notice(f"R26TG{i:04d}", title=f"○○전시관 전시물 제작설치 {i}", award=award) for i in range(n)]
    stats = RunStats(period_begin=datetime(2026, 8, 31), period_end=datetime(2026, 8, 31, 23, 59),
                     categories=["입찰"])
    candidates = build_candidates([notice_from_raw(r, "용역") for r in raws], config, {}, {}, NOW, stats, {"입찰"})
    return candidates, stats


def _messages(candidates, stats):
    from nego.report import report_title

    return T.build_messages([T.candidate_row(c) for c in candidates], report_title(stats),
                            stats.period_begin, stats.period_end)


class FakeResponse:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self._body = body or {"ok": status == 200}
        self.text = str(self._body)

    def json(self):
        return self._body


class FakeSession:
    def __init__(self, responses=None):
        self.calls = []
        self._responses = list(responses or [])

    def post(self, url, timeout=None, data=None, files=None):
        self.calls.append({"url": url, "data": data, "files": files})
        return self._responses.pop(0) if self._responses else FakeResponse()


class TestBuildMessages(unittest.TestCase):
    def test_lists_candidates_with_category_and_short_money(self):
        candidates, stats = _candidates(2)
        self.assertEqual(len(candidates), 2)
        messages = _messages(candidates, stats)
        self.assertEqual(len(messages), 1)
        text = messages[0]
        self.assertIn("나라장터 입찰 모니터링 리포트 (입찰)", text)
        self.assertIn("조회 기간: 2026.8.31 ~ 2026.8.31", text)
        self.assertIn("후보 <b>2건</b>", text)
        self.assertIn("[입찰·용역]", text)
        self.assertIn("억", text)
        self.assertIn("href=", text)

    def test_empty_run_still_reports(self):
        _, stats = _candidates(0)
        messages = _messages([], stats)
        self.assertEqual(len(messages), 1)
        self.assertIn("새 공고가 없습니다", messages[0])

    def test_long_list_is_split_without_breaking_a_notice(self):
        candidates, stats = _candidates(40)
        messages = _messages(candidates, stats)
        self.assertGreater(len(messages), 1)
        for m in messages:
            self.assertLessEqual(len(m), T.MESSAGE_LIMIT)
        joined = "\n\n".join(messages)
        for i in range(1, 41):
            self.assertEqual(joined.count(f"<b>{i}. ["), 1)

    def test_title_is_html_escaped(self):
        candidates, stats = _candidates(1)
        candidates[0].notice.title = "A <B> & C"
        self.assertIn("A &lt;B&gt; &amp; C", _messages(candidates, stats)[0])

    def test_card_rows_closed_review_flags_and_sync(self):
        """웹앱 카드 JSON 그대로: 마감 지난 건 건수만, 검토 필요는 끝에, 확인 필요·싱크로율·지역 표시."""
        base = {"title": "가 전시관", "category": "협상", "work_type": "용역", "kind": "본공고",
                "demand_institution": "가군", "budget": 320_000_000, "days_left": 5,
                "deadline_label": "자격등록 마감", "deadline": "2026-10-17T18:00",
                "qualification": {"checked": True, "passes": True, "summary": "자격 충족"},
                "joint": {"label": "공동이행"}, "region_check": {"status": "미달", "required": ["경기"]},
                "text_flags": [{"kind": "실적"}, {"kind": "현장설명회"}, {"kind": "실적"}],
                "sync": {"score": 0.723}}
        rows = [base,
                {**base, "title": "나 체험관", "days_left": -1},
                {**base, "title": "다 체험관 연출", "confidence": "검토필요", "review_exclude": "연출"}]
        text = "\n".join(T.build_messages(rows, "보고"))
        self.assertIn("후보 <b>1건</b> · 검토 필요 1건 (마감 지난 1건 제외)", text)
        self.assertIn("3.2억 · D-5 자격등록 마감(10/17)", text)
        self.assertIn("자격 충족 · 지역 미달(경기만) · 공동수급 공동이행", text)
        self.assertIn("확인 필요: 실적 · 현장설명회", text)
        self.assertIn("싱크로율 72%", text)
        self.assertNotIn("나 체험관", text)
        self.assertIn("다 체험관 연출", text)
        self.assertIn('(제외어 "연출")', text)
        self.assertLess(text.index("가 전시관"), text.index("다 체험관 연출"))


class TestSendReport(unittest.TestCase):
    def setUp(self):
        self.config = T.TelegramConfig(token="123:SECRET", chat_ids=["111", "222"])

    def test_sends_message_and_report_to_every_chat(self):
        candidates, stats = _candidates(1)
        html_path = Path(__file__).resolve().parent / "fixtures" / "attachments" / "README.md"
        session = FakeSession()
        errors = T.send_report(self.config, candidates, stats, html_path, session=session)
        self.assertEqual(errors, [])
        methods = [(c["url"].rsplit("/", 1)[1], c["data"]["chat_id"]) for c in session.calls]
        self.assertEqual(
            methods,
            [("sendMessage", "111"), ("sendDocument", "111"), ("sendMessage", "222"), ("sendDocument", "222")],
        )

    def test_no_document_when_no_candidates(self):
        _, stats = _candidates(0)
        session = FakeSession()
        T.send_report(self.config, [], stats, Path("없는파일.html"), session=session)
        self.assertTrue(all(c["url"].endswith("sendMessage") for c in session.calls))

    def test_client_error_is_reported_with_token_masked(self):
        _, stats = _candidates(0)
        session = FakeSession([FakeResponse(400, {"ok": False, "description": "Bad Request: chat not found"})])
        errors = T.send_report(
            T.TelegramConfig(token="123:SECRET", chat_ids=["999"]), [], stats, None, session=session
        )
        self.assertEqual(len(errors), 1)
        self.assertIn("chat not found", errors[0])
        self.assertNotIn("SECRET", errors[0])
        self.assertEqual(len(session.calls), 1, "4xx는 다시 시도해도 같으니 재시도하지 않는다")

    def test_config_from_env(self):
        with mock.patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_CHAT_ID": "1, 2"}):
            self.assertEqual(T.TelegramConfig.from_env().chat_ids, ["1", "2"])
        with mock.patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "", "TELEGRAM_CHAT_ID": "1"}):
            self.assertIsNone(T.TelegramConfig.from_env())


class TestWebappNotify(unittest.TestCase):
    RUN = {"id": 3, "params": {"days": 7, "attachments": False},
           "stats": {"period_begin": "2026-10-05T00:00", "period_end": "2026-10-12T09:00", "prespec_error": "403"},
           "candidates": [{"title": "가 전시관", "days_left": 3, "budget": 50_000_000}]}

    def test_build_uses_saved_run(self):
        with mock.patch.dict(os.environ, {"WEBAPP_URL": "http://192.168.0.22:8765/"}):
            text = notify.build(self.RUN, "김지일")[0]
        self.assertIn("지일 입찰 공고 보고 (최근 7일)", text)
        self.assertIn("조회 기간: 2026.10.5 ~ 2026.10.12", text)
        self.assertIn("사전규격 조회 실패", text)
        self.assertIn("첨부 자격판정 안 함", text)
        self.assertIn("5,000만원", text)
        self.assertIn("보낸 사람: 김지일", text)
        self.assertIn("http://192.168.0.22:8765/", text)

    def test_send_without_config_raises(self):
        with mock.patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "", "TELEGRAM_CHAT_ID": ""}):
            with self.assertRaises(ValueError):
                notify.send(self.RUN, "김지일")

    def test_send_posts_to_every_chat(self):
        session = FakeSession()
        with mock.patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "1:x", "TELEGRAM_CHAT_ID": "1,2"}):
            chats, errors = notify.send(self.RUN, "김지일", session=session)
        self.assertEqual((chats, errors), (2, []))
        self.assertEqual([c["data"]["chat_id"] for c in session.calls], ["1", "2"])


if __name__ == "__main__":
    unittest.main()
