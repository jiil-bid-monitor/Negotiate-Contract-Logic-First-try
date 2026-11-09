"""PDF로만 있는 서식 — 서식마다 쪽 단위로 나누고, 글자 정보가 있으면 회사 값을 겹쳐 찍는다. AI 없이 규칙으로만.

칸을 찾는 규칙과 비우는 칸은 한글 서식(`form_fill`)과 같다: 대리인·증명기관 칸은 비우고, 증명서는 신청인 칸만,
인력 서식은 소속만, 이미 글자가 있는 칸에는 쓰지 않는다. 발주처 서식의 글자는 지우거나 바꾸지 않는다.
겹쳐 찍기엔 PyMuPDF가 필요하다 — 없으면 나누기만 하고 안내한다.
"""

from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path

from pypdf import PdfReader, PdfWriter

from .form_fill import CERT, JIIL, STAFF, _configure, key_of
from .form_split import tidy

log = logging.getLogger("under100m")

_N = r"(\d+(?:\s*-\s*\d+)?)"
_MARK = re.compile(r"[\[【<〈]\s*(별\s*지|서\s*식|붙\s*임)\s*(?:제\s*)?" + _N + r"\s*(?:호)?\s*(?:서\s*식)?\s*[\]】>〉]")
_PAGE_NO = re.compile(r"\s*-\s*\d+\s*-\s*")
_FORM_END = re.compile(r"(서|장|표|계|지|현황|사항|실적|내용)$")
_UNSAFE = re.compile(r'[\\/:*?"<>|]')
_BLOCK = re.compile(r"대리인|수임인|수임자|피위임|위임받는|발급기관|증명기관|신용평가기관|확인자")
_APPLICANT = re.compile(r"신청인|신청자|의뢰인|신청업체|제출자")
_SIGN = re.compile(r"^(?:\(?인\)?|\(인감\)|\(서명\)|\(서명또는인\)|㉿|㊞)?$")
_LINE_LABEL = re.compile(r"^[○ㅇ◦•·□\-\d.]*((?:[가-힣A-Za-z()]+))[:：](.*)$")
_LINE_REST = re.compile(r"^(?:\(?인\)?(?:또는\(?서명\)?)?|\(서명\)|㉿|㊞|\(?연락처.*)?$")  # "(인) 또는 (서명)"(5·6호선 별지1)
MIN_SIZE = 7.0
MIN_CELL_WIDTH = 28


def _title_after(lines: list[str], mark) -> str:
    """머리 표시 뒤 제목 — 같은 줄이 먼저, 없으면 다음 줄."""
    joined = " ".join(lines)
    found = mark.search(joined)
    same_line = next((line for line in lines if mark.search(line)), "")
    after = mark.sub(" ", same_line).strip() if same_line else ""
    if len(after) >= 2:
        return tidy(after)
    rest = [line.strip() for line in lines if not mark.search(line) and len(line.strip()) > 1]
    if not rest:
        return ""
    title = rest[0]
    # 제목이 두 줄로 나뉜 경우 — 서약서처럼 긴 제목만("지방계약법 제31조의5 및" / "같은 법 시행령 … 서약서")
    if found and len(rest) > 1 and not _FORM_END.search(title.replace(" ", "")) and re.search(r"(및|따른|관한|의)$", title):
        title = f"{title} {rest[1]}"
    return tidy(title)


