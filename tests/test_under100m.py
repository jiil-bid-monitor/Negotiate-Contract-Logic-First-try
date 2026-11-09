"""1억 미만 공고 폴더 — 대상 고르기·사업금액·중복·실패 재시도. 나라장터·회사 묶음은 가짜."""

from __future__ import annotations

import json
import re
import shutil
import sys
import tempfile
import unittest
import zipfile
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nego.qualify import JointSupply, QualificationResult  # noqa: E402
from nego.region import RegionCheck  # noqa: E402
from nego.screen import Schedule  # noqa: E402
from under100m.__main__ import alert_text, business_amount, collect, folder_name, is_target, over_by_api, run_once  # noqa: E402
from under100m.form_fill import fill_form  # noqa: E402
from under100m.form_split import merge_forms, split_forms  # noqa: E402

NOW = datetime(2026, 11, 4, 10, 0)
COMPANY = {"회사": {"name": "가나회사", "ceo": "다라", "addr": "마바시 사아로 1", "bizno": "000-00-00000", "tel": "02-000-0000"},
           "칸": {"자본금": {"빈칸": "", "지일 값": "999,999"}}}
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "attachments" / "jeongseon_culture_center_rfp.hwpx"


def notice(no="R26BK00000001", title="○○ 체험관 전시물 제작 설치", assigned=None, estimated=None, ord_="000"):
    return SimpleNamespace(notice_no=no, notice_ord=ord_, title=title, kind="본공고", assigned_budget=assigned,
                           estimated_price=estimated, demand_institution="○○시", notice_institution="○○시", detail_url="https://example.invalid/1")


def candidate(text="", **kw):
    schedule = Schedule(qualification_deadline=None, joint_agreement_deadline=None, bid_deadline=datetime(2026, 11, 20, 18, 0))
    return SimpleNamespace(notice=notice(**kw), is_candidate=True, category="협상", attachment_text=text, schedule=schedule,
                           qualification=QualificationResult(0, [], True, False), region_check=RegionCheck(),
                           joint=JointSupply(True, None, None, None), text_flags=[])


class TargetTest(unittest.TestCase):
    def test_title_does_not_matter(self):
        self.assertTrue(is_target(candidate(title="체험관 리모델링")))
        self.assertTrue(is_target(candidate(title="도서관 냉난방 교체")))

    def test_over_by_api_uses_business_amount(self):
        self.assertTrue(over_by_api(notice(assigned=100_000_000)))
        self.assertTrue(over_by_api(notice(estimated=94_000_000)))  # 부가세 포함 1.034억
        self.assertFalse(over_by_api(notice(assigned=99_000_000, estimated=90_000_000)))
        self.assertFalse(over_by_api(notice()))  # 금액 모름 — 첨부에서 찾음

    def test_only_candidates_and_main_notices(self):
        c = candidate()
        c.is_candidate = False
        self.assertFalse(is_target(c))
        c = candidate()
        c.notice.kind = "사전규격"
        self.assertFalse(is_target(c))


