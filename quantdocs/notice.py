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
    weak: bool = False  # 범위·칸 풀린 표·깨진 이름 — 다른 문서의 더 구체적인 이름(서식 본문 제목)으로 바꿀 수 있음


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

# ── 서식 목록: 적는 방식 ─────────────────────────────
# ① 표 한 줄: "- 일반현황 및 연혁 | 서식14" (hwpx 표가 한 줄로 읽힘)
# ② 표 칸이 줄마다 풀림(옛 hwp): "- 제안사 연혁 및 일반현황" 다음 줄 "서식12" (또는 "서식28" / "~31"),
#    "4. 사업수행실적" 다음 줄 "[서식5호, 6호]"(11/04 당진)
# ③ 목록·목차 줄 안: "3) 참여전문기술자 보유현황(서식 9)", "응모신청서 제출 1부 < 서식 1 >",
#    "▪사업수행능력 평가서 3부(A4) [서식2~10호]"(11/04 당진)
# ④ 서식 목차 표 "[서식 N] | 이름"
# ⑤ 서식 본문 머리 "〔서식 3호〕" 다음 줄 제목 — 목록이 범위("2~10호")로만 적었거나 없을 때 이름을 채움
# 번호 표기: "서식 3", "서식 3호", "서식 제3호", "서식2~10호", "서식5호, 6호", "서식7, 8호", "서식4-1"(4번의 하위 서식)
_NO = r"서식\s*(?:제\s*)?\d+\s*호?(?:\s*-\s*\d+)?(?:\s*[~∼,]\s*(?:서식\s*)?(?:제\s*)?\d+\s*호?)*"
_SYM = r"[^\w\s|]"  # 서식 번호를 감싸는 특수기호 하나 — 괄호 모양을 가리지 않음(11/04 사용자 요청: 〈서식 11〉 등)
_FORM_CELL = re.compile(rf"^\s*(?:{_SYM}\s*)?(?P<nos>{_NO})\s*(?:{_SYM})?\s*$")
_FORM_INLINE = re.compile(rf"{_SYM}\s*(?P<nos>{_NO})\s*{_SYM}")
_FORM_HEAD = re.compile(rf"^\s*{_SYM}\s*서\s*식\s*(?:제\s*)?(\d+)\s*호?(?P<sub>\s*-\s*\d+)?\s*{_SYM}\s*(?P<rest>.*)$")
_LIST_MARK = re.compile(rf"^\s*(?:\d+\s*[).]|[{_CIRCLED}]|[-·∙•▪]|[가-하]\.)\s*")


def _span_nos(nos: str) -> list[int]:
    """'서식2~10호' → 2..10(15개까지), '서식5호, 6호' → [5, 6], '서식4-1' → [4]."""
    nos = re.sub(r"(\d)\s*-\s*\d+", r"\1", nos)
    nums = [int(x) for x in re.findall(r"\d+", nos)]
    if len(nums) == 2 and re.search(r"[~∼]", nos) and nums[1] > nums[0]:
        return list(range(nums[0], min(nums[1], nums[0] + 15) + 1))
    return list(dict.fromkeys(nums))


def _squash(s: str) -> str:
    return re.sub(r"\s+", "", s)


def _clean_name(name: str) -> str:
    name = _LIST_MARK.sub("", name.strip())
    head = re.split(rf"\s*[{_CIRCLED}]", name)[0]  # "참가자격을 입증할 서류 ① 사업자등록증 사본, …" → 앞 이름만(11/04 필암서원)
    if len(_squash(head)) >= 4:
        name = head
    name = re.split(r"\s*※", name)[0]  # "… ※ 응모자 표기 1부" 꼬리 설명
    name = re.sub(r"\s+(?:각\s*)?\d+\s*부\b.*$|\s*(?:각\s*)?\d+\s*부\.?\s*(?:\([^)]*\))?\s*$", "", name)  # "… 1부", "… 10부(A3)", "… 10부 (A4 규격 30매 이내)"
    name = re.sub(r"\s*(?:제출|작성)\s*$", "", name)
    return re.sub(r"\s+", " ", name).strip(" -:·,")


