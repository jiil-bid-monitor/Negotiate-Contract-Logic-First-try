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

    def test_four_digit_code_after_line_break(self):
        """제천 공고문 PDF: "측지측량업\n(5021)" — 5021을 못 읽어 5023의 "또는"이 측량용역(8115160401)에 이어졌다."""
        item = ("다. 측량분야는 … 공공측량업(5023) 또는 측지측량업\n(5021)에 등록된 업체로서, 분담이행방식(단독분담 또는 "
                "공동이행분담)으로 참여해야하며, … 직접생산확인증명서[측량용역(분류번호:8115160401)-필수특이사항]를 소지한 업체")
        self.assertEqual([[c for c, _ in b] for b in qualify._or_groups(item)], [["5023", "5021"], ["8115160401"]])

    def test_nonstandard_code_with_held_name(self):
        """2026-10-19 제보: 공고문이 나라장터 업종코드가 아닌 "실내건축공사업(업종코드: 452106)"을 적었고, PDF 줄바꿈에 이름이
        "사업"으로 잘려 미보유로 나왔다. 이름이 보유 면허와 같으면 코드가 달라도 보유."""
        item = ("다. 「건설산업기본법」 제9조 제1항에 따른 실내건축공\n사업(업종코드: 452106) 면허를 단독으로 보유하지 않은 "
                "경우에는, 반드시 해당 면허를 보유한 업체와 공동수급체를 구성하여 참여하여야 합니다.")
        self.assertEqual(qualify._extract_code_requirements(item), [("452106", "실내건축공사업(452106)")])
        result = qualify.evaluate_attachment_text([item], held_codes={"0006", "4990"},
                                                  held_code_names={"0006": "실내건축공사업", "4990": "실내건축공사업"})
        self.assertEqual(result.summary, "자격 충족")

    def test_tax_registration_code_not_held_by_name(self):
        """재검토(2026-10-19): 452106은 국세청 사업자등록 업종코드 — "업태·종목"처럼 사업자등록을 말하면 면허 보유와 별개라
        이름이 같아도 보유로 치지 않고 이름표에 밝힌다."""
        result = qualify.evaluate_attachment_text(
            ["다. 사업자등록증상 업태·종목이 실내건축공사업(업종코드: 452106)인 업체"], held_codes={"0006", "4990"},
            held_code_names={"0006": "실내건축공사업", "4990": "실내건축공사업"})
        self.assertEqual(result.summary, "자격 미달(실내건축공사업[사업자등록 업종](452106))")

    def test_tax_code_in_held_list(self):
        """사용자 결정(2026-10-19): 실내건축공사업 면허 보유라 세무 업종코드 452106도 보유 목록에 넣음 — 사업자등록 문맥이어도 보유."""
        cfg = load_config()
        held, names = qualify.load_held_codes(cfg.held_raw), qualify.load_held_code_names(cfg.held_raw)
        self.assertIn("452106", held)
        result = qualify.evaluate_attachment_text(["다. 사업자등록증상 업태·종목이 실내건축공사업(업종코드: 452106)인 업체"], held, names)
        self.assertEqual(result.summary, "자격 충족")

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


class TestFreeIndustry(unittest.TestCase):
    def test_free_industry_9999_counts_as_held(self):
        """2026-10-21 제보(탑승동 이동여객확인기기 설치사업): 나라장터 '기타자유업종(9999)'은 업종 제한 없음 —
        면허제한에 이것만 와도 미보유로 세면 안 된다."""
        cfg = load_config()
        held_names, held_codes = qualify.load_held_names(cfg.held_raw), qualify.load_held_codes(cfg.held_raw)
        groups = [qualify.LicenseGroup("1", ["기타자유업종/9999"], rows=[["기타자유업종/9999"]])]
        self.assertEqual(qualify.evaluate(groups, held_names, held_codes).missing_groups, [])
        self.assertIsNone(qualify.no_held_license(groups, held_names, held_codes))


if __name__ == "__main__":
    unittest.main()