def split_pdf(src: Path, out: Path) -> list[str]:
    """서식마다 PDF 한 개. 만든 파일 이름들(서식 표시가 없으면 빈 목록)."""
    reader = PdfReader(src)
    pages = [(page.extract_text() or "").splitlines() for page in reader.pages]
    starts: list[tuple[int, str, str, str]] = []
    for i, lines in enumerate(pages):
        if len({m.group(2) for m in _MARK.finditer(" ".join(lines))}) >= 3 or re.search(r"서\s*식\s*번\s*호", " ".join(lines[:8])):
            continue  # 서식 목록 쪽이지 서식이 아님 — 쪽 첫머리에 표시가 있어도 건너뜀
        top = [line for line in lines[:5] if not _PAGE_NO.fullmatch(line)]
        alone = next((line for line in lines[5:] if _MARK.fullmatch(line.strip())), None)  # 쪽 가운데 "【붙임1】"만 있는 줄
        if alone and not _MARK.search(" ".join(top)):
            at = lines.index(alone)
            top = lines[at:at + 4]
        found = _MARK.search(" ".join(top))
        if not found:
            continue
        number = re.sub(r"\s+", "", found.group(2))
        if starts and starts[-1][1] == number:
            continue  # 같은 번호가 이어지면 같은 서식의 다음 쪽
        starts.append((i, number, _title_after(top, _MARK), re.sub(r"\s+", "", found.group(1))))
    if not starts:
        return []
    out.mkdir(parents=True, exist_ok=True)
    names = []
    for k, (i, number, title, kind) in enumerate(starts):
        writer = PdfWriter()
        for j in range(i, starts[k + 1][0] if k + 1 < len(starts) else len(reader.pages)):
            writer.add_page(reader.pages[j])
        label = f"{kind}{number.zfill(2) if number.isdigit() else number}"
        name = _UNSAFE.sub("_", f"{label}_{title}".strip("_ "))[:80].strip() + ".pdf"
        for page in writer.pages:
            page.compress_content_streams()  # 그대로 쓰면 압축이 풀려 원본 0.7MB가 쪽마다 13MB가 됐음(2026-11-09 5·6호선)
        writer.compress_identical_objects()  # 기본값이 겹친 것·안 쓰는 것 모두 지움
        with open(out / name, "wb") as handle:
            writer.write(handle)
        names.append(name)
    return names


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def _put(page, rect_x: float, baseline: float, limit: float, value: str, size: float, font, font_file: str) -> float | None:
    """칸 폭에 맞게 줄여서 찍음. 최소 크기로도 안 들어가면 찍지 않고 None."""
    room = limit - rect_x
    width = font.text_length(value, fontsize=size)
    if width > room:
        size = size * room / width
    if size < MIN_SIZE:
        return None
    page.insert_text((rect_x, baseline), value, fontname="kr", fontfile=font_file, fontsize=size)
    return size


def _fill_tables(page, font, font_file: str, modes: dict, filled: list[str]) -> list[tuple[float, float, float, float]]:
    used_rects = []
    for table in page.find_tables(strategy="lines").tables:
        cells = [tuple(c) for c in table.cells if c]
        texts = {c: _norm(page.get_textbox(c)) for c in cells}
        if modes["agent"] and any(t == "성명" for t in texts.values()) and not any("대표자" in t for t in texts.values()):
            continue  # 위임장 "다음" 표 = 대리인 정보
        blocks = [(c[1], c[3]) for c, t in texts.items() if _BLOCK.search(t) and len(t) <= 20]
        applicants = [(c[1], c[3]) for c, t in texts.items() if _APPLICANT.search(t) and len(t) <= 10]
        for cell, text in texts.items():
            key = key_of(text)
            if not key or key == "rrn" or key == "sig":
                continue
            middle = (cell[1] + cell[3]) / 2
            if any(a <= middle <= b for a, b in blocks):
                continue
            if modes["cert"] and not any(a <= middle <= b for a, b in applicants):
                continue
            if modes["staff"] and text != "소속":
                continue
            # 바로 오른쪽 칸 — 선이 겹쳐 생긴 아주 좁은 칸은 건너뜀
            side = sorted((c for c in cells if cell[2] - 3 <= c[0] <= cell[2] + 8 and c[2] - c[0] >= MIN_CELL_WIDTH and c[1] <= middle <= c[3]), key=lambda c: c[0])
            right = side[0] if side else None
            if not right or not _SIGN.match(texts[right]):
                continue
            size = min(11.0, (right[3] - right[1]) * 0.6)
            limit = right[2] - (24 if texts[right] else 3)  # 칸에 (인)이 있으면 그 앞까지
            baseline = (right[1] + right[3]) / 2 + size * 0.35
            if _put(page, right[0] + 4, baseline, limit, JIIL[key], size, font, font_file):
                filled.append(f"표 {text}")
                used_rects.append(right)
    return used_rects


