"""지역 제한 판정 (nego/region.py) — 2026-09-30 경기북부어린이박물관 공고 계기."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nego import region  # noqa: E402

GYEONGGI_ITEM = ("가. 입찰공고일 전일부터 입찰일까지 주된 영업소의 소재지가 경기도에 있는 업체로서, "
                 "법인의 경우 법인등기부상 본점 소재지를 기준으로 하며")


class TestSido(unittest.TestCase):
    def test_names(self):
        for name, sido in [("경기도", "경기"), ("강원특별자치도 정선군", "강원"), ("강원도", "강원"),
                           ("서울특별시 강남구", "서울"), ("전라북도", "전북"), ("전북특별자치도", "전북"), ("없음", None)]:
            self.assertEqual(region.sido_of(name), sido, name)


class TestFromText(unittest.TestCase):
    def test_gyeonggi_only_fails_for_gangwon(self):
        r = region.from_text([GYEONGGI_ITEM], "강원")
        self.assertEqual((r.status, r.required, r.source), ("미달", ["경기"], "첨부 공고문"))
        self.assertIn("경기도", r.evidence)

    def test_gangwon_passes(self):
        r = region.from_text(["본점 소재지가 강원특별자치도 또는 경기도에 있는 업체"], "강원")
        self.assertEqual((r.status, r.required), ("충족", ["경기", "강원"]))

    def test_joint_supply_region_duty_is_not_a_location_limit(self):
        # 지역의무 공동도급 — 구성원 중 한 곳만 그 지역이면 되므로 업체 소재지 제한이 아니다
        self.assertIsNone(region.from_text(["공동수급체 구성원 중 1인 이상은 주된 영업소가 경기도에 소재한 업체"], "강원"))

    def test_recommendation_is_not_a_limit(self):
        self.assertIsNone(region.from_text(
            ["※ 지역경제 활성화를 위하여 주된 영업소의 소재지가 대구시 ․ 경상북도 내에 소재한 업체와 공동으로 참여를 권장함"], "강원"))

    def test_word_containing_short_name_is_ignored(self):
        self.assertIsNone(region.from_text(["본사 경기장 인근 소재지 확인서를 제출"], "강원"))

    def test_no_company_region_is_unknown(self):
        self.assertEqual(region.from_text([GYEONGGI_ITEM], None).status, "미확인")


class TestSigunguList(unittest.TestCase):
    def test_listed_sigungu_all_read(self):
        """2026-10-19 제보: "본점이 강원특별자치도 속초시·고성군·양양군·강릉시에 있는 업체" — 칩에 속초시만 보였다."""
        r = region.from_text(["본점이 강원특별자치도 속초시·고성군·양양군·강릉시에 있는 업체."], "강원특별자치도 정선군")
        self.assertEqual((r.status, r.required), ("미달", ["강원 강릉시", "강원 고성군", "강원 속초시", "강원 양양군"]))

    def test_listed_sigungu_with_company(self):
        r = region.from_text(["주된 영업소 소재지가 강원특별자치도 평창군, 정선군 또는 영월군에 있는 업체"], "강원특별자치도 정선군")
        self.assertEqual(r.status, "충족")

    def test_particle_after_sigungu(self):
        """"춘천시에 있는"처럼 조사가 붙어도 시·군까지 본다(예전엔 강원 전체로 읽어 충족)."""
        r = region.from_text(["본점 소재지가 강원특별자치도 춘천시에 있는 업체"], "강원특별자치도 정선군")
        self.assertEqual((r.status, r.required), ("미달", ["강원 춘천시"]))


class TestFromApiAndCombine(unittest.TestCase):
    def test_api_regions(self):
        self.assertEqual(region.from_api(["경기도"], "강원").status, "미달")
        self.assertEqual(region.from_api(["강원특별자치도", "경기도"], "강원").status, "충족")
        self.assertEqual(region.from_api([], "강원").status, "미확인", "빈 목록은 정보 없음")

    def test_text_wins_over_api(self):
        api = region.from_api(["강원특별자치도"], "강원")
        text = region.from_text([GYEONGGI_ITEM], "강원")
        self.assertEqual(region.combine(api, text).status, "미달")
        self.assertEqual(region.combine(api, None).status, "충족")


class TestRealRfp(unittest.TestCase):
    """사용자가 보내준 경기북부어린이박물관 제안요청서의 참가자격 절 (그대로 옮김)."""

    def test_section_from_rfp(self):
        from nego.qualification_text import find_qualification_section

        text = "\n".join([
            "5. 입찰 참가자격",
            " 1) ｢지방자치단체를당사자로하는계약에관한법률｣ 시행령 제13조 및 동법 시행규칙 제14조에 의한 소정의 자격을 갖추고 "
            "국가종합전자조달시스템 입찰참가자격등록규정에 따라 입찰마감일 전일까지 다음의 자격을 모두 갖춘 자",
            "  " + GYEONGGI_ITEM + ", 협상적격자는 계약체결일까지 해당 자격을 계속 유지하여야 함",
            "  나. 「중소기업기본법」 제2조에 따른 중ˑ소기업자 및 소상공인으로서 확인서를 소지한 자",
            " 2) 공동수급(공동이행방식) : 허용하지 않음",
            "6. 선정 절차",
        ])
        items = find_qualification_section(text).items
        self.assertEqual(region.from_text(items, "강원").status, "미달")


if __name__ == "__main__":
    unittest.main()


def test_summary_uses_same_shape_for_different_wordings():
    """2026-10-02 사용자 요청 — 원문 표기가 공고마다 달라 핵심 낱말로 같은 모양을 만든다."""
    from nego.region import summarize

    a = summarize("입찰공고일 전일부터 입찰일(낙찰자는 계약체결일)까지 법인등기부상 본점 소재지(개인사업자인 경우에는 "
                  "사업자등록증 … 사업장의 소재지)가 경기도 내에 있는 업체이어야 합니다.")
    assert a == {"basis": "법인등기부상 본점 소재지", "period": "입찰공고일 전일 ~ 입찰일 (낙찰자: 계약체결일)", "individual": True}
    b = summarize("입찰 공고일 전일 현재 제주특별자치도에 주된 영업소를 두고 당해 자격을 입찰일(낙찰자는 계약 체결일)까지 유지하고 있는 업체")
    assert b == {"basis": "주된 영업소 소재지", "period": "입찰공고일 전일 ~ 입찰일 (낙찰자: 계약체결일)", "individual": False}
    c = summarize("주된 영업소의 소재지가 경기도에 있는 업체")
    assert c == {"basis": "주된 영업소 소재지", "period": None, "individual": False}


class TestDropRegionUnmet(unittest.TestCase):
    """2026-10-14 사용자 결정: 지역 미달은 공동수급 허용이어도 제외(구성원 모두 소재지 요건), 미확인은 남김."""

    def test_unmet_moved_to_rejected(self):
        from types import SimpleNamespace

        from nego.pipeline import RunStats, drop_region_unmet

        unmet = SimpleNamespace(region_check=region.from_text([GYEONGGI_ITEM], "강원"),
                                is_candidate=True, excluded_reason=None)
        ok = SimpleNamespace(region_check=region.from_api(["강원특별자치도"], "강원"))
        unknown = SimpleNamespace(region_check=region.from_api([], "강원"))
        stats = RunStats(screened_in=3, candidates=3)
        kept = drop_region_unmet([unmet, ok, unknown], stats)
        self.assertEqual(kept, [ok, unknown])
        self.assertEqual(stats.rejected, [unmet])
        self.assertFalse(unmet.is_candidate)
        self.assertEqual(unmet.excluded_reason, "지역제한 (경기만 참가 — 지일 강원, 근거: 첨부 공고문)")
        self.assertEqual((stats.screened_out, stats.candidates, stats.screened_in), ({"지역제한": 1}, 2, 2))
