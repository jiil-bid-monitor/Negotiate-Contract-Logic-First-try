"""리포트(HTML) 생성 단위 테스트. 표준 라이브러리 unittest만 사용한다.

    python -m unittest discover -s tests -v

제목, "조회 기간 ~ 생성 시각" 표기, "본공고 (N건)" 절은 기존에 사용 중이던
수집 시스템의 결과페이지(주간 리포트)를 그대로 따른다. 후보 목록은 표(테이블)
형식이다 — 행마다 신뢰도/업무구분 배지, 자격판정(체크원+호버 팝업), 키워드 태그.
"""

from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nego.config import load_config  # noqa: E402
from nego.models import notice_from_raw  # noqa: E402
from nego.pipeline import RunStats, build_candidates  # noqa: E402
from nego.report import _fmt_kr_date, _fmt_kr_datetime, _fmt_kr_deadline, _fmt_money, render_html  # noqa: E402
from tests import fixtures  # noqa: E402

NOW = datetime(2026, 8, 31, 0, 1, 13)


class TestFormatters(unittest.TestCase):
    def test_fmt_money_shows_exact_won_without_eok_abbreviation(self):
        self.assertEqual(_fmt_money(180_600_000), "180,600,000원")

    def test_fmt_kr_date_has_no_leading_zeros(self):
        self.assertEqual(_fmt_kr_date(datetime(2026, 8, 24)), "2026. 8. 24.")

    def test_fmt_kr_datetime_midnight_is_am_12(self):
        self.assertEqual(_fmt_kr_datetime(datetime(2026, 8, 31, 0, 1, 13)), "2026. 8. 31. AM 12:01:13")

    def test_fmt_kr_datetime_noon_is_pm_12(self):
        self.assertEqual(_fmt_kr_datetime(datetime(2026, 8, 31, 12, 0, 0)), "2026. 8. 31. PM 12:00:00")

    def test_fmt_kr_deadline_uses_24_hour_format(self):
        self.assertEqual(_fmt_kr_deadline(datetime(2026, 9, 20, 18, 0, 0)), "2026년 09월 20일 18시")

    def test_fmt_kr_deadline_pads_single_digit_month_day_hour(self):
        self.assertEqual(_fmt_kr_deadline(datetime(2026, 1, 5, 9, 30, 0)), "2026년 01월 05일 09시")

    def test_fmt_kr_deadline_none_is_empty(self):
        self.assertEqual(_fmt_kr_deadline(None), "")


