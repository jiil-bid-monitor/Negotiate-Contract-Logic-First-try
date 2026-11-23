"""보고 기록 → 판단 기록 '미판단'·'놓침', 판단 기록 백업·판단기록.csv (2026-10-20, 팀원 레포 10/19 이식).

nego/report_log.py · webapp/backup.py · webapp/mstore.py(백업·merge_report) · webapp/reportsync.py.
git·구글 드라이브는 부르지 않는다 (임시 폴더와 가짜 git).

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nego import report_log as R  # noqa: E402
from nego.config import load_config  # noqa: E402
from nego.models import notice_from_raw  # noqa: E402
from nego.pipeline import RunStats, build_candidates  # noqa: E402
from tests import fixtures  # noqa: E402
from webapp import backup as B  # noqa: E402
from webapp.mstore import TeamStore  # noqa: E402
from webapp.reportsync import ReportSync  # noqa: E402

KST = timezone(timedelta(hours=9))
NOW = datetime(2026, 9, 1, 8, 5)


def _candidates(n: int = 2):
    config = load_config()
    config.screen.keywords = ["전시관"]
    raws = [fixtures.notice(f"R26RL{i:04d}", title=f"○○전시관 전시물 제작설치 {i}", award=fixtures.QUALIFY) for i in range(n)]
    stats = RunStats(period_begin=datetime(2026, 8, 31), period_end=datetime(2026, 8, 31, 23, 59), categories=["입찰"])
    return build_candidates([notice_from_raw(r, "용역") for r in raws], config, {}, {}, NOW, stats, {"입찰"})


def _entry(kind: str, date: str, *nos: str, deadline: str = "2099-01-02T18:00") -> dict:
    return {"v": 1, "kind": kind, "date": date, "reportedAt": f"{date}T00:30:00+00:00",
            "notices": [{"noticeNo": no, "title": f"{no} 전시관", "meta": {"deadline": deadline, "keywords": ["전시관"]}}
                        for no in nos]}


class TestBuildEntry(unittest.TestCase):
    def test_reported_notice_is_public_info_only(self):
        cands = _candidates(1)
        cands[0].sync = {"score": 0.716}
        e = R.build_entry("daily", cands, datetime(2026, 10, 20, 9, 0, tzinfo=KST), {})
        self.assertEqual((e["v"], e["kind"], e["date"]), (1, "daily", "2026-10-20"))
        n = e["notices"][0]
        self.assertEqual(n["noticeNo"], "R26RL0000")
        self.assertEqual(set(n["meta"]), {"sourceType", "businessType", "institution", "budgetAmount", "confidence",
                                           "bidMethod", "keywords", "products", "paths", "licenses", "contractMethod",
                                           "score", "deadline", "detailUrl"})
        self.assertEqual((n["meta"]["keywords"], n["meta"]["score"], n["meta"]["bidMethod"]), (["전시관"], 72, "입찰"))
        self.assertEqual(n["meta"]["paths"], ["관심 키워드"])

    def test_collect_paths_for_tuning(self):
        # 판단 기록으로 수집 조건을 고칠 때 어떤 경로로 들어온 공고인지(11/16 사용자 요청)
        self.assertEqual(R.collect_paths([], ["1"], ["정보통신공사업(0036)"]), ["세부품명", "보유 면허"])
        self.assertEqual(R.collect_paths(["전시관"], [], []), ["관심 키워드"])
        rec = {"R1": {"noticeNo": "R1", "title": "t", "status": "불참", "decidedAt": "2026-11-12T08:00:00Z", "tags": [],
                      "meta": {"paths": ["보유 면허"], "licenses": ["정보통신공사업(0036)"], "contractMethod": "수의계약"}}}
        csv = R.decisions_csv(rec, datetime(2026, 11, 16, tzinfo=KST)).lstrip("\ufeff").split("\r\n")
        head, row = csv[0].split(","), csv[1].split(",")
        got = dict(zip(head, row))
        self.assertEqual((got["수집 경로"], got["보유 면허(경로)"], got["계약방법"]), ("보유 면허", "정보통신공사업(0036)", "수의계약"))

    def test_kst_date(self):
        # 한국 10/20 08:30 = UTC 10/19 23:30
        self.assertEqual(R.kst_date(datetime(2026, 10, 19, 23, 30, tzinfo=timezone.utc)), "2026-10-20")

    def test_write_and_parse_roundtrip(self):
        folder = Path(tempfile.mkdtemp())
        e = R.build_entry("weekly", _candidates(2), datetime(2026, 10, 19, 9, 10, tzinfo=KST), {})
        path = R.write(folder, e)
        self.assertEqual(path.name, "2026-10-19-weekly.json")
        self.assertEqual(R.parse(path.read_text(encoding="utf-8")), e)

    def test_cli_saves_report_log_with_held_product_names(self):
        from nego import cli

        config = load_config()
        config.output_dir = Path(tempfile.mkdtemp())
        cands = _candidates(1)
        held = (config.held_raw.get("heldProducts") or [{}])[0]
        cands[0].screen_result.matched_product_codes = [held.get("code")]
        cli._save_report_log("daily", cands, config, datetime(2026, 10, 20, 9, 0))  # noqa: SLF001
        e = R.parse((config.output_dir / "report-log" / "2026-10-20-daily.json").read_text(encoding="utf-8"))
        self.assertEqual(e["notices"][0]["meta"]["products"], [held.get("name")], "품목은 코드가 아니라 이름으로")

    def test_weekly_log_keeps_mail_body_only(self):
        """주간 보고는 메일 본문 공고만 판단 기록에(11/23 사용자 — 첨부에만 있는 260건은 피로). 매일 보고는 그대로 전부."""
        from unittest import mock

        from nego import cli

        config = load_config()
        config.output_dir = Path(tempfile.mkdtemp())
        cands = _candidates(3)
        with mock.patch("nego.email_html.split_body", lambda cs: (cs[:1], {})):
            cli._save_report_log("weekly", cands, config, datetime(2026, 11, 23, 9, 10))  # noqa: SLF001
            cli._save_report_log("daily", cands, config, datetime(2026, 11, 23, 9, 0))  # noqa: SLF001
        read = lambda k: R.parse((config.output_dir / "report-log" / f"2026-11-23-{k}.json").read_text(encoding="utf-8"))  # noqa: E731
        self.assertEqual(len(read("weekly")["notices"]), 1)
        self.assertEqual(len(read("daily")["notices"]), 3)

    def test_parse_rejects_other_shapes(self):
        self.assertIsNone(R.parse("not json"))
        self.assertIsNone(R.parse(json.dumps({"v": 2, "kind": "daily", "date": "x", "notices": []})))
        self.assertIsNone(R.parse(json.dumps({"v": 1, "kind": "monthly", "date": "x", "notices": []})))


class TestMerge(unittest.TestCase):
    def test_new_notice_becomes_undecided(self):
        records: dict = {}
        self.assertTrue(R.merge(records, _entry("daily", "2026-10-20", "A"), "now"))
        self.assertEqual(records["A"]["status"], None)
        self.assertEqual(records["A"]["reports"], [{"date": "2026-10-20", "kind": "daily"}])
        self.assertEqual(records["A"]["comments"], [])

    def test_decided_notice_keeps_decision(self):
        records = {"A": {"noticeNo": "A", "title": "A", "status": "참가", "owner": "김", "comments": [{"by": "김", "text": "가자"}],
                         "meta": {"score": 80}, "updatedAt": "x"}}
        R.merge(records, _entry("weekly", "2026-10-20", "A"), "now")
        self.assertEqual((records["A"]["status"], records["A"]["owner"], records["A"]["meta"]), ("참가", "김", {"score": 80}))
        self.assertEqual(records["A"]["reports"], [{"date": "2026-10-20", "kind": "weekly"}])

    def test_idempotent_and_one_row_for_both_reports(self):
        records: dict = {}
        e = _entry("daily", "2026-10-21", "A", "B")
        R.merge(records, e, "now")
        once = json.dumps(records, sort_keys=True)
        self.assertFalse(R.merge(records, e, "now"))
        self.assertEqual(json.dumps(records, sort_keys=True), once)
        R.merge(records, _entry("weekly", "2026-10-19", "A"), "now")
        self.assertEqual(records["A"]["reports"], [{"date": "2026-10-19", "kind": "weekly"}, {"date": "2026-10-21", "kind": "daily"}])


class TestMissedPruned(unittest.TestCase):
    def test_old_missed_dropped_but_judged_kept(self):
        today = datetime(2026, 11, 9, 10, 0, tzinfo=KST)
        records = {}
        R.merge(records, _entry("daily", "2026-10-01", "OLD", "JUDGED", "TALKED", deadline="2026-10-05T18:00"), "now", today)
        self.assertEqual(records, {}, "마감 4주 넘은 놓침은 새로 넣지 않는다")
        R.merge(records, _entry("daily", "2026-10-20", "RECENT", deadline="2026-10-25T18:00"), "now", today)
        self.assertIn("RECENT", records, "4주 안 놓침은 남긴다")
        records["JUDGED"] = {"noticeNo": "JUDGED", "status": "불참", "reports": [{"date": "2026-10-01", "kind": "daily"}],
                             "meta": {"deadline": "2026-10-05T18:00"}}
        records["TALKED"] = {"noticeNo": "TALKED", "status": None, "comments": [{"by": "김", "text": "검토"}],
                             "reports": [{"date": "2026-10-01", "kind": "daily"}], "meta": {"deadline": "2026-10-05T18:00"}}
        records["STALE"] = {"noticeNo": "STALE", "status": None, "owner": "", "comments": [],
                            "reports": [{"date": "2026-10-01", "kind": "daily"}], "meta": {"deadline": "2026-10-05T18:00"}}
        self.assertTrue(R.merge(records, _entry("daily", "2026-11-09", "NEW"), "now", today))
        self.assertEqual(sorted(records), ["JUDGED", "NEW", "RECENT", "TALKED"])


class TestLogStatus(unittest.TestCase):
    NOW = datetime(2026, 10, 24, 12, 0, tzinfo=KST)
    REP = {"reports": [{"date": "2026-10-20", "kind": "daily"}]}

    def test_decisions_pass_through(self):
        self.assertEqual(R.log_status({"status": "보류", **self.REP}, self.NOW), "보류")

    def test_undecided_and_missed(self):
        self.assertEqual(R.log_status({"status": None, **self.REP, "meta": {"deadline": "2026-10-24T13:00"}}, self.NOW), "미판단")
        self.assertEqual(R.log_status({"status": None, **self.REP, "meta": {"deadline": "2026-10-24T11:00"}}, self.NOW), "놓침")
        self.assertEqual(R.log_status({"status": None, **self.REP, "meta": {"deadline": "2026-10-24"}}, self.NOW), "미판단",
                         "시각 없는 마감은 그날 끝까지")
        self.assertEqual(R.log_status({"status": "검토중", **self.REP, "meta": {}}, self.NOW), "미판단", "마감 모르면 미판단")

    def test_not_reported_not_decided_is_hidden(self):
        self.assertIsNone(R.log_status({"status": None}, self.NOW))
        self.assertIsNone(R.log_status({"status": "검토중"}, self.NOW))


class TestDecisionsCsv(unittest.TestCase):
    def test_rows_order_and_columns(self):
        records = {
            "A": {"noticeNo": "A", "title": "과학관 전시", "status": "참가", "owner": "김", "tags": ["본업과 맞음", "유사 실적 있음"],
                  "reason": "작년과 비슷", "decidedBy": "김", "decidedAt": "2026-10-19T01:30:00.000Z",
                  "meta": {"institution": "OO시", "keywords": ["과학관"], "score": 72}},
            "B": {"noticeNo": "B", "title": '쉼표, "따옴표"', "status": None, "owner": "",
                  "meta": {"deadline": "2026-10-15T10:00"}, "reports": [{"date": "2026-10-20", "kind": "daily"},
                                                                        {"date": "2026-10-20", "kind": "weekly"}]},
            "C": {"noticeNo": "C", "title": "판단 해제", "status": None, "owner": ""},
        }
        csv = R.decisions_csv(records, datetime(2026, 10, 20, 12, 0, tzinfo=KST))
        self.assertTrue(csv.startswith("﻿판단일시,판단,주된 사유"))
        lines = csv.lstrip("﻿").split("\r\n")
        self.assertEqual(len(lines), 3, "머리글 + 2건 (판단도 보고도 없는 C는 뺌)")
        self.assertTrue(lines[1].startswith(",놓침,,,,,B,"), lines[1])
        self.assertIn('"쉼표, ""따옴표"""', lines[1])
        self.assertTrue(lines[1].endswith(',2026-10-20,"매일, 주간"'), "보고일은 한 번만")
        self.assertTrue(lines[2].startswith('2026-10-19 10:30,참가,"본업과 맞음, 유사 실적 있음",작년과 비슷,김,김,A,'), lines[2])


