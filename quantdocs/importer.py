"""회사 원본 서류 → 회사자료 폴더(회사자료_양식.xlsx + files/) — 잠금 묶음(vault seal)의 재료.

    python -m quantdocs import --orders 공사수주현황.xlsx --corp <법인일괄서류 폴더|zip> --direct <직접생산증명서 폴더|zip> --out 회사자료

- 공사수주현황(연도별 시트, 'No.·사업명·계약종류·사업기간·계약금' 표) → 실적목록. 발주기관·분야·지분율은 원본에 없어 비움.
- 직접생산확인증명서 PDF(공공구매종합정보망 출력본) → 서류상태(세부품명번호·유효기간을 글에서 읽음).
- 법인일괄서류 → 서류상태(config/company_import.json으로 종류·업종코드), 사업자등록증 글 → 회사정보·연혁(법인 설립).
  유효기간은 PDF 글에서, 없으면(스캔본) 파일 이름 '(~27.7.31)'에서. 둘 다 없으면 비움 — 짐작해서 채우지 않음.
원본·결과 폴더는 저장소에 올리지 않는다(.gitignore). 결과 폴더를 `vault seal`로 잠가 vault/company.enc만 커밋.
"""

from __future__ import annotations

import json
import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from .catalog import load_catalog
from .templates import FILE_NAME, build as make_template

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config" / "company_import.json"


@dataclass
class ImportReport:
    tracks: int = 0
    docs: int = 0
    direct_items: int = 0
    registration: tuple[int, int] | None = None  # (등록 업종 수, 등록 품목 수)
    info: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# ── 공통 ──────────────────────────────────────────────────────
def _unzip(src: Path, work: Path) -> list[Path]:
    """폴더면 그 안 파일들, zip이면 풀어서(윈도우 압축의 한글 이름 복원)."""
    src = Path(src)
    if src.is_dir():
        return sorted(p for p in src.rglob("*") if p.is_file() and not p.name.startswith((".", "~$")))
    out = work / src.stem
    out.mkdir(parents=True, exist_ok=True)
    files = []
    with zipfile.ZipFile(src) as z:
        for i in z.infolist():
            if i.is_dir():
                continue
            name = i.filename
            if not i.flag_bits & 0x800:
                raw = name.encode("cp437")
                for enc in ("utf-8", "cp949"):
                    try:
                        name = raw.decode(enc)
                        break
                    except UnicodeDecodeError:
                        continue
            base = Path(name.replace("\\", "/")).name
            if not base or base.startswith("."):
                continue
            p = out / base
            p.write_bytes(z.read(i))
            files.append(p)
    return sorted(files)


def _pdf_text(p: Path) -> str:
    if p.suffix.lower() != ".pdf":
        return ""
    try:
        from pypdf import PdfReader

        return "\n".join((pg.extract_text() or "") for pg in PdfReader(str(p)).pages)
    except Exception:  # noqa: BLE001 — 깨진 PDF는 글 없음으로
        return ""


def _ymd(y: str, m: str, d: str) -> date | None:
    y = int(y)
    y = y + 2000 if y < 100 else y
    try:
        return date(y, int(m), int(d))
    except ValueError:
        return None


_DATE = r"(\d{2,4})\s*[.\-/년]\s*(\d{1,2})\s*[.\-/월]\s*(\d{1,2})"


def validity_from_text(text: str) -> tuple[date | None, date | None]:
    """'유효기간 : 2026-04-01 ~ 2027-03-31'·'2025.02.10 ~ 2027.02.09' → (시작, 끝)."""
    t = re.sub(r"\s+", " ", text)
    m = re.search(r"유\s*효\s*기\s*간\s*:?\s*" + _DATE + r"\s*일?\s*[.]?\s*~\s*" + _DATE, t)
    if not m:
        return None, None
    return _ymd(*m.group(1, 2, 3)), _ymd(*m.group(4, 5, 6))


def validity_from_name(name: str) -> tuple[date | None, str]:
    """파일 이름 괄호 '(~27.7.31)'·'(-29.8.22)'·'(~26년12.25)' → 만료일. 연·월만 있으면 날짜는 비우고 메모로."""
    m = re.search(r"\(\s*[~\-]?\s*`?(\d{2,4})\s*[.년]\s*(\d{1,2})\s*[.월]\s*(\d{1,2})\s*\)", name)
    if m:
        return _ymd(*m.groups()), ""
    m = re.search(r"\(\s*[~\-`]?\s*'?(\d{2})(?:년)?(?:\s*[.]\s*(\d{1,2}))?\s*\)", name)
    if m:
        return None, f"파일 이름에 유효기간 '{m.group(0)}'만 — 날짜 확인 필요"
    return None, ""


# ── 실적(공사수주현황) ────────────────────────────────────────
def _period(v) -> tuple[date | None, date | None]:
    dates = [_ymd(*g) for g in re.findall(r"(\d{4})\s*\.\s*(\d{1,2})\s*\.\s*(\d{1,2})", str(v or ""))]
    dates = [d for d in dates if d]
    if not dates:
        return None, None
    return dates[0], dates[-1] if len(dates) > 1 else None


def _work(name: str, kind: str) -> str | None:
    if "물품" in kind or re.search(r"구매|구입", name):
        return "물품"
    if re.search(r"용역", name):
        return "용역"
    if re.search(r"공사", name):
        return "공사"
    return None


