"""잘라 낸 HWPX 서식에 회사 자료를 채운다 — 규칙으로만, "없으면 없는 대로"(10/28 트랙 1과 같은 원칙).

- 이름표 칸(회사명·대표자·사업자번호·주소·전화·FAX·홈페이지·E-mail·설립년도·자본금·면허 보유현황) 바로 오른쪽 **빈 칸**에 값.
  이미 글이 있는 칸은 건드리지 않는다(자리표시 '년 월'처럼 숫자 없는 칸은 빈 칸으로 봄).
- 실적집계표: 머리글 줄에 '사업명'과 '금액'이 있는 표 — 아래 빈 줄에 실적 후보를 한 줄씩, '실적누계' 줄엔 건수·합계.
- 연혁: '연혁' 이름표 칸(한 줄 전체를 차지하는 칸이면 그 칸 아래쪽 문단, 아니면 오른쪽 칸)에 연혁을 한 줄씩.
- 값을 넣은 문단은 줄 배치 정보(linesegarray)를 지워 한글이 열 때 다시 계산하게 한다.
채운 칸은 FillReport에 남겨 준비표에 '자동 입력 — 확인 필요'로 보인다.
"""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from xml.sax.saxutils import escape


@dataclass
class FillReport:
    file: str
    filled: list[str] = field(default_factory=list)  # "회사명 → (주)지일" 같은 줄
    rows: int = 0  # 실적집계표에 넣은 실적 수
    overflow: int = 0  # 표 줄이 모자라 못 넣은 실적 수


def _spans(xml: str, tag: str, start: int = 0, end: int | None = None) -> list[tuple[int, int]]:
    """[start, end) 안의 맨 바깥 <?:tag>…</?:tag> 위치들(같은 태그가 안에 또 있어도 바깥만)."""
    end = len(xml) if end is None else end
    rx = re.compile(rf"<(/?)(?:[\w.-]+:)?{tag}\b[^>]*?(/?)>")
    out, depth, s = [], 0, 0
    for m in rx.finditer(xml, start, end):
        closing, selfclose = m.group(1), m.group(2)
        if not closing and not selfclose:
            if depth == 0:
                s = m.start()
            depth += 1
        elif closing:
            depth -= 1
            if depth == 0:
                out.append((s, m.end()))
    return out


def _text(seg: str) -> str:
    return "".join(re.findall(r"<(?:[\w.-]+:)?t\b[^>]*>(.*?)</(?:[\w.-]+:)?t>", seg, flags=re.S))


def _sq(s: str) -> str:
    return re.sub(r"\s+", "", s)


@dataclass
class Cell:
    start: int
    end: int
    row: int
    col: int
    colspan: int
    text: str

    @property
    def blank(self) -> bool:
        """빈 칸 — 글이 없거나, 숫자 없는 자리표시('년 월', '총 명', '원', '건')뿐."""
        t = _sq(self.text)
        return t == "" or (not re.search(r"\d", t) and re.fullmatch(r"[년월일총명원건～~\-.:/()]*", t) is not None)


def _cells(xml: str, tbl: tuple[int, int]) -> list[Cell]:
    cells = []
    for s, e in _spans(xml, "tc", tbl[0] + 1, tbl[1]):
        seg = xml[s:e]
        # 칸 안에 표가 또 있으면 그 칸의 주소는 맨 마지막 cellAddr(바깥 칸 것)
        addr = re.findall(r'cellAddr colAddr="(\d+)" rowAddr="(\d+)"', seg)
        span = re.findall(r'cellSpan colSpan="(\d+)"', seg)
        if not addr:
            continue
        col, row = map(int, addr[-1])
        cells.append(Cell(s, e, row, col, int(span[-1]) if span else 1, _text(seg)))
    return cells


def _paragraph(template: str, line: str) -> str:
    """문단 틀(첫 문단)에서 글·줄 배치를 지우고 line 한 줄을 넣은 새 문단."""
    open_tag = re.match(r"<(?:[\w.-]+:)?p\b[^>]*>", template).group()
    prefix = re.match(r"<([\w.-]+:)?p\b", template).group(1) or ""
    char = re.search(r'<(?:[\w.-]+:)?run\b[^>]*charPrIDRef="(\d+)"', template)
    run = f'<{prefix}run charPrIDRef="{char.group(1) if char else "0"}"><{prefix}t>{escape(line)}</{prefix}t></{prefix}run>'
    return f"{open_tag}{run}</{prefix}p>"


