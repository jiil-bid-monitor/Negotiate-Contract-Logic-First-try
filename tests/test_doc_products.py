"""공고문 세부품명으로 용역 공고 찾기 (2026-10-20, 팀원 레포 10/19 이식) — nego/doc_products.py · webapp/docscan.py ·
mview.notices(doc_products) · 수집기가 '세부품명 미달' 용역 공고만 넘기는지. 나라장터·g2b는 부르지 않는다.

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nego import doc_products  # noqa: E402
from nego.attachments import AttachmentText  # noqa: E402
from tests.test_team_ui import HELD, RUN, card  # noqa: E402
from tests.test_webapp import FakePast, config_with_key, fixture_run  # noqa: E402
from webapp import mview  # noqa: E402
from webapp.collect import Collector  # noqa: E402
from webapp.docscan import DocScan  # noqa: E402
from webapp.store import Store  # noqa: E402

# 휘문고 역사관 조성 2차 공고문 참가자격 (팀원 레포 제보) — 나라장터 분류엔 세부품명이 없고 공고문에만 있다
QUAL_TEXT = """2. 입찰참가자격
가. 「조달사업에 관한 법률 시행령」에 따라 입찰참가자격을 등록한 업체로서 아래 요건을 모두 갖춘 업체
 1) 세부품명번호 8214150201(디자인서비스)으로 경쟁입찰참가자격을 등록한 업체
 2) 세부품명번호 8213160301(동영상제작서비스)으로 경쟁입찰참가자격을 등록한 업체
 3) 세부품명번호 6010989901(실물모형및전시물)으로 경쟁입찰참가자격을 등록한 업체
