"""공고 문서(HWPX) 안의 첨부 서식([서식 N] · 〔서식 N〕 …)을 서식마다 한 파일로 잘라 낸다.

    python -m quantdocs forms 제안요청서.hwpx --out "<폴더>"

- 문단 단위로 자른다: 서식 제목 문단부터 다음 서식 제목 문단 전까지(같은 구역 안). 표·그림·글꼴·쪽 설정은 원본 그대로 —
  구역의 쪽 설정(첫 문단의 secPr)은 잘라 낸 파일 맨 앞에 남긴다.
- 서식 목차 표처럼 한 문단(표)에 서식 제목이 여러 개 있는 곳은 본문이 아니라서 건너뛴다. 같은 번호가 여러 번 나오면 마지막(본문) 것.
- 옛 한글(.hwp)은 직접 못 자른다 — 한글에서 'HWPX로 저장'하거나 hwp2hwpx로 바꾼 뒤 이 명령을 쓴다.
- 결과는 공고 서식(공개 자료)뿐이라 회사 정보는 들어가지 않는다. 회사 정보 채우기는 다음 단계.
"""

from __future__ import annotations

import html
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path

_TAG = re.compile(r"<(/?)((?:[\w.-]+:)?)(p|run|t|secPr)\b([^>]*?)(/?)>")
# 서식 머리: 특수기호 하나 + 서식 N(호)(-M) + 특수기호 하나 — 괄호 모양을 가리지 않음(11/04 사용자 요청)
# '[서식 1]'·'〔서식 1호〕'·'【별지서식 4-1】'·'[별지 제1호 서식]'(11/10 경남교육청 학생안전체험원 — '별지'가 끼면 못 잘랐음)
_HEAD = re.compile(r"^[^\w\s]\s*(?:별\s*지\s*)?(?:서식\s*(?:제\s*)?(\d+)\s*호?\s*(?:[-–]\s*(\d+))?|제\s*(\d+)\s*호\s*(?:[-–]\s*(\d+))?\s*서식)\s*[^\w\s]")


# '【양식1】'(11/13 한국산업단지공단 제안요청서 — '서식' 대신 '양식'), 번호 없는 '【별첨】'(양식 목록 끝 묶음)
_YHEAD = re.compile(r"^[^\w\s]\s*양식\s*(?:제\s*)?(\d+)\s*호?\s*(?:[-–]\s*(\d+))?\s*[^\w\s]")
_APPX = re.compile(r"^[^\w\s]\s*별첨\s*[^\w\s]")


@dataclass
class Para:
    start: int
    end: int
    text: str


@dataclass
class CutForm:
    no: str  # "4-1"처럼 하위 번호 포함
    title: str
    section: str
    paras: list[Para]
    file: str = ""


def _top_paragraphs(xml: str) -> list[Para]:
    """구역 XML의 맨 바깥 문단(<?:p>)들 — 표 안 문단은 바깥 문단 안에 포함."""
    out, depth, start = [], 0, 0
    for m in _TAG.finditer(xml):
        closing, _, name, _, selfclose = m.groups()
        if name != "p":
            continue
        if not closing and not selfclose:
            if depth == 0:
                start = m.start()
            depth += 1
        elif closing:
            depth -= 1
            if depth == 0:
                seg = xml[start:m.end()]
                out.append(Para(start, m.end(), _text(seg)))
        elif selfclose and depth == 0:
            out.append(Para(m.start(), m.end(), ""))
    return out


def _text(seg: str) -> str:
    parts = re.findall(r"<(?:[\w.-]+:)?t\b[^>]*>(.*?)</(?:[\w.-]+:)?t>", seg, flags=re.S)
    return html.unescape(re.sub(r"<[^>]+>", "", " ".join(parts)))


def _squash(s: str) -> str:
    return re.sub(r"\s+", "", s)


