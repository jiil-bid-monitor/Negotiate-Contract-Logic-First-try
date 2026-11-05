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

KINDS = [("track", re.compile(r"실적")), ("staff", re.compile(r"인력|기술자")),
         ("patent", re.compile(r"특허|보유\s*기술|전문\s*기술|기술\s*보유")), ("credit", re.compile(r"경영|신용")),
         ("sanction", re.compile(r"신인도|제재"))]
_START = re.compile(r"정량\S{0,4}\s*평가\S{0,4}\s*[:：]?\s*\(?\s*\d+(?:\.\d+)?\s*점")
_STOP = re.compile(r"^\s*주\s*\d+\s*\)|정성\S{0,4}\s*평가\S{0,4}\s*[:：]?\s*\(?\s*\d+(?:\.\d+)?\s*점")
_HEAD = re.compile(r"^\s*(?:\d+\)|\(\d+\)|[가-하]\.)\s*([^:：\n]{2,30}?)\s*[:：]\s*(\d+(?:\.\d+)?)\s*점")
_MULT = {"억": 1e8, "천만": 1e7, "백만": 1e6, "만": 1e4}
_QTY = r"([\d]+(?:[.,]\d+)*)\s*(억|천만|백만|만)?\s*(원|건)?"
_NUM = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*점?\s*$")
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
            below = _num(rows.get(r + 1, {}).get(col, ""))
            if g and below is not None and "기술" in _sq(text) and len(_sq(text)) <= 8:
                c.per_grade.setdefault(g, below)
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


def _read_doc(xmls: list[str]) -> list[Criterion]:
    items: list[tuple[str, str, list[tuple[str, tuple[int, int]]]]] = []  # (글, 표 바깥 글, [(xml, 표)])
    for xml in xmls:
        for p in _top_paragraphs(xml):
            seg = xml[p.start:p.end]
            tbls = [(xml, (p.start + a, p.start + b)) for a, b in _spans(seg, "tbl")]
            outside = re.sub(r"<(?:[\w.-]+:)?tbl\b.*?</(?:[\w.-]+:)?tbl>", "", seg, flags=re.S)
            items.append((_text(outside).strip(), outside, tbls))
    start = next((i for i, (t, _, _) in enumerate(items) if _START.search(t)), None)
    if start is None:
        return []
    out: list[Criterion] = []
    cur: Criterion | None = None
    summary: dict[str, tuple[str, float]] = {}
    for i in range(start, len(items)):
        text, _, tbls = items[i]
        if i > start and _STOP.search(text) and not _HEAD.match(text):
            break
        head = _HEAD.match(text)
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
                continue
            cur.note += re.sub(r"\s+", " ", _text(xml[tbl[0]:tbl[1]])) + "\n"
            _read_table(cur, xml, tbl)
    if not out:  # 항목 머리가 없으면 요약표의 항목·배점만(기준 표는 못 읽음)
        out = [Criterion(name, pts, k) for k, (name, pts) in summary.items()]
    return out


# ── 점수 ────────────────────────────────────────────────────────────
def _band(c: Criterion, value: float) -> Band | None:
    for b in c.bands:
        if (b.lo is None or value >= b.lo) and (b.hi is None or value < b.hi):
            return b
    return None


def _won(v: float) -> str:
    return f"{v / 1e8:.2f}억 원" if v >= 1e8 else f"{v:,.0f}원"


def _track(c: Criterion, vault, ref: date) -> ItemScore:
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
    single = re.search(r"단일\s*(?:계약)?\s*(?:1\s*건)?.{0,20}?" + _QTY + r"\s*이상", note)
    min_single = _qty(single.group(1), single.group(2)) if single else None
    max_mode = bool(re.search(r"단일\s*(?:최대|건)\s*실적.{0,10}평가", note)) and not re.search(r"누계|합계", note)
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
        if alt <= total and band is not None and band.score < top.score:
            res.notes.append(f"⚠ 주석은 '{_won(alt)} 이상이면 만점'인데 배점표는 {top.text}만 만점 — 배점표대로 {band.score:g}점(주석대로면 {top.score:g}점), 발주처 확인")
    return res


