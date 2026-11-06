"""통합서식 쪽 맞춤 — 조금 넘쳐 쪽 하나가 낭비되는 서식만 손본다(2026-11-06 사용자 요청).

서식마다 새 쪽에서 시작하는 모양은 그대로 둔다. 다만 필암서원처럼
- 서식 끝 1~2줄('…접수하였음을 확인합니다.', '장성군수 귀하')만 다음 쪽으로 넘어가 거의 빈 쪽이 생기거나(넘침)
- 제목만 한 쪽에 남고 쪽에서 안 나뉘는 표가 통째로 다음 쪽으로 넘어가면(제목만 남음)
그 서식만 아래 순서로 한 단계씩 손보고 다시 가늠한다.
  - 제목만 남을 때: 표는 그대로 두고 쪽에서 나뉘게 — 제목 아래에서 표가 시작해 넘치는 부분만 다음 쪽으로 이어지고,
    다음 서식은 원래대로 새 쪽에서(사용자 결정). 나눌 수 없는 표(표지·4cm 미만)면 아래 순서로.
  - 끝 1~2줄 넘침: ① 잇단 빈 문단을 하나로 ② 표에서 글보다 칸이 큰 줄(여유)을 넘친 만큼만 줄임 ③ 남은 빈 문단도 뺌.
쪽 가늠은 한글 없이 python-hwpx(`hwpx.layout.pages.estimate_pages`)로 — 필암서원 통합서식을 가늠하면 실제 한글 화면과 같은
자리(2쪽 한 줄·3쪽 제목만)에서 쪽이 넘어간다. 라이브러리가 없거나 가늠을 못 하는 문서면 아무것도 바꾸지 않는다.
종이·쪽 기준 위치에 고정된 그림(봉투 예시 날인 원·조직도 선)이 있는 서식은 글을 옮기면 그림과 어긋나서 손대지 않는다.
"""

from __future__ import annotations

import io
import logging
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from .fill import _ROWSPAN, _Chars, _row_info, _spans
from .forms_cut import _blank_para, _text, _top_paragraphs

log = logging.getLogger(__name__)

MM = 7200 / 25.4
SMALL = round(30 * MM)    # 이 높이 이하만 쓴 쪽 = 1~2줄(제목) — 낭비로 봄
LINE = round(6 * MM)      # 글 한 줄 높이 어림(간격 포함)
MARGIN = round(1.5 * MM)  # 줄일 때 더 줄여 두는 여유(어림 오차)
MAX_ROUNDS = 10
SPLIT_MIN_HEIGHT = 11339  # 4cm — 이보다 낮은 표는 나누지 않음

_SEC = re.compile(r"Contents/section\d+\.xml")
_PB1 = re.compile(r'^(<(?:[\w.-]+:)?p\b[^>]*?\bpageBreak=")1(")')
_LINESEGS = re.compile(r"<(?:[\w.-]+:)?linesegarray\b.*?</(?:[\w.-]+:)?linesegarray>", re.S)
_FIXED_OBJ = re.compile(r'<(?:[\w.-]+:)?pos\b[^>]*\btreatAsChar="0"[^>]*\bvertRelTo="(?:PAPER|PAGE)"')
_COVER = re.compile(r'<(?:[\w.-]+:)?tbl\b[^>]*\bcolCnt="1"')


@dataclass
class _Form:
    paras: list[str]
    fixed: bool
    level: int = 0
    done: list[str] = field(default_factory=list)

    @property
    def name(self) -> str:
        for p in self.paras[:3]:
            t = re.sub(r"\s+", " ", _text(p)).strip()
            if t:
                return t[:20]
        return "서식"


@dataclass
class _Section:
    name: str
    prefix: str
    suffix: str
    forms: list[_Form]
    body: int

    def xml(self, strip: bool = False) -> str:
        paras = [p for f in self.forms for p in f.paras]
        out = self.prefix + "".join(paras) + self.suffix
        return _LINESEGS.sub("", out) if strip else out


def _body_height(xml: str) -> int:
    page = re.search(r'<(?:[\w.-]+:)?pagePr\b[^>]*\bheight="(\d+)"', xml)
    margin = re.search(r"<(?:[\w.-]+:)?margin\b([^>]*)>", xml)
    if not page or not margin:
        return round(257 * MM)
    v = {k: int(x) for k, x in re.findall(r'\b(\w+)="(\d+)"', margin.group(1))}
    return int(page.group(1)) - v.get("top", 0) - v.get("bottom", 0) - v.get("header", 0) - v.get("footer", 0)


