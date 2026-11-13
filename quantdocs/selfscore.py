"""정량평가 자기평점 계산 — 제안요청서 본문의 배점기준대로(11/05 사용자 요청: "제안요청서를 기준으로 항상 계산되게").

제안요청서(HWPX)의 '정량적평가 : N점' 절에서 'N) 항목 : N점' 머리 아래 표·주석을 공고마다 읽어 기준을 만들고
(`read_criteria`), 회사 자료 묶음으로 점수를 매긴다(`score`).

    사업수행실적  금액 구간 → 점수 (최근 N년·준공 완료·유사사업·단일계약 하한·누계/단일최대)
    기술인력      등급별 1인당 점수 × 인원(N개월 이상 재직), 최고 배점까지   ← 인력목록 시트
    보유기술      건수 구간 → 점수                                            ← 특허목록 시트
    경영상태      신용평가등급 목록 → 점수                                    ← 회사정보 '신용평가등급'·'신용평가 종류'
    신인도        부정당업자 제재처분 해당없음/해당 → 점수                    ← 회사정보 '부정당업자 제재처분'

서식의 배점과 본문 배점이 다르면 본문을 따른다(필암서원 서식 24는 실적을 누계 4 + 단일최대 2로 나눴지만 본문은 누계 6).
본문 표와 주석이 서로 다르면 표대로 계산하고 알림(11/05 사용자 결정: 배점표 기준 + 알림).
자료가 없는 항목은 점수를 비워 두고 '자료 없음'으로 알림 — 짐작해서 채우지 않음.
"""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .fill import _cells, _spans, _sq, _text
from .forms_cut import _top_paragraphs

KINDS = [("track", re.compile(r"실적")), ("staff", re.compile(r"인력|기술자|기술\s*능력")),
         ("patent", re.compile(r"특허|보유\s*기술|전문\s*기술|기술\s*보유")), ("credit", re.compile(r"경영|신용|재무\s*상태")),
         ("sanction", re.compile(r"신인도|제재|성실성|사회적\s*책임"))]  # '사회적 책임 및 성실성'(11/12 당진 서식 4호)
_START = re.compile(r"정량\S{0,4}\s*평가(?:\s*지표)?\S{0,4}\s*[:：]?\s*\(?\s*\d+(?:\.\d+)?\s*점")
_STOP = re.compile(r"^\s*주\s*\d+\s*\)|정성\S{0,4}\s*평가(?:\s*지표)?\S{0,4}\s*[:：]?\s*\(?\s*\d+(?:\.\d+)?\s*점")
_MARK = re.compile(r"^\s*(\d+\)|\(\d+\)|[가-하]\.|[가-하]\)|\d+\.)")
_HEAD = re.compile(r"^\s*(?:\d+\)|\(\d+\)|[가-하]\.|[가-하]\))\s*([^:：\n]{2,30}?)\s*[:：]\s*(\d+(?:\.\d+)?)\s*점")
_MULT = {"억": 1e8, "천만": 1e7, "백만": 1e6, "만": 1e4}
_QTY = r"([\d]+(?:[.,]\d+)*)\s*(억|천만|백만|만)?\s*(원|건)?"
_NUM = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*점?\s*$")
# 1인당 점수 — '0.5점'·'각 0.5점'·'각0.5점'·'1인당 0.5점'·'0.5점/인'(11/11 사용자 제보: '각 0.5점'을 못 읽어 기술인력 칸이 안 보였음)
_EACH = re.compile(r"^\s*(?:각|1\s*인\s*당|인\s*당)?\s*(\d+(?:\.\d+)?)\s*점?\s*(?:/\s*인)?\s*$")
GRADES = ["기술사", "특급", "고급", "중급", "초급"]
# 신용등급 차례(앞이 좋음) — 회사채·기업신용평가등급 / 기업어음
_BOND = ["AAA", "AA+", "AA0", "AA-", "A+", "A0", "A-", "BBB+", "BBB0", "BBB-", "BB+", "BB0", "BB-",
         "B+", "B0", "B-", "CCC+", "CCC0", "CCC-", "CC", "C", "D"]
_PAPER = ["A1", "A2+", "A20", "A2-", "A3+", "A30", "A3-", "B+", "B0", "B-", "C", "D"]


@dataclass
class Band:
    lo: float | None  # 이상(포함)
    hi: float | None  # 미만(제외)
    score: float
    text: str


@dataclass
class Criterion:
    name: str
    points: float
    kind: str  # track / staff / patent / credit / sanction / ""
    note: str = ""  # 머리 아래 주석 글(유사사업·기간·인정 기준)
    bands: list[Band] = field(default_factory=list)
    unit: str = ""  # 원 / 건
    per_grade: dict[str, float] = field(default_factory=dict)  # 기술인력 등급별 1인당 점수
    credit_rows: list[tuple[dict[str, str], float]] = field(default_factory=list)  # ({종류: 규칙 글}, 점수)
    sanction: dict[str, float] = field(default_factory=dict)  # {"해당없음": 2, "해당": 0}


@dataclass
class ItemScore:
    name: str
    points: float
    kind: str
    score: float | None
    basis: str = ""
    notes: list[str] = field(default_factory=list)


# ── 읽기 ────────────────────────────────────────────────────────────
def _qty(num: str, unit: str | None) -> float:
    return float(num.replace(",", "")) * _MULT.get(unit or "", 1)


def parse_range(text: str) -> tuple[float | None, float | None, str] | None:
    """'4억 이상 ~ 5.5억 미만' → (4e8, 5.5e8, '원'), '4건 이상' → (4, None, '건'), '0건' → (0, 1, '건'). 못 읽으면 None."""
    t = re.sub(r"\s+", " ", text or "").strip()
    lo = hi = None
    unit = ""
    m = re.search(_QTY + r"\s*(이상|초과)", t)
    if m:
        lo = _qty(m.group(1), m.group(2)) + (1e-9 if m.group(4) == "초과" else 0)
        unit = "원" if m.group(2) or m.group(3) == "원" else (m.group(3) or "")
    m2 = re.search(_QTY + r"\s*(미만|이하)", t)
    if m2:
        hi = _qty(m2.group(1), m2.group(2)) + (1e-9 if m2.group(4) == "이하" else 0)
        unit = unit or ("원" if m2.group(2) or m2.group(3) == "원" else (m2.group(3) or ""))
    if lo is None and hi is None:
        m4 = re.fullmatch(r"(\d+)\s*건\s*[~∼\-]\s*(\d+)\s*건", t)  # '3건 ~ 2건'(11/10 경남교육청 연관기술)
        if m4:
            a, b = sorted((float(m4.group(1)), float(m4.group(2))))
            return a, b + 1, "건"
        m3 = re.fullmatch(r"(\d+)\s*건", t)
        if not m3:
            return None
        n = float(m3.group(1))
        return n, n + 1, "건"
    return lo, hi, unit


