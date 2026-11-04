"""회사 서류(PDF·그림)를 쪽마다 그림으로 바꿔 통합서식 HWPX 뒤에 붙인다 — 한글 파일 하나로 제출(11/04 사용자 요청).

담당자는 서식의 직접 기입 칸만 채우면 곧바로 제출할 수 있게: 서식(채운 것·직접 기입) → '첨부 서류 목록' 한 쪽 → 서류 쪽 그림.
서류 쪽은 읽기 전용(그림, 고정 크기·잠금)으로 그대로 옮긴다 — 글을 다시 치지 않는다.

PDF는 pypdfium2로 그림을 만든다(없으면 poppler `pdftoppm`). 둘 다 없거나 못 읽는 서류는 넣지 않고 이유를 돌려준다(묶음 첨부/에 원본).
"""

from __future__ import annotations

import io
import re
import shutil
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

DPI = 150  # 스캔 서류가 인쇄해도 읽히는 정도 — 한 쪽 약 200~400KB(JPEG)
MAX_SIDE = 2200  # 큰 도면 PDF가 너무 커지지 않게(픽셀)
JPEG_QUALITY = 82
MM = 283.465  # HWPUNIT / mm
MARGIN = round(10 * MM)  # 서류 쪽 여백 10mm


@dataclass
class Doc:
    """붙일 서류 하나 — 목록에 보일 구분·파일 이름, 쪽 그림(JPEG, 가로·세로 픽셀)."""

    section: str
    name: str
    pages: list[tuple[bytes, int, int]] = field(default_factory=list)


# ── PDF·그림 → 쪽 그림 ─────────────────────────────────────────
def _jpeg(img) -> tuple[bytes, int, int]:
    img = img.convert("RGB")
    if max(img.size) > MAX_SIDE:
        r = MAX_SIDE / max(img.size)
        img = img.resize((max(1, round(img.width * r)), max(1, round(img.height * r))))
    out = io.BytesIO()
    img.save(out, "JPEG", quality=JPEG_QUALITY, optimize=True)
    return out.getvalue(), img.width, img.height


def _pdf_pdfium(data: bytes) -> list[tuple[bytes, int, int]]:
    import pypdfium2 as pdfium  # noqa: PLC0415 — 선택 패키지

    pdf = pdfium.PdfDocument(data)
    try:
        out = []
        for i in range(len(pdf)):
            page = pdf[i]
            try:
                out.append(_jpeg(page.render(scale=DPI / 72).to_pil()))
            finally:
                page.close()
        return out
    finally:
        pdf.close()


def _pdf_poppler(data: bytes) -> list[tuple[bytes, int, int]]:
    from PIL import Image

    exe = shutil.which("pdftoppm")
    if not exe:
        raise RuntimeError("PDF를 그림으로 바꿀 도구가 없음(pip install pypdfium2)")
    with tempfile.TemporaryDirectory(prefix="quant_pdf_") as d:
        src = Path(d) / "in.pdf"
        src.write_bytes(data)
        subprocess.run([exe, "-r", str(DPI), "-jpeg", str(src), str(Path(d) / "p")],
                       check=True, capture_output=True, timeout=300)
        files = sorted(Path(d).glob("p*.jpg"), key=lambda p: int(re.sub(r"\D", "", p.stem) or 0))
        return [_jpeg(Image.open(f)) for f in files]


def pages_of(name: str, data: bytes) -> list[tuple[bytes, int, int]]:
    """서류 하나의 쪽 그림들. 못 바꾸면 예외(이유)."""
    ext = Path(name).suffix.lower()
    if ext in (".jpg", ".jpeg", ".png", ".bmp", ".gif", ".tif", ".tiff"):
        from PIL import Image, ImageSequence

        img = Image.open(io.BytesIO(data))
        return [_jpeg(frame.copy()) for frame in ImageSequence.Iterator(img)]
    if ext != ".pdf":
        raise ValueError("PDF·그림이 아님")
    try:
        pages = _pdf_pdfium(data)
    except ImportError:
        pages = _pdf_poppler(data)
    if not pages:
        raise ValueError("쪽이 없음")
    return pages


# ── HWPX에 붙이기 ──────────────────────────────────────────────
_ID = [1900000000]


