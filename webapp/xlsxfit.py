"""엑셀 서류의 글자 잘림 맞추기 — 웹앱이 값을 넣고 저장한 뒤 부름(11/20 사용자 '나중에도 이런 일 없도록').
칸보다 긴 글: 줄바꿈 칸이면 그 줄(합친 칸은 마지막 줄) 높이를 늘리고, 줄바꿈이 꺼진 채 옆 칸에 가려지면 줄바꿈을 켜고 높이를 늘림.
값이 없는 수식 칸도 '=기본사항!$B$8'·'=IF(기본사항!$B$8="","",기본사항!$B$8)'처럼 한 칸을 그대로 받으면 그 값으로 잼.
xlsx 안 XML만 고침(그림·도형·보호·검사 그대로). 글자 너비는 어림(한글 1.0·나머지 0.55 글자) — 아주 살짝 넘치는 칸은 못 잡을 수 있음."""
from __future__ import annotations

import os
import re
import shutil
import tempfile
import zipfile
from pathlib import Path

LINE = 1.5  # 한 줄 높이 = 글자 크기 × 1.5(맑은 고딕 11pt 기본 줄 16.5pt — 1.35로 셌더니 엑셀에서 두 줄이 잘렸음, 11/20)
_REF = re.compile(r"""^=(?:IF\(\s*(?P<a>'?[^'!]+'?!\$?[A-Z]+\$?\d+)\s*=\s*""\s*,\s*""\s*,\s*)?(?P<s>'?[^'!(),]+'?)!\$?(?P<c>[A-Z]+)\$?(?P<r>\d+)\)?$""")


def _wid(s: str, size: float) -> float:
    return sum(1.0 if ord(ch) > 0x2E80 else 0.55 for ch in s) * size * 0.1335