class TestBackup(unittest.TestCase):
    def test_latest_and_daily_copies_then_prune(self):
        d = Path(tempfile.mkdtemp())
        B.backup_content("participation.json", "old", d, datetime(2026, 9, 1, 3, 0, tzinfo=timezone.utc))
        B.backup_content("participation.json", "mid", d, datetime(2026, 9, 25, 3, 0, tzinfo=timezone.utc))
        B.backup_content("participation.json", "new", d, datetime(2026, 10, 19, 23, 0, tzinfo=timezone.utc))
        self.assertEqual((d / "participation.json").read_text(encoding="utf-8"), "new")
        self.assertEqual(sorted(p.name for p in (d / B.DAILY_DIR).iterdir()),
                         ["participation-2026-09-25.json", "participation-2026-10-20.json"], "한국 날짜, 30일 넘은 것은 지움")

    def test_view_only_file_has_no_daily_copy(self):
        d = Path(tempfile.mkdtemp())
        B.backup_content("판단기록.csv", "x", d, daily=False)
        self.assertEqual(list((d / B.DAILY_DIR).iterdir()), [])

    def test_failure_does_not_raise(self):
        blocker = Path(tempfile.mkdtemp()) / "file"
        blocker.write_text("")
        B.backup_content("participation.json", "x", blocker / "sub")  # 파일 아래에 폴더를 만들 수 없다
        B.backup_content("participation.json", "x", None)

    def test_restore_only_when_missing(self):
        local, bk = Path(tempfile.mkdtemp()), Path(tempfile.mkdtemp())
        (bk / "participation.json").write_text('{"A": {}}', encoding="utf-8")
        self.assertTrue(B.restore_if_missing(local / "participation.json", bk))
        (local / "participation.json").write_text("local", encoding="utf-8")
        self.assertFalse(B.restore_if_missing(local / "participation.json", bk))
        self.assertEqual((local / "participation.json").read_text(encoding="utf-8"), "local")