class CollectTest(unittest.TestCase):
    def test_only_bid_award_methods_are_requested(self):
        seen = {}

        def fake_run(config, now, categories, period):
            seen.update(categories=set(categories), period=period)
            return [], SimpleNamespace(), None

        with patch("nego.config.load_config", return_value=SimpleNamespace()), patch("nego.pipeline.run", fake_run):
            self.assertEqual(collect(NOW, 7, Path("out")), [])
        self.assertEqual(seen["categories"], {"입찰"})  # 적격심사·최저가만
        self.assertEqual(seen["period"][0], datetime(2026, 10, 28, 10, 0))

    def test_alert_names_the_category(self):
        text = alert_text(candidate(assigned=70_000_000), 70_000_000, "나라장터 배정예산", Path("폴더"))
        self.assertIn("(협상)", text.splitlines()[0])
        self.assertIn("공고번호: R26BK00000001-000", text)

    def test_alert_lists_documents_to_issue(self):
        with tempfile.TemporaryDirectory() as tmp, patch("under100m.__main__.COMPANY_DOCS", Path(tmp)):
            (Path(tmp) / "_목록.json").write_text(json.dumps(
                [{"종류": "사업자등록증명", "파일": "a.pdf", "발급일": "2026-03-12"}], ensure_ascii=False), encoding="utf-8")
            text = alert_text(candidate(text="사업자등록증명 1부"), 70_000_000, "나라장터 배정예산", Path("폴더"))
        self.assertIn("새로 발급할 서류", text)
        self.assertIn("- 사업자등록증명 (보유본 2026-03-12) · 공고문에 있음", text)


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
        return run_once(self.out, self.state, 3, NOW, collect_fn=lambda *_: cands, load_profile=lambda: COMPANY,
                        fetch=fetch or self.fetch, notify=self.sent.append)

    def test_makes_folder_once(self):
        c = candidate(assigned=80_000_000)
        result = self.go([c])
        folder = self.out / folder_name(c.notice)
        self.assertEqual(result["made"], [folder.name])
        self.assertTrue(folder.name.startswith("[1억미만] "))
        self.assertTrue((folder / "20261104_공고문_v1.pdf").exists())
        self.assertTrue((folder / "서식자동분리" / "제출서류_체크리스트.txt").exists())
        self.assertTrue((folder / "회사준비서류" / "준비서류_체크리스트.txt").exists())
        self.assertFalse((folder / ".받은 공고").exists())
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.go([c])["made"], [])
        self.assertEqual((self.fetches, len(self.sent)), (1, 1))

    def renotice(self, new):
        first = candidate(assigned=80_000_000)
        self.go([first])
        folder = self.out / folder_name(first.notice)
        mine = folder / "회사준비서류" / "담당자가_넣은_파일.pdf"
        mine.write_bytes(b"mine")
        (folder / "서식자동분리" / "서식01_고친것.hwpx").write_bytes(b"edited")
        result = self.go([new])
        self.assertEqual(len(result["made"]), 1)
        return folder, self.out / result["made"][0]

    def assert_merged(self, old, now):
        self.assertFalse(old.exists())
        self.assertEqual([p.name for p in self.out.iterdir() if p.is_dir()], [now.name])
        self.assertTrue((now / "이전공고" / "20261104_공고문_v1.pdf").exists())
        self.assertTrue((now / "20261104_공고문_v1.pdf").exists())
        self.assertTrue((now / "회사준비서류" / "담당자가_넣은_파일.pdf").exists())
        kept = list((now / "이전공고").glob("서식자동분리_*/서식01_고친것.hwpx"))
        self.assertEqual(len(kept), 1)
        self.assertFalse((now / "서식자동분리" / "서식01_고친것.hwpx").exists())
        self.assertIn("재공고", self.sent[-1])

    def test_correction_updates_same_folder(self):
        self.assert_merged(*self.renotice(candidate(assigned=80_000_000, ord_="001")))

    def test_new_number_with_same_title_updates_same_folder(self):
        self.assert_merged(*self.renotice(candidate(assigned=80_000_000, no="R26BK00000002", title="(재공고) ○○ 체험관 전시물 제작 설치")))

    def test_same_title_other_institution_is_new_folder(self):
        self.go([candidate(assigned=80_000_000)])
        other = candidate(assigned=80_000_000, no="R26BK00000002")
        other.notice.demand_institution = "△△군"
        self.go([other])
        self.assertEqual(len([p for p in self.out.iterdir() if p.is_dir()]), 2)

    def test_old_structure_folder_is_not_merged(self):
        (self.out / "예전폴더").mkdir(parents=True)
        self.state.write_text(json.dumps({"notices": {"R26BK00000001-000": {"status": "made", "folder": "예전폴더", "at": NOW.isoformat()}}}), encoding="utf-8")
        self.go([candidate(assigned=80_000_000, ord_="001")])
        self.assertEqual(list((self.out / "예전폴더").iterdir()), [])

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


SEC = ('<hs:sec xmlns:hs="s" xmlns:hp="p">{}</hs:sec>')


def para(text, attr=""):
    return f'<hp:p paraPrIDRef="0" styleIDRef="0"{attr}><hp:run charPrIDRef="0"><hp:t>{text}</hp:t></hp:run></hp:p>'


