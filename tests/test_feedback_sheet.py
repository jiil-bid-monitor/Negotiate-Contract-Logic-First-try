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
        self.assertEqual(F.HEADER[F.VERDICT_COLUMN_INDEX], "판단")
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


# ── 웹앱 판단 → S~V (2026-10-21) ──

DECIDED = {"noticeNo": "R1", "status": "불참", "tags": ["자격 미달", "규모"], "reason": "=공동수급 어려움",
           "decidedBy": "홍길동", "decidedAt": "2026-10-21T01:30:00.000Z"}
REPORTED = {"noticeNo": "R2", "status": None, "reports": [{"date": "2026-10-21", "kind": "daily"}],
            "meta": {"deadline": "2099-01-01 10:00"}}
NOW_UTC = datetime(2026, 10, 21, 3, 0, tzinfo=__import__("datetime").timezone.utc)


class TestVerdicts(unittest.TestCase):
    def test_cells(self):
        self.assertEqual(F.verdict_cells(DECIDED, NOW_UTC),
                         ["불참", "자격 미달, 규모 / =공동수급 어려움", "홍길동", "2026-10-21 10:30"])
        self.assertEqual(F.verdict_cells(REPORTED, NOW_UTC), ["미판단", "", "", ""])
        self.assertEqual(F.verdict_cells({"noticeNo": "R3", "status": None}, NOW_UTC), ["", "", "", ""])

    def test_reason_is_written_as_is(self):
        self.assertEqual(F.verdict_cells(dict(DECIDED, tags=[]), NOW_UTC)[1], "=공동수급 어려움", "RAW로 써서 수식이 안 됨")

    def test_plan_only_changed_rows_and_keeps_manual_entries(self):
        rows = [
            ["R1"],                                            # 2행 — 판단 새로 씀
            ["R2"] + [""] * 17 + ["미판단"],                   # 3행 — 이미 같음
            ["R3"] + [""] * 17 + ["O (맞음)", "직접 적음"],    # 4행 — 웹앱 판단 없음, 사람이 적은 값은 그대로
            ["R4"] + [""] * 17 + ["참가", "", "김", "x"],      # 5행 — 웹앱에서 판단을 지움 → 비움
            ["R9"],                                            # 판단 기록에 없는 공고
            [],
        ]
        wanted = {"R1": ["불참", "a", "b", "c"], "R2": ["미판단", "", "", ""], "R3": ["", "", "", ""],
                  "R4": ["", "", "", ""]}
        self.assertEqual(F.plan_verdicts(rows, wanted), [(2, ["불참", "a", "b", "c"]), (5, ["", "", "", ""])])


class VerdictSession(FakeSession):
    def __init__(self, rows, head=None):
        super().__init__()
        self.rows, self.head = rows, head if head is not None else F.OLD_VERDICT_HEADER
        self.batch = None
        self.head_written = None
        self.validation = None

    def request(self, method, url, json=None, timeout=None, headers=None):
        if "fields=sheets.properties" in url:
            return FakeResponse(200, {"sheets": [{"properties": {"title": "피드백", "sheetId": 7}}]})
        if "S1%3AV1" in url:
            if method == "PUT":
                self.head_written = json["values"][0]
                return FakeResponse(200, {})
            return FakeResponse(200, {"values": [self.head]})
        if "A2%3AV" in url:
            return FakeResponse(200, {"values": self.rows})
        if "values:batchUpdate" in url:
            self.batch = json
            return FakeResponse(200, {})
        if ":batchUpdate" in url:
            self.validation = json
            return FakeResponse(200, {})
        raise AssertionError(url)


class TestWriteVerdicts(unittest.TestCase):
    def _env(self, **extra):
        return mock.patch.dict(os.environ, {"GOOGLE_SERVICE_ACCOUNT_JSON": _key_json(), "FEEDBACK_SHEET_ID": "SHEET",
                                            "GOOGLE_SERVICE_ACCOUNT_FILE": "", **extra})

    def test_writes_s_to_v_and_renames_old_header(self):
        session = VerdictSession([["R1"], ["R2"]])
        with self._env():
            result = F.write_verdicts({"R1": DECIDED, "R2": REPORTED, "R7": dict(DECIDED, noticeNo="R7")}, NOW_UTC,
                                      session=session)
        self.assertEqual(result, {"updated": 2, "not_in_sheet": 1})
        self.assertEqual([d["range"] for d in session.batch["data"]], ["피드백!S2:V2", "피드백!S3:V3"])
        self.assertEqual(session.batch["valueInputOption"], "RAW")
        self.assertEqual(session.head_written, ["판단", "사유", "판단자", "판단일"])
        opts = session.validation["requests"][0]["setDataValidation"]["rule"]["condition"]["values"]
        self.assertEqual([o["userEnteredValue"] for o in opts], F.VERDICT_OPTIONS)

    def test_custom_header_is_kept_and_prepare_once(self):
        session = VerdictSession([["R1"] + [""] * 17 + ["불참", "자격 미달, 규모 / =공동수급 어려움", "홍길동",
                                                         "2026-10-21 10:30"]], head=["내 머리글"])
        with self._env():
            result = F.write_verdicts({"R1": DECIDED}, NOW_UTC, session=session, prepare=False)
        self.assertEqual(result["updated"], 0, "같은 값이면 안 씀")
        self.assertIsNone(session.batch)
        self.assertIsNone(session.head_written)
        self.assertIsNone(session.validation)

    def test_key_file_setting(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "key.json"
            path.write_text(_key_json(), encoding="utf-8")
            with mock.patch.dict(os.environ, {"GOOGLE_SERVICE_ACCOUNT_JSON": "", "GOOGLE_SERVICE_ACCOUNT_FILE": f"'{path}'",
                                              "FEEDBACK_SHEET_ID": "SHEET"}):
                self.assertEqual(F.settings()[1:], ("SHEET", "피드백"))
            with mock.patch.dict(os.environ, {"GOOGLE_SERVICE_ACCOUNT_JSON": "", "GOOGLE_SERVICE_ACCOUNT_FILE": str(path) + "x",
                                              "FEEDBACK_SHEET_ID": "SHEET"}):
                with self.assertRaises(F.SheetsError):
                    F.settings()


class TestSheetSync(unittest.TestCase):
    def test_only_when_changed_or_resync_due(self):
        from webapp import sheetsync

        state = {"v": 1, "t": 0.0}
        calls = []

        def write(records, now, prepare):
            calls.append(prepare)
            return {"updated": 1, "not_in_sheet": 0}

        sync = sheetsync.SheetSync(lambda: (state["v"], {}), write=write, clock=lambda: state["t"])
        sync.tick()
        sync.tick()
        self.assertEqual(calls, [True], "바뀐 게 없으면 안 부름, 머리글·드롭다운은 처음 한 번")
        state["v"] = 2
        sync.tick()
        state["t"] = sheetsync.RESYNC_SECONDS + 1
        sync.tick()
        self.assertEqual(calls, [True, False, False])

    def test_error_keeps_retrying(self):
        from webapp import sheetsync

        calls = []

        def write(records, now, prepare):
            calls.append(prepare)
            if len(calls) == 1:
                raise F.SheetsError("403")
            return {"updated": 0, "not_in_sheet": 0}

        sync = sheetsync.SheetSync(lambda: (1, {}), write=write, clock=lambda: 0.0)
        with self.assertLogs("webapp.sheetsync", "WARNING"):
            self.assertIsNone(sync.tick())
        sync.tick()
        self.assertEqual(calls, [True, True], "실패하면 다음에 다시, 준비도 다시")


if __name__ == "__main__":
    unittest.main()