3. 입찰보증금
"""
HELD_CODES = {"8214150201", "8213160301"}


def _att(name: str, text: str = "", skipped: bool = False) -> AttachmentText:
    a = AttachmentText(seq="1", file_name=name, url="https://x/" + name, ext="hwp", text=text)
    if skipped:
        a.error, a.skipped = "지원하지 않는 형식", True
    return a


class TestRequiredHeldProducts(unittest.TestCase):
    def _run(self, files: list[AttachmentText]):
        by_name = {f.file_name: f for f in files}
        notice = SimpleNamespace(attachments=[{"file_name": f.file_name} for f in files], kind="본공고")
        with mock.patch.object(doc_products, "attachments_for", lambda n, s, c: list(n.attachments)), \
             mock.patch.object(doc_products, "fetch_attachment_text", lambda s, a, timeout=30.0: by_name[a["file_name"]]):
            return doc_products.required_held_products(notice, HELD_CODES, session=None)

    def test_finds_held_product_codes_in_qualification_section(self):
        codes, file_name = self._run([_att("과업지시서.hwp", "과업 내용"), _att("입찰공고문.hwp", QUAL_TEXT)])
        self.assertEqual(codes, ["8214150201", "8213160301"], "지일 등록 품목만, 순서대로")
        self.assertEqual(file_name, "입찰공고문.hwp", "공고문을 먼저 읽는다")

    def test_no_section_reads_at_most_three_files(self):
        files = [_att(f"첨부{i}.hwp", "참가자격 없음") for i in range(5)]
        read: list[str] = []
        by_name = {f.file_name: f for f in files}

        def fetch(s, a, timeout=30.0):
            read.append(a["file_name"])
            return by_name[a["file_name"]]

        notice = SimpleNamespace(attachments=[{"file_name": f.file_name} for f in files], kind="본공고")
        with mock.patch.object(doc_products, "attachments_for", lambda n, s, c: list(n.attachments)), \
             mock.patch.object(doc_products, "fetch_attachment_text", fetch):
            self.assertEqual(doc_products.required_held_products(notice, HELD_CODES, session=None), ([], None))
        self.assertEqual(len(read), doc_products.MAX_FILES)

    def test_skipped_files_do_not_count(self):
        codes, _ = self._run([_att("내역.xlsx", skipped=True), _att("공고문.hwp", QUAL_TEXT)])
        self.assertEqual(codes, ["8214150201", "8213160301"])


class TestDocScan(unittest.TestCase):
    def _wait(self, scan: DocScan) -> None:
        for _ in range(200):
            if not scan.state["running"]:
                return
            time.sleep(0.02)

    def test_scans_once_and_remembers(self):
        path = Path(tempfile.mkdtemp()) / "doc_products.json"
        calls: list[str] = []

        def finder(n, held, session):
            calls.append(n.notice_no)
            return (["8214150201"], "공고문.hwp") if n.notice_no == "A" else ([], None)

        with mock.patch("webapp.docscan.PAUSE_SECONDS", 0):
            scan = DocScan(path, finder=finder)
            notices = [SimpleNamespace(notice_no="A"), SimpleNamespace(notice_no="B")]
            scan.start(notices, HELD_CODES)
            self._wait(scan)
            self.assertEqual(scan.codes(), {"A": ["8214150201"]})
            self.assertEqual((scan.state["done"], scan.state["found"]), (2, 1))
            again = DocScan(path, finder=finder)
            again.start(notices, HELD_CODES)
            self._wait(again)
        self.assertEqual(calls, ["A", "B"], "한 번 읽은 공고는 파일에 남아 다시 읽지 않는다")
        self.assertEqual(again.codes(), {"A": ["8214150201"]})


class TestNoticesWithDocProducts(unittest.TestCase):
    def test_doc_found_notice_goes_to_held_tab(self):
        run = {**RUN, "rejected": RUN["rejected"] + [
            card("R26BK00000030", is_candidate=False, excluded_reason="미매칭 (키워드·품명코드 모두 불일치)",
                 only_keyword_miss=True, work_type="용역", matched_keywords=[]),
            card("R26BK00000031", is_candidate=False, excluded_reason="미매칭 (키워드·품명코드 모두 불일치)",
                 only_keyword_miss=False, work_type="용역", matched_keywords=[]),
        ]}
        doc = {"R26BK00000030": ["6010989901"], "R26BK00000031": ["6010989901"]}
        held = {i["noticeNo"]: i for i in mview.notices(run, {}, HELD, doc)["heldItems"]}
        self.assertIn("R26BK00000030", held)
        self.assertNotIn("R26BK00000031", held, "다른 조건에도 걸린 미매칭은 싣지 않는다")
        row = held["R26BK00000030"]
        self.assertTrue(row["fromDoc"] and row["outside"])
        self.assertEqual((row["productCode"], row["matchedBy"]["products"]),
                         ("6010989901", [{"code": "6010989901", "name": "실물모형및전시물"}]))
        self.assertNotIn("R26BK00000030", {i["noticeNo"] for i in mview.notices(run, {}, HELD)["heldItems"]},
                         "공고문 확인 결과가 없으면 싣지 않는다")


class FakeScan:
    def __init__(self):
        self.got = None

    def start(self, notices, held):
        self.got = (notices, held)


class TestCollectorStartsScan(unittest.TestCase):
    def _collect(self, attachments: bool) -> FakeScan:
        scan = FakeScan()
        collector = Collector(Store(Path(tempfile.mkdtemp()) / "t.sqlite3"), FakePast(), runner=fixture_run,
                              config_loader=config_with_key, docscan=scan)
        job = collector.start(7, attachments, False, None)
        for _ in range(200):
            if not job.running:
                break
            time.sleep(0.02)
        self.assertIsNone(job.error)
        return scan

    def test_only_keyword_miss_service_notices(self):
        scan = self._collect(attachments=True)
        self.assertIsNotNone(scan.got, "첨부를 읽는 수집이면 확인을 시작한다")
        notices, held = scan.got
        self.assertTrue(all(n.work_type == "용역" for n in notices))
        self.assertIn("6010989901", held)

    def test_no_scan_without_attachments(self):
        self.assertIsNone(self._collect(attachments=False).got)


if __name__ == "__main__":
    unittest.main()
