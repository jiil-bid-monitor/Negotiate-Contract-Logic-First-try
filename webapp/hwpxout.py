"""낙찰 서류 엑셀 → 한글(hwpx) — 담당자가 채워 **엑셀에서 저장한** 파일을 우리 서식 모양 그대로(11/25 사용자 '한글로 받기').

엑셀이 저장하면 수식 값이 파일에 남아 한셀·한글 없이(NAS에서도) 만든다. 저장하지 않은 파일(웹앱이 막 만든 것)은 값이 없어 만들지 않고 알린다.
칸 너비·줄 높이·병합·테두리·바탕색·글자·정렬·그림을 한글 표로 옮기고, 탭 하나 = 구역 하나라 쪽 여백·가로 쪽도 그대로.
한글은 칸보다 긴 글을 다음 줄로 넘겨 줄 높이를 늘리므로, 엑셀 '칸에 맞춤'처럼 글자를 줄여 쪽이 밀리지 않게 한다.
shortcut: 조건부 서식은 예정공정표 막대만(다른 틀엔 없음), 엑셀 차트·도형은 안 옮김 — 틀에 생기면 그때 더할 것.
"""
from __future__ import annotations

import hashlib
import io
import re
import struct
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from openpyxl.styles.numbers import BUILTIN_FORMATS

BLANK = Path(__file__).resolve().parent.parent / "quantdocs" / "assets" / "blank.hwpx"
PAGE_W, PAGE_H = 59528, 84188  # A4, HWPUNIT(1/7200인치)
PX = 75                        # 96dpi 1픽셀
EMU_PX = 9525
SKIP = re.compile(r"^(기본사항|점수표|참고_|⚠)")
SIDE = {"thin": ("SOLID", "0.25 mm"), "hair": ("SOLID", "0.12 mm"), "medium": ("SOLID", "0.5 mm"), "thick": ("SOLID", "0.7 mm"),
        "double": ("DOUBLE_SLIM", "0.5 mm"), "dotted": ("DOT", "0.25 mm"), "dashed": ("DASH", "0.25 mm"), "mediumDashed": ("DASH", "0.5 mm"),
        "dashDot": ("DASH_DOT", "0.12 mm"), "dashDotDot": ("DASH_DOT_DOT", "0.12 mm")}
ALIGN = {"center": "CENTER", "centerContinuous": "CENTER", "right": "RIGHT", "distributed": "DISTRIBUTE", "justify": "JUSTIFY"}
NONE = ("NONE", "0.1 mm")
BUILTIN_MONEY = {BUILTIN_FORMATS[i] for i in (5, 6, 7, 8, 42, 44)}
THEME = ["FFFFFF", "000000", "E7E6E6", "44546A", "4472C4", "ED7D31", "A5A5A5", "FFC000", "5B9BD5", "70AD47"]
NOT_SAVED = "서류를 채우고 저장한 뒤 다시 눌러 주세요. 저장하지 않은 파일은 계산된 칸이 비어 있습니다."


class NotSaved(ValueError):
    pass


# ── 값 → 화면 글자 ──────────────────────────────────────────
def show(value: Any, fmt: str) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (datetime, date)):
        return _show_date(value, fmt)
    if isinstance(value, (int, float)):
        return _show_number(value, fmt)
    return str(value)


def _plain(fmt: str, keep) -> str:
    """서식 글자: 따옴표 안·역슬래시 뒤는 글자 그대로, [..]·'_x'·'*x'는 버리고, 나머지는 keep(낱말)로."""
    out, i = [], 0
    while i < len(fmt):
        c = fmt[i]
        if c == '"':
            j = fmt.find('"', i + 1)
            j = len(fmt) if j < 0 else j
            out.append(fmt[i + 1:j]); i = j + 1
        elif c == "\\" and i + 1 < len(fmt):
            out.append(fmt[i + 1]); i += 2
        elif c == "[":
            j = fmt.find("]", i)
            i = len(fmt) if j < 0 else j + 1
        elif c in "_*":  # '* ' = 칸을 채우는 빈칸 → 하나만
            if c == "*" and i + 1 < len(fmt):
                out.append(fmt[i + 1])
            i += 2
        else:
            m = re.match(r"y+|m+|d+|h+|s+|[#0?,.]+|%|.", fmt[i:], re.I)
            out.append(keep(m.group(0))); i += m.end()
    return "".join(out)


def _show_date(v, fmt: str) -> str:
    if fmt in ("mm-dd-yy", "General"):  # 엑셀 기본 날짜(한국 설정)
        return f"{v:%Y-%m-%d}"

    def keep(t: str) -> str:
        k = t.lower()
        if k[0] == "y": return f"{v:%Y}" if len(k) > 2 else f"{v:%y}"
        if k[0] == "m": return f"{v.month:02d}" if len(k) == 2 else str(v.month)
        if k[0] == "d": return ("월화수목금토일"[v.weekday()] if len(k) == 3 else f"{v.day:02d}" if len(k) == 2 else str(v.day))
        if k[0] == "h" and isinstance(v, datetime): return f"{v.hour:0{min(len(k), 2)}d}"
        if k[0] == "s" and isinstance(v, datetime): return f"{v.second:02d}"
        return "" if t == "@" else t
    return _plain(fmt.split(";")[0], keep).strip()


