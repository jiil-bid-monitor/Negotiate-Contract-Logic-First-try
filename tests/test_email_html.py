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

    def test_split_parts_and_size(self):
        """10/21 사용자 요청: 관심·세부품명 메일을 나눔. 지메일 잘림(약 102KB)을 피하려고 칸마다 붙는 스타일은 짧게."""
        import dataclasses

        from nego.email_html import INTEREST, PRODUCT, split_parts
        prod = self.cands[1]
        prod.screen_result = dataclasses.replace(prod.screen_result, matched_keywords=[], matched_product_codes=["6012100201"])
        self.assertEqual([n for n, _ in split_parts(self.cands)], [INTEREST, PRODUCT])
        interest = render_email_html(self.cands, self.stats, NOW, INTEREST)
        self.assertIn("R26EM0000", interest)
        self.assertNotIn("R26EM0001", interest)
        product = render_email_html(self.cands, self.stats, NOW, PRODUCT)
        self.assertIn("R26EM0001", product)
        self.assertNotIn("R26EM0000", product)
        self.assertEqual([n for n, _ in split_parts(self.cands[:1])], [INTEREST], "세부품명 공고가 없으면 한 통")
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