def _rows(cells) -> dict[int, dict[int, str]]:
    out: dict[int, dict[int, str]] = {}
    for c in cells:
        out.setdefault(c.row, {})[c.col] = re.sub(r"\s+", " ", c.text).strip()
    return out


def _each(text: str) -> float | None:
    m = _EACH.match(text or "")
    return float(m.group(1)) if m else None


def _num(text: str) -> float | None:
    m = _NUM.match(text or "")
    return float(m.group(1)) if m else None


def _read_table(c: Criterion, xml: str, tbl: tuple[int, int]) -> None:
    rows = _rows(_cells(xml, tbl))
    flat = " ".join(t for r in rows.values() for t in r.values())
    # 기술자 등급별 점수: '특급기술자' 칸 아래 '1점'
    for r, cols in rows.items():
        for col, text in cols.items():
            g = next((g for g in GRADES if g in _sq(text)), None)
            below = _each(rows.get(r + 1, {}).get(col, ""))
            if g and below is not None and "기술" in _sq(text) and len(_sq(text)) <= 8:
                c.per_grade.setdefault(g, below)
    # 등급별 계수가 같은 줄 맨 오른쪽('특 급 기술자 | 자격 기준 | 학력 기준 | 10.0', 11/10 경남교육청)
    # 등급 칸이 줄 맨 앞이 아니어도(11/12 당진 서식 4호: 세로로 합친 '기술인력 | 인력 보유현황' 칸이 첫 줄(특급)에만 걸려
    # 특급 줄은 '기술인력 | 인력보유현황 | 특급기술자 | 1.0점/인'이라 특급만 못 읽어 '등급 없음') — 등급 칸 오른쪽에서 처음 읽히는 점수
    for r, cols in rows.items():
        order = sorted(cols)
        for i, col in enumerate(order):
            t = _sq(cols[col])
            g = next((g for g in GRADES if t.startswith(g)), None)
            if not g or not re.fullmatch(rf"{g}(?:기술자)?", t):
                continue
            per = next((v for v in (_each(cols[k]) for k in order[i + 1:]) if v is not None), None)
            if per is not None:
                c.per_grade.setdefault(g, per)
            break
    # 기술인력 점수 구간('점수 50점 이상' | 5.0) — 등급별 계수 합으로 구간을 고름
    if c.kind == "staff":
        for r, cols in sorted(rows.items()):
            for col, text in sorted(cols.items()):
                lo = re.search(r"(\d+(?:\.\d+)?)\s*점\s*(이상|초과)", text)
                hi = re.search(r"(\d+(?:\.\d+)?)\s*점\s*(미만|이하)", text)
                if not (lo or hi) or len(_sq(text)) > 20:
                    continue
                score = next((_num(t) for cc, t in sorted(cols.items()) if cc > col and _num(t) is not None), None)
                if score is not None:
                    c.bands.append(Band(float(lo.group(1)) if lo else None, float(hi.group(1)) + (1e-9 if hi.group(2) == "이하" else 0) if hi else None, score, text))
                    c.unit = "점"
    # 제재처분: '해당없음' · '해당' 칸 아래 점수
    for r, cols in rows.items():
        for col, text in cols.items():
            key = _sq(text)
            if key in ("해당없음", "해당") and _num(rows.get(r + 1, {}).get(col, "")) is not None:
                c.sanction.setdefault(key, _num(rows[r + 1][col]))
    # 신용등급: 종류 머리(회사채·기업어음·기업신용평가등급) 아래 줄마다 등급 글 + 맨 오른쪽 점수
    kinds = {col: _sq(text) for r, cols in rows.items() for col, text in cols.items()
             if re.fullmatch(r"회사채|기업어음|기업신용평가등급|신용평가등급", _sq(text))}
    if kinds and "등급" in flat:
        for r, cols in sorted(rows.items()):
            score = _num(cols[max(cols)]) if cols else None
            rules = {kinds[col]: text for col, text in cols.items() if col in kinds and re.search(r"[A-D]", text)}
            if score is not None and rules:
                c.credit_rows.append((rules, score))
        return
    # 금액·건수 구간: 구간 칸 아래(같은 열) 또는 오른쪽(같은 줄) 숫자가 점수
    for r, cols in sorted(rows.items()):
        for col, text in sorted(cols.items()):
            rng = parse_range(text)
            if len(_sq(text)) > 20:  # '…누계실적 금액(10억 이상 실적)' 같은 설명 칸은 구간이 아님(단일계약 하한)
                continue
            if not rng or (rng[2] not in ("원", "건") and not re.search(r"억|만|원|건", text)):
                continue
            score = _num(rows.get(r + 1, {}).get(col, ""))
            if score is None:
                score = next((_num(t) for cc, t in sorted(cols.items()) if cc > col and _num(t) is not None), None)
            if score is not None:
                c.bands.append(Band(rng[0], rng[1], score, text))
                c.unit = c.unit or rng[2]


def _sections(path: Path) -> list[str]:
    z = zipfile.ZipFile(path)
    names = sorted((n for n in z.namelist() if re.fullmatch(r"Contents/section\d+\.xml", n)),
                   key=lambda n: int(re.search(r"\d+", n.rsplit("/", 1)[1]).group()))
    return [z.read(n).decode("utf-8") for n in names]


def read_criteria(paths: list[Path]) -> list[Criterion]:
    """제안요청서 HWPX들에서 정량평가 배점기준. 첫 번째로 찾은 문서만 씀(제안요청서·공고문에 같은 표가 반복될 수 있어서)."""
    for path in paths:
        try:
            xmls = _sections(Path(path))
        except (zipfile.BadZipFile, KeyError, OSError):
            continue
        crit = _read_doc(xmls)
        if crit:
            return crit
    return []


def form_staff_grades(paths: list[Path]) -> tuple[dict[str, float], float]:
    """자기평가표 서식의 기술인력 (등급별 1인당 점수, 배점) — 첫 번째로 찾은 것. 화면 기술인력 목록이 서식 기준을 먼저 쓰게
    (11/12 사용자: '서식에서는 잘 나뉘어져 있잖아' — 서식 '특급기술자 | 1.0점/인', 채점도 서식 기준)."""
    for path in paths:
        try:
            xmls = _sections(Path(path))
        except (zipfile.BadZipFile, KeyError, OSError):
            continue
        for xml in xmls:
            for tbl in _spans(xml, "tbl"):
                for row in read_form(_cells(xml, tbl)):
                    if row.crit is not None and row.crit.kind == "staff" and row.crit.per_grade:
                        return dict(row.crit.per_grade), float(row.points or 0)
    return {}, 0.0


