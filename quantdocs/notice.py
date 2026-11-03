"""공고 자료(공고문·제안요청서 등)를 읽어 정량평가서 준비표를 만든다.

    python -m quantdocs notice 공고문.hwpx 제안요청서.hwpx --out "<폴더>" [--company 회사자료_양식.xlsx] [--notice-date 2026-08-20]

뽑는 것(공개 공고 자료만 읽음):
- 정량평가서 구성 — 제출도서 표에서 '정량적 평가서'(사업수행능력 평가자료) 묶음의 항목·서식 번호
- 제출서류 — '제출서류' 아래 ①②… 목록
- 실적 요건 — '실적'과 'N원 이상'이 함께 있는 문장(기간·금액·단일/누적)
- 마감 — 첨부에 적힌 등록·제출 마감
각 항목을 서류 목록(config/documents.json)의 서류 종류와 이어 '어느 회사 자료로 채우는지'를 적는다.
`--company`로 채운 회사자료 양식을 주면 실적목록에서 기간·금액 요건에 맞는 실적 후보를 고른다(확정은 담당자).

글 읽기·마감·실적 요약은 같은 저장소의 `nego` 패키지(수집기) 코드를 그대로 쓴다(11/03 프로젝트 1 레포로 합침).
평가 점수는 계산하지 않는다(범위 밖).
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from .catalog import Document, load_catalog

ROOT = Path(__file__).resolve().parent.parent
_NEGO_CANDIDATES = ("Negotiate-Contract-Logic-First-try", "negotiate-contract-logic-first-try")


def _import_nego():
    """프로젝트 1의 nego 패키지를 불러온다(NEGO_REPO → 옆 폴더 순)."""
    paths = [str(ROOT), os.environ.get("NEGO_REPO", "")] + [str(ROOT.parent / n) for n in _NEGO_CANDIDATES]
    for p in paths:
        if p and (Path(p) / "nego" / "attachments.py").exists():
            if p not in sys.path:
                sys.path.insert(0, p)
            break
    try:
        from nego import attachments, schedule_text, text_requirements  # noqa: F401
    except ImportError as err:  # pragma: no cover - 환경 문제 안내
        raise SystemExit("프로젝트 1(Negotiate-Contract-Logic-First-try) 폴더를 찾지 못했습니다 — "
                         "NEGO_REPO 환경변수에 그 폴더 경로를 넣으세요.") from err
    return attachments, schedule_text, text_requirements


# ── 서류 종류 연결 ────────────────────────────────────────────
# 위에서부터 처음 맞는 것. 맞는 게 없으면 '공고 서식(직접 작성)' — 서약서·제출서처럼 공고마다 새로 쓰는 서식.
KIND_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("credit_rating", ("신용평가", "경영상태")),
    ("track_record", ("실적",)),
    ("staff", ("인력", "이력", "기술자", "전문기술", "가입자명부")),
    ("history", ("연혁", "일반현황")),
    ("direct_production", ("직접생산",)),
    ("certification", ("여성기업", "벤처", "중소기업확인", "ISO", "인증서")),
    ("qualification", ("면허", "신고필증", "등록증", "자격을 증명")),
]
# 서류 종류 → 회사자료 양식의 어느 시트에서 채우나
SOURCE_SHEET = {
    "history": "서류상태", "qualification": "서류상태", "direct_production": "서류상태",
    "credit_rating": "서류상태", "certification": "서류상태", "track_record": "실적목록", "staff": "인력목록",
}
FORM_ONLY = "공고 서식(직접 작성)"
# 서류 목록 8종엔 없지만 회사가 발급받아 두는 공통 서류 — 서류상태 시트에서 관리
COMMON_ISSUED = ("등기부", "인감증명", "완납증명", "사업자등록증")
COMMON_LABEL = "회사 공통 발급 서류"


def classify(text: str, catalog: list[Document]) -> Document | None:
    s = re.sub(r"\s+", "", text)
    if any(w in s for w in COMMON_ISSUED):
        return None
    by_id = {d.id: d for d in catalog}
    for doc_id, words in KIND_RULES:
        if any(w in s for w in words) and doc_id in by_id:
            return by_id[doc_id]
    return None


# ── 읽기 ─────────────────────────────────────────────────────
@dataclass
class Item:
    text: str
    source: str
    form_no: str = ""
    doc: Document | None = None


@dataclass
class TrackReq:
    text: str
    source: str
    years: int | None = None
    min_amount: int | None = None  # 원
    per_project: bool = True  # 건당·단일(True) / 누적(False)
    summary: list[list[str]] = field(default_factory=list)
    kind: str = "제출 요건"  # 제출 요건 / 평가 실적 인정 기준(점수는 계산 안 함)
    notes: list[str] = field(default_factory=list)  # 같은 묶음의 '*' 참고 줄(인정 범위 등)


@dataclass
class Form:
    """공고가 정한 제출 서식 하나. in_quant = 정량적 평가서(사업수행능력 평가자료) 묶음에 든 서식."""
    no: int
    name: str
    source: str
    in_quant: bool = False
    doc: Document | None = None
    file: str = ""  # 잘라 낸 서식 파일 이름(cut_forms 뒤)


@dataclass
class NoticeSheet:
    files: list[str]
    forms: list[Form]
    submit_items: list[Item]
    track_reqs: list[TrackReq]
    deadlines: list[tuple[str, datetime, str]]
    texts: dict[str, str] = field(default_factory=dict)  # 문서 이름 → 읽은 글(공고 유형 가리기 등)

    @property
    def quant_items(self) -> list[Form]:
        return [f for f in self.forms if f.in_quant]


_CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"
_CIRCLED_LINE = re.compile(rf"^\s*[{_CIRCLED}]\s*(?P<text>.+)$")
_HEAD_LINE = re.compile(r"^\s*(?:[가-하]\.|\d+\.|[ⅠⅡⅢⅣⅤⅥⅦ]|□|■|※)")

# ── 서식 목록: 세 가지 적는 방식 ─────────────────────────────
# ① 표 한 줄: "- 일반현황 및 연혁 | 서식14" (hwpx 표가 한 줄로 읽힘)
# ② 표 칸이 줄마다 풀림(옛 hwp): "- 제안사 연혁 및 일반현황" 다음 줄 "서식12" (또는 "서식28" / "~31")
# ③ 목록·목차 줄 안: "3) 참여전문기술자 보유현황(서식 9)", "9. 참여 전문기술자 보유현황(서식9)", "응모신청서 제출 1부 < 서식 1 >"
_FORM_CELL = re.compile(r"^\s*서식\s*(\d+)\s*(?:[~∼-]\s*(\d+))?\s*$")
_FORM_INLINE = re.compile(r"[(<〔\[【]\s*서식\s*(\d+)\s*(?:[~∼-]\s*(\d+))?\s*[)>〕\]】]")
_LIST_MARK = re.compile(rf"^\s*(?:\d+\s*[).]|[{_CIRCLED}]|[-·∙•▪]|[가-하]\.)\s*")


def _squash(s: str) -> str:
    return re.sub(r"\s+", "", s)


def _clean_name(name: str) -> str:
    name = _LIST_MARK.sub("", name.strip())
    name = re.sub(r"\s*(?:각\s*)?\d+\s*부\.?\s*$", "", name)  # "… 1부"
    name = re.sub(r"\s*(?:제출|작성)\s*$", "", name)
    return re.sub(r"\s+", " ", name).strip(" -:·")


def _quant_range(lines: list[str]) -> range:
    """'정량적 평가서' 묶음(다음 '정성적'·'가격 제안서' 전까지). 칸이 풀린 표는 '정량적' / '평가서'가 두 줄로 나뉨."""
    start = None
    for i in range(len(lines)):
        if re.search(r"정량적평가서|사업수행능력평가자료\(?정량", _squash("".join(lines[i:i + 2]))):
            # 목록 안의 "사업수행능력 평가자료(정량적 지표) 2부" 같은 줄은 묶음 시작이 아님 — 바로 뒤에 서식이 나와야 함
            if any(_FORM_CELL.match(x) or "| 서식" in x for x in lines[i:i + 12]):
                start = i
                break
    if start is None:
        return range(0)
    end = next((k for k in range(start + 1, len(lines)) if re.search(r"정성적|가격제안서", _squash(lines[k]))), len(lines))
    return range(start, end)


def read_forms(text: str, source: str, catalog: list[Document]) -> list[Form]:
    """서식 번호 → 이름. 같은 번호가 여러 번 나오면 처음 이름을 쓰고, 정량 묶음 여부는 하나라도 있으면 참."""
    lines = text.splitlines()
    quant = _quant_range(lines)
    found: dict[int, Form] = {}

    def add_one(n: int, name: str, i: int) -> bool:
        name = _clean_name(name)
        if not name or len(name) > 60 or re.search(r"서식\s*\d", name):
            return False
        f = found.get(n)
        if f is None:
            found[n] = Form(n, name, source, i in quant, classify(name, catalog))
        elif i in quant:
            f.in_quant = True
        return True

    def add(nos: tuple[str, str | None], names: list[str], i: int) -> bool:
        """서식 a(~b)에 이름들을 붙임. 범위 칸 수와 이름 수가 같으면 차례로, 아니면 모두 마지막 이름(범위 표시)."""
        a = int(nos[0])
        b = int(nos[1]) if nos[1] else a
        if b < a:  # "서식4-1"은 범위가 아니라 4번의 하위 서식
            b = a
        span = list(range(a, min(b, a + 15) + 1))
        names = [x for x in names if _clean_name(x)]
        if not names:
            return False
        if len(span) > 1 and len(names) == len(span):
            return all([add_one(n, x, i) for n, x in zip(span, names)])
        label = names[-1] if len(span) == 1 else f"{_clean_name(names[-1])} ({a}~{b})"
        return all([add_one(n, label, i) for n in span])

    for i, ln in enumerate(lines):  # ④ 서식 목차 표 "[서식 N] | 이름" — 이름이 가장 정확해서 먼저
        m = re.match(r"^\s*(?:[\[〔<【]\s*)?서식\s*(?:제\s*)?(\d+)\s*호?\s*(?:-\s*\d+)?\s*(?:[\]〕>】]\s*)?\|\s*([^|]+)", ln)
        if m:  # "[서식 4-1] | 공동수급표준협정서(공동이행)" → 서식4 = 공동수급표준협정서, "서식 1호 | 표지 |"(11/03 영동군)
            name = re.sub(r"\s*\([^)]*\)\s*$", "", m.group(2)) if "-" in ln.split("|")[0] else m.group(2)
            name = re.split(r"\s※", name)[0]  # "… 서약서 ※ 서식 16호 이행 서약서는 …" 꼬리 설명
            add((m.group(1), None), [name], i)

    pending: list[str] = []  # 칸이 풀린 표에서 '서식N' 앞에 쌓인 '- 이름' 줄들
    for i, ln in enumerate(lines):
        if "|" in ln:  # ① 표 한 줄 — 마지막 칸이 서식, 그 앞 칸이 이름(여러 개면 ' - '로 나뉨, 너무 길면 한 칸 더 앞)
            cells = [c.strip() for c in ln.split("|")]
            m = _FORM_CELL.match(cells[-1]) if len(cells) >= 2 else None
            if m:
                parts = [x for x in re.split(r"\s+-\s+|^-\s*", cells[-2]) if x.strip()]
                if not add(m.groups(), parts, i) and len(cells) >= 3:
                    add(m.groups(), [cells[-3]], i)
                pending = []
                continue
        m = _FORM_CELL.match(ln)
        if m and pending:  # ② 칸이 풀린 표 — 다음 줄이 "~31"이면 범위
            nos = m.groups()
            k = i + 1
            while k < len(lines) and not lines[k].strip():
                k += 1
            r = re.match(r"^\s*[~∼]\s*(\d+)\s*$", lines[k]) if k < len(lines) else None
            if r and not nos[1]:
                nos = (nos[0], r.group(1))
                add(nos, pending, i)
            else:
                add(nos, pending[-1:], i)
            pending = []
            continue
        if re.match(r"^\s*[~∼]\s*\d+\s*$", ln):
            continue
        m = _FORM_INLINE.search(ln)
        if m and _LIST_MARK.match(ln) and len(_squash(ln[m.end():])) <= 6:  # ③ 목록 줄 끝의 (서식 N)
            add(m.groups(), [ln[:m.start()]], i)
            pending = []
            continue
        if re.match(r"^\s*[-∙•]\s*\S", ln):
            pending.append(ln)
        elif ln.strip() and not re.match(r"^\s*(?:\d+\s*부|[※·(]|구\s*분|부\s*수|항\s*목|작\s*성)", ln):
            pending = []
    return sorted(found.values(), key=lambda f: f.no)


# ── 제출서류 목록: ①② / 1) 2) / - 줄 ───────────────────────────
_SUBMIT_HEAD = re.compile(r"제출\s*서류|구비\s*서류|제출\s*도서|필요\s*서류|등록\s*서류")
_ITEM_KINDS = {
    "circled": re.compile(rf"^\s*[{_CIRCLED}]\s*(?P<text>.+)$"),
    "paren": re.compile(r"^\s*\d+\s*\)\s*(?P<text>.+)$"),
    "dash": re.compile(r"^\s*[-·∙]\s*(?P<text>.+)$"),
}


_FORM_NOS = re.compile(r"서식\s*(?:제\s*)?(\d+(?:\s*[,~∼·]\s*\d+)*)\s*호?")


def _form_nos(cell: str) -> list[int]:
    m = _FORM_NOS.search(cell or "")
    if not m:
        return []
    out = []
    for part in re.split(r"\s*[,·]\s*", m.group(1)):
        a, _, b = re.split(r"\s*([~∼])\s*", part) + ["", ""] if re.search(r"[~∼]", part) else (part, "", "")
        a, b = int(a), int(b) if b else int(a)
        out += list(range(a, min(b, a + 30) + 1))
    return out


def _submit_table(lines: list[str], j: int) -> tuple[list[tuple[str, str]], int]:
    """'구비서류 | 서식번호' 표(11/03 영동군): 머리줄 다음 '• 묶음 | 서식' / ' - 서류 | 서식 N호' 줄들. (서류 글, 서식 번호 글), 끝난 줄."""
    if not re.search(r"서식|비고|구분|서류", lines[j]):
        return [], j
    k, items = j + 1, []
    while k < len(lines) and "|" in lines[k] and re.match(r"^\s*[-•·∙▪○●]", lines[k]):
        cells = [c.strip() for c in lines[k].split("|")]
        body = re.sub(r"^[-•·∙▪○●]\s*", "", cells[0]).strip()
        nos = ",".join(str(n) for n in _form_nos(cells[1] if len(cells) > 1 else ""))
        if body and not lines[k].lstrip().startswith(("•", "▪", "●")):
            items.append((body, nos))
        k += 1
    return items, k


def submit_quant_forms(text: str) -> set[int]:
    """제출서류 표에서 '정량' 묶음('• 사업수행능력 제안서(정량적 평가서류) | 서식 17호' 아래 줄들)의 서식 번호."""
    out: set[int] = set()
    in_quant = False
    for ln in text.splitlines():
        if "|" not in ln:
            continue
        cells = [c.strip() for c in ln.split("|")]
        if re.match(r"^[•▪●]", cells[0]):
            in_quant = bool(re.search(r"정량|사업수행능력", _squash(cells[0])))
        elif not re.match(r"^[-·∙]", cells[0]):
            in_quant = False
            continue
        if in_quant:
            out.update(_form_nos(cells[1] if len(cells) > 1 else ""))
    return out


def read_submit_items(text: str, source: str, catalog: list[Document]) -> list[Item]:
    """'제출서류'·'구비서류'·'제출도서'·'필요서류' 제목 바로 아래 목록. 첫 항목의 번호 모양(①/1)/-)과 같은 줄만 항목으로,
    그보다 깊은 줄(①·1) 아래 '-' 줄, 괄호 줄)은 앞 항목에 붙임."""
    lines = text.splitlines()
    out, seen = [], set()
    i = 0
    while i < len(lines):
        ln = lines[i]
        head = _SUBMIT_HEAD.search(ln)
        if not head or len(_squash(ln)) > 40:
            i += 1
            continue
        j = i + 1
        while j < len(lines) and not lines[j].strip():
            j += 1
        kind = next((k for k, r in _ITEM_KINDS.items() if j < len(lines) and r.match(lines[j])), None)
        if kind is None and j < len(lines) and "|" in lines[j]:
            table_items, j = _submit_table(lines, j)
            if table_items:
                for body, no in table_items:
                    if _squash(body) not in seen:
                        seen.add(_squash(body))
                        out.append(Item(body, source, no, classify(body, catalog)))
                i = j
                continue
        if kind is None:
            i += 1
            continue
        items: list[str] = []
        while j < len(lines):
            cur = lines[j]
            m = _ITEM_KINDS[kind].match(cur)
            if m and _SUBMIT_HEAD.search(m.group("text")) and len(_squash(m.group("text"))) <= 25:
                break  # "2) 제출서류" — 그 아래 목록은 바깥 반복이 새 제목으로 읽음
            if m and re.search(r"\d{4}\s*\.\s*\d{1,2}\s*\.", m.group("text")):
                j += 1  # "1) 입찰참가신청 등록 : 2026. 09. 03." 같은 일정 줄은 서류가 아님
                while j < len(lines) and re.match(r"^\s*[-·]", lines[j]):
                    j += 1
                continue
            if m:
                items.append(m.group("text").strip())
            elif not cur.strip():
                break
            elif items and (re.match(r"^\s*[-·(（※]", cur) and kind != "dash") and not _HEAD_LINE.match(cur.lstrip("※")):
                extra = re.sub(r"^\s*[-·]\s*", "", cur).strip()
                items[-1] += (" — " if not extra.startswith(("(", "（")) else " ") + extra
            else:
                break
            j += 1
        for body in items:
            key = _squash(body)
            if key not in seen:
                seen.add(key)
                out.append(Item(body, source, "", classify(body, catalog)))
        i = j
    return out


_AMOUNT = re.compile(r"(\d[\d,.]*)\s*(억|천만|백만|만)?\s*원")
_UNIT = {"억": 100_000_000, "천만": 10_000_000, "백만": 1_000_000, "만": 10_000, None: 1}
_YEARS = re.compile(r"(?:최근|기준)\s*(\d+)\s*(?:개\s*)?년|(\d+)\s*년\s*(?:이내|간|동안)")


def parse_track(text: str) -> tuple[int | None, int | None, bool]:
    """실적 문장 → (최근 N년, 최소 금액(원), 건당 여부). 못 찾으면 None."""
    m = _YEARS.search(text)
    years = int(m.group(1) or m.group(2)) if m else None
    amount = None
    for a in _AMOUNT.finditer(text):
        tail = text[a.end():a.end() + 15]
        if "이상" in tail:
            amount = int(float(a.group(1).replace(",", "")) * _UNIT[a.group(2)])
            break
    per_project = not re.search(r"누적|합산|합계|총\s*액", text)
    return years, amount, per_project


def _star_notes(lines: list[str], i: int) -> list[str]:
    """i번째 줄과 같은 '*' 묶음의 다른 줄(실적 인정 범위·공동수급 비율 등)."""
    if not lines[i].lstrip().startswith("*"):
        return []
    lo = i
    while lo > 0 and lines[lo - 1].lstrip().startswith("*"):
        lo -= 1
    hi = i
    while hi + 1 < len(lines) and lines[hi + 1].lstrip().startswith("*"):
        hi += 1
    return [lines[k].strip(" *\t") for k in range(lo, hi + 1) if k != i]


def read_track_reqs(text: str, source: str, text_requirements) -> list[TrackReq]:
    out, seen = [], set()
    lines = text.splitlines()
    for idx, ln in enumerate(lines):
        s = ln.strip(" -·•*\t")
        if "실적" not in s or not re.search(r"\d[\d,.]*\s*(?:억|천만|백만|만)?\s*원[^.\n]{0,15}?이\s*상", s):
            continue
        if re.search(r"배점|평점|점\s*\)|\d+\s*점", s):  # 평가 배점 줄은 요건이 아님(점수는 범위 밖)
            continue
        key = re.sub(r"\s+", "", s)
        if key in seen:
            continue
        seen.add(key)
        years, amount, per = parse_track(s)
        before = "\n".join(lines[max(0, idx - 40):idx])
        kind = "평가 실적 인정 기준" if re.search(r"평가\s*(?:항목|기준|방법)|배점|참고\s*사항", before) else "제출 요건"
        out.append(TrackReq(s, source, years, amount, per, text_requirements.summarize_flag({"kind": "실적", "text": s}),
                            kind, _star_notes(lines, idx)))
    return out


def read_notice(paths: list[Path], catalog: list[Document] | None = None) -> NoticeSheet:
    attachments, schedule_text, text_requirements = _import_nego()
    catalog = catalog or load_catalog()
    forms, submit, tracks, deadlines = [], [], [], []
    texts: dict[str, str] = {}
    for p in paths:
        try:
            text = attachments.extract_text(Path(p).read_bytes(), Path(p).suffix.lstrip(".").lower())
        except Exception:  # 못 읽는 첨부(옛 엑셀·보안 문서 등)는 건너뜀
            continue
        name = Path(p).name
        texts[name] = text
        got = read_forms(text, name, catalog)
        if got and not any(f.in_quant for f in got):  # 정량 묶음 제목이 없으면 제출서류 표의 '정량' 묶음으로
            q = submit_quant_forms(text)
            for f in got:
                f.in_quant = f.no in q
        forms += got
        submit += read_submit_items(text, name, catalog)
        tracks += read_track_reqs(text, name, text_requirements)
        deadlines += [(kind, when, name) for kind, when in schedule_text.extract_deadlines(text)]
    # 같은 실적 문장이 여러 문서에 있으면 한 번만
    uniq, seen = [], set()
    for t in tracks:
        k = re.sub(r"\s+", "", t.text)
        if k not in seen:
            seen.add(k)
            uniq.append(t)
    # 여러 문서에 같은 서식 번호가 있으면 처음 것(정량 묶음 여부는 합침)
    merged: dict[int, Form] = {}
    for f in forms:
        if f.no in merged:
            merged[f.no].in_quant |= f.in_quant
        else:
            merged[f.no] = f
    return NoticeSheet([Path(p).name for p in paths], sorted(merged.values(), key=lambda f: f.no), submit, uniq,
                       sorted(deadlines, key=lambda d: d[1]), texts)


# ── 회사 실적 후보 ────────────────────────────────────────────
def read_company_tracks(path: Path) -> list[dict]:
    from openpyxl import load_workbook

    ws = load_workbook(path, data_only=True)["실적목록"]
    rows = ws.iter_rows(values_only=True)
    head = [str(h or "") for h in next(rows)]
    out = []
    for row in rows:
        rec = dict(zip(head, row))
        if rec.get("사업명"):
            out.append(rec)
    return out


def _as_date(v) -> date | None:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return datetime.strptime(str(v)[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def track_candidates(req: TrackReq, tracks: list[dict], ref: date) -> list[dict]:
    """기간·금액 요건을 넘는 실적. 금액은 계약금액 × 지분율(비면 100%). 판단이 안 서는 칸은 '확인'으로 둠."""
    out = []
    for t in tracks:
        notes = []
        done = _as_date(t.get("준공일"))
        if req.years:
            if done is None:
                notes.append("준공일 없음")
            else:
                try:
                    since = ref.replace(year=ref.year - req.years)
                except ValueError:  # 2월 29일
                    since = ref.replace(year=ref.year - req.years, day=28)
                if done < since or done > ref:
                    continue
        amount = t.get("계약금액(원)")
        share = t.get("지분율(%)")
        try:
            counted = float(amount) * (float(share) / 100 if share not in (None, "") else 1)
        except (TypeError, ValueError):
            counted = None
            notes.append("금액 없음")
        if req.per_project and req.min_amount and counted is not None and counted < req.min_amount:
            continue
        if t.get("부가세") in (None, "", "모름"):
            notes.append("부가세 포함 여부 확인")
        out.append({**t, "인정 금액(원)": counted, "확인할 것": ", ".join(notes)})
    return out


# ── 엑셀로 ───────────────────────────────────────────────────
def write_sheet(sheet: NoticeSheet, out_dir: Path, company: Path | None = None, ref: date | None = None,
                name: str = "공고별_준비표.xlsx", tracks: list[dict] | None = None, extra=None) -> Path:
    """tracks: 회사 실적목록(이미 읽은 것 — 웹앱은 잠금 묶음에서). extra(wb, table): 시트를 더 붙이는 함수."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    ref = ref or date.today()
    head_fill, head_font = PatternFill("solid", fgColor="1B6E43"), Font(bold=True, color="FFFFFF")

    def table(ws, heads: list[str], rows: list[list], widths: list[int]):
        ws.append(heads)
        for c in ws[1]:
            c.fill, c.font = head_fill, head_font
        for r in rows:
            ws.append(r)
        for i, w in enumerate(widths):
            ws.column_dimensions[chr(65 + i)].width = w
        for row in ws.iter_rows(min_row=2):
            for c in row:
                c.alignment = Alignment(wrap_text=True, vertical="top")
        ws.freeze_panes = "A2"

    def kind_cells(it) -> list[str]:
        if it.doc is None:
            if any(w in re.sub(r"\s+", "", getattr(it, "text", "") or getattr(it, "name", "")) for w in COMMON_ISSUED):
                return [COMMON_LABEL, "서류상태"]
            return [FORM_ONLY, "—"]
        return [it.doc.name, SOURCE_SHEET.get(it.doc.id, "—")]

    wb = Workbook()
    ws = wb.active
    ws.title = "공고정보"
    info = [["읽은 파일", ", ".join(sheet.files)], ["실적 기준일", f"{ref:%Y-%m-%d} (공고일 — 모르면 오늘)"]]
    info += [[f"마감 · {k}", f"{w:%Y-%m-%d %H:%M} ({src})"] for k, w, src in sheet.deadlines]
    info += [["", ""], ["안내", "자동으로 뽑은 값입니다 — 원문과 다르면 원문이 맞습니다. 평가 점수는 계산하지 않습니다."]]
    table(ws, ["항목", "값"], info, [18, 90])

    table(wb.create_sheet("서식목록"), ["서식", "이름", "정량평가서", "서류 종류", "채울 곳(회사자료 양식)", "잘라 낸 파일", "출처"],
          [[f"서식{f.no}", f.name, "○" if f.in_quant else "", *kind_cells(f), f.file, f.source] for f in sheet.forms],
          [8, 40, 10, 28, 20, 34, 30])
    table(wb.create_sheet("제출서류"), ["번호", "제출서류", "서류 종류", "채울 곳(회사자료 양식)", "출처"],
          [[i, it.text, *kind_cells(it), it.source] for i, it in enumerate(sheet.submit_items, 1)],
          [6, 60, 30, 22, 36])
    table(wb.create_sheet("실적요건"), ["번호", "구분", "원문", "기간", "최소 금액(원)", "건당/누적", "같은 묶음 참고", "출처"],
          [[n, t.kind, t.text, f"최근 {t.years}년" if t.years else "못 찾음", t.min_amount or "못 찾음",
            "건당(단일)" if t.per_project else "누적(합산)", "\n".join(t.notes), t.source]
           for n, t in enumerate(sheet.track_reqs, 1)],
          [6, 18, 50, 10, 14, 10, 60, 30])
    ws = wb["실적요건"]
    for row in ws.iter_rows(min_row=2, min_col=5, max_col=5):
        for c in row:
            if isinstance(c.value, int):
                c.number_format = "#,##0"

    cand = wb.create_sheet("실적후보")
    if company is None and tracks is None:
        table(cand, ["안내"], [["회사자료 양식(--company)을 주면 실적목록에서 요건에 맞는 실적 후보를 여기에 채웁니다."]], [90])
    elif not sheet.track_reqs:
        table(cand, ["안내"], [["공고에서 실적 요건을 찾지 못했습니다 — 원문을 확인하세요."]], [90])
    else:
        tracks = tracks if tracks is not None else read_company_tracks(company)
        rows = []
        for n, req in enumerate(sheet.track_reqs, 1):
            found = track_candidates(req, tracks, ref)
            for t in found:
                rows.append([n, t.get("번호"), t.get("사업명"), t.get("발주기관"), _as_date(t.get("준공일")),
                             t.get("인정 금액(원)"), t.get("분야"), t.get("증빙 파일 이름"), t.get("확인할 것"), ""])
            if not req.per_project and found:  # 누적 요건은 기간 안 실적을 모두 보이고 합계만(충족 판정·점수는 안 함)
                total = sum(t["인정 금액(원)"] or 0 for t in found)
                rows.append([n, "", f"기간 안 실적 합계 (기준 {req.min_amount:,}원 — 인정 범위는 담당자 확인)"
                             if req.min_amount else "기간 안 실적 합계", "", None, total, "", "", "", ""])
        if not rows:
            rows = [["", "", "요건에 맞는 실적이 없습니다", "", "", "", "", "", "", ""]]
        table(cand, ["요건 번호", "실적 번호", "사업명", "발주기관", "준공일", "인정 금액(원)", "분야", "증빙 파일",
                     "확인할 것", "제출 여부(담당자)"], rows, [8, 8, 36, 20, 12, 16, 14, 28, 22, 14])
        for row in cand.iter_rows(min_row=2):
            row[4].number_format, row[5].number_format = "yyyy-mm-dd", "#,##0"

    if extra:
        extra(wb, table)
    out = Path(out_dir) / name
    out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out)
    return out
