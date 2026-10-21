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
        self.assertRegex(text, r'1\. \[입찰\] <a href="[^"]+">○○전시관 전시물 제작설치 0</a>\n   5\.0억 · D-\d+ · 자격 미확인')
        self.assertFalse(any(ord(ch) >= 0x1F000 or 0x2600 <= ord(ch) < 0x2800 for ch in text), "이모지 없음")

    def test_sorted_by_deadline_and_closed_counted(self):
        rows = [_row("나중", 9), _row("마감남음", None), _row("지남", -1), _row("먼저", 2, qual="자격 미달")]
        text = T.build_messages(rows, "보고")[0]
        self.assertIn("후보 <b>3건</b>", text)
        self.assertNotIn("마감 지난", text, "마감 지난 공고는 수집에서 빠지므로 문구 없음(2026-10-19 통합 결정 4번)")
        self.assertLess(text.index("먼저"), text.index("나중"))
        self.assertLess(text.index("나중"), text.index("마감남음"))
        self.assertIn("3.2억 · <b>🔴 D-2</b> · 자격 미달", text)
        self.assertIn("마감 미상", text)
        self.assertNotIn("지남", text)

    def test_urgent_within_ten_days_is_red_and_bold(self):
        """10/12 사용자 요청: 마감 10일 이내는 붉게 — 텔레그램은 글자 색이 없어 굵은 🔴."""
        text = T.build_messages([_row("a", 10), _row("b", 11), _row("c", 0)], "보고")[0]
        self.assertIn("<b>🔴 D-10</b>", text)
        self.assertIn("<b>🔴 D-0</b>", text)
        self.assertIn(" · D-11 · ", text)
        self.assertNotIn("🔴 D-11", text)

    def test_unqualified_shows_joint_supply(self):
        """10/12 사용자 요청: 자격 미달이면 바로 옆에 공동수급 여부."""
        from types import SimpleNamespace as NS
        from nego.qualify import parse_joint_supply

        def cand(passes, joint_raw, kind="본공고"):
            return NS(qualification=NS(checked=True, passes=passes), joint=parse_joint_supply(joint_raw),
                      notice=NS(title="t", detail_url=None, kind=kind, budget=1e8, linked_system=None),
                      category="입찰", days_left=20)

        self.assertEqual(T.Row.from_candidate(cand(False, "(전자)공동이행 또는 분담이행")).qualification,
                         "자격 미달(공동수급 가능)")
        self.assertEqual(T.Row.from_candidate(cand(False, "(없음)공동수급불허")).qualification, "자격 미달(공동수급 불허)")
        self.assertEqual(T.Row.from_candidate(cand(False, None, "사전규격")).qualification, "자격 미달(공동수급 정보 없음)")
        self.assertEqual(T.Row.from_candidate(cand(True, "(없음)공동수급불허")).qualification, "자격 충족")

    def test_interest_and_product_sections(self):
        """2026-10-19 통합 결정 12·14번 — 관심 / 세부품명 구역, 품목 표시, [정정], 배정예산 표시."""
        rows = [
            T.Row("관심 공고", "https://g2b/1", "입찰", 150_000_000, 5, "자격 충족"),
            T.Row("안내판 설치", "https://g2b/2", "입찰", 120_000_000, 8, "자격 충족", section="세부품명",
                  products=["안내판"], budget_kind="배정예산"),
            T.Row("정정된 공고", "https://g2b/3", "입찰", None, 12, "자격 미확인", corrected=True, notes=["해외 의심"]),
        ]
        text = "\n".join(T.build_messages(rows, "보고"))
        self.assertIn("후보 <b>3건</b> (관심 공고 2 · 세부품명 공고 1)", text)
        self.assertLess(text.index("■ 관심 공고 2건"), text.index("■ 세부품명 공고 (키워드 없이 보유 품목으로 수집) 1건"))
        self.assertIn("1.2억(배정) · <b>🔴 D-8</b> · 자격 충족 — 품목: 안내판", text)
        self.assertIn("[정정] <a", text)
        self.assertIn("금액 모름 · D-12 · 자격 미확인 · 해외 의심", text)

    def test_one_missing_requirement_is_not_qualified(self):
        """예전엔 passes(빠진 요건 1개까지 허용)로 '자격 충족'이 나왔다."""
        from types import SimpleNamespace as NS

        c = NS(qualification=NS(checked=True, passes=True, missing_count=1), joint=NS(raw_value=None, allowed=False),
               notice=NS(kind="본공고", title="t", detail_url=None, budget=None, linked_system=None), category="입찰", days_left=3)
        self.assertTrue(T.Row.from_candidate(c).qualification.startswith("자격 미달"))

    def test_weekly_splits_main_and_prespec_sections(self):
        """10/12 사용자 요청: 본공고·사전규격을 나눠서 — 번호는 이어서, 본공고 안에서 유형이 섞이면 [협상]·[규격가격]."""
        rows = [_row("나", 4, "사전규격", budget=45_000_000), _row("가", 3, "협상"), _row("다", 9, "규격가격동시입찰")]
        text = T.build_messages(rows, T.report_title(["협상", "규격가격동시입찰"], True,
                                                     datetime(2026, 10, 5), datetime(2026, 10, 11, 23, 59)))[0]
        self.assertIn("협상·규격가격동시입찰·사전규격 공고 보고 (10/5~10/11 게시분)", text)
        self.assertIn("후보 <b>3건</b> (본공고 2 · 사전규격 1)", text)
        self.assertLess(text.index("── 본공고 2건 ──"), text.index("── 사전규격 1건 ──"))
        self.assertIn('1. [협상] <a href="https://g2b/가">가</a>', text)
        self.assertIn('2. [규격가격] <a href="https://g2b/다">다</a>', text)
        self.assertIn('3. <a href="https://g2b/나">나</a>', text, "사전규격 구역은 [사전규격] 표시 없이, 번호는 이어서")
        self.assertIn("4,500만원", text)

    def test_kind_tag_always_on_main_and_missing_kind(self):
        """10/21 사용자 요청: 본공고는 유형이 하나뿐이어도 [입찰] 등, 유형을 모르면 [유형 미상]."""
        text = T.build_messages([_row("가", 3, "협상"), _row("나", 5, None)], "보고")[0]
        self.assertIn('1. [협상] <a href="https://g2b/가">가</a>', text)
        self.assertIn('2. [유형 미상] <a href="https://g2b/나">나</a>', text)

    def test_prespec_only_gets_section_title_and_no_tag(self):
        text = T.build_messages([_row("가", 3, "사전규격")], "보고")[0]
        self.assertIn("── 사전규격 1건 ──", text)
        self.assertIn('1. <a href="https://g2b/가">가</a>', text)

    def test_prespec_unchecked_says_why(self):
        from types import SimpleNamespace as NS
        c = NS(qualification=NS(checked=False, passes=True, missing_count=0), joint=NS(raw_value=None, allowed=False),
               notice=NS(kind="사전규격", title="t", detail_url=None, budget=None, linked_system=None), category=None, days_left=3)
        self.assertEqual(T.Row.from_candidate(c).qualification, "자격 미확인(면허제한 정보 없음)")
        c.notice.kind = "본공고"
        self.assertEqual(T.Row.from_candidate(c).qualification, "자격 미확인")

    def test_single_kind_has_no_sections(self):
        text = T.build_messages([_row("가", 3, "입찰"), _row("나", 5, "입찰")], "입찰 공고 보고")[0]
        self.assertNotIn("──", text)
        self.assertIn("후보 <b>2건</b>", text)
        self.assertNotIn("(본공고", text)

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
            self.assertEqual(joined.count(f"\n{i}. [입찰] <a"), 1)

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

    def test_group_upgraded_to_supergroup_resends_to_new_id(self):
        """10/12 실제로 겪음: 일반 그룹이 슈퍼그룹으로 바뀌면 400 + migrate_to_chat_id — 새 ID로 다시 보내고 안내."""
        moved = FakeResponse(400, {"ok": False, "description": "Bad Request: group chat was upgraded to a supergroup chat",
                                   "parameters": {"migrate_to_chat_id": -1001234567890}})
        session = FakeSession([moved])
        config = T.TelegramConfig(token="1:x", chat_ids=["-5450822931"])
        errors = T.send_messages(config, ["a", "b"], session=session)
        self.assertEqual(errors, [])
        self.assertEqual([c["data"]["chat_id"] for c in session.calls], ["-5450822931", "-1001234567890", "-1001234567890"])
        self.assertEqual(config.migrated, {"-5450822931": "-1001234567890"})
        note = T.migration_notes(config, "TELEGRAM_CHAT_ID")[0]
        self.assertIn("-1001234567890", note)
        self.assertIn("TELEGRAM_CHAT_ID", note)

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