def read_orders(path: Path) -> list[dict]:
    from openpyxl import load_workbook

    wb = load_workbook(path, data_only=True)
    rows = []
    for ws in wb:
        head_row, cols = None, {}
        for r in ws.iter_rows(min_row=1, max_row=min(ws.max_row, 15)):
            vals = [str(c.value or "").strip() for c in r]
            if "사업명" in vals:
                head_row = r[0].row
                cols = {v: i for i, v in enumerate(vals) if v}
                break
        if head_row is None:
            continue

        def col(*names):
            return next((cols[n] for n in names if n in cols), None)

        c_no, c_name, c_kind = col("No.", "No", "번호"), col("사업명"), col("계약종류")
        c_period, c_amount = col("사업기간"), col("계약금", "계약금액")
        for r in ws.iter_rows(min_row=head_row + 1, values_only=True):
            name = str(r[c_name] or "").strip() if c_name is not None else ""
            no = r[c_no] if c_no is not None else None
            if not name or not isinstance(no, (int, float)):
                continue  # 합계 줄·빈 줄
            kind = str(r[c_kind] or "").strip() if c_kind is not None else ""
            start, end = _period(r[c_period] if c_period is not None else "")
            amount = r[c_amount] if c_amount is not None else None
            note = [f"{ws.title} 시트", f"계약종류 {kind}" if kind else ""]
            if end is None:
                note.append("준공일 확인 필요(사업기간 칸)")
            rows.append({"업무": _work(name, kind), "사업명": name, "계약일": start, "준공일": end,
                         "계약금액(원)": amount if isinstance(amount, (int, float)) else None,
                         "비고": " · ".join(x for x in note if x)})
    return rows


# ── 직접생산확인증명서 ────────────────────────────────────────
_DIRECT_ITEM = re.compile(r"(\d{10})\s+([^\d~*]+?)\s+(\d{4})\.(\d{2})\.(\d{2})\.?\s*~\s*(\d{4})\.(\d{2})\.(\d{2})")


def read_direct(p: Path) -> list[dict]:
    """한 증명서 안 품목마다 {code, name, start, end, issued, no}."""
    text = _pdf_text(p)
    issued = re.search(r"(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일", text)
    no = re.search(r"제\s*(\d{4}-\d{4}-\d{5})\s*호", text)
    items = []
    for m in _DIRECT_ITEM.finditer(text):  # 품명이 줄을 넘겨도('…서비\n스') 읽음
        items.append({"code": m.group(1), "name": re.sub(r"\s+", "", m.group(2)), "start": _ymd(*m.group(3, 4, 5)),
                      "end": _ymd(*m.group(6, 7, 8)), "issued": _ymd(*issued.groups()) if issued else None,
                      "no": no.group(1) if no else ""})
    # 같은 품목의 지난 유효기간 줄(갱신 전 기록)은 뺌 — 가장 늦게 끝나는 줄만
    latest: dict[str, dict] = {}
    for it in items:
        if it["code"] not in latest or (it["end"] or date.min) > (latest[it["code"]]["end"] or date.min):
            latest[it["code"]] = it
    return [it for it in items if latest[it["code"]] is it]


# ── 사업자등록증 ──────────────────────────────────────────────
def read_bizreg(text: str) -> dict[str, str]:
    t = text
    info = {}
    pats = {
        "사업자등록번호": r"등록번호\s*:\s*(\d{3}-\d{2}-\d{5})",
        "회사명": r"법\s*인\s*명\s*\(\s*단\s*체\s*명\s*\)\s*:\s*(\S+)",
        "법인등록번호": r"법\s*인\s*등\s*록\s*번\s*호\s*:\s*(\d{6}-\d{7})",
        "주소": r"본\s*점\s*소\s*재\s*지\s*:\s*([^\n]+)",
    }
    for k, pat in pats.items():
        m = re.search(pat, t)
        if m:
            info[k] = m.group(1).strip()
    m = re.search(r"개\s*업\s*연\s*월\s*일\s*:\s*(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일", t)
    if m:
        info["설립일"] = f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    m = re.search(r"대표자\(대표유형\)\s*:\s*(\S*)", t)
    name = m.group(1).strip() if m else ""
    if not re.fullmatch(r"[가-힣]{2,4}", name):  # 줄이 밀려 이름이 뒤에 나오는 출력본
        m = re.search(r"본\s*점\s*소\s*재\s*지[^\n]*\n\s*([가-힣]{2,4})\s*\n", t)
        name = m.group(1) if m else ""
    if name:
        info["대표자"] = name
    info.update(_biz_kind(t))
    return info


def _biz_kind(t: str) -> dict[str, str]:
    """사업자등록증 첫 쪽 '사업의 종류'의 첫 업태·종목(주업종) — 서식 12 '업태 : 종목 :' 칸(11/04 사용자 요청).
    홈택스 출력본은 업태·종목이 세로 칸이라 긴 글이 줄바뀜('…임대 서' + '비스업') — 짧은 끝 조각은 앞 줄에 이음."""
    m = re.search(r"(?:^|\n)\s*업\s*태\s*[:：]?\s*([^\n]*)\n(.*?)(?:^|\n)\s*종\s*목\s*[:：]?\s*([^\n]*)\n(.*?)"
                  r"(?=\n[^\n]*(?:별지|발\s*급\s*사\s*유|사업자\s*단위|전자세금)|\Z)", t, re.S)
    if not m:  # 한 줄에 '업태 X 종목 Y'
        m1 = re.search(r"업\s*태\s*[:：]?\s*(\S[^\n]*?)\s+종\s*목\s*[:：]?\s*(\S[^\n]*)", t)
        return {"업태": m1.group(1).strip(), "종목": m1.group(2).strip()} if m1 else {}

    def first(head: str, rest: str) -> str:
        lines = [x.strip() for x in [head, *rest.split("\n")] if x.strip() and x.strip() not in (":", "：")]
        if not lines:
            return ""
        out = lines[0]
        if len(lines) > 1 and len(out) >= 10 and len(lines[1]) <= 3 and not out.endswith("업"):
            out += lines[1]  # 긴 글이 칸 너비에서 끊긴 끝 조각만 한 번
        return out
    out = {"업태": first(m.group(1), m.group(2)), "종목": first(m.group(3), m.group(4))}
    return {k: v for k, v in out.items() if v}


