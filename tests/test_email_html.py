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
        self.assertEqual(body.count("<table"), len(self.cands), "공고마다 카드(표) 한 장")
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
        self.assertIn("자격: 사전규격 — 면허제한 정보 없음, 원문 확인", body)

    def test_empty(self):
        self.assertIn("조건에 맞는 공고가 없습니다", render_email_html([], self.stats, NOW))


if __name__ == "__main__":
    unittest.main()
