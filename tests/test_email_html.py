"""주간 메일 본문(nego/email_html.py, 2026-10-20) — 메일 프로그램이 <style>을 지워도 안 깨지게 카드·인라인 스타일만.

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nego.config import load_config  # noqa: E402
from nego.email_html import render_email_html  # noqa: E402
from nego.models import notice_from_raw  # noqa: E402
from nego.pipeline import RunStats, build_candidates  # noqa: E402
from tests import fixtures  # noqa: E402

NOW = datetime(2026, 10, 20, 9, 0)


class TestEmailHtml(unittest.TestCase):
    def setUp(self):
        config = load_config()
        config.screen.keywords = ["전시관"]
        raws = [fixtures.notice(f"R26EM{i:04d}", title=f"○○전시관 전시물 제작설치 {i}") for i in range(2)]
        self.stats = RunStats(period_begin=datetime(2026, 10, 13), period_end=NOW)
        self.cands = build_candidates([notice_from_raw(r, "용역") for r in raws], config, {}, {}, datetime(2026, 9, 1), self.stats)

    def test_one_card_per_notice_without_style_or_popup(self):
        body = render_email_html(self.cands, self.stats, NOW)
        for no in ("R26EM0000", "R26EM0001"):
            self.assertIn(no, body)
        self.assertEqual(body.count(">공고번호</font>"), len(self.cands), "공고마다 카드 한 장")
        self.assertIn('align="center"', body, "본문은 가운데 정렬(10/21)")
        self.assertIn("max-width:1000px", body)
        self.assertNotIn("width:42%", body, "칸 너비는 %가 아니라 flex 최소 너비로 — 확대해도 같은 모양")
        self.assertIn("500,000,000원<", body)
        self.assertNotIn("원원", body)
        for banned in ("<style", "<script", "<svg", 'class="tip"'):
            self.assertNotIn(banned, body, f"메일 본문에 {banned}가 있으면 지메일에서 깨진다")
        self.assertIn("키워드:전시관", body)

    def test_shared_with_jiil_one_line(self):
        """2026-11-26 사용자: 그리다 메일 — 지일 주간 메일에도 나간 공고는 맨 아래 '지일 공고와 겹침'에 한 줄씩."""
        body = render_email_html(self.cands, self.stats, NOW, shared={"R26EM0001"})
        self.assertEqual(body.count(">공고번호</font>"), 1, "겹친 공고는 카드 없이")
        self.assertIn("지일 공고와 겹침 (1건)", body)
        self.assertIn("지일 주간 메일에 이미 실린 공고 1건은 중복해 싣지 않고", body, "받는 사람에게 겹친 공고를 왜 짧게 적었는지")
        self.assertLess(body.index("R26EM0000"), body.index("지일 공고와 겹침 ("))
        self.assertGreater(body.index("○○전시관 전시물 제작설치 1"), body.index("지일 공고와 겹침 ("))
        self.assertNotIn("지일 공고와 겹침", render_email_html(self.cands, self.stats, NOW))
        self.assertIn("그리다 메일을 따로 보냅니다", render_email_html(self.cands, self.stats, NOW), "지일 메일엔 안내 한 줄")
        from nego.cli import own_rows
        self.assertEqual([c.notice.notice_no for c in own_rows(self.cands, {"R26EM0001"})], ["R26EM0000"])
        self.assertEqual(own_rows(self.cands, {"R26EM0000", "R26EM0001"}), [], "모두 겹치면 그리다 메일을 안 보냄")
        all_same = render_email_html(self.cands, self.stats, NOW, shared={"R26EM0000", "R26EM0001"})
        self.assertNotIn("조건에 맞는 공고가 없습니다", all_same, "모두 겹치면 빈 관심 공고 칸은 안 씀")
        self.assertIn("지일 공고와 겹침 (2건)", all_same)

    def test_main_and_prespec_sub_sections(self):
        """2026-10-21 사용자 요청: 본공고·사전규격이 섞이면 소구역, 사전규격 자격 미확인은 그 이유를 적음."""
        import dataclasses
        pre = self.cands[1]
        pre.notice = dataclasses.replace(pre.notice, kind="사전규격")
        pre.qualification = dataclasses.replace(pre.qualification, checked=False)
        body = render_email_html(self.cands, self.stats, NOW)
        self.assertLess(body.index("본공고 (1건)"), body.index("R26EM0000"))
        self.assertLess(body.index("사전규격 — 면허제한 정보 없음"), body.index("R26EM0001"))
        self.assertIn(">자격</font>", body)
        self.assertIn(">사전규격 — 면허제한 정보 없음, 원문 확인</td>", body)

    def test_body_only_related_rest_in_attachment(self):
        """11/23 사용자 결정(주간 메일 269건): 본문 = 싱크로율 50% 이상(들어온 길 상관없음), '통신'만·운영관리·전산 공고는 첨부에만.
        싱크로율을 못 구한 공고는 본문에 둠."""
        import dataclasses

        from nego.email_html import split_body, split_parts
        extra = [build_candidates([notice_from_raw(fixtures.notice(f"R26EM{i:04d}", title=f"○○전시관 {i}"), "용역")],
                                  load_config(), {}, {}, datetime(2026, 9, 1), self.stats)[0] for i in (2, 3, 4)]
        cands = self.cands + extra
        cands[0].sync = {"score": 0.7}
        cands[1].screen_result = dataclasses.replace(cands[1].screen_result, matched_keywords=[], matched_product_codes=["6012100201"])
        cands[1].sync = {"score": 0.5}
        cands[2].screen_result = dataclasses.replace(cands[2].screen_result, matched_keywords=["통신"])
        cands[3].sync = {"score": 0.49}
        cands[4].notice = dataclasses.replace(cands[4].notice, title="○○전시관 시설 유지 관리 용역")
        body, hidden = split_body(cands)
        self.assertEqual([c.notice.notice_no for c in body], ["R26EM0000", "R26EM0001"], "세부품명으로 들어와도 50% 이상이면 본문")
        self.assertEqual(hidden["통신 키워드만"], 1)
        self.assertEqual(hidden["운영·관리 용역"], 1, "띄어 써도 걸림")
        self.assertEqual(hidden["싱크로율 50% 미만"], 1)
        self.assertEqual(len(split_parts(cands)), 1, "메일은 한 통")
        html = render_email_html(cands, self.stats, NOW)
        self.assertIn("본문 2건 · 첨부 리포트에만 3건", html)
        for no in ("R26EM0002", "R26EM0003", "R26EM0004"):
            self.assertNotIn(no, html)
        self.assertEqual(split_body(self.cands[1:])[0], self.cands[1:], "싱크로율 없음 = 본문")
        c = cands[0]
        c.notice = dataclasses.replace(c.notice, title="○○고 기숙사 증축공사 설계용역")
        self.assertEqual(split_body([c])[1]["설계 용역(제작·설치 없음)"], 1)
        c.notice = dataclasses.replace(c.notice, title="○○전시관 전시 설계 및 제작·설치 용역")
        self.assertEqual(split_body([c])[0], [c], "설계+제작·설치는 본문")
        for keep in ("○○ 해양생태놀이터 조성사업 기본·실시설계 용역", "○○박물관 실감콘텐츠 통합유지관리 용역"):
            c.notice = dataclasses.replace(c.notice, title=keep)
            self.assertEqual(split_body([c])[0], [c], f"11/23 사용자: 본문에 둠 — {keep}")
        c.screen_result = dataclasses.replace(c.screen_result, matched_keywords=[], matched_product_codes=["정보시스템개발서비스"])
        self.assertEqual(split_body([c])[1]["소프트웨어 품목만"], 1, "실제 자료엔 품목 이름이 들어옴")

    def test_card_size(self):
        per_card = len(render_email_html(self.cands[:1], self.stats, NOW).encode()) - len(
            render_email_html([], self.stats, NOW).encode())
        self.assertLess(per_card, 2500, "카드 한 장이 커지면(예전 3KB) 40~50건에서 지메일이 본문을 자름")

    def test_unique_tail_so_gmail_does_not_trim(self):
        """10/21 사용자: 지메일 '•••' 접힘 없애기 — 같은 내용을 두 번 보내도 본문 끝이 달라야 한다."""
        a = render_email_html(self.cands, self.stats, NOW)
        b = render_email_html(self.cands, self.stats, NOW)
        self.assertNotEqual(a[-200:], b[-200:])
        self.assertIn("display:none", a[-300:])

    def test_budget_has_single_won(self):
        """2026-10-21: 사업금액이 "500,000,000원원"으로 '원'이 두 번 찍히던 것."""
        body = render_email_html(self.cands, self.stats, NOW)
        self.assertIn(">사업금액</font>", body)  # 10/21 이름표|값 표로 바뀜
        self.assertNotIn("원원", body)

    def test_empty(self):
        self.assertIn("조건에 맞는 공고가 없습니다", render_email_html([], self.stats, NOW))


if __name__ == "__main__":
    unittest.main()