def _show_number(v, fmt: str) -> str:
    if fmt in BUILTIN_MONEY:  # 엑셀 기본 통화 서식 — 한국 설정에선 ₩
        fmt = fmt.replace('"$"', '"₩"')
    if fmt in ("General", "@", ""):
        return str(int(v)) if float(v).is_integer() else f"{v:.10g}"
    parts = fmt.split(";")
    sec = parts[0] if v > 0 or len(parts) < 2 else parts[1] if v < 0 else (parts[2] if len(parts) > 2 else parts[0])
    n = abs(v) if v < 0 and len(parts) > 1 else v
    if "%" in sec:
        n *= 100
    done = [False]

    def keep(t: str) -> str:
        if not re.fullmatch(r"[#0?,.]+", t):
            return "" if t == "@" else t
        if done[0]:
            return ""
        done[0] = True
        dec = len(t.split(".")[1]) if "." in t else 0
        return f"{n:,.{dec}f}" if "," in t else f"{n:.{dec}f}"
    return _plain(sec, keep).strip()


# ── 모양(header.xml) ────────────────────────────────────────
class Head:
    def __init__(self, xml: str):
        self.xml, self.cache = xml, {}
        self.fonts = {}
        for g in re.finditer(r'<hh:fontface lang="(\w+)"[^>]*>(.*?)</hh:fontface>', xml, re.S):
            for f in re.finditer(r'<hh:font id="(\d+)" face="([^"]+)"', g.group(2)):
                self.fonts[(g.group(1), f.group(2))] = f.group(1)
        self.cbase = re.search(r'<hh:charPr id="0".*?</hh:charPr>', xml, re.S).group(0)
        self.pbase = re.search(r'<hh:paraPr id="0".*?</hh:paraPr>', xml, re.S).group(0)
        self.next = {t: max(int(x) for x in re.findall(rf'<hh:{t} id="(\d+)"', xml)) + 1 for t in ("charPr", "paraPr", "borderFill")}
        self.new: dict[str, list[str]] = {"charPr": [], "paraPr": [], "borderFill": []}

    def _add(self, tag: str, body: str) -> str:
        i = self.next[tag]
        self.next[tag] += 1
        self.new[tag].append(re.sub(r'\bid="\d+"', f'id="{i}"', body, count=1))
        return str(i)

    def _once(self, key, make) -> str:
        if key not in self.cache:
            self.cache[key] = make()
        return self.cache[key]

    def char(self, size: int, bold: bool, under: bool, face: str, color: str = "#000000") -> str:
        def make():
            x = re.sub(r'\bheight="\d+"', f'height="{size}"', self.cbase, count=1)
            x = re.sub(r'\btextColor="[^"]*"', f'textColor="{color}"', x, count=1).replace('useFontSpace="0"', 'useFontSpace="1"')
            for lang in ("hangul", "latin", "hanja", "japanese", "other", "symbol", "user"):
                fid = self.fonts.get((lang.upper(), face))
                if fid:
                    x = re.sub(rf'(<hh:fontRef\b[^>]*\b{lang}=")\d+', rf"\g<1>{fid}", x)
            x = re.sub(r"<hh:(bold|italic)\s*/>", "", x)
            if bold:
                x = x.replace("<hh:underline", "<hh:bold/><hh:underline", 1)
            if under:
                x = re.sub(r'(<hh:underline type=")\w+', r"\g<1>BOTTOM", x)
            return self._add("charPr", x)
        return self._once(("c", size, bold, under, face, color), make)

    def para(self, align: str, spacing: int = 100) -> str:
        def make():
            x = re.sub(r'\bhorizontal="\w+"', f'horizontal="{align}"', self.pbase, count=1)
            x = re.sub(r'(<hh:lineSpacing type=")\w+(" value=")\d+', rf"\g<1>PERCENT\g<2>{spacing}", x)
            x = re.sub(r'(<hc:(?:intent|left|right|prev|next) value=")-?\d+', r"\g<1>0", x)
            return self._add("paraPr", x)
        return self._once(("p", align, spacing), make)

    def border(self, sides, fill: str | None = None, diag: str = "") -> str:
        def make():
            s = "".join(f'<hh:{k}Border type="{t}" width="{w}" color="#000000"/>' for k, (t, w) in zip(("left", "right", "top", "bottom"), sides))
            brush = f'<hc:fillBrush><hc:winBrush faceColor="{fill}" hatchColor="#999999" alpha="0"/></hc:fillBrush>' if fill else ""
            up, down = ("CENTER" if "up" in diag else "NONE"), ("CENTER" if "down" in diag else "NONE")
            return self._add("borderFill", '<hh:borderFill id="0" threeD="0" shadow="0" centerLine="NONE" breakCellSeparateLine="0">'
                             f'<hh:slash type="{up}" Crooked="0" isCounter="0"/><hh:backSlash type="{down}" Crooked="0" isCounter="0"/>{s}'
                             f'<hh:diagonal type="SOLID" width="0.12 mm" color="#000000"/>{brush}</hh:borderFill>')
        return self._once(("b", sides, fill, diag), make)

    def out(self, sections: int) -> str:
        x = self.xml
        for tag, group in (("charPr", "charProperties"), ("paraPr", "paraProperties"), ("borderFill", "borderFills")):
            x = x.replace(f"</hh:{group}>", "".join(self.new[tag]) + f"</hh:{group}>", 1)
            x = re.sub(rf'(<hh:{group}\b[^>]*\bitemCnt=")(\d+)"', lambda m: f'{m.group(1)}{int(m.group(2)) + len(self.new[tag])}"', x, count=1)
        return re.sub(r'(\bsecCnt=")\d+"', rf'\g<1>{sections}"', x, count=1)


