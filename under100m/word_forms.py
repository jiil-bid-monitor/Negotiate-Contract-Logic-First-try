"""워드(.docx) 공고서 안 서식 — 한글 서식처럼 서식마다 잘라 회사 정보를 채우고 한 파일로 (2026-11-11 사용자 요청).

'[서식 1] 공기 준수 서약서'처럼 서식 머리(form_split.mark_match와 같은 규칙)부터 다음 머리 앞까지가 서식 하나.
채우기는 한글 서식과 같은 칸 이름 규칙(form_fill.key_of): '회 사 명 :'·'주    소 :'·'대표이사 :   (인)' 같은 줄,
'업체명 | (빈 칸)' 같은 표. 모르는 칸·날짜·계약기간은 비워 둠.
"""

from __future__ import annotations

import html
import re
import zipfile
from pathlib import Path

from . import form_fill
from .form_split import label_of, mark_match

_TAG = re.compile(r"<(/?)([\w:]+)[^>]*?(/?)>")
_T = re.compile(r"(<w:t(?:\s[^>]*)?>)([^<]*)(</w:t>)")
PAGE_BREAK = '<w:p><w:r><w:br w:type="page"/></w:r></w:p>'


def _blocks(body: str) -> list[tuple[int, int]]:
    """<w:body> 바로 아래 요소(문단·표·sectPr)의 (시작, 끝)."""
    out, depth, start = [], 0, None
    for m in _TAG.finditer(body):
        closing, name, selfclose = m.group(1), m.group(2), m.group(3)
        if name == "w:body":
            continue
        if closing:
            depth -= 1
            if depth == 0:
                out.append((start, m.end()))
        elif selfclose:
            if depth == 0:
                out.append((m.start(), m.end()))
        else:
            if depth == 0:
                start = m.start()
            depth += 1
    return out


def _text(xml: str) -> str:
    return html.unescape("".join(t for _, t, _ in _T.findall(xml)))


def split(doc_xml: str) -> tuple[str, str, list[tuple[str, str, list[str]]], str]:
    """(본문 앞, sectPr, [(서식 이름, 제목, 요소들)], 본문 뒤)."""
    b0 = doc_xml.index("<w:body>") + len("<w:body>")
    b1 = doc_xml.rindex("</w:body>")
    body = doc_xml[b0:b1]
    items = [body[s:e] for s, e in _blocks(body)]
    sect = next((x for x in items if x.startswith("<w:sectPr")), "")
    items = [x for x in items if not x.startswith("<w:sectPr")]
    forms, seen = [], set()
    texts = [_text(x).strip() for x in items]
    for k, x in enumerate(items):
        m = mark_match(texts[k]) if x.startswith("<w:p") else None
        if m:
            kind, number, rest = m
            label = label_of(re.sub(r"\s", "", kind), re.sub(r"\s", "", number))
            title = rest.strip() or next((t for t in texts[k + 1:k + 3] if t and len(t) <= 40 and not mark_match(t)), "")
            if label in seen:  # 같은 번호가 또 나오면(목록·본문 언급) 앞 것을 씀
                forms[-1][2].append(x)
                continue
            seen.add(label)
            forms.append((label, title, [x]))
        elif forms:
            forms[-1][2].append(x)
    return doc_xml[:b0], sect, forms, doc_xml[b1:]


def _width(s: str) -> int:
    return sum(2 if ord(ch) > 0x2E80 else 1 for ch in s)