def _lines(text: str, size: float, w: float) -> int:
    return sum(max(1, int(-(-_wid(l, size) // (w * 0.95)))) for l in text.split("\n"))


def _sheet_map(z: zipfile.ZipFile) -> dict[str, str]:
    wbx = z.read("xl/workbook.xml").decode("utf-8")
    rels = z.read("xl/_rels/workbook.xml.rels").decode("utf-8")
    rid = {}
    for tag in re.findall(r"<Relationship [^>]*/>", rels):
        g = dict(re.findall(r'(\w+)="([^"]*)"', tag))
        rid[g["Id"]] = g["Target"]
    out = {}
    for tag in re.findall(r"<sheet [^>]*/>", wbx):
        name = re.search(r'name="([^"]*)"', tag).group(1).replace("&amp;", "&")
        t = rid[re.search(r'r:id="([^"]*)"', tag).group(1)].lstrip("/")
        out[name] = t if t.startswith("xl/") else "xl/" + t
    return out


def _cols(x: str) -> dict[int, tuple[float, bool]]:
    """{열: (너비, 숨김)} — openpyxl은 <col min max> 범위를 잘못 읽어 XML에서 직접. 0 = 기본 너비."""
    cols: dict[int, tuple[float, bool]] = {}
    for a in re.findall(r"<col [^>]*/>", x):
        g = dict(re.findall(r'(\w+)="([^"]*)"', a))
        for c in range(int(g["min"]), min(int(g["max"]), 300) + 1):
            cols[c] = (float(g.get("width", 8.43)), g.get("hidden") in ("1", "true"))
    dw = re.search(r'<sheetFormatPr [^>]*defaultColWidth="([^"]+)"', x)
    bw = re.search(r'<sheetFormatPr [^>]*baseColWidth="([^"]+)"', x)
    cols[0] = (float(dw.group(1)) if dw else (float(bw.group(1)) + 0.71 if bw else 8.43), False)
    return cols


def _outside_print(ws, has_drawing):
    """그림이 있는 탭에서 인쇄 영역 밖 칸(안내 상자) — 줄을 늘리면 붙인 그림이 늘어나서 안 건드림."""
    if not has_drawing or not ws.print_area:
        return lambda r, c: False
    from openpyxl.utils import range_boundaries
    boxes = [range_boundaries(a.split("!")[-1].replace("$", "")) for a in str(ws.print_area).split(",")]
    return lambda r, c: not any(c0 <= c <= c1 and r0 <= r <= r1 for c0, r0, c1, r1 in boxes)


def plan(path: Path) -> dict[str, dict]:
    """→ {시트 xml 경로: {'rows': {줄: 새 높이}, 'wrap': {칸 주소}, 'title': 시트 이름}} — 잘리는 칸이 있는 시트만."""
    from openpyxl import load_workbook

    with zipfile.ZipFile(path) as z:
        smap = _sheet_map(z)
        xml = {n: z.read(p).decode("utf-8") for n, p in smap.items()}
    wb = load_workbook(path)

    def value(cell):
        v = cell.value
        if isinstance(v, str) and v.startswith("="):
            m = _REF.match(v.replace(" ", "") if "IF(" in v else v)
            if not m or m.group("s").strip("'") not in wb.sheetnames:
                return None
            v = wb[m.group("s").strip("'")][f"{m.group('c')}{m.group('r')}"].value
            if isinstance(v, str) and v.startswith("="):
                return None
        return v if isinstance(v, str) and v.strip() else None

    out: dict[str, dict] = {}
    for ws in wb.worksheets:
        if ws.sheet_state != "visible" or ws.title not in smap:
            continue
        cols = _cols(xml[ws.title])
        skip = _outside_print(ws, "<drawing" in xml[ws.title])
        dflt = ws.sheet_format.defaultRowHeight or 15

        def cw(c):
            w, hidden = cols.get(c, cols[0])
            return 0 if hidden else w

        def rh(r):
            d = ws.row_dimensions.get(r)
            if d is not None and d.hidden:
                return 0
            return d.height if d is not None and d.height else dflt

        merged: dict = {}
        for m in ws.merged_cells.ranges:
            for r in range(m.min_row, m.max_row + 1):
                for c in range(m.min_col, m.max_col + 1):
                    merged[(r, c)] = m if (r, c) == (m.min_row, m.min_col) else None
        rows: dict[int, float] = {}
        wrap: set[str] = set()
        for row in ws.iter_rows():
            for cell in row:
                k = (cell.row, cell.column)
                if (k in merged and merged[k] is None) or skip(cell.row, cell.column):
                    continue
                v = value(cell)
                if v is None or cell.alignment.shrink_to_fit:
                    continue
                m = merged.get(k)
                c0, c1 = (m.min_col, m.max_col) if m else (cell.column, cell.column)
                r0, r1 = (m.min_row, m.max_row) if m else (cell.row, cell.row)
                w = sum(cw(c) for c in range(c0, c1 + 1))
                h = sum(rh(r) for r in range(r0, r1 + 1))
                if w == 0 or h == 0:
                    continue
                size = cell.font.sz or 11
                if cell.alignment.wrap_text:
                    need = _lines(v, size, w)
                elif max(_wid(l, size) for l in v.split("\n")) > w * 1.02 and (m or ws.cell(cell.row, c1 + 1).value not in (None, "")):
                    wrap.add(cell.coordinate)
                    need = _lines(v, size, w)
                else:
                    need = len(v.split("\n"))
                if need * size * LINE + (2 if need > 1 else -1.5) > h:  # 여러 줄은 위아래 여백까지(딱 맞으면 엑셀에서 잘려 보임), 한 줄은 기본 줄 높이면 됨
                    rows[r1] = max(rows.get(r1, 0), rh(r1) + need * size * LINE + 4 - h)
        if rows or wrap:
            out[smap[ws.title]] = {"rows": {r: round(v, 2) for r, v in rows.items()}, "wrap": wrap, "title": ws.title}
    return out


def _add_row(x, r, h):
    """그 줄 태그가 없으면(빈 줄 — 합친 칸의 마지막 줄 등) 줄 번호 차례에 맞춰 높이만 있는 줄을 넣음."""
    new = f'<row r="{r}" ht="{h}" customHeight="1"/>'
    for mm in re.finditer(r'<row r="(\d+)"', x):
        if int(mm.group(1)) > r:
            return x[:mm.start()] + new + x[mm.start():]
    if "<sheetData/>" in x:
        return x.replace("<sheetData/>", f"<sheetData>{new}</sheetData>", 1)
    return x.replace("</sheetData>", new + "</sheetData>", 1)


def _apply(path: Path, fixes: dict[str, dict]) -> None:
    with zipfile.ZipFile(path) as z:
        data = {n: z.read(n) for n in z.namelist()}
        infos = {i.filename: i for i in z.infolist()}
    styles = data["xl/styles.xml"].decode("utf-8")
    xm = re.search(r"<cellXfs[^>]*>(.*?)</cellXfs>", styles, re.S)
    xfs = re.findall(r"<xf\b[^>]*/>|<xf\b[^>]*>.*?</xf>", xm.group(1), re.S)
    clone: dict[int, int] = {}

    def wrapped(s: int) -> int:
        if s not in clone:
            xf = xfs[s]
            if 'wrapText="1"' in xf:
                clone[s] = s
                return s
            if "<alignment" in xf:
                nx = re.sub(r"<alignment\b", '<alignment wrapText="1"', xf, count=1)
            elif xf.endswith("/>"):
                nx = xf[:-2] + ' applyAlignment="1"><alignment wrapText="1"/></xf>'
            else:
                nx = re.sub(r"(<xf\b[^>]*>)", r'\1<alignment wrapText="1"/>', xf, count=1)
            xfs.append(nx)
            clone[s] = len(xfs) - 1
        return clone[s]

    for sx, fx in fixes.items():
        x = data[sx].decode("utf-8")
        for r, h in fx["rows"].items():
            def setrow(mm, r=r, h=h):
                tag = re.sub(r'\s(ht|customHeight)="[^"]*"', "", mm.group(0))
                return tag.replace(f'<row r="{r}"', f'<row r="{r}" ht="{h}" customHeight="1"', 1)
            x, n = re.subn(r'<row r="%d"(?=[\s>/])[^>]*?/?>' % r, setrow, x, count=1)
            if n == 0:
                x = _add_row(x, r, h)
        for coord in fx["wrap"]:
            def setc(mm):
                tag = mm.group(0)
                s = re.search(r'\ss="(\d+)"', tag)
                ns = wrapped(int(s.group(1)) if s else 0)
                return re.sub(r'\ss="\d+"', f' s="{ns}"', tag) if s else tag.replace(f'r="{coord}"', f'r="{coord}" s="{ns}"', 1)
            x = re.sub(r'<c r="%s"(?=[\s>/])[^>]*>' % coord, setc, x, count=1)
        data[sx] = x.encode("utf-8")
    if len(xfs) != len(re.findall(r"<xf\b[^>]*/>|<xf\b[^>]*>.*?</xf>", xm.group(1), re.S)):
        new = re.sub(r'count="\d+"', f'count="{len(xfs)}"', xm.group(0).replace(xm.group(1), "".join(xfs)), count=1)
        data["xl/styles.xml"] = styles.replace(xm.group(0), new).encode("utf-8")
    fd, tmp = tempfile.mkstemp(suffix=".xlsx", dir=str(Path(path).parent))
    os.close(fd)
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as out:
            for n, b in data.items():
                out.writestr(infos[n], b, compress_type=zipfile.ZIP_DEFLATED)
        from openpyxl import load_workbook
        load_workbook(tmp)  # 열리는지 확인 — 못 열면 원래 파일 그대로
        shutil.move(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def fit(path: Path) -> list[str]:
    """잘리는 칸을 맞추고 고친 곳을 '시트 칸' 줄로 돌려줌. 실패해도 파일은 그대로(빈 목록)."""
    try:
        fixes = plan(Path(path))
        if fixes:
            _apply(Path(path), fixes)
    except Exception:  # noqa: BLE001 — 맞추기를 못 해도 서류는 그대로 씀
        return []
    return [f"{v['title']} {len(v['rows'])}줄" for v in fixes.values()]
