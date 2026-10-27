"""예외 낱말 후보 보고서 → 개발자 방(nego/exclusion_report.py) 테스트 — jiil 레포 없이 도는 부분만."""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from unittest import mock

from nego import exclusion_report as er


@dataclass
class FakeCandidate:
    word: str
    keyword: str
    tag: str
    notices: set = field(default_factory=set)


class TestExclusionReport(unittest.TestCase):
    def test_load_docs_groups_by_notice_and_skips_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "R26BK1_000_1_공고문.hwp.txt").write_text("가", encoding="utf-8")
            (d / "R26BK1_000_2_과업지시서.hwp.txt").write_text("나", encoding="utf-8")
            (d / "R26BK1_000_1_공고문.hwp_참가자격.txt").write_text("요약", encoding="utf-8")
            (d / "R26BK2_000_1_.txt").write_text("다", encoding="utf-8")
            docs = dict((no, text) for no, _t, text in er.load_docs(d))
        self.assertEqual(docs, {"R26BK1": "가\n나", "R26BK2": "다"})

    def test_summary_message(self):
        cands = [FakeCandidate("농식품부", "식품", "시설·주제/식품·영양", {"A", "B"}),
                 FakeCandidate("VRAM<x>", "VR", "콘텐츠·매체/VR", {"A"})]
        text = er.summary_message(cands, "학습", 30, "예외 낱말 후보 (주간)")
        self.assertIn("공고 30건 · 후보 2개 · 순위 학습", text)
        self.assertIn("1. 농식품부 ← '식품' (식품·영양) · 공고 2", text)
        self.assertIn("VRAM&lt;x&gt;", text)  # 텔레그램 HTML이라 꺾쇠는 바꿔 쓴다

    def test_skips_without_jiil_repo(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(er, "default_jiil_repo", return_value=Path(tmp)):
            self.assertEqual(er.main([tmp]), 0)


if __name__ == "__main__":
    unittest.main()