class TestTeamStoreBackupAndReports(unittest.TestCase):
    def test_save_backs_up_without_csv(self):
        bk = Path(tempfile.mkdtemp())
        store = TeamStore(Path(tempfile.mkdtemp()), backup_dir=bk)
        store.update({"noticeNo": "A", "title": "공고 A", "by": "김", "status": "불참", "tags": ["금액 적음"]})
        self.assertEqual(json.loads((bk / "participation.json").read_text(encoding="utf-8"))["A"]["status"], "불참")
        self.assertTrue((bk / "promoted.json").exists())
        self.assertFalse((bk / "판단기록.csv").exists(), "보기용 CSV는 안 쓴다 — 피드백 시트로 본다 (10/21)")

    def test_new_pc_restores_from_backup(self):
        bk = Path(tempfile.mkdtemp())
        TeamStore(Path(tempfile.mkdtemp()), backup_dir=bk).update({"noticeNo": "A", "by": "김", "status": "참가"})
        fresh = TeamStore(Path(tempfile.mkdtemp()), backup_dir=bk)
        self.assertEqual(fresh.participation["A"]["status"], "참가")

    def test_merge_report(self):
        store = TeamStore(Path(tempfile.mkdtemp()))
        store.update({"noticeNo": "A", "by": "김", "status": "참가"})
        self.assertEqual(store.merge_report(_entry("daily", "2026-10-20", "A", "B")), 1, "새로 생긴 공고는 B 하나")
        self.assertEqual((store.participation["A"]["status"], store.participation["B"]["status"]), ("참가", None))
        again = TeamStore(store.part_path.parent)
        self.assertEqual(again.participation["B"]["reports"], [{"date": "2026-10-20", "kind": "daily"}], "파일에 저장됨")


