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
    return info


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
_MONTH_LINE = re.compile(r"^\s*[-·•ㆍ]?\s*(\d{1,2})\s*[.월/]\s*(?:(\d{1,2})\s*[.일]?)?\s*[:\-–]?\s*(\S.*)$")


def read_history(text: str) -> list[dict]:
    """연혁 문서 글 → [{연월일, 내용}]. '2005.03 법인 설립'·'2005년 3월 …' 줄, 또는 '2005' 한 줄 뒤 '03. …' 줄.
    날짜는 일까지 있으면 date, 월까지면 '2005.03' 글자. 짐작해서 채우지 않음 — 날짜로 시작하지 않는 줄은 앞 줄 내용에 이어 붙이지도 않음."""
    out, year = [], None
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
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
    return out


def _bytes_pdf_text(data: bytes) -> str:
    try:
        import io

        from pypdf import PdfReader

        return "\n".join((pg.extract_text() or "") for pg in PdfReader(io.BytesIO(data)).pages)
    except Exception:  # noqa: BLE001 — 깨진·암호 PDF는 글 없음으로
        return ""


def enrich_vault(vault) -> list[str]:
    """묶음 안 연혁·경쟁입찰참가자격등록증·사업자등록증 PDF를 읽어 회사정보의 빈 칸과 연혁을 채움(메모리에서만, 11/04 사용자 요청:
    '연혁 pdf·입찰참가등록증 pdf 확인해서 기입 가능한 건 바로 작성'). 회사정보 시트에 적힌 값이 늘 먼저. 돌려줌: 알림 줄."""
    notes: list[str] = []
    found_info: dict[str, tuple[str, str]] = {}  # 항목 → (값, 파일)
    hist: list[dict] = []
    hist_src = ""
    scanned = []
    for path, data in sorted(vault.files.items()):
        name = path.rsplit("/", 1)[-1]
        if not name.lower().endswith(".pdf"):
            continue
        sq = re.sub(r"\s+", "", name)
        kinds = [k for k, words in (("연혁", ("연혁",)), ("등록증", ("참가자격", "입찰참가")), ("사업자", ("사업자등록",)))
                 if any(w in sq for w in words)]
        if not kinds:
            continue
        text = _bytes_pdf_text(data)
        if len(re.sub(r"\s", "", text)) < 20:
            scanned.append(name)
            continue
        if "연혁" in kinds and not hist:
            hist, hist_src = read_history(text), name
        if "등록증" in kinds:
            for k, v in read_registration(text)["info"].items():
                found_info.setdefault(k, (v, name))
        for k, v in read_bizreg(text).items():
            found_info.setdefault(k, (v, name))
    filled = []
    for k, (v, src) in found_info.items():
        if not vault.info.get(k):
            vault.info[k] = v
            filled.append(f"{k}({src})")
    if filled:
        notes.append("회사정보 빈 칸을 서류 PDF에서 읽어 채움: " + ", ".join(filled))
    if hist and len(hist) > len(vault.history):  # 시트 연혁(보통 법인 설립 한 줄)보다 많으면 PDF 연혁을 씀
        vault.history = hist
        notes.append(f"연혁 {len(hist)}줄을 '{hist_src}'에서 읽어 넣음 — 줄 확인")
    if scanned:
        notes.append("글이 없는 스캔본이라 읽지 못한 서류: " + ", ".join(scanned[:5]) + " — 회사자료 양식에 직접 적어 주세요")
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