def _mark_style(text: str) -> str:
    """항목 머리표 종류 — '2)'·'가)'·'(1)'·'가.'·'1.' → 'n)'·'k)'·'(n)'·'k.'·'n.'."""
    m = _MARK.match(text or "")
    if not m:
        return ""
    return re.sub(r"[가-하]", "k", re.sub(r"\d+", "n", m.group(1)))


def _read_doc(xmls: list[str]) -> list[Criterion]:
    items: list[tuple[str, str, list[tuple[str, tuple[int, int]]]]] = []  # (글, 표 바깥 글, [(xml, 표)])
    for xml in xmls:
        for p in _top_paragraphs(xml):
            seg = xml[p.start:p.end]
            tbls = [(xml, (p.start + a, p.start + b)) for a, b in _spans(seg, "tbl")]
            outside = re.sub(r"<(?:[\w.-]+:)?tbl\b.*?</(?:[\w.-]+:)?tbl>", "", seg, flags=re.S)
            items.append((_text(outside).strip(), outside, tbls))
    # 시작 = '2) 정량적 평가 지표: 20점' 같은 항목 머리 먼저, 없으면 '정량적 평가 20점'이 든 첫 문장
    start = next((i for i, (t, _, _) in enumerate(items) if _HEAD.match(t) and _START.search(t)), None)
    if start is None:
        start = next((i for i, (t, _, _) in enumerate(items) if _START.search(t)), None)
    if start is None:
        return _read_doc_nopoints(items)
    start_mark = _mark_style(items[start][0])
    out: list[Criterion] = []
    cur: Criterion | None = None
    summary: dict[str, tuple[str, float]] = {}
    summary_tbl: dict[str, tuple[str, tuple[int, int]]] = {}  # 요약표 항목이 든 표 — 기준(등급별 점수)이 같은 표에 있으면 읽음(11/11)
    for i in range(start, len(items)):
        text, _, tbls = items[i]
        if i > start and _STOP.search(text) and not _HEAD.match(text):
            break
        head = _HEAD.match(text)
        if head and i > start and start_mark and _mark_style(text) == start_mark:
            break  # '2) 정량적 평가 지표: 20점' 다음의 '3) 입찰가격 평가: 10점' — 같은 차례 머리면 정량평가 절 끝(11/05 국민안전체험관)
        if head and i > start:
            name = head.group(1).strip()
            kind = next((k for k, rx in KINDS if rx.search(name)), "")
            cur = Criterion(name, float(head.group(2)), kind)
            out.append(cur)
            rest = text[head.end():].strip()
            if rest:
                cur.note += rest + "\n"
        elif cur is not None and text:
            cur.note += text + "\n"
        for xml, tbl in tbls:
            if cur is None:  # 머리 앞 요약표(평가항목·배점) — 머리가 없는 공고에 씀
                rows = _rows(_cells(xml, tbl))
                for cols in rows.values():
                    vals = list(cols.values())
                    pts = next((_num(v) for v in reversed(vals) if _num(v) is not None), None)
                    name = next((v for v in vals if v and _num(v) is None and len(_sq(v)) <= 12
                                 and any(rx.search(v) for _, rx in KINDS)), None)
                    if name and pts is not None:
                        k = next(k for k, rx in KINDS if rx.search(name))
                        summary.setdefault(k, (name, pts))
                        summary_tbl.setdefault(k, (xml, tbl))
                continue
            cur.note += re.sub(r"\s+", " ", _text(xml[tbl[0]:tbl[1]])) + "\n"
            _read_table(cur, xml, tbl)
    if not out:  # 항목 머리가 없으면 요약표의 항목·배점 — 기술인력은 같은 표 안의 등급별 점수도('전문인력 | 3.0 | 특급기술자 각 0.5점 …', 11/11)
        out = [Criterion(name, pts, k) for k, (name, pts) in summary.items()]
        for c in out:
            if c.kind == "staff" and c.kind in summary_tbl:
                xml, tbl = summary_tbl[c.kind]
                c.note += re.sub(r"\s+", " ", _text(xml[tbl[0]:tbl[1]])) + "\n"
                _read_table(c, xml, tbl)
    return out


_HEAD_NOPTS = re.compile(r"^\s*(?:\d+\)|\(\d+\)|[가-하]\.|[가-하]\))\s*([^:：\n]{2,20}?)\s*$")


def _table_points(xml: str, tbl: tuple[int, int]) -> float:
    """'평가항목 | 배점 | 평점' 표 — 머리 '배점' 칸 아래 첫 숫자."""
    rows = _rows(_cells(xml, tbl))
    for r, cols in sorted(rows.items()):
        col = next((cc for cc, t in cols.items() if _sq(t) == "배점"), None)
        if col is None:
            continue
        for rr in sorted(k for k in rows if k > r):
            v = _num(rows[rr].get(col, ""))
            if v is not None:
                return v
    return 0.0


def _read_doc_nopoints(items: list) -> list[Criterion]:
    """배점이 항목 머리가 아니라 아래 표 '배점' 칸에 있는 모양(11/10 경남교육청 학생안전체험원):
    '1) 사업 수행 능력 평가' 아래 '가) 사업 수행 실적' + 표(평가항목 | 배점 | 평점) … '2) 기술제안서 평가'에서 끝."""
    start = next((i for i, (t, _, _) in enumerate(items)
                  if re.fullmatch(r"\s*(?:\d+\)|\(\d+\)|\d+\.|[가-하]\.)?\s*(?:사업\s*수행\s*능력|정량\S{0,3})\s*평가(?:\s*\(정량\))?\s*", t)), None)
    if start is None:
        return []
    mark = _mark_style(items[start][0])
    out: list[Criterion] = []
    cur: Criterion | None = None
    for i in range(start + 1, len(items)):
        text, _, tbls = items[i]
        sib = _HEAD_NOPTS.match(text) if text and mark and _mark_style(text) == mark else None
        if text and mark and _mark_style(text) == mark and not (
                sib and (re.search(r"기술\s*능력", sib.group(1)) or any(rx.search(sib.group(1)) for _, rx in KINDS))):
            break  # 같은 차례 머리 — 단 '나. 기술능력 평가'처럼 정량 항목이 같은 차례로 이어지면 계속(11/11)
        if re.search(r"정성\S{0,3}\s*평가|기술\s*제안서\s*평가", text) and _HEAD_NOPTS.match(text):
            break
        head = _HEAD_NOPTS.match(text)
        kind = next((k for k, rx in KINDS if head and rx.search(head.group(1))), "") if head else ""
        if head and re.fullmatch(r"기술\s*능력(?:\s*평가)?", head.group(1).strip()):
            kind = "staff"  # '나) 기술 능력' = 참여기술자 점수(11/10 경남교육청)
        if head and kind:
            cur = Criterion(head.group(1).strip(), 0.0, kind)
            out.append(cur)
        elif cur is not None and text:
            cur.note += text + "\n"
        for xml, tbl in tbls:
            if cur is None:
                continue
            cur.note += re.sub(r"\s+", " ", _text(xml[tbl[0]:tbl[1]])) + "\n"
            _read_table(cur, xml, tbl)
            if not cur.points:
                cur.points = _table_points(xml, tbl)
    for c in out:  # 표에 '배점' 칸이 없으면(경영상태 표) 가장 높은 점수를 배점으로
        if not c.points:
            c.points = max([b.score for b in c.bands] + [sc for _, sc in c.credit_rows] + list(c.sanction.values()) + [0.0])
    return [c for c in out if c.points]


