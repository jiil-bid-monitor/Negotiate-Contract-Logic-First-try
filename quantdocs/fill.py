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
    overflow_names: list[str] = field(default_factory=list)  # 그 실적 이름(금액이 작아 빠진 것)


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


def _paragraph(template: str, line: str, char_id: str | None = None) -> str:
    """문단 틀(첫 문단)에서 글·줄 배치를 지우고 line 한 줄을 넣은 새 문단. char_id: 글자 모양을 바꿔 쓸 때(칸에 맞춰 줄인 크기)."""
    open_tag = re.match(r"<(?:[\w.-]+:)?p\b[^>]*>", template).group()
    prefix = re.match(r"<([\w.-]+:)?p\b", template).group(1) or ""
    char = re.search(r'<(?:[\w.-]+:)?run\b[^>]*charPrIDRef="(\d+)"', template)
    run = f'<{prefix}run charPrIDRef="{char_id or (char.group(1) if char else "0")}"><{prefix}t>{escape(line)}</{prefix}t></{prefix}run>'
    return f"{open_tag}{run}</{prefix}p>"


def _set_cell(cell_xml: str, lines: list[str], keep_first: bool = False, char_id: str | None = None) -> str:
    """칸의 문단을 lines로 바꿈. keep_first면 첫 문단(이름표)은 두고 그 뒤에 붙임."""
    sub = _spans(cell_xml, "subList")
    if not sub:
        return cell_xml
    s, e = sub[0]
    paras = _spans(cell_xml, "p", s, e)
    if not paras:
        return cell_xml
    first = cell_xml[paras[0][0]:paras[0][1]]
    new = "".join(_paragraph(first, ln, char_id) for ln in lines)
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
_SKIP_LABEL = re.compile(r"주민|대리인|담당자|성명$|수신|발주|휴대")  # 휴대폰 = 대리인·담당자 개인 번호


_SEAL_ONLY = re.compile(r"^[(（]?(?:인|서명|서명또는인|인또는서명|직인|날인)[)）]?$")
_INLINE_LABELS = {"TEL": "전화번호", "Tel": "전화번호", "전화": "전화번호", "전화번호": "전화번호",
                  "FAX": "팩스번호", "Fax": "팩스번호", "팩스": "팩스번호", "E-mail": "이메일", "Email": "이메일", "이메일": "이메일"}
_INLINE_RE = re.compile(r"(?P<lab>TEL|Tel|FAX|Fax|E-?mail|전화번호|전화|팩스|이메일)(?P<colon>\s*[:：])(?P<sp>[ \u00a0\u3000]*)"
                        r"(?=(?:TEL|Tel|FAX|Fax|E-?mail|전화|팩스|이메일)|$)")


def _fill_inline(seg: str, vault) -> tuple[str, list[str]]:
    """칸 안 'TEL :      FAX :' 같은 빈 자리에 값을 넣음(11/04 제보: 제안 제출서 전화번호 칸) — 빈칸 수는 값 길이만큼 줄여 줄 모양 유지."""
    done: list[str] = []

    def fill_t(m):
        body = m.group(2)

        def rep(mm):
            key = _INLINE_LABELS.get(mm.group("lab").replace("-", "-"), "")
            value = _value(key, vault) if key else ""
            if not value:
                return mm.group(0)
            done.append(f"{mm.group('lab')} → {value}")
            pad = max(2, len(mm.group("sp")) - len(value) - 1) if mm.end() < len(body) else 0
            return f"{mm.group('lab')}{mm.group('colon')} {escape(value)}" + " " * pad
        parts = re.split(r"(<[^>]*>)", body)  # 태그(<hp:tab/> 등)는 그대로 두고 글 조각만
        return m.group(1) + "".join(x if x.startswith("<") else _INLINE_RE.sub(rep, x) for x in parts) + m.group(3)
    new = re.sub(r"(<(?:[\w.-]+:)?t\b[^>]*>)(.*?)(</(?:[\w.-]+:)?t>)", fill_t, seg, flags=re.S)
    if done:
        new = re.sub(r"<(?:[\w.-]+:)?linesegarray\b.*?</(?:[\w.-]+:)?linesegarray>", "", new, flags=re.S)
    return new, done


