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
from types import SimpleNamespace
from xml.sax.saxutils import escape, unescape


@dataclass
class FillReport:
    file: str
    filled: list[str] = field(default_factory=list)  # "회사명 → (주)지일" 같은 줄
    rows: int = 0  # 실적집계표에 넣은 실적 수
    overflow: int = 0  # 표 줄이 모자라 못 넣은 실적 수
    overflow_names: list[str] = field(default_factory=list)  # 그 실적 이름(금액이 작아 빠진 것)
    self_rows: list[list[str]] = field(default_factory=list)  # 자기평가표에 넣은 점수(항목·배점·점수·근거·확인할 것)


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
    """칸·문단의 글. 글 안 태그(<hp:fwSpace/>·<hp:tab/> 등)는 빈칸으로(11/04: '상호<fwSpace/>또는 법인명' 이름표를 못 알아봄)."""
    raw = "".join(re.findall(r"<(?:[\w.-]+:)?t\b[^>]*>(.*?)</(?:[\w.-]+:)?t>", seg, flags=re.S))
    return re.sub(r"<[^>]*>", " ", raw)


def _sq(s: str) -> str:
    return re.sub(r"\s+", "", s)


# 이름표 뒤에 끼워 넣은 값 표시(11/05 사용자 결정: 채운 글은 11pt) — 섹션을 다 채운 뒤 _apply_marks가 그 값만 11pt 글자 모양으로 떼어 냄
_VS, _VE = "\ue000", "\ue001"


def _mark(value: str) -> str:
    return f"{_VS}{escape(value)}{_VE}"


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
    (("회사명", "업체명", "상호", "법인명", "제안사명", "제안업체", "공급받는"), "회사명"),
    (("대표자", "대표이사"), "대표자"),
    (("주소", "소재지"), "주소"),
    (("전화",), "전화번호"),
    (("팩스", "FAX"), "팩스번호"),
    (("홈페이지",), "홈페이지"),
    (("E-mail", "이메일", "전자우편"), "이메일"),
    (("회사설립", "설립년", "설립연", "설립일", "개업"), "설립일"),
    (("자본금",), "자본금"),
    (("총직원수", "총종업원수", "종업원수", "직원수", "상시근로자수"), "종업원 수"),  # '총직원수 | 명'(11/05 사용자 요청)
    (("사업분야", "업태"), "사업분야"),
    (("면허/허가/등록증", "면허보유", "등록증보유"), "_licenses"),
    (("입찰명", "입찰건명", "공고명"), "_title"),  # 이번 공고 이름(서식 5·6) — '사업명'은 실적증명서의 실적 이름이기도 해서 뺌
]
_SKIP_LABEL = re.compile(r"주민|대리인|담당자|성명$|수신|발주|휴대")  # 휴대폰 = 대리인·담당자 개인 번호


_SEAL_ONLY = re.compile(r"^[(（]?(?:인|서명|서명또는인|인또는서명|직인|날인)[)）]?$")
_INLINE_LABELS = {"TEL": "전화번호", "Tel": "전화번호", "전화": "전화번호", "전화번호": "전화번호",
                  "FAX": "팩스번호", "Fax": "팩스번호", "팩스": "팩스번호", "E-mail": "이메일", "Email": "이메일", "이메일": "이메일",
                  "업태": "업태", "종목": "종목"}
_INLINE_RE = re.compile(r"(?P<lab>TEL|Tel|FAX|Fax|E-?mail|전화번호|전화|팩스|이메일|업태|종목)(?P<colon>\s*[:：])(?P<sp>[ \u00a0\u3000]*)"
                        r"(?=(?:TEL|Tel|FAX|Fax|E-?mail|전화|팩스|이메일|업태|종목)|$)")


# 표 밖 문단용 — 다음 이름표 앞 '(' · 끝의 ')'도 빈자리로 봄('전화번호 :      (FAX :      )', 11/05 국민안전체험관 서식 12).
# 표 칸엔 쓰지 않음 — 증명서 발급기관 칸 '기관 명 : (인) (전화 : )'처럼 다른 쪽이 적는 칸이 같은 모양이라.
_INLINE_LOOSE = re.compile(_INLINE_RE.pattern.replace(
    r"(?=(?:TEL|Tel|FAX|Fax|E-?mail|전화|팩스|이메일|업태|종목)|$)",
    r"(?=[(（]?\s*(?:TEL|Tel|FAX|Fax|E-?mail|전화|팩스|이메일|업태|종목)|\s*[)）]?\s*$)"))


def _fill_inline(seg: str, vault, loose: bool = False) -> tuple[str, list[str]]:
    """칸 안 'TEL :      FAX :' 같은 빈 자리에 값을 넣음(11/04 제보: 제안 제출서 전화번호 칸) — 빈칸 수는 값 길이만큼 줄여 줄 모양 유지.
    loose = 표 밖 문단(괄호 안 이름표까지)."""
    done: list[str] = []
    rx = _INLINE_LOOSE if loose else _INLINE_RE

    def fill_t(m):
        body = m.group(2)

        def rep(mm):
            key = _INLINE_LABELS.get(mm.group("lab").replace("-", "-"), "")
            value = _value(key, vault) if key else ""
            if not value:
                return mm.group(0)
            done.append(f"{mm.group('lab')} → {value}")
            pad = max(2, len(mm.group("sp")) - len(value) - 1) if mm.end() < len(body) else 0
            return f"{mm.group('lab')}{mm.group('colon')} {_mark(value)}" + " " * pad
        parts = re.split(r"(<[^>]*>)", body)  # 태그(<hp:tab/> 등)는 그대로 두고 글 조각만
        return m.group(1) + "".join(x if x.startswith("<") else rx.sub(rep, x) for x in parts) + m.group(3)
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
    if re.fullmatch(r"(?:입찰)?공고(?:\(지명\))?번호", t):  # '입찰공고(지명)번호'·'입찰공고번호'(서식 2·6)
        return "_notice_no"
    if t in ("사무실", "사무실전화", "사무실전화번호"):  # '연락처 | 사무실 / FAX' 칸(공동수급 대표자 선임계)
        return "전화번호"
    if "사업자등록번호" in t and re.match(r"생년월일|법인등록번호|법인번호", t):
        # '생년월일(사업자등록번호)'·'법인등록번호(사업자등록번호)' — 법인은 생년월일 = 법인등록번호, 괄호 안에 사업자등록번호(11/05 사용자 요청)
        return "_corp_biz"
    if t in ("서약자", "서약인", "각서인", "확약자"):  # 청렴계약 이행서약서 '서 약 자 :   (인)' = 대표자 이름(11/05)
        return "대표자"
    for words, key in LABELS:  # 앞머리가 맞는 것 먼저('홈페이지주소'는 주소가 아니라 홈페이지)
        if any(t.startswith(w) for w in words):
            return key
    if "사업자등록번호" in t:
        return "사업자등록번호"
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
    if key == "_corp_biz":
        corp, biz = vault.info.get("법인등록번호", ""), vault.info.get("사업자등록번호", "")
        return f"{corp} ({biz})" if corp and biz else corp or biz
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
    if key == "종업원 수" and v:  # '18'·'18명'·'18 명' → '18명'
        n = re.search(r"\d[\d,]*", v)
        return f"{n.group(0)}명" if n else v
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
    """금액 — 머리글 단위(백만원·천원)와 상관없이 원 단위 전체 금액, 절사 없이(11/05 사용자 요청 — 단위는 담당자가 고침)."""
    v = t.get("인정 금액(원)") or t.get("계약금액(원)")
    try:
        v = float(v)
    except (TypeError, ValueError):
        return ""
    return _in_unit(v, head)