# ── 점수 ────────────────────────────────────────────────────────────
def _band(c: Criterion, value: float) -> Band | None:
    for b in c.bands:
        if (b.lo is None or value >= b.lo) and (b.hi is None or value < b.hi):
            return b
    return None


def _won(v: float) -> str:
    return f"{v / 1e8:.2f}억 원" if v >= 1e8 else f"{v:,.0f}원"


def _track(c: Criterion, vault, ref: date, mode: str = "") -> ItemScore:
    from . import notice as N

    res = ItemScore(c.name, c.points, c.kind, None)
    if not c.bands:
        res.notes.append("배점기준 표(금액 구간)를 읽지 못함 — 제안요청서 확인")
        return res
    if not vault.tracks:
        res.notes.append("자료 없음 — 회사자료 '실적목록' 시트")
        return res
    note = c.note
    years = re.search(r"최근\s*(\d+)\s*(?:개\s*)?년", note)
    years = int(years.group(1)) if years else None
    single = re.search(r"단일\s*(?:계약)?\s*(?:1\s*건)?.{0,20}?" + _QTY + r"\s*이상", note) \
        or re.search(_QTY + r"\s*이상\s*실적", note)  # '(부가세 포함 10억 이상 실적)'(11/05 국민안전체험관)
    min_single = _qty(single.group(1), single.group(2)) if single else None
    max_mode = (mode == "max") if mode else (bool(re.search(r"단일\s*(?:최대|건)\s*실적.{0,10}평가", note))
                                             and not re.search(r"누계|합계", note))
    no_design = bool(re.search(r"설계\s*용역.{0,30}인정\s*(?:하지\s*않|불가|제외)", note))
    vat_in = bool(re.search(r"부가세\s*포함|부가가치세\s*포함", note))
    similar = " ".join(re.findall(r"[^.\n]*유사\s*사업[^.\n]*", note)) or note
    fields = set().union(*[fs for rx, fs in N._FORM_FIELDS if rx.search(similar)]) if similar else set()
    since = N._since(ref, years) if years else None
    used, skipped = [], {"준공 전·준공일 없음": 0, "기간 밖": 0, "단일계약 하한 미만": 0, "설계 용역": 0, "분야 다름": 0, "금액 없음": 0}
    for t in vault.tracks:
        done = N._as_date(t.get("준공일"))
        if done is None or done > ref:
            skipped["준공 전·준공일 없음"] += 1
            continue
        if since and done < since:
            skipped["기간 밖"] += 1
            continue
        name = str(t.get("사업명") or "")
        if no_design and re.search(r"설계", name) and re.search(r"용역", name) and not re.search(r"제작|설치|시공|구축|조성", name):
            skipped["설계 용역"] += 1
            continue
        field_name = str(t.get("분야") or "").strip() or N.track_field(name)
        if fields and field_name not in fields:
            skipped["분야 다름"] += 1
            continue
        try:
            amount = float(t.get("계약금액(원)"))
        except (TypeError, ValueError):
            skipped["금액 없음"] += 1
            continue
        if vat_in and str(t.get("부가세") or "") == "별도":
            amount *= 1.1
        share = t.get("지분율(%)")
        try:
            amount *= float(share) / 100 if share not in (None, "") else 1
        except (TypeError, ValueError):
            pass
        if min_single and amount < min_single:
            skipped["단일계약 하한 미만"] += 1
            continue
        used.append((name, amount))
    total = max((a for _, a in used), default=0.0) if max_mode else sum(a for _, a in used)
    band = _band(c, total)
    res.score = band.score if band else None
    how = "단일 최대" if max_mode else "누계"
    res.basis = (f"{how} {_won(total)} ({len(used)}건" + (f", 최근 {years}년" if years else "")
                 + (f", 단일계약 {_won(min_single)} 이상" if min_single else "") + f") → '{band.text if band else '구간 없음'}'")
    res.notes += [f"뺀 실적 {k} {n}건" for k, n in skipped.items() if n]
    if fields:
        res.notes.append("유사사업 판단은 사업명 분야 분류(" + "·".join(sorted(fields)) + ") — 실적증명서와 대조")
    if any(str(t.get("부가세") or "") in ("", "모름") for t in vault.tracks) and vat_in:
        res.notes.append("부가세 포함 여부를 모르는 실적이 있음 — 그대로 셈")
    full = re.search(r"(?:사업비|추정\s*금액|추정\s*가격|예산)\s*\(?\s*" + _QTY + r"\s*\)?\s*이상인?\s*경우\s*만점", note)
    if full:
        alt = _qty(full.group(1), full.group(2))
        top = max(c.bands, key=lambda b: b.score)
        if alt <= total and (band is None or band.score < top.score):
            # 계산 규칙끼리 다르면 지일에게 유리한 쪽(11/05 사용자 결정 — 예전엔 배점표 기준 + 알림)
            res.notes.append(f"⚠ 주석은 '{_won(alt)} 이상이면 만점'인데 배점표는 {top.text}만 만점 — 유리한 쪽(주석)으로 {top.score:g}점"
                             + (f"(배점표대로면 {band.score:g}점)" if band else "") + ", 발주처 확인")
            res.score = top.score
    return res