# ── 경쟁입찰참가자격등록증(나라장터 출력본) ───────────────────
def read_registration(text: str) -> dict:
    """등록 품목(세부품명번호·제조·증명서류·등록유효기간)과 등록 업종(코드·이름·상태), 회사 전화·팩스.
    대표자·대리인 개인정보는 읽지 않는다."""
    out: dict = {"info": {}, "products": [], "industries": []}
    for key, label in (("전화번호", r"전\s*화\s*번\s*호"), ("팩스번호", r"팩\s*스\s*번\s*호")):
        m = re.search(label + r"\s+(\d{2,4}-\d{3,4}-\d{4})", text)
        if m:
            out["info"][key] = m.group(1)
    # 품목 표: 줄바꿈으로 잘린 날짜('2025/08/\n04')와 품명을 이어 붙임
    a = text.find("입찰참가 등록물품")
    b = text.find("등록업종 및 시공능력")
    if a >= 0:
        t = re.sub(r"\s*\n\s*", " ", text[a:b if b > a else None])
        t = re.sub(r"(\d{4}/\d{2}/) (\d{2})", r"\1\2", t)
        for row in re.split(r"(?=\d{4}/\d{2}/\d{2} \d{10} )", t):
            m = re.match(r"\d{4}/\d{2}/\d{2} (\d{10}) (.+?) ([YN])\b(.*)", row)
            if not m:
                continue
            tail = re.split(r"등록일 세부|\* 등록유효기간|\d+ / \d+", m.group(4))[0]
            dates = [_ymd(*d.split("/")) for d in re.findall(r"\d{4}/\d{2}/\d{2}", tail)]
            proof = "직접생산증명서" if "직접생산" in tail else ("공장등록증" if "공장등록" in tail else "")
            out["products"].append({"code": m.group(1), "name": m.group(2).replace(" ", ""), "made": m.group(3) == "Y",
                                    "proof": proof, "reg_end": dates[3] if len(dates) >= 4 else None})
    if b >= 0:
        c = text.find("주력분야", b)
        t = re.sub(r"\s*\n\s*", " ", text[b:c if c > b else None])
        for m in re.finditer(r"(?<![\d/])(\d{4})\((.+?)\)(?=\s)(.*?)(정상|말소|정지|취소)", t):
            name = re.sub(r"\s+", "", m.group(2))
            name = name + ")" * (name.count("(") - name.count(")"))
            out["industries"].append({"code": m.group(1), "name": name, "status": m.group(4)})
    return out


# ── 연혁 PDF ─────────────────────────────────────────────────
_HIST_LINE = re.compile(r"^\s*[-·•ㆍ▪◦○●]?\s*((?:19|20)\d{2})\s*[.년/-]\s*(\d{1,2})\s*[.월/-]?\s*(?:(\d{1,2})(?=\s*[.일]|\s)\s*[.일]?)?\s*[.:\-–~]?\s*(\S.*)$")
_YEAR_ONLY = re.compile(r"^\s*((?:19|20)\d{2})\s*년?\s*$")
_DATE_ONLY = re.compile(r"^\s*((?:19|20)\d{2})\s*[.년/-]\s*(\d{1,2})\s*[.월]?\s*(?:(\d{1,2})\s*[.일]?)?\s*$")
_MONTH_LINE = re.compile(r"^\s*[-·•ㆍ]?\s*(\d{1,2})\s*[.월/]\s*(?:(\d{1,2})\s*[.일]?)?\s*[:\-–]?\s*(\S.*)$")


def read_history(text: str) -> list[dict]:
    """연혁 문서 글 → [{연월일, 내용}]. '2005.03 법인 설립'·'2005년 3월 …' 줄, 또는 '2005' 한 줄 뒤 '03. …' 줄.
    날짜는 일까지 있으면 date, 월까지면 '2005.03' 글자. 짐작해서 채우지 않음 — 날짜로 시작하지 않는 줄은 앞 줄 내용에 이어 붙이지도 않음."""
    out, year = [], None
    pending = None  # 표에서 '2009.09' 날짜만 한 줄, 내용은 다음 줄(11/05 받은 한글 연혁) — (연, 월, 일, [내용 줄])
    lines = [ln.strip() for ln in (text or "").splitlines()]

    def flush():
        nonlocal pending
        if pending and pending[3]:
            y, mo, d, body = pending
            when = _ymd(y, mo, d) if d else None
            if 1 <= int(mo) <= 12:
                out.append({"연월일": when or f"{y}.{int(mo):02d}", "내용": re.sub(r"\s+", " ", " ".join(body)).strip()})
        pending = None
    for line in lines:
        if not line:
            continue
        dm = _DATE_ONLY.match(line)
        if dm:
            flush()
            pending = (dm.group(1), dm.group(2), dm.group(3), [])
            year = dm.group(1)
            continue
        if pending is not None:
            if _HIST_LINE.match(line) or _YEAR_ONLY.match(line):
                flush()
            elif len(pending[3]) < 2:  # 내용이 두 줄로 넘어간 칸까지만(그 뒤 '비고' 칸 등은 안 붙임)
                pending[3].append(line)
                continue
            else:
                continue
        m = _HIST_LINE.match(line)
        if m:
            y, mo, d, body = m.groups()
            year = y
        elif (my := _YEAR_ONLY.match(line)):
            year = my.group(1)
            continue
        elif year and (mm := _MONTH_LINE.match(line)):
            y, (mo, d, body) = year, mm.groups()
        else:
            continue
        mo_i = int(mo)
        if not 1 <= mo_i <= 12 or len(body) < 2:
            continue
        when = _ymd(y, mo, d) if d else None
        out.append({"연월일": when or f"{y}.{mo_i:02d}", "내용": re.sub(r"\s+", " ", body).strip()})
    flush()
    return out