class FakeGit:
    """bot-state 브랜치 흉내 — ls-tree·cat-file만"""

    def __init__(self, files: dict[str, str], fail: bool = False):
        self.files, self.fail, self.calls = files, fail, []

    def __call__(self, *args: str) -> str:
        self.calls.append(args)
        if self.fail:
            raise subprocess.CalledProcessError(128, ["git", *args], stderr=b"fatal: couldn't find remote ref bot-state")
        if args[0] == "fetch":
            return ""
        if args[0] == "ls-tree":
            return "".join(f"100644 blob {i:040x}\treports/{name}\n" for i, name in enumerate(self.files))
        if args[0] == "cat-file":
            return list(self.files.values())[int(args[2], 16)]
        raise AssertionError(args)


class TestReportSync(unittest.TestCase):
    def test_pulls_and_merges_once(self):
        store = TeamStore(Path(tempfile.mkdtemp()))
        git = FakeGit({"2026-10-20-daily.json": json.dumps(_entry("daily", "2026-10-20", "A", "B")),
                       "broken.json": "not json"})
        sync = ReportSync(store.merge_report, git=git)
        self.assertEqual(sync.pull(), 2)
        self.assertEqual(sync.pull(), 0, "이미 합친 파일은 다시 읽지 않는다")
        self.assertEqual(sum(1 for c in git.calls if c[0] == "cat-file"), 2)

    def test_http_without_git(self):
        from webapp.reportsync import HttpGit
        body = json.dumps(_entry("daily", "2026-11-09", "A")).encode("utf-8")

        class Res:
            def __init__(self, data=None, content=b""):
                self.data, self.content = data, content
            def raise_for_status(self):
                pass
            def json(self):
                return self.data

        class Session:
            def get(self, url, timeout):
                if "api.github.com" in url:
                    return Res([{"name": "2026-11-09-daily.json", "sha": "abc", "download_url": "https://raw/x"}])
                return Res(content=body)

        store = TeamStore(Path(tempfile.mkdtemp()))
        sync = ReportSync(store.merge_report, git=HttpGit(Session()))
        self.assertEqual(sync.pull(), 1)
        self.assertEqual(sync.pull(), 0)

    def test_failure_is_quiet(self):
        store = TeamStore(Path(tempfile.mkdtemp()))
        sync = ReportSync(store.merge_report, git=FakeGit({}, fail=True))
        with self.assertLogs("webapp.reportsync", level="WARNING") as logs:
            self.assertEqual(sync.pull(), 0)
            self.assertEqual(sync.pull(), 0)
        self.assertEqual(len(logs.output), 1, "같은 오류는 한 번만 남긴다")