def _sec_pr_para(xml: str, first: Para) -> str:
    """구역 첫 문단에서 쪽 설정(secPr·단 설정 ctrl)이 든 run만 남긴 문단 — 원래 글·표는 뺌."""
    seg = xml[first.start:first.end]
    runs, depth, start = [], 0, 0
    for m in _TAG.finditer(seg):
        closing, _, name, _, selfclose = m.groups()
        if name != "run":
            continue
        if not closing and not selfclose:
            if depth == 0:
                start = m.start()
            depth += 1
        elif closing:
            depth -= 1
            if depth == 0:
                runs.append((start, m.end()))
    keep = [seg[a:b] for a, b in runs if re.search(r"<(?:[\w.-]+:)?secPr\b", seg[a:b])]
    if not keep or not runs:
        return seg
    # secPr run 안의 글(t)은 지움 — 원래 표지 글이 따라오지 않게
    kept = re.sub(r"(<(?:[\w.-]+:)?t\b[^>]*>).*?(</(?:[\w.-]+:)?t>)", r"\1\2", keep[0], flags=re.S)
    return seg[:runs[0][0]] + kept + seg[runs[-1][1]:]


_INNER_P = re.compile(r"<(?:[\w.-]+:)?p\b[^>]*>(?:(?!<(?:[\w.-]+:)?p\b).)*?</(?:[\w.-]+:)?p>", re.S)
_RUN = re.compile(r'<(?:[\w.-]+:)?run\b[^>]*?charPrIDRef="(\d+)"[^>]*>(.*?)</(?:[\w.-]+:)?run>', re.S)
_NOT_TITLE = re.compile(r"\d{4}\s*년|년\s*월\s*일|절\s*취\s*선|^\s*(?:※|주\s*\)|붙\s*임|접\s*수\s*번\s*호)|(?:귀\s*하|귀\s*중)\s*$")


def char_heights(zin: zipfile.ZipFile) -> dict[str, int]:
    """header.xml 글자 모양 id → 글자 크기(height, 1/100pt)."""
    try:
        h = zin.read("Contents/header.xml").decode("utf-8")
    except KeyError:
        return {}
    out = {}
    for m in re.finditer(r"<(?:[\w.-]+:)?charPr\b([^>]*)>", h):
        a = dict(re.findall(r'(\w+)="([^"]*)"', m.group(1)))
        if a.get("id") and a.get("height", "").isdigit():
            out[a["id"]] = int(a["height"])
    return out


def page_title(body_xml: str, heights: dict[str, int], source: str = "") -> str:
    """서식 페이지 전체(표 안 포함)에서 가장 큰 글씨 = 제목(11/04 사용자 제안 — '다음 줄' 짐작은 날짜·공고명을 잡았음).
    같은 크기로 바로 이어지는 문단(줄이 넘어간 제목)은 이어 붙임. 서식 머리·날짜('2026년 월 일')·절취선·안내(※·주))·
    '○○ 귀하'·「공고명」은 빼고, 파일 이름에 있는 공고명 낱말도 뺌('정선군 … 제작설치 제안서' → '제안서').
    제목이 본문보다 크지 않으면 ''(짐작하지 않음)."""
    src = re.sub(r"[^가-힣A-Za-z0-9]", "", re.sub(r"\.[A-Za-z]+(?=\s|$)", "", source))
    keyof = lambda t: re.sub(r"[^가-힣A-Za-z0-9]", "", t)
    paras = []  # (글자 크기, 글, 제목 후보인지)
    for m in _INNER_P.finditer(body_xml):
        parts, h = [], 0
        for cid, inner in _RUN.findall(m.group()):
            t = _text(inner)
            if t.strip():
                parts.append(t)
                h = max(h, heights.get(cid, 0))
        text = re.sub(r"[「『][^」』]*[」』]", " ", " ".join(parts)).strip()
        if not keyof(text):
            continue
        ok = not (_HEAD.match(_squash(text)) or _NOT_TITLE.search(text))
        paras.append((h, text, ok))
    if not paras:
        return ""
    sizes = [h for h, _, _ in paras]
    common = min(set(sizes), key=lambda x: (-sizes.count(x), x))  # 본문 크기 = 가장 많이 쓴 크기(같으면 작은 쪽)
    best = max((h for h, _, ok in paras if ok), default=0)
    if best <= common:  # 제목이 따로 크지 않은 서식
        return ""
    i = next(k for k, (h, _, ok) in enumerate(paras) if ok and h == best)
    lines = [paras[i][1]]
    for h, t, ok in paras[i + 1:i + 4]:  # 줄이 넘어간 제목
        if h != best or not ok:
            break
        lines.append(t)
    words = " ".join(lines).split()
    if len(src) >= 8:  # 파일 이름에 이어서 나오는 낱말 묶음(공고명)은 뺌
        span = None
        for a in range(len(words)):
            for b in range(len(words), a, -1):
                run = keyof("".join(words[a:b]))
                if len(run) >= 8 and run in src:
                    if span is None or b - a > span[1] - span[0]:
                        span = (a, b)
                    break
        if span:
            words = words[:span[0]] + words[span[1]:]
    title = _norm_title(" ".join(words))
    return title if 2 <= len(keyof(title)) <= 40 else ""