def staff_status(c: Criterion | None, vault, ref: date) -> list[dict]:
    """인력목록 사람마다 이 공고 기술인력 점수에 들어가는지 — 화면에서 고르기(11/05 사용자 요청)·점수 계산이 같이 씀.
    [{이름, 등급, 입사일, counted, why}] — 한 사람이 여러 줄이면 가장 높은 등급 하나(표에 없는 등급은 뺌)."""
    from . import notice as N

    per_grade = (c.per_grade if c else {}) or {g: 1.0 for g in GRADES}
    months = re.search(r"(\d+)\s*개월\s*이상", c.note if c else "")
    months = int(months.group(1)) if months else 0
    cut = None  # 이 날 이전에 입사해야 'N개월 이상 재직'
    if months:
        y, m = divmod(ref.month - 1 - months, 12)
        try:
            cut = date(ref.year + y, m + 1, min(ref.day, 28))
        except ValueError:
            cut = None
    people: dict[str, dict] = {}
    for r in getattr(vault, "staff", []) or []:
        name = str(r.get("이름") or "").strip()
        if not name:
            continue
        raw = str(r.get("등급") or "")
        grade = next((g for g in GRADES if g in _sq(raw)), None)
        if grade == "기술사" and "기술사" not in per_grade:
            grade = "특급"  # 기술사는 특급 이상 — 표에 기술사 점수가 없으면 특급 점수
        joined = N._as_date(r.get("입사일"))
        why = ""
        if str(r.get("재직") or "재직").strip() == "퇴사":
            why = "퇴사"
        elif cut and (joined is None or joined > cut):
            why = f"재직 {months}개월 미만·입사일 없음"
        elif grade not in per_grade:
            why = "등급 없음"
        cur = {"이름": name, "등급": grade or raw, "입사일": f"{joined:%Y-%m-%d}" if joined else str(r.get("입사일") or ""),
               "counted": not why, "why": why, "pts": per_grade.get(grade, 0) if not why else 0}
        old = people.get(name)
        if old is None or (cur["counted"], cur["pts"]) > (old["counted"], old["pts"]):
            people[name] = cur
    return list(people.values())


def _staff(c: Criterion, vault, ref: date) -> ItemScore:
    res = ItemScore(c.name, c.points, c.kind, None)
    if not c.per_grade:
        res.notes.append("배점기준 표(등급별 점수)를 읽지 못함 — 제안요청서 확인")
        return res
    staff = getattr(vault, "staff", []) or []
    if not staff and not getattr(vault, "staff_pick_from_screen", False):  # 화면에서 0명을 고른 건 0명으로 채점(11/11 사용자 제보)
        res.notes.append("자료 없음 — 회사자료 '인력목록' 시트(이름·입사일·재직·등급) 또는 jiil-quant-docs 폴더의 기술인력 엑셀")
        return res
    people = staff_status(c, vault, ref)
    best = {p["이름"]: (p["등급"], p["pts"]) for p in people if p["counted"]}
    skipped: dict[str, int] = {}
    for p in people:
        if not p["counted"]:
            skipped[p["why"]] = skipped.get(p["why"], 0) + 1
    raw = sum(p for _, p in best.values())
    res.score = min(raw, c.points)
    if c.unit == "점" and c.bands:  # 등급별 계수 합 → 점수 구간(11/10 경남교육청 '점수 50점 이상 5.0')
        band = _band(c, raw)
        res.score = band.score if band else None
        counts0 = {g: sum(1 for gg, _ in best.values() if gg == g) for g in GRADES}
        res.basis = (", ".join(f"{g} {n}명×{c.per_grade[g]:g}" for g, n in counts0.items() if n) or "고른 인원 0명") \
            + f" = {raw:g}점 → '{band.text if band else '구간 없음'}'"
        res.notes += [f"뺀 인원 {k} {n}명" for k, n in {p['why']: sum(1 for q in people if not q['counted'] and q['why'] == p['why']) for p in people if not p['counted']}.items()]
        field = re.search(r"대상\s*기술\s*인력[^\n]*", c.note)
        if field:
            res.notes.append("공고가 인정하는 분야·참여 인력인지 확인(" + re.sub(r"\s+", " ", field.group(0))[:60] + ")")
        return res
    # '평점 = 4.2 + ∑(점수×인원)'(본문)·'미보유 시 4.2점'(서식)처럼 기본 점수가 있으면 — 식끼리 다르면 유리한 쪽(11/05 사용자 결정)
    base = re.search(r"평점\s*=\s*([\d.]+)\s*\+", c.note)
    floor = re.search(r"(?:미보유\s*시|최저)\s*([\d.]+)\s*점", c.note)
    if base and min(raw + float(base.group(1)), c.points) > res.score:
        res.score = min(raw + float(base.group(1)), c.points)
        res.notes.append(f"기본 {base.group(1)}점 + 인원 점수(본문 식 — 유리한 쪽)")
    if floor and float(floor.group(1)) > res.score:
        res.score = min(float(floor.group(1)), c.points)
        res.notes.append(f"최저 {floor.group(1)}점 적용")
    counts = {g: sum(1 for gg, _ in best.values() if gg == g) for g in GRADES}
    res.basis = (", ".join(f"{g} {n}명×{c.per_grade[g]:g}" for g, n in counts.items() if n) or "고른 인원 0명") + f" = {raw:g}점" + (
        f" → 최고 {c.points:g}점" if raw > c.points else "")
    res.notes += [f"뺀 인원 {k} {n}명" for k, n in skipped.items() if n]
    if re.search(r"분야\s*인력", c.note):
        res.notes.append("공고가 인정하는 분야 인력인지 확인(" + re.sub(r"\s+", " ", re.search(r"[^.\n]*분야\s*인력[^.\n]*", c.note).group(0))[:60] + ")")
    return res


# 공고가 인정하는 특허 분야(주석 '※ 영상 제작 및 실감 콘텐츠 관련 보유 특허') — 낱말마다 같은 뜻 낱말도 함께(11/06 사용자 요청)
_PATENT_SYN = {
    "영상": ["영상", "동영상", "비디오", "미디어", "디스플레이", "프로젝션", "프로젝터", "홀로그램", "스크린", "촬영"],
    "실감": ["실감", "가상", "증강", "혼합현실", "몰입", "인터랙티브", "체감", "VR", "AR", "MR", "XR", "메타버스"],
    "콘텐츠": ["콘텐츠", "컨텐츠"],
    "미디어": ["미디어", "영상", "디스플레이"],
    "디지털": ["디지털", "미디어", "영상", "가상", "증강"],
    "전시": ["전시", "쇼케이스", "진열"],
    "체험": ["체험"],
}
_PATENT_STOP = re.compile(r"관련|보유|특허|실용\s*신안|및|등|인정|기술|분야|해당|건수|제작|개발|^\W+$")


def patent_scope(c: Criterion | None) -> tuple[str, list[str]]:
    """(주석의 인정 분야 문장, 맞춰 볼 낱말들). 분야 문장이 없으면 ('', []) — 모든 특허를 인정으로 봄."""
    if c is None:
        return "", []
    m = re.search(r"※\s*([^.\n]*(?:특허|실용\s*신안)[^.\n]*)", c.note)
    if not m:
        return "", []
    scope = re.sub(r"\s+", " ", m.group(1)).strip()
    words: list[str] = []
    for w in re.findall(r"[가-힣A-Za-z]{2,}", scope):
        if _PATENT_STOP.search(w):
            continue
        for k, syn in _PATENT_SYN.items():
            if k in w:
                words += syn
        words.append(w)
    return scope, list(dict.fromkeys(words))


