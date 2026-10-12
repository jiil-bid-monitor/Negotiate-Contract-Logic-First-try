"""텔레그램 발송(`nego/telegram.py`) 테스트. 실제 텔레그램 API는 부르지 않는다.

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
    title = T.report_title(stats.categories, False, stats.period_begin, stats.period_end)
    return T.build_messages([T.Row.from_candidate(c) for c in candidates], title)


def _row(title, days, kind="입찰", budget=320_000_000, qual="자격 충족"):
    return T.Row(title, f"https://g2b/{title}", kind, budget, days, qual)


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
    def test_compact_two_line_format(self):
        """10/12 결정 모양: 공고명(링크) / 금액 · D-N · 자격."""
        candidates, stats = _candidates(2)
        self.assertEqual(len(candidates), 2)
        text = _messages(candidates, stats)[0]
        self.assertIn("<b>입찰 공고 보고 (8/31 게시분)</b>", text)
        self.assertIn("후보 <b>2건</b>", text)
        self.assertRegex(text, r'1\. <a href="[^"]+">○○전시관 전시물 제작설치 0</a>\n   5\.0억 · D-\d+ · 자격 미확인')
        self.assertNotIn("[입찰]", text, "한 유형만이면 유형 표시 없음")
        self.assertFalse(any(ord(ch) >= 0x1F000 or 0x2600 <= ord(ch) < 0x2800 for ch in text), "이모지 없음")

    def test_sorted_by_deadline_and_closed_counted(self):
        rows = [_row("나중", 9), _row("마감남음", None), _row("지남", -1), _row("먼저", 2, qual="자격 미달")]
        text = T.build_messages(rows, "보고")[0]
        self.assertIn("후보 <b>3건</b> (마감 지난 1건 제외)", text)
        self.assertLess(text.index("먼저"), text.index("나중"))
        self.assertLess(text.index("나중"), text.index("마감남음"))
        self.assertIn("3.2억 · D-2 · 자격 미달", text)
        self.assertIn("마감 미상", text)
        self.assertNotIn("지남", text)

    def test_weekly_mixed_kinds_are_tagged(self):
        rows = [_row("가", 3, "협상"), _row("나", 4, "사전규격", budget=45_000_000)]
        text = T.build_messages(rows, T.report_title(["협상", "규격가격동시입찰"], True,
                                                     datetime(2026, 10, 5), datetime(2026, 10, 11, 23, 59)))[0]
        self.assertIn("협상·규격가격동시입찰·사전규격 공고 보고 (10/5~10/11 게시분)", text)
        self.assertIn('[협상] <a href="https://g2b/가">가</a>', text)
        self.assertIn("[사전규격]", text)
        self.assertIn("4,500만원", text)

    def test_empty_run_still_reports(self):
        _, stats = _candidates(0)
        messages = _messages([], stats)
        self.assertEqual(len(messages), 1)
        self.assertIn("새 공고가 없습니다", messages[0])

    def test_long_list_is_split_without_breaking_a_notice(self):
        candidates, stats = _candidates(80)
        messages = _messages(candidates, stats)
        self.assertGreater(len(messages), 1)
        for m in messages:
            self.assertLessEqual(len(m), T.MESSAGE_LIMIT)
        joined = "\n\n".join(messages)
        for i in range(1, 81):
            self.assertEqual(joined.count(f"\n{i}. <a"), 1)

    def test_title_is_html_escaped(self):
        candidates, stats = _candidates(1)
        candidates[0].notice.title = "A <B> & C"
        self.assertIn("A &lt;B&gt; &amp; C", _messages(candidates, stats)[0])


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


class TestDevAlert(unittest.TestCase):
    def test_skipped_without_dev_chat(self):
        with mock.patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "1:x", "TELEGRAM_DEV_CHAT_ID": ""}):
            self.assertIsNone(T.send_dev_alert("실패"))

    def test_goes_only_to_dev_chat_with_key_masked(self):
        session = FakeSession()
        env = {"TELEGRAM_BOT_TOKEN": "1:x", "TELEGRAM_CHAT_ID": "111", "TELEGRAM_DEV_CHAT_ID": "999"}
        with mock.patch.dict(os.environ, env):
            errors = T.send_dev_alert("조회 실패 url: /a?serviceKey=SECRETKEY&type=json <b>", session=session)
        self.assertEqual(errors, [])
        self.assertEqual([c["data"]["chat_id"] for c in session.calls], ["999"], "담당자 방(111)엔 안 감")
        text = session.calls[0]["data"]["text"]
        self.assertNotIn("SECRETKEY", text)
        self.assertIn("serviceKey=***", text)
        self.assertIn("&lt;b&gt;", text)

    def test_cli_entry_never_fails_workflow(self):
        with mock.patch.dict(os.environ, {"TELEGRAM_DEV_CHAT_ID": ""}):
            self.assertEqual(T.main(["dev-alert", "x"]), 0)
        self.assertEqual(T.main(["oops"]), 2)


if __name__ == "__main__":
    unittest.main()