def page_titles(path: Path, source: str = "") -> dict[int, str]:
    """HWPX 공고 문서의 서식마다 페이지 제목(가장 큰 글씨) — 서식 번호 → 제목. 하위 서식(4-1)·제목을 못 고른 서식은 뺌."""
    zin = zipfile.ZipFile(path)
    heights = char_heights(zin)
    if not heights:
        return {}
    sections = {n: zin.read(n).decode("utf-8") for n in zin.namelist() if re.fullmatch(r"Contents/section\d+\.xml", n)}
    out = {}
    for f in find_forms(sections):
        if "-" in f.no:
            continue
        xml = sections[f.section]
        t = page_title(xml[f.paras[0].start:f.paras[-1].end], heights, source)
        if t:
            out[int(f.no)] = t
    return out


def find_forms(sections: dict[str, str], heights: dict[str, int] | None = None, source: str = "") -> list[CutForm]:
    found: dict[str, CutForm] = {}
    order: list[str] = []
    for name, xml in sections.items():
        paras = _top_paragraphs(xml)
        heads = []
        for idx, p in enumerate(paras):
            sq = _squash(p.text)
            m = _HEAD.match(sq)
            if not m:
                continue
            if len(re.findall(r"[^\w\s](?:별지)?서식\d+", sq)) >= 2:  # 서식 목차 표 — 본문 아님
                continue
            no, sub_no = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
            heads.append((idx, no + (f"-{sub_no}" if sub_no else "")))
        if not heads:
            heads = _yangsik_heads(paras, xml)
        for k, (idx, no) in enumerate(heads):
            end = heads[k + 1][0] if k + 1 < len(heads) else len(paras)
            body = paras[idx:end]
            title = (page_title(xml[body[0].start:body[-1].end], heights, source) if heights else "") or _guess_title(body)
            if no not in found:
                order.append(no)
            found[no] = CutForm(no, title, name, body)  # 같은 번호는 뒤(본문) 것으로
    return [found[n] for n in order]


def _yangsik_heads(paras: list[Para], xml: str) -> list[tuple[int, str]]:
    """'【양식N】' 머리(서식 머리가 하나도 없는 구역만). 목차 줄(다음 머리까지 빈 문단뿐)은 건너뛰고, 본문 번호가 다시 작아지면
    (【별첨】 보안 서류 안의 '[양식1]'…) 거기부터 끝까지를 마지막 양식 다음 번호 하나로 — `notice.yangsik_text`와 같은 번호."""
    marks = [(i, _squash(p.text)) for i, p in enumerate(paras)]
    marks = [(i, sq) for i, sq in marks if _YHEAD.match(sq) or _APPX.match(sq)]
    nxt = {a: b for (a, _), (b, _) in zip(marks, marks[1:])}
    out, top, prev = [], 0, 0
    for i, sq in marks:
        m = _YHEAD.match(sq)
        if not m:
            continue
        n = int(m.group(1))
        body = paras[i + 1:nxt.get(i, i + 1)]
        if i in nxt and all(not p.text.strip() and "tbl" not in xml[p.start:p.end] for p in body):
            top = max(top, n)  # 목차 줄
            continue
        if n <= prev:
            out.append((i, str(top + 1)))
            break
        prev, top = n, max(top, n)
        out.append((i, m.group(1) + (f"-{m.group(2)}" if m.group(2) else "")))
    return out