class TestOrLinkAcrossPdfLayout(unittest.TestCase):
    """2026-10-21 검수: 같은 공고의 hwp는 "또는", pdf는 줄바꿈·글자 띄움 때문에 "필수"로 읽혀 팝업에 같은 요건이 겹쳐 나왔다."""

    def codes(self, item):
        return [[c for c, _ in b] for b in qualify._or_groups(item)]

    def test_or_after_wrapped_description(self):
        """국립부산과학관 4D R26BK01780275 공고서 PDF — 3230이 "필수"로 잡혀 자격 미달이 됐다."""
        item = ("4) ⌜ 영화 및 비디오물의 진흥에 관한 법률 ⌟ 제 57 조에 따른 비디오물제작업 ( 업종코드 : 3244) \n"
                "신고필증을 교부받은 업체 또는 ⌜ 문화산업진흥 기본법 시행령 ｣ 제 11 조에 따른 방송영상독 \n"
                "립제작사 ( 업종코드 : 3230)  신고를 필한 업체 ")
        self.assertEqual(self.codes(item), [["3244", "3230"]])

    def test_letter_spaced_or(self):
        """R26BK01759031 PDF — "7215409901 또 는 전 시 홍 보 관 …"."""
        item = ("1) 실내건축공사업(업종코드 : 4990)으로 입찰 참가 등록한 자로 직접생산확인증명서(전 시 부 스 설 치 및 디 자 인 서 비 스  "
                "7215409901 또 는  전 시 홍 보 관 설 치 및 디 자 인 서 비 스 \n7215409902)를 소지한 자")
        self.assertEqual(self.codes(item), [["4990"], ["7215409901", "7215409902"]])

    def test_or_inside_parenthesis(self):
        """R26BK01764739 제안요청서 — "측지측량업(업종코드: 5021)(또는 공공측량업(업종코드: 5023)), 수치지도제작업(…5029)"."""
        item = ("가. 측지측량업(업종코드: 5021)(또는 공공측량업(업종코드: 5023)), 수치지도제작업(업종코드: 5029), "
                "지하시설물측량업(업종코드 5031)을 모두 등록한 업체로서,")
        self.assertEqual(self.codes(item), [["5021", "5023"], ["5029"], ["5031"]])

    def test_wrapped_line_without_subject_or_stays_and(self):
        """줄바꿈을 넘어도 "또는" 바로 앞이 업체/자가 아니면 잇지 않는다 — "공동수급 또는 단독"."""
        item = "가. 실내건축공사업(업종코드 4990)을 등록한\n업체로서 공동수급 또는 단독으로 참여하며 조형물(6012100201)을 소지한 자"
        self.assertEqual(self.codes(item), [["4990"], ["6012100201"]])


class TestLabelCleanup(unittest.TestCase):
    """2026-10-21 제보: R26BK01759031 팝업에 "①전시부스설치및디자인서비스", 7215409901 이름이 옆 품목("또는 전시홍보관…")."""

    def test_item_mark_inside_label(self):
        got = qualify._extract_code_requirements(
            "직접생산확인증명서를 소지한 자: ①전시부스설치및디자인서비스(7215409901), ②전시홍보관설치서비스(7215409902)")
        self.assertEqual([l for _, l in got], ["전시부스설치및디자인서비스(7215409901)", "전시홍보관설치서비스(7215409902)"])
        self.assertEqual(qualify.drop_item_marks("㉮ 측량업(측지측량업)(5021)"), "측량업(측지측량업)(5021)")

    def test_name_before_code_in_parenthesis(self):
        got = qualify._extract_code_requirements(
            "직접생산확인증명서(전시부스설치및디자인서비스 7215409901 또는 전시홍보관설치및디자인서비스 7215409902)를 소지한 자")
        self.assertEqual([l for _, l in got], ["전시부스설치및디자인서비스(7215409901)", "전시홍보관설치및디자인서비스(7215409902)"])

    def test_name_before_code_letter_spaced_pdf(self):
        got = qualify._extract_code_requirements(
            "직접생산확인증명서(전 시 부 스 설 치 및 디 자 인 서 비 스  7215409901 또 는  전 시 홍 보 관 설 치 및 디 자 인 서 비 스 \n"
            "7215409902)를 소지한 자")
        self.assertEqual([l for _, l in got], ["전시부스설치및디자인서비스(7215409901)", "전시홍보관설치및디자인서비스(7215409902)"])


class TestLearnedNamesAreFallback(unittest.TestCase):
    """2026-10-21 제보: 제천 신월~모산 R26BK01775782 — 공고문엔 "측량용역(분류번호:8115160401)"인데 같은 실행의 다른 공고에서
    가져온 "측량부문은 측량"이 앞서 팝업에 그대로 나왔다. 다른 공고 이름은 이 공고가 코드만 적었을 때만 쓴다."""

    def test_own_label_beats_learned_name(self):
        item = "다. 직접생산확인증명서[측량용역(분류번호:8115160401)-필수특이사항에 공공측량업 또는 측지측량업에 한함]를 소지한 업체"
        r = qualify.evaluate_attachment_text([item], set(), {}, {}, {"8115160401": "측량부문은 측량"})
        self.assertEqual(r.missing_groups[0].allowed_names, ["측량용역(8115160401)"])

    def test_learned_name_fills_bare_code(self):
        item = "다. 세부품명번호 8115160401 직접생산확인증명서를 소지한 업체"
        r = qualify.evaluate_attachment_text([item], set(), {}, {}, {"8115160401": "측량용역"})
        self.assertEqual(r.missing_groups[0].allowed_names, ["측량용역(8115160401)"])