def _in_unit(v: float, head: str) -> str:
    return f"{int(round(v)):,}"


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
    (("계약금액", "금액", "실적액", "도급액", "도급)액", "납품액"), _amount),
    (("준공", "완료일", "년월일"), lambda t, h: _fmt_ymd(t.get("준공일"))),  # '납품(준공)년 월 일'(11/05 국민안전체험관 서식 7)
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
        if not (any("사업명" in h or "용역명" in h or "공사명" in h for h in heads)
                and any(re.search(r"금액|도급\)?액|납품액|실적액", h) for h in heads)):
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
        unit = "원"  # 금액은 늘 원 단위 전체(11/05)
        for line in totals if n else []:
            for c in line:
                h = headtext.get(c.col, "")
                if _sq(c.text).startswith("총실적금액"):  # '총 실적 금액 :   백만원' 한 칸(11/04 당진)
                    edits.append((c, [f"총 실적 금액 : {_in_unit(total, amount_head)} {unit}"], False))
                elif _sq(c.text) == "건":  # '실적 누계 | 건 | 천원' 줄(11/05 국민안전체험관 서식 7)
                    edits.append((c, [f"{n}건"], False))
                elif _sq(c.text) in ("원", "천원", "백만원"):
                    edits.append((c, [_in_unit(total, amount_head) + "원"], False))
                elif "기간" in h and c.blank:
                    edits.append((c, [f"{n}건"], False))
                elif "금액" in h and c.blank:
                    edits.append((c, [_in_unit(total, amount_head) + "원"], False))
        rep.overflow += len(queue)
        break  # 표 하나에 한 번
    return edits


# 지일이 아닌 쪽이 적는 칸 — 장비 공급확약서의 발행자(제조사·공식 수입원) 등(11/04 서식 28: 지일 정보가 들어가 '스스로 확약'이 됨)
_OTHER_PARTY = re.compile(r"발행자|제조사|수입원|공급자|총판")


def _agent_rows(xml: str, cells: list[Cell]) -> set[int]:
    """'대리인'·'수임인' 칸이 세로로 묶은 줄 번호(위임장 — 대리인 주소는 개인 주소라 비움)."""
    out: set[int] = set()
    for c in cells:
        if re.fullmatch(r"대리인|수임인|위임받는자|위임받은자|피위임인", _sq(c.text)):
            rs = re.findall(r'rowSpan="(\d+)"', xml[c.start:c.end])
            out.update(range(c.row, c.row + (int(rs[-1]) if rs else 1)))
    return out


def _other_party_cells(xml: str) -> list[tuple[int, int]]:
    """줄 머리(첫 칸) 이름표가 발행자·제조사 등인 표 줄의 칸들 — 채우지 않음."""
    out = []
    for tbl in _spans(xml, "tbl"):
        cells = _cells(xml, tbl)
        rows = {c.row for c in cells if c.col == 0 and _OTHER_PARTY.search(_sq(c.text))}
        out += [(c.start, c.end) for c in cells if c.row in rows]
    return out


_SEAL_TAIL = r"[(（](?:인|서명)[^()（）]{0,8}[)）]"  # '(인)'·'(인 또는 서명)'
# 쌍점 없이 이름표만 있는 줄 — 이런 줄이 둘 이상 잇달아 있을 때만(서식 21 '주  소 / 상  호 / 대표자')
_BARE_LABELS = {"주소": "주소", "소재지": "주소", "상호": "회사명", "업체명": "회사명", "회사명": "회사명",
                "상호또는법인명": "회사명", "대표자": "대표자", "대표이사": "대표자"}


def _after_colon(seg: str, value: str, seal: bool) -> str | None:
    """문단(또는 한 문단짜리 칸) 글 속 첫 쌍점 뒤에 값. 쌍점이 없으면 맨 끝 글 뒤에 ' : 값'. 못 넣으면 None."""
    ts = list(re.finditer(r"(<(?:[\w.-]+:)?t\b[^>]*>)(.*?)(</(?:[\w.-]+:)?t>)", seg, re.S))
    hit = next((t for t in ts if re.search(r"[:：]", re.sub(r"<[^>]*>", "", t.group(2)))), None)
    if hit is None:
        if not ts:
            return None
        last = ts[-1]
        body = last.group(2).rstrip(" ") + f" : {_mark(value)}"
    else:
        used = []

        def put(mm):  # 글 속 첫 쌍점에만 — 태그 이름의 쌍점(<hp:tab/>)은 건드리지 않음
            if mm.group(1) or used:
                return mm.group(0)
            used.append(1)
            return f"{mm.group(2)} {_mark(value)}" + " " * max(2 if seal else 0, len(mm.group(3)) - len(value) - 1)
        last = hit
        body = re.sub(r"(<[^>]*>)|([:：])( *)", put, hit.group(2))
    seg = seg[:last.start(2)] + body + seg[last.end(2):]
    return re.sub(r"<(?:[\w.-]+:)?linesegarray\b.*?</(?:[\w.-]+:)?linesegarray>", "", seg, flags=re.S)


def _hang(seg: str, chars: "_Chars | None") -> str:
    """이름표 문단('주 소 : 값')에 값이 들어간 뒤 — 값이 둘째 줄로 넘어가도 값 시작 위치에 맞춰 이어지게 내어쓰기(11/05 사용자 요청).
    앞부분('   주  소 : ') 너비 = 탭은 한글 파일에 적힌 너비, 글자는 글자 모양 크기로 어림."""
    if chars is None or _VS not in seg:
        return seg
    pm = re.search(r'(<(?:[\w.-]+:)?p\b[^>]*\bparaPrIDRef=")(\d+)(")', seg)
    if not pm:
        return seg
    width, cur, in_t = 0.0, "0", False
    for part in re.split(r"(<[^>]+>)", seg[:seg.index(_VS)]):
        if part.startswith("<"):
            run = re.match(r'<(?:[\w.-]+:)?run\b[^>]*charPrIDRef="(\d+)"', part)
            if run:
                cur = run.group(1)
            elif re.match(r"<(?:[\w.-]+:)?t\b", part):
                in_t = True
            elif re.match(r"</(?:[\w.-]+:)?t>", part):
                in_t = False
            elif re.match(r"<(?:[\w.-]+:)?tab\b", part):
                tw = re.search(r'\bwidth="(\d+)"', part)
                width += int(tw.group(1)) if tw else chars.height(cur) * 2
            elif re.match(r"<(?:[\w.-]+:)?fwSpace\b", part):
                width += chars.height(cur)
            elif re.match(r"<(?:[\w.-]+:)?nbSpace\b", part):
                width += chars.char_width(cur, " ")
        elif in_t:
            width += sum(chars.char_width(cur, ch) for ch in unescape(part))
    width = int(round(width))
    if width <= 0 or width > 40000:  # 14cm 넘으면 어림이 틀렸을 수 있어 건드리지 않음
        return seg
    new = chars.hanging(pm.group(2), width)
    return seg[:pm.start(2)] + new + seg[pm.end(2):] if new != pm.group(2) else seg


