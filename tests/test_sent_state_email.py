"""보낸 공고 기록(통합 결정 14번)·주간 메일(15번)."""

import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nego import email_report, sent_state  # noqa: E402


def _cand(no, ord_="000"):
    return NS(notice=NS(notice_no=no, notice_ord=ord_), corrected=False)


class TestSentState(unittest.TestCase):
    def test_new_and_corrected_only(self):
        sent = {"A": {"ord": "000", "at": "2026-10-18"}, "B": {"ord": "001", "at": "2026-10-18"}}
        a, b, c = _cand("A", "001"), _cand("B", "001"), _cand("C")
        picked = sent_state.select_new([a, b, c], sent)
        self.assertEqual(picked, [a, c])
        self.assertTrue(a.corrected)
        self.assertFalse(c.corrected)

    def test_mark_save_load_and_prune(self):
        now = datetime(2026, 10, 19)
        sent = {"OLD": {"ord": "000", "at": "2026-06-01"}}
        sent = sent_state.mark(sent, [_cand("N", "002")], now)
        self.assertNotIn("OLD", sent, "90일 지난 기록은 지운다")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state" / "sent.json"
            sent_state.save(path, sent)
            self.assertEqual(sent_state.load(path), {"N": {"ord": "002", "at": "2026-10-19"}})
        self.assertEqual(sent_state.load(Path("/nonexistent/x.json")), {})


class TestEmail(unittest.TestCase):
    def test_recipients_and_message(self):
        self.assertEqual(email_report.load_recipients(), ["dl4360@hanyang.ac.kr", "hjmun4178@gmail.com"])
        subject = email_report.subject_for("2026-10-19", 7)
        self.assertEqual(subject, "[나라장터 입찰 모니터링] 2026-10-19 주간 리포트 - 매칭 7건")
        msg = email_report.build_message(subject, "<p>본문</p>", "bot@x", ["a@x", "b@x"])
        self.assertEqual(msg["To"], "a@x, b@x")
        self.assertIn("<p>본문</p>", msg.get_body(("html",)).get_content())

    def test_missing_settings_returns_reason(self):
        with mock.patch.dict("os.environ", {"SMTP_HOST": ""}, clear=False):
            self.assertIsNotNone(email_report.send("s", "<p/>", recipients=["a@x"]))

    def test_sends_with_starttls(self):
        env = {"SMTP_HOST": "smtp.x", "SMTP_USER": "u@x", "SMTP_PASS": "pw", "SMTP_PORT": "587"}
        with mock.patch.dict("os.environ", env), mock.patch("smtplib.SMTP") as smtp:
            self.assertIsNone(email_report.send("s", "<p/>", recipients=["a@x"]))
            server = smtp.return_value.__enter__.return_value
            server.starttls.assert_called_once()
            server.login.assert_called_once_with("u@x", "pw")
            server.send_message.assert_called_once()


if __name__ == "__main__":
    unittest.main()