def _title_key(s: str) -> str:
    return re.sub(r"[^가-힣A-Za-z0-9]", "", re.sub(r"\[[^\]]*\]|\.[A-Za-z]+$", "", s))


def _head_title(lines: list[str], i: int, rest: str, source: str) -> str:
    """서식 머리 뒤 제목: 같은 줄 나머지, 없으면 다음 줄들 중 처음 제목다운 줄.
    '<편집용지>' 같은 안내, 공고명(파일 이름과 같은 줄 — 표지 서식 맨 위에 흔함)은 건너뜀."""
    src = _title_key(source)
    cands = [rest] + lines[i + 1:i + 6]
    for ln in cands:
        t = ln.split("|")[0].strip(" -·")
        tokens = t.split()
        if len(tokens) >= 2 and sum(len(x) == 1 for x in tokens) >= len(tokens) - 1:
            t = "".join(tokens)  # '청  렴  서  약  서' → '청렴서약서'
        t = re.split(r"\s{3,}", t)[0].strip()
        k = _title_key(t)
        if not k or re.fullmatch(r"<[^>]*>", t) or re.match(r"^(접수\s*번호|공고\s*번호|구\s*분|※|주\)|\d+\s*[.)])", t):
            continue
        if len(k) >= 8 and k in src:
            continue
        if 2 <= len(k) <= 30 and not re.search(r"서식\s*\d", t):
            return t
    return ""


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
    """서식 번호 → 이름. 같은 번호가 여러 번 나오면 처음 이름을 쓰고, 정량 묶음 여부는 하나라도 있으면 참.
    단 '사업수행능력 평가서 (2~10)'처럼 범위로만 적은 이름·칸이 풀린 표에서 짐작한 이름(약한 이름)은
    뒤에 나오는 더 구체적인 이름(서식 본문 머리의 제목 등)으로 바꿈."""
    lines = text.splitlines()
    quant = _quant_range(lines)
    found: dict[int, Form] = {}
    weak: set[int] = set()  # 범위 이름·칸 풀린 표 이름 — 본문 제목으로 바꿀 수 있음

    def add_one(n: int, name: str, i: int, is_weak: bool = False, in_quant: bool = False) -> bool:
        name = _clean_name(name)
        if not name or len(name) > 60 or re.search(r"서식\s*\d", name):
            return False
        # 배점·숫자만 있는 칸("0.3점", "10%", "3부")은 이름이 아님 — 평가표 칸이 서식 칸 앞에 풀려 나올 때(11/04)
        if len(re.sub(r"[^가-힣A-Za-z]", "", re.sub(r"\d[\d.,]*\s*(?:점|%|원|억원?|천만원|백만원|만원|개|건|년|부|매|쪽|명|인)", "", name))) < 2:
            return False
        if re.match(r"^\d+\s", name) or name.count("(") > name.count(")"):  # '8 위임장 … (대리인이 등록할 경우' — 표 칸이 섞인 이름
            is_weak = True
        f = found.get(n)
        if f is None:
            found[n] = Form(n, name, source, i in quant or in_quant, classify(name, catalog))
            if is_weak:
                weak.add(n)
        else:
            if n in weak and not is_weak:
                f.name, f.doc = name, classify(name, catalog)
                weak.discard(n)
            if i in quant or in_quant:
                f.in_quant = True
        return True

    def add(span: list[int], names: list[str], i: int, is_weak: bool = False) -> bool:
        """서식 번호들에 이름들을 붙임. 번호 수와 이름 수가 같으면 차례로, 아니면 모두 '이름 (a~b)'(약한 이름).
        범위 이름이 '사업수행능력 평가서'·'정량'이면 그 번호들은 정량 묶음."""
        names = [x for x in names if _clean_name(x)]
        if not names or not span:
            return False
        if len(span) > 1 and len(names) == 1:  # "청렴서약서, 서약서 [서식11~12호]" — 이름도 쉼표로 나뉘면 차례로
            parts = [x for x in re.split(r"\s*[,，]\s*|\s+및\s+", _clean_name(names[0])) if x]
            if len(parts) == len(span):
                names = parts
        if len(span) > 1 and len(names) == len(span):
            return all([add_one(n, x, i, is_weak) for n, x in zip(span, names)])
        if len(span) == 1:
            return add_one(span[0], names[-1], i, is_weak)
        base = _clean_name(names[-1])
        sep = "~" if span == list(range(span[0], span[-1] + 1)) and len(span) > 2 else "·"
        label = f"{base} ({span[0]}{sep}{span[-1]})" if sep == "~" else f"{base} ({'·'.join(map(str, span))})"
        q = bool(re.search(r"정량|사업수행능력(?:평가서|평가자료)", _squash(base)))
        return all([add_one(n, label, i, True, q) for n in span])

    for i, ln in enumerate(lines):  # ④ 서식 목차 표 "[서식 N] | 이름" — 이름이 가장 정확해서 먼저
        m = re.match(rf"^\s*(?:{_SYM}\s*)?서식\s*(?:제\s*)?(\d+)\s*호?\s*(?:-\s*\d+)?\s*(?:{_SYM}\s*)?\|\s*([^|]+)", ln)
        if m:  # "[서식 4-1] | 공동수급표준협정서(공동이행)" → 서식4 = 공동수급표준협정서, "서식 1호 | 표지 |"(11/03 영동군)
            name = re.sub(r"\s*\([^)]*\)\s*$", "", m.group(2)) if "-" in ln.split("|")[0] else m.group(2)
            name = re.split(r"\s※", name)[0]  # "… 서약서 ※ 서식 16호 이행 서약서는 …" 꼬리 설명
            add([int(m.group(1))], [name], i)

    pending: list[str] = []  # 칸이 풀린 표에서 '서식N' 앞에 쌓인 '- 이름' 줄들
    for i, ln in enumerate(lines):
        if "|" in ln:  # ① 표 한 줄 — 마지막 칸이 서식, 그 앞 칸이 이름(여러 개면 ' - '로 나뉨, 너무 길면 한 칸 더 앞)
            cells = [c.strip() for c in ln.split("|")]
            m = _FORM_CELL.match(cells[-1]) if len(cells) >= 2 else None
            if m and "호" in cells[-1]:  # '구비서류 | 서식 2호' 제출서류 표는 read_submit_items가 읽음(11/03 영동군 — 목록은 목차 표로)
                pending = []
                continue
            if m:
                span = _span_nos(m.group("nos"))
                parts = [x for x in re.split(r"\s+-\s+|^-\s*", cells[-2]) if x.strip()]
                if not add(span, parts, i) and len(cells) >= 3:
                    add(span, [cells[-3]], i)
                pending = []
                continue
        m = _FORM_CELL.match(ln)
        if m and pending:  # ② 칸이 풀린 표 — 다음 줄이 "~31"이면 범위
            span = _span_nos(m.group("nos"))
            k = i + 1
            while k < len(lines) and not lines[k].strip():
                k += 1
            r = re.match(r"^\s*[~∼]\s*(\d+)\s*$", lines[k]) if k < len(lines) else None
            if r and len(span) == 1:
                add(_span_nos(f"서식{span[0]}~{r.group(1)}"), pending, i)
            else:
                add(span, pending[-1:], i, is_weak=bool(re.match(r"^\s*\d+\s*[.)]", pending[-1])))
            pending = []
            continue
        if re.match(r"^\s*[~∼]\s*\d+\s*$", ln):
            continue
        m = _FORM_INLINE.search(ln)
        if m and _LIST_MARK.match(ln) and len(_squash(ln[m.end():])) <= 6:  # ③ 목록 줄 끝의 (서식 N)
            add(_span_nos(m.group("nos")), [ln[:m.start()]], i)
            pending = []
            continue
        if re.match(r"^\s*(?:[-∙•▪]\s*\S|\d+\s*[.)]\s*[가-힣])", ln):
            pending.append(ln)
        elif ln.strip() and not re.match(r"^\s*(?:\d+\s*부|[※·(]|구\s*분|부\s*수|항\s*목|작\s*성)", ln):
            pending = []

    # ⑤ 서식 본문 머리 — 같은 번호는 뒤(본문) 것. 목록에 없거나 약한 이름만 채움(목록·목차 표 이름은 그대로)
    heads: dict[int, tuple[int, str]] = {}
    for i, ln in enumerate(lines):
        m = _FORM_HEAD.match(ln)
        if m and "|" not in ln and not m.group("sub"):  # '〔서식 4-1〕' 하위 서식은 4번 이름으로 안 씀
            heads[int(m.group(1))] = (i, m.group("rest"))
    for n, (i, rest) in sorted(heads.items()):
        if n in found and n not in weak:
            continue
        title = _head_title(lines, i, rest, source)
        if title:
            if n in found:
                f = found[n]
                f.name, f.doc = _clean_name(title), classify(title, catalog)
                weak.discard(n)
            else:
                add_one(n, title, i)
    # ⑥ PDF 머리 '서식 일반현황서식 일반현황[ 2]'·'서식 서식 [ 1]  제안서 표지제안서 표지'(11/05 창업진흥원 제안요청서 — 번호가 이름 뒤,
    # 굵은 글씨가 두 번 찍혀 글이 겹침). 하위 서식 '[ 4-1]~[ 4-3]'은 4번 하나로 묶어 이름을 이음
    subs: dict[int, list[str]] = {}
    for i, ln in enumerate(lines):
        m = re.match(r"^\s*서식\s*(?P<name>[^\[\]|]*?)\s*\[\s*(?P<no>\d+)\s*(?:-\s*(?P<sub>\d+))?\s*\]\s*(?P<rest>.*)$", ln)
        if not m:
            continue
        name = _undouble(re.split(r"서식", m.group("name"))[0]) or _undouble(re.split(r"\s{2,}|\[", m.group("rest").strip())[0] if m.group("rest").strip() else "")
        if not name or re.search(r"[<>]", name):
            continue
        n = int(m.group("no"))
        if m.group("sub"):
            if name not in subs.setdefault(n, []):
                subs[n].append(name)
            continue
        if n not in found or n in weak:
            if n in found:
                found[n].name, found[n].doc = _clean_name(name), classify(name, catalog)
                weak.discard(n)
            else:
                add_one(n, name, i)
    for n, names in subs.items():
        if n not in found:
            add_one(n, " · ".join(names) + f" ({n}-1~{n}-{len(names)})" if len(names) > 1 else names[0], 0)
    for n in weak:
        found[n].weak = True
    return sorted(found.values(), key=lambda f: f.no)