# ── 신용평가등급 확인서(11/05 사용자: 기업신용평가확인서 PDF를 폴더에 둠 — 점수와 통합본 둘 다) ──
_GRADE = r"(AAA|AA[+0\-]?|A[+0\-]?|BBB[+0\-]?|BB[+0\-]?|B[+0\-]?|CCC[+0\-]?|CC|C|D|A1|A2[+0\-]?|A3[+0\-]?)(?![A-Za-z0-9])"
_DATE = r"((?:19|20)\d{2})\s*[.\-/년]\s*(\d{1,2})\s*[.\-/월]\s*(\d{1,2})"


def read_credit(text: str) -> dict[str, str]:
    """확인서 글 → {신용평가등급, 신용평가 종류, 신용평가 평가일, 신용평가 유효기간, 신용평가 재무기준일}. 못 찾은 것은 빼고."""
    t = re.sub(r"[ \t]+", " ", text or "").replace("Ｏ", "0").replace("ｏ", "0")
    out: dict[str, str] = {}
    m = re.search(r"(?:신용\s*등급|평가\s*등급|등\s*급)\s*[:：]?\s*(?:\n\s*)?" + _GRADE, t)
    if m:
        g = m.group(1).replace("O", "0")
        out["신용평가등급"] = g
    head = re.sub(r"\s+", "", t[:600])
    out["신용평가 종류"] = "기업어음" if "기업어음" in head else "회사채" if "회사채" in head else "기업신용평가등급"
    d = re.search(r"(?:등급\s*)?평가\s*(?:일자|일|기준일|완료일)\s*[:：]?\s*" + _DATE, t) or re.search(r"(?:발급|확인)\s*일\s*[:：]?\s*" + _DATE, t)
    if d:
        out["신용평가 평가일"] = f"{d.group(1)}-{int(d.group(2)):02d}-{int(d.group(3)):02d}"
    v = re.search(r"유효\s*기간[^\n]{0,40}?[~∼～]\s*" + _DATE, t) or re.search(r"유효\s*기간\s*[:：]?\s*(?:까지\s*)?" + _DATE, t)  # '유효기간 2027년 06월 15일'(KCB 확인서)
    if v:
        out["신용평가 유효기간"] = f"{v.group(1)}-{int(v.group(2)):02d}-{int(v.group(3)):02d}"
    # '재무기준일 2025년 12월 31일'(KCB 확인서) — 등급을 매긴 재무제표의 결산 기준일(11/13 사용자 제보: 서식 13호 '재무결산기준' 칸이 빔)
    f = re.search(r"(?:재무|결산)\s*(?:기준\s*일|결산\s*일|기준)\s*[:：]?\s*" + _DATE, t)
    if f:
        out["신용평가 재무기준일"] = f"{f.group(1)}-{int(f.group(2)):02d}-{int(f.group(3)):02d}"
    if "신용평가등급" not in out:
        return {}
    return out


# 회사 '일반현황' 표(한글 연혁 문서 앞쪽) — 이름표 한 줄, 값 다음 줄. 회사정보 시트가 비었을 때만 씀
_OVERVIEW = {"회사명": "회사명", "상호": "회사명", "대표자명": "대표자", "대표자": "대표자", "주소": "주소",
             "전화번호": "전화번호", "팩스번호": "팩스번호", "사업자번호": "사업자등록번호", "사업자등록번호": "사업자등록번호",
             "자본금": "자본금", "총종업원수": "종업원 수", "총직원수": "종업원 수", "종업원수": "종업원 수"}


def read_overview(text: str) -> dict[str, str]:
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    out: dict[str, str] = {}
    for i, ln in enumerate(lines[:-1]):
        key = _OVERVIEW.get(re.sub(r"\s+", "", ln))
        nxt = lines[i + 1]
        if key and key not in out and re.sub(r"\s+", "", nxt) not in _OVERVIEW and len(nxt) <= 60:
            out[key] = nxt
    return out


def _bytes_pdf_text(data: bytes) -> str:
    try:
        import io

        from pypdf import PdfReader

        return "\n".join((pg.extract_text() or "") for pg in PdfReader(io.BytesIO(data)).pages)
    except Exception:  # noqa: BLE001 — 깨진·암호 PDF는 글 없음으로
        return ""


def _ocr_setup():
    """pytesseract 모듈(설정 반영) — 없으면 None."""
    try:
        import pytesseract
    except ImportError:
        return None
    from .vault import read_env

    cmd = read_env("TESSERACT_CMD")
    if cmd:
        pytesseract.pytesseract.tesseract_cmd = cmd
    data_dir = read_env("TESSDATA_PREFIX")
    if data_dir:
        import os

        os.environ["TESSDATA_PREFIX"] = data_dir
    return pytesseract