def _rows(page) -> list[list[tuple[str, tuple, float]]]:
    """글자를 줄로 묶음 — 같은 자리에 두 번 그린 글자(가짜 굵게)는 한 번만."""
    chars, seen = [], set()
    for block in page.get_text("rawdict")["blocks"]:
        for line in block.get("lines", []):
            for span in line["spans"]:
                for ch in span["chars"]:
                    mark = (ch["c"], round(ch["bbox"][0]), round(ch["bbox"][1]))
                    if mark not in seen:
                        seen.add(mark)
                        chars.append((ch["c"], tuple(ch["bbox"]), span["size"]))
    chars.sort(key=lambda c: (c[1][3], c[1][0]))
    rows: list[list] = []
    for ch in chars:
        if rows and abs(rows[-1][0][1][3] - ch[1][3]) <= 2:
            rows[-1].append(ch)
        else:
            rows.append([ch])
    for row in rows:
        row.sort(key=lambda c: c[1][0])
    return rows


def _fill_lines(page, font, font_file: str, modes: dict, filled: list[str]) -> None:
    """표 밖 또는 큰 칸 안의 "업 체 명 :" 줄 — 쌍점 뒤가 비어 있을 때만. 증명서·인력 서식은 줄 규칙을 안 씀."""
    if modes["cert"] or modes["staff"]:
        return
    width = page.rect.width
    for row in _rows(page):
        marks = [c for c in row if not c[0].isspace()]
        text = "".join(c[0] for c in marks)
        found = _LINE_LABEL.match(text)
        if not found or (_LINE_REST.match(_norm(found.group(2))) is None):
            continue
        label = found.group(1)
        key = key_of(label)
        if not key or key in ("rrn", "sig") or (modes["agent"] and label == "소속"):
            continue
        colon = next(i for i, c in enumerate(marks) if c[0] in ":：")
        after = marks[colon + 1:]
        start = marks[colon][1][2] + marks[colon][2] * 0.5
        limit = after[0][1][0] - 4 if after else width - 40
        if _put(page, start, marks[colon][1][3] - marks[colon][2] * 0.2, limit, JIIL[key], marks[colon][2], font, font_file):
            filled.append(f"줄 {label}")


def fill_pdf(src: Path, dst: Path, company: dict, title: str | None = None, name: str | None = None) -> tuple[list[str], str]:
    """(넣은 칸, 안내). 스캔본·프로그램 없음이면 원본을 그대로 복사한다."""
    try:
        import pymupdf
    except ImportError:
        shutil.copyfile(src, dst)
        return [], "PDF 입력 프로그램(PyMuPDF)이 없어 쪽만 나눔"
    from quantdocs.bundle import _FONTS

    font_file = next((f for f in _FONTS if Path(f).exists()), None)
    if not font_file:
        shutil.copyfile(src, dst)
        return [], "한글 글꼴이 없어 쪽만 나눔"
    _configure(company, None, title, None)
    label = name or dst.name
    modes = {"cert": bool(CERT.search(label)), "agent": "위임" in label, "staff": bool(STAFF.search(label))}
    font = pymupdf.Font(fontfile=font_file)
    filled: list[str] = []
    with pymupdf.open(src) as doc:
        if not any(page.get_text().strip() for page in doc):
            shutil.copyfile(src, dst)
            return [], "스캔본이라 칸을 찾지 못함"
        for page in doc:
            _fill_tables(page, font, font_file, modes, filled)
            _fill_lines(page, font, font_file, modes, filled)
        doc.subset_fonts()  # 넣은 글자만 남김 — 한글 글꼴 통째(약 4MB)가 서식마다 들어갔음
        doc.save(dst, garbage=4, deflate=True)  # 쪽만 떼어도 원본 PDF 전체 자원이 따라와 13MB씩 됐음(2026-11-09 5·6호선)
    return filled, ""
