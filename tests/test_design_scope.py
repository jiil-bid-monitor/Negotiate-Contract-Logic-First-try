"""설계 용역이 제작·설치까지 하는지 (2026-10-19 사용자 요청 — 표시만)."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nego.design_scope import design_scope  # noqa: E402


class TestDesignScope(unittest.TestCase):
    def test_not_design_service(self):
        self.assertIsNone(design_scope("○○체험관 전시물 제작·설치", "용역", "x"))
        self.assertIsNone(design_scope("○○박물관 설계공모", "용역", "x"))

    def test_title_has_make(self):
        d = design_scope("국립디자인박물관 전시·체험 실시설계 및 제작·설치", "용역", "")
        self.assertEqual((d["kind"], d["source"]), ("설계+제작설치", "공고명"))

    def test_attachment_has_make(self):
        text = "가. 전시물 제작·설치\n나. 영상 콘텐츠 제작 및 설치\n다. 체험물 제작설치"
        self.assertEqual(design_scope("○○체험관 전시 설계 용역", "용역", text)["kind"], "설계+제작설치")

    def test_design_only(self):
        text = "1. 과업 범위\n- 전시 기본설계 및 실시설계\n- 전시물 제작·설치는 별도 발주 예정"
        d = design_scope("○○박물관 리모델링 기본 및 실시설계 용역", "용역", text)
        self.assertEqual(d["kind"], "설계만")
        self.assertIn("별도 발주", d["lines"][0])
        self.assertEqual(design_scope("○○박물관 리모델링 공사 설계 용역", "용역", "건축 설계도서 작성")["kind"], "설계만")

    def test_unread(self):
        self.assertEqual(design_scope("○○체험관 전시 설계 용역", "용역", "")["kind"], "확인 필요")


if __name__ == "__main__":
    unittest.main()