def _norm_title(line: str) -> str:
    """'서  약  서' → '서약서', '사 용 인 감 계' → '사용인감계'. 칸(|) 앞까지, 길면 40자."""
    line = line.split("|")[0].strip(" -·")
    tokens = line.split()
    if len(tokens) >= 2 and sum(len(t) == 1 for t in tokens) >= len(tokens) - 1:
        line = "".join(tokens)
    line = re.split(r"\s{3,}", line)[0]
    return line.strip()[:40]


def _guess_title(body: list[Para]) -> str:
    """제목 문단의 '[서식 N]' 뒤 글, 없으면 다음 줄들 중 처음 2글자 넘는 줄."""
    rest = re.sub(r"^\s*[^\w\s]\s*(?:별\s*지\s*)?(?:[서양]식\s*(?:제\s*)?[\d\s\-–호]+|제[\d\s\-–호]+서식\s*)[^\w\s]", "", body[0].text)  # '〔서식 1호〕'도(11/04 당진)
    lines = [ln for p in [rest] + [p.text for p in body[1:4]] for ln in p.splitlines()]
    for ln in lines:
        t = _norm_title(ln)
        if len(_squash(t)) >= 2 and not re.match(r"^(접수번호|공고번호|구\s*분)", t) and not re.fullmatch(r"<[^>]*>", t):
            return t
    return ""


def _safe(name: str) -> str:
    name = re.sub(r'[\\/:*?"<>|\r\n\t]+', " ", name)
    return re.sub(r"\s+", "_", name.strip())[:40] or "서식"


def cut(path: Path, out_dir: Path, names: dict[str, str] | None = None) -> list[CutForm]:
    """names: 서식 번호 → 이름(공고 목차·제출도서 표에서 읽은 것). 있으면 파일 이름에 그 이름을 씀."""
    path, out_dir = Path(path), Path(out_dir)
    if path.suffix.lower() == ".hwp":
        raise ValueError("옛 한글(.hwp)은 먼저 HWPX로 바꿔 주세요 — 한글에서 '다른 이름으로 저장 → HWPX' 또는 hwp2hwpx.")
    zin = zipfile.ZipFile(path)
    entries = zin.namelist()
    sec_names = sorted((n for n in entries if re.fullmatch(r"Contents/section\d+\.xml", n)),
                       key=lambda n: int(re.search(r"\d+", n.rsplit("/", 1)[1]).group()))
    sections = {n: zin.read(n).decode("utf-8") for n in sec_names}
    forms = find_forms(sections, char_heights(zin), path.name)
    out_dir.mkdir(parents=True, exist_ok=True)
    for f in forms:
        xml = sections[f.section]
        all_paras = _top_paragraphs(xml)
        head = xml[:all_paras[0].start]
        tail = xml[all_paras[-1].end:]
        body = "".join(xml[p.start:p.end] for p in f.paras)
        body = re.sub(r'^(<(?:[\w.-]+:)?p\b[^>]*?)\bpageBreak="1"', r'\1pageBreak="0"', body, count=1)  # 빈 첫 쪽 방지
        first = all_paras[0]
        prefix = "" if f.paras[0].start == first.start else _sec_pr_para(xml, first)
        new_xml = head + prefix + body + tail
        if names and names.get(f.no):  # 하위 서식('4-1')은 이름에 그 번호가 따로 있을 때만(PDF 서식 다시 만들기가 줌)
            f.title = names[f.no]
        f.file = f"서식{f.no.replace('-', '_')}_{_safe(f.title)}.hwpx"
        target = out_dir / f.file
        # 다른 구역은 뺌 — 남는 구역은 section0으로(목록·구역 수도 맞춤)
        _write_package(zin, target, {f.section: new_xml}, {"Preview/PrvText.txt": _text(body)[:1000].encode("utf-8")})
    return forms


