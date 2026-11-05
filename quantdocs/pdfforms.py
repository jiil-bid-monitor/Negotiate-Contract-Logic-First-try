"""PDF 제안요청서의 서식 쪽 → 고칠 수 있는 한글(HWPX) 서식으로 다시 만들기(11/05 사용자 결정 (다) — 담당자가 첨삭할 수 있게).

나라장터 첨부가 PDF뿐이면 서식을 자르거나 채울 수 없었다. 서식 쪽만 그림으로 그려
  ① 쪽 위쪽에 '[서식 N]'·'서식 N' 머리가 있는 쪽만 서식으로 본다(본문 속 '서식' 낱말은 거름) — 다음 서식 머리 전까지가 그 서식,
  ② 그림의 검은 가로·세로 선으로 표 칸(합친 칸 포함)을 찾고,
  ③ 선을 지운 그림을 글자 인식(OCR)해 글자를 칸·문단에 넣어
한글 표·문단으로 다시 쓴다. 만든 파일은 한글 서식처럼 잘라(forms_cut.cut) 회사 정보를 채우고 통합서식으로 잇는다.
칸 모양·글자는 원본과 다를 수 있어 담당자 확인용 — 원본 PDF 쪽도 함께 본다.
"""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from xml.sax.saxutils import escape

BLANK = Path(__file__).resolve().parent / "assets" / "blank.hwpx"
DPI = 300
HU = 7200 / DPI  # 그림 1픽셀 = HWPUNIT
_HEAD = re.compile(r"^\s*[\[\(〔〈<【]?\s*서\s*식\s*(?:제\s*)?(\d+)\s*(?:[-‐–]\s*(\d+))?\s*(?:호)?\s*[\]\)〕〉>】]?")


@dataclass
class Word:
    text: str
    x0: int
    y0: int
    x1: int
    y1: int


@dataclass
class Table:
    xs: list[int]
    ys: list[int]
    cells: list[tuple[int, int, int, int]]  # (줄, 칸, 줄 수, 칸 수)
    words: dict[tuple[int, int], list[Word]] = field(default_factory=dict)
    shade: set[tuple[int, int]] = field(default_factory=set)  # 회색 바탕 칸

    @property
    def top(self) -> int:
        return self.ys[0]


# ── 그림 → 선·표 ──────────────────────────────────────────────
def _runs(mask, min_len: int):
    """2차원 참/거짓 배열의 줄마다 참이 min_len 이상 이어진 구간 → [(줄, 시작, 끝)]."""
    import numpy as np

    out = []
    m = mask.astype(np.int8)
    pad = np.zeros((m.shape[0], 1), dtype=np.int8)
    d = np.diff(np.hstack([pad, m, pad]), axis=1)
    for r in range(m.shape[0]):
        starts = np.where(d[r] == 1)[0]
        ends = np.where(d[r] == -1)[0]
        for s, e in zip(starts, ends):
            if e - s >= min_len:
                out.append((r, int(s), int(e)))
    return out


def _merge_segments(segs, tol: int = 3):
    """같은 선을 이루는 가까운 줄(두께)·겹치는 구간을 하나로 → [(위치, 시작, 끝)]."""
    segs = sorted(segs)
    lines: list[list[int]] = []
    for r, s, e in segs:
        for ln in lines:
            if abs(r - ln[0]) <= tol and s <= ln[2] + tol and e >= ln[1] - tol:
                ln[1], ln[2] = min(ln[1], s), max(ln[2], e)
                ln[0] = r if r - ln[0] <= tol else ln[0]
                break
        else:
            lines.append([r, s, e])
    return [tuple(x) for x in lines]


def _cluster(vals: list[int], tol: int) -> list[int]:
    out: list[list[int]] = []
    for v in sorted(vals):
        if out and v - out[-1][-1] <= tol:
            out[-1].append(v)
        else:
            out.append([v])
    return [int(round(sum(c) / len(c))) for c in out]