def _fill_label_paragraphs(xml: str, vault, rep: FillReport, chars: "_Chars | None" = None) -> str:
    """표 밖 '업 체 명 :'처럼 이름표만 있는 문단 끝에 값을 붙임(11/04 당진 실적 서식)."""
    tbls = _spans(xml, "tbl")
    # 표 안이어도 여러 줄짜리 칸(서식 본문 칸)의 줄은 채움 — 한 줄짜리 칸은 이름표 칸이라 표 채우기가 옆 칸에 씀(11/04 서식 8·9 서명 줄)
    cells = _spans(xml, "tc")
    single = [(cs, ce) for cs, ce in cells if len(_spans(xml, "p", cs, ce)) <= 1]
    edits = []
    paras, stack = [], []  # 모든 문단(표 칸 안 문단 포함) — _spans는 맨 바깥만 줌
    for m in re.finditer(r"<(/?)(?:[\w.-]+:)?p\b[^>]*?(/?)>", xml):
        if m.group(2):
            continue
        if not m.group(1):
            stack.append(m.start())
        elif stack:
            paras.append((stack.pop(), m.end()))
    other = _other_party_cells(xml)
    cands = []  # (s, e, 이름표, 서명 줄?, 쌍점 없는 이름표?, 문단 순번)
    other_at: set[int] = set()  # '발행자   상호 :' 줄과 바로 이어지는 서명 줄('대표자 :   (인)')
    for n, (s, e) in enumerate(sorted(paras)):
        if any(ts >= s and te <= e for ts, te in tbls) or any(cs <= s and e <= ce for cs, ce in single + other):
            continue
        text = _text(xml[s:e])
        if _OTHER_PARTY.search(_sq(text)) or (n - 1 in other_at and re.search(r"[:：]", text) and len(_sq(text)) <= 24):
            other_at.add(n)
            continue
        # '신청인 : 대표자 (주)        대표자 성명 :        (인)' — '(주)' 뒤·'성명 :' 뒤가 빈 서명 줄(11/04 서식 2)
        if re.search(r"\(주\)\s{3,}", text) and re.search(rf"성\s*명\s*[:：]\s{{3,}}(?:{_SEAL_TAIL})?\s*$", text) \
                and re.match(r"\s*(?:신청인|제안자|입찰자|신청자|대표자)", text):
            company, name = _value("회사명", vault), _value("대표자", vault)
            if company and name:
                corp = company if company.startswith("(주)") else f"(주){re.sub(r'^주식회사\s*', '', company)}"

                def fill_t(mm):
                    body = mm.group(2)
                    body = re.sub(r"\(주\)( {3,})", lambda m2: _mark(corp) + " " * max(3, len(m2.group(1)) - len(corp) + 3), body, count=1)
                    body = re.sub(r"(성\s*명\s*[:：])( {3,})", lambda m2: f"{m2.group(1)} {_mark(name)}" + " " * max(3, len(m2.group(2)) - len(name) - 1), body, count=1)
                    return mm.group(1) + body + mm.group(3)
                seg = re.sub(r"(<(?:[\w.-]+:)?t\b[^>]*>)(.*?)(</(?:[\w.-]+:)?t>)", fill_t, xml[s:e], flags=re.S)
                seg = re.sub(r"<(?:[\w.-]+:)?linesegarray\b.*?</(?:[\w.-]+:)?linesegarray>", "", seg, flags=re.S)
                edits.append((s, e, seg))
                rep.filled.append(f"서명 줄 → {corp} 대표자 성명 {name}")
            continue
        # 서식에 '○○○업체 대표자 :   ○○○ (인)'처럼 자리표시가 미리 적힌 서명 줄(11/04 서식 19)
        ph = re.fullmatch(rf"\s*○{{2,}}\s*(?:업체|회사)\s*(대\s*표\s*(?:자|이\s*사))\s*[:：]\s*○{{2,}}\s*({_SEAL_TAIL})?\s*", text)
        if ph:
            company, name = _value("회사명", vault), _value("대표자", vault)
            if company and name:
                seg = re.sub(r"○{2,}(\s*)(?=업체|회사)(?:업체|회사)", lambda mm: _mark(company) + mm.group(1), xml[s:e], count=1)
                seg = re.sub(r"([:：]\s*)○{2,}", lambda mm: f"{mm.group(1)}{_mark(name)}", seg, count=1)
                seg = re.sub(r"<(?:[\w.-]+:)?linesegarray\b.*?</(?:[\w.-]+:)?linesegarray>", "", seg, flags=re.S)
                edits.append((s, e, seg))
                rep.filled.append(f"서명 줄 자리표시 → {company} 대표자 {name}")
            continue
        # 한 줄에 이름표가 둘 이상인 '전화번호 :      (FAX :      )'(11/05 국민안전체험관 서식 12) — 빈자리마다 값
        if len(_INLINE_LOOSE.findall(text)) >= 2 or re.search(r"[(（]\s*(?:FAX|Fax|팩스|E-?mail|이메일)\s*[:：]", text):
            seg, done = _fill_inline(xml[s:e], vault, loose=True)
            if done:
                edits.append((s, e, seg))
                rep.filled += done
            continue
        # '업 체 명 :' 또는 서명 줄 '대표자 :          (인)'(11/04 서식 8·9) · '신청인(대표자) :      (인 또는 서명)'(서식 1)
        m = re.fullmatch(rf"\s*(.{{2,16}}?)\s*[:：]\s*({_SEAL_TAIL})?\s*", text)
        if m:
            cands.append((s, e, m.group(1), bool(m.group(2)), False, n))
        elif _sq(text) in _BARE_LABELS and not any(cs <= s and e <= ce for cs, ce in cells):  # 표 칸 안 줄은 머리글일 수 있음
            cands.append((s, e, _sq(text), False, True, n))
    bare_at = {c[5] for c in cands if c[4]}
    for s, e, label, seal, bare, n in cands:
        if bare and not ({n - 1, n + 1} & bare_at):
            continue  # 혼자 있는 '주소' 같은 줄은 제목일 수 있음
        key = _BARE_LABELS[label] if bare else _label_key(label)
        value = _value(key, vault) if key else ""
        if not value:
            continue
        seg = _after_colon(xml[s:e], value, seal)
        if seg is None:
            continue
        if bare:  # 쌍점 없는 서식은 쌍점 없이 — ' : 값'을 '   값'으로
            seg = seg.replace(f" : {_mark(value)}", f"   {_mark(value)}", 1)
        edits.append((s, e, _hang(seg, chars)))
        rep.filled.append(f"{_sq(label)} → {value[:40]}")
    for s, e, seg in sorted(edits, key=lambda x: x[0], reverse=True):  # 뒤에서부터 — 문단 순서대로 모이지 않은 고침도 있어 정렬(11/05 서식 3 줄 겹침)
        xml = xml[:s] + seg + xml[e:]
    return xml


# ── 채운 글 크기(11/05 사용자 결정: 채운 글은 전부 11pt — 서식에 원래 적힌 글자는 그대로) ─────────────
# 칸에 한 줄로 안 들어가면 11pt 그대로 두 줄로(칸이 늘어남). 줄 간격이 100% 미만·고정값인 칸만 두 줄이면 글이 겹쳐서(11/04 필암서원 주소 칸)
# 한 줄에 들어가게 같은 글꼴에서 크기만 줄인 글자 모양을 header.xml에 더해 씀(최소 7pt). 글꼴·장평·색은 칸의 원래 글자 모양 그대로.
FILL_HEIGHT = 1100  # 11pt
MIN_HEIGHT = 700  # 7pt