def digit_px(wb) -> int:
    """엑셀 열 너비 단위(기본 글꼴 숫자 폭 px). 엑셀이 저장한 너비는 픽셀 ÷ 이 값이라 거꾸로 찾음."""
    ws = {d.width for s in wb.worksheets for d in s.column_dimensions.values() if d.width and d.width < 255}
    off = lambda n: sum(abs(x * n - round(x * n)) for x in ws)  # noqa: E731
    base = round((wb._fonts[0].sz or 11) * 96 / 72 * 0.53)  # 글꼴에 따라 한 칸 넓음(돋움 11 = 9) — 한셀이 저장한 너비는 조금 어긋나 후보를 둘로만
    return min((base, base + 1), key=off)


def rgb(c, default=None) -> str | None:
    """엑셀 색 → '#RRGGBB'. 없거나 흰 바탕이면 default."""
    if c is None:
        return default
    v = None
    if c.type == "rgb" and isinstance(c.rgb, str):
        v = c.rgb[-6:]
    elif c.type == "indexed":  # 옛 색 번호(64 = 글자색)
        from openpyxl.styles.colors import COLOR_INDEX
        v = "000000" if c.indexed == 64 else COLOR_INDEX[c.indexed][-6:] if c.indexed < 64 else None
    elif c.type == "theme" and c.theme is not None and c.theme < len(THEME):
        v, t = THEME[c.theme], c.tint or 0
        if t:
            ch = [int(v[i:i + 2], 16) for i in (0, 2, 4)]
            ch = [round(x * (1 + t)) if t < 0 else round(x + (255 - x) * t) for x in ch]
            v = "".join(f"{x:02X}" for x in ch)
    return "#" + v.upper() if v else default


def text_width(text: str, size: float) -> float:
    """글 너비 어림(맑은 고딕, HWPUNIT) — 한글·전각 1.0, 영문·숫자 0.56, 빈칸 0.35 — 한글 PDF에서 잰 값."""
    return sum(0.35 if c == " " else 0.56 if ord(c) < 0x2000 else 1.0 for c in text) * size