def find_tables(gray) -> tuple[list[Table], object]:
    """회색조 그림 → 표들과 '선 픽셀' 표시(OCR 전에 지움)."""
    import numpy as np

    dark = gray < 140
    h, w = dark.shape
    hseg = _merge_segments(_runs(dark, max(60, w // 25)))
    vseg = _merge_segments([(c, s, e) for c, s, e in _runs(dark.T, max(45, h // 50))])
    lines_mask = np.zeros_like(dark)
    for y, x0, x1 in hseg:
        lines_mask[max(0, y - 2):y + 3, x0:x1] = True
    for x, y0, y1 in vseg:
        lines_mask[y0:y1, max(0, x - 2):x + 3] = True
    # 가로·세로 선이 맞닿은 묶음 = 표 하나
    items = [("h", s) for s in hseg] + [("v", s) for s in vseg]
    parent = list(range(len(items)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    tol = 6
    for i, (ki, a) in enumerate(items):
        for j in range(i + 1, len(items)):
            kj, b = items[j]
            if ki == kj:
                continue
            hl, vl = (a, b) if ki == "h" else (b, a)
            if vl[1] - tol <= hl[0] <= vl[2] + tol and hl[1] - tol <= vl[0] <= hl[2] + tol:
                parent[find(i)] = find(j)
    groups: dict[int, list] = {}
    for i, it in enumerate(items):
        groups.setdefault(find(i), []).append(it)
    tables = []
    for g in groups.values():
        hs = [s for k, s in g if k == "h"]
        vs = [s for k, s in g if k == "v"]
        if len(hs) < 2 or len(vs) < 2:
            continue
        ys = _cluster([y for y, _, _ in hs], 8)
        xs = _cluster([x for x, _, _ in vs], 8)
        if len(ys) < 2 or len(xs) < 2:
            continue

        def has_v(x, y0, y1):
            need = (y1 - y0) * 0.6
            return any(abs(vx - x) <= 8 and min(ve, y1) - max(vs_, y0) >= need for vx, vs_, ve in vs)

        def has_h(y, x0, x1):
            need = (x1 - x0) * 0.6
            return any(abs(hy - y) <= 8 and min(he, x1) - max(hs_, x0) >= need for hy, hs_, he in hs)
        R, C = len(ys) - 1, len(xs) - 1
        cid = [[r * C + c for c in range(C)] for r in range(R)]
        par = list(range(R * C))

        def f(i):
            while par[i] != i:
                par[i] = par[par[i]]
                i = par[i]
            return i
        for r in range(R):
            for c in range(C):
                if c + 1 < C and not has_v(xs[c + 1], ys[r], ys[r + 1]):
                    par[f(cid[r][c])] = f(cid[r][c + 1])
                if r + 1 < R and not has_h(ys[r + 1], xs[c], xs[c + 1]):
                    par[f(cid[r][c])] = f(cid[r + 1][c])
        boxes: dict[int, list[int]] = {}
        for r in range(R):
            for c in range(C):
                b = boxes.setdefault(f(cid[r][c]), [r, c, r, c])
                b[0], b[1], b[2], b[3] = min(b[0], r), min(b[1], c), max(b[2], r), max(b[3], c)
        cells, covered = [], set()
        for r0, c0, r1, c1 in sorted(boxes.values()):
            if (r0, c0) in covered:
                continue
            for r in range(r0, r1 + 1):
                for c in range(c0, c1 + 1):
                    covered.add((r, c))
            cells.append((r0, c0, r1 - r0 + 1, c1 - c0 + 1))
        t = Table(xs, ys, cells)
        for r, c, rs, cs in cells:  # 회색 바탕(이름표 칸) — 칸 안쪽 가운데의 밝기
            x0, x1, y0, y1 = xs[c] + 6, xs[c + cs] - 6, ys[r] + 6, ys[r + rs] - 6
            if x1 > x0 and y1 > y0:
                inner = gray[y0:y1, x0:x1]
                light = inner[inner > 140]
                if light.size and float(np.median(light)) < 238:
                    t.shade.add((r, c))
        tables.append(t)
    tables.sort(key=lambda t: t.top)
    return tables, lines_mask


# ── 글자 인식 ────────────────────────────────────────────────
def _ocr_words(img, psm: int = 11, dx: int = 0, dy: int = 0, min_conf: float = 30) -> list[Word]:
    """그림 → 글자 상자들(쪽 좌표로 dx·dy만큼 옮김). 쪽 전체는 흩어진 글자 모드(11), 표 칸 하나는 문단 모드(6) —
    칸마다 따로 읽어야 짧은 이름표('대표자'·'주 소')를 덜 틀림(11/05 실측: 쪽 전체 '대 a5 고루 자' → 칸 따로 '대표 자')."""
    from .importer import _ocr_setup

    tess = _ocr_setup()
    if tess is None:
        return []
    d = tess.image_to_data(img, lang="kor+eng", config=f"--psm {psm}", output_type=tess.Output.DICT)
    out = []
    for i, t in enumerate(d["text"]):
        t = (t or "").strip().strip("|ㅣ")
        if not t or float(d["conf"][i]) < min_conf or re.fullmatch(r"[|ㅣ—_\-=~.,·'\"`]+", t):
            continue
        x, y, w, h = d["left"][i], d["top"][i], d["width"][i], d["height"][i]
        out.append(Word(t, x + dx, y + dy, x + w + dx, y + h + dy))
    return out


# 서식 표 이름표 — OCR이 비슷하게 틀린 짧은 칸 글을 바로잡음('주초' → '주 소'). 회사 정보 채우기가 이름표로 칸을 찾아서
_LABELS = ["회사명", "업체명", "상호", "대표자", "주소", "소재지", "전화번호", "팩스번호", "사업자번호", "사업자등록번호", "법인등록번호",
           "사업분야", "업태", "종목", "자본금", "회사설립년도", "설립일", "성명", "생년월일", "소속", "직위", "직책", "담당업무", "연락처",
           "이메일", "홈페이지", "총직원수", "서약자", "제출자", "신청인"]


def _fix_label(text: str) -> str:
    import difflib

    sq = re.sub(r"[\s|]", "", text)
    if not 2 <= len(sq) <= 7 or re.search(r"\d", sq):
        return text
    hit = difflib.get_close_matches(sq, _LABELS, n=1, cutoff=0.5)
    if hit and hit[0] != sq and abs(len(hit[0]) - len(sq)) <= 1:
        return hit[0]
    return text


def _lines(words: list[Word]) -> list[list[Word]]:
    """글자 상자들을 줄로(세로 위치가 겹치면 같은 줄) — 줄 안은 왼쪽부터."""
    rows: list[list[Word]] = []
    for w in sorted(words, key=lambda w: (w.y0 + w.y1) / 2):
        cy = (w.y0 + w.y1) / 2
        for row in rows:
            ry0 = min(x.y0 for x in row)
            ry1 = max(x.y1 for x in row)
            if ry0 - 2 <= cy <= ry1 + 2:
                row.append(w)
                break
        else:
            rows.append([w])
    return [sorted(r, key=lambda w: w.x0) for r in rows]


def _text(row: list[Word]) -> str:
    return " ".join(w.text for w in row)


_STARTS = re.compile(r"^\s*(?:[○◦●•·\-□■▶※*]|[①-⑳]|\d{1,2}\s*[.)]|[가-하]\s*[.)]|\(\d{1,2}\)|제\s*\d+\s*조)")


def _paragraphs(lines: list[list[Word]], width: float, left: float = 0) -> list[tuple[int, str, int, bool]]:
    """줄들 → 문단(위치, 글, 글자 높이, 가운데?). 줄 끝이 문장 끝이 아니고 다음 줄이 머리표로 시작하지 않으면 한 문단으로 이음
    (인쇄된 줄마다 문단이 되면 고칠 때 줄이 엉킴)."""
    out: list[list] = []
    prev_y1 = None
    for ln in lines:
        text = _text(ln)
        y0, y1 = min(x.y0 for x in ln), max(x.y1 for x in ln)
        hgt = sorted(x.y1 - x.y0 for x in ln)[len(ln) // 2]
        mid = (ln[0].x0 + ln[-1].x1) / 2
        center = abs(mid - (left + width / 2)) < width * 0.08 and (ln[-1].x1 - ln[0].x0) < width * 0.7
        if (out and not center and not out[-1][3] and prev_y1 is not None and y0 - prev_y1 < hgt * 1.2
                and not re.search(r"[.:。)」』]\s*$|다\.?\s*$|함\s*$|음\s*$", out[-1][1]) and not _STARTS.match(text)
                and (ln[-1].x1 - ln[0].x0) > 0 and len(out[-1][1]) > 25):
            out[-1][1] += " " + text
        else:
            out.append([y0, text, hgt, center])
        prev_y1 = y1
    return [tuple(x) for x in out]


# ── 쪽 → 한글 조각 ───────────────────────────────────────────
@dataclass
class Styles:
    """빈 틀 머리 정보에 더한 글자 모양(크기별)·문단 모양(왼쪽/가운데)·칸 테두리 id."""
    header: str
    char: dict[int, str] = field(default_factory=dict)
    para_left: str = "0"
    para_center: str = "0"
    border: str = "1"
    border_gray: str = "1"
    border_none: str = "1"

    @classmethod
    def build(cls, header: str) -> "Styles":
        s = cls(header)

        def next_id(tag):
            return max(int(x) for x in re.findall(rf'<hh:{tag} id="(\d+)"', s.header)) + 1

        def add(tag, group, xml):
            s.header = re.sub(rf"(</hh:{group}>)", lambda m: xml + m.group(1), s.header, count=1)
            s.header = re.sub(rf'(<hh:{group}\b[^>]*\bitemCnt=")(\d+)"', lambda m: f'{m.group(1)}{int(m.group(2)) + 1}"', s.header, count=1)
        base = re.search(r'<hh:charPr id="0".*?</hh:charPr>', s.header, re.S).group(0)
        for pt in (9, 10, 11, 12, 14, 16, 18, 20):
            i = next_id("charPr")
            add("charPr", "charProperties", re.sub(r'\bheight="\d+"', f'height="{pt * 100}"', re.sub(r'\bid="\d+"', f'id="{i}"', base, count=1), count=1))
            s.char[pt] = str(i)
        pbase = re.search(r'<hh:paraPr id="0".*?</hh:paraPr>', s.header, re.S).group(0)
        pbase = re.sub(r'(<hh:lineSpacing type=")\w+(" value=")\d+', r"\g<1>PERCENT\g<2>130", pbase)
        for how in ("LEFT", "CENTER"):
            i = next_id("paraPr")
            add("paraPr", "paraProperties", re.sub(r'\bhorizontal="\w+"', f'horizontal="{how}"', re.sub(r'\bid="\d+"', f'id="{i}"', pbase, count=1), count=1))
            if how == "LEFT":
                s.para_left = str(i)
            else:
                s.para_center = str(i)

        def border(kind: str, fill: str) -> str:
            i = next_id("borderFill")
            sides = "".join(f'<hh:{k}Border type="{kind}" width="0.12 mm" color="#000000"/>' for k in ("left", "right", "top", "bottom"))
            brush = f'<hc:fillBrush><hc:winBrush faceColor="{fill}" hatchColor="#999999" alpha="0"/></hc:fillBrush>' if fill != "none" else ""
            add("borderFill", "borderFills", f'<hh:borderFill id="{i}" threeD="0" shadow="0" centerLine="NONE" breakCellSeparateLine="0">'
                f'<hh:slash type="NONE" Crooked="0" isCounter="0"/><hh:backSlash type="NONE" Crooked="0" isCounter="0"/>{sides}'
                f'<hh:diagonal type="SOLID" width="0.1 mm" color="#000000"/>{brush}</hh:borderFill>')
            return str(i)
        s.border = border("SOLID", "none")
        s.border_gray = border("SOLID", "#E5E5E5")
        s.border_none = border("NONE", "none")
        return s

    def size(self, px_height: float, cap: int = 20) -> str:
        """글자 상자 높이(픽셀) → 가장 가까운 글자 크기의 글자 모양 id(한글 글자 높이 ≈ 글자 크기 × 0.85), cap까지."""
        pt = min(cap, px_height / DPI * 72 / 0.85)
        best = min(self.char, key=lambda k: abs(k - pt))
        return self.char[best]


def _para(st: Styles, text: str, char: str, center: bool = False, page_break: bool = False) -> str:
    pid = st.para_center if center else st.para_left
    t = f"<hp:t>{escape(text)}</hp:t>" if text else "<hp:t/>"
    return (f'<hp:p id="0" paraPrIDRef="{pid}" styleIDRef="0" pageBreak="{1 if page_break else 0}" columnBreak="0" merged="0">'
            f'<hp:run charPrIDRef="{char}">{t}</hp:run></hp:p>')


def _table_xml(st: Styles, t: Table, scale: float, uid: int) -> str:
    colw = [max(800, int((t.xs[i + 1] - t.xs[i]) * HU * scale)) for i in range(len(t.xs) - 1)]
    rowh = [max(800, int((t.ys[i + 1] - t.ys[i]) * HU * scale)) for i in range(len(t.ys) - 1)]
    rows: dict[int, list[str]] = {}
    for r, c, rs, cs in t.cells:
        lines = _lines(t.words.get((r, c), []))
        cx0, cx1 = t.xs[c], t.xs[c + cs]
        paras = []
        for _, text, h, center in _paragraphs(lines, cx1 - cx0, cx0):
            if len(lines) <= 2:
                text = _fix_label(text)
            paras.append(_para(st, text, st.size(h, 11), center))
        body = "".join(paras) or _para(st, "", st.char[10])
        bf = st.border_gray if (r, c) in t.shade else st.border
        rows.setdefault(r, []).append(
            f'<hp:tc name="" header="0" hasMargin="0" protect="0" editable="0" dirty="0" borderFillIDRef="{bf}">'
            f'<hp:subList id="" textDirection="HORIZONTAL" lineWrap="BREAK" vertAlign="CENTER" linkListIDRef="0" linkListNextIDRef="0" '
            f'textWidth="0" textHeight="0" hasTextRef="0" hasNumRef="0">{body}</hp:subList>'
            f'<hp:cellAddr colAddr="{c}" rowAddr="{r}"/><hp:cellSpan colSpan="{cs}" rowSpan="{rs}"/>'
            f'<hp:cellSz width="{sum(colw[c:c + cs])}" height="{sum(rowh[r:r + rs])}"/>'
            f'<hp:cellMargin left="141" right="141" top="141" bottom="141"/></hp:tc>')
    trs = "".join(f"<hp:tr>{''.join(rows.get(r, []))}</hp:tr>" for r in range(len(rowh)))
    return (f'<hp:p id="0" paraPrIDRef="{st.para_left}" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0"><hp:run charPrIDRef="{st.char[10]}">'
            f'<hp:tbl id="{1800000000 + uid}" zOrder="{uid}" numberingType="TABLE" textWrap="TOP_AND_BOTTOM" textFlow="BOTH_SIDES" lock="0" '
            f'dropcapstyle="None" pageBreak="CELL" repeatHeader="1" rowCnt="{len(rowh)}" colCnt="{len(colw)}" cellSpacing="0" '
            f'borderFillIDRef="{st.border}" noAdjust="0"><hp:sz width="{sum(colw)}" widthRelTo="ABSOLUTE" height="{sum(rowh)}" '
            f'heightRelTo="ABSOLUTE" protect="0"/><hp:pos treatAsChar="1" affectLSpacing="0" flowWithText="1" allowOverlap="0" '
            f'holdAnchorAndSO="0" vertRelTo="PARA" horzRelTo="COLUMN" vertAlign="TOP" horzAlign="LEFT" vertOffset="0" horzOffset="0"/>'
            f'<hp:outMargin left="0" right="0" top="0" bottom="0"/><hp:inMargin left="141" right="141" top="141" bottom="141"/>'
            f"{trs}</hp:tbl></hp:run><hp:run charPrIDRef=\"{st.char[10]}\"><hp:t/></hp:run></hp:p>")


def page_xml(img, st: Styles, content_width: int, page_break: bool, uid: int, drop_head: bool = False) -> tuple[str, list[str]]:
    """쪽 그림 하나 → 한글 문단·표(XML), 쪽 맨 위 글(서식 머리 찾기용)."""
    import numpy as np

    gray = np.array(img.convert("L"))
    tables, lines_mask = find_tables(gray)
    clean = gray.copy()
    clean[lines_mask] = 255
    from PIL import Image

    h, w = gray.shape
    # 다른 표 안에 들어 있는 상자(표지의 '제 안 서'·'2026년 월 일' 상자)는 바깥 칸을 읽을 때 같이 읽히므로 뺌
    tables = [t for t in tables if not any(o is not t and o.xs[0] - 4 <= t.xs[0] and t.xs[-1] <= o.xs[-1] + 4
                                           and o.ys[0] - 4 <= t.ys[0] and t.ys[-1] <= o.ys[-1] + 4 for o in tables)]
    for t in tables:  # 표 칸은 칸마다 따로 읽음
        for r, c, rs, cs in t.cells:
            x0, x1, y0, y1 = t.xs[c] + 5, t.xs[c + cs] - 5, t.ys[r] + 5, t.ys[r + rs] - 5
            if x1 - x0 < 10 or y1 - y0 < 10:
                continue
            crop = clean[y0:y1, x0:x1]
            if int((crop < 140).sum()) < 30:
                continue
            # 칸 하나만 읽으면 맞게 읽고도 확신도를 0으로 줄 때가 있음('회사명' 0) — 칸 글은 확신도로 거르지 않음(글자 없는 기호만 뺌)
            t.words[(r, c)] = [x for x in _ocr_words(Image.fromarray(np.pad(crop, 20, constant_values=255)), 6, x0 - 20, y0 - 20, -1)
                               if re.search(r"[가-힣A-Za-z0-9]", x.text)]
    rest = clean.copy()
    for t in tables:  # 표 밖 글만 쪽 전체로
        rest[max(0, t.ys[0] - 3):t.ys[-1] + 3, max(0, t.xs[0] - 3):t.xs[-1] + 3] = 255
    words = _ocr_words(Image.fromarray(rest), 11)
    loose = [x for x in words if not (x.y0 > h * 0.93 and re.fullmatch(r"[-–\d\s]+", x.text))]  # 쪽 번호 '- 35 -'
    # 칸 하나짜리 짧은 제목 상자('제안업체 일반현황')는 가운데 제목 문단으로 — 이름표 칸으로 잘못 채우지 않게
    for t in list(tables):
        if len(t.cells) == 1:
            ws = t.words.get((0, 0), [])
            lines = _lines(ws)
            if ws and len(lines) <= 2 and sum(len(_text(ln)) for ln in lines) <= 30:
                loose += ws
                tables.remove(t)
    left = min([t.xs[0] for t in tables] + [x.x0 for x in loose] + [w])
    right = max([t.xs[-1] for t in tables] + [x.x1 for x in loose] + [0])
    scale = min(1.0, content_width / max(1.0, (right - left) * HU))
    blocks: list[tuple[int, str]] = []
    paras = _paragraphs(_lines(loose), w)
    cut_y = -1
    if drop_head:
        # 서식 머리 줄은 새 머리 문단('[서식 N] 제목')으로 바꾸니, 쪽 위쪽의 머리 줄(OCR이 'A 1] 제안서 표지'로 읽기도 함)과
        # 그 위('붙임 2 제출 서식' 띠 등)는 모두 뺌
        heads = [y for y, text, _, _ in paras if y < h * 0.25 and re.search(r"서\s*식\s*\[?\s*\d|\[\s*서|\d\s*\]", text)]
        if heads:
            cut_y = max(heads)
    for y, text, hgt, center in paras:
        if y <= cut_y or (drop_head and y < h * 0.2 and re.fullmatch(r"\W{0,3}\w?\W{0,3}", text)):
            continue
        blocks.append((y, _para(st, text, st.size(hgt, 20), center)))
    tables = [t for t in tables if t.ys[-1] > cut_y + 10]
    for i, t in enumerate(tables):
        blocks.append((t.top, _table_xml(st, t, scale, uid * 100 + i)))  # (머리 줄 위 표는 위에서 뺌)
    blocks.sort(key=lambda b: b[0])
    xml = "".join(x for _, x in blocks) or _para(st, "", st.char[10])
    if page_break:
        xml = re.sub(r'pageBreak="0"', 'pageBreak="1"', xml, count=1)
    top = [_text(ln) for ln in _lines([x for x in loose if x.y0 < h * 0.2])[:3]]
    return xml, top


# ── 문서 ─────────────────────────────────────────────────────
def form_pages(pdf_bytes: bytes, page_texts: list[str]) -> list[int]:
    """'서식'이 적힌 쪽(0부터) — 그다음 서식 머리가 나오기 전까지의 이어지는 쪽도 OCR 대상에 넣으려 끝까지 셈은 rebuild가 함."""
    return [i for i, t in enumerate(page_texts) if re.search(r"서\s*식", t or "")]


def rebuild(pdf_path: Path, out: Path, max_pages: int = 40,
            names: dict[str, str] | None = None) -> tuple[Path | None, list[str], dict[str, str]]:
    """PDF의 서식 쪽들 → HWPX 한 파일(서식마다 '[서식 N] 제목' 머리 문단으로 시작 — forms_cut이 그대로 자름).
    names: PDF 글에서 읽은 서식 번호 → 이름. 돌려줌: (결과, 알림, 서식 번호 → 제목)."""
    notes: list[str] = []
    try:
        import pypdfium2 as pdfium
        from pypdf import PdfReader
    except ImportError:
        return None, ["PDF 서식 다시 만들기: pypdfium2가 없어 못 함"], {}
    from .importer import _ocr_setup

    if _ocr_setup() is None:
        return None, ["PDF 서식 다시 만들기: 글자 인식 도구(Tesseract)가 없어 못 함 — docs/quantdocs.md"], {}
    data = Path(pdf_path).read_bytes()
    import io

    texts = [(p.extract_text() or "") for p in PdfReader(io.BytesIO(data)).pages]
    cands = form_pages(data, texts)
    if not cands:
        return None, [], {}
    # 서식 머리가 쪽 맨 앞(글 앞 150자)에 있는 첫 쪽부터 — 목차·본문 속 '서식' 낱말 쪽은 건너뜀(글이 깨진 PDF면 '서식'이 든 첫 쪽부터)
    heads = [i for i in cands if re.search(r"서\s*식", re.sub(r"\s+", " ", texts[i])[:150])]
    start = heads[0] if heads else cands[0]
    pdf = pdfium.PdfDocument(data)
    z = zipfile.ZipFile(BLANK)
    sec = z.read("Contents/section0.xml").decode("utf-8")
    st = Styles.build(z.read("Contents/header.xml").decode("utf-8"))
    m = re.search(r'<hp:pagePr\b[^>]*width="(\d+)"[^>]*>\s*<hp:margin\b[^>]*left="(\d+)"[^>]*right="(\d+)"', sec)
    content_width = int(m.group(1)) - int(m.group(2)) - int(m.group(3)) if m else 48000
    body, count, cur, done = [], 0, None, 0
    titles: dict[str, str] = {}
    for i in range(start, len(pdf)):
        if done >= max_pages:
            notes.append(f"PDF 서식 다시 만들기: {max_pages}쪽까지만 함")
            break
        img = pdf[i].render(scale=DPI / 72).to_pil()
        found = _pdf_head(texts[i])  # 서식 머리: PDF 글자 정보 먼저(정확), 없으면 OCR 쪽 위 글
        if found is None:
            found = _ocr_top_head(img)
        if found is None and cur and not re.search(r"서\s*식", texts[i][:200]):
            from .notice import guess_form_title

            guess = guess_form_title(texts[i])  # OCR도 못 읽으면 쪽 첫 줄이 '…각서' 같은 서식 제목인지 — 앞 서식 다음 번호
            if guess:
                found = (str(int(re.match(r"\d+", cur).group(0)) + 1), guess)
        xml, top = page_xml(img, st, content_width, False, i, drop_head=found is not None)
        if found is None:
            m = next((m for m in (_HEAD.match(ln) for ln in top) if m), None)
            if m:
                found = (m.group(1) + (f"-{m.group(2)}" if m.group(2) else ""), m.string[m.end():].strip())
        if found and names and found[0] not in names and found[0].split("-")[0] not in names:
            found = None  # PDF 글에서 읽은 서식 번호가 아님(OCR이 잘못 읽은 '서식 71' 등) — 앞 서식의 다음 쪽으로
        head = found
        if head:  # 새 서식 — 머리 문단을 '[서식 N] 제목'으로 맞춤(자르기가 알아보게), 제목은 서식 목록 이름 → PDF 머리 → OCR
            cur = head[0]
            title = (names or {}).get(cur) or head[1]
            xml = _para(st, f"[서식 {cur}] {title}".strip(), st.char[12], False, page_break=bool(body)) + _drop_first_line(xml)
            count += 1
        elif cur is None:
            continue  # 첫 서식 앞 쪽(본문 속 '서식' 낱말)
        else:
            xml = re.sub(r'pageBreak="0"', 'pageBreak="1"', xml, count=1)  # 같은 서식의 다음 쪽
        if head:
            titles[cur] = title
        body.append(xml)
        done += 1
    if not count:
        return None, notes, {}
    first_end = sec.rfind("</hp:p>") + len("</hp:p>")
    new_sec = sec[:first_end] + "".join(body) + sec[first_end:]
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w") as w:
        for info in z.infolist():
            d = {"Contents/section0.xml": new_sec.encode("utf-8"), "Contents/header.xml": st.header.encode("utf-8")}.get(info.filename)
            w.writestr(zipfile.ZipInfo(info.filename, date_time=info.date_time), d if d is not None else z.read(info.filename),
                       compress_type=zipfile.ZIP_STORED if info.filename == "mimetype" else zipfile.ZIP_DEFLATED)
    notes.append(f"{Path(pdf_path).name}: PDF 서식 {count}개({done}쪽)를 글자 인식(OCR)으로 한글 서식으로 다시 만듦 — 칸 모양·글자를 원본 PDF와 대조해 고칠 것")
    return out, notes, titles


def _ocr_top_head(img) -> tuple[str, str] | None:
    """쪽 위쪽 15%만 OCR(psm 4)해 '[서식 N] 제목' → (번호, 제목). 머리가 PDF 글 정보에 없는 쪽(11/05 창업진흥원 서식 7 —
    쪽 전체 OCR(psm 11)은 '[서직 7]'로 읽음)."""
    from .importer import _ocr_setup
    from .notice import _OCR_HEAD

    tess = _ocr_setup()
    if tess is None:
        return None
    w, h = img.size
    try:
        top = tess.image_to_string(img.crop((0, 0, w, int(h * 0.15))), lang="kor+eng", config="--psm 4")
    except Exception:  # noqa: BLE001
        return None
    m = _OCR_HEAD.search(top)
    if not m:
        return None
    title = m.group(2).strip()
    if not re.search(r"[가-힣]{2}", title):
        rest = [ln.strip() for ln in top[m.end():].splitlines() if re.search(r"[가-힣]{2}", ln)]
        title = rest[0] if rest else ""
    return re.sub(r"\s+", "", m.group(1)), title


def _pdf_head(text: str) -> tuple[str, str] | None:
    """PDF 글(글자 정보)의 쪽 앞 몇 줄에서 서식 머리 → (번호, 제목). '[서식 2] 일반현황'·'서식 일반현황서식 일반현황[ 2]'·
    '서식 서식 [ 1]  제안서 표지제안서 표지'(굵은 글씨가 두 번 찍힘). OCR이 머리를 틀리게 읽을 때(쪽 36 'A 1]') 먼저 씀."""
    from .notice import _undouble

    for ln in [x for x in (text or "").splitlines() if x.strip()][:6]:
        m = re.match(r"^\s*[\[〔〈(<【]?\s*서\s*식\s*(?:제\s*)?(\d+)\s*(?:[-‐–]\s*(\d+))?\s*호?\s*[\]〕〉)>】]?\s*(.*)$", ln)
        if m:
            return m.group(1) + (f"-{m.group(2)}" if m.group(2) else ""), _undouble(m.group(3))
        m = re.match(r"^\s*서\s*식\s*(.*?)\s*\[\s*(\d+)\s*(?:-\s*(\d+))?\s*\]\s*(.*)$", ln)
        if m:
            name = _undouble(re.split(r"서\s*식", m.group(1))[0]) or _undouble(re.split(r"\s{2,}", m.group(4).strip())[0])
            return m.group(2) + (f"-{m.group(3)}" if m.group(3) else ""), name
    return None


def _drop_first_line(xml: str) -> str:
    """쪽 맨 위 '서식' 머리 줄 문단은 새 머리 문단으로 바꿨으니 원래 것은 뺌(표 안이 아닌 첫 문단 하나)."""
    m = re.match(r'<hp:p\b[^>]*><hp:run charPrIDRef="\d+"><hp:t>(.*?)</hp:t></hp:run></hp:p>', xml)
    if m and re.search(r"서\s*식", m.group(1)):
        return xml[m.end():]
    return xml
