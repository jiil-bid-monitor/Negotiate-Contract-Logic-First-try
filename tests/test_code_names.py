"""미보유 자격이 코드만 뜨는 문제 — 코드 이름 찾기 테스트.

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nego import qualify  # noqa: E402
from nego.config import load_config  # noqa: E402

BARE_ITEM = "3) 산업디자인 전문업[업종코드 4440, 4444]로 등록되어 있는 업체"


class TestUnheldCodeNames(unittest.TestCase):
    def test_keyword_inside_bracket_extracts_every_code_without_junk_label(self):
        """실측 표기: "산업디자인 전문업[업종코드 4440, 4442, 4444]" — 예전엔 첫 코드만
        잡히고 이름표가 "3) 산업디자인 전문업[(4440)" 같은 문장 조각이 됐다."""
        item = "3) 산업디자인 전문업[업종코드 4440, 4442, 4444]로 등록되어 있는 업체"
        self.assertEqual(
            qualify._extract_code_requirements(item),
            [("4440", "4440"), ("4442", "4442"), ("4444", "4444")],
        )

    def test_bare_codes_without_lookup_are_marked_unnamed(self):
        result = qualify.evaluate_attachment_text([BARE_ITEM], held_codes=set())
        names = [n for g in result.missing_groups for n in g.allowed_names]
        self.assertIn("이름 미확인(4440)", names)
        self.assertEqual(result.unnamed_codes, ["4440", "4444"])

    def test_lookup_fills_name(self):
        result = qualify.evaluate_attachment_text(
            [BARE_ITEM], held_codes=set(), code_names={"4440": "산업디자인전문회사(제품디자인분야)"}
        )
        names = [n for g in result.missing_groups for n in g.allowed_names]
        self.assertIn("산업디자인전문회사(제품디자인분야)(4440)", names)
        self.assertEqual(result.unnamed_codes, ["4444"])

    def test_name_written_elsewhere_in_same_notice_is_reused(self):
        """공고문엔 코드만, 제안요청서엔 이름까지 적힌 경우."""
        items = [
            "가. 직접생산확인증명서 [3912110702, 4511189301] 소지자",
            "나. 조명용제어장치(세부품명번호 3912110702) 직접생산확인증명서를 소지한 자",
        ]
        result = qualify.evaluate_attachment_text(items, held_codes=set())
        names = {n for g in result.missing_groups for n in g.allowed_names}
        self.assertNotIn("이름 미확인(3912110702)", names)
        self.assertTrue(any("조명용제어장치(3912110702)" in n for n in names), names)
        self.assertEqual(result.unnamed_codes, ["4511189301"])

    def test_dictionary_name_beats_generic_phrase_from_document(self):
        """사용자 제보(R26BK01739064 계열): 문서에서 뽑은 이름표가 "직접생산확인증명서"
        같은 구절이면 사전의 품목명을 써야 한다."""
        item = "나. 직접생산확인증명서 (세부품명번호 7215409901) 소지자"
        result = qualify.evaluate_attachment_text(
            [item], held_codes=set(), code_names={"7215409901": "전시부스설치서비스"}
        )
        self.assertEqual(result.summary, "자격 미달(전시부스설치서비스(7215409901))")

    def test_colon_after_keyword_is_recognized(self):
        result = qualify.evaluate_attachment_text(["다. 세부품명번호: 7215409901"], held_codes=set())
        self.assertTrue(result.checked)
        self.assertEqual(result.unnamed_codes, ["7215409901"])

    def test_item_marker_is_stripped_from_parsed_label(self):
        result = qualify.evaluate_attachment_text(
            ["라. 조형물(세부품명번호 6012100201)을 등록한 업체"], held_codes=set()
        )
        self.assertEqual(result.summary, "자격 미달(조형물(6012100201))")

    def test_name_before_bracket_when_inner_is_label_and_code(self):
        """2026-10-19 제보(제천시 신월~모산 도로개설공사 실시설계용역 R26BK01775782): "직접생산확인증명서[측량용역
        (분류번호:8115160401)-…]"가 "이름 미확인(8115160401)"로 보였다 — 괄호 안이 "…번호:" + 코드뿐이면 괄호 앞 이름."""
        item = ("다. … 직접생산확인증명서[측량용역(분류번호:8115160401)-필수특이사항에 공공측량업 또는 "
                "측지측량업에 한함]를 소지한 업체이어야 합니다.")
        self.assertEqual(qualify._extract_code_requirements(item), [("8115160401", "측량용역(8115160401)")])
        result = qualify.evaluate_attachment_text([item], held_codes=set())
        self.assertEqual(result.unnamed_codes, [])

    def test_four_digit_code_after_industry_name(self):
        """2026-10-19 사용자 결정: "공공측량업(5023) 또는 측지측량업(5021)"처럼 업·사업자·회사·사무소로 끝나는 이름 바로 뒤
        (4자리)는 업종코드. 연도·조항 번호는 그대로 안 읽는다."""
        item = "별표7에서 정하고 있는 공공측량업(5023) 또는 측지측량업(5021)에 등록된 업체"
        self.assertEqual(qualify._or_groups(item), [[("5023", "공공측량업(5023)"), ("5021", "측지측량업(5021)")]])
        self.assertEqual(qualify._extract_code_requirements("2024년(2024) 기준, 제44조(1234)에 따라"), [])

    def test_unclosed_bracket_name_keeps_company_and_field(self):
        """"산업디자인전문회사[환경디자인분야(업종코드 4442)"처럼 대괄호를 안 닫은 표기 — 예전엔 "[" 가 이름표에 그대로 남았다."""
        self.assertEqual(
            qualify._extract_code_requirements("산업디자인전문회사[환경디자인분야(업종코드 4442)"),
            [("4442", "산업디자인전문회사(환경디자인분야)(4442)")],
        )
        self.assertEqual(
            qualify._extract_code_requirements("따른 [산업디자인전문회사(환경디자인분야)(업종코드 4442)]"),
            [("4442", "산업디자인전문회사(환경디자인분야)(4442)")],
        )

    def test_held_code_is_not_reported_as_unnamed(self):
        result = qualify.evaluate_attachment_text([BARE_ITEM], held_codes={"4440", "4444"})
        self.assertEqual(result.missing_count, 0)
        self.assertEqual(result.unnamed_codes, [])

    def test_api_code_names(self):
        groups = [qualify.LicenseGroup("1", ["실내건축공사업/4990", "건축공사업"])]
        self.assertEqual(qualify.api_code_names(groups), {"4990": "실내건축공사업"})

    def test_load_code_names_merges_codes_json_and_dictionary(self):
        names = qualify.load_code_names(
            {"productCodes": [{"code": "6012100201", "name": "조형물"}]},
            {"names": {"3912110702": "조명용제어장치", "6012100201": "조형물(사전)"}},
        )
        self.assertEqual(names["3912110702"], "조명용제어장치")
        self.assertEqual(names["6012100201"], "조형물(사전)", "code_names.json이 우선")

    def test_config_loads_dictionary(self):
        config = load_config()
        self.assertEqual(config.code_names.get("3912110702"), "조명용제어장치")
        self.assertIn("6012100201", config.code_names, "codes.json 이름도 포함")


if __name__ == "__main__":
    unittest.main()