def patent_key(p: dict) -> str:
    return str(p.get("파일") or p.get("등록번호") or p.get("명칭") or "").strip()


def patent_status(c: Criterion | None, vault) -> list[dict]:
    """특허마다 이 공고 분야에 맞는지 — 화면 고르기(처음 체크 상태)·기본 점수 계산이 같이 씀. [{key, 명칭, counted, why}]."""
    scope, words = patent_scope(c)
    out = []
    for p in getattr(vault, "patents", None) or []:
        key = patent_key(p)
        if not key:
            continue
        name = str(p.get("명칭") or Path(key).stem)
        hay = _sq(name + " " + Path(key).stem).upper()
        hit = next((w for w in words if _sq(w).upper() in hay), None)
        ok = not words or hit is not None
        why = "" if not words else (f"'{hit}' 맞음" if hit else "공고 인정 분야와 이름이 안 맞음")
        out.append({"key": key, "명칭": name, "counted": ok, "why": why})
    return out


def _patent(c: Criterion, vault) -> ItemScore:
    res = ItemScore(c.name, c.points, c.kind, None)
    if not c.bands:
        res.notes.append("배점기준 표(건수 구간)를 읽지 못함 — 제안요청서 확인")
        return res
    pats = getattr(vault, "patents", None)
    picked = getattr(vault, "patent_pick", None)  # 화면에서 고른 특허(또는 공고 분야로 고른 기본) — 없으면 전부
    if not pats:  # 시트가 비어 있으면 아직 안 적은 것으로 봄(0건이면 '0건'이라고 직접 기입)
        res.notes.append("자료 없음 — 회사자료 '특허목록' 시트")
        return res
    if picked is not None:
        pats = [p for p in pats if patent_key(p) in picked]
    n = sum(1 for p in pats if patent_key(p))
    band = _band(c, n)
    res.score = band.score if band else None
    res.basis = f"{n}건 → '{band.text if band else '구간 없음'}'"
    if getattr(vault, "patent_src", "") == "파일":
        res.basis += " (특허증 파일 수 — 파일 하나를 1건으로 셈)"
        res.notes.append("특허증 파일 하나에 여러 건이 들었거나 만료·말소된 특허가 있으면 고칠 것")
    scope, _ = patent_scope(c)
    if pats:
        res.basis += " — " + ", ".join(str(p.get("명칭") or Path(patent_key(p)).stem) for p in pats[:8])
    if scope:
        res.notes.append(f"공고 인정 분야: {scope[:50]} — {'화면에서 고른' if getattr(vault, 'patent_pick_from_screen', False) else '이름으로 고른'} 특허만 셈, 확인할 것")
    return res


def _grade_rank(grade: str) -> tuple[list[str], int] | None:
    g = grade.upper().replace(" ", "").replace("O", "0")
    for scale in (_BOND, _PAPER):
        cand = g if g in scale else (g + "0" if g + "0" in scale else None)
        if cand:
            return scale, scale.index(cand)
    return None


def _rule_hit(rule: str, grade: str) -> bool:
    got = _grade_rank(grade)
    if not got:
        return False
    scale, rank = got
    for part in re.split(r"[,，/]", rule):
        part = part.strip().upper().replace("O", "0")
        rng = re.fullmatch(r"([A-D][A-D0-9+\-]*)\s*[~∼～\-]\s*([A-D][A-D0-9+\-]*)(?:\s*등급)?", part)
        if rng:  # 'BB- ~ BBB-' — 두 등급 사이(양 끝 포함, 11/13 서식 14호)
            a, b = _grade_rank(rng.group(1)), _grade_rank(rng.group(2))
            if a and b and a[0] is scale and b[0] is scale and min(a[1], b[1]) <= rank <= max(a[1], b[1]):
                return True
            continue
        m = re.match(r"([A-D][A-D0-9+\-]*)\s*(이상|이하)?", part)
        if not m:
            continue
        ref = _grade_rank(m.group(1))
        if not ref or ref[0] is not scale:
            continue
        if (m.group(2) == "이상" and rank <= ref[1]) or (m.group(2) == "이하" and rank >= ref[1]) or (not m.group(2) and rank == ref[1]):
            return True
    return False


def _credit(c: Criterion, vault) -> ItemScore:
    res = ItemScore(c.name, c.points, c.kind, None)
    if not c.credit_rows:
        res.notes.append("배점기준 표(신용등급)를 읽지 못함 — 제안요청서 확인")
        return res
    grade = str(vault.info.get("신용평가등급") or "").strip()
    if not grade:
        res.notes.append("자료 없음 — 회사정보 '신용평가등급'(예: BB+)·'신용평가 종류'(기업신용평가등급·회사채·기업어음)")
        return res
    kind = _sq(str(vault.info.get("신용평가 종류") or ""))
    kinds = list(c.credit_rows[0][0])
    col = kind if kind in kinds else next((k for k in kinds if "기업신용" in k), kinds[0])
    for rules, score in c.credit_rows:
        if col in rules and _rule_hit(rules[col], grade):
            res.score = score
            res.basis = f"{col} {grade} → '{rules[col]}'"
            break
    else:
        res.notes.append(f"신용평가등급 '{grade}'를 표({col})에서 찾지 못함 — 확인")
    if not kind:
        res.notes.append(f"신용평가 종류를 몰라 '{col}' 기준으로 봄")
    return res


def _sanction(c: Criterion, vault) -> ItemScore:
    """신인도 = 늘 배점 만점(11/05 사용자 결정 '신인도는 2점으로 고정') — 회사정보에 제재처분이 적혀 있으면 알림만.
    감점만 있는 항목('사회적 책임 및 성실성' 임금체불 -2점 등, 배점이 0 이하)은 감점 사유가 없으니 0점(11/12 당진 서식 4호)."""
    if c.points <= 0:
        res = ItemScore(c.name, 0.0, c.kind, 0.0, "감점 항목 — 해당 사유 없음 → 0점(감점 없음)")
    else:
        res = ItemScore(c.name, c.points, c.kind, c.points, "신인도는 배점 만점으로 고정 기재")
    v = _sq(str(vault.info.get("부정당업자 제재처분") or ""))
    if v and not re.fullmatch(r"없음|해당없음|무|해당사항없음|-", v):
        res.notes.append(f"⚠ 회사정보 '부정당업자 제재처분'이 '{vault.info.get('부정당업자 제재처분')}' — 처분 기간 안이면 고칠 것")
    return res


