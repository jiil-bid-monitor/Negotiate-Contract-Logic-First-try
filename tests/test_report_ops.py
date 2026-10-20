"""정기 보고 운영(2026-10-20, 팀원 수집기 방식): 기간 발송 기록·주말 건너뛰기·미리보기·개발자 알림·첨부 읽기 전 거르기."""

import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nego import cli, dev_alert, sent_reports, telegram  # noqa: E402

MON = datetime(2026, 10, 19, 9, 0)  # 월요일
WED = datetime(2026, 10, 21, 9, 0)
SAT = datetime(2026, 10, 24, 9, 0)


class TestSentReports(unittest.TestCase):
    def test_period_key(self):
        self.assertEqual(sent_reports.period_key("daily", WED), "2026-10-21")
        self.assertEqual(sent_reports.period_key("weekly", WED), "2026-10-19", "주간은 그 주 월요일")
        self.assertEqual(sent_reports.period_key("weekly", MON), "2026-10-19")
        # 시간대가 붙은 값은 한국 날짜로 — UTC 일요일 16:00 = 한국 월요일 01:00
        utc = datetime(2026, 10, 18, 16, 0, tzinfo=timezone.utc)
        self.assertEqual(sent_reports.period_key("daily", utc), "2026-10-19")

    def test_weekend(self):
        self.assertTrue(sent_reports.is_weekend(SAT))
        self.assertTrue(sent_reports.is_weekend(datetime(2026, 10, 25, 9)))
        self.assertFalse(sent_reports.is_weekend(MON))

    def test_mark_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state" / "sent_report_daily.json"
            self.assertIsNone(sent_reports.load(path))
            self.assertFalse(sent_reports.already_sent(path, "daily", WED))
            sent_reports.mark(path, "daily", WED)
            self.assertTrue(sent_reports.already_sent(path, "daily", WED))
            self.assertFalse(sent_reports.already_sent(path, "daily", datetime(2026, 10, 22, 9)))
            path.write_text("{깨짐", encoding="utf-8")
            self.assertIsNone(sent_reports.load(path), "깨진 기록은 처음부터 — 빠지느니 한 번 더 간다")


class TestDevAlert(unittest.TestCase):
    def test_describe_failure(self):
        self.assertIn("키 거절", dev_alert.describe_failure("HTTP 401 Unauthorized SERVICE_KEY_IS_NOT_REGISTERED"))
        self.assertIn("접속 실패", dev_alert.describe_failure("연결 실패: ReadTimeout"))
        self.assertIn("텔레그램", dev_alert.describe_failure("sendMessage 실패: HTTP 403 telegram"))
        self.assertEqual(dev_alert.describe_failure("KeyError: 'x'"), "예상하지 못한 오류")

    def test_when_to_alert(self):
        log = "10:00:01 ERROR    용역 조회 실패: 연결 실패: ConnectTimeout\n그냥 줄"
        first = dev_alert.build_text("failure", 1, 5, 60, "매일 입찰 보고", log, "URL")
        self.assertIn("60분 뒤 다시 시도", first)
        self.assertIn("나라장터 접속 실패", first)
        self.assertIn("ConnectTimeout", first)
        self.assertNotIn("그냥 줄", first)
        self.assertIsNone(dev_alert.build_text("failure", 3, 5, 60, "m", log), "가운데 재시도 실패는 조용히")
        self.assertIn("5번 모두 실패", dev_alert.build_text("failure", 5, 5, 60, "m", log))
        self.assertIsNone(dev_alert.build_text("success", 1, 5, 60, "m"))
        self.assertIn("해결됨", dev_alert.build_text("success", 2, 5, 60, "m"))


class TestTelegramPacing(unittest.TestCase):
    def test_interval_between_messages(self):
        session = mock.Mock()
        session.post.return_value = NS(status_code=200)
        config = telegram.TelegramConfig(token="t", chat_ids=["1"])
        with mock.patch.object(telegram.time, "sleep") as sleep:
            errors = telegram.send_messages(config, ["a", "b", "c"], session=session)
        self.assertEqual(errors, [])
        self.assertEqual(session.post.call_count, 3)
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [telegram.SEND_INTERVAL] * 2,
                         "첫 메시지 앞엔 기다리지 않고 사이에만")


def _main(args, now, env=None):
    out = io.StringIO()
    with mock.patch.object(cli, "datetime") as dt, mock.patch.dict(os.environ, env or {}, clear=False), \
            redirect_stdout(out):
        dt.now.return_value = now
        code = cli.main(args)
    return code, out.getvalue()


class _Stop(Exception):
    pass


class TestCliReportGate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "sent_report_daily.json"
        self.base = ["--report", "daily", "--sent-reports", str(self.path), "--telegram",
                     "--store", str(Path(self.tmp.name) / "n.jsonl"), "--output", self.tmp.name]

    def tearDown(self):
        self.tmp.cleanup()

    def test_weekend_skips_before_collecting(self):
        with mock.patch.object(cli, "run", side_effect=AssertionError("주말엔 수집하지 않는다")):
            code, out = _main(self.base, SAT, {"NARA_SERVICE_KEY": "k"})
        self.assertEqual(code, 0)
        self.assertIn("주말", out)

    def test_already_sent_skips(self):
        sent_reports.mark(self.path, "daily", WED)
        with mock.patch.object(cli, "run", side_effect=AssertionError("이미 보낸 날은 수집하지 않는다")):
            code, out = _main(self.base, WED, {"NARA_SERVICE_KEY": "k"})
        self.assertEqual(code, 0)
        self.assertIn("이미 보냈습니다", out)

    def test_force_and_dry_run_go_through(self):
        sent_reports.mark(self.path, "daily", SAT)
        for extra in (["--force"], ["--dry-run"]):
            with self.subTest(extra=extra), mock.patch.object(cli, "run", side_effect=_Stop) as run:
                with self.assertRaises(_Stop):
                    _main(self.base + extra, SAT if extra == ["--force"] else WED, {"NARA_SERVICE_KEY": "k"})
                run.assert_called_once()
        sent_reports.mark(self.path, "daily", WED)
        with mock.patch.object(cli, "run", side_effect=_Stop), self.assertRaises(_Stop):
            _main(self.base + ["--dry-run"], WED, {"NARA_SERVICE_KEY": "k"})

    def test_sent_filter_runs_before_attachments(self):
        """매일 보고 3일치 중 이미 보낸 공고의 첨부는 읽지 않는다."""
        state = Path(self.tmp.name) / "sent_bids.json"
        state.write_text('{"OLD": {"ord": "000", "at": "2026-10-20"}}', encoding="utf-8")
        cands = [NS(notice=NS(notice_no=no, notice_ord="000"), corrected=False) for no in ("OLD", "NEW")]
        seen = []

        def fake_attachments(candidates, *a, **k):
            seen.extend(c.notice.notice_no for c in candidates)
            raise _Stop

        with mock.patch.object(cli, "run", return_value=(cands, NS(candidates=2), [])), \
                mock.patch("nego.attachments.save_attachment_texts", side_effect=fake_attachments), \
                self.assertRaises(_Stop):
            _main(self.base + ["--fetch-attachment-text", "--sent-state", str(state)], WED, {"NARA_SERVICE_KEY": "k"})
        self.assertEqual(seen, ["NEW"])


if __name__ == "__main__":
    unittest.main()
