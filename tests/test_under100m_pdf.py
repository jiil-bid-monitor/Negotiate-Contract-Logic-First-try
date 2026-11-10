"""PDF 서식 — 쪽 나누기, 이름표 오른쪽 칸 입력, 대리인·증명기관 칸 비우기."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pypdf import PdfReader
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from quantdocs.bundle import _FONTS
from under100m.pdf_forms import fill_pdf, split_pdf

COMPANY = {"회사": {"name": "가나회사", "ceo": "다라", "addr": "마바시 사아로 1", "bizno": "000-00-00000", "tel": "02-000-0000"}}
FONT = next((f for f in _FONTS if Path(f).exists()), None)


def draw_table(pdf, rows, top=700):
    """rows: [[칸 글, …], …] — 왼쪽 칸 100, 오른쪽 칸 300."""
    for r, row in enumerate(rows):
        y = top - r * 30
        x = 60
        for c, text in enumerate(row):
            width = 100 if c == 0 else 300
            pdf.rect(x, y - 30, width, 30)
            if text:
                pdf.drawString(x + 4, y - 20, text)
            x += width


@unittest.skipUnless(FONT, "한글 글꼴 없음")
class PdfFormTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        pdfmetrics.registerFont(TTFont("KR", FONT, subfontIndex=0) if FONT.endswith(".ttc") else TTFont("KR", FONT))

    def make(self, name, pages):
        path = self.dir / name
        pdf = canvas.Canvas(str(path), pagesize=A4)
        for page in pages:
            pdf.setFont("KR", 11)
            page(pdf)
            pdf.showPage()
        pdf.save()
        return path

    def text_of(self, path):
        import pymupdf

        with pymupdf.open(path) as doc:
            return "".join(page.get_text() for page in doc)

    def test_split_skips_list_page_and_names_forms(self):
        def head(text):
            return lambda pdf: pdf.drawString(60, 780, text)

        src = self.make("doc.pdf", [head("본문"), head("서식번호 서식명 [별지 제1호] [별지 제2호] [별지 제3호]"),
                                    head("[별지 제1호 서식] 일반현황 및 연혁"), head("[별지 제2호 서식] 위 임 장")])
        names = split_pdf(src, self.dir / "out")
        self.assertEqual(names, ["별지01_일반현황 및 연혁.pdf", "별지02_위임장.pdf"])

    def test_pdf_forms_go_into_one_file(self):
        from types import SimpleNamespace

        from under100m.__main__ import MERGED_PDF, build_forms

        def head(text):
            return lambda pdf: pdf.drawString(60, 780, text)

        src = self.make("공고문.pdf", [head("본문"), head("[별지 제1호 서식] 일반현황 및 연혁"), head("[별지 제2호 서식] 위 임 장")])
        candidate = SimpleNamespace(notice=SimpleNamespace(title="시험 공고", notice_no="R26BK00000001", notice_ord="000"),
                                    schedule=SimpleNamespace(bid_deadline=None))
        out = self.dir / "서식자동분리"
        work = self.dir / "work"
        work.mkdir()
        checklist, _, _, names = build_forms([src], COMPANY, candidate, out, work)
        self.assertEqual(sorted(p.name for p in out.iterdir()), [MERGED_PDF])  # 서식별 파일 없이 한 파일
        self.assertEqual(len(PdfReader(out / MERGED_PDF).pages), 2)
        self.assertIn("[ ] 2. 별지02_위임장 — ", "\n".join(checklist))
        self.assertEqual(names, ["별지01_일반현황 및 연혁", "별지02_위임장"])

    def test_fills_right_cell_only_when_empty(self):
        src = self.make("a.pdf", [lambda pdf: draw_table(pdf, [["업체명(상호)", ""], ["대표자", "(인)"], ["주소", "이미 적힘"]])])
        filled, _ = fill_pdf(src, self.dir / "서식01_일반현황.pdf", COMPANY)
        text = self.text_of(self.dir / "서식01_일반현황.pdf")
        self.assertEqual(sorted(filled), ["표 대표자", "표 업체명(상호)"])
        self.assertIn("가나회사", text)
        self.assertIn("다라", text)
        self.assertNotIn("마바시", text)

    def test_agent_and_certifier_cells_stay_empty(self):
        src = self.make("b.pdf", [lambda pdf: draw_table(pdf, [["성명", ""], ["주소", ""]])])
        filled, _ = fill_pdf(src, self.dir / "서식12_위임장.pdf", COMPANY)
        self.assertEqual(filled, [])
        src = self.make("c.pdf", [lambda pdf: draw_table(pdf, [["신청인", ""], ["발급기관", ""]])])
        filled, _ = fill_pdf(src, self.dir / "서식05_실적증명서.pdf", COMPANY)
        self.assertNotIn("표 발급기관", filled)

    def test_line_label_with_colon(self):
        src = self.make("d.pdf", [lambda pdf: (pdf.drawString(60, 700, "○ 업 체 명 :"), pdf.drawString(60, 670, "○ 주 소 :                 (연락처 :    )"))])
        filled, _ = fill_pdf(src, self.dir / "서식12_위임장.pdf", COMPANY)
        self.assertEqual(filled, ["줄 업체명", "줄 주소"])
        src = self.make("e.pdf", [lambda pdf: pdf.drawString(60, 700, "대 표 자 :                (인) 또는 (서명)")])
        filled, _ = fill_pdf(src, self.dir / "별지01_신청서.pdf", COMPANY)
        self.assertEqual(filled, ["줄 대표자"])


if __name__ == "__main__":
    unittest.main()