def _staff(c: Criterion, vault, ref: date) -> ItemScore:
    from . import notice as N

    res = ItemScore(c.name, c.points, c.kind, None)
    if not c.per_grade:
        res.notes.append("배점기준 표(등급별 점수)를 읽지 못함 — 제안요청서 확인")
        return res
    staff = getattr(vault, "staff", []) or []
    if not staff:
        res.notes.append("자료 없음 — 회사자료 '인력목록' 시트(이름·입사일·재직·등급)")
        return res
    months = re.search(r"(\d+)\s*개월\s*이상", c.note)
    months = int(months.group(1)) if months else 0
    cut = None  # 이 날 이전에 입사해야 'N개월 이상 재직'
    if months:
        y, m = divmod(ref.month - 1 - months, 12)
        try:
            cut = date(ref.year + y, m + 1, min(ref.day, 28))
        except ValueError:
            cut = None
    best: dict[str, tuple[str, float]] = {}
    skipped = {"퇴사": 0, f"재직 {months}개월 미만·입사일 없음": 0, "등급 없음": 0}
    for r in staff:
        name = str(r.get("이름") or "").strip()
        if not name:
            continue
        if str(r.get("재직") or "재직").strip() == "퇴사":
            skipped["퇴사"] += 1
            continue
        joined = N._as_date(r.get("입사일"))
        if cut and (joined is None or joined > cut):
            skipped[f"재직 {months}개월 미만·입사일 없음"] += 1
            continue
        grade = next((g for g in GRADES if g in _sq(str(r.get("등급") or ""))), None)
        if grade == "기술사" and "기술사" not in c.per_grade:
            grade = "특급"  # 기술사는 특급 이상 — 표에 기술사 점수가 없으면 특급 점수
            res.notes.append(f"{name}: 기술사 → 특급 점수로 셈")
        if grade not in c.per_grade:
            skipped["등급 없음"] += 1
            continue
        pts = c.per_grade[grade]
        if name not in best or best[name][1] < pts:
            best[name] = (grade, pts)
    raw = sum(p for _, p in best.values())
    res.score = min(raw, c.points)
    counts = {g: sum(1 for gg, _ in best.values() if gg == g) for g in GRADES}
    res.basis = ", ".join(f"{g} {n}명×{c.per_grade[g]:g}" for g, n in counts.items() if n) + f" = {raw:g}점" + (
        f" → 최고 {c.points:g}점" if raw > c.points else "")
    res.notes += [f"뺀 인원 {k} {n}명" for k, n in skipped.items() if n]
    if re.search(r"분야\s*인력", c.note):
        res.notes.append("공고가 인정하는 분야 인력인지 확인(" + re.sub(r"\s+", " ", re.search(r"[^.\n]*분야\s*인력[^.\n]*", c.note).group(0))[:60] + ")")
    return res


def _patent(c: Criterion, vault) -> ItemScore:
    res = ItemScore(c.name, c.points, c.kind, None)
    if not c.bands:
        res.notes.append("배점기준 표(건수 구간)를 읽지 못함 — 제안요청서 확인")
        return res
    pats = getattr(vault, "patents", None)
    if pats is None:
        res.notes.append("자료 없음 — 회사자료 '특허목록' 시트")
        return res
    n = sum(1 for p in pats if str(p.get("등록번호") or p.get("명칭") or "").strip())
    band = _band(c, n)
    res.score = band.score if band else None
    res.basis = f"{n}건 → '{band.text if band else '구간 없음'}'"
    scope = re.search(r"※\s*([^.\n]*특허[^.\n]*)", c.note)
    if scope:
        res.notes.append(f"공고가 인정하는 특허인지 확인({scope.group(1).strip()[:50]})")
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
    res = ItemScore(c.name, c.points, c.kind, None)
    if not c.sanction:
        res.notes.append("배점기준 표(해당없음/해당)를 읽지 못함 — 제안요청서 확인")
        return res
    v = _sq(str(vault.info.get("부정당업자 제재처분") or ""))
    if not v:
        res.notes.append("자료 없음 — 회사정보 '부정당업자 제재처분'(없음 / 있음·처분일)")
        return res
    key = "해당없음" if re.fullmatch(r"없음|해당없음|무|해당사항없음|-", v) else "해당"
    res.score = c.sanction.get(key)
    res.basis = f"제재처분 {'없음' if key == '해당없음' else vault.info.get('부정당업자 제재처분')} → {key}"
    if key == "해당":
        res.notes.append("처분일이 공고가 보는 기간(최근 N년) 안인지 확인")
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
