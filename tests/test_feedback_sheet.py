"""피드백 구글 시트 적재 (nego/feedback_sheet.py, 2026-10-20 팀원 레포 이식). 구글은 부르지 않는다 — 가짜 세션.

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import base64
import json
import os
import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nego import feedback_sheet as F  # noqa: E402
from nego.config import load_config  # noqa: E402
from nego.models import notice_from_raw  # noqa: E402
from nego.pipeline import RunStats, build_candidates  # noqa: E402
from tests import fixtures  # noqa: E402

NOW = datetime(2026, 10, 20, 9, 0)


def _candidates(n: int = 2):
    config = load_config()
    config.screen.keywords = ["전시관"]
    raws = [fixtures.notice(f"R26FS{i:04d}", title=f"○○전시관 전시물 제작설치 {i}") for i in range(n)]
    stats = RunStats(period_begin=datetime(2026, 10, 17), period_end=NOW)
    cands = build_candidates([notice_from_raw(r, "용역") for r in raws], config, {}, {}, datetime(2026, 9, 1), stats)
    cands[0].sync = {"score": 0.4123, "basis": "과업 원문", "top": [{"year": 2023, "title": "과학관 전시", "score": 0.41},
                                                                 {"year": 2020, "title": "거의 무관", "score": 0.01}]}
    return cands


class TestRows(unittest.TestCase):
    def test_header_matches_team_repo(self):
        self.assertEqual(len(F.HEADER), 22)
        self.assertEqual(F.HEADER[F.VERDICT_COLUMN_INDEX], "판정")
        self.assertEqual(F.HEADER[:F.SYSTEM_COLUMNS][-1], "공동수급방식")

    def test_row_has_18_system_columns_in_order(self):
        cands = _candidates()
        row = F.to_row(cands[0], NOW)
        self.assertEqual(len(row), F.SYSTEM_COLUMNS)
        self.assertEqual(row[0], "R26FS0000")
        self.assertIn("키워드 전시관", row[9])
        self.assertEqual(row[12], "2026-10-20 09:00")
        self.assertEqual((row[13], row[14], row[15]), (41, 0.4123, "제목+과업내용"))
        self.assertEqual(row[16], "2023 과학관 전시 (0.410)", "0.02 이하 유사 사업은 뺀다")
        no_sync = F.to_row(cands[1], NOW)
        self.assertEqual(no_sync[13:16], ["", "", ""], "싱크로율 자료가 없으면 0이 아니라 빈 칸")

    def test_sanitize_formula_start(self):
        self.assertEqual(F.sanitize("=HYPERLINK(1)"), "'=HYPERLINK(1)")
        self.assertEqual(F.sanitize("-공고"), "'-공고")
        self.assertEqual(F.sanitize("2026-10-20 09:00"), "2026-10-20 09:00", "숫자로 시작하는 값은 그대로")

    def test_skip_existing_and_duplicates(self):
        cands = _candidates(2)
        rows, skipped = F.select_new_rows(cands + [cands[1]], {"R26FS0000"}, NOW)
        self.assertEqual([r[0] for r in rows], ["R26FS0001"])
        self.assertEqual(skipped, 2)


class FakeResponse:
    def __init__(self, status: int, payload: dict | None = None):
        self.status_code, self.ok = status, 200 <= status < 300
        self._payload = payload or {}
        self.text = json.dumps(self._payload) if payload is not None else ""

    def json(self):
        return self._payload


class FakeSession:
    """구글 토큰·시트 API 흉내 — 기존 행 1개, 헤더 있음"""

    def __init__(self, header: list[str] | None = None, verdicts: list | None = None):
        self.header = header if header is not None else F.HEADER
        self.verdicts = verdicts or []
        self.appended: list = []
        self.header_written = None
        self.assertion = None

    def post(self, url, data=None, timeout=None):
        self.assertion = data["assertion"]
        return FakeResponse(200, {"access_token": "tok"})

    def request(self, method, url, json=None, timeout=None, headers=None):
        assert headers["Authorization"] == "Bearer tok"
        if "fields=sheets.properties" in url:
            return FakeResponse(200, {"sheets": [{"properties": {"title": "피드백", "sheetId": 7}}]})
        if "A1%3AV1" in url and method == "GET":
            return FakeResponse(200, {"values": [self.header]} if self.header else {})
        if "A1%3AV1" in url and method == "PUT":
            self.header_written = json["values"][0]
            return FakeResponse(200, {})
        if "S2%3AV" in url:
            return FakeResponse(200, {"values": self.verdicts})
        if "A2%3AA" in url:
            return FakeResponse(200, {"values": [["R26FS0000"]]})
        if ":append" in url:
            self.appended.extend(json["values"])
            return FakeResponse(200, {})
        return FakeResponse(200, {})


def _key_json() -> str:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    return json.dumps({"client_email": "bot@x.iam.gserviceaccount.com", "private_key": pem.replace("\n", "\\n")})


class TestAppend(unittest.TestCase):
    def test_no_config_does_nothing(self):
        with mock.patch.dict(os.environ, {"GOOGLE_SERVICE_ACCOUNT_JSON": "", "FEEDBACK_SHEET_ID": ""}):
            self.assertIsNone(F.append_candidates(_candidates(), NOW, session=FakeSession()))

    def test_appends_only_new_rows_with_signed_jwt(self):
        session = FakeSession()
        with mock.patch.dict(os.environ, {"GOOGLE_SERVICE_ACCOUNT_JSON": _key_json(), "FEEDBACK_SHEET_ID": "SHEET"}):
            result = F.append_candidates(_candidates(), NOW, session=session)
        self.assertEqual(result, {"appended": 1, "skipped": 1})
        self.assertEqual([r[0] for r in session.appended], ["R26FS0001"])
        head, claims, sig = session.assertion.split(".")
        payload = json.loads(base64.urlsafe_b64decode(claims + "=" * (-len(claims) % 4)))
        self.assertEqual((payload["iss"], payload["scope"]), ("bot@x.iam.gserviceaccount.com", F.SCOPE))
        self.assertTrue(sig)

    def _run(self, session):
        with mock.patch.dict(os.environ, {"GOOGLE_SERVICE_ACCOUNT_JSON": _key_json(), "FEEDBACK_SHEET_ID": "SHEET"}):
            F.append_candidates(_candidates(), NOW, session=session)

    def test_existing_header_is_never_rewritten(self):
        session = FakeSession(header=F.HEADER[:13])
        self._run(session)
        self.assertIsNone(session.header_written, "이미 있는 머리글은 건드리지 않는다")
        self.assertEqual(len(session.appended), 1, "적재는 계속")

    def test_empty_sheet_gets_header(self):
        session = FakeSession(header=[])
        self._run(session)
        self.assertEqual(session.header_written, F.HEADER)

    def test_bad_key_json_is_a_clear_error(self):
        with mock.patch.dict(os.environ, {"GOOGLE_SERVICE_ACCOUNT_JSON": "not json", "FEEDBACK_SHEET_ID": "SHEET"}):
            with self.assertRaises(F.SheetsError):
                F.append_candidates(_candidates(), NOW, session=FakeSession())


if __name__ == "__main__":
    unittest.main()