class TestRenderHtml(unittest.TestCase):
    def _candidates(self):
        config = load_config()
        config.screen.keywords = ["인테리어"]
        raw = fixtures.notice("R26TEST0001", title="(긴급)경북동부근로자건강센터 인테리어공사 입찰공고")
        notices = [notice_from_raw(raw, "공사")]
        stats = RunStats(period_begin=datetime(2026, 8, 24), period_end=NOW)
        candidates = build_candidates(notices, config, {}, {}, NOW, stats)
        self.assertEqual(len(candidates), 1, "픽스처가 스크리닝을 통과해야 아래 검증이 의미가 있다")
        return candidates, stats

    def test_header_matches_existing_system_title_and_period(self):
        candidates, stats = self._candidates()
        out = render_html(candidates, stats, NOW)
        self.assertIn("<h1>나라장터 입찰 모니터링 리포트 (전체 유형)</h1>", out)
        self.assertIn("조회 기간: 2026. 8. 24. ~ 2026. 8. 31.", out)
        self.assertIn("생성 시각: 2026. 8. 31. AM 12:01:13", out)

    def test_section_header_shows_candidate_count(self):
        candidates, stats = self._candidates()
        out = render_html(candidates, stats, NOW)
        self.assertIn(f"관심 공고 ({len(candidates)}건)", out)  # 2026-10-19 통합 결정 12번: 관심 / 세부품명 구역

    def test_row_shows_confidence_and_worktype_badges(self):
        candidates, stats = self._candidates()
        out = render_html(candidates, stats, NOW)
        self.assertIn('<span class="badge">공사</span>', out)
        self.assertIn(candidates[0].screen_result.confidence, out)

    def test_row_shows_qualification_verdict_and_keyword_tag(self):
        candidates, stats = self._candidates()
        out = render_html(candidates, stats, NOW)
        self.assertIn("키워드:인테리어", out)
        self.assertIn(candidates[0].qualification.summary, out)

    def test_long_title_gets_ellipsis_wrapper_and_full_text_in_title_attr(self):
        """공고명이 길면 여러 줄로 감싸이는 대신 한 줄로 자르고("..."는 CSS의
        text-overflow가 담당) 마우스오버로 전체 제목을 볼 수 있게 title
        속성에 원문을 그대로 남긴다."""
        candidates, stats = self._candidates()
        full_title = candidates[0].notice.title
        out = render_html(candidates, stats, NOW)
        self.assertIn(f'<div class="title-text" title="{full_title}">', out)

    def test_opening_at_is_shown_date_only_without_time(self):
        """개찰일은 연월일까지만 표기한다(시각 생략) — opengDt는 시각을 포함해서
        내려오지만 입찰마감일 칸에서 이미 시각을 다루므로 여기선 날짜만 쓴다.
        헤더가 "공고일 / 개찰일" 순서를 알려주므로 칸 안엔 값만 둘째 줄(dim)로 둔다."""
        candidates, stats = self._candidates()
        candidates[0].notice.opening_at = "2026-09-24 10:00:00"
        out = render_html(candidates, stats, NOW)
        self.assertIn('<div class="dim nowrap">2026. 9. 24.</div>', out)
        self.assertNotIn("10:00", out)

    def test_opening_at_missing_shows_placeholder(self):
        candidates, stats = self._candidates()
        candidates[0].notice.opening_at = None
        out = render_html(candidates, stats, NOW)
        self.assertIn('<div class="dim nowrap">일정 미상</div>', out)

    def test_deadline_shows_dday_badge(self):
        candidates, stats = self._candidates()
        candidates[0].days_left = 3
        out = render_html(candidates, stats, NOW)
        self.assertIn('<div class="dday">D-3</div>', out)

    def test_deadline_past_shows_closed_badge_instead_of_dplus(self):
        """마감이 지난 공고는 'D+N'이 아니라 어두운 회색 '공고 마감' 배지로 보여준다."""
        candidates, stats = self._candidates()
        candidates[0].days_left = -5
        out = render_html(candidates, stats, NOW)
        self.assertIn('<div class="dday-closed">공고 마감</div>', out)
        self.assertNotIn("D+5", out)

    def test_estimate_price_method_shows_real_value(self):
        """예가방법(예정가격 결정방법) — 실측(2026-09-17 `nego --verify`, Run #46)으로
        확인한 prearngPrceDcsnMthdNm 필드값을 그대로 보여준다."""
        candidates, stats = self._candidates()
        candidates[0].notice.estimate_price_method = "단일예가"
        out = render_html(candidates, stats, NOW)
        self.assertIn('<td><div class="nowrap">단일예가</div></td>', out)

    def test_estimate_price_method_missing_shows_dash(self):
        candidates, stats = self._candidates()
        candidates[0].notice.estimate_price_method = None
        out = render_html(candidates, stats, NOW)
        self.assertIn('<td><div class="nowrap">-</div></td>', out)

    def test_no_period_line_when_stats_lack_period(self):
        """--from-store처럼 조회 기간 정보가 없는 실행에서도 죽지 않아야 한다."""
        candidates, _ = self._candidates()
        stats = RunStats()
        out = render_html(candidates, stats, NOW)
        self.assertNotIn("조회 기간", out)
        self.assertIn("생성 시각", out)

    def test_qualification_pass_is_a_blue_check_circle(self):
        """'자격 충족'은 파란 체크원(circle-pass)으로 표시돼야 한다."""
        from nego.qualify import QualificationResult

        candidates, stats = self._candidates()
        candidates[0].qualification = QualificationResult(
            total_groups=1, missing_groups=[], passes=True, checked=True
        )
        out = render_html(candidates, stats, NOW)
        self.assertIn('<span class="circle circle-pass">', out)
        self.assertIn('aria-label="자격 충족"', out)

    def test_tooltip_position_script_is_present_for_dynamic_placement(self):
        """실측: 팝업을 위/아래 한쪽으로만 고정하면 표의 반대쪽 끝 행에서
        뷰포트 밖으로 잘린다. 원 오른쪽(공간 없으면 왼쪽)에, 세로도 뷰포트
        안으로 클램프해서 여는 스크립트가 리포트에 포함돼야 한다
        (Playwright로 상단/하단/우측 행 모두 뷰포트 안에 들어오는 것을
        별도로 확인함)."""
        candidates, stats = self._candidates()
        out = render_html(candidates, stats, NOW)
        self.assertIn("<script>", out)
        self.assertIn("position:fixed", out)
        self.assertIn(".qual-dot, .badge.overseas", out)

    def test_qualification_pass_tooltip_lists_satisfied_names_like_the_fail_one(self):
        """파란 원(자격 충족)도 빨간 원과 같은 형식으로 충족된 자격명·코드번호를
        호버/포커스 팝업에 보여줘야 한다."""
        from nego.qualify import LicenseGroup, QualificationResult

        candidates, stats = self._candidates()
        name = "실내건축공사업(4990)"
        candidates[0].qualification = QualificationResult(
            total_groups=1,
            missing_groups=[],
            passes=True,
            checked=True,
            satisfied_groups=[LicenseGroup(group_no="1", allowed_names=[name])],
        )
        out = render_html(candidates, stats, NOW)
        self.assertIn('<span class="circle circle-pass">', out)
        self.assertIn(f'<div class="tip-item">{name}</div>', out)
        self.assertIn('<div class="tip-title" style="color:var(--pass-fg);">충족된 자격 1건</div>', out)

    def test_tooltip_reformats_raw_slash_code_to_name_code(self):
        """실측(2026-09-17): 면허제한정보 API의 lcnsLmtNm 필드는 "이름/코드"로
        코드를 그대로 붙여 내려온다(예: "실내건축공사업/4990") — 첨부파일에서
        뽑은 "이름(코드)" 항목과 표기가 안 맞았다. 표시 직전에만 변환하고
        (qualify.py의 allowed_names 자체, 즉 매칭용 값은 그대로 둔다 —
        `test_substring_matching_is_permissive` 참고), 이름이 같으면 코드
        있는 쪽만 남긴다."""
        from nego.qualify import LicenseGroup, QualificationResult

        candidates, stats = self._candidates()
        candidates[0].qualification = QualificationResult(
            total_groups=1,
            missing_groups=[],
            passes=True,
            checked=True,
            satisfied_groups=[LicenseGroup(group_no="1", allowed_names=["실내건축공사업/4990", "실내건축공사업"])],
        )
        out = render_html(candidates, stats, NOW)
        self.assertIn('<div class="tip-item">실내건축공사업(4990)</div>', out)
        self.assertNotIn("실내건축공사업/4990", out)
        self.assertEqual(out.count('class="tip-item"'), 1, "이름이 겹치면 코드 있는 쪽 하나만 남아야 한다")

    def test_missing_qualification_tooltip_dedupes_same_name_across_groups(self):
        """실측: 단양군 미디어아트 공고(R26BK01731335) — 첨부문서 참가자격 절에
        같은 세부품명번호(조명용제어장치)가 항목 3개에 걸쳐 등장하면
        `evaluate_attachment_text`가 미충족 그룹 3개를 만든다. 빨간 체크원
        호버 팝업 안 목록은 이름 기준으로 한 번만 나와야 한다(summary()의
        중복 제거와 동일)."""
        from nego.qualify import LicenseGroup, QualificationResult

        candidates, stats = self._candidates()
        name = "조명용제어장치(3912110702)"
        candidates[0].qualification = QualificationResult(
            total_groups=3,
            missing_groups=[LicenseGroup(group_no=str(i), allowed_names=[name]) for i in range(3)],
            passes=False,
            checked=True,
        )
        out = render_html(candidates, stats, NOW)
        self.assertIn('<span class="circle circle-fail">', out)
        self.assertEqual(out.count(f"✗ {name}</div>"), 1)
        self.assertIn("미보유 자격 1건</div>", out)

    def test_partial_qualification_is_yellow_box_with_held_and_missing_split(self):
        """2026-10-21 사용자 요청: 일부 보유(충족 요건 1개 이상 + 미보유 1개 이상)면 빨간 원 대신 노란 바탕 체크박스,
        팝업은 메일 '자격 미보유' 상자와 같은 묶음으로 미보유 / 보유를 나눠 보여준다."""
        from nego.qualify import LicenseGroup, QualificationResult

        candidates, stats = self._candidates()
        candidates[0].qualification = QualificationResult(
            total_groups=2,
            missing_groups=[LicenseGroup(group_no="1", allowed_names=["조명용제어장치(3912110702)"])],
            passes=False,
            checked=True,
            satisfied_groups=[LicenseGroup(group_no="2", allowed_names=["실내건축공사업(4990)"])],
        )
        out = render_html(candidates, stats, NOW)
        self.assertIn('<span class="circle box-partial">', out)
        self.assertNotIn('<span class="circle circle-fail">', out)
        self.assertIn("일부 보유 — 보유 1 · 미보유 1", out)
        miss, held = out.index("미보유 1건</div>"), out.index(";\">보유 1건</div>")
        self.assertLess(miss, out.index("✗ 조명용제어장치(3912110702)"))
        self.assertLess(out.index("✗ 조명용제어장치(3912110702)"), held, "미보유 칸이 보유 칸보다 먼저")
        self.assertLess(held, out.index("✓ 실내건축공사업(4990)"))

    def test_or_requirement_shows_alternatives_in_one_block(self):
        """'A 또는 B' 요건은 한 묶음("아래 2개 중 1개 이상")으로 — 각각 미보유 줄로 늘어놓지 않는다."""
        from nego.qualify import LicenseGroup, QualificationResult

        candidates, stats = self._candidates()
        candidates[0].qualification = QualificationResult(
            total_groups=1,
            missing_groups=[LicenseGroup(group_no="1", allowed_names=["조경공사업(0005)", "토목공사업(0001)"])],
            passes=False,
            checked=True,
        )
        out = render_html(candidates, stats, NOW)
        self.assertIn('<span class="circle circle-fail">', out)
        self.assertIn("아래 2개 중 1개 이상 — 미보유", out)

    def test_qualification_unchecked_is_a_neutral_circle(self):
        """자격정보가 없어 판정을 못 한(통과 처리된) 경우는 노란 원(circle-unchecked)으로,
        '자격 미달'과 헷갈리지 않는 중립적인 문구로 표시돼야 한다."""
        from nego.qualify import QualificationResult

        candidates, stats = self._candidates()
        candidates[0].qualification = QualificationResult(
            total_groups=0, missing_groups=[], passes=True, checked=False
        )
        out = render_html(candidates, stats, NOW)
        self.assertIn('<span class="circle circle-unchecked">', out)
        self.assertIn('aria-label="자격정보 미확인 (통과)"', out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
