"""적격심사 서식 모양(착수서류 표준틀 v4와 같은 틀): A4 한 쪽, 칸 24개, 굵은 바깥선·가는 칸선, 왼쪽 위 작은 로고."""

from __future__ import annotations

import os
from pathlib import Path

from openpyxl.drawing.image import Image as XImg
from openpyxl.styles import Alignment, Border, Font, Side
from openpyxl.utils import get_column_letter as L

FONT = "맑은 고딕"
NC = 24  # 칸 수 — 폭 3.0 × 24 ≈ A4 본문 폭 170mm
CW = 3.0
# 로고 그림은 저장소에 넣지 않음(회사 자료) — 서버 PC .env JG_LOGO, 없으면 NAS 표준틀 도구 폴더
LOGO = Path(os.environ.get("JG_LOGO", r"\\jiilserver\002 진행프로젝트\실습폴더\트랙1_파일 자동 생성\표준틀_도구_20261109\logo.png"))
THIN, MED = Side(style="thin"), Side(style="medium")


class Form:
    def __init__(self, wb, name):
        self.ws = wb.create_sheet(name)
        for c in range(1, NC + 1):
            self.ws.column_dimensions[L(c)].width = CW
        self.r = 1

    def cell(self, r, c1, c2, value, sz=11, b=False, u=False, h="left", v="center", wrap=False, fmt=None, indent=0):
        ws = self.ws
        if c2 > c1:
            ws.merge_cells(start_row=r, end_row=r, start_column=c1, end_column=c2)
        x = ws.cell(r, c1, value)
        x.font = Font(name=FONT, sz=sz, b=b, u="single" if u else None)
        x.alignment = Alignment(horizontal=h, vertical=v, wrap_text=wrap, indent=indent)
        if fmt:
            x.number_format = fmt
        return x

    def logo(self, tag=None):
        """왼쪽 위 작은 로고(높이 약 1cm) — 지일이 만든 자료에만. tag(붙임 번호)는 오른쪽."""
        self.ws.row_dimensions[1].height = 30
        if not LOGO.is_file():  # 로고 파일을 못 찾으면 자리만 비움
            if tag:
                self.cell(1, 19, NC, tag, sz=10, h="right")
            self.r = 2
            return
        im = XImg(str(LOGO))
        im.height, im.width = 28, int(28 * 2.21)
        self.ws.add_image(im, "A1")
        self.ws.merge_cells("A1:F1")
        if tag:
            self.cell(1, 19, NC, tag, sz=10, h="right")
        self.r = 2

    def gap(self, h=12):
        self.ws.row_dimensions[self.r].height = h
        self.r += 1

    def title(self, text, sz=20):
        self.ws.row_dimensions[self.r].height = 60
        self.cell(self.r, 1, NC, text, sz=sz, b=True, h="center")
        self.r += 1
        self.gap(30)

    def box(self, r1, r2, c1, c2, outer=MED):
        """r1~r2, c1~c2 칸마다 가는 선, 바깥은 굵은 선."""
        for r in range(r1, r2 + 1):
            for c in range(c1, c2 + 1):
                x = self.ws.cell(r, c)
                x.border = Border(left=outer if c == c1 else THIN, right=outer if c == c2 else THIN,
                                  top=outer if r == r1 else THIN, bottom=outer if r == r2 else THIN)

    def table(self, rows, lw=7, h=34):
        """[(이름표, 값, 값 서식)] — 이름표 칸(벌린 글자, 굵게) | 값 칸."""
        r1, split = self.r, []
        for label, value, *rest in rows:
            fmt = rest[0] if rest else None
            self.ws.row_dimensions[self.r].height = h
            self.cell(self.r, 1, lw, label, b=True, h="distributed")
            if isinstance(value, tuple):  # 금액: 한글 금액 | 숫자 금액(오른쪽) — 한 칸에 넣으면 엑셀에서 두 줄로 꺾임
                self.cell(self.r, lw + 1, NC - 4, value[0], h="left", indent=1, sz=10)
                self.cell(self.r, NC - 3, NC, value[1], h="right", indent=1, sz=10)
                split.append(self.r)
            else:
                self.cell(self.r, lw + 1, NC, value, h="left", indent=1, fmt=fmt, wrap=True)
            self.r += 1
        self.box(r1, self.r - 1, 1, NC)
        for r in range(r1, self.r):  # 이름표와 값 사이 선
            self.ws.cell(r, lw).border = self.ws.cell(r, lw).border.copy(right=THIN)
        for r in split:  # 한글 금액과 숫자 금액 사이엔 선 없음(한 칸처럼)
            for c, side in ((NC - 4, "right"), (NC - 3, "left")):
                x = self.ws.cell(r, c)
                x.border = x.border.copy(**{side: Side()})

    def grid(self, head, widths, rows, h=26):
        """머리줄 + 줄들. widths = 칸마다 차지할 열 수(합 24)."""
        r1 = self.r
        for i, line in enumerate([head] + rows):
            self.ws.row_dimensions[self.r].height = h
            c = 1
            for w, value in zip(widths, line):
                self.cell(self.r, c, c + w - 1, value, b=(i == 0), h="center", wrap=True, sz=10 if i else 11)
                c += w
            self.r += 1
        self.box(r1, self.r - 1, 1, NC)
        c = 1
        for w in widths[:-1]:  # 칸 사이 세로선
            c += w
            for r in range(r1, self.r):
                x = self.ws.cell(r, c - 1)
                x.border = x.border.copy(right=THIN)

    def para(self, text, lines=1, sz=11, b=False, h="left"):
        self.ws.row_dimensions[self.r].height = 22 * lines + 8
        self.cell(self.r, 1, NC, text, sz=sz, b=b, h=h, wrap=True, v="center")
        self.r += 1

    def done(self):
        ws = self.ws
        ws.print_area = f"A1:{L(NC)}{self.r - 1}"
        ws.page_setup.paperSize, ws.page_setup.orientation = ws.PAPERSIZE_A4, "portrait"
        ws.sheet_properties.pageSetUpPr.fitToPage = True
        ws.page_setup.fitToWidth, ws.page_setup.fitToHeight = 1, 1
        ws.page_margins.left = ws.page_margins.right = 0.75
        ws.page_margins.top = ws.page_margins.bottom = 0.6
        ws.print_options.horizontalCentered = True
        ws.sheet_view.showGridLines = False
        ws.protection.sheet = True  # 비밀번호 없음 — 수식을 실수로 지우는 것만 막음