def section_names(names) -> list[str]:
    return sorted((n for n in names if re.fullmatch(r"Contents/section\d+\.xml", n)),
                  key=lambda n: int(re.search(r"\d+", n.rsplit("/", 1)[1]).group()))


_SEC_REF = re.compile(r"Contents/section\d+\.xml")


def _write_package(zin: zipfile.ZipFile, out: Path, sections_out: dict[str, str], replace: dict[str, bytes] | None = None) -> None:
    """원본 HWPX(zin)를 바탕으로 out을 씀. sections_out = 남길 구역(원본 이름 → 새 XML), replace = 바꿔 쓸 다른 파일.
    뺀 구역은 content.hpf(manifest·spine)·META-INF/container.rdf에서도 지우고 머리 정보 구역 수(secCnt)를 맞추며,
    남는 구역은 section0부터 차례로 다시 이름 붙임(11/05 제보: 구역이 둘인 제안요청서로 만든 통합서식이 '파일이 손상되었습니다' —
    content.hpf에서만 지우고 container.rdf·secCnt는 그대로였음)."""
    replace = replace or {}
    all_secs = section_names(zin.namelist())
    kept = [n for n in all_secs if n in sections_out]
    dropped = [n for n in all_secs if n not in sections_out]
    rename = {old: f"Contents/section{i}.xml" for i, old in enumerate(kept)}
    renamed = lambda text: _SEC_REF.sub(lambda m: rename.get(m.group(0), m.group(0)), text)
    with zipfile.ZipFile(out, "w") as zout:
        for info in zin.infolist():
            n = name = info.filename
            if n in dropped:
                continue
            if n in sections_out:
                name, data = rename[n], sections_out[n].encode("utf-8")
            else:
                data = replace[n] if n in replace else zin.read(n)
                if n == "Contents/content.hpf":
                    text = _drop_sections(data.decode("utf-8"), dropped)
                    ids = {}  # 남는 구역 항목 이름(id)도 새 번호로 — spine의 idref와 함께
                    for old, new in rename.items():
                        m = re.search(rf'<(?:[\w.-]+:)?item\b[^>]*href="{re.escape(old)}"[^>]*/>', text)
                        got = re.search(r'\bid="([^"]+)"', m.group()) if m else None
                        if got:
                            ids[got.group(1)] = new.rsplit("/", 1)[1].rsplit(".", 1)[0]
                    text = re.sub(r'\b(id|idref)="([^"]+)"', lambda m: f'{m.group(1)}="{ids.get(m.group(2), m.group(2))}"', text)
                    data = renamed(text).encode("utf-8")
                elif n == "META-INF/container.rdf":
                    text = data.decode("utf-8")
                    for d in dropped:  # 뺀 구역을 가리키는 Description 묶음을 지움
                        text = re.sub(rf'<((?:[\w.-]+:)?Description)\b(?:(?!</\1>).)*?"{re.escape(d)}"(?:(?!</\1>).)*?</\1>', "", text, flags=re.S)
                    data = renamed(text).encode("utf-8")
                elif n == "Contents/header.xml":
                    data = re.sub(rb'(\bsecCnt=")\d+"', lambda m: m.group(1) + str(len(kept)).encode() + b'"', data, count=1)
            # mimetype은 압축하지 않고 맨 앞(원본 순서 유지)
            ctype = zipfile.ZIP_STORED if n == "mimetype" else zipfile.ZIP_DEFLATED
            zout.writestr(zipfile.ZipInfo(name, date_time=info.date_time), data, compress_type=ctype)


def _drop_sections(hpf: str, drop: list[str]) -> str:
    """content.hpf의 manifest·spine에서 뺀 구역 항목을 지움."""
    for sec in drop:
        m = re.search(rf'<(?:[\w.-]+:)?item\b[^>]*href="{re.escape(sec)}"[^>]*/>', hpf)
        if not m:
            continue
        item_id = re.search(r'\bid="([^"]+)"', m.group()).group(1)
        hpf = hpf.replace(m.group(), "")
        hpf = re.sub(rf'<(?:[\w.-]+:)?itemref\b[^>]*idref="{re.escape(item_id)}"[^>]*/>', "", hpf)
    return hpf