# ── 탭 하나 ─────────────────────────────────────────────────
class Sheet:
    def __init__(self, ws, head: Head, pics: "Pics", digit: int = 8):
        self.ws, self.hd, self.pics, self.digit = ws, head, pics, digit
        from openpyxl.utils.cell import range_boundaries
        pa = ws.print_area
        pa = pa[0] if isinstance(pa, (list, tuple)) else pa
        self.c1, self.r1, self.c2, self.r2 = range_boundaries(pa.split(",")[0].split("!")[-1].replace("$", ""))
        pm = ws.page_margins
        self.land = ws.page_setup.orientation == "landscape"
        pw, ph = (PAGE_H, PAGE_W) if self.land else (PAGE_W, PAGE_H)
        self.margin = [int((x or 0) * 7200) for x in (pm.left, pm.right, pm.top, pm.bottom)]
        self.w, self.h = pw - self.margin[0] - self.margin[1], int((ph - self.margin[2] - self.margin[3]) * 0.985)
        self._sizes()

    def _sizes(self) -> None:
        ws = self.ws
        base = (ws.sheet_format.defaultColWidth or ws.sheet_format.baseColWidth + 0.71) if ws.sheet_format else 8.71
        widths, hidden = {}, set()
        for d in ws.column_dimensions.values():  # 엑셀이 저장하면 같은 너비 열이 한 줄로 묶임(min~max)
            for c in range(d.min or 0, min(d.max or 0, self.c2) + 1):
                widths[c] = d.width if d.width else base
                if d.hidden:
                    hidden.add(c)
        self.cols = [c for c in range(self.c1, self.c2 + 1) if c not in hidden]
        dh = (ws.sheet_format.defaultRowHeight if ws.sheet_format else None) or 16.5
        self.rows = [r for r in range(self.r1, self.r2 + 1) if not ws.row_dimensions[r].hidden]
        cw = {c: round(widths.get(c, base) * self.digit) * PX for c in self.cols}
        rh = {r: int((ws.row_dimensions[r].height or dh) * 100) for r in self.rows}
        one = ws.page_setup.fitToHeight != 0  # 0 = 높이는 여러 쪽(사진대지)
        self.k = min(1.0, self.w / max(1, sum(cw.values())), self.h / max(1, sum(rh.values())) if one else 1.0)
        self.cw = {c: int(v * self.k) for c, v in cw.items()}
        self.rh = {r: int(v * self.k) for r, v in rh.items()}
        self.pages, page, used = [], [], 0
        brk = {b.id for b in ws.row_breaks.brk}
        for r in self.rows:
            if page and (used + self.rh[r] > self.h * 1.005 and not one):
                self.pages.append(page); page, used = [], 0
            page.append(r); used += self.rh[r]
            if r in brk and not one:
                self.pages.append(page); page, used = [], 0
        if page:
            self.pages.append(page)
        self.top = self.margin[2]
        if ws.print_options.verticalCentered and len(self.pages) == 1:
            self.top += max(0, (self.h - sum(self.rh.values())) // 2)

    def _merge_of(self):
        span, covered = {}, {}
        for m in self.ws.merged_cells.ranges:
            for r in range(m.min_row, m.max_row + 1):
                for c in range(m.min_col, m.max_col + 1):
                    covered[(r, c)] = (m.min_row, m.min_col)
            span[(m.min_row, m.min_col)] = (m.max_row, m.max_col)
        return span, covered

    def _images(self, alone: bool) -> tuple[dict[int, list], list]:
        """({시작 줄: [(그림, x, y, 너비, 높이)]}, 쪽 그림들) — 표 위 그림(로고·도장·사진)과 쪽을 통째로 차지하는 증빙 그림."""
        marks: dict[int, list] = {}
        full = []
        for im in self.ws._images:
            a = im.anchor
            f = getattr(a, "_from", None)
            if f is None:
                continue
            if getattr(a, "to", None) is not None:
                w, h = self._span_px(f.col, f.colOff, a.to.col, a.to.colOff), self._span_pt(f.row, f.rowOff, a.to.row, a.to.rowOff)
            else:
                w, h = a.ext.width / EMU_PX * PX, a.ext.height / EMU_PX * PX
            w, h = int(w * self.k), int(h * self.k)
            if alone and h > self.h * 0.45 and w > self.w * 0.4:
                full.append((im, f.row))
                continue
            if abs(w / self.k - 7200) < 600 and abs(h / self.k - 7200) < 600 and im.width > im.height * 1.5:
                h = int(w * im.height / im.width)  # 한셀이 크기를 잃고 1인치 네모로 저장한 로고
            x = sum(self.cw.get(c, 0) for c in range(self.c1, f.col + 1)) + int(f.colOff / EMU_PX * PX * self.k)
            marks.setdefault(f.row + 1, []).append((im, x, int(f.rowOff / EMU_PX * PX * self.k), w, h))
        return marks, [im for im, _ in sorted(full, key=lambda x: x[1])]

    def _span_px(self, c0, o0, c1, o1) -> float:
        raw = lambda c: self.cw.get(c + 1, 0) / self.k  # noqa: E731
        return sum(raw(c) for c in range(c0, c1)) + (o1 - o0) / EMU_PX * PX

    def _span_pt(self, r0, o0, r1, o1) -> float:
        raw = lambda r: self.rh.get(r + 1, 0) / self.k  # noqa: E731
        return sum(raw(r) for r in range(r0, r1)) + (o1 - o0) / EMU_PX * PX

    def _bars(self) -> tuple[dict[tuple[int, int], str], set[tuple[int, int]]]:
        """예정공정표 조건부 서식 — (칸 색: 막대·짝수 달·안 쓰는 칸, 왼쪽에 달 경계선을 긋는 칸). 날짜는 39행, 공종 시작·종료는 AT·AU."""
        ws, fill, line = self.ws, {}, set()
        day = lambda c: _as_date(ws.cell(39, c).value)  # noqa: E731
        for cf in ws.conditional_formatting:
            for rule in cf.rules:
                f = rule.formula[0] if rule.type == "expression" and rule.formula else ""
                if "$39" not in f:
                    continue
                color = rgb(rule.dxf.fill.bgColor) if rule.dxf and rule.dxf.fill else None
                for rg in cf.sqref.ranges:
                    r = rg.min_row
                    a, z, name = _as_date(ws[f"AT{r}"].value), _as_date(ws[f"AU{r}"].value), ws[f"AV{r}"].value or "노랑"
                    for c in range(rg.min_col, rg.max_col + 1):
                        d = day(c)
                        if "$AT" in f:
                            nx = day(c + 1)
                            end = nx - timedelta(days=1) if nx else _as_date(ws["AU4"].value)
                            on = bool(a and z and d and end and d <= z and end >= a and f'"{name}"' in f)
                        elif f.endswith('$39=""'):
                            on = d is None
                        elif "MOD(MONTH" in f:
                            on = bool(d and d.month % 2 == 0)
                        else:  # 달이 바뀌는 칸
                            prev = day(c - 1) if c > rg.min_col - 1 else None
                            on = bool(d and prev and d.month != prev.month)
                        if on:
                            for x in range(rg.min_row, rg.max_row + 1):
                                fill.setdefault((x, c), color) if color else line.add((x, c))
        return fill, line

    def xml(self) -> list[str]:
        """이 탭의 쪽들 — [표 문단 안 내용]."""
        ws, hd = self.ws, self.hd
        span, covered = self._merge_of()
        bars = self._bars() if any(cf.rules for cf in self.ws.conditional_formatting) else ({}, set())
        texts = {}
        for row in ws.iter_rows(min_row=self.r1, max_row=self.r2, min_col=self.c1, max_col=self.c2):
            for c in row:
                if c.value not in (None, "") and covered.get((c.row, c.column), (c.row, c.column)) == (c.row, c.column):
                    t = show(c.value, c.number_format)
                    if t:
                        texts[(c.row, c.column)] = t
        marks, full = self._images(not texts)  # 글 없는 탭의 큰 그림 = 증빙 한 장이 한 쪽(사진대지·자격증 칸의 그림은 그 자리에)
        out = []
        for page in self.pages:
            # 글·그림이 하나도 없는 탭(아직 안 붙인 증빙)만 뺌
            if any(r in page for r, _ in texts) or (not full and (texts or marks)):
                out.append(self._floats(page, marks) + self._table(page, texts, span, bars))
        for im in full:
            out.append(self.pics.page(im, self.w, self.h))
        return out

    def _floats(self, page, marks) -> str:
        out, y = "", self.top
        x0 = self.margin[0] + max(0, (self.w - sum(self.cw.values())) // 2)
        for r in page:
            for im, x, dy, w, h in marks.get(r, ()):
                out += self.pics.inline(im, w, h, (x0 + x, y + dy))
            y += self.rh[r]
        return out

    def _table(self, page, texts, span, bars) -> str:
        bars, months = bars
        ws, hd, cols = self.ws, self.hd, self.cols
        pset, cset = set(page), set(cols)
        box, hide, src = {}, set(), {}
        for (r, c), (r9, c9) in span.items():
            rr, cc = [x for x in range(r, r9 + 1) if x in pset], [x for x in range(c, c9 + 1) if x in cset]
            if not rr or not cc:
                continue
            at = (rr[0], cc[0])
            box[at] = (rr, cc)
            if at != (r, c):
                src[at] = (r, c)
            hide |= {(a, b) for a in rr for b in cc} - {at}
        free = lambda r, c: (r, c) not in texts and (r, c) not in hide and (r, c) not in box and (r, c) not in bars  # noqa: E731
        # 엑셀은 글이 옆 빈칸으로 넘쳐 보임 — 사이에 세로 선이 없으면 합침(왼쪽 정렬은 오른쪽으로, 오른쪽 정렬은 왼쪽으로)
        for r in page:
            for i, c in enumerate(cols):
                if (r, c) not in texts or (r, c) in hide or (r, c) in box:
                    continue
                cell = ws.cell(r, c)
                al, size = cell.alignment, (cell.font.sz or 11) * 100 * self.k
                if al.wrap_text or al.shrink_to_fit or text_width(texts[(r, c)], size) < self.cw[c] * 0.96:
                    continue
                h = al.horizontal or ("right" if not isinstance(cell.value, str) else "left")
                if h in ("left", "general"):
                    e = i
                    while e + 1 < len(cols) and free(r, cols[e + 1]) and not (ws.cell(r, cols[e]).border.right.style or ws.cell(r, cols[e + 1]).border.left.style):
                        e += 1
                    if e > i:
                        box[(r, c)] = ([r], cols[i:e + 1]); hide |= {(r, x) for x in cols[i + 1:e + 1]}
                elif h in ("right", "center"):
                    b = i
                    while b > 0 and free(r, cols[b - 1]) and not (ws.cell(r, cols[b]).border.left.style or ws.cell(r, cols[b - 1]).border.right.style):
                        b -= 1
                    e = i
                    while h == "center" and e + 1 < len(cols) and e - i < i - b and free(r, cols[e + 1]) and not (ws.cell(r, cols[e]).border.right.style or ws.cell(r, cols[e + 1]).border.left.style):
                        e += 1
                    if h == "center":
                        b = i - min(i - b, e - i); e = i + (i - b)
                    if b < i or e > i:
                        at = (r, cols[b])
                        box[at] = ([r], cols[b:e + 1]); src[at] = (r, c)
                        hide |= {(r, x) for x in cols[b:e + 1]} - {at}

        def edge(cells, name, off, opp):
            for r, c in cells:
                if months and ((name == "left" and (r, c) in months) or (name == "right" and (r, c + 1) in months)):
                    return SIDE["thin"]
                s = getattr(ws.cell(r, c).border, name)
                if s is not None and s.style:
                    return SIDE.get(s.style, ("SOLID", "0.12 mm"))
                rr, cc = r + off[0], c + off[1]
                if rr in pset and cc in cset:
                    s = getattr(ws.cell(rr, cc).border, opp)
                    if s is not None and s.style:
                        return SIDE.get(s.style, ("SOLID", "0.12 mm"))
            return NONE

        # 칸이 하나도 시작하지 않는 줄(큰 병합 안)은 한글이 파일을 못 엶 — 줄만 빼고 높이는 칸에 이미 들어 있음
        kept = [r for r in page if any((r, c) not in hide for c in cols)]
        keep = set(kept)
        trs = []
        for ri, r in enumerate(kept):
            tds = []
            for ci, c in enumerate(cols):
                if (r, c) in hide:
                    continue
                rr, cc = box.get((r, c), ([r], [c]))
                o = src.get((r, c), (r, c))
                cell = ws.cell(*o)
                w, h = sum(self.cw[x] for x in cc), sum(self.rh[x] for x in rr)
                sides = (edge([(x, cc[0]) for x in rr], "left", (0, -1), "right"), edge([(x, cc[-1]) for x in rr], "right", (0, 1), "left"),
                         edge([(rr[0], y) for y in cc], "top", (-1, 0), "bottom"), edge([(rr[-1], y) for y in cc], "bottom", (1, 0), "top"))
                fill = rgb(cell.fill.fgColor) if cell.fill is not None and cell.fill.fill_type == "solid" else None
                fill = bars.get((r, c)) or (None if fill in ("#FFFFFF",) else fill)
                bd = cell.border
                diag = ("up" if bd.diagonalUp else "") + ("down" if bd.diagonalDown else "") if bd.diagonal is not None and bd.diagonal.style else ""
                tds.append(self._cell(cell, texts.get(o, ""), w, h, hd.border(sides, fill, diag), ci, ri, len(cc), sum(x in keep for x in rr)))
            trs.append(f"<hp:tr>{''.join(tds)}</hp:tr>")
        none = hd.border((NONE,) * 4)
        uid = self.pics.uid()
        tw, th = sum(self.cw[c] for c in cols), sum(self.rh[r] for r in page)
        return (f'<hp:tbl id="{uid}" zOrder="{uid % 100000}" numberingType="TABLE" textWrap="TOP_AND_BOTTOM" textFlow="BOTH_SIDES" lock="0" '
                f'dropcapstyle="None" pageBreak="CELL" repeatHeader="0" rowCnt="{len(kept)}" colCnt="{len(cols)}" cellSpacing="0" borderFillIDRef="{none}" '
                f'noAdjust="0"><hp:sz width="{tw}" widthRelTo="ABSOLUTE" height="{th}" heightRelTo="ABSOLUTE" protect="0"/><hp:pos treatAsChar="1" '
                'affectLSpacing="0" flowWithText="1" allowOverlap="0" holdAnchorAndSO="0" vertRelTo="PARA" horzRelTo="COLUMN" vertAlign="TOP" '
                'horzAlign="CENTER" vertOffset="0" horzOffset="0"/><hp:outMargin left="0" right="0" top="0" bottom="0"/>'
                f'<hp:inMargin left="70" right="70" top="0" bottom="0"/>{"".join(trs)}</hp:tbl>')

    def _cell(self, cell, text, w, h, border, ci, ri, cs, rs) -> str:
        hd, a, f = self.hd, cell.alignment, cell.font
        horiz = a.horizontal if a.horizontal not in (None, "general") else ("left" if isinstance(cell.value, str) or cell.value is None else "right")
        pad = 0 if len(text) < 3 and w < 1500 else 60 if w < 1500 else 300 if horiz == "distributed" else 100
        left, right = pad + (int((a.indent or 0) * 900 * self.k) if horiz == "left" else 0), pad + (int((a.indent or 0) * 900 * self.k) if horiz == "right" else 0)
        size = int((f.sz or 11) * 100 * self.k)
        align = "CENTER" if pad == 0 else ALIGN.get(horiz, "LEFT")  # 좁은 칸의 '(' 하나는 오른쪽 정렬이면 한글이 안 찍음
        if horiz == "distributed" and re.search(r"[0-9A-Za-z]{2}", text):
            text, align = " ".join(re.findall(r"[0-9A-Za-z.,()-]+|[^\s]", text)), "DISTRIBUTE_SPACE"
        lines = text.split("\n") if text else [""]
        inner = max(200, w - left - right)
        spacing = 100
        if text:
            need = lambda z: sum(max(1, -(-text_width(x, z) // inner)) for x in lines)  # noqa: E731
            if a.wrap_text or len(lines) > 1 or horiz in ("distributed", "justify"):
                spacing = 130
                tw = text_width(text, size)
                if len(lines) == 1 and len(text) <= 14 and inner * 0.95 < tw <= inner * 1.15:
                    size = int(size * inner * 0.95 / tw)  # 엑셀에서 한 줄에 꼭 맞던 글 — 한글 글자 폭이 조금 넓어 끝 글자가 넘어감
                while size > 300 and (need(size) * size * spacing / 100 > h * 1.02 and h > 0):
                    if spacing > 100:
                        spacing -= 10
                    else:
                        size -= 20
            else:  # 한 줄 글 — 넘치면 엑셀처럼 줄임(한글은 다음 줄로 넘겨 줄이 커짐)
                while len(text) > 2 and size > 300 and text_width(text, size) > inner:
                    size -= 10
            size = min(size, max(300, h), max(300, int(inner * 0.88)))  # 글자 크기가 칸 너비만 하면 한글이 그 글자를 안 찍음
        else:
            size = max(100, min(size, int(h * 0.7)))
        char = hd.char(size, bool(f.b), bool(f.u and f.u != "none"), f.name or "맑은 고딕", rgb(f.color, "#000000") or "#000000")
        para = hd.para(align, spacing)
        ps = []
        for line in lines:
            ps.append(f'<hp:p id="0" paraPrIDRef="{para}" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0"><hp:run charPrIDRef="{char}">'
                      + (f"<hp:t>{escape(line)}</hp:t>" if line else "<hp:t/>") + "</hp:run></hp:p>")
        v = {"top": "TOP", "center": "CENTER", "distributed": "CENTER", "justify": "CENTER"}.get(a.vertical or "", "BOTTOM")
        return (f'<hp:tc name="" header="0" hasMargin="1" protect="0" editable="0" dirty="0" borderFillIDRef="{border}"><hp:subList id="" '
                f'textDirection="HORIZONTAL" lineWrap="BREAK" vertAlign="{v}" linkListIDRef="0" linkListNextIDRef="0" textWidth="0" textHeight="0" '
                f'hasTextRef="0" hasNumRef="0">{"".join(ps)}</hp:subList><hp:cellAddr colAddr="{ci}" rowAddr="{ri}"/><hp:cellSpan colSpan="{cs}" '
                f'rowSpan="{rs}"/><hp:cellSz width="{w}" height="{h}"/><hp:cellMargin left="{left}" right="{right}" top="0" bottom="0"/></hp:tc>')


def _as_date(v):
    return v if isinstance(v, datetime) else datetime.fromordinal(datetime(1899, 12, 30).toordinal() + int(v)) if isinstance(v, (int, float)) else None


class Pics:
    """그림 파일(같은 그림은 한 번만)과 고유 번호."""

    def __init__(self):
        self.files: dict[str, tuple[str, str, bytes]] = {}  # 내용 해시 → (id, 확장자, 내용)
        self._n = 1800000000
        self.raw: dict[int, bytes] = {}

    def uid(self) -> int:
        self._n += 1
        return self._n

    def _bin(self, im) -> tuple[str, int, int]:
        data = self.raw.get(id(im)) or im._data()  # openpyxl은 한 번 읽으면 파일을 닫음
        self.raw[id(im)] = data
        k = hashlib.sha1(data).hexdigest()
        if k not in self.files:
            ext = "png" if data[:4] == b"\x89PNG" else "jpg" if data[:2] == b"\xff\xd8" else "bmp" if data[:2] == b"BM" else "gif"
            self.files[k] = (f"image{len(self.files) + 1}", ext, data)
        w, h = _size(data) or (im.width, im.height)
        return self.files[k][0], w, h

    def inline(self, im, w: int, h: int, at: tuple[int, int] | None = None) -> str:
        """at = 종이 위 자리(없으면 글자처럼)."""
        bid, pw, ph = self._bin(im)
        uid, ow, oh = self.uid(), pw * PX, ph * PX
        return (f'<hp:pic id="{uid}" zOrder="{uid % 100000}" numberingType="PICTURE" textWrap="{"IN_FRONT_OF_TEXT" if at else "TOP_AND_BOTTOM"}" textFlow="BOTH_SIDES" lock="0" '
                f'dropcapstyle="None" href="" groupLevel="0" instid="{uid}" reverse="0"><hp:offset x="0" y="0"/><hp:orgSz width="{ow}" height="{oh}"/>'
                f'<hp:curSz width="{w}" height="{h}"/><hp:flip horizontal="0" vertical="0"/><hp:rotationInfo angle="0" centerX="{w // 2}" '
                f'centerY="{h // 2}" rotateimage="1"/><hp:renderingInfo><hc:transMatrix e1="1" e2="0" e3="0" e4="0" e5="1" e6="0"/>'
                f'<hc:scaMatrix e1="{w / ow:.6f}" e2="0" e3="0" e4="0" e5="{h / oh:.6f}" e6="0"/><hc:rotMatrix e1="1" e2="0" e3="0" e4="0" e5="1" e6="0"/>'
                f'</hp:renderingInfo><hp:imgRect><hc:pt0 x="0" y="0"/><hc:pt1 x="{ow}" y="0"/><hc:pt2 x="{ow}" y="{oh}"/><hc:pt3 x="0" y="{oh}"/></hp:imgRect>'
                f'<hp:imgClip left="0" right="{ow}" top="0" bottom="{oh}"/><hp:inMargin left="0" right="0" top="0" bottom="0"/><hp:imgDim dimwidth="{ow}" '
                f'dimheight="{oh}"/><hc:img binaryItemIDRef="{bid}" bright="0" contrast="0" effect="REAL_PIC" alpha="0"/><hp:effects/>'
                f'<hp:sz width="{w}" widthRelTo="ABSOLUTE" height="{h}" heightRelTo="ABSOLUTE" protect="0"/><hp:pos treatAsChar="{0 if at else 1}" affectLSpacing="0" '
                f'flowWithText="{0 if at else 1}" allowOverlap="{1 if at else 0}" holdAnchorAndSO="0" vertRelTo="{"PAPER" if at else "PARA"}" '
                f'horzRelTo="{"PAPER" if at else "PARA"}" vertAlign="TOP" horzAlign="LEFT" vertOffset="{at[1] if at else 0}" horzOffset="{at[0] if at else 0}"/><hp:outMargin left="0" right="0" top="0" bottom="0"/></hp:pic>')

    def page(self, im, w: int, h: int) -> str:
        _, pw, ph = self._bin(im)
        z = min(w / pw, h / ph)
        return self.inline(im, int(pw * z), int(ph * z))


def _size(data: bytes) -> tuple[int, int] | None:
    if data[:4] == b"\x89PNG":
        return struct.unpack(">II", data[16:24])
    if data[:2] == b"\xff\xd8":
        i = 2
        while i + 9 < len(data):
            if data[i] != 0xFF:
                i += 1
                continue
            m = data[i + 1]
            if 0xC0 <= m <= 0xCF and m not in (0xC4, 0xC8, 0xCC):
                h, w = struct.unpack(">HH", data[i + 5:i + 9])
                return w, h
            i += 2 + struct.unpack(">H", data[i + 2:i + 4])[0]
    return None


def convert(xlsx: Path | str) -> bytes:
    """엑셀 파일 → hwpx 내용. 엑셀에서 저장하지 않아 수식 값이 없으면 NotSaved."""
    from .xlsxread import load_workbook

    wb = load_workbook(xlsx, data_only=True)
    sheets = [ws for ws in wb.worksheets if ws.sheet_state == "visible" and ws.print_area and not SKIP.match(ws.title)]
    _check_saved(Path(xlsx), wb)
    z = zipfile.ZipFile(BLANK)
    base = z.read("Contents/section0.xml").decode("utf-8")
    top, sec_pr = base[:base.index("<hp:p ")], re.search(r"<hp:secPr.*?</hp:secPr>\s*<hp:ctrl>.*?</hp:ctrl>", base, re.S).group(0)
    hd, pics = Head(z.read("Contents/header.xml").decode("utf-8")), Pics()
    sections, digit = [], digit_px(wb)
    for ws in sheets:
        sh = Sheet(ws, hd, pics, digit)
        bodies = sh.xml()
        if not bodies:
            continue
        l, r, _, b = sh.margin
        t = sh.top
        pr = re.sub(r'<hp:pagePr[^>]*>.*?</hp:pagePr>', f'<hp:pagePr landscape="{"NARROWLY" if sh.land else "WIDELY"}" width="{PAGE_W}" height="{PAGE_H}" '
                    f'gutterType="LEFT_ONLY"><hp:margin header="0" footer="0" gutter="0" left="{l}" right="{r}" top="{t}" bottom="{b}"/></hp:pagePr>', sec_pr, count=1, flags=re.S)
        pp, ch = hd.para("CENTER"), hd.char(100, False, False, "맑은 고딕")  # 표를 담는 문단은 아주 작은 글자(빈 줄이 안 생기게)
        ps = [f'<hp:p id="0" paraPrIDRef="{pp}" styleIDRef="0" pageBreak="{1 if i else 0}" columnBreak="0" merged="0">'
              + (f'<hp:run charPrIDRef="{ch}">{pr}</hp:run>' if i == 0 else "") + f'<hp:run charPrIDRef="{ch}">{x}<hp:t/></hp:run></hp:p>'
              for i, x in enumerate(bodies)]
        sections.append(top + "".join(ps) + "</hs:sec>")
    if not sections:
        raise ValueError("한글로 바꿀 서식이 없습니다")
    return _package(z, hd, pics, sections)


def _check_saved(xlsx: Path, values) -> None:
    """웹앱이 만든 파일(openpyxl)은 수식 값이 하나도 없음 — 엑셀이 저장하면 값이 남음."""
    from .xlsxread import load_workbook

    formulas = [(ws.title, c.coordinate) for ws in load_workbook(xlsx).worksheets for row in ws.iter_rows() for c in row
                if isinstance(c.value, str) and c.value.startswith("=")]
    if formulas and all(values[t][at].value is None for t, at in formulas):
        raise NotSaved(NOT_SAVED)


def _package(z: zipfile.ZipFile, hd: Head, pics: Pics, sections: list[str]) -> bytes:
    hpf = z.read("Contents/content.hpf").decode("utf-8")
    items = "".join(f'<opf:item id="section{i}" href="Contents/section{i}.xml" media-type="application/xml"/>' for i in range(len(sections)))
    items += "".join(f'<opf:item id="{b}" href="BinData/{b}.{e}" media-type="image/{"jpeg" if e == "jpg" else e}" isEmbeded="1"/>' for b, e, _ in pics.files.values())
    hpf = re.sub(r'<opf:item id="section0"[^>]*/>', items, hpf, count=1)
    hpf = re.sub(r'<opf:itemref idref="section0"[^>]*/>', "".join(f'<opf:itemref idref="section{i}" linear="yes"/>' for i in range(len(sections))), hpf, count=1)
    rdf = z.read("META-INF/container.rdf").decode("utf-8")
    one = re.search(r'<rdf:Description rdf:about=""><ns0:hasPart[^>]*section0\.xml"/></rdf:Description><rdf:Description rdf:about="Contents/section0\.xml">.*?</rdf:Description>', rdf, re.S).group(0)
    rdf = rdf.replace(one, "".join(one.replace("section0", f"section{i}") for i in range(len(sections))))
    new = {"Contents/header.xml": hd.out(len(sections)), "Contents/content.hpf": hpf, "META-INF/container.rdf": rdf}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as w:
        for info in z.infolist():
            if info.filename == "Contents/section0.xml":
                for i, x in enumerate(sections):
                    w.writestr(f"Contents/section{i}.xml", x.encode("utf-8"), compress_type=zipfile.ZIP_DEFLATED)
                continue
            data = new[info.filename].encode("utf-8") if info.filename in new else z.read(info.filename)
            w.writestr(info.filename, data, compress_type=zipfile.ZIP_STORED if info.filename == "mimetype" else zipfile.ZIP_DEFLATED)
        for b, e, data in pics.files.values():
            w.writestr(f"BinData/{b}.{e}", data, compress_type=zipfile.ZIP_DEFLATED)
    return buf.getvalue()