def ocr_pages(data: bytes, pages: list[int], dpi: int = 250) -> dict[int, str] | None:
    """PDF의 몇 쪽만 글자 인식(0부터 센 쪽 번호) → {쪽: 글}. OCR 도구가 없으면 None(11/05 — 글꼴 문제로 글이 빠진 제안요청서)."""
    tess = _ocr_setup()
    if tess is None:
        return None
    try:
        import pypdfium2 as pdfium
    except ImportError:
        return None
    out: dict[int, str] = {}
    try:
        pdf = pdfium.PdfDocument(data)
        for i in pages:
            if 0 <= i < len(pdf):
                out[i] = tess.image_to_string(pdf[i].render(scale=dpi / 72).to_pil(), lang="kor+eng")
    except Exception:  # noqa: BLE001 — tesseract·한국어 데이터 없음, 깨진 PDF
        return None if not out else out
    return out


def ocr_page_tops(data: bytes, pages: list[int], frac: float = 0.15, dpi: int = 300) -> dict[int, str] | None:
    """PDF 쪽 윗부분(높이의 frac)만 글자 인식 → {쪽: 글}. 서식 머리 '[서식 7]'이 PDF 글 정보에 없는 쪽을 찾을 때(11/05 창업진흥원 서식 7).
    psm 4(한 단 글) — 300dpi 위쪽만 잘라 읽으면 '[서식 7]'이 맞게 읽힘(psm 11은 '[서직 7]'). OCR 도구가 없으면 None."""
    tess = _ocr_setup()
    if tess is None:
        return None
    try:
        import pypdfium2 as pdfium
    except ImportError:
        return None
    out: dict[int, str] = {}
    try:
        pdf = pdfium.PdfDocument(data)
        for i in pages:
            if 0 <= i < len(pdf):
                img = pdf[i].render(scale=dpi / 72).to_pil()
                w, h = img.size
                out[i] = tess.image_to_string(img.crop((0, 0, w, int(h * frac))), lang="kor+eng", config="--psm 4")
    except Exception:  # noqa: BLE001
        return None if not out else out
    return out


def _ocr_pdf(data: bytes) -> str | None:
    """글 없는 스캔 PDF → 글(Tesseract kor+eng, 300dpi). 도구가 없으면 None.
    서버 PC: Tesseract(한국어 데이터 포함) 설치 + `pip install pytesseract pypdfium2`, 실행 파일이 PATH에 없으면 .env TESSERACT_CMD."""
    try:
        import pytesseract
    except ImportError:
        return None
    from .vault import read_env

    cmd = read_env("TESSERACT_CMD")
    if cmd:
        pytesseract.pytesseract.tesseract_cmd = cmd
    data_dir = read_env("TESSDATA_PREFIX")  # 한국어 데이터를 사용자 폴더에 둔 경우(관리자 권한 없이 설치, 11/04)
    if data_dir:
        import os

        os.environ["TESSDATA_PREFIX"] = data_dir
    try:
        import pypdfium2 as pdfium

        pdf = pdfium.PdfDocument(data)
        images = [pdf[i].render(scale=300 / 72).to_pil() for i in range(min(len(pdf), 10))]
    except ImportError:
        import shutil as _sh
        import subprocess
        from PIL import Image

        exe = _sh.which("pdftoppm")
        if not exe:
            return None
        with tempfile.TemporaryDirectory(prefix="quant_ocr_") as d:
            (Path(d) / "in.pdf").write_bytes(data)
            subprocess.run([exe, "-r", "300", "-l", "10", "-png", str(Path(d) / "in.pdf"), str(Path(d) / "p")],
                           check=True, capture_output=True, timeout=300)
            images = [Image.open(f).copy() for f in sorted(Path(d).glob("p*.png"))]
    except Exception:  # noqa: BLE001 — 깨진 PDF
        return ""
    try:
        return "\n".join(pytesseract.image_to_string(im, lang="kor+eng") for im in images)
    except Exception:  # noqa: BLE001 — tesseract 없음·한국어 데이터 없음
        return None


# 대표자 주민등록번호 — 뒷자리는 절대 남기지 않음(앞 6자리 + '-*******', 11/04 사용자 요청 '뒷자리 암호화된 것')
_RRN = re.compile(r"(?<![\d-])(\d{6})\s*-\s*([1-8*Xx][\d*Xx]{6})(?![\d])")


def masked_rrn(text: str) -> str:
    """대표자 주민등록번호 앞 6자리 + '-*******'. 법인등록번호(같은 6-7자리 모양)를 잡지 않게(11/04 제보: '110111-*******'):
    바로 앞 30자에 '법인'이 있으면(법인등록번호) 빼고, 이미 '*'로 가려진 번호(등록증)를 먼저, 그다음 '대표' 뒤 120자 안의 번호."""
    text = text or ""
    best, score = "", 0
    for m in _RRN.finditer(text):
        before = re.sub(r"\s+", "", text[max(0, m.start() - 30):m.start()])
        if "법인" in before or "사업자" in before:
            continue
        near_ceo = "대표" in re.sub(r"\s+", "", text[max(0, m.start() - 120):m.start()])
        sc = (2 if "*" in m.group(2) else 0) + (1 if near_ceo else 0)
        if near_ceo and sc > score:
            best, score = m.group(1), sc
    return f"{best}-*******" if best else ""


_TEXT_CACHE: dict[tuple[str, int, int], str] = {}