_PLAIN_TAGS = re.compile(r"</?(?:[\w.-]+:)?(?:p|run|t|linesegarray|lineseg)\b[^>]*>")


def _blank_para(seg: str) -> bool:
    """글도 표·그림·조판 부호도 없는 빈 문단(글자 모양·줄 배치 태그만)."""
    return not _text(seg).strip() and not _PLAIN_TAGS.sub("", seg).strip()


def _drop_blank_tail(body: str) -> str:
    """서식 끝과 '새 쪽' 문단 바로 앞의 빈 문단을 지움(11/05 사용자 제보: 통합서식에 빈 쪽) — 서식 5 끝 빈 문단이 쪽을 넘어가
    혼자 한 쪽을 차지하고, 다음 서식은 다시 새 쪽에서 시작했음. 맨 앞 문단은 남김."""
    paras = _top_paragraphs(body)
    drop = set()
    for i in range(len(paras) - 1, 0, -1):
        seg = body[paras[i].start:paras[i].end]
        nxt = body[paras[i + 1].start:paras[i + 1].end] if i + 1 < len(paras) else None
        ends_page = nxt is None or i + 1 in drop or re.match(r'<(?:[\w.-]+:)?p\b[^>]*\bpageBreak="1"', nxt)
        if ends_page and _blank_para(seg) and not re.match(r'<(?:[\w.-]+:)?p\b[^>]*\bpageBreak="1"', seg):
            drop.add(i)
    for i in sorted(drop, reverse=True):
        body = body[:paras[i].start] + body[paras[i].end:]
    return body