def _next_id() -> str:
    _ID[0] += 1
    return str(_ID[0])


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _pic(bin_id: str, w: int, h: int, note: str) -> str:
    """글자처럼 취급하는 그림 하나(크기·위치 잠금) — python-hwpx가 실한글로 확인한 모양과 같은 순서."""
    i = _next_id()
    return (
        f'<hp:pic id="{i}" zOrder="0" numberingType="PICTURE" textWrap="TOP_AND_BOTTOM" textFlow="BOTH_SIDES" lock="1" '
        f'dropcapstyle="None" href="" groupLevel="0" instid="{i}" reverse="0">'
        f'<hp:offset x="0" y="0"/><hp:orgSz width="{w}" height="{h}"/><hp:curSz width="{w}" height="{h}"/>'
        f'<hp:flip horizontal="0" vertical="0"/><hp:rotationInfo angle="0" centerX="{w // 2}" centerY="{h // 2}" rotateimage="1"/>'
        '<hp:renderingInfo><hc:transMatrix e1="1" e2="0" e3="0" e4="0" e5="1" e6="0"/>'
        '<hc:scaMatrix e1="1" e2="0" e3="0" e4="0" e5="1" e6="0"/><hc:rotMatrix e1="1" e2="0" e3="0" e4="0" e5="1" e6="0"/></hp:renderingInfo>'
        f'<hp:imgRect><hc:pt0 x="0" y="0"/><hc:pt1 x="{w}" y="0"/><hc:pt2 x="{w}" y="{h}"/><hc:pt3 x="0" y="{h}"/></hp:imgRect>'
        f'<hp:imgClip left="0" right="{w}" top="0" bottom="{h}"/><hp:inMargin left="0" right="0" top="0" bottom="0"/>'
        f'<hp:imgDim dimwidth="{w}" dimheight="{h}"/>'
        f'<hc:img binaryItemIDRef="{bin_id}" bright="0" contrast="0" effect="REAL_PIC" alpha="0"/><hp:effects/>'
        f'<hp:sz width="{w}" widthRelTo="ABSOLUTE" height="{h}" heightRelTo="ABSOLUTE" protect="1"/>'
        '<hp:pos treatAsChar="1" affectLSpacing="0" flowWithText="1" allowOverlap="0" holdAnchorAndSO="0" vertRelTo="PARA" '
        'horzRelTo="COLUMN" vertAlign="TOP" horzAlign="LEFT" vertOffset="0" horzOffset="0"/>'
        f'<hp:outMargin left="0" right="0" top="0" bottom="0"/><hp:shapeComment>{_esc(note)}</hp:shapeComment></hp:pic>'
    )


def _para(para_id: str, char_id: str, inner: str, page_break: bool) -> str:
    return (f'<hp:p id="0" paraPrIDRef="{para_id}" styleIDRef="0" pageBreak="{1 if page_break else 0}" columnBreak="0" merged="0">'
            f'<hp:run charPrIDRef="{char_id}">{inner}</hp:run></hp:p>')


def _text_para(para_id: str, char_id: str, text: str, page_break: bool = False) -> str:
    return _para(para_id, char_id, f"<hp:t>{_esc(text)}</hp:t>", page_break)


def _add_items(header: str, kind: str, items: list[str]) -> str:
    """header.xml의 paraProperties/charProperties 끝에 항목을 더하고 itemCnt를 늘림."""
    h = re.sub(rf"(</(?:[\w.-]+:)?{kind}>)", lambda m: "".join(items) + m.group(1), header, count=1)
    return re.sub(rf'(<(?:[\w.-]+:)?{kind}\b[^>]*\bitemCnt=")(\d+)"', lambda m: f'{m.group(1)}{int(m.group(2)) + len(items)}"', h, count=1)


def _max_id(header: str, tag: str) -> int:
    return max([int(x) for x in re.findall(rf'<(?:[\w.-]+:)?{tag}\b[^>]*\bid="(\d+)"', header)] or [0])