def _cached_text(path: str, data: bytes) -> str:
    """묶음 안 PDF 글 — 만들기마다 같은 파일을 다시 읽지 않게(경로·크기·내용 해시)."""
    key = (path, len(data), hash(data[:4096]))
    if key not in _TEXT_CACHE:
        ext = path.rsplit(".", 1)[-1].lower()
        if ext in ("hwp", "hwpx"):  # 한글 연혁·일반현황(11/05 받은 연혁이 .hwp)
            try:
                from nego import attachments
                _TEXT_CACHE[key] = attachments.extract_text(data, ext)
            except Exception:  # noqa: BLE001 — 못 읽는 한글 파일은 글 없음으로
                _TEXT_CACHE[key] = ""
        else:
            _TEXT_CACHE[key] = _bytes_pdf_text(data)
    return _TEXT_CACHE[key]


def enrich_vault(vault, report: list | None = None) -> list[str]:
    """묶음 안 연혁·경쟁입찰참가자격등록증·사업자등록증 PDF를 읽어 회사정보의 빈 칸과 연혁을 채움(메모리에서만, 11/04 사용자 요청:
    '연혁 pdf·입찰참가등록증 pdf 확인해서 기입 가능한 건 바로 작성'). 회사정보 시트에 적힌 값이 늘 먼저. 돌려줌: 알림 줄."""
    notes: list[str] = []
    # 회사정보 값 바로잡기 — 서버 PC .env의 QUANT_FAX·QUANT_TEL이 묶음 값보다 먼저(11/05 사용자 요청: FAX 번호 수정).
    # 번호는 공개 저장소에 적지 않고 .env에만. 이메일·홈페이지도 같은 방식(11/11 사용자 — QUANT_EMAIL·QUANT_HOMEPAGE).
    from .vault import read_env
    for env_key, item in (("QUANT_FAX", "팩스번호"), ("QUANT_TEL", "전화번호"), ("QUANT_EMAIL", "이메일"),
                          ("QUANT_HOMEPAGE", "홈페이지")):
        v = read_env(env_key).strip()
        if v:
            vault.info[item] = v
    if getattr(vault, "loose", None):
        notes.append("회사 자료 묶음 밖 PDF도 읽음(jiil-quant-docs 폴더): " + ", ".join(vault.loose[:5]))
    if getattr(vault, "patent_src", "") == "파일":
        notes.append(f"특허 건수는 특허증 파일 {len(vault.patents)}개로 셈(특허목록 시트가 비어 있음) — 파일 하나 = 1건")
    if getattr(vault, "tracks_src", ""):
        notes.append(f"실적목록은 jiil-quant-docs 폴더의 '{vault.tracks_src}'에서 읽음({len(vault.tracks)}건) — 묶음 실적의 발주기관·지분율 등은 사업명으로 살림")
    if getattr(vault, "staff_src", ""):
        notes.append(f"기술인력은 jiil-quant-docs 폴더의 '{vault.staff_src}'에서 읽음({len(vault.staff)}명) — 회사 자료 묶음의 인력목록보다 먼저")
    found_info: dict[str, tuple[str, str]] = {}  # 항목 → (값, 파일)
    hist: list[dict] = []
    hist_src = ""
    credit: dict[str, str] = {}
    credit_src = ""
    scanned, ocr, skipped_scans, regs = [], [], [], []
    for path, data in sorted(vault.files.items()):
        name = path.rsplit("/", 1)[-1]
        hangul = name.lower().endswith((".hwp", ".hwpx"))
        if not name.lower().endswith(".pdf") and not hangul:
            continue
        sq = re.sub(r"\s+", "", path)  # 폴더 이름까지('연혁/…', '기업신용평가확인/…' — 11/05 사용자가 폴더로 분류)
        by_name = [k for k, words in (("연혁", ("연혁",)), ("등록증", ("참가자격", "입찰참가")), ("사업자", ("사업자등록",)),
                                      ("인감", ("인감증명", "등기")), ("신용", ("신용평가", "신용등급")))
                   if any(w in sq for w in words)]
        text = _cached_text(path, data)
        how = "글"
        if hangul and not re.search(r"연혁|일반\s*현황", re.sub(r"\s+", "", text[:400])):
            continue  # 한글 파일은 연혁·일반현황 문서만(서식 등 다른 한글 파일은 안 봄)
        if len(re.sub(r"\s", "", text)) < 20:  # 스캔본 — 이름으로 짐작되는 서류만 글자 인식(OCR, 느려서)
            if not by_name:
                skipped_scans.append(name)
                if report is not None:
                    report.append((name, "스캔본(이름으로 종류를 몰라 안 읽음)", ""))
                continue
            text = _ocr_pdf(data)
            if text is None:
                scanned.append(name)
                if report is not None:
                    report.append((name, "스캔본(OCR 도구 없음)", "·".join(by_name)))
                continue
            ocr.append(name)
            how = "OCR"
        # 파일 이름이 제각각이라 글 내용으로도 서류 종류를 알아봄(11/04 제보: 등록증이 있는데 전화·팩스를 못 읽음)
        head = re.sub(r"\s+", "", text[:400])
        kinds = set(by_name)
        if "경쟁입찰참가자격등록증" in re.sub(r"\s+", "", text):
            kinds.add("등록증")
        if "사업자등록증" in head:
            kinds.add("사업자")
        if "인감증명" in head:
            kinds.add("인감")
        if re.search(r"(회사|사업|주요)?연혁", head):
            kinds.add("연혁")
        if re.search(r"신용(?:평가|등급)", head):
            kinds.add("신용")
        if report is not None:
            report.append((name, how, "·".join(sorted(kinds))))
        if not kinds:
            continue
        if "등록증" in kinds:
            regs.append(name)
        if kinds & {"인감", "사업자", "등록증"}:
            rrn = masked_rrn(text)
            if rrn:
                found_info.setdefault("대표자 주민등록번호", (rrn, name))
        if kinds == {"인감"}:
            continue
        if "신용" in kinds and not credit:  # 확인서 값이 회사정보 시트보다 먼저(해마다 6월 갱신되는 서류라)
            credit, credit_src = read_credit(text), name
        if "연혁" in kinds and not hist:
            hist, hist_src = read_history(text), name
        if "연혁" in kinds and re.search(r"일반\s*현황", head):  # '일반현황 및 연혁' 문서 — 회사명·전화·팩스 등
            for k, v in read_overview(text).items():
                found_info.setdefault(k, (v, name))
        if "등록증" in kinds:
            for k, v in read_registration(text)["info"].items():
                found_info.setdefault(k, (v, name))
        if "사업자" in kinds:
            for k, v in read_bizreg(text).items():
                found_info.setdefault(k, (v, name))
    filled = []
    for k, (v, src) in found_info.items():
        if not vault.info.get(k):
            vault.info[k] = v
            filled.append(f"{k}({src})")
    if filled:
        notes.append("회사정보 빈 칸을 서류 PDF에서 읽어 채움: " + ", ".join(filled))
    if credit:
        for k, val in credit.items():
            vault.info[k] = val
        notes.append(f"신용평가등급 {credit['신용평가등급']}({credit['신용평가 종류']}"
                     + (f", 평가일 {credit['신용평가 평가일']}" if credit.get("신용평가 평가일") else "") + f")을 '{credit_src}'에서 읽음 — 자기평점 경영상태에 씀")
        end = credit.get("신용평가 유효기간")
        if end and end < f"{date.today():%Y-%m-%d}":
            notes.append(f"⚠ 신용평가등급 확인서 유효기간({end})이 지났음 — 새 확인서로 바꾸세요")
    if hist and len(hist) > len(vault.history):  # 시트 연혁(보통 법인 설립 한 줄)보다 많으면 PDF 연혁을 씀
        vault.history = hist
        notes.append(f"연혁 {len(hist)}줄을 '{hist_src}'에서 읽어 넣음 — 줄 확인")
    if ocr:
        notes.append("스캔본이라 글자 인식(OCR)으로 읽은 서류: " + ", ".join(ocr[:5]) + " — 채운 값(특히 숫자·연혁) 원본과 대조")
    if scanned:
        notes.append("글이 없는 스캔본인데 글자 인식 도구(Tesseract)가 없어 읽지 못한 서류: " + ", ".join(scanned[:5])
                     + " — 서버 PC에 Tesseract 설치(docs/quantdocs.md) 또는 회사자료 양식에 직접 적기")
    # 서식에 꼭 들어가야 하는 값을 못 찾았으면 이유와 할 일을 분명히(11/04 제보: 같은 빈칸이 되풀이)
    def has(words, bad=()):
        return any(v and any(w in k.upper() for w in words) and not any(b in k.upper() for b in bad)
                   for k, v in vault.info.items())
    why = (f"등록증으로 알아본 PDF({', '.join(regs[:3])})에 그 줄이 없음" if regs
           else "묶음 안에 경쟁입찰참가자격등록증으로 알아본 PDF가 없음")
    if skipped_scans:
        why += f" · 종류를 모르는 스캔본 {len(skipped_scans)}개는 안 읽음(파일 이름에 '참가자격등록증'을 넣으면 OCR로 읽음)"
    fix = "회사자료 양식 '회사정보' 시트에 적거나 나라장터 등록증 PDF를 files/에 넣고 다시 잠그세요"
    if not has(("전화", "TEL"), ("휴대", "담당", "팩스", "FAX")):
        notes.append(f"⚠ 회사 전화번호를 찾지 못해 서식의 전화번호 칸이 비어 있음 — {why}. {fix}")
    if not has(("주민",)):
        notes.append(f"⚠ 대표자 주민등록번호(앞자리)를 찾지 못해 비어 있음 — 등록증·인감증명서에서 못 읽음. {fix}")
    return notes