def make_hwpx(path, paragraphs):
    """서식 자르기에 필요한 부분만 있는 HWPX — 쪽 설정·그림 파일 포함."""
    first = '<hp:p paraPrIDRef="0"><hp:run charPrIDRef="0"><hp:secPr/></hp:run></hp:p>'
    hpf = ('<opf:package xmlns:opf="o"><opf:manifest><opf:item id="section0" href="Contents/section0.xml"/>'
           '<opf:item id="image1" href="BinData/image1.png"/></opf:manifest><opf:spine><opf:itemref idref="section0"/></opf:spine></opf:package>')
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/hwp+zip")
        z.writestr("Contents/header.xml", '<hh:head xmlns:hh="h" secCnt="1"></hh:head>')
        z.writestr("Contents/content.hpf", hpf)
        z.writestr("Contents/section0.xml", SEC.format(first + "".join(paragraphs)))
        z.writestr("BinData/image1.png", b"x" * 1000)


class FormSampleRuleTest(unittest.TestCase):
    """표본 규칙 — 11/5 첫 실행에서 나온 문제(빈 첫 쪽·붙은 서식·이름 없음·안 채운 표지·큰 파일)."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        make_hwpx(self.dir / "src.hwpx", [
            para("본문"),
            para("【서식 제1호】 사업수행능력평가서 표지", ' pageBreak="1"'),
            para("업 체 명: (대표사)"), para("대 표 자:"), para("주 소:"), para("전화번호:"),
            para("【서식 제7호】", ' pageBreak="1"'), para("제안업체 경영 상태"), para("내용"),
            para("【서식 제7-1호】", ' pageBreak="1"'), para("신용평가등급확인서"), para("내용")])
        self.out = self.dir / "cut"
        self.out.mkdir()
        self.names, _ = split_forms(self.dir / "src.hwpx", self.out)

    def section(self, name):
        return zipfile.ZipFile(self.out / name).read("Contents/section0.xml").decode("utf-8")

    def test_names_and_separate_forms(self):
        self.assertEqual(sorted(self.names), sorted(["서식01_사업수행능력평가서 표지.hwpx", "서식07_제안업체 경영 상태.hwpx", "서식7-1_신용평가등급확인서.hwpx"]))
        self.assertNotIn("신용평가등급확인서", self.section("서식07_제안업체 경영 상태.hwpx"))

    def test_no_blank_first_page(self):
        xml = self.section("서식01_사업수행능력평가서 표지.hwpx")
        self.assertNotIn('pageBreak="1"', xml)
        self.assertLess(xml.index("<hp:secPr"), xml.index("【서식 제1호】"))
        self.assertEqual(xml.count("<hp:p "), 5)  # 쪽 설정만 든 빈 문단을 따로 두지 않음

    def test_unused_images_dropped(self):
        self.assertNotIn("BinData/image1.png", zipfile.ZipFile(self.out / self.names[0]).namelist())

    def test_cover_filled_without_manual_values(self):
        target = self.dir / "filled.hwpx"
        filled = fill_form(self.out / "서식01_사업수행능력평가서 표지.hwpx", target, COMPANY)
        xml = zipfile.ZipFile(target).read("Contents/section0.xml").decode("utf-8")
        for value in ("가나회사", "다라", "마바시 사아로 1", "02-000-0000"):
            self.assertIn(value, xml)
        self.assertNotIn("999,999", xml)
        self.assertEqual(len(filled), 4)

    def test_toc_lines_and_bracket_name_heads(self):
        """앞쪽 서식 목록 줄은 버리고, '[서식 1. 이름] ※ 안내' 머리로 자름 — 목록 뒤 표지는 서식1에(2026-11-09 주진불빛공원)."""
        make_hwpx(self.dir / "toc.hwpx", [
            para("본문"), para("&lt;서식 1&gt; 입찰 참가 신청서(표지 포함)"), para("&lt;서식 2&gt; 위임장"), para("&lt;서식 3&gt; 확약서"),
            para("표지 내용"), para("[서식 1. 입찰 참가신청서]"), para("신청서 내용"),
            para("[서식 2. 위임장] ※ 참여자 모두 작성"), para("위임장 내용"), para("[서식 3. 확약서]"), para("확약서 내용")])
        out = self.dir / "toc_cut"
        names, _ = split_forms(self.dir / "toc.hwpx", out, all_forms=True)
        self.assertEqual(len(names), 3)
        first = zipfile.ZipFile(out / sorted(names)[0]).read("Contents/section0.xml").decode("utf-8")
        self.assertIn("표지 내용", first)
        self.assertIn("신청서 내용", first)
        self.assertNotIn("위임장 내용", first)
        last = zipfile.ZipFile(out / sorted(names)[2]).read("Contents/section0.xml").decode("utf-8")
        self.assertIn("확약서 내용", last)
        self.assertNotIn("표지 내용", last)

    def test_forms_merged_into_one_file(self):
        """서식을 하나로 — 서식마다 구역 하나, 순서 그대로, 그림은 쓰는 것만(2026-11-09 사용자 요청)."""
        filled = []
        for name in sorted(self.names):
            target = self.dir / f"filled_{name}"
            fill_form(self.out / name, target, COMPANY)
            filled.append(target)
        merged = self.dir / "서식_통합.hwpx"
        self.assertEqual(merge_forms(filled, merged), 3)
        z = zipfile.ZipFile(merged)
        self.addCleanup(z.close)
        secs = [z.read(f"Contents/section{i}.xml").decode("utf-8") for i in range(3)]
        self.assertIn("【서식 제1호】", secs[0])
        self.assertIn("【서식 제7호】", secs[1])
        self.assertIn("【서식 제7-1호】", secs[2])
        self.assertIn("가나회사", secs[0])
        hpf = z.read("Contents/content.hpf").decode("utf-8")
        self.assertEqual(hpf.count("<opf:itemref"), 3)
        self.assertIn('secCnt="3"', z.read("Contents/header.xml").decode("utf-8"))
        self.assertNotIn("BinData/image1.png", z.namelist())
        self.assertEqual(z.namelist()[0], "mimetype")

    def test_merge_renumbers_added_paragraph_shapes(self):
        """서식 채우기가 파일마다 더한 문단 모양 번호가 겹치면 새 번호로."""
        def doc(path, extra, text):
            head = ('<hh:head xmlns:hh="h" secCnt="1"><hh:paraProperties itemCnt="3"><hh:paraPr id="0"/><hh:paraPr id="1"/>'
                    f'<hh:paraPr id="2" mark="{extra}"/></hh:paraProperties></hh:head>')
            hpf = ('<opf:package xmlns:opf="o"><opf:manifest><opf:item id="section0" href="Contents/section0.xml"/></opf:manifest>'
                   '<opf:spine><opf:itemref idref="section0"/></opf:spine></opf:package>')
            with zipfile.ZipFile(path, "w") as z:
                z.writestr("mimetype", "application/hwp+zip")
                z.writestr("Contents/header.xml", head)
                z.writestr("Contents/content.hpf", hpf)
                z.writestr("Contents/section0.xml", SEC.format(para(text).replace('paraPrIDRef="0"', 'paraPrIDRef="2"')))
        a, b, c = self.dir / "a.hwpx", self.dir / "b.hwpx", self.dir / "c.hwpx"
        doc(a, "A", "첫 서식")
        doc(b, "B", "둘째 서식")
        with zipfile.ZipFile(c, "w") as z:  # 아무것도 더하지 않은 서식 — 원래 문단 모양(0~1)만
            with zipfile.ZipFile(a) as src:
                for n in ("mimetype", "Contents/content.hpf"):
                    z.writestr(n, src.read(n))
            z.writestr("Contents/header.xml", '<hh:head xmlns:hh="h" secCnt="1"><hh:paraProperties itemCnt="2"><hh:paraPr id="0"/><hh:paraPr id="1"/></hh:paraProperties></hh:head>')
            z.writestr("Contents/section0.xml", SEC.format(para("셋째 서식")))
        merged = self.dir / "m.hwpx"
        merge_forms([a, b, c], merged)
        z = zipfile.ZipFile(merged)
        self.addCleanup(z.close)
        head = z.read("Contents/header.xml").decode("utf-8")
        self.assertIn('<hh:paraPr id="2" mark="A"/>', head)
        self.assertIn('<hh:paraPr id="3" mark="B"/>', head)
        self.assertIn('itemCnt="4"', head)
        self.assertIn('paraPrIDRef="2"', z.read("Contents/section0.xml").decode("utf-8"))
        self.assertIn('paraPrIDRef="3"', z.read("Contents/section1.xml").decode("utf-8"))
        self.assertIn('paraPrIDRef="0"', z.read("Contents/section2.xml").decode("utf-8"))

    def test_staff_forms_and_agent_line(self):
        company = {**COMPANY, "칸": {"E-Mail": {"빈칸": "", "지일 값": "a@b.c"}}}
        cell = ('<hp:p paraPrIDRef="0"><hp:run charPrIDRef="0"><hp:tbl><hp:tr><hp:tc><hp:cellAddr colAddr="0" rowAddr="0"/>'
                '<hp:p paraPrIDRef="0"><hp:run charPrIDRef="0"><hp:t>E-Mail</hp:t></hp:run></hp:p></hp:tc><hp:tc><hp:cellAddr colAddr="1" rowAddr="0"/>'
                '<hp:p paraPrIDRef="0"><hp:run charPrIDRef="0"/></hp:p></hp:tc></hp:tr></hp:tbl></hp:run></hp:p>')
        make_hwpx(self.dir / "people.hwpx", [cell, para("소 속:"), para("대 표 자:")])
        for name, expected in (("서식06_참여인력 이력사항.hwpx", ["줄 소속", "줄 대표자"]), ("서식10_위임장.hwpx", ["표 E-Mail", "줄 대표자"])):
            shutil.copyfile(self.dir / "people.hwpx", self.dir / name)
            filled = fill_form(self.dir / name, self.dir / f"out_{name}", company)
            self.assertEqual([re.sub(" → .*", "", f) for f in filled], expected)

    def test_name_and_ceo_in_one_cell(self):
        cell = ('<hp:p paraPrIDRef="0"><hp:run charPrIDRef="0"><hp:tbl><hp:tr><hp:tc><hp:cellAddr colAddr="0" rowAddr="0"/>'
                '<hp:p paraPrIDRef="0"><hp:run charPrIDRef="0"><hp:t>업 체 명</hp:t></hp:run></hp:p></hp:tc><hp:tc><hp:cellAddr colAddr="1" rowAddr="0"/>'
                '<hp:p paraPrIDRef="0"><hp:run charPrIDRef="0"><hp:t>대표자:             (인)</hp:t></hp:run></hp:p></hp:tc></hp:tr></hp:tbl></hp:run></hp:p>')
        make_hwpx(self.dir / "q.hwpx", [cell])
        fill_form(self.dir / "q.hwpx", self.dir / "q_out.hwpx", COMPANY)
        xml = zipfile.ZipFile(self.dir / "q_out.hwpx").read("Contents/section0.xml").decode("utf-8")
        self.assertRegex(xml, r"가나회사\s+대표자: 다라\s+\(인\)")

    def test_all_forms_even_with_misleading_table(self):
        table = ('<hp:p paraPrIDRef="0"><hp:run charPrIDRef="0"><hp:tbl><hp:tr>'
                 '<hp:tc><hp:cellAddr colAddr="0" rowAddr="0"/><hp:p><hp:run><hp:t>제출 자료</hp:t></hp:run></hp:p></hp:tc>'
                 '<hp:tc><hp:cellAddr colAddr="1" rowAddr="0"/><hp:p><hp:run><hp:t>규격 및 양식</hp:t></hp:run></hp:p></hp:tc></hp:tr><hp:tr>'
                 '<hp:tc><hp:cellAddr colAddr="0" rowAddr="1"/><hp:p><hp:run><hp:t>제안서</hp:t></hp:run></hp:p></hp:tc>'
                 '<hp:tc><hp:cellAddr colAddr="1" rowAddr="1"/><hp:p><hp:run><hp:t>제공된 서식(표지) 사용, A4(210mm)</hp:t></hp:run></hp:p></hp:tc></hp:tr></hp:tbl></hp:run></hp:p>')
        make_hwpx(self.dir / "t.hwpx", [table, para("【서식 제1호】 표지"), para("내용"), para("【서식 제2호】 위임장"), para("내용")])
        out = self.dir / "t"
        out.mkdir()
        names, _ = split_forms(self.dir / "t.hwpx", out, all_forms=True)
        self.assertEqual(sorted(names), ["서식01_표지.hwpx", "서식02_위임장.hwpx"])

    def test_real_form_document(self):
        if not FIXTURE.exists():
            self.skipTest("표본 없음")
        out = self.dir / "real"
        out.mkdir()
        names, _ = split_forms(FIXTURE, out)
        for name in names:
            with zipfile.ZipFile(out / name) as z:
                self.assertIsNone(z.testzip())


if __name__ == "__main__":
    unittest.main()