def _fill_line(p: str, log: list[str]) -> str:
    """'회 사 명 :' 같은 줄 — 쌍점 뒤에 값, 뒤 빈칸은 값 너비만큼 줄여 '(인)' 자리를 지킴."""
    text = _text(p)
    m = re.match(r"^\s*(?:\d{1,2}\.\s*)?([^:：]{1,20}?)\s*[:：]\s*(\((?:인|서명)\))?\s*$", text)
    if not m:
        return p
    key = form_fill.key_of(m.group(1))
    if not key or key in ("title",):  # 공사명은 공고에 이미 적혀 있음
        return p
    value = form_fill.JIIL[key]
    runs = list(_T.finditer(p))
    for i, r in enumerate(runs):
        if re.search(r"[:：]", html.unescape(r.group(2))):
            break
    else:
        return p
    t = html.unescape(r.group(2))
    cut = max(t.rfind(":"), t.rfind("："))
    after = t[cut + 1:]
    keep = len(after) - len(after.lstrip(" "))
    trim = _width(value) + 1
    new_t = t[:cut + 1] + " " + value + " " * max(1 if after.strip() or i + 1 < len(runs) else 0, keep - trim) + after.lstrip(" ")
    trim -= keep
    out = p[:r.start()] + '<w:t xml:space="preserve">' + html.escape(new_t, quote=False) + "</w:t>"
    pos = r.end()
    for r2 in runs[i + 1:]:  # 뒤 run의 앞 빈칸도 남은 만큼 줄임
        t2 = html.unescape(r2.group(2))
        lead = len(t2) - len(t2.lstrip(" "))
        cut2 = max(0, min(lead - 1, trim)) if trim > 0 else 0
        out += p[pos:r2.start()] + '<w:t xml:space="preserve">' + html.escape(t2[cut2:], quote=False) + "</w:t>"
        trim -= cut2
        pos = r2.end()
    log.append(f"줄 {re.sub(r'\s+', '', m.group(1))} → {value}")
    return out + p[pos:]


def _fill_table(tbl: str, log: list[str]) -> str:
    """'업체명 | 빈 칸' 표 — 빈 값 칸에 이름표 칸의 글자 모양으로 값."""
    def row(rm):
        r = rm.group(0)
        cells = list(re.finditer(r"<w:tc>.*?</w:tc>|<w:tc\s[^>]*>.*?</w:tc>", r, re.S))
        edits = {}
        for a, b in zip(cells, cells[1:]):
            key = form_fill.key_of(_text(a.group(0)))
            if not key or key == "title" or _text(b.group(0)).strip():
                continue
            rpr = re.search(r"<w:rPr>.*?</w:rPr>", a.group(0), re.S)
            run = f'<w:r>{rpr.group(0) if rpr else ""}<w:t xml:space="preserve">{html.escape(form_fill.JIIL[key], quote=False)}</w:t></w:r>'
            cell = b.group(0)
            cell = re.sub(r"(<w:p\b[^>]*>(?:\s*<w:pPr>.*?</w:pPr>)?)", lambda pm: pm.group(1) + run, cell, count=1, flags=re.S) \
                if "<w:p" in cell else cell
            edits[b.start()] = (b.end(), cell)
            log.append(f"표 {re.sub(r'\s+', '', _text(a.group(0)))} → {form_fill.JIIL[key]}")
        for s in sorted(edits, reverse=True):
            e, new = edits[s]
            r = r[:s] + new + r[e:]
        return r

    tbl = re.sub(r"<w:tr\b.*?</w:tr>", row, tbl, flags=re.S)
    return re.sub(r"<w:p\b(?:(?!</w:p>).)*?</w:p>", lambda pm: _fill_line(pm.group(0), log), tbl, flags=re.S)


def build(src: Path, dst: Path, company: dict, bid_date: str | None = None, title: str | None = None,
          notice_no: str | None = None) -> tuple[list[str], list[str]]:
    """서식마다 잘라 채우고 한 파일(서식마다 새 쪽)로. ([서식 이름], [채운 칸 기록]). 서식이 없으면 ([], [])."""
    form_fill._configure(company, bid_date, title, notice_no)
    z = zipfile.ZipFile(src)
    head, sect, forms, tail = split(z.read("word/document.xml").decode("utf-8"))
    if not forms:
        return [], []
    log: list[str] = []
    parts = []
    for k, (_, _, items) in enumerate(forms):
        if k:
            parts.append(PAGE_BREAK)
        for x in items:
            if x.startswith("<w:tbl"):
                parts.append(_fill_table(x, log))
            elif x.startswith("<w:p"):
                parts.append(_fill_line(x, log))
            else:
                parts.append(x)
    doc = head + "".join(parts) + sect + tail
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as w:
        for info in z.infolist():
            w.writestr(info, doc.encode("utf-8") if info.filename == "word/document.xml" else z.read(info.filename))
    return [f"{label} {name}".strip() for label, name, _ in forms], log