def _fill_info(wb, info: dict, rep) -> None:
    ws = wb["회사정보"]
    for r in range(2, ws.max_row + 1):
        k = ws.cell(r, 1).value
        if k in info and not ws.cell(r, 2).value:
            ws.cell(r, 2, info[k])
    rep.info = sorted(set(rep.info) | set(info))


def _put(ws, row: list) -> None:
    """양식은 300줄까지 서식이 걸려 있어 append하면 그 아래로 감 — 첫 빈 줄에 씀."""
    r = 2
    while any(ws.cell(r, c).value not in (None, "") for c in range(1, ws.max_column + 1)):
        r += 1
    for c, v in enumerate(row, 1):
        ws.cell(r, c, v)


# ── 묶어서 ────────────────────────────────────────────────────
def run(out: Path, orders: Path | None = None, corp: Path | None = None, direct: Path | None = None,
        today: date | None = None, registration: Path | None = None) -> ImportReport:
    from openpyxl import load_workbook

    today = today or date.today()
    out = Path(out)
    rep = ImportReport()
    if (out / FILE_NAME).exists():
        raise FileExistsError(f"이미 있습니다 — 채운 양식일 수 있어 덮어쓰지 않습니다: {out / FILE_NAME}")
    make_template(out)
    files_dir = out / "files"
    files_dir.mkdir(parents=True, exist_ok=True)
    wb = load_workbook(out / FILE_NAME)
    cat = {d.id: d.name.split(" (")[0] for d in load_catalog()}
    rules = json.loads(CONFIG.read_text(encoding="utf-8"))["corp_docs"]
    work = Path(tempfile.mkdtemp(prefix="quantdocs_import_"))
    doc_rows: list[list] = []

    def doc(kind, name, codes, issued, end, rule, fname, note=""):
        status = "재발급" if end and end < today else "사용"
        if status == "재발급":
            note = " · ".join(x for x in [note, f"만료({end:%Y-%m-%d}) — 갱신본 받을 것"] if x)
        doc_rows.append([len(doc_rows) + 1, cat.get(kind, kind), name, ", ".join(codes), issued, end,
                         rule or ("유효기간 갱신" if end else "그대로 둠"), status, fname, None, note or None])

    try:
        if orders:
            ws = wb["실적목록"]
            for i, t in enumerate(read_orders(Path(orders)), 1):
                from .notice import track_field
                _put(ws, [i, t["업무"], t["사업명"], None, t["계약일"], t["준공일"], t["계약금액(원)"], None, None,
                           track_field(t["사업명"]), None, None, t["비고"]])
                rep.tracks += 1
            rep.warnings.append("실적목록: 분야는 사업명으로 자동 분류(확인 필요) · 발주기관·부가세·지분율·증빙 파일은 원본에 없어 비움 — 실적증명서와 함께 채울 것")
        if direct:
            for p in _unzip(Path(direct), work):
                items = read_direct(p) if p.suffix.lower() == ".pdf" else []
                dest = f"직접생산/{p.name}"
                (files_dir / "직접생산").mkdir(exist_ok=True)
                shutil.copy2(p, files_dir / dest)
                if not items:
                    end, note = validity_from_name(p.name)
                    doc("direct_production", f"직접생산확인증명서 {p.stem}", [], None, end, "", dest,
                        note or "증명서 글에서 품목을 못 읽음 — 세부품명번호 직접 입력")
                    rep.warnings.append(f"직접생산 {p.name}: 품목을 못 읽음")
                    continue
                for it in items:
                    doc("direct_production", f"직접생산확인증명서 {it['name']}", [it["code"]], it["issued"], it["end"],
                        "", dest, f"발급번호 {it['no']}" if it["no"] else "")
                    rep.direct_items += 1
        if corp:
            (files_dir / "법인").mkdir(exist_ok=True)
            for p in _unzip(Path(corp), work):
                dest = f"법인/{p.name}"
                shutil.copy2(p, files_dir / "법인" / p.name)
                rule = next((r for r in rules if r["keyword"] in p.name.replace(" ", "")), None)
                text = _pdf_text(p)
                start, end = validity_from_text(text)
                note = ""
                if end is None:
                    end, note = validity_from_name(p.name)
                if rule is None:
                    doc("qualification", p.stem, [], None, end, "", dest,
                        " · ".join(x for x in [note, "종류 미분류 — config/company_import.json에 추가"] if x))
                    rep.warnings.append(f"법인 {p.name}: 종류를 몰라 '자격 서류'로 둠")
                    continue
                if not text.strip() and p.suffix.lower() == ".pdf":
                    note = " · ".join(x for x in [note, "스캔본(글 없음) — 발급일·유효기간은 원본 확인"] if x)
                doc(rule["kind"], rule["name"], rule["codes"], None, end, rule.get("rule", ""), dest, note)
                if rule["keyword"] == "사업자등록증" and text:
                    info = read_bizreg(text)
                    _fill_info(wb, info, rep)
                    if "설립일" in info:
                        _put(wb["연혁"], [datetime.strptime(info["설립일"], "%Y-%m-%d").date(),
                                          f"{info.get('회사명', '법인')} 개업(법인 설립)", dest])
        if registration:
            p = Path(registration)
            dest = f"법인/{p.name}"
            (files_dir / "법인").mkdir(exist_ok=True)
            shutil.copy2(p, files_dir / dest)
            reg = read_registration(_pdf_text(p))
            _fill_info(wb, reg["info"], rep)
            ok = [i for i in reg["industries"] if i["status"] == "정상"]
            doc("qualification", "경쟁입찰참가자격등록증(등록 업종)", [i["code"] for i in ok], None, None, "", dest,
                "업종: " + ", ".join(f"{i['code']} {i['name']}" for i in ok))
            direct_codes = {c for r in doc_rows if r[1] == cat["direct_production"] and r[7] == "사용"
                            for c in str(r[3]).split(", ") if c}
            for it in reg["products"]:
                note = "제조 아님(N)" if not it["made"] else (f"증명: {it['proof']}" if it["proof"] else "")
                if it["reg_end"] and it["reg_end"] < today and it["code"] in direct_codes:
                    note += " · 직접생산확인증명서는 갱신됨 — 나라장터 등록 품목 유효기간도 갱신됐는지 확인"
                    rep.warnings.append(f"등록 품목 {it['name']}({it['code']}): 등록증엔 만료, 직접생산증명서는 유효 — 나라장터 갱신 확인")
                doc("qualification", f"등록 품목 {it['name']}", [it["code"]], None, it["reg_end"], "", dest, note)
                if (it["proof"] == "직접생산증명서" and (it["reg_end"] is None or it["reg_end"] >= today)
                        and direct and it["code"] not in direct_codes):
                    rep.warnings.append(f"등록 품목 {it['name']}({it['code']})은 유효한데 직접생산확인증명서 파일이 묶음에 없음")
            rep.registration = (len(ok), len(reg["products"]))
        ws = wb["서류상태"]
        for row in doc_rows:
            _put(ws, row)
        rep.docs = len(doc_rows)
        wb.save(out / FILE_NAME)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return rep