def _set_cell(cell_xml: str, lines: list[str], keep_first: bool = False) -> str:
    """칸의 문단을 lines로 바꿈. keep_first면 첫 문단(이름표)은 두고 그 뒤에 붙임."""
    sub = _spans(cell_xml, "subList")
    if not sub:
        return cell_xml
    s, e = sub[0]
    paras = _spans(cell_xml, "p", s, e)
    if not paras:
        return cell_xml
    first = cell_xml[paras[0][0]:paras[0][1]]
    new = "".join(_paragraph(first, ln) for ln in lines)
    if keep_first:
        return cell_xml[:paras[0][1]] + new + cell_xml[paras[-1][1]:]
    return cell_xml[:paras[0][0]] + new + cell_xml[paras[-1][1]:]


# ── 이름표 → 회사정보 항목 ────────────────────────────────────
LABELS: list[tuple[tuple[str, ...], str]] = [
    (("사업자등록번호", "사업자번호"), "사업자등록번호"),
    (("법인등록번호",), "법인등록번호"),
    (("회사명", "업체명", "상호", "법인명", "제안사명"), "회사명"),
    (("대표자",), "대표자"),
    (("주소", "소재지"), "주소"),
    (("전화",), "전화번호"),
    (("팩스", "FAX"), "팩스번호"),
    (("홈페이지",), "홈페이지"),
    (("E-mail", "이메일", "전자우편"), "이메일"),
    (("회사설립", "설립년", "설립연", "설립일", "개업"), "설립일"),
    (("자본금",), "자본금"),
    (("사업분야", "업태"), "사업분야"),
    (("면허/허가/등록증", "면허보유", "등록증보유"), "_licenses"),
]
_SKIP_LABEL = re.compile(r"주민|대리인|담당자|성명$|수신|발주")


def _label_key(text: str) -> str | None:
    t = _sq(text).strip(":：")
    if not t or len(t) > 16 or _SKIP_LABEL.search(t):
        return None
    for words, key in LABELS:  # 앞머리가 맞는 것 먼저('홈페이지주소'는 주소가 아니라 홈페이지)
        if any(t.startswith(w) for w in words):
            return key
    for words, key in LABELS:
        if len(t) <= 8 and any(w in t for w in words):
            return key
    return None


def _fmt_date(v) -> str:
    if isinstance(v, (datetime, date)):
        return f"{v.year}년 {v.month}월"
    m = re.match(r"(\d{4})[-./년\s]+(\d{1,2})", str(v))
    return f"{m.group(1)}년 {int(m.group(2))}월" if m else str(v)


def _value(key: str, vault) -> str:
    if key == "_licenses":
        names = [str(d.get("서류 이름") or "") for d in vault.docs
                 if d.get("서류 이름") and str(d.get("상태") or "") != "재발급"
                 and re.search(r"자격|직접생산|면허|등록|신고", str(d.get("서류 종류") or "") + str(d.get("서류 이름") or ""))]
        return ", ".join(dict.fromkeys(names))
    if key == "사업분야":
        return vault.info.get("사업분야") or vault.info.get("종목") or vault.info.get("업태") or ""
    v = vault.info.get(key, "")
    if key == "설립일" and v:
        return _fmt_date(v)
    if key == "자본금" and v and re.fullmatch(r"[\d,]+", v):
        return f"{int(v.replace(',', '')):,}원"
    return v


def _money(v) -> str:
    try:
        return f"{int(float(v)):,}"
    except (TypeError, ValueError):
        return ""


def _period(t: dict) -> str:
    def d(x):
        if isinstance(x, (datetime, date)):
            return f"{x:%Y.%m.%d}"
        return str(x or "")[:10].replace("-", ".")
    a, b = d(t.get("계약일")), d(t.get("준공일"))
    return f"{a}~{b}" if a and b else (a or b)


_TRACK_COLS = [  # 실적집계표 머리글 낱말 → 값
    (("사업명", "용역명", "공사명", "건명"), lambda t: str(t.get("사업명") or "")),
    (("수행기간", "계약기간", "사업기간", "기간"), _period),
    (("사업개요", "내용", "분야"), lambda t: str(t.get("분야") or "")),
    (("계약금액", "금액", "실적액"), lambda t: _money(t.get("인정 금액(원)") or t.get("계약금액(원)"))),
    (("발주기관", "발주처", "발주자"), lambda t: str(t.get("발주기관") or "")),
    (("구분", "업무"), lambda t: (str(t.get("업무") or "") + (f" (지분 {t.get('지분율(%)')}%)" if t.get("지분율(%)") not in (None, "", 100, 100.0) else "")).strip()),
    (("증빙", "비고"), lambda t: str(t.get("증빙 파일 이름") or "")),
]