def _load(name: str, xml: str) -> _Section:
    paras = _top_paragraphs(xml)
    groups: list[list[str]] = []
    for pa in paras:
        seg = xml[pa.start:pa.end]
        if not groups or _PB1.match(seg):
            groups.append([])
        groups[-1].append(seg)
    forms = [_Form(g, any(_FIXED_OBJ.search(s) for s in g)) for g in groups]
    return _Section(name, xml[:paras[0].start], xml[paras[-1].end:], forms, _body_height(xml))


def _top_table(seg: str) -> str | None:
    spans = _spans(seg, "tbl")
    return seg[spans[0][0]:spans[0][1]] if spans else None


def _is_split(tbl: str) -> bool:
    head = tbl[:tbl.find(">")]
    return 'pageBreak="TABLE"' in head and bool(re.search(r'<(?:[\w.-]+:)?pos\b[^>]*\btreatAsChar="0"', tbl[:3000]))


def _table_height(tbl: str, chars: _Chars) -> int:
    rows = _row_info(tbl, chars)
    if rows:
        return sum(max(h, n) for h, n in rows)
    sz = re.search(r'<(?:[\w.-]+:)?sz\b[^>]*\bheight="(\d+)"', tbl)
    return int(sz.group(1)) if sz else 0


def _usage(form: _Form, lines: list, chars: _Chars, body: int) -> dict[int, int]:
    """쪽 → 그 쪽에서 이 서식이 쓴 높이(가장 아래 줄의 바닥)."""
    used: dict[int, int] = {}
    for seg, ls in zip(form.paras, lines):
        tbl = _top_table(seg)
        for k, ln in enumerate(ls):
            if k == 0 and tbl is not None:
                bottom = body if _is_split(tbl) else ln.vertpos + _table_height(tbl, chars)
            else:
                bottom = ln.vertpos + LINE
            used[ln.page] = max(used.get(ln.page, 0), min(bottom, body))
    return used


def _problem(form: _Form, lines: list, chars: _Chars, body: int) -> tuple[str, int, int] | None:
    """('넘침', 마지막 쪽에 쓴 높이, 쪽) 또는 ('제목만', 첫 쪽에 쓴 높이, 다음 쪽) — 서식이 두 쪽 이상인데 한쪽 끝 쪽이 1~2줄뿐일 때."""
    used = _usage(form, lines, chars, body)
    pages = sorted(used)
    if len(pages) < 2:
        return None
    if used[pages[0]] <= SMALL:  # 제목만 남은 쪽을 먼저 — 표가 올라오면 끝 넘침도 같이 풀리는 일이 많음(필암 서식 16)
        return "제목만", used[pages[0]], pages[1]
    if used[pages[-1]] <= SMALL:
        return "넘침", used[pages[-1]], pages[-1]
    return None


def _shrink(tables: list[str], amount: int, chars: _Chars) -> tuple[list[str], int] | None:
    """표들에서 칸이 글보다 큰 줄을 여유가 큰 줄부터 모두 합쳐 amount만큼 줄임. 여유가 모자라면 None(줄여도 쪽이 안 맞음)."""
    free = []
    for ti, tbl in enumerate(tables):
        info = _row_info(tbl, chars)
        if not info:
            continue
        rows = _spans(tbl, "tr")
        free += [(h - n - MARGIN, ti, i) for i, (h, n) in enumerate(info)
                 if h - n - MARGIN > 0 and not _ROWSPAN.search(tbl[rows[i][0]:rows[i][1]])]
    free.sort(reverse=True)
    if sum(r for r, _, _ in free) < amount:
        return None
    cuts: dict[tuple[int, int], int] = {}
    left = amount
    for room, ti, i in free:
        if left <= 0:
            break
        cuts[(ti, i)] = min(room, left)
        left -= cuts[(ti, i)]
    out = list(tables)
    for ti in {ti for ti, _ in cuts}:
        tbl = out[ti]
        rows = _spans(tbl, "tr")
        mine = {i: c for (t2, i), c in cuts.items() if t2 == ti}
        for i in sorted(mine, reverse=True):
            ts, te = rows[i]
            tr = re.sub(r'(cellSz\b[^>]*height=")(\d+)"', lambda m: f'{m.group(1)}{int(m.group(2)) - mine[i]}"', tbl[ts:te])
            tbl = tbl[:ts] + tr + tbl[te:]
        total = sum(mine.values())
        out[ti] = re.sub(r'(<(?:[\w.-]+:)?sz\b[^>]*\bheight=")(\d+)"', lambda m: f'{m.group(1)}{max(0, int(m.group(2)) - total)}"', tbl, count=1)
    return out, sum(cuts.values())


