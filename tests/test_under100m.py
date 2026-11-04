"""1억 미만 공고 폴더 — 대상 고르기·사업금액·중복·실패 재시도. 나라장터·회사 묶음은 가짜."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
import zipfile
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nego.qualify import JointSupply, QualificationResult  # noqa: E402
from nego.region import RegionCheck  # noqa: E402
from nego.screen import Schedule  # noqa: E402
from quantdocs.fill import fill_hwpx  # noqa: E402
from quantdocs.forms_cut import cut  # noqa: E402
from under100m.__main__ import COMPANY_KEYS, TITLE_LABELS, business_amount, folder_name, is_target, run_once  # noqa: E402

NOW = datetime(2026, 11, 4, 10, 0)
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "attachments" / "jeongseon_culture_center_rfp.hwpx"


def notice(no="R26BK00000001", title="○○ 체험관 전시물 제작 설치", assigned=None, estimated=None, ord_="000"):
    return SimpleNamespace(notice_no=no, notice_ord=ord_, title=title, kind="본공고", assigned_budget=assigned,
                           estimated_price=estimated, demand_institution="○○시", notice_institution="○○시", detail_url="https://example.invalid/1")


def candidate(text="", **kw):
    schedule = Schedule(qualification_deadline=None, joint_agreement_deadline=None, bid_deadline=datetime(2026, 11, 20, 18, 0))
    return SimpleNamespace(notice=notice(**kw), is_candidate=True, attachment_text=text, schedule=schedule,
                           qualification=QualificationResult(0, [], True, False), region_check=RegionCheck(),
                           joint=JointSupply(True, None, None, None), text_flags=[])


class TargetTest(unittest.TestCase):
    def test_title_words_ignore_spaces(self):
        self.assertTrue(is_target(candidate(title="역사 전시 관 조성")))
        self.assertTrue(is_target(candidate(title="체험관 리모델링")))
        self.assertFalse(is_target(candidate(title="도서관 냉난방 교체")))

    def test_only_candidates_and_main_notices(self):
        c = candidate()
        c.is_candidate = False
        self.assertFalse(is_target(c))
        c = candidate()
        c.notice.kind = "사전규격"
        self.assertFalse(is_target(c))


class AmountTest(unittest.TestCase):
    def test_document_business_amount_first(self):
        amount, source = business_amount(notice(assigned=50_000_000, estimated=94_000_000), "사 업 비 : 금 103,900,000원")
        self.assertEqual((amount, source), (103_900_000, "공고문 사업비"))

    def test_estimate_alone_is_not_business_amount(self):
        amount, source = business_amount(notice(estimated=94_000_000), "")
        self.assertAlmostEqual(amount, 103_400_000)
        self.assertEqual(source, "추정가격×1.1")

    def test_order_and_thousand_won(self):
        self.assertEqual(business_amount(notice(assigned=70_000_000), "기초금액 80,000,000원")[1], "나라장터 배정예산")
        self.assertEqual(business_amount(notice(), "기초금액 80,000천원")[0], 80_000_000)
        self.assertEqual(business_amount(notice(assigned=1), ""), (None, ""))


class RunOnceTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.out, self.state = self.root / "out", self.root / "state.json"
        self.sent = []
        self.fetches = 0

    def tearDown(self):
        self._tmp.cleanup()

    def fetch(self, no, folder):
        self.fetches += 1
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / "공고문.pdf"
        path.write_bytes(b"%PDF-1.4")
        return SimpleNamespace(files=[path], skipped=[])

    def go(self, cands, fetch=None):
        return run_once(self.out, self.state, 3, NOW, collect_fn=lambda *_: cands, load_vault=lambda: object(),
                        fetch=fetch or self.fetch, notify=self.sent.append)

    def test_makes_folder_once(self):
        c = candidate(assigned=80_000_000)
        result = self.go([c])
        folder = self.out / folder_name(c.notice, NOW)
        self.assertEqual(result["made"], [folder.name])
        self.assertTrue((folder / "공고문" / "공고문.pdf").exists())
        self.assertIn("채운 칸", (folder / "자동입력_확인.txt").read_text(encoding="utf-8") + "채운 칸")
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.go([c])["made"], [])
        self.assertEqual((self.fetches, len(self.sent)), (1, 1))

    def test_correction_gets_new_folder(self):
        self.go([candidate(assigned=80_000_000)])
        self.assertEqual(len(self.go([candidate(assigned=80_000_000, ord_="001")])["made"]), 1)

    def test_over_limit_skipped_and_remembered(self):
        result = self.go([candidate(assigned=100_000_000)])
        self.assertEqual((result["made"], result["over"], self.fetches), ([], 1, 0))
        self.assertEqual(self.go([candidate(assigned=100_000_000)])["over"], 0)

    def test_unknown_amount_still_makes_folder(self):
        self.assertEqual(len(self.go([candidate()])["made"]), 1)
        self.assertIn("확인 필요", self.sent[0])

    def test_failure_retries_then_stops_without_half_folder(self):
        def broken(no, folder):
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "반쪽.pdf").write_bytes(b"x")
            raise RuntimeError("첨부 오류")

        c = candidate(assigned=80_000_000)
        for _ in range(3):
            self.assertEqual(len(self.go([c], broken)["failed"]), 1)
        self.assertEqual(self.go([c], broken)["failed"], [])
        self.assertEqual(list(self.out.iterdir()), [])
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(json.loads(self.state.read_text(encoding="utf-8"))["notices"]["R26BK00000001-000"]["tries"], 3)


class FillLimitTest(unittest.TestCase):
    @unittest.skipUnless(FIXTURE.exists(), "표본 없음")
    def test_only_fixed_company_info_and_notice_values(self):
        vault = SimpleNamespace(info={"회사명": "가나회사", "자본금": "999,999", "대표자": "다라"},
                                history=[{"연월일": "2001-01-01", "내용": "연혁내용"}], docs=[])
        with tempfile.TemporaryDirectory() as tmp:
            forms = cut(FIXTURE, Path(tmp) / "cut")
            form = next(f for f in forms if "일반현황" in f.file)
            out = Path(tmp) / "out.hwpx"
            report = fill_hwpx(Path(tmp) / "cut" / form.file, out, vault, None, keys=COMPANY_KEYS, history=False,
                               extras=[(TITLE_LABELS, "시험 사업")])
            xml = zipfile.ZipFile(out).read("Contents/section0.xml").decode("utf-8")
        self.assertIn("가나회사", xml)
        self.assertNotIn("999,999", xml)
        self.assertNotIn("연혁내용", xml)
        self.assertTrue(report.filled)


if __name__ == "__main__":
    unittest.main()