def _undouble(s: str) -> str:
    """PDF 굵은 글씨가 두 번 찍혀 '제안서 표지제안서 표지'처럼 겹친 글 → 한 번만. 겹치지 않았으면 그대로."""
    s = re.sub(r"\s+", " ", s or "").strip(" -·")
    for k in range(len(s) // 2, 1, -1):
        a, b = s[:k].strip(), s[k:].strip()
        if a and a == b:
            return a
    return s


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
        a, _, b = (re.split(r"\s*([~∼])\s*", part) + ["", ""])[:3] if re.search(r"[~∼]", part) else (part, "", "")
        a, b = int(a), int(b) if b else int(a)
        out += list(range(a, min(b, a + 30) + 1))
    return out


def _split_docs(cell: str) -> list[str]:
    """한 칸에 여러 서류: '참가자격을 입증할 서류 ① 사업자등록증 사본, 법인등기부등본, …, 사용인감계 각 1부, 직접생산확인증명서(실물모형)
    ② 공동수급인 경우 : …' → ①의 서류들(공동수급·조건부 ②부터는 뺌)(11/04 필암서원)."""
    if not re.search(rf"[{_CIRCLED}]", cell):
        return [cell]
    first = re.split(rf"\s*[{_CIRCLED}]\s*", cell)
    part = first[1] if len(first) > 1 else cell
    out = []
    for x in re.split(r"\s*[,，]\s*(?![^()]*\))", part):
        x = re.sub(r"\s*각\s*\d+\s*부\s*$", "", x).strip(" .")
        if x:
            out.append(x)
    return out


def _grid_table(lines: list[str], j: int) -> tuple[list[tuple[str, str]], int]:
    """'구 분 | 연번 | 제 출 서 류 | 비 고' 같은 표 — 머리줄에서 '서류' 칸을 찾아 아래 줄마다 그 칸(오른쪽 기준, 앞 칸이 합쳐진 줄도)을
    서류로, 마지막 칸의 '서식 N'을 서식 번호로(11/04 필암서원 공고문)."""
    head = [_squash(c) for c in lines[j].split("|")]
    idx = next((i for i, c in enumerate(head) if re.search(r"서류|서류명|제출물", c)), None)
    if idx is None or len(head) < 2:
        return [], j
    from_right = len(head) - idx
    k, items = j + 1, []
    while k < len(lines) and "|" in lines[k]:
        cells = [c.strip() for c in lines[k].split("|")]
        if len(cells) >= from_right:
            body = cells[len(cells) - from_right]
            nos = ",".join(str(n) for n in _form_nos(cells[-1])) if from_right > 1 else ""
            for doc in _split_docs(body):
                items.append((doc, nos))
        k += 1
    return items, k


def _submit_table(lines: list[str], j: int) -> tuple[list[tuple[str, str]], int]:
    """'구비서류 | 서식번호' 표(11/03 영동군): 머리줄 다음 '• 묶음 | 서식' / ' - 서류 | 서식 N호' 줄들. (서류 글, 서식 번호 글), 끝난 줄.
    그 모양이 아니면 '구 분 | 연번 | 제 출 서 류 | 비 고' 칸 표(_grid_table)."""
    if not re.search(r"서식|비고|구분|서류", _squash(lines[j])):
        return [], j
    if j + 1 < len(lines) and "|" in lines[j + 1] and not re.match(r"^\s*[-•·∙▪○●]", lines[j + 1]):
        return _grid_table(lines, j)
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


# 서류 이름처럼 끝나는지(…서·…증·…본·…표·…도·…목록·…계·…장·…점(USB 1점)·…자료·…파일·영문 약자) — 문장('…경우', '…한다.')이나
# 절 제목('저작권', '공통 사항')은 서류가 아님(11/04 사용자 제보: 실격 사유·도면 작성 요령이 제출서류로 보였음)
_SENTENCE_END = re.compile(r"(?:경우|한다|하여야|할\s*것|함|됨|않음|가능|불가|있음|없음|바람|하시기)\s*[.)）]*\s*$")
_NOT_DOC = re.compile(r"^(?:저작권|지식재산권|공통\s*사항|기타\s*사항|유의\s*사항|일반\s*사항|참고\s*사항|주의\s*사항|기타)$")


def _doc_like(body: str) -> bool:
    core = body.split(" — ")[0]
    core = re.split(r"\s*※", core)[0]
    core = re.sub(r"\s*(?:각\s*)?\d+\s*(?:부|점|매|권|식)\b.*$", "", core)  # '… 1부(원본)', 'USB 1점(…)'
    core = re.sub(r"\s*[(（][^)）]*[)）]?\s*$", "", core).strip()  # 꼬리 괄호
    if not core or _SENTENCE_END.search(core):  # '위임장 및 재직증명서(대리인이 등록할 경우에 한함)'은 괄호를 뗀 뒤 봄
        return False
    return not _NOT_DOC.match(core)  # 서류 이름 끝말로 고르면 '일반현황 및 연혁' 같은 서식이 빠져서 — 문장·절 제목만 뺌


def read_submit_items(text: str, source: str, catalog: list[Document]) -> list[Item]:
    """'제출서류'·'구비서류'·'제출도서'·'필요서류' 제목 바로 아래 목록. 첫 항목의 번호 모양(①/1)/-)과 같은 줄만 항목으로,
    그보다 깊은 줄(①·1) 아래 '-' 줄, 괄호 줄)은 앞 항목에 붙임."""
    lines = text.splitlines()
    out, seen = [], set()
    i = 0
    while i < len(lines):
        ln = lines[i]
        head = _SUBMIT_HEAD.search(ln)
        if not head or len(_squash(ln)) > 40 or re.search(r"무효|실격|반환|탈락", ln):  # '제출서류 무효·실격' 절은 서류 목록이 아님
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
                    if not _doc_like(body):
                        continue
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
            if not _doc_like(body):
                continue
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
    # 여러 문서에 같은 서식 번호가 있으면 처음 것(정량 묶음 여부는 합침) — 단 처음 것이 약한 이름(범위 '자료 (11~19)' 등)이고
    # 뒤 문서(제안요청서 서식 본문 머리)에 구체적인 이름이 있으면 그 이름(11/04 필암서원: 공고문 목록이 먼저 읽혀 본문 제목을 덮음)
    merged: dict[int, Form] = {}
    for f in forms:
        if f.no in merged:
            m = merged[f.no]
            m.in_quant |= f.in_quant
            if m.weak and not f.weak:
                m.name, m.doc, m.source, m.weak = f.name, f.doc, f.source, False
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
        pass
    # '2011.09.01'·'2011/09/01'·'2011년 9월 1일'처럼 글자로 적힌 날짜(11/05 받은 기술인력표)
    m = re.match(r"\s*((?:19|20)\d{2})\s*[.\-/년]\s*(\d{1,2})\s*[.\-/월]\s*(\d{1,2})", str(v or ""))
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    return None


def unfinished(tracks: list[dict], ref: date) -> list[str]:
    """기준일(공고 게시일)까지 끝나지 않은 실적 이름 — 준공일이 없거나 기준일 뒤(사업기간 중). 수행실적은 준공된 것만 인정(11/04 사용자 확인)."""
    return [str(t.get("사업명") or "") for t in tracks
            if (d := _as_date(t.get("준공일"))) is None or d > ref]


def _since(ref: date, years: int) -> date:
    try:
        return ref.replace(year=ref.year - years)
    except ValueError:  # 2월 29일
        return ref.replace(year=ref.year - years, day=28)


def track_candidates(req: TrackReq, tracks: list[dict], ref: date) -> list[dict]:
    """기간·금액 요건을 넘는 실적. 금액은 계약금액 × 지분율(비면 100%). 판단이 안 서는 칸은 '확인'으로 둠.
    준공되지 않은 실적(준공일 없음·기준일 뒤)은 기간 요건과 상관없이 뺌 — `unfinished`로 알림."""
    out = []
    for t in tracks:
        notes = []
        done = _as_date(t.get("준공일"))
        if done is None or done > ref:
            continue
        if req.years and done < _since(ref, req.years):
            continue
        amount = t.get("계약금액(원)")
        share = t.get("지분율(%)")
        try:
            counted = float(amount) * (float(share) / 100 if share not in (None, "") else 1)
        except (TypeError, ValueError):
            counted = None
            notes.append("금액 없음")
        # 금액 하한('단일 30백만원 이상')·분야로는 거르지 않음 — 기간 조건만 맞으면 전부(11/05 사용자 요청)
        if t.get("부가세") in (None, "", "모름"):
            notes.append("부가세 포함 여부 확인")
        out.append({**t, "인정 금액(원)": counted, "확인할 것": ", ".join(notes)})
    return out


# ── 실적 분야·서식에 맞는 실적(11/04 사용자 결정) ────────────────
# 공사수주현황엔 분야 칸이 없어 사업명으로 나눔 — 투찰 계산기와 같은 규칙(nego.bidfield, 공고명 키워드)에
# 그 규칙이 놓치는 전시 낱말을 더함. 규칙 파일(config/bid_fields.json)은 투찰 계산기도 쓰므로 여기서만 보탬.
_EXHIBIT_EXTRA = re.compile(r"디오라마|실감\s*콘텐츠|히스토리\s*월|관광\s*콘텐츠")
EXHIBIT = "전시·체험"
# 서식 제목 낱말 → 넣을 실적 분야(사용자 결정: 전시분야엔 영상·미디어·조형물도, 유지보수·설계 용역도 넣고 아니면 담당자가 지움)
_FORM_FIELDS = [
    (re.compile(r"전시|체험"), {EXHIBIT, "영상·미디어", "조형물·경관"}),
    (re.compile(r"조형|경관"), {"조형물·경관"}),
    (re.compile(r"영상|미디어|콘텐츠"), {"영상·미디어", EXHIBIT}),
    (re.compile(r"놀이|조경"), {"놀이·조경"}),
]


def track_field(name: str) -> str:
    """사업명 → 분야 이름(투찰 계산기 분야 + 전시 보탬 낱말)."""
    _import_nego()
    from nego import bidfield
    f = bidfield.classify(name or "")
    if f != EXHIBIT and _EXHIBIT_EXTRA.search(name or ""):
        return EXHIBIT
    return f


def form_tracks(title: str, tracks: list[dict], ref: date) -> tuple[list[dict], list[str]]:
    """실적 서식 제목("전시분야 최근 3년간 사업수행실적")으로 넣을 실적 고르기.
    기간: 제목의 '최근 N년'(준공일 기준, 준공일 없는 실적은 빼고 알림). 분야로는 거르지 않음(11/05 사용자 요청 — 예전엔 제목 낱말로 분야를 골랐음).
    순서: 준공일 오래된 것부터(서식 주석 '연도순'). 돌려줌: (실적, 알림 줄)."""
    m = re.search(r"최근\s*(\d+)\s*(?:개\s*)?년", title or "")
    years = int(m.group(1)) if m else None
    fields = next((fs for rx, fs in _FORM_FIELDS if rx.search(title or "")), None)
    out, no_done, ongoing, notes = [], [], [], []
    del fields  # 분야로는 거르지 않음 — 기간 조건만 맞으면 전부(11/05 사용자 요청: '3년 내·5년 내 조건 아래 전체 수집')
    for t in tracks:
        done = _as_date(t.get("준공일"))
        # 수행실적은 준공된 것만(11/04 사용자 확인) — 기간 요건이 없는 서식이어도 진행 중·준공일 없는 실적은 뺌
        if done is None:
            no_done.append(str(t.get("사업명") or ""))
            continue
        if done > ref:
            ongoing.append(str(t.get("사업명") or ""))
            continue
        if years and done < _since(ref, years):
            continue
        out.append(t)
    out.sort(key=lambda t: _as_date(t.get("준공일")) or date.max)
    for label, names in (("준공일이 없어", no_done), (f"기준일({ref:%Y-%m-%d})까지 준공 전이라", ongoing)):
        if names:
            notes.append(f"{label} 뺀 실적 {len(names)}건: " + ", ".join(names[:5]) + (" 외" if len(names) > 5 else ""))
    return out, notes


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