if __name__ == "__main__":
    unittest.main()


class TestWeeklyBodyOnly(unittest.TestCase):
    """주간 보고는 메일 본문 공고만 판단 기록에(2026-11-23) — 보고 파일을 다시 읽어도 되살아나지 않음."""

    def test_attachment_only_weekly_not_kept(self):
        def notice(no, title, score):
            return {"noticeNo": no, "title": title, "meta": {"keywords": ["전시"], "products": [], "score": score,
                                                             "deadline": "2099-01-02T18:00"}}
        entry = {"kind": "weekly", "date": "2026-11-23", "notices": [
            notice("A", "○○체험관 전시물 제작 설치", 80), notice("B", "○○관 시설 유지관리 용역", 80), notice("C", "△△ 전시 기획", 20)]}
        records: dict = {}
        R.merge(records, entry, "now")
        self.assertEqual(list(records), ["A"], "운영·관리 용역(B)·싱크로율 낮음(C)은 안 쌓음")
        records["D"] = {"noticeNo": "D", "title": "□□ 유지관리 용역", "status": None, "reports": [{"date": "2026-11-23", "kind": "weekly"}],
                        "meta": {"keywords": ["전시"]}}
        records["E"] = {**records["D"], "noticeNo": "E", "status": "불참"}
        records["F"] = {**records["D"], "noticeNo": "F", "reports": [{"date": "2026-11-23", "kind": "daily"}]}
        R.merge(records, entry, "now")
        self.assertEqual(sorted(records), ["A", "E", "F"], "이미 쌓인 첨부 전용(D)은 지움, 판단한 것(E)·매일 보고(F)는 그대로")