def merge(path: Path, parts: list[tuple[CutForm, Path]], out: Path, fit_pages: bool = True) -> Path:
    """잘라 낸(채웠거나 양식 그대로인) 서식 파일들을 서식 순서대로 다시 한 HWPX로 — 담당자가 한글 파일 하나에서 직접 기입(11/03 사용자 요청).

    parts: (cut()이 돌려준 서식, 그 서식의 최종 파일). 원본(path)의 머리·쪽 설정·글꼴을 그대로 쓰고, 서식 본문만 각 파일에서 가져온다.
    서식마다 새 쪽에서 시작(fit_pages면 조금 넘쳐 쪽이 낭비되는 서식만 맞춤 — `pagefit`). 서식이 없는 구역은 뺌."""
    path, out = Path(path), Path(out)
    zin = zipfile.ZipFile(path)
    entries = zin.namelist()
    sec_names = sorted((n for n in entries if re.fullmatch(r"Contents/section\d+\.xml", n)),
                       key=lambda n: int(re.search(r"\d+", n.rsplit("/", 1)[1]).group()))
    sections = {n: zin.read(n).decode("utf-8") for n in sec_names}
    bodies: dict[str, list[tuple[CutForm, str]]] = {}
    header = zin.read("Contents/header.xml").decode("utf-8") if "Contents/header.xml" in entries else ""
    orig_header = header
    if header:  # 채운 값 글꼴(휴먼명조, 11/11) — 채울 때 각 서식 머리에 더한 것과 같은 번호로 통합 머리에도
        from .fill import ensure_font
        header = ensure_font(header)
    # 채울 때 더한 글자 모양(charPr)·문단 모양(paraPr, 가운데 정렬 사본) — 원본 머리에 없는 id는 새 번호로 옮겨 더함
    kinds = {}
    for tag, ref, group in (("charPr", "charPrIDRef", "charProperties"), ("paraPr", "paraPrIDRef", "paraProperties")):
        rx = re.compile(rf'<(?:[\w.-]+:)?{tag}\b[^>]*\bid="(\d+)".*?</(?:[\w.-]+:)?{tag}>', re.S)
        known_ids = {m.group(1) for m in rx.finditer(header)}
        kinds[tag] = {"rx": rx, "ref": ref, "group": group, "known": known_ids,
                      "next": max([int(x) for x in known_ids] or [0]) + 1, "extra": []}
    for f, file in parts:
        pz = zipfile.ZipFile(file)
        part_secs = section_names(pz.namelist())  # 잘라 낸 서식 파일엔 구역이 하나(section0으로 다시 이름 붙임)
        xml = pz.read(f.section if f.section in part_secs else part_secs[0]).decode("utf-8")
        # 채울 때 칸에 맞춰 줄인 글자 모양(원본 머리에 없는 id) — 새 번호로 옮겨 통합 머리에 더함(서식마다 같은 번호를 따로 썼을 수 있어서)
        if header and "Contents/header.xml" in pz.namelist():
            part_header = pz.read("Contents/header.xml").decode("utf-8")
            for k in kinds.values():
                remap = {}
                for m in k["rx"].finditer(part_header):
                    if m.group(1) not in k["known"] and f'{k["ref"]}="{m.group(1)}"' in xml:
                        remap[m.group(1)] = str(k["next"])
                        k["extra"].append(re.sub(r'\bid="\d+"', f'id="{k["next"]}"', m.group(0), count=1))
                        k["next"] += 1
                if remap:
                    xml = re.sub(rf'{k["ref"]}="(\d+)"', lambda m, r=remap, a=k["ref"]: f'{a}="{r.get(m.group(1), m.group(1))}"', xml)
        paras = _top_paragraphs(xml)
        body_paras = paras[-len(f.paras):]  # 채우기는 문단 안만 바꾸므로 문단 수는 그대로 — 앞의 쪽 설정 문단은 뺌
        body = _drop_blank_tail(xml[body_paras[0].start:body_paras[-1].end])
        bodies.setdefault(f.section, []).append((f, body))
    kept = [s for s in sec_names if s in bodies]
    if not kept:
        raise ValueError("이어 붙일 서식이 없습니다")
    new_sections: dict[str, str] = {}
    preview = []
    for sec in kept:
        xml = sections[sec]
        all_paras = _top_paragraphs(xml)
        first = all_paras[0]
        chunks = []
        for i, (f, body) in enumerate(bodies[sec]):
            if i or sec != kept[0]:
                body = re.sub(r'^(<(?:[\w.-]+:)?p\b[^>]*?)\bpageBreak="0"', r'\1pageBreak="1"', body, count=1)
            else:  # 맨 앞 서식은 쪽 나눔 없이 — 쪽 설정 문단 뒤 '새 쪽'이면 첫 장이 빈 쪽이 됐음(11/04 필암서원)
                body = re.sub(r'^(<(?:[\w.-]+:)?p\b[^>]*?)\bpageBreak="1"', r'\1pageBreak="0"', body, count=1)
            chunks.append(body)
            preview.append(_text(body))
        starts_at_first = bodies[sec][0][0].paras[0].start == first.start
        prefix = "" if starts_at_first else _sec_pr_para(xml, first)
        new_sections[sec] = xml[:first.start] + prefix + "".join(chunks) + xml[all_paras[-1].end:]

    out.parent.mkdir(parents=True, exist_ok=True)
    replace = {"Preview/PrvText.txt": " ".join(preview)[:1000].encode("utf-8")}
    if header and (any(k["extra"] for k in kinds.values()) or header != orig_header):
        h = header
        for k in kinds.values():
            if not k["extra"]:
                continue
            h = re.sub(rf"(</(?:[\w.-]+:)?{k['group']}>)", lambda m, e=k["extra"]: "".join(e) + m.group(1), h, count=1)
            h = re.sub(rf'(<(?:[\w.-]+:)?{k["group"]}\b[^>]*\bitemCnt=")(\d+)"',
                       lambda m, e=k["extra"]: f'{m.group(1)}{int(m.group(2)) + len(e)}"', h, count=1)
        replace["Contents/header.xml"] = h.encode("utf-8")
    _write_package(zin, out, new_sections, replace)
    if fit_pages:  # 1~2줄·제목만 남아 쪽 하나가 낭비되는 서식만 손봄(11/06 사용자 요청, `pagefit`)
        from .pagefit import fit
        fit(out)
    return out
