"""2026-10-16·19 수집기 통합 결정(N·M)으로 바뀐 판정 규칙."""

import sys
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import fixtures  # noqa: E402
from nego import region, scope, screen  # noqa: E402
from nego.config import load_config  # noqa: E402
from nego.models import notice_from_raw  # noqa: E402
from nego.pipeline import RunStats, drop_expired, drop_region_unmet  # noqa: E402


def _notice(title="OO박물관 전시물 제작 설치", work_type="용역", **raw):
    return notice_from_raw(fixtures.notice("R26TEST20000", title=title, **raw), work_type)


def _screen_config():
    config = load_config().screen
    return config


class TestBudget(unittest.TestCase):
    """결정 2번 — 추정가격, 없으면 배정예산 그대로(÷1.1 없음). 0·빈 값은 금액 모름."""

    def test_assigned_budget_used_as_is(self):
        n = _notice(presmptPrce="", asignBdgtAmt="105000000")
        self.assertEqual(n.budget, 105000000)
        self.assertEqual(n.budget_kind, "배정예산")

    def test_estimated_price_first(self):
        n = _notice(presmptPrce="90000000", asignBdgtAmt="99000000")
        self.assertEqual((n.budget, n.budget_kind), (90000000, "추정가격"))

    def test_zero_is_unknown_and_passes_min_budget(self):
        n = _notice(presmptPrce="0", asignBdgtAmt="0")
        self.assertIsNone(n.budget)
        self.assertIsNone(n.budget_kind)
        self.assertTrue(screen.screen(n, _screen_config()).matched, "금액 모름은 최소예산에서 빼지 않는다")


class TestDesignServiceVariants(unittest.TestCase):
    """결정 3번 — '공사 설계 용역'의 기본·실시·기본 및 실시설계 변형도 관심 키워드·예산 예외."""

    def test_variants_match_and_are_budget_exempt(self):
        for title in (
            "OO도서관 리모델링 공사 설계 용역",
            "OO공원 조성공사 설계용역",
            "OO체험관 증축공사 기본설계 용역",
            "OO센터 리모델링공사 실시설계용역",
            "OO기념관 개선공사 기본 및 실시설계 용역",
            "OO관 정비공사 기본·실시설계 용역",
        ):
            with self.subTest(title=title):
                n = _notice(title=title, presmptPrce="30000000", asignBdgtAmt="33000000")
                result = screen.screen(n, _screen_config())
                if result.excluded_by == "제외키워드":
                    continue  # '정비' 같은 제외 키워드는 그대로 먼저 적용된다
                self.assertTrue(result.matched, result.excluded_reason)
                self.assertIn("공사 설계 용역", result.matched_keywords)

    def test_any_design_service_is_budget_exempt(self):
        """10/19 결정 — '공사'가 없는 설계 용역도 예산 상관없이(관심 키워드는 맞아야 함)."""
        n = _notice(title="OO전시관 실시설계 용역", presmptPrce="30000000", asignBdgtAmt="33000000")
        result = screen.screen(n, _screen_config())
        self.assertTrue(result.matched, result.excluded_reason)
        n = _notice(title="OO본부 청사 실시설계 용역", presmptPrce="30000000", asignBdgtAmt="33000000")
        self.assertEqual(screen.screen(n, _screen_config()).excluded_by, "미매칭")
        n = _notice(title="OO전시관 전시물 제작", presmptPrce="30000000", asignBdgtAmt="33000000")
        self.assertEqual(screen.screen(n, _screen_config()).excluded_by, "최소예산")

    def test_design_service_with_interest_keyword_is_collected(self):
        """10/19 결정 — 관심 키워드가 있으면 제작·설치 없는 설계 용역도 수집(울란바타르 공간기획·인테리어 설계 용역)."""
        for title in ("OO박물관 리모델링 기본 및 실시설계 용역", "OO 스타트업 타운 조성을 위한 공간기획 및 인테리어 설계 용역"):
            with self.subTest(title=title):
                n = _notice(title=title, presmptPrce="300000000", asignBdgtAmt="330000000")
                result = screen.screen(n, _screen_config())
                self.assertTrue(result.matched, result.excluded_reason)


class TestDesignContest(unittest.TestCase):
    """결정 5번 — 낙찰방법에만 '설계공모'가 있어도 수집 범위 밖."""

    def test_award_method_design_contest_dropped(self):
        n = _notice(title="신안갯벌박물관 리모델링 기본 및 실시설계 용역", award="설계공모-일반설계공모")
        self.assertIsNone(scope.bid_category(n))
        result = scope.apply_scope([n])
        self.assertEqual(result.dropped_design_contest, 1)
        self.assertEqual(result.dropped_private, 0)

    def test_design_contest_with_private_contract_counted_as_design_contest(self):
        """설계공모는 당선자와 수의계약이라 계약방법이 '수의계약'이어도 설계공모로 센다(10/19 실수집 로그 "설계공모 0건")."""
        n = _notice(title="OO도서관 건립 설계공모", award="설계공모-일반설계공모")
        n.contract_method = "수의계약"
        result = scope.apply_scope([n])
        self.assertEqual((result.dropped_design_contest, result.dropped_private), (1, 0))

    def test_proposal_contest_by_negotiation_kept(self):
        n = _notice(title="거제 지심도 산마루문화놀이터 명소화 사업 제안공모")
        self.assertEqual(scope.bid_category(n), "협상")