class _Chars:
    def __init__(self, header: str):
        self.header = header
        self.blocks = {m.group(1): m.group(0) for m in
                       re.finditer(r'<(?:[\w.-]+:)?charPr\b[^>]*\bid="(\d+)".*?</(?:[\w.-]+:)?charPr>', header, re.S)}
        self.paras = {m.group(1): m.group(0) for m in
                      re.finditer(r'<(?:[\w.-]+:)?paraPr\b[^>]*\bid="(\d+)".*?</(?:[\w.-]+:)?paraPr>', header, re.S)}
        self.added: dict[tuple[str, int | None], str] = {}
        self.added_para: dict[str, str] = {}  # 문단 모양 id → 가운데 정렬로 바꾼 사본 id
        self.added_left: dict[str, str] = {}  # 문단 모양 id → 왼쪽 정렬로 바꾼 사본 id(양쪽·배분 정렬 칸)
        self.added_hang: dict[tuple[str, int], str] = {}  # (문단 모양 id, 내어쓰기 너비) → 사본 id
        # 언어별 글꼴 목록 {LATIN: {id: 이름}} — 채운 글의 숫자·영문을 한글과 같은 글꼴로(11/04 사용자 제보: 숫자 글꼴이 다름)
        self.faces = {m.group(1).upper(): dict(re.findall(r'<(?:[\w.-]+:)?font\b[^>]*\bid="(\d+)"[^>]*\bface="([^"]*)"', m.group(2)))
                      for m in re.finditer(r'<(?:[\w.-]+:)?fontface\b[^>]*\blang="(\w+)"[^>]*>(.*?)</(?:[\w.-]+:)?fontface>', header, re.S)}

    def _block(self, cid: str) -> str:
        """글자 모양 정의 — 채우면서 더한 사본(11pt·줄인 크기)이면 원래 모양에 그 크기를 넣은 것."""
        if cid in self.blocks:
            return self.blocks[cid]
        for (orig, h), new in self.added.items():
            if new == cid and orig in self.blocks:
                b = self.blocks[orig]
                return re.sub(r'\bheight="\d+"', f'height="{h}"', b, count=1) if h is not None else b
        return ""

    def height(self, cid: str) -> int:
        m = re.search(r'\bheight="(\d+)"', self._block(cid))
        return int(m.group(1)) if m else 1000

    def ratio(self, cid: str) -> float:
        m = re.search(r'<(?:[\w.-]+:)?ratio\b[^>]*\bhangul="(\d+)"', self._block(cid))
        return int(m.group(1)) / 100 if m else 1.0

    def _next_para_id(self) -> str:
        ids = [int(x) for x in self.paras] + [int(x) for x in self.added_para.values()] + [int(x) for x in self.added_hang.values()] \
            + [int(x) for x in self.added_left.values()]
        return str(max(ids) + 1)

    def centered(self, pid: str) -> str:
        """pid 문단 모양을 가운데 정렬로 바꾼 사본 id(이미 가운데거나 모르면 pid 그대로)."""
        if pid not in self.paras or self.align(pid) in ("CENTER", ""):
            return pid
        if pid not in self.added_para:
            self.added_para[pid] = self._next_para_id()
        return self.added_para[pid]

    def left(self, pid: str) -> str:
        """양쪽·배분 정렬 문단 모양의 왼쪽 정렬 사본 id — 채운 값이 줄을 넘기면 첫 줄 글자 사이가 벌어져서(11/05 제보: 법인등록번호
        '1 1 0 1 1 1 - …'). 다른 정렬이면 pid 그대로."""
        if pid not in self.paras or self.align(pid) not in ("JUSTIFY", "DISTRIBUTE", "DISTRIBUTE_SPACE"):
            return pid
        if pid not in self.added_left:
            self.added_left[pid] = self._next_para_id()
        return self.added_left[pid]

    def margins(self, pid: str) -> tuple[int, int]:
        """(들여쓰기 intent, 왼쪽 여백) HWPUNIT — 문단 모양의 첫 margin 묶음(hp:case가 있으면 그것)."""
        block = self.paras.get(pid, "")
        m = re.search(r"<(?:[\w.-]+:)?margin\b.*?</(?:[\w.-]+:)?margin>", block, re.S)
        get = lambda k: int(re.search(rf'<(?:[\w.-]+:)?{k}\b[^>]*value="(-?\d+)"', m.group(0)).group(1)) \
            if m and re.search(rf'<(?:[\w.-]+:)?{k}\b[^>]*value="', m.group(0)) else 0
        return get("intent"), get("left")

    def hanging(self, pid: str, width: int) -> str:
        """pid 문단 모양에 내어쓰기 width를 준 사본 id — 둘째 줄부터 첫 줄 시작보다 width만큼 오른쪽에서 시작(11/05 '주 소 :' 줄)."""
        if pid not in self.paras or self.align(pid) in ("CENTER", "RIGHT", "DISTRIBUTE"):
            return pid
        key = (pid, width)
        if key not in self.added_hang:
            self.added_hang[key] = self._next_para_id()
        return self.added_hang[key]

    def _hang_block(self, pid: str, width: int, new: str) -> str:
        block = re.sub(r'\bid="\d+"', f'id="{new}"', self.paras[pid], count=1)
        intent, left = self.margins(pid)
        start = left + max(intent, 0)  # 첫 줄이 시작하는 곳 — 새 모양에서도 첫 줄은 그대로

        def margin(mm):  # hp:default 묶음은 값이 hp:case의 두 배(한글 저장 형식) — 원래 비율대로
            seg = mm.group(0)
            cur = re.search(r'<(?:[\w.-]+:)?left\b[^>]*value="(-?\d+)"', seg)
            factor = (int(cur.group(1)) / left) if cur and left else (2 if mm.start() > first_end else 1)
            seg = re.sub(r'(<(?:[\w.-]+:)?intent\b[^>]*value=")-?\d+', lambda x: f"{x.group(1)}{round(-width * factor)}", seg, count=1)
            return re.sub(r'(<(?:[\w.-]+:)?left\b[^>]*value=")-?\d+', lambda x: f"{x.group(1)}{round(start * factor)}", seg, count=1)
        first = re.search(r"<(?:[\w.-]+:)?margin\b.*?</(?:[\w.-]+:)?margin>", block, re.S)
        first_end = first.end() if first else 0
        return re.sub(r"<(?:[\w.-]+:)?margin\b.*?</(?:[\w.-]+:)?margin>", margin, block, flags=re.S)

    def char_width(self, cid: str, ch: str) -> float:
        """글자 하나 너비 어림(HWPUNIT). 빈칸은 '글꼴에 어울리는 빈칸'이 꺼져 있으면 글자 크기의 절반(한글 기본)."""
        h, ratio, block = self.height(cid), self.ratio(cid), self._block(cid)
        sp = re.search(r'<(?:[\w.-]+:)?spacing\b[^>]*\bhangul="(-?\d+)"', block)
        extra = 1 + (int(sp.group(1)) / 100 if sp else 0)
        if ch == " ":
            return h * (0.3 if 'useFontSpace="1"' in block else 0.5) * extra
        # 한글·한자·전각 + ❍○□※①· 같은 기호(한글 글꼴에서 한 글자 너비)
        if "가" <= ch <= "힣" or "一" <= ch <= "鿿" or "　" <= ch <= "ヿ" or "＀" <= ch <= "￯" \
                or "\u2460" <= ch <= "\u27bf" or ch in "※·ㆍ":
            return h * ratio * extra
        return h * ratio * (0.5 if ch.isalnum() else 0.45) * extra

    def align(self, pid: str) -> str:
        m = re.search(r'<(?:[\w.-]+:)?align\b[^>]*\bhorizontal="(\w+)"', self.paras.get(pid, ""))
        return m.group(1) if m else ""

    def _para(self, pid: str) -> str:
        """문단 모양 정의 — 채우면서 더한 사본(가운데 정렬·내어쓰기)이면 원래 모양(줄 간격 등은 같음)."""
        if pid in self.paras:
            return self.paras[pid]
        for orig, new in list(self.added_para.items()) + list(self.added_left.items()):
            if new == pid:
                return self.paras.get(orig, "")
        for (orig, _w), new in self.added_hang.items():
            if new == pid:
                return self.paras.get(orig, "")
        return ""

    def spacing(self, pid: str) -> float | None:
        """줄 간격 비율(160% → 1.6). 비율이 아닌 줄 간격(고정값 등)·문단 모양을 모르면 None."""
        block = self._para(pid)
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

    @property
    def changed(self) -> bool:
        return bool(self.added or self.added_para or self.added_hang or self.added_left)

    def finish(self) -> str:
        header = self.header
        if self.added_para or self.added_hang or self.added_left:
            def aligned(pid, new, how):
                return re.sub(r'\bhorizontal="\w+"', f'horizontal="{how}"', re.sub(r'\bid="\d+"', f'id="{new}"', self.paras[pid], count=1))
            extra = "".join(aligned(pid, new, "CENTER") for pid, new in self.added_para.items())
            extra += "".join(aligned(pid, new, "LEFT") for pid, new in self.added_left.items())
            extra += "".join(self._hang_block(pid, w, new) for (pid, w), new in self.added_hang.items())
            n = len(self.added_para) + len(self.added_hang) + len(self.added_left)
            header = re.sub(r"(</(?:[\w.-]+:)?paraProperties>)", lambda m: extra + m.group(1), header, count=1)
            header = re.sub(r'(<(?:[\w.-]+:)?paraProperties\b[^>]*\bitemCnt=")(\d+)"',
                            lambda m: f'{m.group(1)}{int(m.group(2)) + n}"', header, count=1)
        if not self.added:
            return header

        def block(cid: str, h: int | None, new: str) -> str:
            b = re.sub(r'\bid="\d+"', f'id="{new}"', self.blocks[cid], count=1)
            if h is not None:
                b = re.sub(r'\bheight="\d+"', f'height="{h}"', b, count=1)
            ref = self._font_ref(cid)
            return b.replace(ref, self._unified_ref(cid), 1) if ref else b
        extra = "".join(block(cid, h, new) for (cid, h), new in self.added.items())
        h = re.sub(r"(</(?:[\w.-]+:)?charProperties>)", lambda m: extra + m.group(1), header, count=1)
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