def _para_align(header: str, seg: str) -> str:
    pid = re.match(r'<[^>]*\bparaPrIDRef="(\d+)"', seg)
    m = re.search(rf'<(?:[\w.-]+:)?paraPr\b[^>]*\bid="{pid.group(1)}".*?</(?:[\w.-]+:)?paraPr>', header, re.S) if pid else None
    a = re.search(r'<(?:[\w.-]+:)?align\b[^>]*\bhorizontal="(\w+)"', m.group(0)) if m else None
    return {"CENTER": "CENTER", "RIGHT": "RIGHT"}.get(a.group(1) if a else "", "LEFT")


def _make_split(tbl: str, align: str) -> str:
    """'글자처럼 취급' 표 → 쪽 경계에서 나뉘는 표(한글이 저장한 '나눔' 표 모양 — treatAsChar·affectLSpacing 0, pageBreak TABLE)."""
    head = tbl[:tbl.find(">") + 1]
    new_head = re.sub(r'\bpageBreak="\w+"', 'pageBreak="TABLE"', head) if "pageBreak=" in head else head[:-1] + ' pageBreak="TABLE">'
    body = tbl[len(head):]
    pos = re.search(r"<(?:[\w.-]+:)?pos\b[^>]*>", body)
    if pos:
        p2 = pos.group(0)
        for k, v in (("treatAsChar", "0"), ("affectLSpacing", "0"), ("horzRelTo", "COLUMN"), ("horzAlign", align), ("horzOffset", "0")):
            p2 = re.sub(rf'\b{k}="[^"]*"', f'{k}="{v}"', p2)
        body = body[:pos.start()] + p2 + body[pos.end():]
    return new_head + body


def _replace_table(seg: str, new_tbl: str) -> str:
    s, e = _spans(seg, "tbl")[0]
    return seg[:s] + new_tbl + seg[e:]


SHRINK_TRIES = (0, 3, 6, 10)  # 표 줄이기를 다시 할 때마다 더 줄이는 양(mm) — 표 높이 어림이 한글보다 작을 수 있어서


def _step(form: _Form, prob: tuple[str, int, int], lines: list, chars: _Chars, header: str, body: int) -> bool:
    """다음 단계 하나를 해 봄 — 바뀐 게 있으면 True.
    제목만: 표 나누기 → (못 나누면) 아래와 같음. 넘침: 빈 문단 하나로 → 표 줄 여유 줄이기(넘친 만큼, 안 맞으면 조금씩 더) → 빈 문단 빼기."""
    kind, used, page = prob
    plan = ["blank1"] + [f"shrink{i}" for i in range(len(SHRINK_TRIES))] + ["blank_all"]
    if kind == "제목만":
        # 11/06 사용자 결정: 표는 그대로 두고 제목 아래에서 시작해 넘치는 부분만 다음 쪽으로(그 쪽은 그대로, 다음 서식은 새 쪽).
        # 나눌 수 없는 표(표지·4cm 미만)일 때만 빈 줄·칸 여유 줄이기로
        plan = ["split"] + plan
    start = 1  # 이 문단부터만 빈 문단을 뺌
    if form.fixed:
        # 종이 기준 그림이 있는 서식(조직도·봉투 예시): 그림 아래 글은 그대로 두고, 마지막 그림 문단 다음 첫 내용 뒤의 빈 문단만
        # (필암 서식 13 — 조직도 표 아래 빈 줄 3개 때문에 주) 3번 한 줄이 다음 쪽으로). 표 줄이기·나누기는 안 함(그림과 어긋남)
        if kind != "넘침":
            return False
        last = max(k for k, p in enumerate(form.paras) if _FIXED_OBJ.search(p))
        start = next((k + 1 for k in range(last + 1, len(form.paras)) if not _blank_para(form.paras[k])), len(form.paras))
        plan = ["blank1", "blank_all"]
    while form.level < len(plan):
        step = plan[form.level]
        form.level += 1
        if step == "blank1":
            kept = [p for k, p in enumerate(form.paras)
                    if not (k >= start and _blank_para(p) and _blank_para(form.paras[k - 1]) and "secPr" not in p)]
            if len(kept) < len(form.paras):
                form.done.append(f"빈 줄 {len(form.paras) - len(kept)}개 뺌")
                form.paras = kept
                return True
        elif step.startswith("shrink"):
            extra = round(SHRINK_TRIES[int(step[6:])] * MM)
            idx = [k for k, p in enumerate(form.paras) if (tb := _top_table(p)) and not _is_split(tb)]
            if kind == "제목만":  # 다음 쪽으로 넘어간 표가 첫 쪽에 들어갈 만큼
                idx = [k for k in idx if lines[k] and lines[k][0].page == page][:1]
                amount = max(0, used + sum(_table_height(_top_table(form.paras[k]), chars) for k in idx) - body) + MARGIN + extra
            else:  # 넘친 만큼 — 서식 안 표들에서 모아
                amount = used + MARGIN + extra
            got = _shrink([_top_table(form.paras[k]) for k in idx], amount, chars) if idx else None
            if got:
                for k, tb in zip(idx, got[0]):
                    form.paras[k] = _replace_table(form.paras[k], tb)
                form.done.append(f"표 칸 여유 {got[1] / MM:.1f}mm 줄임")
                return True
        elif step == "blank_all":
            kept = [p for k, p in enumerate(form.paras) if not (k >= start and _blank_para(p) and "secPr" not in p)]
            if len(kept) < len(form.paras):
                form.done.append(f"빈 줄 {len(form.paras) - len(kept)}개 더 뺌")
                form.paras = kept
                return True
        elif step == "split" and kind == "제목만":
            for k, p in enumerate(form.paras):
                tbl = _top_table(p)
                if (tbl and lines[k] and lines[k][0].page == page and not _is_split(tbl) and not _COVER.search(tbl[:tbl.find(">") + 1])
                        and 'textWrap="TOP_AND_BOTTOM"' in tbl[:tbl.find(">")] and _table_height(tbl, chars) >= SPLIT_MIN_HEIGHT):
                    form.paras[k] = _replace_table(p, _make_split(tbl, _para_align(header, p)))
                    form.done.append("표를 쪽에서 나뉘게(제목 아래에서 시작해 다음 쪽으로 이어짐)")
                    return True
    return False