def score(criteria: list[Criterion], vault, ref: date) -> list[ItemScore]:
    out = []
    for c in criteria:
        if c.kind == "track":
            out.append(_track(c, vault, ref))
        elif c.kind == "staff":
            out.append(_staff(c, vault, ref))
        elif c.kind == "patent":
            out.append(_patent(c, vault))
        elif c.kind == "credit":
            out.append(_credit(c, vault))
        elif c.kind == "sanction":
            out.append(_sanction(c, vault))
        else:
            out.append(ItemScore(c.name, c.points, c.kind, None, notes=["계산 규칙이 없는 항목 — 직접 기입"]))
    return out


# ── 서식(자기평가표) 기준 ─────────────────────────────────────────────
# 11/05 사용자 결정: 제안요청서 배점표·계산 조건대로 계산하되, 서식의 배점 기준과 다르면 서식 기준,
# 계산식끼리 다르면 지일에게 유리한 쪽. 서식 표 안에 구간·등급이 적힌 서식(국민안전체험관 서식 6)은 그 구간으로,
# 줄마다 배점만 적힌 서식(필암서원 서식 24)은 본문 기준 점수를 서식 배점에 맞춰 비율로.
_SCORE_HEAD = ("자기평점", "자체평점", "자기평가점수", "자기평가", "평점", "평가점수", "점수")  # '점수' — 국립아시아문화전당 서식 14호(11/13)


# 자동으로 채점하는 항목 — 기술인력·경영상태(신용평가)·신인도·보유기술(특허)(11/05 사용자 결정, 실적은 담당자가 직접)
AUTO_KINDS = {"staff", "credit", "sanction", "patent"}  # 특허는 11/05 이어서 사용자 결정으로 더함(특허증 파일 수)


@dataclass
class SelfScore:
    """서식 채우기에 넘기는 자기평점 재료 — 본문 기준(`read_criteria`)·회사 자료·기준일."""
    criteria: list
    vault: object
    ref: date


@dataclass
class FormRow:
    label: str
    points: float | None  # 서식 배점(상한)
    cell: object  # 점수를 넣을 칸(fill.Cell)
    crit: Criterion | None = None  # 서식 표 안의 기준(구간·등급) — 없으면 본문 기준
    total: bool = False
    by_points: dict = field(default_factory=dict)  # 구간 줄마다 평점 칸이 따로인 표 — {그 줄 배점: 평점 칸}(11/10 경남교육청)


def _per_person(text: str) -> float | None:
    """서식 줄의 1인당 점수 — '0.5점/인'·'각0.5점'·'1인당 0.5점'(11/11). 그냥 '0.5'는 배점이라 아님."""
    m = re.fullmatch(r"\s*([\d.]+)\s*점?\s*/\s*인\s*", text or "") or \
        re.fullmatch(r"\s*(?:각|1\s*인\s*당|인\s*당)\s*([\d.]+)\s*점?\s*", text or "")
    return float(m.group(1)) if m else None


def read_form(cells) -> list[FormRow]:
    """자기평가표 표 칸들 → 점수 줄. '배점' 칸과 '자기평점'(또는 '평점') 칸이 있는 표만."""
    plain = lambda t: re.sub(r"\([^)]*\)", "", _sq(t))  # '배점(점)'·'평점(점)'(11/10 경남교육청)
    head = next((c for c in cells if plain(c.text) in _SCORE_HEAD), None)
    pts_head = next((c for c in cells if plain(c.text) in ("배점", "총배점")), None)  # '총 배 점(상한점수)'(11/13)
    if head is None or pts_head is None or head.row != pts_head.row:
        return []
    rows: dict[int, list] = {}
    for c in cells:
        if c.row > head.row:
            rows.setdefault(c.row, []).append(c)
    pcol, scol = pts_head.col, head.col
    blocks: list[list[int]] = []
    for r in sorted(rows):
        if any(c.col <= scol < c.col + c.colspan for c in rows[r]):
            blocks.append([r])
        elif blocks:
            blocks[-1].append(r)
    # 구간 줄마다 평점 칸이 따로 있는 표 — 맨 왼쪽 항목 칸(세로로 합친 칸)이 같은 줄들은 한 항목으로 묶음(11/10 경남교육청 서식 21)
    def item_cell(r):
        return next((c for c in cells if c.col == 0 and c.row <= r < c.row + c.rowspan and c.rowspan > 1), None)
    merged: list[list[int]] = []
    split_cells: dict[int, dict] = {}
    for rs in blocks:
        ic = item_cell(rs[0])
        if merged and ic is not None and item_cell(merged[-1][0]) is ic:
            merged[-1] += rs
            split_cells.setdefault(merged[-1][0], {})
        else:
            merged.append(list(rs))
    blocks = merged
    # 등급이 가로로 놓인 표 — 머리 아래 줄에 'BBB0이상 | BB- ~ BBB- | B+ ~ B- | CCC+ 이하', 점수 줄엔 그 아래 '15 | 14.25 | …'
    # (11/13 사용자 제보: 국립아시아문화전당 서식 14호 재무상태 점수가 빔) — {열: 등급 규칙}
    grade_cols: dict[int, str] = {}
    in_block = {r for rs in blocks for r in rs}
    for r in sorted(rows):
        if r in in_block:
            continue
        cand = {c.col: re.sub(r"\s+", " ", c.text).strip() for c in rows[r]
                if c.col > scol and len(_sq(c.text)) <= 20 and re.search(r"(?<![A-Za-z가-힣])[A-D]{1,3}[0+\-]?", c.text)}
        if len(cand) >= 2:
            grade_cols = cand
            break
    out = []
    for rs in blocks:
        first = sorted(rows[rs[0]], key=lambda c: c.col)
        target = next((c for c in first if c.col <= scol < c.col + c.colspan), None)
        by_points: dict = {}
        if rs[0] in split_cells:  # 줄마다 평점 칸 — {그 줄 배점: 평점 칸}
            for r in rs:
                line = rows[r]
                sc = next((c for c in line if c.col <= scol < c.col + c.colspan), None)
                pv = _num(next((c.text for c in line if c.col <= pcol < c.col + c.colspan), ""))
                if sc is not None and pv is not None and sc.blank:
                    by_points.setdefault(pv, sc)
            target = target if target is not None and target.blank else next(iter(by_points.values()), None)
        if target is None or not target.blank:
            continue
        def is_label(t: str) -> bool:  # 줄 이름 — 구간('70억 이상')·등급·해당없음 칸은 뺌
            q = _sq(t)
            return bool(q) and not (parse_range(t) and len(q) <= 20) and not any(g in q for g in GRADES) \
                and q not in ("해당없음", "해당") and not re.search(r"회사채|기업어음|^[A-D][A-D0-9+\-]*", q)
        label = " ".join(re.sub(r"\s+", " ", c.text).strip() for c in first if c.col < min(pcol, scol) and is_label(c.text))
        if re.fullmatch(r"\s*(?:계|합\s*계|소\s*계|총\s*점)\s*", label):
            out.append(FormRow(label, None, target, total=True))
            continue
        kind = next((k for k, rx in KINDS if rx.search(_sq(label))), "")
        crit = Criterion(label, 0.0, kind)
        cap = None
        kinds: dict[int, str] = {}
        nums = []
        for r in rs:
            line = sorted(rows[r], key=lambda c: c.col)
            pts_text = next((c.text for c in line if c.col <= pcol < c.col + c.colspan), "")
            pts, per = _num(pts_text), _per_person(pts_text)
            crit.note += " ".join(c.text.strip() for c in line if c.col > scol and c.text.strip()) + "\n"
            texts = [(c.col, re.sub(r"\s+", " ", c.text).strip()) for c in line if c.col < min(pcol, scol) and c.text.strip()]
            if any("상한" in _sq(t) for _, t in texts):
                cap = pts
                continue
            for col, t in texts:
                if re.fullmatch(r"회사채.*|기업어음|기업신용.*|신용평가등급", _sq(t)):
                    kinds[col] = _sq(t)
            if per is not None:
                g = next((g for g in GRADES for _, t in texts if g in _sq(t)), None)
                if g:
                    crit.per_grade.setdefault(g, per)
                continue
            if pts is None:
                continue
            nums.append(pts)
            rules = {kinds[col]: t for col, t in texts if col in kinds and re.search(r"[A-D]", t)}
            if not rules and len(set(kinds.values())) == 1:
                # 종류 이름 칸('기업신용평가등급')이 왼쪽에 세로로 합쳐져 있고 등급은 옆 칸('BBB0 이상 등급')(11/12 당진 서식 4호)
                only = next(iter(kinds.values()))
                rules = {only: t for col, t in texts if col not in kinds and re.search(r"(?<![가-힣])[A-D]{1,3}[0+\-]?", t)}
            if rules:
                crit.credit_rows.append((rules, pts))
                continue
            key = next((_sq(t) for _, t in texts if _sq(t) in ("해당없음", "해당")), None)
            if key:
                crit.sanction[key] = pts
                continue
            for _, t in reversed(texts):
                rng = parse_range(t)
                if rng and len(_sq(t)) <= 20 and re.search(r"억|만|원|건", t):
                    crit.bands.append(Band(rng[0], rng[1], pts, t))
                    crit.unit = crit.unit or rng[2]
                    break
        for r in rs if grade_cols else ():
            for c in rows[r]:
                v = _num(c.text) if c.col in grade_cols else None
                if v is not None:
                    crit.credit_rows.append(({"기업신용평가등급": grade_cols[c.col]}, v))
        points = cap if cap is not None else (max(nums) if nums else None)
        crit.points = points or 0.0
        has = crit.bands or crit.per_grade or crit.credit_rows or crit.sanction
        out.append(FormRow(label, points, target, crit if has else None, by_points=by_points))
    return out


