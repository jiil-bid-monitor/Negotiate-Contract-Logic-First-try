"""파이프라인 단위 테스트. 표준 라이브러리 unittest만 사용한다.

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nego import fields as F  # noqa: E402
from nego import qualify, schedule_text, scope, screen  # noqa: E402
from nego.config import load_config  # noqa: E402
from nego.fields import PERSONAL_FIELDS  # noqa: E402
from nego.http_client import ApiError, parse_response_body  # noqa: E402
from nego.models import notice_from_raw  # noqa: E402
from nego.pipeline import RunStats, build_candidates, collect_notices  # noqa: E402
from tests import fixtures  # noqa: E402

NOW = datetime(2026, 9, 14, 9, 0)


def _notices():
    return [notice_from_raw(raw, "용역") for raw in fixtures.service_notices()]


class TestHttpClient(unittest.TestCase):
    def test_items_as_object_with_item_list(self):
        body = '{"response":{"header":{"resultCode":"00","resultMsg":"NORMAL"},' \
               '"body":{"items":{"item":[{"bidNtceNo":"A"},{"bidNtceNo":"B"}]},"totalCount":2}}}'
        env = parse_response_body(body, "test")
        self.assertEqual(len(env.items), 2)
        self.assertEqual(env.total_count, 2)

    def test_items_as_single_object(self):
        body = '{"response":{"header":{"resultCode":"00","resultMsg":"OK"},' \
               '"body":{"items":{"bidNtceNo":"A"},"totalCount":1}}}'
        self.assertEqual(len(parse_response_body(body, "test").items), 1)

    def test_items_empty_string(self):
        body = '{"response":{"header":{"resultCode":"00","resultMsg":"OK"},"body":{"items":"","totalCount":0}}}'
        self.assertEqual(parse_response_body(body, "test").items, [])

    def test_nonstandard_error_envelope_is_found(self):
        """비표준 오류 포맷에서도 resultCode를 찾아내야 한다 (99로 덮어쓰면 안 됨)."""
        body = '{"nkoneps.com.response.ResponseError":{"header":{"resultCode":"30",' \
               '"resultMsg":"SERVICE KEY IS NOT REGISTERED ERROR"}}}'
        self.assertEqual(parse_response_body(body, "test").result_code, "30")

    def test_xml_response_is_parsed(self):
        body = (
            "<response><header><resultCode>00</resultCode><resultMsg>OK</resultMsg></header>"
            "<body><items><item><bidNtceNo>A</bidNtceNo></item></items><totalCount>1</totalCount></body></response>"
        )
        env = parse_response_body(body, "test")
        self.assertEqual(env.result_code, "00")
        self.assertEqual(len(env.items), 1)


class TestScope(unittest.TestCase):
    def test_negotiated_detection_covers_variants(self):
        for award in (fixtures.NEGO, fixtures.NEGO_SW):
            n = notice_from_raw(fixtures.notice("X", award=award), "용역")
            self.assertTrue(scope.is_negotiated(n), award)

    def test_non_negotiated_is_rejected(self):
        n = notice_from_raw(fixtures.notice("X", award=fixtures.QUALIFY), "용역")
        self.assertFalse(scope.is_negotiated(n))

    def test_design_contest_is_adjacent_not_negotiated(self):
        n = notice_from_raw(fixtures.notice("X", award=fixtures.DESIGN_CONTEST), "용역")
        self.assertFalse(scope.is_negotiated(n))
        self.assertTrue(scope.is_adjacent(n))

    def test_variant_extraction(self):
        n = notice_from_raw(fixtures.notice("X", award=fixtures.NEGO_SW), "용역")
        self.assertEqual(scope.negotiation_variant(n), "SW사업")

    def test_categories(self):
        cases = [
            (fixtures.NEGO, "제한경쟁", "협상"),
            (fixtures.NEGO_SW, "일반경쟁", "협상"),
            ("규격가격동시입찰-규격가격동시입찰", "제한경쟁", "규격가격동시입찰"),
            (fixtures.QUALIFY, "제한경쟁", "입찰"),
            (fixtures.DESIGN_CONTEST, "일반경쟁", "입찰"),
            (fixtures.QUALIFY, "수의계약", None),
            (fixtures.QUALIFY, "수의(소액)", None),
        ]
        for award, contract, expected in cases:
            with self.subTest(award=award, contract=contract):
                n = notice_from_raw(fixtures.notice("X", award=award, cntrctCnclsMthdNm=contract), "용역")
                self.assertEqual(scope.bid_category(n), expected)

    def test_private_contract_dropped_competitive_bids_kept(self):
        raws = [
            fixtures.notice("R-NEGO"),
            fixtures.notice("R-BID", award=fixtures.QUALIFY),
            fixtures.notice("R-PRIVATE", award=fixtures.QUALIFY, cntrctCnclsMthdNm="수의계약"),
        ]
        result = scope.apply_scope([notice_from_raw(r, "용역") for r in raws])
        self.assertEqual({n.notice_no for n in result.kept}, {"R-NEGO", "R-BID"})
        self.assertEqual(result.dropped_private, 1)

    def test_category_filter_keeps_only_chosen(self):
        raws = [fixtures.notice("R-NEGO"), fixtures.notice("R-BID", award=fixtures.QUALIFY)]
        result = scope.apply_scope([notice_from_raw(r, "용역") for r in raws], {"입찰"})
        self.assertEqual([n.notice_no for n in result.kept], ["R-BID"])
        self.assertEqual(result.dropped_other_category, 1)

    def test_parse_categories(self):
        self.assertIsNone(scope.parse_categories(""))
        self.assertEqual(scope.parse_categories("입찰, 협상"), {"입찰", "협상"})
        with self.assertRaises(ValueError):
            scope.parse_categories("수의계약")

    def test_cancelled_dropped_and_latest_ordinal_kept(self):
        result = scope.apply_scope(_notices(), {"협상"})
        numbers = {n.notice_no for n in result.kept}
        self.assertNotIn("R26TEST00003", numbers, "취소공고가 남아 있으면 안 된다")
        self.assertNotIn("R26TEST00004", numbers, "협상만 고르면 적격심사 공고는 빠져야 한다")
        ords = {n.notice_no: n.notice_ord for n in result.kept}
        self.assertEqual(ords["R26TEST00002"], "001", "최신 차수만 남아야 한다")
        self.assertEqual(result.dropped_cancelled, 1)
        self.assertEqual(result.dropped_old_ordinal, 1)

    def test_cancelled_notice_does_not_revive_older_ordinal(self):
        """최신 차수가 취소공고면 이전 차수가 되살아나면 안 된다."""
        raws = [
            fixtures.notice("R26REVIVE", "000", title="○○전시관 제작"),
            fixtures.notice("R26REVIVE", "001", title="○○전시관 제작", kind="취소공고"),
        ]
        result = scope.apply_scope([notice_from_raw(r, "용역") for r in raws])
        self.assertEqual([n.notice_no for n in result.kept], [])


class TestJointSupply(unittest.TestCase):
    def test_parses_submit_and_exec_type(self):
        joint = qualify.parse_joint_supply("(전자)공동이행 또는 분담이행")
        self.assertTrue(joint.allowed)
        self.assertEqual(joint.submit_type, "전자")
        self.assertEqual(joint.exec_type, "공동이행 또는 분담이행")

    def test_detects_not_allowed(self):
        joint = qualify.parse_joint_supply("(없음)공동수급불허")
        self.assertFalse(joint.allowed)
        self.assertEqual(joint.submit_type, "없음")

    def test_missing_value(self):
        joint = qualify.parse_joint_supply(None)
        self.assertEqual(joint.label, "정보 없음")


class TestQualification(unittest.TestCase):
    """자격 판정 규칙. 이름 비교는 부분일치가 아니라 코드 비교 + 이름 완전일치(2026-09-30 변경)."""

    held = ["실내건축공사업", "산업디자인전문회사(환경디자인분야)", "전시사업자(전시장치사업자)"]

    def test_bracketed_industry_list_is_split_and_code_removed(self):
        names = qualify.split_industry_list("[건축공사업/0002][토목건축공사업/0001]")
        self.assertEqual(names, ["건축공사업", "토목건축공사업"])

    def test_no_groups_is_fail_open(self):
        result = qualify.evaluate([], self.held)
        self.assertTrue(result.passes)
        self.assertFalse(result.checked)

    def test_all_groups_satisfied(self):
        groups = qualify.group_license_rows(fixtures.license_rows())["R26TEST00002"]
        result = qualify.evaluate(groups, self.held)
        self.assertEqual(result.missing_count, 0)
        self.assertTrue(result.passes)

    def test_one_missing_group_passes(self):
        # 한 그룹 안 두 행 = 둘 다 필요 (그룹끼리는 "또는"이라 요건을 AND로 걸려면 행으로 둔다)
        groups = [qualify.LicenseGroup("1", rows=[["실내건축공사업"], ["전기공사업"]])]
        result = qualify.evaluate(groups, self.held)
        self.assertEqual(result.missing_count, 1)
        self.assertTrue(result.passes, "1개까지는 통과 — 기존 규칙")

    def test_satisfied_groups_tracked_separately_from_missing(self):
        """자격판정 팝업에서 파란 원(충족)도 빨간 원(미달)과 같은 형식으로
        보여주려면 어떤 그룹이 충족됐는지 알아야 한다."""
        groups = [qualify.LicenseGroup("1", rows=[["실내건축공사업"], ["전기공사업"]])]  # 보유 행 + 미보유 행
        result = qualify.evaluate(groups, self.held)
        self.assertEqual([g.allowed_names for g in result.satisfied_groups], [["실내건축공사업"]])
        self.assertEqual([g.allowed_names for g in result.missing_groups], [["전기공사업"]])

    def test_substring_name_does_not_satisfy(self):
        """'실내건축공사업' 보유가 '건축공사업(0002)' 요건을 채우지 않는다.

        예전엔 이름 양방향 부분일치라 충족으로 나왔다(2026-09-30 백령 체험관 증축공사에서 드러남).
        코드 비교 + 이름 완전일치로 바꿈.
        """
        groups = qualify.group_license_rows(fixtures.substring_overmatch_rows())["R26SUBSTR"]
        result = qualify.evaluate(groups, self.held, {"0006", "4990"})
        self.assertEqual(result.missing_count, 1)

    def test_code_match_satisfies_even_if_name_differs(self):
        groups = [qualify.LicenseGroup("1", ["실내건축공사업(표기다름)/4990"])]
        self.assertEqual(qualify.evaluate(groups, [], {"4990"}).missing_count, 0)
        self.assertEqual(qualify.evaluate(groups, [], {"0006"}).missing_count, 1)

    def test_two_missing_groups_excluded(self):
        groups = qualify.group_license_rows(fixtures.license_rows())["R26TEST00009"]
        result = qualify.evaluate(groups, self.held)
        self.assertEqual(result.missing_count, 2)
        self.assertFalse(result.passes, "2개 이상 미충족이면 제외 — 기존 규칙")

    def test_evaluate_attachment_text_passes_when_all_codes_held(self):
        items = [
            "마.「건설산업기본법」 제9조에 따른 실내건축공사업(업종코드 4990)으로 입찰참가 등록한 자",
            "아. 부정당업자 제재 중에 있지 아니한 자",  # 코드 없음 — 판정 대상에서 제외돼야 함
        ]
        result = qualify.evaluate_attachment_text(items, held_codes={"4990"})
        self.assertTrue(result.checked)
        self.assertEqual(result.total_groups, 1, "코드 없는 일반 결격사유는 판정 대상이 아니다")
        self.assertEqual(result.summary, "자격 충족")

    def test_evaluate_attachment_text_fails_when_code_missing(self):
        items = ["마.「건설산업기본법」 제9조에 따른 실내건축공사업(업종코드 4990)으로 입찰참가 등록한 자"]
        result = qualify.evaluate_attachment_text(items, held_codes={"9999"})
        self.assertIn("자격 미달", result.summary)
        self.assertIn("실내건축공사업(4990)", result.summary, "이름과 코드가 함께 표시돼야 한다")

    def test_satisfied_display_scans_full_text_regardless_of_bracket_position(self):
        """실측(사용자 제보, 2026-09-17): "산업디자인 전문업[업종코드 4440, 4442,
        4444]로 등록되어 있는 업체"처럼 키워드가 괄호 안쪽 맨 앞에 오고 코드가
        여러 개 나열되면, 단계별 추출은 첫 코드(4440)만 잡고 나머지(4442, 4444)를
        놓친다(뒤 코드들이 이미 소비된 구간 안에 있어 바깥 규칙이 건너뛴다).
        '충족된 자격' 표시는 그룹 추출과 무관하게 원문 전체에서 보유 코드를
        직접 찾으므로, 그 4442 하나만 보유해도 정상적으로 팝업에 뜬다."""
        item = (
            "3)「산업디자인진흥법 시행규칙」제9조에 의한 산업디자인전문업회사로 "
            "산업디자인 전문업[업종코드 4440, 4442, 4444]로 등록되어 있는 업체"
        )
        result = qualify.evaluate_attachment_text(
            [item], held_codes={"4442"}, held_code_names={"4442": "산업디자인전문회사(환경디자인분야)"}
        )
        self.assertEqual(
            [g.allowed_names for g in result.satisfied_groups],
            [["산업디자인전문회사(환경디자인분야)(4442)"]],
        )

    def test_evaluate_attachment_text_satisfied_label_uses_held_registry_name(self):
        """실측 버그(사용자 제보, 2026-09-17): 첨부문서 원문 파싱은 문서 표기가
        제각각이라 '충족된 자격' 팝업에 코드만 남거나("6010989901") 절차성
        문구가 뒤섞인 이름("환경디자인을 포함한 종합디자인분야)[4444)")이 뜬다.
        코드가 이미 held_codes에 있다는 걸 확인한 뒤이니, 파싱한 이름표 대신
        등록증 원문 이름(held_code_names)을 그대로 쓰면 이 문제가 없어진다."""
        item = "마.「건설산업기본법」 제9조에 따른 실내건축공사업(업종코드 4990)으로 입찰참가 등록한 자"
        result = qualify.evaluate_attachment_text(
            [item], held_codes={"4990"}, held_code_names={"4990": "실내건축공사업"}
        )
        self.assertEqual(
            [g.allowed_names for g in result.satisfied_groups],
            [["실내건축공사업(4990)"]],
        )

    def test_evaluate_attachment_text_satisfied_label_falls_back_without_registry(self):
        """held_code_names를 안 주면(기존 호출부와의 하위호환) 파싱한 이름표를
        그대로 쓴다 — 동작이 바뀌지 않아야 한다."""
        item = "마.「건설산업기본법」 제9조에 따른 실내건축공사업(업종코드 4990)으로 입찰참가 등록한 자"
        result = qualify.evaluate_attachment_text([item], held_codes={"4990"})
        self.assertEqual([g.allowed_names for g in result.satisfied_groups], [["실내건축공사업(4990)"]])

    def test_load_held_code_names_maps_products_and_industries(self):
        held_config = {
            "heldProducts": [{"code": "6010989901", "name": "실물모형및전시물"}],
            "heldIndustries": [{"code": "4990", "name": "실내건축공사업"}],
        }
        self.assertEqual(
            qualify.load_held_code_names(held_config),
            {"6010989901": "실물모형및전시물", "4990": "실내건축공사업"},
        )

    def test_extract_code_requirements_skips_digit_count_filler(self):
        """실측 오류 재현: "세부품명번호 10자리, 4924159701"에서 "10"을 코드로
        잘못 잡으면, 실제로는 보유한 코드(4924159701)인데도 미달로 오판정된다."""
        item = (
            "나.「국가종합전자조달시스템 입찰참가자격등록규정」에 의하여 조합놀이대"
            "(세부품명번호 10자리, 4924159701)을 제조물품으로 입찰참가 등록한 자"
        )
        requirements = qualify._extract_code_requirements(item)
        self.assertEqual([code for code, _ in requirements], ["4924159701"])

        self.assertEqual(qualify.evaluate_attachment_text([item], held_codes={"4924159701"}).summary, "자격 충족")
        fail_summary = qualify.evaluate_attachment_text([item], held_codes={"9999"}).summary
        self.assertIn("자격 미달", fail_summary)
        self.assertIn("(4924159701)", fail_summary)
        self.assertLess(len(fail_summary), 60, "실측 오류 재현: 문장 전체가 그대로 딸려 나오면 안 된다")

    def test_extract_code_requirements_skips_deadline_clause_before_item_name(self):
        """실측 오류 재현(사용자 제보 원문, 2026-09-17): 연결어("에 의하여")와
        괄호 사이에 마감일자 안내 절차 문구("…마감일시까지")가 끼면
        "마감일시까지 조합놀이대"처럼 이름표에 그 문구가 그대로 딸려 나왔다.
        "까지"도 연결어 목록에 추가해 떼어낸다."""
        item = (
            "나.「국가종합전자조달시스템 입찰참가자격등록규정」에 의하여 국가종합전자조달"
            "시스템G2B(나라장터)에 입찰참가자격등록 마감일시까지 조합놀이대"
            "(세부품명번호 10자리, 4924159701)을 제조물품으로 입찰참가 등록한 자"
        )
        requirements = qualify._extract_code_requirements(item)
        self.assertEqual([code for code, _ in requirements], ["4924159701"])
        self.assertEqual(requirements[0][1], "조합놀이대(4924159701)")

        fail_summary = qualify.evaluate_attachment_text([item], held_codes={"9999"}).summary
        self.assertEqual(fail_summary, "자격 미달(조합놀이대(4924159701))")

    def test_extract_code_requirements_merges_code_split_across_pdf_line_wrap(self):
        """실측 오류(사용자 제보, 2026-09-17, 한국항공우주연구원 나로우주센터
        공고 R26BK01710751): PDF는 페이지 폭 기준으로만 줄바꿈해서 "영상정보
        디스플레이장치"라는 한 단어가 줄바꿈에 걸려 "디스플레이" / "장치"로
        반으로 잘렸고, 그 사이에 코드를 담은 괄호가 걸쳐 있었다. 줄 단위로만
        괄호를 찾으면 이 코드를 통째로 놓친다 — 줄바꿈을 합치면 코드 자체는
        찾는다("이름, 코드" 순서라 이름표는 못 붙지만, 그건 held_code_names
        lookup으로 '충족' 팝업에서 별도로 채워진다 — 아래 두 번째 테스트)."""
        item = (
            "④「중소기업제품 구매촉진 및 판로지원에 관한 법률」에 의한 직접생산확인증명서(영상정보디스플레이\n"
            "장치, 4511189301)를 소지한 자"
        )
        requirements = qualify._extract_code_requirements(item)
        self.assertEqual([code for code, _ in requirements], ["4511189301"])

    def test_evaluate_attachment_text_satisfied_label_recovers_code_split_across_line_wrap(self):
        """위 테스트가 확인한 코드 추출과, held_code_names lookup(앞서 구현한
        '충족된 자격' 이름표 기능)을 합치면 실제 실측 문서에서도 최종적으로
        올바른 이름("영상정보디스플레이장치(4511189301)")이 나와야 한다."""
        item = (
            "④「중소기업제품 구매촉진 및 판로지원에 관한 법률」에 의한 직접생산확인증명서(영상정보디스플레이\n"
            "장치, 4511189301)를 소지한 자"
        )
        result = qualify.evaluate_attachment_text(
            [item], held_codes={"4511189301"}, held_code_names={"4511189301": "영상정보디스플레이장치"}
        )
        self.assertEqual(result.summary, "자격 충족")
        self.assertEqual(
            [g.allowed_names for g in result.satisfied_groups], [["영상정보디스플레이장치(4511189301)"]]
        )

    def test_extract_code_requirements_preserves_multi_word_item_name(self):
        """이름을 "마지막 한 단어"로 단순화하면 안 된다 — "실물모형 및 전시물"처럼
        여러 단어로 된 실제 품목명이 있고, 한 단어만 남기면("전시물") 정보가
        없어진다."""
        item = "실물모형 및 전시물(세부품명번호 6010989901)"
        requirements = qualify._extract_code_requirements(item)
        self.assertEqual(requirements, [("6010989901", "실물모형 및 전시물(6010989901)")])

    def test_extract_code_requirements_generalizes_connector_and_strips_quotes(self):
        """실측 오류 재현: "규정에 따라"만 알던 연결어 정규식이 "「무슨무슨법」
        제5조에 따라"처럼 다른 인용구 뒤의 "에 따라"는 못 걸렀고, 인용부호로
        감싼 품목명("조합놀이대")의 따옴표도 그대로 남았다."""
        item = '「무슨무슨법」 제5조에 따라 "조합놀이대"(세부품명번호 4924159701)를 소지한 자'
        requirements = qualify._extract_code_requirements(item)
        self.assertEqual(requirements, [("4924159701", "조합놀이대(4924159701)")])

    def test_extract_code_requirements_recognizes_bare_10_digit_code(self):
        """실측: 두바이 의료기기전시회 한국관 공고문 — "세부품명번호" 키워드 없이
        "이름(코드)"만 쓴 문서. 정확히 10자리일 때만 코드로 인정해 연도·조항
        번호 같은 일반 괄호 숫자를 코드로 오인하지 않게 한다."""
        item = "o 전시부스설치서비스(7215409901) 소지 업체"
        requirements = qualify._extract_code_requirements(item)
        self.assertEqual([code for code, _ in requirements], ["7215409901"])

        result = qualify.evaluate_attachment_text([item], held_codes={"7215409901"})
        self.assertEqual(result.summary, "자격 충족")

    def test_extract_code_requirements_ignores_short_bare_parens(self):
        """4자리 등 10자리가 아닌 괄호 숫자는(예: 법조문 인용) 코드로 보지 않는다."""
        item = "o 국가를 당사자로 하는 계약에 관한 법률(2024) 제12조에 따른 자"
        self.assertEqual(qualify._extract_code_requirements(item), [])

    def test_evaluate_attachment_text_or_group_needs_only_one_code(self):
        items = [
            "바. 다음 중 어느 하나의 자격으로 입찰참가 등록한 자\n"
            "    -「산업디자인진흥법」 제9조에 따른 산업디자인전문회사(환경디자인 분야, 업종코드 4442)\n"
            "    -「공공디자인의 진흥에 관한 법률」 제18조에 따른 공공디자인전문회사(업종코드 6484)"
        ]
        result = qualify.evaluate_attachment_text(items, held_codes={"6484"})
        self.assertEqual(result.summary, "자격 충족", "OR그룹은 코드 하나만 있어도 충족")

    # 실측: 2026 대한민국 지방시대 엑스포(R26BK01739064) — 키워드 뒤 콜론 때문에
    # 이름표가 코드만 남고, "또는" 나열을 AND로 봐서 보유 품목이 있는데도 미달로 떴다.
    _EXPO_ITEM = (
        "가. 아래 직접생산확인증명서를 소지한 자\n"
        " · 전시부스설치및디자인서비스(세부품명번호 : 7215409901) 또는\n"
        " 전시홍보관설치및디자인서비스(세부품명번호 : 7215409902)이 기재된 직접생산확인증명서"
    )

    def test_extract_code_requirements_allows_colon_after_keyword(self):
        self.assertEqual(
            qualify._extract_code_requirements(self._EXPO_ITEM),
            [
                ("7215409901", "전시부스설치및디자인서비스(7215409901)"),
                ("7215409902", "전시홍보관설치및디자인서비스(7215409902)"),
            ],
        )

    def test_extract_code_requirements_label_ignores_previous_requirement_on_same_line(self):
        one_line = self._EXPO_ITEM.replace(" 또는\n", " 또는 ")
        labels = [label for _, label in qualify._extract_code_requirements(one_line)]
        self.assertEqual(labels[1], "전시홍보관설치및디자인서비스(7215409902)")

    def test_evaluate_attachment_text_or_between_codes_needs_only_one(self):
        result = qualify.evaluate_attachment_text([self._EXPO_ITEM], held_codes={"7215409902"})
        self.assertEqual(result.summary, "자격 충족", "코드 뒤 '또는'은 OR")

    _MIXED_ITEM = (
        "가. 다음 직접생산확인증명서를 모두 소지한 자\n"
        "  - 전시부스설치서비스(세부품명번호 7215409901) 또는 전시홍보관설치서비스(세부품명번호 7215409902)\n"
        "  - 조명용제어장치(세부품명번호 3912110702)"
    )

    def test_evaluate_attachment_text_or_pair_inside_all_list_still_needs_other_codes(self):
        result = qualify.evaluate_attachment_text([self._MIXED_ITEM], held_codes={"7215409902"})
        self.assertEqual(result.summary, "자격 미달(조명용제어장치(3912110702))",
                         "'또는' 묶음은 충족이어도 '모두' 안의 나머지 미보유는 미달")

    def test_evaluate_attachment_text_or_pair_inside_all_list_passes_when_rest_held(self):
        result = qualify.evaluate_attachment_text([self._MIXED_ITEM], held_codes={"7215409902", "3912110702"})
        self.assertEqual(result.summary, "자격 충족")

    def test_or_after_double_closing_brackets(self):
        """2026-10-13: "[이름(…)(업종코드 1478)] 또는 [이름(업종코드 4119)]"는 닫는 괄호가 ")]" 두 겹이라 "또는"을 못 읽고
        셋 다 필요(AND)로 봤다. 하나만 보유해도 충족이어야 한다."""
        item = ("가. 「산림자원의 조성 및 관리에 관한 법률」에 따른 [산림사업법인(자연휴양림등 조성)(업종코드 1478)] 또는 "
                "[산림조합(지역조합)(업종코드 4119)] 또는 [산림조합중앙회(업종코드 4125)]를 등록한 업체")
        self.assertEqual([[c for c, _ in g] for g in qualify._or_groups(item)], [["1478", "4119", "4125"]])
        self.assertEqual(qualify.evaluate_attachment_text([item], held_codes={"4119"}).summary, "자격 충족")
        # 나라장터 화면 문구처럼 "] 업종 또는"
        item = "가. [산림사업법인(업종코드 1478)] 업종 또는 [산림조합(업종코드 4119)] 업종을 등록한 업체"
        self.assertEqual([[c for c, _ in g] for g in qualify._or_groups(item)], [["1478", "4119"]])
        # 괄호 속 허용업종은 대신 인정 — 과거 문서(2019 원주 거돈사지·2023 도담체험관 등) 표기
        item = ("⑥ 비디오물제작업[업종코드 3244](허용업종코드 : 음반·음악영상물제작업[업종코드 3243] 또는 "
                "문화산업전문회사[업종코드 3276]) 또는 소프트웨어사업자(디지털콘텐츠개발서비스사업)[업종코드 1469]로 등록한 자")
        self.assertEqual([sorted(c for c, _ in g) for g in qualify._or_groups(item)], [["1469", "3243", "3244", "3276"]])
        # 코드 뒤 서술이 "업체/자"로 끝나고 "또는" — 2026-10-13 제보 국립부산과학관 4D 체험관(3244 보유인데 미달로 나왔음)
        item = ("라. ⌜영화 및 비디오물의 진흥에 관한 법률⌟ 제57조에 따른 비디오물제작업(업종코드: 3244) 신고필증을 교부받은 업체 또는 "
                "⌜문화산업진흥 기본법 시행령｣ 제11조에 따른 방송영상독립제작사(업종코드: 3230) 신고를 필한 업체")
        self.assertEqual([[c for c, _ in g] for g in qualify._or_groups(item)], [["3244", "3230"]])
        self.assertEqual(qualify.evaluate_attachment_text([item], held_codes={"3244"}).summary, "자격 충족")
        # "업체로서 공동수급 또는 단독"처럼 "또는" 바로 앞이 업체/자가 아니면 잇지 않는다
        item = "가. 실내건축공사업(업종코드 4990)을 등록한 업체로서 공동수급 또는 단독으로 참가하며 산림조합(업종코드 4119)을 등록한 자"
        self.assertEqual([[c for c, _ in g] for g in qualify._or_groups(item)], [["4990"], ["4119"]])
        # "과/와"로 이으면 그대로 모두 필요
        item = "가. [실내건축공사업(업종코드 4990)]과 [산림조합(업종코드 4119)]을 모두 등록한 자"
        self.assertEqual([[c for c, _ in g] for g in qualify._or_groups(item)], [["4990"], ["4119"]])

    def test_evaluate_attachment_text_comma_list_ending_in_or_is_one_or_group(self):
        item = "가. A(세부품명번호 1111111111), B(세부품명번호 2222222222) 또는 C(세부품명번호 7215409902)"
        result = qualify.evaluate_attachment_text([item], held_codes={"7215409902"})
        self.assertEqual(result.summary, "자격 충족", "'A, B 또는 C'는 셋 중 하나")

    def test_evaluate_attachment_text_unrelated_or_word_keeps_and(self):
        items = [
            "라. 공동수급 또는 단독으로 참가하는 자로서 다음을 모두 소지한 자\n"
            "    - 실물모형 및 전시물(세부품명번호 6010989901)\n"
            "    - 책장(세부품명번호 5610150701)"
        ]
        result = qualify.evaluate_attachment_text(items, held_codes={"6010989901"})
        self.assertIn("자격 미달", result.summary, "코드와 무관한 '또는'은 OR로 보지 않는다")

    def test_evaluate_attachment_text_and_group_needs_every_code(self):
        items = [
            "라. 다음 직접생산확인증명서를 모두 소지한 자이어야 합니다.\n"
            "    - 실물모형 및 전시물(세부품명번호 6010989901)\n"
            "    - 책장(세부품명번호 5610150701)"
        ]
        result = qualify.evaluate_attachment_text(items, held_codes={"6010989901"})
        self.assertIn("자격 미달", result.summary, "AND그룹은 하나라도 빠지면 미달")

    def test_evaluate_attachment_text_no_codes_found_stays_unchecked(self):
        items = ["아. 부정당업자 제재 중에 있지 아니한 자"]
        result = qualify.evaluate_attachment_text(items, held_codes={"4990"})
        self.assertFalse(result.checked, "코드가 명시된 항목이 하나도 없으면 판정하지 않는다")

    def test_extract_code_requirements_finds_all_codes_sharing_one_paren(self):
        """실측 오류 재현: "세부품명번호 10자리(코드1 이름1, 코드2 이름2, 코드3 이름3)"처럼
        프리픽스 하나 뒤에 괄호 하나를 공유하는 코드 여러 개가 있으면, 기존 정규식은
        프리픽스당 코드 하나만 잡아 나머지가 조용히 빠졌다 — 보유하지 않은 코드가
        빠진 채로 "자격 충족"이라고 잘못 표시되는 원인이었다(단양군 미디어아트 공고문)."""
        item = (
            "1) 국가종합전자조달시스템 입찰참가자격 등록규정에 따라 반드시 입찰(개찰) 전일까지 "
            "나라장터(G2B)에 세부품명번호 10자리(4511161601 비디오프로젝터, 3911160501 LED경관조명기구, "
            "3912110702 조명용제어장치) 제조 또는 공급으로 입찰참가 등록한 업체로 입찰일(낙찰자는 "
            "계약체결일)까지 당해 자격을 계속 유지하여야 합니다."
        )
        requirements = qualify._extract_code_requirements(item)
        self.assertEqual(
            [code for code, _ in requirements], ["4511161601", "3911160501", "3912110702"]
        )

        result = qualify.evaluate_attachment_text([item], held_codes={"4511161601", "3911160501"})
        self.assertIn("자격 미달", result.summary, "코드 하나(3912110702)가 없으면 미달이어야 한다")
        self.assertIn("조명용제어장치(3912110702)", result.summary)

    def test_extract_code_requirements_finds_bracket_codes_without_keyword(self):
        """실측 오류 재현: 직접생산확인증명서 항목이 "업종코드"/"세부품명번호" 키워드 없이
        "[코드, 이름]" 대괄호만 쓰면 코드가 하나도 안 잡혔다(위와 같은 공고문의 2번 항목)."""
        item = (
            "2) 「중소기업제품 구매촉진 및 판로지원에 관한 법률」제9조 및 같은 법 시행령 제10조에 "
            "의한 직접생산확인증명서[3911160501, LED경관조명기구], [3912110702, 조명용제어장치]를 "
            "소지한 업체"
        )
        requirements = qualify._extract_code_requirements(item)
        self.assertEqual([code for code, _ in requirements], ["3911160501", "3912110702"])

        result = qualify.evaluate_attachment_text([item], held_codes={"3911160501"})
        self.assertIn("자격 미달", result.summary)
        self.assertIn("조명용제어장치(3912110702)", result.summary)

    def test_summary_deduplicates_same_missing_label_across_groups(self):
        """단양군 공고문처럼 항목 여러 개가 같은 미보유 코드를 요구하면, 같은
        이름표가 그대로 두 번 표시되지 않고 한 번만 나와야 한다."""
        items = [
            "1) 세부품명번호 10자리(4511161601 비디오프로젝터, 3912110702 조명용제어장치) 제조 업체",
            "2) 직접생산확인증명서[3912110702, 조명용제어장치]를 소지한 업체",
        ]
        result = qualify.evaluate_attachment_text(items, held_codes={"4511161601"})
        self.assertEqual(result.summary, "자격 미달(조명용제어장치(3912110702))")

    def test_extract_code_requirements_handles_semicolon_delimited_group(self):
        """프리픽스 공유형 괄호는 콤마뿐 아니라 세미콜론 등 다른 구분자를 써도
        코드를 전부 뽑아야 한다 — 문서마다 구분자가 다를 때마다 정규식을 새로
        추가하지 않기 위한 일반화."""
        item = "나.「국가종합전자조달시스템 입찰참가자격등록규정」에 의하여 세부품명번호(1234567890; 9876543210)을 소지한 업체"
        requirements = qualify._extract_code_requirements(item)
        self.assertEqual([code for code, _ in requirements], ["1234567890", "9876543210"])

    def test_merge_results_flips_api_pass_to_fail_when_attachment_finds_missing_code(self):
        """실측 버그(단양군 미디어아트 R26BK01731335): API 면허제한정보에는 면허·업종만
        있고 세부품명번호 필드가 아예 없다(fields.LICENSE_LIMIT_FIELDS). 그래서 API가
        '업종 4그룹 전부 충족'이라 해도 첨부파일이 요구하는 품목(직접생산확인)이 빠진
        채로 자격 충족이 돼버렸다. 둘 중 하나라도 미충족이면 미충족이어야 한다."""
        api = qualify.QualificationResult(
            total_groups=4, missing_groups=[], passes=True, checked=True
        )
        attachment = qualify.evaluate_attachment_text(
            ["1) 세부품명번호 10자리(4511161601 비디오프로젝터, 3912110702 조명용제어장치) 제조 업체"],
            held_codes={"4511161601"},
        )
        merged = qualify.merge_results(api, attachment)
        self.assertEqual(merged.summary, "자격 미달(조명용제어장치(3912110702))")
        self.assertEqual(merged.total_groups, 5, "양쪽 그룹 수를 합산한다")

    def test_merge_results_keeps_api_verdict_when_attachment_has_no_codes(self):
        api = qualify.QualificationResult(
            total_groups=4, missing_groups=[], passes=True, checked=True
        )
        attachment = qualify.evaluate_attachment_text(["아. 부정당업자 제재 중에 있지 아니한 자"], set())
        self.assertIs(qualify.merge_results(api, attachment), api)

    def test_merge_results_uses_attachment_when_api_gave_nothing(self):
        api = qualify.QualificationResult(
            total_groups=0, missing_groups=[], passes=True, checked=False
        )
        attachment = qualify.evaluate_attachment_text(
            ["마. 실내건축공사업(업종코드 4990)으로 입찰참가 등록한 자"], held_codes={"4990"}
        )
        self.assertEqual(qualify.merge_results(api, attachment).summary, "자격 충족")

    def test_extract_code_requirements_handles_bare_parens_without_keyword(self):
        """키워드 없이 괄호(대괄호가 아니라)만으로 코드를 표기해도 잡아야 한다 —
        지금까지는 대괄호 표기만 지원했는데, 문서마다 괄호/대괄호가 섞여 쓰이므로
        일반화한다."""
        item = "직접생산확인증명서(LED경관조명기구 3911160501)를 소지한 업체"
        requirements = qualify._extract_code_requirements(item)
        self.assertEqual([code for code, _ in requirements], ["3911160501"])

    def test_summary_is_plain_pass_when_all_groups_satisfied(self):
        groups = qualify.group_license_rows(fixtures.license_rows())["R26TEST00002"]
        result = qualify.evaluate(groups, self.held)
        self.assertEqual(result.summary, "자격 충족")

    def test_summary_lists_missing_license_names(self):
        # 행의 맨 앞이 대표 면허, 뒤는 대신 인정 업종 — 요약엔 대표 면허만 (대체 인정은 팝업에서)
        groups = [qualify.LicenseGroup("1", rows=[["실내건축공사업"], ["전기공사업", "정보통신공사업"]])]
        result = qualify.evaluate(groups, self.held)
        self.assertEqual(result.summary, "자격 미달(전기공사업)")

    def test_summary_dedupes_name_repeated_within_one_group_before_matching_other_groups(self):
        """실측 재현(Run #45, R26BK01731335): 첨부문서 한 항목 안에서 같은 코드가
        중복 추출되면 그 그룹의 allowed_names 자체가 ["A", "A"]가 된다. 이걸
        그대로 "/"로 이으면 "A/A"가 되어, 다른 그룹의 단순 "A"와 문자열이 달라
        중복 제거를 피해간다 — "자격 미달(A/A, A)"로 잘못 표시됐다."""
        name = "조명용제어장치(3912110702)"
        groups = [
            qualify.LicenseGroup("1", [name, name]),
            qualify.LicenseGroup("2", [name]),
        ]
        result = qualify.evaluate(groups, self.held)
        self.assertEqual(result.summary, f"자격 미달({name})")


class TestScreen(unittest.TestCase):
    config = screen.ScreenConfig(
        keywords=["전시관", "박물관", "과학관", "체험관", "전시디자인", "전시홍보관", "미디어아트", "전시콘텐츠"],
        exclude_keywords=["유지보수", "정비", "진단"],
        min_budget_amount=100_000_000,
        product_codes=[],
        industry_codes=[],
    )

    def _screen(self, raw):
        return screen.screen(notice_from_raw(raw, "용역"), self.config)

    def test_exclude_keyword_blocks(self):
        result = self._screen(fixtures.notice("X", title="○○박물관 전시물 유지보수 용역"))
        self.assertFalse(result.matched)
        self.assertEqual(result.excluded_by, "제외키워드")

    def test_conflict_goes_to_review_when_enabled(self):
        """관심·제외 키워드 공존 → 웹앱(review_conflicts)에선 "검토필요"로 남긴다. 기본(CLI)은 예전처럼 제외."""
        from dataclasses import replace

        raw = fixtures.notice("X", title="체험관 전시연출 설계 및 제작설치 정비 용역")
        on = screen.screen(notice_from_raw(raw, "용역"), replace(self.config, review_conflicts=True))
        self.assertTrue(on.matched)
        self.assertEqual((on.confidence, on.review_exclude, on.matched_keywords), ("검토필요", "정비", ["체험관"]))
        self.assertEqual(self._screen(raw).excluded_by, "제외키워드")
        only_exclude = fixtures.notice("Y", title="청사 정비 용역")
        self.assertEqual(screen.screen(notice_from_raw(only_exclude, "용역"),
                                       replace(self.config, review_conflicts=True)).excluded_by, "제외키워드")
        cheap = fixtures.notice("Z", title="체험관 정비", presmptPrce="50000000")
        self.assertEqual(screen.screen(notice_from_raw(cheap, "용역"),
                                       replace(self.config, review_conflicts=True)).excluded_by, "최소예산", "예산 조건은 그대로")

    def test_hard_exclude_ignores_interest_keyword(self):
        """특별전·순회전은 무조건 제외 — 관심 키워드(박물관)가 있어도 웹앱 "검토 필요"로 올리지 않는다(2026-10-01)."""
        from dataclasses import replace

        from nego.config import load_config

        real = load_config().screen
        self.assertEqual(sorted(set(real.hard_exclude_keywords) & {"특별전", "순회전"}), ["순회전", "특별전"])
        self.assertFalse({"특별전", "순회전"} & set(real.exclude_keywords))
        config = replace(self.config, hard_exclude_keywords=["특별전", "순회전"], review_conflicts=True)
        for title in ("국립○○박물관 특별전 전시 연출 및 제작설치", "○○박물관 순회전 전시물 제작"):
            res = screen.screen(notice_from_raw(fixtures.notice("H", title=title), "용역"), config)
            self.assertEqual((res.matched, res.excluded_by), (False, "제외키워드"), title)

    def test_godohwa_is_conditional_exclude(self):
        """'고도화' 단독은 조건부 제외(2026-10-01) — 관심 키워드가 있으면 웹앱 "검토 필요", IT 표현은 그대로 제외."""
        from dataclasses import replace
        from nego.config import load_config

        config = replace(load_config().screen, review_conflicts=True)
        museum = screen.screen(notice_from_raw(fixtures.notice(
            "G", title="[경기북부어린이박물관 전시고도화사업] 상설전시 개편 설계 및 체험전시물 제작·설치"), "용역"), config)
        self.assertEqual((museum.confidence, museum.review_exclude), ("검토필요", "고도화"))
        homepage = screen.screen(notice_from_raw(fixtures.notice("H", title="○○군청 홈페이지 고도화 용역"), "용역"), config)
        self.assertEqual((homepage.excluded_by, homepage.excluded_reason), ("제외키워드", "홈페이지 고도화"))

    def test_product_code_match_alone_is_not_review(self):
        """관심 품명코드만 맞고 관심 키워드가 없으면 제외 키워드대로 제외 — "검토 필요"는 관심 키워드가 있을 때만(2026-10-01)."""
        from dataclasses import replace

        config = replace(self.config, review_conflicts=True,
                         product_codes=[{"code": "6010989901", "name": "실물모형및전시물"}])
        raw = fixtures.notice("P", title="○○군 홍보시설 구매 설치 정비", prdctClsfcNo="6010989901")
        self.assertEqual(screen.screen(notice_from_raw(raw, "물품"), config).excluded_by, "제외키워드")

    def test_budget_below_minimum_blocks(self):
        result = self._screen(fixtures.notice("X", title="○○과학관 전시", presmptPrce="50000000"))
        self.assertFalse(result.matched)
        self.assertEqual(result.excluded_by, "최소예산")

    def test_unknown_budget_passes(self):
        result = self._screen(
            fixtures.notice("X", title="○○과학관 전시물 제작", presmptPrce="", asignBdgtAmt="")
        )
        self.assertTrue(result.matched, "예산 미상은 통과시킨다 — 기존 정책")

    def test_whitespace_is_ignored_in_matching(self):
        result = self._screen(fixtures.notice("X", title="○○ 과 학 관 전시물"))
        self.assertTrue(result.matched)

    def test_overseas_exhibition_korea_pavilion_is_excluded(self):
        """실측: R26BK01717819(UAE 두바이 의료기기전시회), R26BK01714892(두바이) —
        둘 다 "한국관" 때문에 국내 공고로 오인됐지만 실제로는 해외 개최다."""
        result = self._screen(
            fixtures.notice("X", title="2027 UAE 두바이 의료기기전시회 한국관 전시디자인설치공사 입찰")
        )
        self.assertFalse(result.matched)
        self.assertEqual(result.excluded_by, "해외개최")

    def test_korea_pavilion_alone_is_excluded_without_event_keyword(self):
        """"한국관"은 "전시회/박람회/엑스포" 같은 행사 키워드 없이 단독으로도 제외된다."""
        result = self._screen(fixtures.notice("X", title="□□센터 한국관 조성 및 운영 지원"))
        self.assertFalse(result.matched)
        self.assertEqual(result.excluded_by, "해외개최")

    def test_group_pavilion_alone_without_event_keyword_is_unaffected(self):
        """"단체관"은 "한국관"과 달리 행사 키워드가 같이 있어야 신호로 본다."""
        result = self._screen(fixtures.notice("X", title="□□체험관 단체관 운영 프로그램"))
        self.assertNotEqual(result.excluded_by, "해외개최")

    def test_overseas_exhibition_without_korea_pavilion_is_unaffected(self):
        """"전시회/박람회/엑스포"만으로는 안 걸린다 — "한국관/단체관"이 같이 있어야 한다."""
        result = self._screen(fixtures.notice("X", title="○○과학관 특별전시회 운영 박람회 홍보"))
        self.assertNotEqual(result.excluded_by, "해외개최")

    def test_domestic_exhibition_design_notice_is_unaffected(self):
        """일반 국내 전시디자인 공고는 "한국관/단체관" 문구가 없으니 그대로 통과해야 한다."""
        result = self._screen(fixtures.notice("X", title="○○박물관 상설전시관 전시디자인 제작 설치"))
        self.assertTrue(result.matched)
        self.assertFalse(result.overseas_flag)

    def test_mongolia_korea_pavilion_is_flagged_not_excluded(self):
        """회사가 실제로 확장 중인 몽골은 예외 — 배제하지 않고 플래그만 남긴다."""
        result = self._screen(fixtures.notice("X", title="2026 몽골 울란바토르 국제산업박람회 한국관 조성"))
        self.assertTrue(result.matched, "몽골은 키워드/코드 불일치로도 배제되면 안 된다")
        self.assertTrue(result.overseas_flag)
        self.assertIsNone(result.excluded_by)

    def test_overseas_flag_carries_hover_tooltip_evidence(self):
        """리포트 배지의 마우스오버 툴팁에 쓸 근거 문구 — 어떤 단어가
        매치됐는지와 몽골이라 배제 안 했다는 사실이 같이 들어있어야 한다."""
        result = self._screen(fixtures.notice("X", title="2026 몽골 울란바토르 국제산업박람회 한국관 조성"))
        self.assertIsNotNone(result.overseas_evidence)
        self.assertIn("한국관", result.overseas_evidence)
        self.assertIn("몽골", result.overseas_evidence)

    def test_excluded_reason_also_carries_evidence(self):
        result = self._screen(
            fixtures.notice("X", title="2027 UAE 두바이 의료기기전시회 한국관 전시디자인설치공사 입찰")
        )
        self.assertIn("한국관", result.excluded_reason)

    def test_mongolia_korea_pavilion_with_no_other_match_is_still_surfaced(self):
        """몽골 해외관은 등록된 업무 키워드가 하나도 없어도(=원래는 '미매칭'으로
        걸러질 상황) 플래그를 위해 강제로 통과시킨다."""
        result = self._screen(fixtures.notice("X", title="2026 몽골 울란바토르 산업박람회 단체관 설치"))
        self.assertTrue(result.matched)
        self.assertTrue(result.overseas_flag)
        self.assertEqual(result.matched_keywords, [])


class TestSchedule(unittest.TestCase):
    def test_card_deadline_is_qualification_registration_first(self):
        """카드 마감은 가장 이른 것이 아니라 입찰참가자격등록 마감 우선(2026-10-02 사용자 결정) — 공동수급협정 마감(9/18)이
        더 일러도 자격등록(9/20)이 기준이고, 공동수급협정 마감은 팝업(all_deadlines)에 보인다."""
        n = notice_from_raw(
            fixtures.notice(
                "X",
                bidQlfctRgstDt="2026-09-20 18:00:00",
                cmmnSpldmdAgrmntClseDt="2026-09-18 18:00:00",
                bidClseDt="2026-09-25 10:00:00",
            ),
            "용역",
        )
        sched = screen.build_schedule(n)
        label, when = sched.earliest
        self.assertEqual(label, "자격등록 마감")
        self.assertEqual(when, datetime(2026, 9, 20, 18, 0))
        self.assertEqual(sched.days_left(NOW), 6)
        self.assertEqual([l for l, _ in sched.all_deadlines], ["공고 게시", "공동수급협정 마감", "자격등록 마감", "입찰 마감"])

    def test_card_deadline_fallback_order(self):
        """자격등록 없으면 입찰 마감, 둘 다 없으면 첨부 마감, 그것도 없으면 공동수급협정 마감."""
        s = screen.Schedule(qualification_deadline=None, joint_agreement_deadline=datetime(2026, 9, 18, 18),
                            bid_deadline=datetime(2026, 9, 25, 10))
        self.assertEqual(s.earliest[0], "입찰 마감")
        s = screen.Schedule(qualification_deadline=None, joint_agreement_deadline=datetime(2026, 9, 18, 18), bid_deadline=None)
        self.assertEqual(s.earliest[0], "공동수급협정 마감")
        s.attachment_deadline_kind, s.attachment_deadline = "첨부파일 제출기한", datetime(2026, 9, 20, 17)
        self.assertEqual(s.earliest, ("첨부파일 제출기한", datetime(2026, 9, 20, 17)))

    def test_missing_schedule_returns_none(self):
        n = notice_from_raw(
            fixtures.notice("X", bidQlfctRgstDt="", bidClseDt="", cmmnSpldmdAgrmntClseDt=""), "용역"
        )
        self.assertIsNone(screen.build_schedule(n).days_left(NOW))

    def test_attachment_deadline_used_only_when_api_fields_all_empty(self):
        sched = screen.Schedule(
            qualification_deadline=None,
            joint_agreement_deadline=None,
            bid_deadline=None,
            attachment_deadline=datetime(2026, 9, 14, 18, 0),
        )
        self.assertEqual(sched.earliest, ("첨부파일 제출기한", datetime(2026, 9, 14, 18, 0)))

    def test_attachment_deadline_ignored_when_an_api_field_exists(self):
        """API 값이 하나라도 있으면 첨부파일 추정치보다 우선한다."""
        sched = screen.Schedule(
            qualification_deadline=None,
            joint_agreement_deadline=None,
            bid_deadline=datetime(2026, 9, 20, 18, 0),
            attachment_deadline=datetime(2026, 9, 14, 18, 0),
        )
        label, when = sched.earliest
        self.assertEqual(label, "입찰 마감")
        self.assertEqual(when, datetime(2026, 9, 20, 18, 0))


class TestNoticeOpeningAt(unittest.TestCase):
    """개찰일(opengDt)은 fields.py에 후보가 있었지만 Notice에 연결이 안 돼 있었다 —
    API가 실제로 주는 값이 파이프라인 어디에도 안 남고 버려지던 상태. 연결됐는지 확인."""

    def test_opening_at_is_mapped_from_api_field(self):
        n = notice_from_raw(fixtures.notice("X", opengDt="2026-09-25 11:00:00"), "용역")
        self.assertEqual(n.opening_at, "2026-09-25 11:00:00")

    def test_opening_at_is_none_when_absent(self):
        n = notice_from_raw(fixtures.notice("X", opengDt=""), "용역")
        self.assertIsNone(n.opening_at)


class TestNoticeEstimatePriceMethod(unittest.TestCase):
    """예가방법(예정가격 결정방법). 실측(2026-09-17 `nego --verify`, Run #46)으로
    확인한 필드 — 용역/물품/공사 세 오퍼레이션 모두에서 prearngPrceDcsnMthdNm으로
    내려온다(예: 용역="비예가", 물품/공사="단일예가")."""

    def test_estimate_price_method_is_mapped_from_api_field(self):
        n = notice_from_raw(fixtures.notice("X", prearngPrceDcsnMthdNm="단일예가"), "용역")
        self.assertEqual(n.estimate_price_method, "단일예가")

    def test_estimate_price_method_is_none_when_absent(self):
        n = notice_from_raw(fixtures.notice("X", prearngPrceDcsnMthdNm=""), "용역")
        self.assertIsNone(n.estimate_price_method)


class TestScheduleTextExtraction(unittest.TestCase):
    def test_extracts_deadline_with_time_range(self):
        """실측: 경상남도관광재단 「K-거상」공고 R26BK01707504 — API 마감
        필드가 전부 비어 있었지만 첨부 제안요청서에는 제출기한이 명시돼
        있었다. 범위(9:00~18:00)면 종료 시각을 마감으로 본다."""
        text = (
            "다. 기본서류 및 제안서 제출일시(반드시 방문제출)\n"
            "- 제출기간 : 2026. 9. 14.(월) 9:00~18:00 (점심시간 12:00~13:00 접수 불가)\n"
        )
        self.assertEqual(schedule_text.extract_deadline(text), datetime(2026, 9, 14, 18, 0))

    def test_defaults_to_end_of_day_when_no_time_given(self):
        text = "마감일자 : 2026.10.5."
        self.assertEqual(schedule_text.extract_deadline(text), datetime(2026, 10, 5, 23, 59))

    def test_skips_label_when_date_is_too_far_away(self):
        """절 제목처럼 라벨만 있고 실제 값은 다른 줄에 있으면 건너뛰고 다음
        라벨(실제 값이 가까운)로 넘어가야 한다."""
        text = (
            "다. 기본서류 및 제안서 제출일시(반드시 방문제출)\n"
            "    * 아래 접수처로 방문 제출하며 우편 및 온라인 접수는 불가합니다.\n"
            "- 제출기한 : 2026.11.3.(화) 17:00\n"
        )
        self.assertEqual(schedule_text.extract_deadline(text), datetime(2026, 11, 3, 17, 0))

    def test_returns_none_when_no_deadline_text_present(self):
        self.assertIsNone(schedule_text.extract_deadline("과업 내용은 별첨 과업지시서를 참조합니다."))


class TestPersonalDataStripped(unittest.TestCase):
    def test_person_fields_removed_from_raw(self):
        n = notice_from_raw(fixtures.notice("X"), "용역")
        for field_name in PERSONAL_FIELDS:
            self.assertNotIn(field_name, n.raw, f"{field_name}이 raw에 남아 있으면 안 된다")


class TestAttachments(unittest.TestCase):
    def test_attachments_collected_with_extension(self):
        n = notice_from_raw(fixtures.notice("X"), "용역")
        self.assertEqual(len(n.attachments), 2)
        self.assertEqual(n.attachments[0]["ext"], "hwp")
        self.assertEqual(n.attachments[1]["ext"], "pdf")


class _FailingClient:
    """지정한 label만 재시도 소진(ApiError)으로 실패하는 가짜 클라이언트."""

    def __init__(self, fail_labels: set[str]):
        self.fail_labels = fail_labels
        self.called_labels: list[str] = []

    def fetch_all_pages_chunked(self, base_url, operation, params, begin, end, label):
        self.called_labels.append(label)
        if label in self.fail_labels:
            raise ApiError(label, "요청 실패: 재시도 소진")
        return []


class TestCollectNotices(unittest.TestCase):
    def test_first_failed_operation_stops_the_rest_immediately(self):
        """용역/물품/공사 중 하나라도 재시도를 소진해 실패하면, 나머지 부문은
        시도조차 하지 않고 즉시 ApiError를 올려야 한다 — 부분 데이터로 계속
        진행하지 않고, Actions에서 새 Run으로 빨리 재시도할 수 있어야 하기
        때문이다."""
        for failing in F.BID_NOTICE_OPERATIONS:
            with self.subTest(failing=failing):
                stats = RunStats()
                client = _FailingClient(fail_labels={f"본공고/{failing}"})
                with self.assertRaises(ApiError):
                    collect_notices(client, "202601010000", "202601312359", stats)
                self.assertEqual(stats.failed_operations, [f"본공고/{failing}"])
                self.assertEqual(client.called_labels[-1], f"본공고/{failing}", "실패한 부문 이후는 시도하지 않아야 한다")


class TestEndToEnd(unittest.TestCase):
    def test_full_pipeline_on_fixtures(self):
        config = load_config()
        # 픽스처 제목에 맞춰 키워드만 좁혀서 검증한다 (config 원본은 건드리지 않는다).
        config.screen.keywords = [
            "전시관", "박물관", "과학관", "체험관", "전시디자인", "전시홍보관", "미디어아트", "전시콘텐츠", "전시물",
        ]
        stats = RunStats()
        license_groups = qualify.group_license_rows(fixtures.license_rows())

        candidates = build_candidates(_notices(), config, license_groups, {}, NOW, stats, {"협상"})
        numbers = [c.notice.notice_no for c in candidates]

        self.assertIn("R26TEST00001", numbers)
        self.assertIn("R26TEST00002", numbers)
        self.assertIn("R26TEST00008", numbers)
        self.assertNotIn("R26TEST00003", numbers, "취소공고")
        self.assertNotIn("R26TEST00004", numbers, "협상 아님")
        self.assertNotIn("R26TEST00005", numbers, "설계공모")
        self.assertNotIn("R26TEST00006", numbers, "제외키워드")
        self.assertNotIn("R26TEST00007", numbers, "예산 미달")
        self.assertIn("R26TEST00009", numbers, "자격 미충족 2그룹이어도 제외하지 않는다 (공동수급 보완 가능)")
        self.assertNotIn("R26TEST00010", numbers, "키워드 미매칭")

        flagged = next(c for c in candidates if c.notice.notice_no == "R26TEST00009")
        self.assertIn("자격 미달", flagged.qualification.summary)
        self.assertEqual(stats.cancelled, 1)
        self.assertEqual(stats.qualification_flagged, 1)
        self.assertEqual(stats.other_category, 2, "적격심사·설계공모는 '입찰' 유형이라 협상 실행에서 빠진다")
        self.assertTrue(all(c.category == "협상" for c in candidates))

    def test_all_categories_include_competitive_bids(self):
        """유형을 고르지 않으면 적격심사(입찰)·설계공모(입찰)도 후보가 될 수 있다."""
        config = load_config()
        config.screen.keywords = ["전시관", "박물관"]
        stats = RunStats()
        candidates = build_candidates(_notices(), config, {}, {}, NOW, stats)
        by_no = {c.notice.notice_no: c.category for c in candidates}
        self.assertEqual(by_no.get("R26TEST00004"), "입찰")
        self.assertEqual(by_no.get("R26TEST00005"), "입찰")
        self.assertEqual(by_no.get("R26TEST00002"), "협상")

    def test_sorted_by_deadline_first(self):
        config = load_config()
        config.screen.keywords = ["전시관", "박물관", "과학관", "전시디자인", "전시물", "전시홍보관"]
        stats = RunStats()
        candidates = build_candidates(_notices(), config, {}, {}, NOW, stats)
        days = [c.days_left if c.days_left is not None else 9999 for c in candidates]
        self.assertEqual(days, sorted(days), "마감 임박 순으로 정렬되어야 한다")


class TestAnswers20261013(unittest.TestCase):
    """2026-10-13: "건축" 제외가 "실내건축"을 안 빼고(대리님 답변), 요구 면허를 지일이 하나도 없는 공고는 뺀다(사용자 결정)."""

    def _notice(self, title):
        return notice_from_raw(fixtures.notice("R26TEST10013", title=title), "공사")

    def test_interior_architecture_is_not_excluded_by_building(self):
        config = load_config()
        result = screen.screen(self._notice("○○박물관 실내건축공사"), config.screen)
        self.assertTrue(result.matched, result.excluded_reason)
        # 다른 곳에 "건축"이 또 있으면 그대로 제외
        result = screen.screen(self._notice("○○박물관 건축 및 실내건축공사"), config.screen)
        self.assertEqual((result.excluded_by, result.excluded_reason), ("제외키워드", "건축"))
        result = screen.screen(self._notice("○○박물관 증축 건축공사"), config.screen)
        self.assertEqual(result.excluded_by, "제외키워드")

    def test_no_held_license(self):
        held = ["실내건축공사업/4990"]

        def f(groups):
            return qualify.no_held_license(groups, held, {"4990"})

        # 조경·토목만 요구 → 제외
        self.assertEqual(f([qualify.LicenseGroup("1", rows=[["조경공사업/0004"]])]), ["조경공사업(0004)"])
        self.assertEqual(f([
            qualify.LicenseGroup("1", rows=[["토목공사업/0001", "토목건축공사업"]]),
            qualify.LicenseGroup("2", rows=[["조경공사업/0004"]]),
        ]), ["토목공사업(0001)", "조경공사업(0004)"])
        # 미보유 면허 둘을 함께 요구 → 제외
        self.assertEqual(f([qualify.LicenseGroup("1", rows=[["전기공사업/0007"], ["정보통신공사업"]])]),
                         ["전기공사업(0007)", "정보통신공사업"])
        # 지일 보유 면허를 함께 요구 → 공동수급(분담이행) 여지가 있어 남김
        self.assertIsNone(f([qualify.LicenseGroup("1", rows=[["실내건축공사업/4990"], ["토목공사업/0001"]])]))
        # 다른 그룹(또는)에 보유 면허 → 남김
        self.assertIsNone(f([
            qualify.LicenseGroup("1", rows=[["토목공사업/0001"]]),
            qualify.LicenseGroup("2", rows=[["실내건축공사업/4990"]]),
        ]))
        # 허용업종으로 보유 면허 인정 → 남김
        self.assertIsNone(f([qualify.LicenseGroup("1", rows=[["건축공사업/0002", "실내건축공사업"]])]))
        # 면허정보 없음 → 남김
        self.assertIsNone(f([]))

    def test_pipeline_rejects_notice_without_held_license(self):
        config = load_config()
        notices = [notice_from_raw(fixtures.notice("R26TEST10013", title="○○체험관 조성공사"), "공사")]
        groups = {"R26TEST10013": [qualify.LicenseGroup("1", rows=[["토목공사업/0001"]])]}
        stats = RunStats()
        self.assertEqual(build_candidates(notices, config, groups, {}, NOW, stats), [])
        self.assertEqual(stats.screened_out.get("보유면허없음"), 1)
        self.assertEqual(stats.rejected[0].excluded_reason, "보유면허없음 (요구 면허 토목공사업(0001) — 지일 보유 없음, 공동수급 불가)")
        stats = RunStats()
        self.assertEqual(len(build_candidates(notices, config, {}, {}, NOW, stats)), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestConjunctionIsNotAName(unittest.TestCase):
    """'(업종코드 4442 또는 4444)'에서 '또는'이 4442의 이름으로 붙던 문제 (2026-09-30)."""

    def test_or_codes_in_parens(self):
        items = ["① 소프트웨어사업자(디지털콘텐츠개발서비스사업, 업종코드 1469)로 등록한 자",
                 "② 산업디자인전문회사(업종코드 4442 또는 4444)로 등록한 자",
                 "③ 직접생산확인증명서[세부품명 : 실물모형및전시물, 세부품명번호 10자리 : 6010989901]를 소지한 자"]
        ok = qualify.evaluate_attachment_text(items, {"1469", "4444", "6010989901"}, {})
        self.assertEqual(ok.summary, "자격 충족", "②는 4442·4444 중 하나면 된다")
        miss = qualify.evaluate_attachment_text(items, {"1469", "4442"}, {})
        self.assertIn("6010989901", miss.summary, "①②③은 모두 갖춰야 한다")
        names = [n for g in ok.satisfied_groups + miss.satisfied_groups for n in g.allowed_names]
        self.assertFalse(any(n.startswith("또는") for n in names), names)


class TestNoMatchExplain(unittest.TestCase):
    """미매칭으로 빠진 공고에 '무엇을 무엇과 비교해 안 맞았는지'를 남긴다 (2026-09-30 요청)."""

    def test_explain_lines(self):
        cfg = screen.ScreenConfig(keywords=["박물관", "전시관"], product_codes=[{"code": "6010989901", "name": "실물모형"}],
                                  industry_codes=[{"code": "4990", "name": "실내건축공사업"}])
        n = notice_from_raw(fixtures.notice("R26X1", title="재해문자전광판 설치 사업", prdctClsfcNo="5512190301",
                                            prdctClsfcNoNm="안내전광판"), "물품")
        r = screen.screen(n, cfg)
        self.assertEqual(r.excluded_by, "미매칭")
        text = "\n".join(r.match_explain)
        self.assertIn("「재해문자전광판 설치 사업」", text)
        self.assertIn("관심 키워드 2개(박물관, 전시관)", text)
        self.assertIn("5512190301", text)

    def test_industry_only_match_is_explained(self):
        cfg = screen.ScreenConfig(keywords=["박물관"], industry_codes=[{"code": "4990", "name": "실내건축공사업"}])
        n = notice_from_raw(fixtures.notice("R26X2", title="청사 리모델링", bidprcPsblIndstrytyNm="실내건축공사업"), "공사")
        r = screen.screen(n, cfg)
        self.assertTrue(any("업종만 맞는 공고는 후보로 보지 않음" in l for l in r.match_explain), r.match_explain)


class TestCodeLabelsNotBleeding(unittest.TestCase):
    """2026-09-30 제보: 미보유 세부품명번호가 '4511189301), 교육용로봇(6010621401)'처럼 앞 항목 꼬리가 붙어 표시."""

    def labels(self, text):
        return [r[3] for r in qualify._locate_code_requirements(text)[1]]

    def test_consecutive_name_code_pairs(self):
        for text in ["직접생산확인증명서[영상정보디스플레이장치(4511189301), 교육용로봇(6010621401)]를 모두 소지한 자",
                     "직접생산확인증명서(영상정보디스플레이장치(4511189301), 교육용로봇(6010621401))를 소지한 자",
                     "직접생산확인증명서 : 영상정보디스플레이장치(세부품명번호 4511189301), 교육용로봇(6010621401) 모두 소지"]:
            with self.subTest(text=text):
                self.assertEqual(self.labels(text), ["영상정보디스플레이장치(4511189301)", "교육용로봇(6010621401)"])

    def test_item_name_written_before_code(self):
        self.assertEqual(self.labels("직접생산확인증명서 [세부품명: 실물모형및전시물, 세부품명번호 10자리(6010989901)"),
                         ["실물모형및전시물(6010989901)"])


class TestOverlappingLicenseGroups(unittest.TestCase):
    """2026-09-30 실측: 공고문 '건축(또는 토목건축)공사업'인데 면허제한 API는 두 그룹으로 나눠 줌."""

    ROWS = [
        {"bidNtceNo": "R26BK09", "lmtGrpNo": "1", "lmtSno": "1", "lcnsLmtNm": "건축공사업/0002",
         "permsnIndstrytyList": "[건축공사업/0002][토목건축공사업]"},
        {"bidNtceNo": "R26BK09", "lmtGrpNo": "2", "lmtSno": "1", "lcnsLmtNm": "토목건축공사업/0003",
         "permsnIndstrytyList": "[토목건축공사업/0003]"},
    ]

    def test_merged_into_one_or_requirement(self):
        groups = qualify.group_license_rows(self.ROWS)["R26BK09"]
        none = qualify.evaluate(groups, ["실내건축공사업"], {"0006"})
        self.assertEqual(none.missing_count, 1, "요건 1건")
        self.assertEqual(len(none.missing_groups[0].allowed_names), 2, "건축 또는 토목건축")
        self.assertEqual(qualify.evaluate(groups, ["건축공사업"]).missing_count, 0, "건축공사업만 있어도 충족")
        self.assertEqual(qualify.evaluate(groups, ["토목건축공사업"]).missing_count, 0)

    def test_rows_in_one_group_are_all_required(self):
        groups = qualify.group_license_rows(fixtures.license_rows())["R26TEST00009"]
        self.assertEqual(qualify.evaluate(groups, []).missing_count, 3)

    def test_separate_groups_are_alternatives(self):
        """나라장터 "[출판사(1517)] 업종 또는 [인쇄사(1518)] 업종" — API는 그룹 두 개로 준다
        (2026-09-30 국립세종도서관 정책도서 발행 공고). 출판사만 있어도 충족이어야 한다."""
        rows = [
            {"bidNtceNo": "R26BK10", "lmtGrpNo": "1", "lmtSno": "1", "lcnsLmtNm": "출판사/1517", "permsnIndstrytyList": "[출판사/1517]"},
            {"bidNtceNo": "R26BK10", "lmtGrpNo": "2", "lmtSno": "1", "lcnsLmtNm": "인쇄사/1518", "permsnIndstrytyList": "[인쇄사/1518]"},
        ]
        groups = qualify.group_license_rows(rows)["R26BK10"]
        held = qualify.evaluate(groups, ["출판사"], {"1517"})
        self.assertEqual(held.missing_count, 0)
        self.assertEqual(held.satisfied_groups[0].allowed_names, ["출판사/1517", "인쇄사/1518"])
        none = qualify.evaluate(groups, [], set())
        self.assertEqual(none.missing_count, 1, "택1 요건 1건")

    def test_common_row_is_required_and_rest_is_choice(self):
        """그룹1 [4442, 1469] 또는 그룹2 [4444, 1469] → 공고문처럼 "1469 반드시" + "4442·4444 중 1개 이상"."""
        rows = [
            {"bidNtceNo": "N", "lmtGrpNo": "1", "lmtSno": "1", "lcnsLmtNm": "산업디자인전문회사(환경디자인분야)/4442"},
            {"bidNtceNo": "N", "lmtGrpNo": "1", "lmtSno": "2", "lcnsLmtNm": "소프트웨어사업자(디지털콘텐츠개발서비스사업)/1469"},
            {"bidNtceNo": "N", "lmtGrpNo": "2", "lmtSno": "1", "lcnsLmtNm": "산업디자인전문회사(종합디자인분야)/4444"},
            {"bidNtceNo": "N", "lmtGrpNo": "2", "lmtSno": "2", "lcnsLmtNm": "소프트웨어사업자(디지털콘텐츠개발서비스사업)/1469"},
        ]
        groups = qualify.group_license_rows(rows)["N"]
        reqs = qualify.license_requirements(groups)
        self.assertEqual([r.allowed_names for r in reqs], [
            ["소프트웨어사업자(디지털콘텐츠개발서비스사업)/1469"],
            ["산업디자인전문회사(환경디자인분야)/4442", "산업디자인전문회사(종합디자인분야)/4444"],
        ])
        self.assertEqual(qualify.evaluate(groups, [], {"4444", "1469"}).missing_count, 0)
        missing = qualify.evaluate(groups, [], {"4442"}).missing_groups
        self.assertEqual([g.allowed_names for g in missing], [["소프트웨어사업자(디지털콘텐츠개발서비스사업)/1469"]])

    def test_waste_notice_required_plus_one_of_six(self):
        """폐기물 공고 공고문: "① 수집·운반업(1227) 반드시 + ②~⑦ 중 1개 이상". 나라장터는 6조합으로 준다."""
        pairs = ["6786", "1143", "6770", "1257", "6778", "1388"]
        rows = []
        for g, code in enumerate(pairs, 1):
            rows.append({"bidNtceNo": "W", "lmtGrpNo": str(g), "lmtSno": "1", "lcnsLmtNm": "폐기물수집·운반업/1227",
                         "permsnIndstrytyList": "[폐기물수집·운반업/1227][폐기물종합처분업]"})
            rows.append({"bidNtceNo": "W", "lmtGrpNo": str(g), "lmtSno": "2", "lcnsLmtNm": f"처분재활용{code}/{code}"})
        groups = qualify.group_license_rows(rows)["W"]
        reqs = qualify.license_requirements(groups)
        self.assertEqual(len(reqs), 2)
        self.assertEqual(reqs[0].rows, [["폐기물수집·운반업/1227", "폐기물종합처분업"]], "필수 + 대체 인정")
        self.assertEqual(len(reqs[1].allowed_names), 6)
        self.assertEqual(qualify.evaluate(groups, [], {"1227", "6770"}).missing_count, 0)
        self.assertEqual(qualify.evaluate(groups, [], {"6770"}).missing_count, 1)

    def test_amended_notice_rows_are_not_repeated(self):
        """정정공고 차수 3개(000·001·002)가 같은 행을 또 보내도 겹치지 않고 마지막 차수만 쓴다
        (2026-09-30 제보: 토목공사업 ×3, 상·하수도/지반조성 번갈아 ×3)."""
        rows = []
        for ord_ in ("000", "001", "002"):
            rows += [
                {"bidNtceNo": "R", "bidNtceOrd": ord_, "lmtGrpNo": "1", "lmtSno": "1", "lcnsLmtNm": "토목공사업/0001"},
                {"bidNtceNo": "R", "bidNtceOrd": ord_, "lmtGrpNo": "2", "lmtSno": "1", "lcnsLmtNm": "상·하수도설비공사업/4996"},
                {"bidNtceNo": "R", "bidNtceOrd": ord_, "lmtGrpNo": "2", "lmtSno": "2", "lcnsLmtNm": "지반조성·포장공사업/4989"},
            ]
        groups = qualify.group_license_rows(rows)["R"]
        self.assertEqual([g.rows for g in groups], [
            [["토목공사업/0001"]],
            [["상·하수도설비공사업/4996"], ["지반조성·포장공사업/4989"]],
        ])
        reqs = qualify.license_requirements(groups)
        self.assertEqual([len(c) for c in reqs[0].combos], [1, 2], "① 토목공사업 또는 ② 상·하수도 + 지반조성")

    def test_rows_with_same_license_merge_into_one_requirement(self):
        """같은 차수 안에서 대표 면허가 같은 행이 허용업종만 달리해 여러 번 와도 한 요건으로 합친다."""
        rows = [
            {"bidNtceNo": "R", "bidNtceOrd": "000", "lmtGrpNo": "1", "lmtSno": str(i), "lcnsLmtNm": "토목공사업/0001",
             "permsnIndstrytyList": alt}
            for i, alt in enumerate(["[토목공사업/0001]", "[토목건축공사업/0003]", "[토목공사업/0001]"], 1)
        ]
        for i in range(6):
            name = "상·하수도설비공사업/4996" if i % 2 == 0 else "지반조성·포장공사업/4989"
            rows.append({"bidNtceNo": "R", "bidNtceOrd": "000", "lmtGrpNo": "2", "lmtSno": str(i + 1), "lcnsLmtNm": name})
        groups = qualify.group_license_rows(rows)["R"]
        self.assertEqual(groups[0].rows, [["토목공사업/0001", "토목건축공사업"]])  # 허용업종 목록은 코드를 떼어 둔다
        self.assertEqual(groups[1].rows, [["상·하수도설비공사업/4996"], ["지반조성·포장공사업/4989"]])
        from webapp import qualview
        q = qualify.evaluate(groups, [])
        self.assertEqual(qualview.build(q, (set(), set()), {"industry": True})[0]["need"], 1, "토목공사업 하나면 됨")

    def test_latest_amendment_wins(self):
        rows = [
            {"bidNtceNo": "R", "bidNtceOrd": "000", "lmtGrpNo": "1", "lmtSno": "1", "lcnsLmtNm": "조경공사업/0005"},
            {"bidNtceNo": "R", "bidNtceOrd": "001", "lmtGrpNo": "1", "lmtSno": "1", "lcnsLmtNm": "실내건축공사업/4990"},
        ]
        self.assertEqual(qualify.group_license_rows(rows)["R"][0].allowed_names, ["실내건축공사업/4990"])

    def test_combos_factor_into_required_and_pick_one(self):
        """나라장터 조합 4개 = 0036·4990 공통 + (4442 또는 4444) + (1469 또는 1426) — 경우의 수를 나열하지 않고
        필수 2건 + "N개 중 1개 이상" 2건으로 푼다(2026-10-01 요청). 판정은 조합 판정과 같아야 한다."""
        sets = [["0036", "4990", "4442", "1469"], ["0036", "4990", "4444", "1469"],
                ["0036", "4990", "4442", "1426"], ["0036", "1426", "4444", "4990"]]
        rows = [{"bidNtceNo": "S", "lmtGrpNo": str(g + 1), "lmtSno": str(i + 1), "lcnsLmtNm": f"업종{c}/{c}"}
                for g, codes in enumerate(sets) for i, c in enumerate(codes)]
        groups = qualify.group_license_rows(rows)["S"]
        reqs = qualify.license_requirements(groups)
        self.assertEqual([(r.group_no.split("-")[0].rstrip("0123456789"), len(r.allowed_names), bool(r.combos)) for r in reqs],
                         [("필수", 1, False), ("필수", 1, False), ("택", 2, False), ("택", 2, False)])
        self.assertEqual(qualify.evaluate(groups, [], {"0036", "4990", "4444", "1426"}).missing_count, 0)
        self.assertEqual(qualify.evaluate(groups, [], {"0036", "4990", "4444"}).missing_count, 1)
        # 조합이 일부만 있으면(모든 경우의 수가 아니면) 칸으로 못 나눔 → 예전처럼 조합으로
        part = qualify.group_license_rows(rows[:12])["S"]
        self.assertTrue(qualify.license_requirements(part)[-1].combos)
        self.assertEqual(qualify.evaluate(part, [], {"0036", "4990", "4444", "1426"}).missing_count, 1)

    def test_combo_needs_every_license_of_one_set(self):
        """"A와 B" 또는 "C와 D" — 한 조합을 다 갖춰야 충족. A·C처럼 조합을 섞으면 미달."""
        rows = [
            {"bidNtceNo": "N", "lmtGrpNo": "1", "lmtSno": "1", "lcnsLmtNm": "A/0001"},
            {"bidNtceNo": "N", "lmtGrpNo": "1", "lmtSno": "2", "lcnsLmtNm": "B/0002"},
            {"bidNtceNo": "N", "lmtGrpNo": "2", "lmtSno": "1", "lcnsLmtNm": "C/0003"},
            {"bidNtceNo": "N", "lmtGrpNo": "2", "lmtSno": "2", "lcnsLmtNm": "D/0004"},
        ]
        groups = qualify.group_license_rows(rows)["N"]
        self.assertEqual(qualify.evaluate(groups, [], {"0003", "0004"}).missing_count, 0)
        self.assertEqual(qualify.evaluate(groups, [], {"0001", "0003"}).missing_count, 1)
        self.assertEqual(qualify.evaluate(groups, [], set()).summary, "자격 미달(A(0001) + B(0002)/C(0003) + D(0004))")