def _para_pr(header: str, new_id: int, align: str) -> str:
    """paraPr 0을 본떠 줄 간격 100%·여백 0·정렬만 바꾼 문단 모양(그림이 쪽 높이를 넘지 않게)."""
    m = re.search(r'<(?:[\w.-]+:)?paraPr\b[^>]*\bid="0".*?</(?:[\w.-]+:)?paraPr>', header, re.S)
    if not m:
        raise ValueError("header.xml에 문단 모양이 없음")
    p = re.sub(r'\bid="0"', f'id="{new_id}"', m.group(0), count=1)
    p = re.sub(r'(<(?:[\w.-]+:)?align\b[^>]*\bhorizontal=")[A-Z_]+"', rf'\g<1>{align}"', p)
    p = re.sub(r'(<(?:[\w.-]+:)?lineSpacing\b[^>]*\btype=")[A-Z_]+("[^>]*\bvalue=")\d+"', r'\1PERCENT\g<2>100"', p)
    p = re.sub(r'(<(?:hc:)?(?:intent|left|right|prev|next)\b[^>]*\bvalue=")-?\d+"', r'\g<1>0"', p)
    return p


def _char_pr(header: str, new_id: int, height: int) -> str | None:
    """charPr 0을 본떠 글자 크기만 바꾼 글자 모양(목록 쪽 제목·줄)."""
    m = re.search(r'<(?:[\w.-]+:)?charPr\b[^>]*\bid="0".*?</(?:[\w.-]+:)?charPr>', header, re.S)
    if not m:
        return None
    c = re.sub(r'\bid="0"', f'id="{new_id}"', m.group(0), count=1)
    c = re.sub(r'\bheight="\d+"', f'height="{height}"', c, count=1)
    return c


def _new_section(last_xml: str, body: str) -> str:
    """마지막 구역의 머리(이름공간)·쪽 설정을 본떠 A4 세로·여백 10mm인 새 구역 — 서류 쪽 그림이 쪽을 가득 채우게."""
    root = re.match(r'(<\?xml[^>]*\?>)?\s*(<(?:[\w.-]+:)?sec\b[^>]*>)', last_xml)
    if not root:
        raise ValueError("구역 XML 머리를 못 찾음")
    close = re.search(r"</(?:[\w.-]+:)?sec>\s*$", last_xml)
    sec = re.search(r"<(?:[\w.-]+:)?secPr\b.*?</(?:[\w.-]+:)?secPr>", last_xml, re.S)
    if not sec or not close:
        raise ValueError("쪽 설정(secPr)을 못 찾음")
    s = sec.group(0)
    s = re.sub(r'(<(?:[\w.-]+:)?pagePr\b[^>]*?)\blandscape="\w+"', r'\1landscape="WIDELY"', s)
    s = re.sub(r'(<(?:[\w.-]+:)?pagePr\b[^>]*?)\bwidth="\d+"', r'\1width="59528"', s)
    s = re.sub(r'(<(?:[\w.-]+:)?pagePr\b[^>]*?)\bheight="\d+"', r'\1height="84188"', s)
    s = re.sub(r'<((?:[\w.-]+:)?margin)\b[^>]*/>',
               lambda m: f'<{m.group(1)} header="0" footer="0" gutter="0" left="{MARGIN}" right="{MARGIN}" top="{MARGIN}" bottom="{MARGIN}"/>',
               s, count=1)
    s = re.sub(r'\bmasterPageCnt="\d+"', 'masterPageCnt="0"', s)
    head = (root.group(1) or "") + root.group(2)
    first = ('<hp:p id="0" paraPrIDRef="0" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0"><hp:run charPrIDRef="0">'
             '<hp:ctrl><hp:colPr id="" type="NEWSPAPER" layout="LEFT" colCount="1" sameSz="1" sameGap="0"/></hp:ctrl>'
             f'{s}</hp:run><hp:run charPrIDRef="0"><hp:t/></hp:run></hp:p>')
    return head + first + body + close.group(0)