def _scaled(item: ItemScore, points: float | None) -> ItemScore:
    """본문 기준 점수를 서식 배점에 맞춤(본문 6점 → 서식 4점이면 × 4/6)."""
    if item.score is None or not points or not item.points or abs(points - item.points) < 1e-9:
        return item
    v = round(item.score * points / item.points, 2)
    return ItemScore(item.name, points, item.kind, v, item.basis + f" → 서식 배점 {points:g}점에 맞춰 ×{points:g}/{item.points:g}",
                     item.notes + [f"본문 배점 {item.points:g}점과 서식 배점 {points:g}점이 달라 서식 기준으로 환산"])


def score_form(rows: list[FormRow], criteria: list[Criterion], vault, ref: date) -> list[tuple[FormRow, ItemScore]]:
    """서식 점수 줄마다 점수. 계 줄은 빼고 돌려줌."""
    body = {c.kind: c for c in criteria if c.kind}
    out = []
    for row in rows:
        if row.total:
            continue
        label = _sq(row.label)
        kind = next((k for k, rx in KINDS if rx.search(label)), "")
        if kind not in AUTO_KINDS:  # 실적·특허 등은 담당자가 직접(11/05 사용자 결정)
            out.append((row, ItemScore(row.label, row.points or 0, kind, None, notes=["담당자가 직접 채점"])))
            continue
        single = kind == "track" and "단일" in label and not re.search(r"누계|합계", label)
        if kind == "sanction":
            c = Criterion(row.label, row.points or (body["sanction"].points if "sanction" in body else 0), kind)
            out.append((row, _sanction(c, vault)))
            continue
        if row.crit is not None:
            c = row.crit
            if kind in body:  # 기간·단일계약 하한·재직 개월 같은 조건은 본문 주석에서
                c.note = body[kind].note + "\n" + row.label + "\n" + c.note
            else:
                c.note = row.label + "\n" + c.note
            c.kind = kind
            item = score([c], vault, ref)[0] if not single else _track(c, vault, ref, "max")
            item.notes.append("서식 표의 배점 기준으로 계산")
            out.append((row, item))
            continue
        if kind not in body:
            out.append((row, ItemScore(row.label, row.points or 0, kind, None, notes=["제안요청서 본문에 배점기준이 없어 비움 — 직접 기입"])))
            continue
        c = body[kind]
        if single:  # 본문은 누계로만 평가하는데 서식엔 '단일최대' 줄 — 본문 구간을 단일 최대 실적에 적용해 서식 배점으로 환산
            item = _track(c, vault, ref, "max")
            item.notes.append("본문엔 단일최대 기준이 없어 본문 누계 구간을 단일 최대 실적에 적용 — 확인")
        else:
            item = score([c], vault, ref)[0]
        out.append((row, _scaled(item, row.points)))
    return out


def fmt(v: float | None) -> str:
    return "" if v is None else f"{v:g}"


def match(label: str, scores: list[ItemScore]) -> ItemScore | None:
    """서식(자기평가표) 줄 이름 → 본문 항목. 본문이 누계로 평가하는데 서식 줄이 '단일'이면 맞추지 않음."""
    label = _sq(label)  # '신 인 도'처럼 글자를 띄운 줄 이름
    kind = next((k for k, rx in KINDS if rx.search(label)), None)
    hits = [s for s in scores if s.kind == kind]
    if not hits:
        return None
    if kind == "track" and re.search(r"단일", label) and not re.search(r"누계|합계", label):
        return None
    return hits[0]