def _fill_tracks(xml: str, cells: list[Cell], tracks: list[dict], rep: FillReport) -> list[tuple[Cell, list[str], bool]]:
    rows: dict[int, list[Cell]] = {}
    for c in cells:
        rows.setdefault(c.row, []).append(c)
    edits = []
    for r in sorted(rows):
        heads = [_sq(c.text) for c in rows[r]]
        if not (any("사업명" in h or "용역명" in h or "공사명" in h for h in heads) and any("금액" in h for h in heads)):
            continue
        colmap = {}
        for c in rows[r]:
            h = _sq(c.text)
            for words, fn in _TRACK_COLS:
                if any(w in h for w in words) and fn not in colmap.values():
                    colmap[c.col] = fn
                    break
        queue = list(tracks)
        for rr in sorted(k for k in rows if k > r):
            line = rows[rr]
            first = _sq(line[0].text)
            if "누계" in first or "합계" in first:  # 실적누계 줄: 건수·합계
                for c in line:
                    h = next((_sq(x.text) for x in rows[r] if x.col == c.col), "")
                    if "기간" in h and tracks:
                        edits.append((c, [f"{len(tracks)}건"], False))
                    elif "금액" in h and tracks:
                        total = sum(float(t.get("인정 금액(원)") or t.get("계약금액(원)") or 0) for t in tracks)
                        edits.append((c, [f"{int(total):,}원"], False))
                continue
            if not queue or not all(c.blank for c in line):
                continue
            t = queue.pop(0)
            for c in line:
                if c.col in colmap and (v := colmap[c.col](t)):
                    edits.append((c, [v], False))
            rep.rows += 1
        rep.overflow += len(queue)
        break  # 표 하나에 한 번
    return edits


def fill_hwpx(src: Path, dst: Path, vault, tracks: list[dict] | None = None, keys=None, history: bool = True,
              extras: list | None = None) -> FillReport:
    """src 서식(HWPX)을 채워 dst에 저장. tracks: 실적집계표에 넣을 실적(후보 순서대로).
    keys를 주면 그 회사정보 항목만, history=False면 연혁을 안 채운다. extras: [(이름표들, 값)] — 공고 정보처럼 회사 자료 밖의 값."""
    rep = FillReport(Path(dst).name)
    zin = zipfile.ZipFile(src)
    out_entries = {}
    for name in zin.namelist():
        data = zin.read(name)
        if re.fullmatch(r"Contents/section\d+\.xml", name):
            xml = data.decode("utf-8")
            edits: list[tuple[Cell, list[str], bool]] = []
            for tbl in _spans(xml, "tbl"):
                cells = _cells(xml, tbl)
                by_pos = {(c.row, c.col): c for c in cells}
                if tracks:
                    edits += _fill_tracks(xml, cells, tracks, rep)
                for c in cells:
                    extra = next((v for words, v in extras or () if _sq(c.text).strip(":：") in words), None)
                    if extra:
                        target = by_pos.get((c.row, c.col + c.colspan))
                        if target is not None and target.blank:
                            edits.append((target, [extra], False))
                            rep.filled.append(f"{_sq(c.text)} → {extra[:40]}")
                        continue
                    key = _label_key(c.text)
                    if keys is not None and key not in keys:
                        key = None
                    if history and "연혁" in _sq(c.text) and len(_sq(c.text)) <= 8 and vault.history:
                        lines = [f"{_fmt_ymd(h.get('연월일'))}  {h.get('내용') or ''}".strip() for h in vault.history]
                        right = by_pos.get((c.row, c.col + c.colspan))
                        if right is not None and right.blank:
                            edits.append((right, lines, False))
                        else:  # 한 줄 전체 칸 — 이름표 아래에 붙임
                            edits.append((c, lines, True))
                        rep.filled.append(f"연혁 → {len(lines)}줄")
                        continue
                    if not key:
                        continue
                    target = by_pos.get((c.row, c.col + c.colspan))
                    value = _value(key, vault)
                    if target is None or not target.blank or not value:
                        continue
                    edits.append((target, [value], False))
                    rep.filled.append(f"{_sq(c.text)} → {value[:40]}")
            for cell, lines, keep in sorted(edits, key=lambda e: e[0].start, reverse=True):
                xml = xml[:cell.start] + _set_cell(xml[cell.start:cell.end], lines, keep) + xml[cell.end:]
            data = xml.encode("utf-8")
        out_entries[name] = data
    Path(dst).parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dst, "w") as zout:
        for info in zin.infolist():
            ctype = zipfile.ZIP_STORED if info.filename == "mimetype" else zipfile.ZIP_DEFLATED
            zout.writestr(zipfile.ZipInfo(info.filename, date_time=info.date_time), out_entries[info.filename],
                          compress_type=ctype)
    return rep


def _fmt_ymd(v) -> str:
    if isinstance(v, (datetime, date)):
        return f"{v:%Y.%m.%d}"
    return str(v or "")[:10].replace("-", ".")