def _fill_variant(chars: "_Chars", cid: str) -> str:
    """cid 글자 모양의 11pt 사본(숫자·영문도 한글 글꼴로). 이미 11pt면 크기는 그대로 두고 글꼴만."""
    return chars.variant(cid, None if chars.height(cid) == FILL_HEIGHT else FILL_HEIGHT)


def _fit_char(cell_xml: str, line: str, chars: "_Chars") -> tuple[str | None, str]:
    """(채운 글에 쓸 글자 모양 id, 알림). 늘 11pt — 줄 간격이 100% 미만·고정값인 칸에서 한 줄에 안 들어갈 때만 줄임."""
    run = re.search(r'<(?:[\w.-]+:)?run\b[^>]*charPrIDRef="(\d+)"', cell_xml)
    cid = run.group(1) if run else "0"
    first_p = re.search(r'<(?:[\w.-]+:)?p\b[^>]*paraPrIDRef="(\d+)"', cell_xml)
    spacing = chars.spacing(first_p.group(1)) if first_p else 1.6
    size = re.search(r'<(?:[\w.-]+:)?cellSz\b[^>]*width="(\d+)"[^>]*height="(\d+)"', cell_xml)
    if spacing is not None and spacing >= 1.0 or not size:  # 두 줄로 넘어가도 되는 칸
        return _fill_variant(chars, cid), ""
    tag = re.search(r'<(?:[\w.-]+:)?cellMargin\b[^>]*>', cell_xml)
    mg = dict(re.findall(r'\b(left|right|top|bottom)="(\d+)"', tag.group(0) if tag else ""))
    width = int(size.group(1)) - int(mg.get("left", 141)) - int(mg.get("right", 141))
    ratio = chars.ratio(cid)

    def fits(h: int) -> bool:  # 한 줄에 들어가나
        return _text_width(line, h, ratio) <= width * 0.95

    if width <= 0 or fits(FILL_HEIGHT):
        return _fill_variant(chars, cid), ""
    new_h = next((h for h in range(FILL_HEIGHT - 10, MIN_HEIGHT - 1, -10) if fits(h)), MIN_HEIGHT)
    note = f"줄 간격이 좁은 칸이라 글자 11→{new_h / 100:g}pt로 줄여 한 줄에 맞춤"
    if not fits(new_h):
        note += "(7pt로도 넘쳐 글이 겹칠 수 있음 — 확인)"
    return chars.smaller(cid, new_h), note


def _apply_marks(xml: str, chars: "_Chars | None") -> str:
    """이름표 뒤에 끼워 넣은 값(_mark)을 그 글 조각만 11pt 글자 모양으로 떼어 냄 — 같은 줄의 이름표·빈칸은 원래 글자 모양 그대로."""
    if _VS not in xml:
        return xml
    out, pos = [], 0
    for m in re.finditer(f"{_VS}(.*?){_VE}", xml, re.S):
        before = xml[max(0, m.start() - 20000):m.start()]
        runs = list(re.finditer(r'<([\w.-]+:)?run\b[^>]*?charPrIDRef="(\d+)"[^>]*>', before))
        ts = list(re.finditer(r"<(?:[\w.-]+:)?t\b[^>]*>", before))
        value = m.group(1)
        if chars is not None and runs and ts:
            r, t = runs[-1], ts[-1]
            new = _fill_variant(chars, r.group(2))
            if new != r.group(2):
                p = r.group(1) or ""
                value = (f'</{p}t></{p}run><{p}run charPrIDRef="{new}"><{p}t>{value}</{p}t></{p}run>'
                         f"{r.group(0)}{t.group(0)}")
        out.append(xml[pos:m.start()] + value)
        pos = m.end()
    return "".join(out) + xml[pos:]


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


_NUMBER = re.compile(r"[\d*][\d\-*~.() ]{5,}")


def _align_numbers(xml: str, edits: list, chars: "_Chars", override: dict[int, str]) -> None:
    """같은 표·같은 열에 넣은 번호(전화·FAX·등록번호) 칸 중 하나가 가운데 정렬이면 나머지 번호 칸도 가운데로
    (11/04 사용자 요청: 서식 1에서 FAX만 가운데, 법인등록번호·전화는 왼쪽이라 어긋나 보임)."""
    by_col: dict[int, list[tuple[Cell, str]]] = {}
    for cell, lines, _keep in edits:
        if not lines or len(lines) != 1 or not _NUMBER.fullmatch(lines[0].strip()):
            continue
        m = re.search(r'<(?:[\w.-]+:)?p\b[^>]*\bparaPrIDRef="(\d+)"', xml[cell.start:cell.end])
        if m:
            by_col.setdefault(cell.col, []).append((cell, m.group(1)))
    for group in by_col.values():
        center = next((pid for _c, pid in group if chars.align(pid) == "CENTER"), None)
        if center is None or len(group) < 2:
            continue
        for cell, pid in group:
            if chars.align(pid) != "CENTER":
                override[cell.start] = center