def _masked(v: str) -> str:
    """주민등록번호는 앞 6자리만 — 뒷자리는 늘 '*******'(11/04 사용자 요청)."""
    m = re.search(r"(\d{6})\s*-?\s*[\d*Xx]{7}", v or "")
    return f"{m.group(1)}-*******" if m else ""


def _label_key(text: str) -> str | None:
    t = _sq(text).strip(":：")
    if not t or len(t) > 16 or _SKIP_LABEL.search(t):
        return None
    if t in ("사무실", "사무실전화", "사무실전화번호"):  # '연락처 | 사무실 / FAX' 칸(공동수급 대표자 선임계)
        return "전화번호"
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
    if not v and key in ("전화번호", "팩스번호"):  # 회사정보 시트 항목 이름이 '대표전화'·'TEL'·'FAX' 등이어도
        words = ("전화", "TEL", "연락처") if key == "전화번호" else ("팩스", "FAX")
        v = next((str(x) for k, x in vault.info.items() if x and any(w in k.upper() for w in words)
                  and not re.search(r"휴대|담당|팩스|FAX" if key == "전화번호" else r"휴대|담당", k.upper())), "")
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


def _amount(t: dict, head: str) -> str:
    """금액 — 머리글 단위(백만원·천원)에 맞춤(11/04 당진 '계약금액(단위:백만원)')."""
    v = t.get("인정 금액(원)") or t.get("계약금액(원)")
    try:
        v = float(v)
    except (TypeError, ValueError):
        return ""
    return _in_unit(v, head)


def _in_unit(v: float, head: str) -> str:
    if "백만" in head:
        return f"{v / 1e6:,.1f}".rstrip("0").rstrip(".")
    if "천원" in head:
        return f"{round(v / 1000):,}"
    return f"{int(v):,}"


def _alone(t: dict) -> str:
    share = t.get("지분율(%)")
    try:
        share = float(share)
    except (TypeError, ValueError):
        return "단독"  # 지분율 칸이 비면 단독으로 봄(공사수주현황엔 공동 표시 없음 — 공동이면 회사 자료에 지분율)
    return "단독" if share >= 100 else f"공동({share:g}%)"