class TestOverseas(unittest.TestCase):
    """결정 8번 — 팀원 수집기 규칙: 한국관 단독, 전시회류·해외 지명 + 단체관, 영문 국가 약자."""

    def _excluded(self, title):
        return screen.screen(_notice(title=title), _screen_config()).excluded_by == "해외개최"

    def test_excluded_titles(self):
        for title in (
            "CES 2027 통합한국관 조성운영 위탁용역",
            "NANO TECH 2027 한국관 장치/조성",
            "2026 홍콩 국제 전시회 단체관 전시디자인 및 설치용역",
            "2026 하노이 단체관 부스 조성",
            "2027 관계부처 해외홍보관 미국(KOREA360 USA) 총괄 운영 위탁용역",
        ):
            with self.subTest(title=title):
                self.assertTrue(self._excluded(title))

    def test_not_excluded_titles(self):
        for title in (
            "한국관광공사 홍보관 전시물 제작",
            "박람회 단체관람 버스 임차 및 체험관 운영",
            "부산 BUSAN 박물관 전시물 제작",
        ):
            with self.subTest(title=title):
                self.assertFalse(self._excluded(title))

    def test_mongolia_flag(self):
        r = screen.screen(_notice(title="울란바토르 박람회 한국관 조성"), _screen_config())
        self.assertTrue(r.matched)
        self.assertTrue(r.overseas_flag)

    def test_suspect_flag_only(self):
        r = screen.screen(_notice(title="두바이 홍보 전시관 콘텐츠 제작"), _screen_config())
        self.assertTrue(r.matched)
        self.assertEqual(r.overseas_suspect, "제목에 해외 지명 '두바이'")
        r = screen.screen(_notice(title="보행자 인도 정비 전시관 안내"), _screen_config())
        self.assertIsNone(r.overseas_suspect, "'인도'는 표시에서 뺀다")


class TestSection(unittest.TestCase):
    """결정 9·10번 — 등록 품목(세부품명)만 맞아도 수집, 구역은 '세부품명'."""

    def test_held_product_without_keyword(self):
        n = _notice(title="OO청사 안내판 제작 설치", work_type="물품", dtilPrdctClsfcNo="5512171801",
                    dtilPrdctClsfcNoNm="안내판")
        r = screen.screen(n, _screen_config())
        self.assertTrue(r.matched)
        self.assertEqual(r.section, "세부품명")

    def test_keyword_section(self):
        r = screen.screen(_notice(title="OO박물관 전시물 제작"), _screen_config())
        self.assertEqual(r.section, "관심")

    def test_collection_codes_follow_held_file(self):
        config = load_config()
        held = {e["code"] for e in config.held_raw["heldProducts"]}
        self.assertEqual({e["code"] for e in config.screen.product_codes}, held)
        self.assertIn("7215409902", held)

    def test_bosu_construction_collected(self):
        r = screen.screen(_notice(title="OO과학관 보수공사"), _screen_config())
        self.assertTrue(r.matched)


class TestRegion(unittest.TestCase):
    """결정 7번 — 제외는 API로만, 시·군 단위까지. 첨부는 의심 표시."""

    COMPANY = "강원특별자치도 정선군"

    def test_city_level(self):
        self.assertEqual(region.from_api(["강원특별자치도 춘천시"], self.COMPANY).status, "미달")
        self.assertEqual(region.from_api(["강원특별자치도 정선군"], self.COMPANY).status, "충족")
        self.assertEqual(region.from_api(["강원특별자치도"], self.COMPANY).status, "충족")
        self.assertEqual(region.from_api(["강원특별자치도 춘천시", "강원특별자치도 정선군"], self.COMPANY).status, "충족")
        self.assertEqual(region.split_location(self.COMPANY), ("강원", "정선군"))

    def test_text_only_is_suspect_not_excluded(self):
        api = region.from_api([], self.COMPANY)
        text = region.from_text(["주된 영업소의 소재지가 경기도에 있는 업체"], self.COMPANY)
        from nego.pipeline import Candidate

        c = Candidate.__new__(Candidate)
        c.region_check, c.region_text = api, text
        self.assertEqual(c.region_suspect, "지역 제한 의심: 경기 (첨부)")
        stats = RunStats(screened_in=1)
        self.assertEqual(drop_region_unmet([c], stats), [c])


class TestExpired(unittest.TestCase):
    """결정 4번 — 기준 마감이 지나면 수집에서 뺀다."""

    def test_drop_expired(self):
        def cand(deadline):
            return SimpleNamespace(schedule=screen.Schedule(deadline, None, None), is_candidate=True, excluded_reason=None)

        past, future, none = cand(datetime(2026, 9, 1)), cand(datetime(2026, 12, 1)), cand(None)
        stats = RunStats(screened_in=3)
        kept = drop_expired([past, future, none], datetime(2026, 10, 19), stats)
        self.assertEqual(kept, [future, none])
        self.assertEqual(stats.screened_out.get("마감 지남"), 1)


if __name__ == "__main__":
    unittest.main()