def _cell_need(cell: str, chars: "_Chars") -> int | None:
    """칸 안 글이 차지할 높이 어림(HWPUNIT) — 문단마다 (줄 수 × 가장 큰 글자 × 줄 간격) + 칸 위아래 여백. 표가 든 칸은 None."""
    if re.search(r"<(?:[\w.-]+:)?tbl\b", cell[cell.find(">") + 1:]):
        return None
    size = re.search(r'<(?:[\w.-]+:)?cellSz\b[^>]*width="(\d+)"', cell)
    tag = re.search(r'<(?:[\w.-]+:)?cellMargin\b[^>]*>', cell)
    mg = dict(re.findall(r'\b(left|right|top|bottom)="(\d+)"', tag.group(0) if tag else ""))
    avail = (int(size.group(1)) if size else 0) - int(mg.get("left", 141)) - int(mg.get("right", 141))
    if avail <= 0:
        return None
    need = int(mg.get("top", 141)) + int(mg.get("bottom", 141))
    for ps, pe in _spans(cell, "p"):
        para = cell[ps:pe]
        pm = re.search(r'paraPrIDRef="(\d+)"', para)
        spacing = chars.spacing(pm.group(1)) if pm else 1.6
        width, cur, in_t, tallest = 0.0, "0", False, 0
        for part in re.split(r"(<[^>]+>)", para):
            if part.startswith("<"):
                run = re.match(r'<(?:[\w.-]+:)?run\b[^>]*charPrIDRef="(\d+)"', part)
                if run:
                    cur = run.group(1)
                    tallest = max(tallest, chars.height(cur))
                elif re.match(r"<(?:[\w.-]+:)?t\b", part):
                    in_t = True
                elif re.match(r"</(?:[\w.-]+:)?t>", part):
                    in_t = False
                elif re.match(r"<(?:[\w.-]+:)?tab\b", part):
                    tw = re.search(r'\bwidth="(\d+)"', part)
                    width += int(tw.group(1)) if tw else chars.height(cur) * 2
            elif in_t:
                width += sum(chars.char_width(cur, ch) for ch in unescape(part))
        lines = max(1, -(-int(width) // avail))
        need += int(lines * (tallest or 1000) * (spacing if spacing else 1.0))
    return need


def _row_parts(tbl_xml: str) -> list[list[tuple[int, int]]]:
    return [[(ts + cs, ts + ce) for cs, ce in _spans(tbl_xml[ts:te], "tc")] for ts, te in _spans(tbl_xml, "tr")]


_ROWSPAN = re.compile(r'rowSpan="([2-9]|\d\d+)"')


def _row_info(tbl_xml: str, chars: "_Chars") -> list[tuple[int, int]] | None:
    """줄마다 (칸 높이, 글이 차지할 높이 어림). 세로로 합친 칸은 그 줄 계산에서 뺌. 어림이 안 되는 칸(표가 든 칸)이 있으면 None."""
    out = []
    for cells in _row_parts(tbl_xml):
        height, need = 0, 0
        for cs, ce in cells:
            cell = tbl_xml[cs:ce]
            h = re.search(r'<(?:[\w.-]+:)?cellSz\b[^>]*height="(\d+)"', cell)
            if not h:
                return None
            if _ROWSPAN.search(cell):
                continue
            n = _cell_need(cell, chars)
            if n is None:
                return None
            height, need = max(height, int(h.group(1))), max(need, n)
        out.append((height, need))
    return out


def _page_slack(xml: str, tbl_end: int) -> int | None:
    """표가 든 문단의 원본 줄 배치(linesegarray)로 본 그 쪽에 남는 높이(HWPUNIT) — 쪽 본문 높이 − (줄 위치 + 줄 높이). 모르면 None."""
    page = re.search(r'<(?:[\w.-]+:)?pagePr\b[^>]*\bheight="(\d+)".*?<(?:[\w.-]+:)?margin\b([^>]*)>', xml, re.S)
    seg = re.match(r'(?:<(?:[\w.-]+:)?t\s*/>|</(?:[\w.-]+:)?run>|\s)*<(?:[\w.-]+:)?linesegarray>(.*?)</(?:[\w.-]+:)?linesegarray>',
                   xml[tbl_end:tbl_end + 2000], re.S)
    if not page or not seg:
        return None
    mg = dict(re.findall(r'\b(header|footer|top|bottom)="(\d+)"', page.group(2)))
    lines = re.findall(r'vertpos="(\d+)" vertsize="(\d+)"', seg.group(1))
    if len(mg) < 4 or not lines:
        return None
    body = int(page.group(1)) - sum(int(v) for v in mg.values())
    vp, vs = map(int, lines[-1])
    return body - vp - vs


def _keep_table_heights(before: str, after: str, chars: "_Chars", rep: FillReport) -> str:
    """채운 글 때문에 줄이 높아지면 같은 표에서 여유가 가장 많은 줄(연혁 빈 칸 등)을 그만큼 줄여 표 전체 높이를 원래대로(11/05 사용자 요청).
    계기: 필암서원 서식 12 — 원본도 쪽 끝까지 0.2mm만 남는 표인데 업태·종목이 두 줄이 되며 '글자처럼 취급' 표가 통째로 다음 쪽으로 넘어가
    제목만 남음(서식 2·16도 남는 공간 0.6mm 이하). 어림 오차를 줄이려 채우기 전 같은 표와 비교해 늘어난 만큼만 셈."""
    old_tbls, new_tbls = _spans(before, "tbl"), _spans(after, "tbl")
    if len(old_tbls) != len(new_tbls):
        return after
    for (os_, oe), (ns, ne) in reversed(list(zip(old_tbls, new_tbls))):
        old, new = before[os_:oe], after[ns:ne]
        if old == new or re.search(r"<(?:[\w.-]+:)?tbl\b", new[4:]):
            continue
        r_old, r_new = _row_info(old, chars), _row_info(new, chars)
        if r_old is None or r_new is None or len(r_old) != len(r_new):
            continue
        grow = [max(0, n - h) - max(0, no - ho) for (ho, no), (h, n) in zip(r_old, r_new)]
        extra = sum(grow)  # 늘어난 줄 − 줄어든 줄(빈칸이 값으로 바뀌며 짧아진 줄)
        slack = _page_slack(before, oe)  # 원본에서 그 쪽에 남던 높이 — 그만큼은 늘어나도 쪽이 안 넘어감(0.5mm 남김)
        if slack is not None:
            extra -= max(0, slack - 150)
        if extra <= 0:
            continue
        extra += 300  # 글자 너비는 어림이라 1mm 더 줄여 둠
        # 여유 = 칸 높이 − 글 높이 − 0.5mm 남김(어림 오차) — 여유가 큰 줄부터 필요한 만큼 줄임
        rows = _spans(new, "tr")
        # 1cm 넘게 비는 줄만(11/05 국민안전체험관 서식 4: 면허 칸이 길어 주소·전화 같은 보통 줄까지 빠듯하게 줄였음)
        free = sorted(((h - n - 500, i) for i, (h, n) in enumerate(r_new) if grow[i] <= 0 and h - n >= 2835), reverse=True)
        cuts: dict[int, int] = {}
        for room, i in free:
            if extra <= 0 or room <= 0:
                break
            if _ROWSPAN.search(new[rows[i][0]:rows[i][1]]):
                continue
            cuts[i] = min(room, extra)
            extra -= cuts[i]
        if not cuts:
            rep.filled.append("⚠ 채운 글로 표가 길어졌는데 줄일 여유 칸이 없음 — 제목과 표가 다른 쪽이면 확인")
            continue
        for i in sorted(cuts, reverse=True):
            ts, te = rows[i]
            tr = re.sub(r'(cellSz\b[^>]*height=")(\d+)"', lambda m: f'{m.group(1)}{int(m.group(2)) - cuts[i]}"', new[ts:te])
            new = new[:ts] + tr + new[te:]
        total = sum(cuts.values())
        new = re.sub(r'(<(?:[\w.-]+:)?sz\b[^>]*\bheight=")(\d+)"', lambda m: f'{m.group(1)}{max(0, int(m.group(2)) - total)}"', new, count=1)
        after = after[:ns] + new + after[ne:]
        names = ", ".join(f"'{_sq(_text(new[slice(*_spans(new, 'tr')[i])]))[:10]}'" for i in sorted(cuts))
        rep.filled.append(f"채운 글로 표가 길어져 쪽을 넘지 않게 {names} 줄을 {total / 283.46:.1f}mm 줄임")
        if extra > 300:
            rep.filled.append(f"⚠ 줄일 여유가 모자라 표가 쪽을 넘을 수 있음({(extra - 300) / 283.46:.1f}mm) — 제목과 표가 다른 쪽이면 확인")
    return after


_NO_SLOT = re.compile(r"\s*제\s*\d{4}\s*[-–]\s*호\s*")  # 서식에 미리 적힌 '제 2026 - 호'


def agency_notice_no(texts: dict[str, str]) -> tuple[str, str] | None:
    """공고문의 기관 공고번호('장성군 공고 제2026 - 1129 호') → ('2026', '1129'). 이름에 '공고'가 든 문서 먼저."""
    for name in sorted(texts, key=lambda n: "공고" not in n):
        m = re.search(r"공\s*고\s*제\s*(\d{4})\s*[-–]\s*(\d{1,6})\s*호", texts[name] or "")
        if m:
            return m.group(1), m.group(2)
    return None


# ── 입찰보증금 지급 확약서(11/05 사용자 요청) ─────────────────────────
# 입찰 금액 = 제안요청서의 기초금액(없으면 사업비·사업예산 — 공고문), 보증금 = 그 금액 × BOND_RATE, '금 420,000,000원(금 사억이천만원)'
BOND_RATE = 1.05  # 기초금액 × 1.05(11/05 사용자 확인 — 4억이면 4억 2천만원)
_BASIS = re.compile(r"(?:기초\s*금액|사\s*업\s*비|사업\s*예산|사업\s*금액|소요\s*예산)(?:\([^)]{0,12}\))?[^\d\n]{0,60}?([\d]{1,3}(?:,\d{3}){2,})\s*원")


def basis_amount(texts: dict[str, str]) -> tuple[int, str] | None:
    """(기초금액, 읽은 문서). 제안요청서 먼저, 그다음 공고문·나머지."""
    order = sorted(texts, key=lambda n: (0 if "제안요청" in n else 1 if "공고" in n else 2))
    for name in order:
        t = re.sub(r"\s+", " ", texts[name] or "")
        m = _BASIS.search(t)
        if m:
            return int(m.group(1).replace(",", "")), name
    return None


_KD = "영일이삼사오육칠팔구"


def kor_money(n: int) -> str:
    """400000000 → '사억', 1500000 → '백오십만', 20000000 → '이천만'."""
    if n <= 0:
        return "영"
    out = []
    for gi, unit in enumerate(["", "만", "억", "조", "경"]):
        g = (n // 10 ** (4 * gi)) % 10000
        if not g:
            continue
        part = ""
        for d, u in ((g // 1000, "천"), (g // 100 % 10, "백"), (g // 10 % 10, "십"), (g % 10, "")):
            if d:
                part += ("" if d == 1 and u else _KD[d]) + u
        out.append(part + unit)
    return "".join(reversed(out))


def _won_text(n: int) -> str:
    return f"{n:,}원(금 {kor_money(n)}원)"


def _fill_bond(xml: str, basis: int | None, rep: FillReport) -> str:
    """'입 찰 금 액 : 금     원(금     원)'·'입찰 보증금 : 금     원(금     원)' — 보증금 확약서·각서에서만(가격제안서의 입찰금액은 지일이 정함)."""
    flat = _sq(_text(xml))
    if not basis or "보증금" not in flat or not re.search(r"확약|각서|지급", flat):
        return xml
    bond = int(round(basis * BOND_RATE))
    blank = r"(금)\s+(원\s*\(\s*금)\s+(원\s*\))"
    done = []

    def fill_t(m):
        body = m.group(2)
        for label, value in ((r"입\s*찰\s*금\s*액", basis), (r"보\s*증\s*금", bond)):
            rx = re.compile(rf"({label}\s*[:：]\s*){blank}")
            if rx.search(body):
                body = rx.sub(lambda mm: f"{mm.group(1)}금 {_mark(_won_text(value))}", body, count=1)
                done.append(value)
        return m.group(1) + body + m.group(3)
    new = re.sub(r"(<(?:[\w.-]+:)?t\b[^>]*>)(.*?)(</(?:[\w.-]+:)?t>)", fill_t, xml, flags=re.S)
    if done:  # 값을 넣은 문단은 줄 배치를 다시 계산하게
        new = re.sub(r"(<(?:[\w.-]+:)?p\b[^>]*>(?:(?!</(?:[\w.-]+:)?p>).)*?\ue000(?:(?!</(?:[\w.-]+:)?p>).)*?)"
                     r"<(?:[\w.-]+:)?linesegarray\b.*?</(?:[\w.-]+:)?linesegarray>", r"\1", new, flags=re.S)
        rep.filled.append(f"입찰 금액 → {basis:,}원(기초금액) · 입찰 보증금 → {bond:,}원(기초금액 × {BOND_RATE:g})")
    return new


def _self_score_edits(cells: list[Cell], ctx, rep: FillReport) -> list[tuple[Cell, list[str], bool]]:
    """자기평가 심사표('자기평점'·'평점' 칸)에 점수 — 제안요청서 배점표·계산 조건대로, 서식 배점 기준과 다르면 서식 기준,
    계산식끼리 다르면 유리한 쪽(11/05 사용자 결정, `selfscore.score_form`). '계' = 모든 줄 점수가 있을 때만 합계."""
    from .selfscore import fmt, read_form, score_form

    rows = read_form(cells)
    if not rows:
        return []
    out = []
    scored = score_form(rows, ctx.criteria, ctx.vault, ctx.ref)
    for row, item in scored:
        name = _sq(row.label)[:16]
        rep.self_rows.append([row.label, fmt(row.points), fmt(item.score), item.basis, "\n".join(item.notes)])
        if item.score is None:
            rep.filled.append(f"자기평점 '{name}' 비움 — " + "; ".join(item.notes[:2]))
            continue
        out.append((row.cell, [fmt(item.score)], False))
        rep.filled.append(f"자기평점 '{name}' → {fmt(item.score)}점 ({item.basis})")
    total = next((r for r in rows if r.total), None)
    if scored and all(it.score is not None for _, it in scored):
        value = round(sum(it.score for _, it in scored), 2)
        if total is not None:
            out.append((total.cell, [fmt(value)], False))
            rep.filled.append(f"자기평점 계 → {fmt(value)}점")
        rep.self_rows.append(["계", fmt(sum(r.points or 0 for r, _ in scored)), fmt(value), "서식 줄 합계", ""])
    return out


def fill_hwpx(src: Path, dst: Path, vault, tracks: list[dict] | None = None, title: str = "",
              notice_no: tuple[str, str] | None = None, g2b_no: str = "", self_scores=None,
              basis: int | None = None) -> FillReport:
    """src 서식(HWPX)을 채워 dst에 저장. tracks: 실적집계표에 넣을 실적(후보 순서대로). title: 공고 이름(입찰명 칸)."""
    rep = FillReport(Path(dst).name)
    # 묶음은 그대로 두고 이번 서식에만 공고 이름·공고번호를 더함(11/04 사용자 요청 — 공고마다 자동)
    # 공고번호 = 공고문의 기관 공고번호('제2026-1129호'), 못 찾으면 나라장터 공고번호
    # 공고번호 = 나라장터 입찰공고번호('R26BK01806620-000', 11/04 사용자 정정 — 기관 공고번호를 넣었더니 오기입),
    # 나라장터 번호를 모를 때만 공고문의 기관 공고번호('제2026-1129호')
    no_text = g2b_no or (f"제{notice_no[0]}-{notice_no[1]}호" if notice_no else "")
    if title or no_text:
        vault = SimpleNamespace(info={**vault.info, "_title": title, "_notice_no": no_text}, docs=vault.docs, history=vault.history)
    zin = zipfile.ZipFile(src)
    out_entries = {}
    chars = _Chars(zin.read("Contents/header.xml").decode("utf-8")) if "Contents/header.xml" in zin.namelist() else None
    for name in zin.namelist():
        data = zin.read(name)
        if re.fullmatch(r"Contents/section\d+\.xml", name):
            xml = data.decode("utf-8")
            edits: list[tuple[Cell, list[str], bool]] = []
            para_override: dict[int, str] = {}  # 칸 시작 위치 → 쓸 문단 모양(번호 칸 가운데 정렬 맞춤)
            if tracks:  # 세로로 긴 빈 칸 한 줄인 실적표는 먼저 줄을 나눔
                for i in range(len(_spans(xml, "tbl"))):
                    xml = _split_tall_rows(xml, _spans(xml, "tbl")[i], len(tracks))
            before_fill = xml
            # 서식이 '해당 사항이 없을 경우 "해당 없음" 기재'라고 적었으면 빈 표 첫 칸에 '해당 없음'(11/04 사용자 요청 — 행정처분내용 등)
            none_rule = re.search(r"(?:없을|없는)\s*(?:경우|때).{0,40}?해당\s*(?:사항\s*)?없음", _text(xml), re.S)
            for tbl in _spans(xml, "tbl"):
                cells = _cells(xml, tbl)
                by_pos = {(c.row, c.col): c for c in cells}
                other_rows = {c.row for c in cells if c.col == 0 and _OTHER_PARTY.search(_sq(c.text))}
                cells = [c for c in cells if c.row not in other_rows]  # 발행자·제조사 줄은 안 채움
                agent_rows = _agent_rows(xml, cells)  # 위임장 '대리인' 칸이 세로로 묶은 줄 — 주소는 비움(11/05 사용자 요청)
                if tracks:
                    edits += _fill_tracks(xml, cells, tracks, rep)
                if self_scores:
                    edits += _self_score_edits(cells, self_scores, rep)
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
                    if key == "주소" and c.row in agent_rows:
                        continue
                    target = by_pos.get((c.row, c.col + c.colspan))
                    value = _value(key, vault)
                    if key == "전화번호" and re.search(r"FAX|Fax|팩스", c.text) and _value("팩스번호", vault) and value:
                        value = f"{value} / {_value('팩스번호', vault)}"  # '전화번호(FAX번호)' 한 칸(11/05 국민안전체험관 서식 2)
                    if key == "회사명" and "대표" in _sq(c.text) and not _sq(c.text).startswith("대표사") and _value("대표자", vault):
                        value = f"{value} {_value('대표자', vault)}".strip()  # '상호·법인명(대표성명)'
                    if not value:
                        continue
                    if target is None and not any(o.row == c.row and o.col > c.col for o in cells) \
                            and len(_spans(xml, "p", c.start, c.end)) == 1 \
                            and re.fullmatch(r"[^:：]{1,20}[:：]?", c.text.strip()):  # 이름표 하나뿐('TEL : FAX :'는 칸 안 채우기가 함)
                        # 줄 전체를 차지하는 '◦대표자 :'·'제안업체명' 칸(서식 11 표지) — 그 칸 안 이름표 뒤에
                        seg = _after_colon(xml[c.start:c.end], value, False)
                        if seg is not None:
                            edits.append((c, None, seg))
                            rep.filled.append(f"{_sq(c.text)} → {value[:40]}")
                        continue
                    if target is None:
                        continue
                    seal = _sq(target.text)
                    if key == "_notice_no" and _NO_SLOT.fullmatch(target.text) and (g2b_no or notice_no):
                        # 서식에 미리 적힌 '제 2026 - 호' — 나라장터 번호면 칸을 그 번호로, 기관 번호면 모양 그대로 번호만
                        line = g2b_no or f"제 {notice_no[0]} - {notice_no[1]} 호"
                        edits.append((target, [line], False))
                        rep.filled.append(f"{_sq(c.text)} → {line}")
                        continue
                    if key == "회사명" and not target.blank and _value("대표자", vault) and re.fullmatch(
                            rf"\s*대\s*표\s*(?:자|이\s*사)\s*[:：]\s*(?:{_SEAL_TAIL})?\s*", target.text):
                        # '회사명 | 대표자 :      (인)' — 값 칸에 대표자 서명 줄이 미리 적힌 칸(11/05 국민안전체험관 서식 1 질의서)
                        seg = _after_colon(xml[target.start:target.end], _value("대표자", vault), True)
                        if seg is not None:
                            seg = re.sub(r"(<(?:[\w.-]+:)?t\b[^>/]*>)", lambda m: m.group(1) + _mark(value) + "   ", seg, count=1)
                            edits.append((target, None, seg))
                            rep.filled.append(f"{_sq(c.text)} → {value} · 대표자 {_value('대표자', vault)}")
                        continue
                    if not target.blank:
                        if not _SEAL_ONLY.match(seal):
                            continue
                        value = f"{value}   {target.text.strip()}"  # '(인)' 자리 — 이름 뒤에 그대로 남김(11/04 제보)
                        pm = re.search(r'<(?:[\w.-]+:)?p\b[^>]*\bparaPrIDRef="(\d+)"', xml[target.start:target.end])
                        if chars is not None and pm:  # '(인)'이 오른쪽 정렬이던 칸 — 이름까지 넣으면 가운데로(11/04 사용자 요청)
                            para_override[target.start] = chars.centered(pm.group(1))
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
                if chars is not None:
                    _align_numbers(xml, [e for e in edits if tbl[0] <= e[0].start < tbl[1]], chars, para_override)
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
                if cell.start in para_override:
                    seg = re.sub(r'(<(?:[\w.-]+:)?p\b[^>]*\bparaPrIDRef=")\d+(")', rf"\g<1>{para_override[cell.start]}\2", seg, count=1)
                elif chars is not None:  # 양쪽·배분 정렬 칸은 왼쪽 정렬 사본으로 — 넘친 줄의 글자 사이가 벌어지지 않게
                    seg = re.sub(r'(<(?:[\w.-]+:)?p\b[^>]*\bparaPrIDRef=")(\d+)(")', lambda m: m.group(1) + chars.left(m.group(2)) + m.group(3),
                                 seg, count=1)
                char_id = None
                if chars is not None and len(lines) == 1 and not keep:
                    char_id, note = _fit_char(seg, lines[0], chars)
                    if note:
                        rep.filled.append(f"  ↳ '{lines[0][:20]}' {note}")
                elif chars is not None:  # 여러 줄(연혁 등)도 11pt, 숫자·영문은 한글 글꼴로
                    run = re.search(r'<(?:[\w.-]+:)?run\b[^>]*charPrIDRef="(\d+)"', seg)
                    char_id = _fill_variant(chars, run.group(1) if run else "0")
                xml = xml[:cell.start] + _set_cell(seg, lines, keep, char_id) + xml[cell.end:]
            xml = _fill_bond(xml, basis, rep)
            xml = _fill_label_paragraphs(xml, vault, rep, chars)
            xml = _apply_marks(xml, chars)
            if chars is not None:
                xml = _keep_table_heights(before_fill, xml, chars, rep)
            data = xml.encode("utf-8")
        out_entries[name] = data
    if chars is not None and chars.changed:  # 칸에 맞춰 줄인 글자 모양·가운데 정렬 문단 모양을 머리 정보에 더함
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
