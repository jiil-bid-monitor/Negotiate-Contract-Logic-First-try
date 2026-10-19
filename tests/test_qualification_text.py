"""참가자격 절 추출 단위 테스트. 표준 라이브러리 unittest만 사용한다.

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nego.qualification_text import find_qualification_section, split_items  # noqa: E402


class TestFindQualificationSection(unittest.TestCase):
    def test_returns_none_when_no_heading(self):
        self.assertIsNone(find_qualification_section("아무 관련 없는 문서 내용입니다."))

    def test_pdf_spaced_numbering(self):
        """2026-10-19 제보(구미시 스타트업 필드 공용공간 리모델링 공사 설계용역 공고문 PDF): 번호가 "3 . 1 . 1 ."처럼
        띄어져 뽑혀 절을 못 찾았다."""
        text = (
            "증서를 제출하여야 합니다 . \n\n"
            "3 .  입찰참가자격 \n"
            " 3 . 1 .  아래의 자격을 모두 갖춘 자 이어야 합니다 . \n"
            "  3 . 1 . 1 .  ｢ 건축사법 ｣ 에 의한 건축사사무소 를 개설하고 국토교통부장관에게 신고를 필한 자 \n"
            "  3 . 1 . 2 .  ｢ 엔지니어링산업진흥법령 ｣ 에 의한 정보통신부문 에 엔지니어링사업자 로 신고를 필한 자 \n"
            "  3 . 1 . 3 .  ｢ 소방시설공사업법령 ｣ 에 의한 전문소방시설설계업 으로 등록한 자 \n"
            "4 .  입찰참가 등록 관련 \n"
        )
        section = find_qualification_section(text)
        self.assertIsNotNone(section)
        self.assertEqual(len(section.items), 4)
        self.assertIn("소방시설설계업", section.items[-1])

    def test_skips_table_of_contents_style_run_on_line(self):
        """목차처럼 줄바꿈 없이 나열된 항목은 줄 시작이 아니라 매칭되면 안 된다."""
        text = (
            "Ⅱ. 제안 일반사항      1. 입찰 및 계약방법      2. 입찰참가자격      3. 세부 제안절차\n"
            "\n"
            "본문 내용...\n"
            "2. 입찰참가자격\n"
            " 가. 진짜 자격 요건입니다.\n"
            " 나. 두 번째 요건입니다.\n"
            "3. 다음 절\n"
        )
        section = find_qualification_section(text)
        self.assertIsNotNone(section)
        self.assertIn("진짜 자격 요건", section.body)
        self.assertNotIn("다음 절", section.body)

    def test_stops_before_next_top_level_heading(self):
        text = "5. 입찰 참가자격\n 가. 요건 하나\n 나. 요건 둘\n6. 입찰내용\n여기는 포함되면 안 됨\n"
        section = find_qualification_section(text)
        self.assertNotIn("포함되면 안 됨", section.body)

    def test_does_not_stop_at_decimal_sub_numbering(self):
        """'3.1.'처럼 소항목 번호는 새 최상위 절로 오인하면 안 된다."""
        text = "3. 입찰참가자격\n 3.1. 요건 하나\n 3.2. 요건 둘\n4. 다음 절\n여긴 빠져야 함\n"
        section = find_qualification_section(text)
        self.assertIn("요건 둘", section.body)
        self.assertNotIn("빠져야 함", section.body)

    def test_captures_to_end_of_text_when_no_next_heading(self):
        text = "5. 입찰 참가자격\n 가. 마지막 절이라 다음 헤딩이 없음\n"
        section = find_qualification_section(text)
        self.assertIn("마지막 절", section.body)

    def test_heading_with_letter_spacing_is_matched(self):
        """실측(사용자 제보, 2026-09-17): 한식진흥원 공고문 — 절 제목 글자 사이마다
        공백을 넣어 자간을 벌린 "2. 입 찰 참 가 자 격" 표기. "입찰"/"참가"/"자격"
        2글자 묶음 사이 공백만 허용하면 이 표기를 놓쳐서 절 전체를 못 찾는다."""
        text = "2. 입 찰 참 가 자 격\n 가. 요건 하나\n 나. 요건 둘\n3. 다음 절\n"
        section = find_qualification_section(text)
        self.assertIsNotNone(section)
        self.assertIn("요건 둘", section.body)

    def test_heading_with_trailing_text_on_same_line_is_matched(self):
        """실측: 두바이 의료기기전시회 한국관 공고문 — "4. 입찰참가자격 : 안내문"처럼
        콜론 뒤에 같은 줄로 안내문이 붙어도 절 제목으로 인식해야 한다."""
        text = (
            "4. 입찰참가자격 : 아래 자격을 모두 충족하는 경우에만 본 입찰에 참여 가능\n"
            "o 첫째 요건\n"
            "o 둘째 요건\n"
            "5. 다음 절\n여긴 빠져야 함\n"
        )
        section = find_qualification_section(text)
        self.assertIsNotNone(section)
        self.assertIn("첫째 요건", section.body)
        self.assertNotIn("빠져야 함", section.body)


class TestSplitItems(unittest.TestCase):
    def test_splits_korean_letter_items(self):
        body = " 가. 첫째\n 나. 둘째\n 다. 셋째\n"
        items = split_items(body)
        self.assertEqual(len(items), 3)
        self.assertTrue(items[0].startswith("가."))
        self.assertTrue(items[1].startswith("나."))

    def test_splits_bullet_items(self):
        """실측: 두바이 의료기기전시회 한국관 공고문 — "o" 불릿으로 나열된 항목."""
        body = "o 첫째 요건\no 둘째 요건\no 셋째 요건\n"
        items = split_items(body)
        self.assertEqual(len(items), 3)
        self.assertTrue(items[0].startswith("o 첫째"))

    def test_splits_bullet_items_indented_with_ideographic_space(self):
        """HWP류 문서는 들여쓰기에 전각 공백(U+3000)을 쓰기도 한다 — 일반
        공백/탭만 인식하면 이런 문서의 불릿을 통째로 항목 1개로 오인한다."""
        body = "　o 첫째 요건\n　o 둘째 요건\n"
        items = split_items(body)
        self.assertEqual(len(items), 2)

    def test_splits_decimal_sub_items(self):
        body = " 3.1. 첫째\n 3.2. 둘째\n"
        items = split_items(body)
        self.assertEqual(len(items), 2)
        self.assertTrue(items[0].startswith("3.1."))

    def test_splits_numbered_paren_items_when_no_letter_or_decimal_markers(self):
        body = " 가. 아래 조건을 모두 충족한 업체\n    1) 첫째 조건\n    2) 둘째 조건\n    3) 셋째 조건\n"
        items = split_items(body)
        self.assertEqual(len(items), 3)
        self.assertTrue(items[0].startswith("1)"))
        self.assertTrue(items[2].startswith("3)"))

    def test_falls_back_to_whole_body_when_no_marker_recognized(self):
        body = "이 문서는 알려진 어떤 목록 기호도 쓰지 않는다."
        self.assertEqual(split_items(body), [body])

    def test_empty_body_returns_empty_list(self):
        self.assertEqual(split_items("   \n  "), [])

    def test_splits_circled_number_items(self):
        """실측: 한국항공우주연구원 나로우주센터 공고(R26BK01710751) — 참가자격
        절이 원문자(①②③④)로 나열됐다."""
        body = " ① 첫째 요건\n ② 둘째 요건\n ③ 셋째 요건\n ④ 넷째 요건\n"
        items = split_items(body)
        self.assertEqual(len(items), 4)
        self.assertTrue(items[0].startswith("①"))
        self.assertTrue(items[3].startswith("④"))

    def test_single_marker_is_not_enough_to_split(self):
        """마커가 1개뿐이면(전체가 '가.' 하나 아래) 그 패턴으론 안 쪼갠다 — 다음 패턴을 시도."""
        body = " 가. 이 항목 하나뿐\n    1) 실제 요건 A\n    2) 실제 요건 B\n"
        items = split_items(body)
        # '가.'는 1개뿐이라 레터 분할은 안 되고, 대신 '1) 2)' 숫자 목록으로 쪼개져야 한다.
        self.assertEqual(len(items), 2)
        self.assertTrue(items[0].startswith("1)"))


class TestRealFixtures(unittest.TestCase):
    """실제 나라장터 첨부파일로 검증 (tests/fixtures/attachments)."""

    FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "attachments"

    def _extract(self, file_name: str) -> str:
        from nego.attachments import extract_text

        path = self.FIXTURES_DIR / file_name
        return extract_text(path.read_bytes(), path.suffix.lstrip("."))

    def test_notice_uses_korean_letter_items(self):
        """정선군 공고문: '5. 입찰 참가자격' 아래 가~차 10개 항목."""
        text = self._extract("jeongseon_culture_center_notice.hwpx")
        section = find_qualification_section(text)
        self.assertIsNotNone(section)
        self.assertEqual(section.heading, "5. 입찰 참가자격")
        self.assertEqual(len(section.items), 10)
        self.assertTrue(section.items[0].startswith("가."))

    def test_rfp_uses_numbered_paren_items_under_single_letter(self):
        """정선군 제안요청서: '가.' 하나 아래 1)~5) 5개 실제 요건."""
        text = self._extract("jeongseon_culture_center_rfp.hwpx")
        section = find_qualification_section(text)
        self.assertIsNotNone(section)
        self.assertEqual(section.heading, "2. 입찰참가자격")
        self.assertEqual(len(section.items), 5)
        self.assertIn("실내건축공사업", section.items[2])

    def test_sejong_hwpx_uses_decimal_items(self):
        """세종 입찰설명서(HWPX): '3. 입찰참가자격' 아래 3.1~3.3, 다음 절(4.) 앞에서 멈춤."""
        text = self._extract("sejong_labor_relations_bid_explanation.hwpx")
        section = find_qualification_section(text)
        self.assertIsNotNone(section)
        self.assertEqual(len(section.items), 3)
        self.assertIn("세종특별자치시", section.items[0])

    def test_task_order_has_no_qualification_section(self):
        """과업지시서에는 참가자격 절 자체가 없다 — None을 반환해야 한다."""
        text = self._extract("jeongseon_culture_center_task_order.hwpx")
        self.assertIsNone(find_qualification_section(text))

    def test_sejong_pdf_finds_heading_but_overruns_boundary(self):
        """알려진 한계 회귀 테스트: PDF는 문단 구분이 없어 다음 절까지 딸려온다.

        완벽한 경계 검출을 요구하지 않는다 — 절 시작과 첫 항목 내용이
        맞는지만 확인한다 (모듈 docstring의 '한계' 참고).
        """
        text = self._extract("sejong_labor_relations_bid_explanation.pdf")
        section = find_qualification_section(text)
        self.assertIsNotNone(section)
        self.assertIn("세종특별자치시", section.items[0])
        self.assertGreater(len(section.items), 3)  # 다음 절(4. 이하)까지 딸려옴 — 알려진 한계


if __name__ == "__main__":
    unittest.main()


class TestSkipsTableOfContents(unittest.TestCase):
    """실측(2026-09-29, 울산박물관 R26BK01748232): 목차의 "2. 입찰참가자격  2"를 절로 착각해
    참가자격이 쪽 번호 한 줄만 잡혔다."""

    def test_body_section_after_toc(self):
        text = "\n".join([
            "목차",
            "  1. 사업 개요   1",
            "  2. 입찰참가자격   2",
            "  3. 제안서 작성 요령   5",
            "1. 사업 개요",
            "사업명: 울산박물관 상설전시실 무장애 관광 콘텐츠 개발",
            "2. 입찰참가자격",
            "① 소프트웨어사업자(디지털콘텐츠개발서비스사업, 업종코드 1469)로 등록한 자",
            "② 산업디자인전문회사(업종코드 4442 또는 4444)로 등록한 자",
            "③ 직접생산확인증명서[세부품명번호 10자리 : 6010989901]를 소지한 자",
            "3. 제안서 작성 요령",
        ])
        sec = find_qualification_section(text)
        self.assertIsNotNone(sec)
        self.assertEqual(len(sec.items), 3)
        self.assertIn("6010989901", sec.items[2])

    def test_toc_only_document_still_returns_something(self):
        sec = find_qualification_section("2. 입찰참가자격   2\n3. 기타   3")
        self.assertIsNotNone(sec)


class TestHeadingVariants(unittest.TestCase):
    """실측(과거 제안요청서 206건): "2. 입찰참가자격" 말고도 여러 제목 형태가 있다."""

    BODY = "\n가. 「지방자치단체를 당사자로 하는 계약에 관한 법률 시행령」 제13조에 의한 자격을 갖춘 자\n나. 실내건축공사업(4990) 등록업체\n"

    def _heading(self, heading, after="3. 제안서 작성 요령"):
        sec = find_qualification_section(heading + self.BODY + after)
        return sec.heading if sec else None

    def test_accepted_forms(self):
        for h in ["3. 참가자격", "나. 입찰참가자격", "3) 입찰참가자격", "Ⅰ. 입찰 참가자격", "□ 입찰 참가 자격",
                  "1.3 입찰참가자격", "1. 입찰 참가 자격 및 제한", "2. 입찰참가자격 및 관련사항",
                  "다. 입찰참가자격 : 다음 조건을 모두 충족한 자", "2. 입찰참가자격[입찰공고문 참조]", "입찰참가자격"]:
            with self.subTest(h=h):
                self.assertIsNotNone(self._heading(h), h)

    def test_rejects_document_list_sentence(self):
        # 제출서류 목록의 한 줄 — 제목이 아니다
        self.assertIsNone(find_qualification_section("붙임서류\n1. 입찰 참가자격을 증명하는 서류 사본 1통\n2. 사업자등록증"))

    def test_korean_letter_section_ends_at_next_letter(self):
        text = "나. 입찰참가자격" + self.BODY.replace("가.", "1)").replace("나.", "2)") + "다. 입찰방법\n총액입찰"
        sec = find_qualification_section(text)
        self.assertNotIn("총액입찰", sec.body)

    def test_prefers_bid_qualification_heading(self):
        text = ("5. 참가자격\n요약: 관련 법령에 따른 자격을 갖춘 업체로서 공고문을 참조하시기 바랍니다\n6. 일정\n"
                "2. 입찰참가자격" + self.BODY + "3. 끝")
        self.assertEqual(find_qualification_section(text).heading, "2. 입찰참가자격")


class TestHeadingAfterBody(unittest.TestCase):
    """실측(2026-09-30, 울산박물관 R26BK01748232): 한글 파일의 절 제목이 본문 뒤에 붙어 나와
    ("…있어야 함2. 입찰참가자격") 제목으로는 목차 줄만 잡혔다. 항목 묶음으로 찾는다.
    (본문 가~타 항목은 사용자가 보내준 원문, 앞뒤 목차·다른 절은 재현용으로 덧붙임)"""

    def setUp(self):
        path = Path(__file__).parent / "fixtures" / "ulsan_museum_heading_after_body.txt"
        self.text = path.read_text(encoding="utf-8")

    def test_finds_item_block_and_strips_glued_heading(self):
        sec = find_qualification_section(self.text)
        self.assertTrue(sec.items[0].startswith("가."))
        self.assertTrue(any("6010989901" in i for i in sec.items))
        self.assertFalse(sec.body.rstrip().endswith("입찰참가자격"))
        self.assertNotIn("A4 용지", sec.body, "다음 절(제안서 작성 요령)의 가. 항목까지 삼키면 안 된다")

    def test_verdict_all_required(self):
        from nego.qualify import evaluate_attachment_text

        items = find_qualification_section(self.text).items
        self.assertEqual(evaluate_attachment_text(items, {"1469", "4444", "6010989901"}, {}).summary, "자격 충족")
        self.assertIn("6010989901", evaluate_attachment_text(items, {"1469", "4442"}, {}).summary)

    def test_evaluation_section_is_not_qualification(self):
        text = "가. 제안서 평가는 참가자격 요건을 갖춘 업체를 대상으로 기술능력평가와 가격평가로 구분\n나. 기술능력 80점"
        self.assertIsNone(find_qualification_section(text))


def test_box_heading_section_ends_at_next_box_heading():
    """안성 고삼호수 R26BK01719354 — "□ 참가자격" 절이 "□ 제안서 접수"·평가 배점표까지 삼키던 것."""
    text = (
        " □ 선정방식 \n   ○ 협상에 의한 계약\n"
        " □ 참가자격 \n   ○ 입찰참가자격: 각 호를 모두 충족하여야 함\n"
        "  - 실내건축공사업(업종코드 4990)을 등록한 업체\n"
        "   ○ 공동도급(분담이행방식)으로 입찰 참여가 가능\n"
        "□ 제안서 접수 \n   ○ 접수일시: 2026년 9월 7일\n"
        "□ 평가항목 및 배점기준\n   ○ 평가항목별 배점 - 전문인력 보유현황 (6점)\n"
    )
    section = find_qualification_section(text)
    assert section is not None
    assert "4990" in section.body
    assert "제안서 접수" not in section.body and "배점" not in section.body