def _estimate(files: dict[str, bytes], secs: list[_Section]):
    from hwpx.layout.pages import estimate_pages

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        by = {s.name: s for s in secs}
        for name, data in files.items():
            if name in by:
                # 가늠 도구가 못 따라가는 '어울림'(SQUARE) 배치는 가늠용 사본에서만 위아래 배치로(필암 서식 13 총괄책임자 상자)
                data = by[name].xml(strip=True).replace('textWrap="SQUARE"', 'textWrap="TOP_AND_BOTTOM"').encode("utf-8")
            z.writestr(name, data, compress_type=zipfile.ZIP_STORED if name == "mimetype" else zipfile.ZIP_DEFLATED)
    est = estimate_pages(buf.getvalue())
    if est.pages is None:
        return None
    return list(est.lines)


def fit(path: Path) -> list[str]:
    """통합서식 파일을 고쳐 다시 쓴다. 돌려줌: 손본 서식 알림(없으면 빈 목록)."""
    try:
        import hwpx.layout.pages  # noqa: F401
    except ImportError:
        return ["쪽 맞춤 건너뜀 — python-hwpx가 설치되지 않음(py -m pip install -r requirements.txt)"]
    path = Path(path)
    with zipfile.ZipFile(path) as z:
        infos = z.infolist()
        files = {i.filename: z.read(i.filename) for i in infos}
    names = sorted((n for n in files if _SEC.fullmatch(n)), key=lambda n: int(re.search(r"(\d+)\.xml$", n).group(1)))
    header = files.get("Contents/header.xml", b"").decode("utf-8")
    chars = _Chars(header)
    secs = [_load(n, files[n].decode("utf-8")) for n in names]
    touched: set[int] = set()
    for _ in range(MAX_ROUNDS):
        try:
            lines = _estimate(files, secs)
        except Exception as err:  # noqa: BLE001 — 가늠이 실패해도 통합서식은 그대로 둠
            log.warning("쪽 가늠 실패: %s", err)
            lines = None
        if lines is None:
            break
        changed = False
        k = 0
        for sec in secs:
            for form in sec.forms:
                fl = lines[k:k + len(form.paras)]
                k += len(form.paras)
                prob = _problem(form, fl, chars, sec.body)
                if prob and _step(form, prob, fl, chars, header, sec.body):
                    touched.add(id(form))
                    changed = True
        if not changed:
            break
    notes = []
    for sec in secs:
        for form in sec.forms:
            if id(form) in touched:
                form.paras = [_LINESEGS.sub("", p) for p in form.paras]  # 한글이 다시 계산하게
                notes.append(f"쪽 맞춤 — {form.name}: " + ", ".join(form.done))
    if not notes:
        return []
    out = io.BytesIO()
    by = {s.name: s for s in secs}
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for i in infos:
            data = by[i.filename].xml().encode("utf-8") if i.filename in by else files[i.filename]
            z.writestr(i.filename, data, compress_type=zipfile.ZIP_STORED if i.filename == "mimetype" else zipfile.ZIP_DEFLATED)
    path.write_bytes(out.getvalue())
    for n in notes:
        log.info(n)
    return notes
