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
_HEAD = re.compile(r"^[^\w\s]\s*서식\s*(?:제\s*)?(\d+)\s*호?\s*(?:[-–]\s*(\d+))?\s*[^\w\s]")


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
            if len(re.findall(r"[^\w\s]서식\d+", sq)) >= 2:  # 서식 목차 표 — 본문 아님
                continue
            heads.append((idx, m.group(1) + (f"-{m.group(2)}" if m.group(2) else "")))
        for k, (idx, no) in enumerate(heads):
            end = heads[k + 1][0] if k + 1 < len(heads) else len(paras)
            body = paras[idx:end]
            title = (page_title(xml[body[0].start:body[-1].end], heights, source) if heights else "") or _guess_title(body)
            if no not in found:
                order.append(no)
            found[no] = CutForm(no, title, name, body)  # 같은 번호는 뒤(본문) 것으로
    return [found[n] for n in order]


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
    rest = re.sub(r"^\s*[^\w\s]\s*서식\s*(?:제\s*)?[\d\s\-–호]+[^\w\s]", "", body[0].text)  # '〔서식 1호〕'도(11/04 당진)
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
    hpf = zin.read("Contents/content.hpf").decode("utf-8") if "Contents/content.hpf" in entries else None
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
        if names and names.get(f.no) and "-" not in f.no:
            f.title = names[f.no]
        f.file = f"서식{f.no.replace('-', '_')}_{_safe(f.title)}.hwpx"
        target = out_dir / f.file
        with zipfile.ZipFile(target, "w") as zout:
            for info in zin.infolist():
                n = info.filename
                if n in sections and n != f.section:
                    continue  # 다른 구역은 뺌
                if n == f.section:
                    data = new_xml.encode("utf-8")
                elif n == "Contents/content.hpf" and hpf is not None:
                    data = _drop_sections(hpf, [s for s in sec_names if s != f.section]).encode("utf-8")
                elif n == "Preview/PrvText.txt":
                    data = _text(body)[:1000].encode("utf-8")  # 미리보기 글(원본도 UTF-8)
                else:
                    data = zin.read(n)
                # mimetype은 압축하지 않고 맨 앞(원본 순서 유지)
                ctype = zipfile.ZIP_STORED if n == "mimetype" else zipfile.ZIP_DEFLATED
                zout.writestr(zipfile.ZipInfo(n, date_time=info.date_time), data, compress_type=ctype)
    return forms


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


def merge(path: Path, parts: list[tuple[CutForm, Path]], out: Path) -> Path:
    """잘라 낸(채웠거나 양식 그대로인) 서식 파일들을 서식 순서대로 다시 한 HWPX로 — 담당자가 한글 파일 하나에서 직접 기입(11/03 사용자 요청).

    parts: (cut()이 돌려준 서식, 그 서식의 최종 파일). 원본(path)의 머리·쪽 설정·글꼴을 그대로 쓰고, 서식 본문만 각 파일에서 가져온다.
    서식마다 새 쪽에서 시작. 서식이 없는 구역은 뺌."""
    path, out = Path(path), Path(out)
    zin = zipfile.ZipFile(path)
    entries = zin.namelist()
    sec_names = sorted((n for n in entries if re.fullmatch(r"Contents/section\d+\.xml", n)),
                       key=lambda n: int(re.search(r"\d+", n.rsplit("/", 1)[1]).group()))
    sections = {n: zin.read(n).decode("utf-8") for n in sec_names}
    bodies: dict[str, list[tuple[CutForm, str]]] = {}
    header = zin.read("Contents/header.xml").decode("utf-8") if "Contents/header.xml" in entries else ""
    char_rx = re.compile(r'<(?:[\w.-]+:)?charPr\b[^>]*\bid="(\d+)".*?</(?:[\w.-]+:)?charPr>', re.S)
    known = {m.group(1) for m in char_rx.finditer(header)}
    next_id = max([int(x) for x in known] or [0]) + 1
    extra_chars: list[str] = []
    for f, file in parts:
        pz = zipfile.ZipFile(file)
        xml = pz.read(f.section).decode("utf-8")
        # 채울 때 칸에 맞춰 줄인 글자 모양(원본 머리에 없는 id) — 새 번호로 옮겨 통합 머리에 더함(서식마다 같은 번호를 따로 썼을 수 있어서)
        if header and "Contents/header.xml" in pz.namelist():
            remap = {}
            for m in char_rx.finditer(pz.read("Contents/header.xml").decode("utf-8")):
                if m.group(1) not in known and f'charPrIDRef="{m.group(1)}"' in xml:
                    remap[m.group(1)] = str(next_id)
                    extra_chars.append(re.sub(r'\bid="\d+"', f'id="{next_id}"', m.group(0), count=1))
                    next_id += 1
            if remap:
                xml = re.sub(r'charPrIDRef="(\d+)"', lambda m: f'charPrIDRef="{remap.get(m.group(1), m.group(1))}"', xml)
        paras = _top_paragraphs(xml)
        body_paras = paras[-len(f.paras):]  # 채우기는 문단 안만 바꾸므로 문단 수는 그대로 — 앞의 쪽 설정 문단은 뺌
        body = xml[body_paras[0].start:body_paras[-1].end]
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
    hpf = zin.read("Contents/content.hpf").decode("utf-8") if "Contents/content.hpf" in entries else None
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w") as zout:
        for info in zin.infolist():
            n = info.filename
            if n in sections and n not in new_sections:
                continue
            if n in new_sections:
                data = new_sections[n].encode("utf-8")
            elif n == "Contents/content.hpf" and hpf is not None:
                data = _drop_sections(hpf, [s for s in sec_names if s not in new_sections]).encode("utf-8")
            elif n == "Contents/header.xml" and extra_chars:
                h = re.sub(r"(</(?:[\w.-]+:)?charProperties>)", lambda m: "".join(extra_chars) + m.group(1), header, count=1)
                data = re.sub(r'(<(?:[\w.-]+:)?charProperties\b[^>]*\bitemCnt=")(\d+)"',
                              lambda m: f'{m.group(1)}{int(m.group(2)) + len(extra_chars)}"', h, count=1).encode("utf-8")
            elif n == "Preview/PrvText.txt":
                data = " ".join(preview)[:1000].encode("utf-8")
            else:
                data = zin.read(n)
            ctype = zipfile.ZIP_STORED if n == "mimetype" else zipfile.ZIP_DEFLATED
            zout.writestr(zipfile.ZipInfo(n, date_time=info.date_time), data, compress_type=ctype)
    return out