_TRACK_COLS = [  # 실적집계표 머리글 낱말 → 값(t, 머리글)
    (("연번", "순번"), None),  # 줄 번호 — 따로 채움
    (("사업명", "용역명", "공사명", "건명"), lambda t, h: str(t.get("사업명") or "")),
    (("수행기간", "계약기간", "사업기간", "기간"), lambda t, h: _period(t)),
    (("사업개요", "내용", "분야"), lambda t, h: str(t.get("분야") or "")),
    (("계약금액", "금액", "실적액"), _amount),
    (("발주기관", "발주처", "발주자"), lambda t, h: str(t.get("발주기관") or "")),
    (("단독", "공동"), lambda t, h: _alone(t)),
    (("구분", "업무"), lambda t, h: (str(t.get("업무") or "") + (f" (지분 {t.get('지분율(%)')}%)" if t.get("지분율(%)") not in (None, "", 100, 100.0) else "")).strip()),
    (("증빙", "비고"), lambda t, h: str(t.get("증빙 파일 이름") or "")),
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
        colmap, headtext, no_col = {}, {}, None
        for c in rows[r]:
            h = _sq(c.text)
            headtext[c.col] = h
            for words, fn in _TRACK_COLS:
                if any(w in h for w in words):
                    if fn is None:
                        no_col = c.col
                    elif fn not in colmap.values():
                        colmap[c.col] = fn
                    break
        amount_head = next((headtext[k] for k, fn in colmap.items() if fn is _amount), "")

        def empty(line):  # 빈 줄 — 연번 칸에 번호만 미리 적힌 줄도 빈 줄(11/04 당진 '1')
            return all(c.blank or (c.col in (no_col, line[0].col) and re.fullmatch(r"\d+\.?", _sq(c.text))) for c in line)

        def is_total(line):
            f = _sq(line[0].text)
            return "누계" in f or "합계" in f or f == "계"

        later = sorted(k for k in rows if k > r)
        slots = sum(1 for k in later if not is_total(rows[k]) and empty(rows[k]))
        queue = list(tracks)
        if 0 < slots < len(queue):  # 줄이 모자라면 금액 큰 것부터 줄 수만큼 고르고 순서는 그대로(연도순)
            amt = lambda t: float(t.get("인정 금액(원)") or t.get("계약금액(원)") or 0)
            keep = {id(t) for t in sorted(queue, key=amt, reverse=True)[:slots]}
            rep.overflow_names = [str(t.get("사업명") or "") for t in queue if id(t) not in keep]
            rep.overflow += len(queue) - slots
            queue = [t for t in queue if id(t) in keep]
        tracks = list(queue)
        n = 0
        totals = []
        for rr in later:
            line = rows[rr]
            if is_total(line):  # 실적누계·계 줄 — 실적을 다 넣은 뒤 채움(표 위쪽에 있어도)
                totals.append(line)
                continue
            if not queue or not empty(line):
                continue
            t = queue.pop(0)
            n += 1
            for c in line:
                if c.col == no_col and c.blank:
                    edits.append((c, [str(n)], False))
                elif c.col in colmap and (v := colmap[c.col](t, headtext.get(c.col, ""))):
                    edits.append((c, [v], False))
            rep.rows += 1
        total = sum(float(t.get("인정 금액(원)") or t.get("계약금액(원)") or 0) for t in tracks[:n])
        unit = "백만원" if "백만" in amount_head else "천원" if "천원" in amount_head else "원"
        for line in totals if n else []:
            for c in line:
                h = headtext.get(c.col, "")
                if _sq(c.text).startswith("총실적금액"):  # '총 실적 금액 :   백만원' 한 칸(11/04 당진)
                    edits.append((c, [f"총 실적 금액 : {_in_unit(total, amount_head)} {unit}"], False))
                elif "기간" in h and c.blank:
                    edits.append((c, [f"{n}건"], False))
                elif "금액" in h and c.blank:
                    edits.append((c, [_in_unit(total, amount_head) + ("" if ("백만" in h or "천원" in h) else "원")], False))
        rep.overflow += len(queue)
        break  # 표 하나에 한 번
    return edits


def _fill_label_paragraphs(xml: str, vault, rep: FillReport) -> str:
    """표 밖 '업 체 명 :'처럼 이름표만 있는 문단 끝에 값을 붙임(11/04 당진 실적 서식)."""
    tbls = _spans(xml, "tbl")
    # 표 안이어도 여러 줄짜리 칸(서식 본문 칸)의 줄은 채움 — 한 줄짜리 칸은 이름표 칸이라 표 채우기가 옆 칸에 씀(11/04 서식 8·9 서명 줄)
    single = [(cs, ce) for cs, ce in _spans(xml, "tc") if len(_spans(xml, "p", cs, ce)) <= 1]
    edits = []
    paras, stack = [], []  # 모든 문단(표 칸 안 문단 포함) — _spans는 맨 바깥만 줌
    for m in re.finditer(r"<(/?)(?:[\w.-]+:)?p\b[^>]*?(/?)>", xml):
        if m.group(2):
            continue
        if not m.group(1):
            stack.append(m.start())
        elif stack:
            paras.append((stack.pop(), m.end()))
    for s, e in sorted(paras):
        if any(ts >= s and te <= e for ts, te in tbls) or any(cs <= s and e <= ce for cs, ce in single):
            continue
        text = re.sub(r"<[^>]+>", " ", _text(xml[s:e]))  # 글 안 탭(<hp:tab/>) 등은 빈칸으로
        # '업 체 명 :' 또는 서명 줄 '대표자 :          (인)'(11/04 서식 8·9)
        m = re.fullmatch(r"\s*(.{2,16}?)\s*[:：]\s*([(（](?:인|서명)[)）])?\s*", text)
        if not m:
            continue
        key = _label_key(m.group(1))
        value = _value(key, vault) if key else ""
        if not value:
            continue
        seg = xml[s:e]
        ts = list(re.finditer(r"(<(?:[\w.-]+:)?t\b[^>]*>)(.*?)(</(?:[\w.-]+:)?t>)", seg, re.S))
        hit = next((t for t in ts if re.search(r"[:：]", t.group(2))), None)
        if hit is None:
            continue
        used = []

        def put(mm):  # 글 속 첫 쌍점에만 — 태그 이름의 쌍점(<hp:tab/>)은 건드리지 않음
            if mm.group(1) or used:
                return mm.group(0)
            used.append(1)
            return f"{mm.group(2)} {escape(value)}" + " " * max(2 if m.group(2) else 0, len(mm.group(3)) - len(value) - 1)
        body = re.sub(r"(<[^>]*>)|([:：])( *)", put, hit.group(2))
        seg = seg[:hit.start(2)] + body + seg[hit.end(2):]
        seg = re.sub(r"<(?:[\w.-]+:)?linesegarray\b.*?</(?:[\w.-]+:)?linesegarray>", "", seg, flags=re.S)
        edits.append((s, e, seg))
        rep.filled.append(f"{_sq(m.group(1))} → {value[:40]}")
    for s, e, seg in reversed(edits):
        xml = xml[:s] + seg + xml[e:]
    return xml


# ── 칸에 맞추기(11/04 사용자 요청: 공고문 글꼴 그대로, 칸 안팎으로 넘치거나 겹치지 않게) ─────────────
# 넣은 글이 칸 너비(여백 뺌)를 넘고 칸 높이에 줄을 더 넣을 자리가 없으면, 같은 글꼴에서 크기만 줄인 글자 모양을
# header.xml에 하나 더 만들어 씀(최소 7pt). 글꼴·장평·색은 칸의 원래 글자 모양 그대로.
MIN_HEIGHT = 700  # 7pt


class _Chars:
    def __init__(self, header: str):
        self.header = header
        self.blocks = {m.group(1): m.group(0) for m in
                       re.finditer(r'<(?:[\w.-]+:)?charPr\b[^>]*\bid="(\d+)".*?</(?:[\w.-]+:)?charPr>', header, re.S)}
        self.paras = {m.group(1): m.group(0) for m in
                      re.finditer(r'<(?:[\w.-]+:)?paraPr\b[^>]*\bid="(\d+)".*?</(?:[\w.-]+:)?paraPr>', header, re.S)}
        self.added: dict[tuple[str, int | None], str] = {}
        # 언어별 글꼴 목록 {LATIN: {id: 이름}} — 채운 글의 숫자·영문을 한글과 같은 글꼴로(11/04 사용자 제보: 숫자 글꼴이 다름)
        self.faces = {m.group(1).upper(): dict(re.findall(r'<(?:[\w.-]+:)?font\b[^>]*\bid="(\d+)"[^>]*\bface="([^"]*)"', m.group(2)))
                      for m in re.finditer(r'<(?:[\w.-]+:)?fontface\b[^>]*\blang="(\w+)"[^>]*>(.*?)</(?:[\w.-]+:)?fontface>', header, re.S)}

    def height(self, cid: str) -> int:
        m = re.search(r'\bheight="(\d+)"', self.blocks.get(cid, ""))
        return int(m.group(1)) if m else 1000

    def ratio(self, cid: str) -> float:
        m = re.search(r'<(?:[\w.-]+:)?ratio\b[^>]*\bhangul="(\d+)"', self.blocks.get(cid, ""))
        return int(m.group(1)) / 100 if m else 1.0

    def spacing(self, pid: str) -> float | None:
        """줄 간격 비율(160% → 1.6). 비율이 아닌 줄 간격(고정값 등)·문단 모양을 모르면 None."""
        block = self.paras.get(pid, "")
        if not block:
            return 1.6
        m = re.search(r'<(?:[\w.-]+:)?lineSpacing\b[^>]*type="PERCENT"[^>]*value="(\d+)"', block)
        return int(m.group(1)) / 100 if m else None

    def _font_ref(self, cid: str) -> str:
        m = re.search(r"<(?:[\w.-]+:)?fontRef\b[^>]*>", self.blocks.get(cid, ""))
        return m.group(0) if m else ""

    def _unified_ref(self, cid: str) -> str:
        """fontRef에서 한글 말고 다른 언어(영문·숫자·기호 등)도 한글과 같은 글꼴을 가리키게 — 문서 글꼴 목록에 그 글꼴이 있는 언어만.
        (글꼴을 새로 더하지 않음: 통합서식으로 이을 때 원본 머리의 글꼴 목록을 그대로 쓰기 때문)"""
        ref = self._font_ref(cid)
        hangul = re.search(r'\bhangul="(\d+)"', ref)
        face = self.faces.get("HANGUL", {}).get(hangul.group(1)) if hangul else None
        if not face:
            return ref

        def swap(m):
            lang, cur = m.group(1), m.group(2)
            ids = [i for i, f in self.faces.get(lang.upper(), {}).items() if f == face]
            return f'{lang}="{ids[0] if ids else cur}"'
        return re.sub(r'\b(latin|hanja|japanese|other|symbol|user)="(\d+)"', swap, ref)

    def variant(self, cid: str, height: int | None = None) -> str:
        """채운 글에 쓸 글자 모양: 숫자·영문도 한글 글꼴로, height를 주면 그 크기로. 바꿀 게 없으면 cid 그대로."""
        if cid not in self.blocks or (height is None and self._unified_ref(cid) == self._font_ref(cid)):
            return cid
        key = (cid, height)
        if key not in self.added:
            ids = [int(x) for x in self.blocks] + [int(x) for x in self.added.values()]
            self.added[key] = str(max(ids) + 1)
        return self.added[key]

    def smaller(self, cid: str, height: int) -> str:
        return self.variant(cid, height)

    def finish(self) -> str:
        if not self.added:
            return self.header

        def block(cid: str, h: int | None, new: str) -> str:
            b = re.sub(r'\bid="\d+"', f'id="{new}"', self.blocks[cid], count=1)
            if h is not None:
                b = re.sub(r'\bheight="\d+"', f'height="{h}"', b, count=1)
            ref = self._font_ref(cid)
            return b.replace(ref, self._unified_ref(cid), 1) if ref else b
        extra = "".join(block(cid, h, new) for (cid, h), new in self.added.items())
        h = re.sub(r"(</(?:[\w.-]+:)?charProperties>)", lambda m: extra + m.group(1), self.header, count=1)
        return re.sub(r'(<(?:[\w.-]+:)?charProperties\b[^>]*\bitemCnt=")(\d+)"',
                      lambda m: f'{m.group(1)}{int(m.group(2)) + len(self.added)}"', h, count=1)


def _text_width(text: str, height: int, ratio: float) -> float:
    """글 너비 어림(HWPUNIT): 한글·한자·전각 = 글자 크기 × 장평, 숫자·영문 0.55, 공백 0.3, 문장부호 0.4."""
    w = 0.0
    for ch in text:
        if "가" <= ch <= "힣" or "一" <= ch <= "鿿" or "　" <= ch <= "ヿ" or "＀" <= ch <= "￯":
            w += 1.0 * ratio
        elif ch == " ":
            w += 0.3
        elif ch.isalnum():
            w += 0.55
        else:
            w += 0.4
    return w * height


def _fit_char(cell_xml: str, line: str, chars: "_Chars") -> tuple[str | None, str]:
    """(바꿔 쓸 글자 모양 id 또는 None, 알림). 칸 너비·높이·여백·줄 간격으로 한 칸 안에 들어가는지 봄."""
    size = re.search(r'<(?:[\w.-]+:)?cellSz\b[^>]*width="(\d+)"[^>]*height="(\d+)"', cell_xml)
    if not size:
        return None, ""
    tag = re.search(r'<(?:[\w.-]+:)?cellMargin\b[^>]*>', cell_xml)
    mg = dict(re.findall(r'\b(left|right|top|bottom)="(\d+)"', tag.group(0) if tag else ""))
    width = int(size.group(1)) - int(mg.get("left", 141)) - int(mg.get("right", 141))
    room = int(size.group(2)) - int(mg.get("top", 141)) - int(mg.get("bottom", 141))
    first_p = re.search(r'<(?:[\w.-]+:)?p\b[^>]*paraPrIDRef="(\d+)"', cell_xml)
    run = re.search(r'<(?:[\w.-]+:)?run\b[^>]*charPrIDRef="(\d+)"', cell_xml)
    cid = run.group(1) if run else "0"
    h, ratio = chars.height(cid), chars.ratio(cid)
    spacing = chars.spacing(first_p.group(1)) if first_p else 1.6
    if width <= 0:
        return None, ""
    def fits(size: int) -> bool:  # 이 크기에서 칸 높이에 들어가는 줄 수 × 칸 너비 안에 글이 들어가나
        # 줄 간격이 100%보다 작거나 고정값이면 두 줄로 넘어갈 때 글이 겹침(11/04 필암서원 주소 칸 50%) — 한 줄에 들어가야 함
        lines_fit = 1 if spacing is None or spacing < 1.0 else max(1, int(room // max(1.0, size * spacing)))
        return _text_width(line, size, ratio) <= width * 0.95 * lines_fit

    if fits(h):
        return None, ""
    new_h = next((size for size in range(h - 10, MIN_HEIGHT - 1, -10) if fits(size)), MIN_HEIGHT)
    note = f"글자 {h / 100:g}→{new_h / 100:g}pt로 줄여 칸에 맞춤"
    if not fits(new_h):
        note += "(7pt로도 넘쳐 칸이 늘어날 수 있음 — 확인)"
    return chars.smaller(cid, new_h), note


def _split_tall_rows(xml: str, tbl: tuple[int, int], need: int) -> str:
    """실적표 아래가 세로로 긴 빈 칸 한 줄뿐이면(11/04 필암서원 서식16 — 17.8cm 한 줄) 그 줄을 실적 수만큼(칸 높이가 허락하는 만큼)
    같은 높이의 줄로 나눔 — 표 전체 높이는 그대로, 줄마다 실적 하나. 세로로 합친 칸이 있는 표는 건드리지 않음."""
    seg = xml[tbl[0]:tbl[1]]
    if re.search(r'rowSpan="([2-9]|\d\d+)"', seg):
        return xml
    trs = _spans(seg, "tr")
    cells = _cells(xml, tbl)
    rows: dict[int, list[Cell]] = {}
    for c in cells:
        rows.setdefault(c.row, []).append(c)
    head = next((r for r in sorted(rows) if any("사업명" in _sq(c.text) or "용역명" in _sq(c.text) or "공사명" in _sq(c.text) for c in rows[r])
                 and any("금액" in _sq(c.text) for c in rows[r])), None)
    if head is None or len(trs) != len(rows):
        return xml
    head_h = max(int(m) for m in re.findall(r'cellSz\b[^>]*height="(\d+)"', seg[trs[head][0]:trs[head][1]]) or [0])
    empty = [r for r in sorted(rows) if r > head and all(c.blank for c in rows[r])]
    for r in empty:
        tr = seg[trs[r][0]:trs[r][1]]
        hs = [int(x) for x in re.findall(r'cellSz\b[^>]*height="(\d+)"', tr)]
        if not hs or not head_h:
            continue
        k = min(need - (len(empty) - 1), max(hs) // max(head_h, 2000))  # 한 줄 높이 = 머리줄 높이(최소 약 7mm)
        if k < 2:
            continue
        new_h = max(hs) // k
        copies = []
        for i in range(k):
            t = re.sub(r'(cellSz\b[^>]*height=")\d+"', lambda m: f'{m.group(1)}{new_h}"', tr)
            t = re.sub(r'(cellAddr\b[^>]*rowAddr=")(\d+)"', lambda m: f'{m.group(1)}{int(m.group(2)) + i}"', t)
            copies.append(t)
        after = seg[trs[r][1]:]
        after = re.sub(r'(cellAddr\b[^>]*rowAddr=")(\d+)"', lambda m: f'{m.group(1)}{int(m.group(2)) + k - 1}"', after)
        seg = seg[:trs[r][0]] + "".join(copies) + after
        seg = re.sub(r'(<(?:[\w.-]+:)?tbl\b[^>]*\browCnt=")(\d+)"', lambda m: f'{m.group(1)}{int(m.group(2)) + k - 1}"', seg, count=1)
        return xml[:tbl[0]] + seg + xml[tbl[1]:]  # 한 번에 한 줄만(나눈 뒤 위치가 바뀜)
    return xml


def fill_hwpx(src: Path, dst: Path, vault, tracks: list[dict] | None = None) -> FillReport:
    """src 서식(HWPX)을 채워 dst에 저장. tracks: 실적집계표에 넣을 실적(후보 순서대로)."""
    rep = FillReport(Path(dst).name)
    zin = zipfile.ZipFile(src)
    out_entries = {}
    chars = _Chars(zin.read("Contents/header.xml").decode("utf-8")) if "Contents/header.xml" in zin.namelist() else None
    for name in zin.namelist():
        data = zin.read(name)
        if re.fullmatch(r"Contents/section\d+\.xml", name):
            xml = data.decode("utf-8")
            edits: list[tuple[Cell, list[str], bool]] = []
            if tracks:  # 세로로 긴 빈 칸 한 줄인 실적표는 먼저 줄을 나눔
                for i in range(len(_spans(xml, "tbl"))):
                    xml = _split_tall_rows(xml, _spans(xml, "tbl")[i], len(tracks))
            # 서식이 '해당 사항이 없을 경우 "해당 없음" 기재'라고 적었으면 빈 표 첫 칸에 '해당 없음'(11/04 사용자 요청 — 행정처분내용 등)
            none_rule = re.search(r"(?:없을|없는)\s*(?:경우|때).{0,40}?해당\s*(?:사항\s*)?없음", _text(xml), re.S)
            for tbl in _spans(xml, "tbl"):
                cells = _cells(xml, tbl)
                by_pos = {(c.row, c.col): c for c in cells}
                if tracks:
                    edits += _fill_tracks(xml, cells, tracks, rep)
                for c in cells:
                    key = _label_key(c.text)
                    core = re.sub(r"\(.*?\)|[^\w]", "", c.text)  # '〈 주요연혁(요약) 〉' → '주요연혁'
                    if "연혁" in core and len(core) <= 8 and vault.history:
                        lines = [f"{_fmt_ymd(h.get('연월일'))}  {h.get('내용') or ''}".strip() for h in vault.history]
                        right = by_pos.get((c.row, c.col + c.colspan))
                        if right is not None and right.blank:
                            edits.append((right, lines, False))
                        else:  # 한 줄 전체 칸 — 이름표 아래에 붙임
                            edits.append((c, lines, True))
                        rep.filled.append(f"연혁 → {len(lines)}줄")
                        continue
                    if not key and "주민등록번호" in _sq(c.text):
                        # 대표자 줄의 주민등록번호만(위임장 '대 표 자 | 이름 | 주민등록번호 | __') — 대리인 줄은 비움, 뒷자리는 '*'
                        if any(o.row == c.row and _sq(o.text).startswith("대표자") for o in cells):
                            rrn = _masked(next((str(v) for k, v in vault.info.items() if "주민" in k and v), ""))
                            target = by_pos.get((c.row, c.col + c.colspan))
                            if rrn and target is not None and target.blank:
                                edits.append((target, [rrn], False))
                                rep.filled.append(f"대표자 주민등록번호 → {rrn}")
                        continue
                    if not key:
                        continue
                    target = by_pos.get((c.row, c.col + c.colspan))
                    value = _value(key, vault)
                    if key == "회사명" and "대표" in _sq(c.text) and _value("대표자", vault):
                        value = f"{value} {_value('대표자', vault)}".strip()  # '상호·법인명(대표성명)'
                    if target is None or not value:
                        continue
                    seal = _sq(target.text)
                    if not target.blank:
                        if not _SEAL_ONLY.match(seal):
                            continue
                        value = f"{value}   {target.text.strip()}"  # '(인)' 자리 — 이름 뒤에 그대로 남김(11/04 제보)
                    edits.append((target, [value], False))
                    rep.filled.append(f"{_sq(c.text)} → {value[:40]}")
                targets = {e[0].start for e in edits}
                for c in cells:  # 'TEL :      FAX :'처럼 칸 안에 이름표와 빈자리가 같이 있는 칸
                    if c.start in targets or ":" not in c.text and "：" not in c.text:
                        continue
                    new_seg, done = _fill_inline(xml[c.start:c.end], vault)
                    if done:
                        edits.append((c, None, new_seg))
                        rep.filled += done
                if none_rule and not any(tbl[0] <= e[0].start < tbl[1] for e in edits):
                    head = [c for c in cells if c.row == 0]
                    body = [c for c in cells if c.row > 0]
                    if head and any(_sq(c.text) for c in head) and body and all(c.blank for c in body):
                        first = min(body, key=lambda c: (c.row, c.col))
                        edits.append((first, ["해당 없음"], False))
                        rep.filled.append("표가 비어 '해당 없음' 기재 — 해당 사항이 있으면 고칠 것")
            for cell, lines, keep in sorted(edits, key=lambda e: e[0].start, reverse=True):
                seg = xml[cell.start:cell.end]
                if lines is None:  # 칸 안 빈자리 채우기 — 바꾼 칸 그대로
                    xml = xml[:cell.start] + keep + xml[cell.end:]
                    continue
                char_id = None
                if chars is not None and len(lines) == 1 and not keep:
                    char_id, note = _fit_char(seg, lines[0], chars)
                    if note:
                        rep.filled.append(f"  ↳ '{lines[0][:20]}' {note}")
                if chars is not None and char_id is None:  # 크기는 그대로, 숫자·영문만 한글 글꼴로
                    run = re.search(r'<(?:[\w.-]+:)?run\b[^>]*charPrIDRef="(\d+)"', seg)
                    base = chars.variant(run.group(1)) if run else None
                    char_id = base if run and base != run.group(1) else None
                xml = xml[:cell.start] + _set_cell(seg, lines, keep, char_id) + xml[cell.end:]
            xml = _fill_label_paragraphs(xml, vault, rep)
            data = xml.encode("utf-8")
        out_entries[name] = data
    if chars is not None and chars.added:  # 칸에 맞춰 줄인 글자 모양을 머리 정보에 더함
        out_entries["Contents/header.xml"] = chars.finish().encode("utf-8")
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