def append_docs(hwpx: Path, docs: list[Doc], out: Path, title: str = "첨부 서류") -> Path:
    """통합서식 HWPX 뒤에 새 구역: '첨부 서류 목록' 한 쪽 + 서류마다 쪽 그림(쪽마다 새 쪽, 가운데, 쪽 안에 맞춤)."""
    hwpx, out = Path(hwpx), Path(out)
    zin = zipfile.ZipFile(hwpx)
    names = zin.namelist()
    secs = sorted((n for n in names if re.fullmatch(r"Contents/section\d+\.xml", n)),
                  key=lambda n: int(re.search(r"(\d+)\.xml$", n).group(1)))
    if not secs or "Contents/header.xml" not in names or "Contents/content.hpf" not in names:
        raise ValueError("HWPX 구성(구역·머리·목록)이 모자람")
    header = zin.read("Contents/header.xml").decode("utf-8")
    hpf = zin.read("Contents/content.hpf").decode("utf-8")
    pid = _max_id(header, "paraPr") + 1
    cid = _max_id(header, "charPr") + 1
    paras = [_para_pr(header, pid, "CENTER"), _para_pr(header, pid + 1, "LEFT")]
    chars = [c for c in (_char_pr(header, cid, 1600), _char_pr(header, cid + 1, 1100)) if c]
    center, left = str(pid), str(pid + 1)
    big, small = (str(cid), str(cid + 1)) if len(chars) == 2 else ("0", "0")

    body_w = 59528 - 2 * MARGIN
    body_h = 84188 - 2 * MARGIN
    max_h = int(body_h * 0.97)  # 줄 높이 여유 — 넘으면 빈 쪽이 생김
    used = set(re.findall(r'\bid="([^"]+)"', hpf))
    bins: list[tuple[str, str, bytes]] = []  # (id, 경로, 내용)
    n = 0

    # 첨부 서류 목록 — 순서·구분·파일 이름·쪽 수
    body = [_text_para(center, big, title)]
    body += [_text_para(left, small, " ")]
    for i, d in enumerate(docs, 1):
        body.append(_text_para(left, small, f"{i}. {d.section} — {d.name} ({len(d.pages)}쪽)"))
    for d in docs:
        for k, (jpg, pw, ph) in enumerate(d.pages, 1):
            n += 1
            bid = f"qdoc{n}"
            while bid in used:
                n += 1
                bid = f"qdoc{n}"
            used.add(bid)
            bins.append((bid, f"BinData/{bid}.jpg", jpg))
            w = body_w
            h = round(w * ph / pw)
            if h > max_h:
                h, w = max_h, round(max_h * pw / ph)
            body.append(_para(center, small, _pic(bid, w, h, f"{d.section} — {d.name} {k}/{len(d.pages)}쪽"), True))

    new_sec = f"Contents/section{int(re.search(r'(\d+)\.xml$', secs[-1]).group(1)) + 1}.xml"
    sec_xml = _new_section(zin.read(secs[-1]).decode("utf-8"), "".join(body))
    sec_id = Path(new_sec).stem  # 한글은 구역을 manifest id 'sectionN'으로 찾음 — 다른 이름이면 파일이 안 열림
    items = "".join(f'<opf:item id="{b}" href="{p}" media-type="image/jpeg" isEmbeded="1"/>' for b, p, _ in bins)
    items += f'<opf:item id="{sec_id}" href="{new_sec}" media-type="application/xml"/>'
    hpf = re.sub(r"(</(?:[\w.-]+:)?manifest>)", lambda m: items + m.group(1), hpf, count=1)
    hpf = re.sub(r"(</(?:[\w.-]+:)?spine>)", lambda m: f'<opf:itemref idref="{sec_id}" linear="yes"/>' + m.group(1), hpf, count=1)
    header = _add_items(header, "paraProperties", paras)
    if len(chars) == 2:
        header = _add_items(header, "charProperties", chars)
    # 머리의 구역 수(secCnt)
    header = re.sub(r'(<(?:[\w.-]+:)?head\b[^>]*\bsecCnt=")(\d+)"', lambda m: f'{m.group(1)}{int(m.group(2)) + 1}"', header, count=1)

    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    with zipfile.ZipFile(tmp, "w") as zout:
        for info in zin.infolist():
            if info.filename == "Contents/header.xml":
                data = header.encode("utf-8")
            elif info.filename == "Contents/content.hpf":
                data = hpf.encode("utf-8")
            else:
                data = zin.read(info.filename)
            ctype = zipfile.ZIP_STORED if info.filename == "mimetype" else zipfile.ZIP_DEFLATED
            zout.writestr(zipfile.ZipInfo(info.filename, date_time=info.date_time), data, compress_type=ctype)
        zout.writestr(new_sec, sec_xml.encode("utf-8"), compress_type=zipfile.ZIP_DEFLATED)
        for _, path, data in bins:
            zout.writestr(path, data, compress_type=zipfile.ZIP_STORED)  # JPEG은 이미 압축됨
    zin.close()
    tmp.replace(out)
    return out
