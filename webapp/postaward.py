"""낙찰 후 서류 — 1단계: 낙찰 여부 확인 목록.

투찰 기록(bid_log.json)에 있는 공고와 사람이 직접 넣은 공고를 지켜보다가, 나라장터 낙찰현황에 낙찰자가 뜨면
지일이면 '낙찰', 다른 업체면 '다른 업체 낙찰'로 바꾼다. 매일 22시 밤 작업(bidnightly)이 부르고, 화면에서 바로 확인할 수도 있다.
개찰 1순위는 적격심사에서 바뀔 수 있어 쓰지 않는다 — 낙찰현황(심사 끝난 뒤 낙찰자)만 본다.

실측(2026-11-10): 낙찰현황은 공사·용역·물품 오퍼레이션이 따로, 공고 하나는 inqryDiv=4 + bidNtceNo.
개찰부터 낙찰 확정까지 20일 걸린 공사가 있어 GIVE_UP_DAYS를 넉넉히 둔다.
"""
from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from nego import fields as F
from nego.region import sido_of

KST = timezone(timedelta(hours=9))
STATE_FILE = "postaward.json"
GIVE_UP_DAYS = 60  # 추가(개찰)한 뒤 이만큼 지나도 낙찰자가 없으면 '결과 없음'
WORKS = ("공사", "용역", "물품")
GROUPS = {"공사": "공사", "용역": "물품·용역", "물품": "물품·용역"}  # 화면·폴더 구분(사용자 결정 11/10)
_NO_RE = re.compile(r"^[A-Z0-9]{8,14}$")


def _now() -> datetime:
    return datetime.now(KST)


def _digits(text: Any) -> str:
    return re.sub(r"\D", "", str(text or ""))


def split_no(text: str) -> tuple[str, str]:
    """'R26BK01800345-000' → (번호, 차수). 차수가 없으면 ''."""
    raw = re.sub(r"\s", "", str(text or "")).upper()
    no, _, ord_ = raw.partition("-")
    if not _NO_RE.match(no):
        raise ValueError("공고번호 형식이 맞지 않습니다 (예: R26BK01800345-000)")
    return no, ord_[:3]


def group_of(work: str) -> str:
    return GROUPS.get(work or "", "업무 미확인")


# 표준틀이 만드는 서류(시험본 v4 탭 이름에서) — 틀이 정해지면 이 목록만 바꾼다. 서류 이름에 이 낱말이 있으면 '틀에 있음'
TEMPLATE_DOCS = ("공문", "착수계", "착공계", "준공계", "납품계", "완료계", "청구서", "조직도", "선임신고서", "보안각서", "재직증명서", "참여기술자")


def required_docs(files: list[Path]) -> list[dict]:
    """첨부가 요구하는 제출서류(정량평가서의 제출서류 읽기 quantdocs.notice.read_submit_items). 같은 이름은 한 번."""
    from quantdocs.notice import doc_text, read_submit_items

    out, seen = [], set()
    # 같은 문서가 한글·PDF로 함께 오면 한글 쪽을 먼저(PDF는 띄어쓰기가 흐트러짐)
    for f in sorted(files, key=lambda f: f.suffix.lower() == ".pdf"):
        if f.suffix.lower() not in (".hwp", ".hwpx", ".pdf", ".docx"):
            continue
        try:
            items = read_submit_items(doc_text(f), f.name, [])
        except Exception:  # noqa: BLE001 — 한 첨부를 못 읽어도 나머지로
            continue
        for it in items:
            name = re.sub(r"\s+", "", it.text)
            if not re.search(r"[가-힣A-Za-z]", name) or name[:20] in seen:  # 구분선 '-----', 한글·PDF 같은 줄
                continue
            seen.add(name[:20])
            out.append({"text": it.text, "source": f.name, "covered": any(w in name for w in TEMPLATE_DOCS)})
    return out


# 착공·착수 표준틀 v5(차장님 확인 11/11) — 회사 정보가 들어 있어 저장소 밖 NAS 공유 폴더 '낙찰후서류/_표준틀' 한 곳에 둠
NAS_TEMPLATES = Path(r"\\jiilserver\jiil-webapp\낙찰후서류\_표준틀")


def template_dir(root: Path) -> Path:
    """표준틀 폴더 — POSTAWARD_TEMPLATE_DIR > NAS 공유 폴더(서버 PC도 여기서 읽어 틀을 따로 복사하지 않음, 11/11) > root/_표준틀(NAS 컨테이너 안)."""
    env = os.environ.get("POSTAWARD_TEMPLATE_DIR", "").strip()
    for d in ([Path(env)] if env else []) + [NAS_TEMPLATES]:
        try:
            if d.is_dir():
                return d
        except OSError:  # 공유 폴더가 안 닿으면 다음 후보로
            pass
    return Path(root) / "_표준틀"


TEMPLATES = {"공사": ("표준틀_착공서류(공사).xlsx", "2_착공", "착공서류.xlsx"),
             "물품·용역": ("표준틀_착수서류(물품·용역).xlsx", "2_착수", "착수서류.xlsx")}
# 준공·청구 표준틀(11/18) — 업무 3개로 고름(공사 준공계 · 물품 납품계 · 용역 완료계, 폴더 구분 2개와 따로), 청구서는 공통.
# 기본사항 A열 이름으로 채움(틀마다 줄 위치가 달라도 코드 하나). 파란 칸(계약서 값)을 자동으로.
DONE_TEMPLATES = {"공사": ("표준틀_준공계.xlsx", "3_준공", "준공계.xlsx"),
                  "물품": ("표준틀_납품계.xlsx", "3_준공", "납품계.xlsx"),
                  "용역": ("표준틀_완료계.xlsx", "3_준공", "완료계.xlsx")}
CLAIM_TEMPLATE = ("표준틀_대금청구서.xlsx", "4_청구", "청구서.xlsx")
DONE_LABELS = {"to": ("수신자",), "title": ("공사명", "사업명", "용역명"), "amount": ("계약금액",), "total": ("준공금액",),
               "contract": ("계약년월일",), "start": ("착공년월일",), "due": ("준공예정년월일", "완료기한"),
               "gwiha": ("귀하", "귀하(재무관)"), "nap": ("납품서 붙임",)}
# 착공·착수서류 기본사항(담당자가 계약 때 채움) A열 이름 → 값 이름
FROM_START = {"계약건명": "title", "공사명": "title", "계약금액(금차)": "amount", "도급액": "amount", "준공금액(총액)": "total",
              "금액2": "total", "계약일": "contract", "착수일": "start", "착공일": "start", "준공예정일": "due", "수신": "gwiha"}

# 나라장터 계약정보(11/18 사용자 "붙여 줘") — 공고번호 → 계약과정통합공개(계약번호·계약일) → 계약정보 목록(등록일 기준 하루씩)에서
# 같은 공고·계약번호를 찾아 계약일·착수일·준공예정일·계약금액·담당부서. 찾은 건은 통합계약번호로 기억해 다음엔 한 번에.
# 실측(홍천 설계용역 R25TE02018742): 계약기관+담당부서 = 완료계 수신자, wbgnDate = 착수일. 날짜 칸은 inqryBgnDt(년월일시분).
CNTRCT_BASE_URL = "https://apis.data.go.kr/1230000/ao/CntrctInfoService"
CNTRCT_OPS = {"용역": "getCntrctInfoListServc", "공사": "getCntrctInfoListCnstwk", "물품": "getCntrctInfoListThng"}
INTG_BASE_URL = "https://apis.data.go.kr/1230000/ao/CntrctProcssIntgOpenService"
INTG_OP = "getCntrctProcssIntgOpenServc"
CONTRACT_SCAN_DAYS = 10  # 계약일부터 나라장터 등록일까지 며칠 걸릴 수 있음(홍천: 계약 3/12 → 등록 3/14)


def _date(text: Any) -> datetime | None:
    try:
        return datetime.strptime(str(text or "").strip()[:10], "%Y-%m-%d")
    except ValueError:
        return None


def contract_values(row: dict) -> dict[str, Any]:
    """계약정보 한 줄 → 준공·청구 틀 값 이름. 빈 값은 뺌."""
    amt = lambda k: int(_digits(row.get(k)) or 0) or None  # noqa: E731
    inst, dept = str(row.get("cntrctInsttNm") or "").strip(), str(row.get("cntrctInsttChrgDeptNm") or "").strip()
    to = inst if dept and dept in inst else " ".join(x for x in (inst, dept) if x)  # 에코팜랜드: 기관 이름에 부서 이름이 이미 있음
    out = {"title": str(row.get("cntrctNm") or "").strip(), "amount": amt("thtmCntrctAmt"), "total": amt("totCntrctAmt"),
           "contract": _date(row.get("cntrctDate") or row.get("cntrctCnclsDate")), "start": _date(row.get("wbgnDate")),
           "due": _date(row.get("thtmScmpltDate")),
           # 조달청 대행 계약은 계약기관이 조달청 — 준공·납품 신고 수신은 수요기관이라 그대로 둠(공고의 수요기관)
           "to": "" if "조달청" in str(row.get("cntrctInsttNm") or "") else to}
    return {k: v for k, v in out.items() if v not in (None, "")}


def find_contract(client: Any, no: str, work: str, own_bizno: str, known: str = "") -> dict | None:
    """공고의 나라장터 계약정보 한 줄(없으면 None — 아직 계약 전). known = 지난번 찾은 통합계약번호."""
    op = CNTRCT_OPS.get(work)
    if not op:
        return None
    if known:
        rows = client.fetch_all_pages(CNTRCT_BASE_URL, op, {"inqryDiv": "2", "untyCntrctNo": known}, "계약정보")
        if rows:
            return rows[-1]
    refs, days = set(), []
    for r in client.fetch_all_pages(INTG_BASE_URL, INTG_OP, {"inqryDiv": "1", "bidNtceNo": no}, "계약과정통합공개"):
        for entry in re.findall(r"\[([^\]]*)\]", str(r.get("cntrctInfoList") or "")):
            parts = entry.split("^")  # [순번^계약번호^계약명^계약기관^수요기관^방법^금액^계약일]
            if len(parts) >= 8:
                refs.add(parts[1].strip())
                if (d := _date(parts[7])) is not None:
                    days.append(d)
    refs.discard("")
    if not days:
        return None
    own = _digits(own_bizno)
    for n in range(CONTRACT_SCAN_DAYS + 1):
        day = (min(days) + timedelta(days=n)).strftime("%Y%m%d")
        for row in client.fetch_all_pages(CNTRCT_BASE_URL, op, {"inqryDiv": "1", "inqryBgnDt": day + "0000", "inqryEndDt": day + "2359"}, "계약정보"):
            if str(row.get("ntceNo") or "") == no or ({str(row.get("cntrctRefNo") or ""), str(row.get("dcsnCntrctNo") or "")} & refs):
                if not own or own in _digits(row.get("corpList")):
                    return row
    return None


DONE_NAMES = {"to": "수신자", "title": "건명", "amount": "계약금액", "total": "준공금액", "contract": "계약년월일",
              "start": "착공(착수)년월일", "due": "준공예정일(완료기한)", "gwiha": "귀하", "nap": "납품서 붙임"}


def _labels(xlsx: Path) -> set[str]:
    """기본사항 A열 이름들."""
    from openpyxl import load_workbook

    ws = load_workbook(xlsx)["기본사항"]
    return {str(ws.cell(r, 1).value or "").strip() for r in range(1, ws.max_row + 1)}


def done_todo(xlsx: Path, auto: dict[str, str]) -> tuple[list[str], list[list[str]]]:
    """준공·청구 서류 기본사항에서 (빈 노랑·파랑 칸 이름 — 안내에 '비우면'·'때만'이 있는 칸은 뺌, [자동으로 채운 칸 이름, 값])."""
    from openpyxl import load_workbook

    ws = load_workbook(xlsx)["기본사항"]
    names = {label: key for key, labels in DONE_LABELS.items() for label in labels}
    todo, filled = [], []
    for r in range(5, ws.max_row + 1):
        name, cell = str(ws.cell(r, 1).value or "").strip(), ws.cell(r, 2)
        if not name:
            continue
        if cell.value in (None, ""):
            hint = str(ws.cell(r, 3).value or "")
            if cell.fill.fgColor.rgb in (_YELLOW, _BLUE) and "비우면" not in hint and "때만" not in hint:
                todo.append(name)
        elif names.get(name) in auto and _key(cell.value) == auto[names[name]]:
            filled.append([name, f"{cell.value:,}" if isinstance(cell.value, int) else _key(cell.value)])
    return todo, filled


def start_values(xlsx: Path) -> dict[str, Any]:
    """착공·착수서류 기본사항에서 담당자가 넣은 계약 값(빈 칸·수식은 뺌)."""
    from openpyxl import load_workbook

    ws = load_workbook(xlsx)["기본사항"]
    out: dict[str, Any] = {}
    for r in range(4, 22):
        key = FROM_START.get(str(ws.cell(r, 1).value or "").strip())
        val = ws.cell(r, 2).value
        if key and val not in (None, "") and not (isinstance(val, str) and val.startswith("=")):
            out.setdefault(key, val)
    return out


def _key(value: Any) -> str:
    """같은 값인지 비교용 글(날짜·숫자 모양이 달라도)."""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, float) and value == int(value):
        value = int(value)
    return "" if value is None else str(value).strip()


def fill_done(source: Path, target: Path, values: dict[str, Any], prev: dict[str, str]) -> tuple[dict[str, str], bool]:
    """준공·청구 틀(source)을 target에 복사해 기본사항의 이름이 맞는 칸(B열)에 값. target이 이미 있으면 그 파일에서
    빈 칸·틀 기본 수식·지난번 자동 값 그대로인 칸만 씀 —
    담당자가 고친 칸은 안 건드림. (이번에 자동으로 둔 값 {값 이름: 글}, 파일을 바꿨는지)."""
    from openpyxl import load_workbook

    from openpyxl.styles import PatternFill

    fresh = not target.exists()  # 처음(틀에서 복사) — 틀 기본값(예: 납품서 붙임 '예')도 자동 값으로 바꿈
    wb = load_workbook(source if fresh else target)
    ws = wb["기본사항"]
    names = {label: key for key, labels in DONE_LABELS.items() for label in labels}
    done, changed = {}, False
    for r in range(1, ws.max_row + 1):
        key = names.get(str(ws.cell(r, 1).value or "").strip())
        if not key or values.get(key) in (None, ""):
            continue
        cell, new = ws.cell(r, 2), values[key]
        cur = cell.value
        if not (fresh or cur in (None, "") or (isinstance(cur, str) and cur.startswith("=")) or _key(cur) == prev.get(key)):
            continue  # 담당자가 고친 값
        if _key(cur) != _key(new) or (isinstance(cur, str) and cur.startswith("=")):
            cell.value, changed = new, True
            if cell.fill.fgColor.rgb == _YELLOW:  # 노란 칸(계약일 등 — 보통 담당자가 적음)을 자동으로 채웠으면 파랑으로(11/18 사용자)
                cell.fill = PatternFill("solid", fgColor=_BLUE)
        done[key] = _key(new)
    if changed or fresh:
        target.parent.mkdir(parents=True, exist_ok=True)
        wb.save(target)
    return done, changed


_SITE = re.compile(r"공\s*사\s*(?:위\s*치|장\s*소|현\s*장)\s*[:：]\s*([^\n]{4,60})")
_PERIOD = re.compile(r"(?:착\s*공|착\s*수|계\s*약)\s*일\s*(?:로\s*)?부\s*터\s*(\d{1,4})\s*일")
_LONG = re.compile(r"장\s*기\s*계\s*속\s*(?:계\s*약|공\s*사)")


SUMMARY = "공고정보_요약.pdf"


def write_summary(path: Path, key: str, v: dict, nf) -> bool:
    """공고문서/공고정보_요약.pdf — 1억 미만 폴더와 같은 모양(기본 정보 + 참가 자격·제출 서류·유의사항 원문, 11/11 사용자)."""
    from under100m.__main__ import read_docs
    from under100m.sections import find_sections
    from under100m.summary import won, write_award_summary_pdf

    win = v.get("winner") or {}
    amount = _digits(str(win.get("amount") or "").split(".")[0])
    rows = [("공고기관·수요기관", nf.institution or v.get("institution")), ("업무", nf.work_type or v.get("work")),
            ("계약방법", nf.contract_method), ("낙찰방법", nf.award_method), ("공고일", nf.posted),
            ("배정예산", won(nf.budget) if nf.budget and nf.budget >= 1000 else "비공개 또는 찾지 못함 — 공고문 확인"),
            ("면허제한", " / ".join(nf.licenses)),
            ("낙찰", f"{win.get('name') or '-'} · {won(int(amount))}" if amount else "아직 모름(낙찰 확인 전)")]
    docs = read_docs(nf.files)
    sections = find_sections([(f.name, text) for f, text in docs])
    return write_award_summary_pdf(path, key, nf.title, rows, sections, [f.name for f, _ in docs], datetime.now())


def basic_values(v: dict, title: str, institution: str, contract_method: str, texts: list[str]) -> tuple[dict, list[str]]:
    """기본사항 행 → (값, 출처) — 표준틀 v5 칸 위치(4 업무·5 발주기관·8 건명·11 계약 형태·12 금액·17 준공예정일 메모·21 공사위치).
    계약번호·계약일·수신·문서번호·인력은 계약 뒤에 정해지거나 사람이 고르는 값이라 비움. 돌려주는 줄 = 서류_확인.txt 안내."""
    work, gong = v.get("work", ""), v.get("work") == "공사"
    joined = "\n".join(texts)
    out: dict[int, tuple[Any, str]] = {}
    if work in WORKS:
        out[4] = (work, "나라장터 업무")
    if institution:
        out[5] = (institution, "나라장터 수요기관")
    if title:
        out[8] = (title, "나라장터 공고명 — 계약서 건명과 대조")
    amount = _digits(str((v.get("winner") or {}).get("amount") or "").split(".")[0])
    if amount:
        out[12] = (int(amount), "나라장터 낙찰금액 — 계약금액과 대조")
    if not gong and ("장기" in contract_method or _LONG.search(joined)):
        out[11] = ("장기계속", "계약방법·공고문의 '장기계속' — 차수 금액 확인")
    site = _SITE.search(joined) if gong else None
    if site:
        out[21] = (site.group(1).strip(" ,."), "공고문 공사위치")
    notes = []
    period = _PERIOD.search(joined)
    if period:
        notes.append(f"준공예정일: 공고문에 '{re.sub(chr(10), ' ', period.group(0))}' — 계약일·착수일을 넣고 계산하세요")
    if not amount:
        notes.append("낙찰금액을 아직 모름 — 낙찰 확인 뒤 다시 만들거나 직접 넣으세요")
    for word, tab in (("안전관리자", "안전관리자 선임계"), ("품질관리자", "품질관리자 선임계")) if gong else (("현장대리인", "현장대리인 서류(해당시)"),):
        if re.search(r"\s*".join(word), joined):
            notes.append(f"공고문에 '{word}'가 있음 — {tab} 탭도 제출할지 확인")
    return out, notes


def fill_basic(template: Path, target: Path, values: dict) -> None:
    """표준틀을 복사해 기본사항 B열에 값, C열에 '[자동] 출처'. 채운 칸은 하늘색(사람이 넣는 노란 칸과 구분)."""
    from openpyxl import load_workbook
    from openpyxl.styles import PatternFill

    wb = load_workbook(template)
    ws = wb["기본사항"]
    blue = PatternFill("solid", fgColor="FFDDEBF7")
    for row, (value, source) in values.items():
        ws.cell(row, 2).value = value
        ws.cell(row, 2).fill = blue
        ws.cell(row, 3).value = f"[자동] {source}"
    target.parent.mkdir(parents=True, exist_ok=True)
    wb.save(target)


# 적격심사 표준틀(11/16 — 공사·물품 × 추정가격 10억 미만/이상, 11/17 용역 = 지역·분야·금액별) → 0_적격심사/적격심사서류.xlsx.
# 기본사항 A열 이름으로 칸을 찾음(틀이 바뀌어 줄이 밀려도 맞게). 틀의 칸 색: 파랑 = 공고·개찰 값(여기서 채움), 노랑 = 담당자.
QUAL_DOC = "적격심사서류.xlsx"
QUAL_LABELS = {"no": ("입찰공고번호", "입찰등록번호", "관리번호"), "title": ("공사명", "입찰 대상 물품명", "용역명"),
               "institution": ("수요기관",), "basis": ("기초금액",),
               "opening": ("입찰일시",), "bid": ("입찰금액",), "planned": ("예정가격",), "a": ("A값",), "posted": ("입찰공고일",),
               "est": ("추정가격",), "classno": ("정부물품 분류번호",), "qty": ("수량",), "site": ("공사현장 시·군",),
               "trade": ("업종",), "rate": ("업종평가비율(%)",), "to": ("수신(발주기관)", "수신"), "kind": ("업종 구분", "업종구분")}
# 종합공사업(건설산업기본법) — 그 밖(실내건축·정보통신·전기·소방 등)은 '전문·그밖'
GENERAL_TRADES = ("토목공사업", "건축공사업", "토목건축공사업", "산업환경설비공사업", "조경공사업")
_YELLOW = "FFFFF4C2"
_BLUE = "FFDDEBF7"  # 준공·청구 틀 파란 칸(계약서 값)
# 기본사항 C열(안내)이 비어 있으면 넣는 설명 — 틀에 안내가 없는 칸만
QUAL_NOTES = {"구매관리번호": "보통 비워서 냄(2023 강서구 실물모형 제출본도 빈칸) — 조달청 대행 공고처럼 공고문에 구매관리번호가 있을 때만 적음"}


def qual_template(templates: Path, work: str, estimate: float | None) -> Path | None:
    """업무·추정가격에 맞는 적격심사 표준틀. 금액을 모르면 10억 미만 틀."""
    kind = "공사" if work == "공사" else "물품" if work == "물품" else ""
    tier = "10억이상" if (estimate or 0) >= 1_000_000_000 else "10억미만"
    path = Path(templates) / f"표준틀_적격심사_{kind}_{tier}.xlsx"
    return path if kind and path.exists() else None


def service_template(templates: Path, sido: str | None, estimate: float | None, it: bool, procure: bool, sw: bool = False,
                     tech: bool = False, pq: bool = False) -> Path | None:
    """용역 틀 '표준틀_적격심사_용역_{지역}_{분야}_{금액}.xlsx' — 조달청 공고면 조달청 틀, 아니면 수요기관 시·도·분야·추정가격 구간.
    기술용역(나라장터 용역구분)은 행안부 예규 제393호 제2장의2 틀 — P.Q 공고면 PQ_, 아니면 기술_(학술연구는 나라장터가 안 가려 담당자가 바꿈).
    조달청 기술용역은 맞는 틀이 없어 None(조달청 틀은 일반용역 세부기준).
    분야: 정보통신 업종이면 정보통신, 아니면 SW 사업이면 SW 틀 먼저(경기 2~5억만 SW/일반이 따로), 없으면 일반.
    금액 구간은 이름의 'N억이상'·'N억미만'으로 읽음. 금액을 모르면 2억 미만으로. 맞는 틀이 없으면 None."""
    won = estimate or 0
    hits = []
    for p in sorted(Path(templates).glob("표준틀_적격심사_용역_*.xlsx")):
        region, *rest = p.stem.split("_")[3:]
        if tech:
            if procure or region != "행안부" or len(rest) != 2 or rest[0] != ("PQ" if pq else "기술"):
                continue
        elif procure:
            if region == "조달청":
                return p
            continue
        elif region != sido or len(rest) != 2 or ("정보통신" in rest[0]) != it:
            continue
        lo, hi = re.search(r"(\d+)억이상", rest[1]), re.search(r"(\d+)억미만", rest[1])
        if (lo or hi) and (not lo or won >= int(lo[1]) * 1e8) and (not hi or won < int(hi[1]) * 1e8):
            hits.append((("SW" in rest[0]) != sw, p))  # 원하는 분야(SW/일반)가 먼저
    return min(hits)[1] if hits else None


def fill_qual(template: Path, target: Path, values: dict[str, Any]) -> list[str]:
    """표준틀을 복사해 기본사항의 이름이 맞는 칸(B열)에 값. 채운 칸 이름을 돌려줌."""
    from openpyxl import load_workbook

    wb = load_workbook(template)
    ws = wb["기본사항"]
    names = {label: key for key, labels in QUAL_LABELS.items() for label in labels}
    done = []
    for r in range(1, ws.max_row + 1):
        key = names.get(str(ws.cell(r, 1).value or "").strip())
        if key and values.get(key) not in (None, ""):
            ws.cell(r, 2).value = values[key]
            done.append(str(ws.cell(r, 1).value).strip())
        note = QUAL_NOTES.get(str(ws.cell(r, 1).value or "").strip())
        if note and not ws.cell(r, 3).value:
            ws.cell(r, 3).value = note
    target.parent.mkdir(parents=True, exist_ok=True)
    wb.save(target)
    return done


def qual_todo(xlsx: Path) -> tuple[list[str], list[list[str]]]:
    """적격심사서류 기본사항에서 (빈 노란 칸 이름 — 안내에 '비우면'·'때만'이 있는 선택 칸은 뺌, [파란 칸 이름, 값])."""
    from openpyxl import load_workbook

    ws = load_workbook(xlsx)["기본사항"]
    labels = {label for group in QUAL_LABELS.values() for label in group}
    todo, auto = [], []
    for r in range(1, ws.max_row + 1):
        name, cell = str(ws.cell(r, 1).value or "").strip(), ws.cell(r, 2)
        if not name:
            continue
        if cell.value in (None, ""):
            hint = str(ws.cell(r, 3).value or "")
            if (cell.fill.fgColor.rgb == _YELLOW or name in labels) and "비우면" not in hint and "때만" not in hint:
                todo.append(name)
        elif name in labels:
            auto.append([name, f"{cell.value:,}" if isinstance(cell.value, int) else str(cell.value)])
    return todo, auto


# 서류 현황(화면 '서류 현황' 칸) — 단계 폴더마다 무엇이 만들어졌는지. 표준틀은 착공·착수만, 나머지는 샘플을 받은 뒤(11/11)
STAGE_NAME = {"0_적격심사": "적격심사", "1_계약": "계약", "2_착공": "착공", "2_착수": "착수", "3_준공": "준공", "4_청구": "청구"}
READY_STAGES = {"2_착공", "2_착수", "3_준공", "4_청구"}  # 표준틀이 있는 단계(3·4는 11/18)
# 기본사항에서 담당자가 꼭 넣을 칸(행 번호 — 이름은 A열에서 읽어 틀 이름이 바뀌어도 맞음). 공사는 21행 공사위치도
REQUIRED_ROWS = (6, 8, 9, 10, 12, 15, 16, 17, 18, 19, 20)


def share_path(path: Path | str) -> str:
    """화면에 보일 폴더 주소 — NAS 컨테이너(/work = \\\\jiilserver\\jiil-webapp)면 윈도우 탐색기에 붙여 넣을 수 있는 주소로."""
    s = str(path)
    return "\\\\jiilserver\\jiil-webapp\\" + s[len("/work/"):].replace("/", "\\") if s.startswith("/work/") else s


def sheet_todo(xlsx: Path, gong: bool) -> tuple[list[str], list[list[str]]]:
    """착공·착수서류 기본사항에서 (아직 빈 필수 칸 이름, [자동으로 채운 칸 이름, 값]). 숨긴 줄은 안 봄."""
    from openpyxl import load_workbook

    ws = load_workbook(xlsx)["기본사항"]
    todo, auto = [], []
    for r in REQUIRED_ROWS + ((21,) if gong else ()):
        if ws.row_dimensions[r].hidden or not ws.cell(r, 1).value:
            continue
        if ws.cell(r, 2).value in (None, ""):
            todo.append(str(ws.cell(r, 1).value))
    # 인력 — 이름을 적은 사람은 9칸 전부(성명~주민등록번호·주소·입사일·연락처, 11/11 사용자 결정 — '빨간 칸만'·'주민번호·주소는 틀에 안 적음'(11/09)을 대체).
    # 필수 줄(공사 1, 착수 1·3~5)은 이름이 비어도 셈, 나머지(참여기술자·안전·품질관리자·현장대리인(해당 시))는 이름을 적었을 때만
    heads = {c: str(ws.cell(23, c).value or "") for c in range(2, 11)}
    required = (1,) if gong else (1, 3, 4, 5)  # 참여기술자 줄은 인원이 공고마다 달라 이름을 적었을 때만(11/11 사용자)
    for n in range(1, 7):
        r = 23 + n
        if n not in required and ws.cell(r, 2).value in (None, ""):
            continue
        empty = [heads[c] or str(c) for c in range(2, 11) if ws.cell(r, c).value in (None, "")]
        if empty:
            todo.append(f"{ws.cell(r, 1).value or f'{n}번'}: " + "·".join(empty))
    for r in range(4, 22):
        if str(ws.cell(r, 3).value or "").startswith("[자동]") and ws.cell(r, 2).value not in (None, ""):
            value = ws.cell(r, 2).value
            auto.append([str(ws.cell(r, 1).value), f"{value:,}" if isinstance(value, int) else str(value)])
    return todo, auto


def lookup(client: Any, base_url: str, no: str, ord_: str, work: str) -> tuple[str, dict | None]:
    """낙찰현황에서 공고 하나의 낙찰자. (확인한 업무, 줄 또는 None). 업무를 모르면 공사·용역·물품 순으로."""
    for w in ([work] if work in WORKS else list(WORKS)):
        rows = client.fetch_all_pages(base_url, F.SCSBID_OPERATIONS[f"낙찰현황/{w}"],
                                      {"inqryDiv": "4", "bidNtceNo": no}, f"낙찰현황/{w}")
        rows = [r for r in rows if not ord_ or str(r.get("bidNtceOrd") or "") == ord_] or rows
        if rows:
            return w, max(rows, key=lambda r: str(r.get("bidNtceOrd") or ""))
    return work, None


class PostAward:
    def __init__(self, data_dir: Path, bidlog=None, own_bizno: str = "", now=_now):
        self.path = Path(data_dir) / STATE_FILE
        self.bidlog = bidlog
        self.own = _digits(own_bizno)
        self.now = now
        self._lock = threading.Lock()

    def _read(self) -> dict[str, dict]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _write(self, items: dict[str, dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.path)

    def items(self) -> list[dict]:
        """화면용 — 공사 / 물품·용역 / 업무 미확인 순, 그 안에선 최근 추가 먼저."""
        order = {"공사": 0, "물품·용역": 1}
        opened = self._opened()
        rows = [{**v, "group": group_of(v.get("work", "")),
                 **({"opening": opened[k]} if k in opened else {})} for k, v in self._read().items()]
        return sorted(rows, key=lambda v: (order.get(v["group"], 2), v.get("added", "")), reverse=False)

    def _opened(self) -> dict[str, dict]:
        """투찰 기록의 개찰 결과 중 지일 등수 — 낙찰자 확정 전에도 탈락·등수를 보이게(11/12 사용자 요청). 표시만, 상태는 안 바꿈."""
        out: dict[str, dict] = {}
        for e in (self.bidlog.entries() if self.bidlog is not None else []):  # 최근 기록 먼저 — 같은 공고는 첫 것만
            res = e.get("result") or {}
            own = res.get("own")
            if res.get("status") != "완료" or not own or not e.get("noticeNo"):
                continue
            o = {"rank": own.get("rank"), "underRank": own.get("underRank"), "bidders": res.get("bidderCount"),
                 "remark": own.get("remark") or ""}
            out.setdefault(f'{e["noticeNo"]}-{(e.get("noticeOrd") or "")[:3]}', o)
            out.setdefault(e["noticeNo"], o)
        return out

    def _add(self, items: dict, no: str, ord_: str, **info) -> bool:
        key = f"{no}-{ord_}" if ord_ else no
        if key in items or (not ord_ and any(k.startswith(no + "-") for k in items)):
            return False
        items[key] = {"no": no, "ord": ord_, "status": "대기", "added": self.now().isoformat(timespec="seconds"), **info}
        return True

    def add(self, text: str, title: str = "", work: str = "") -> dict:
        """공고번호 직접 입력(협상·규격가격동시입찰도 여기로)."""
        no, ord_ = split_no(text)
        with self._lock:
            items = self._read()
            self._add(items, no, ord_, title=title[:200], work=work if work in WORKS else "", source="직접 입력")
            self._write(items)
        return items.get(f"{no}-{ord_}" if ord_ else no, {})

    def remove(self, key: str) -> None:
        with self._lock:
            items = self._read()
            items.pop(key, None)
            self._write(items)

    def sync_bidlog(self) -> int:
        """투찰 기록의 공고를 목록에 올림(이미 있으면 그대로). 올린 수."""
        if self.bidlog is None:
            return 0
        with self._lock:
            items = self._read()
            added = sum(self._add(items, e["noticeNo"], (e.get("noticeOrd") or "")[:3], title=e.get("title", ""),
                                  institution=e.get("institution", ""), work=e.get("workType", ""), source="투찰 기록",
                                  **({"private": True} if "수의" in (e.get("contractMethod") or "") else {}))
                        for e in self.bidlog.entries() if e.get("noticeNo"))
            self._write(items)
        return added

    def make_folder(self, key: str, root: Path, qual_only: bool = False) -> Path:
        """공고 폴더 — root/공사|물품·용역/번호-차수_공고명/ 아래 단계별 폴더. 이미 있으면 빈 폴더만 채우고 파일은 안 건드림.
        `qual_only`(낙찰 전 적격심사 단계)면 0_적격심사·공고문서만 — 나머지 단계는 낙찰 뒤에 더함."""
        with self._lock:
            items = self._read()
            v = items.get(key)
            if v is None:
                raise ValueError("목록에 없는 공고입니다")
            name = re.sub(r'[\\/:*?"<>|\s]+', " ", f"{key}_{v.get('title') or ''}").strip()[:90].rstrip(" ._")
            folder = Path(root) / group_of(v.get("work", "")) / name
            stages = ["0_적격심사", "1_계약", "2_착공" if v.get("work") == "공사" else "2_착수", "3_준공", "4_청구", "공고문서"]
            if qual_only:
                stages = ["0_적격심사", "공고문서"]
            elif v.get("private"):  # 수의계약 — 적격심사 없이 바로 계약(11/12 사용자 결정)
                stages = stages[1:]
            for s in stages:
                (folder / s).mkdir(parents=True, exist_ok=True)
            v["folder"] = str(folder)
            v["qualOnly"] = qual_only  # 화면: 낙찰되면 '낙찰 서류 만들기'를 다시 보임
            self._write(items)
        return folder

    def prepare(self, key: str, root: Path, fetch=None, templates: Path | None = None, qual_only: bool = False,
                contract_lookup=None) -> tuple[Path, list[dict]]:
        """공고 폴더 만들기 — 나라장터에서 공고 정보·첨부를 받아(정량평가서와 같은 quantdocs.g2b.fetch_notice) 업무를 정하고,
        첨부는 공고문서/에 두고, 첨부가 요구하는 제출서류를 읽어 표준틀에 없는 것을 '서류_확인.txt'에 적는다.
        표준틀(templates, 기본 root/_표준틀)이 있으면 착공·착수서류를 복사해 기본사항을 채운다(시험판 11/11)."""
        import shutil
        import tempfile

        if fetch is None:
            from quantdocs.g2b import fetch_notice as fetch
        v = {**(self._read().get(key) or {})}
        if not v:
            raise ValueError("목록에 없는 공고입니다")
        with tempfile.TemporaryDirectory(prefix="postaward_") as tmp:
            nf = fetch(key, Path(tmp))
            private = "수의" in (getattr(nf, "contract_method", "") or "")
            with self._lock:
                items = self._read()
                items[key]["private"] = private
                self._write(items)
            if private and qual_only:
                raise ValueError("수의계약 공고입니다 — 적격심사 없이 바로 계약하므로 낙찰이 확인되면 '낙찰 서류 만들기'를 쓰세요")
            if nf.title:
                with self._lock:
                    items = self._read()
                    items[key].update(title=nf.title, institution=nf.institution or items[key].get("institution", ""),
                                      work=nf.work_type or items[key].get("work", ""), method=nf.award_method)
                    self._write(items)
            folder = self.make_folder(key, root, qual_only)
            docs = required_docs(nf.files)
            for f in nf.files:
                target = folder / "공고문서" / f.name
                if not target.exists():  # 담당자가 고쳤을 수 있어 덮어쓰지 않음
                    shutil.copyfile(f, target)
            tdir = Path(templates) if templates else template_dir(root)
            basic = [] if private else self._fill_qual(key, folder, nf, tdir)
            basic += (["[적격심사] 낙찰 전 — 0_적격심사·공고문서만 만듦(계약·착공 서류는 낙찰 뒤 '낙찰 서류 만들기')"] if qual_only
                      else self._fill_template(key, folder, nf, tdir) + self._fill_done(key, folder, nf, tdir, contract_lookup))
            summary = folder / "공고문서" / SUMMARY
            if nf.title and not summary.exists():
                try:
                    write_summary(summary, key, v, nf)
                except Exception as err:  # noqa: BLE001 — 요약을 못 만들어도 폴더·서류는 그대로
                    basic.append(f"[공고요약] {SUMMARY}를 만들지 못함({err.__class__.__name__})")
        lines = [f"{key} {v.get('title') or nf.title} — 첨부에서 읽은 제출서류 (자동, 원문과 대조하세요)", ""]
        lines += basic + [""]
        if not nf.title:
            lines.append("나라장터에서 공고를 찾지 못해 첨부를 받지 못했습니다.")
        elif not docs:
            lines.append("첨부에서 '제출서류' 목록을 찾지 못했습니다 — 공고문을 직접 확인하세요.")
        for d in docs:
            lines.append(f"[{'틀에 있음' if d['covered'] else '틀에 없음 — 따로 준비'}] {d['text']}  ({d['source']})")
        lines += [""] + [f"첨부를 받지 못함: {s}" for s in nf.skipped]
        (folder / "공고문서" / "서류_확인.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
        with self._lock:
            items = self._read()
            items[key]["docs"] = {"found": len(docs), "missing": sum(not d["covered"] for d in docs), "files": len(nf.files)}
            # 서류 현황 화면용 — 첨부 제출서류(틀에 있음/없음)와 기본사항 안내(공고문 기간·해당 시 서류)
            items[key]["docList"] = [{"text": d["text"][:120], "covered": d["covered"]} for d in docs][:40]
            items[key]["notes"] = [n.strip() for n in basic if n.startswith("  ") and "직접 넣으세요" not in n]
            self._write(items)
        return folder, docs

    def status(self, key: str) -> dict:
        """서류 현황 — 단계 폴더마다 파일, 착공·착수서류의 빈 필수 칸·자동 칸, 첨부 제출서류, 확인할 것."""
        v = self._read().get(key) or {}
        folder = Path(v["folder"]) if v.get("folder") else None
        if folder is None or not folder.is_dir():
            return {"folder": None}
        gong = v.get("work") == "공사"
        stages = []
        for d in sorted(p for p in folder.iterdir() if p.is_dir() and p.name in STAGE_NAME):
            files = sorted(f.name for f in d.iterdir() if f.is_file() and not f.name.startswith("~$"))
            stage = {"dir": d.name, "name": STAGE_NAME[d.name], "files": files, "ready": d.name in READY_STAGES}
            qual, done = d.name == "0_적격심사", d.name in ("3_준공", "4_청구")
            doc = d / (QUAL_DOC if qual else CLAIM_TEMPLATE[2] if d.name == "4_청구"
                       else DONE_TEMPLATES.get(v.get("work", ""), ("", "", "-"))[2] if done else TEMPLATES["공사" if gong else "물품·용역"][2])
            if (qual or d.name in READY_STAGES) and doc.is_file():
                try:
                    stage["todo"], stage["auto"] = (qual_todo(doc) if qual else sheet_todo(doc, gong) if not done
                                                    else done_todo(doc, (v.get("autoFill") or {}).get(f"{d.name}/{doc.name}", {})))
                    stage["doc"] = doc.name
                except Exception as err:  # noqa: BLE001 — 엑셀이 열려 있거나 깨졌어도 나머지는 보임
                    stage["error"] = f"{doc.name}을 읽지 못함({err.__class__.__name__}) — 엑셀에서 닫고 다시 보세요"
            stages.append(stage)
        notice = folder / "공고문서"
        attach = sorted(f.name for f in notice.iterdir() if f.is_file()) if notice.is_dir() else []
        return {"folder": share_path(folder), "stages": stages, "attach": attach,
                "docList": v.get("docList") or [], "notes": v.get("notes") or []}

    def file(self, key: str, rel: str) -> Path:
        """공고 폴더 안 파일 하나(내려받기용). 폴더 밖을 가리키면 오류."""
        v = self._read().get(key) or {}
        if not v.get("folder"):
            raise ValueError("폴더를 아직 만들지 않은 공고입니다")
        folder = Path(v["folder"]).resolve()
        target = (folder / rel).resolve()
        if folder not in target.parents or not target.is_file():
            raise ValueError("파일을 찾지 못했습니다")
        return target

    def zip(self, key: str, rel: str) -> tuple[str, bytes]:
        """단계 폴더 하나(rel이 비면 공고 폴더 전체 — 폴더 만들기 때 묶음, 11/17)를 zip으로 — (파일 이름, 내용). 폴더 밖·빈 폴더면 오류."""
        import io
        import zipfile

        v = self._read().get(key) or {}
        if not v.get("folder"):
            raise ValueError("폴더를 아직 만들지 않은 공고입니다")
        folder = Path(v["folder"]).resolve()
        target = (folder / rel).resolve() if rel else folder
        if (target != folder and folder not in target.parents) or not target.is_dir():
            raise ValueError("폴더를 찾지 못했습니다")
        files = [f for f in sorted(target.rglob("*")) if f.is_file() and not f.name.startswith("~$")]
        if not files:
            raise ValueError("폴더가 비어 있습니다")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for f in files:
                z.write(f, f.relative_to(target).as_posix())
        return f"{key}_{STAGE_NAME.get(target.name, target.name) if rel else '전체'}.zip", buf.getvalue()

    def _fill_qual(self, key: str, folder: Path, nf, templates: Path) -> list[str]:
        """적격심사 표준틀을 0_적격심사에 복사하고 공고·투찰 기록 값을 채움. 이미 있으면 담당자 파일이라 그대로. 안내 줄을 돌려줌."""
        v = self._read().get(key) or {}
        target = folder / "0_적격심사" / QUAL_DOC
        if target.exists():
            return [f"[적격심사] 0_적격심사/{QUAL_DOC}가 이미 있어 그대로 둠(다시 채우려면 그 파일을 지우고 폴더 만들기)"]
        e = next((e for e in (self.bidlog.entries() if self.bidlog is not None else []) if e.get("noticeNo") == v.get("no")), {})
        res = e.get("result") or {}
        raw = getattr(nf, "raw", None) or {}

        def num(name):
            try:
                n = float(str(raw.get(name) or "").replace(",", ""))
            except ValueError:
                return None
            return (int(n) if n == int(n) else n) or None

        basis = e.get("basis") or getattr(nf, "budget", None)
        est = num("presmptPrce")  # 나라장터 추정가격 — 없으면 기초금액 ÷ 1.1로 틀만 고름
        # 물품 업종구분 — 낙찰방법 이름 끝 '(물품제조)'·'(물품구매)'로(모르면 틀 기본값 그대로)
        method = str(raw.get("sucsfbidMthdNm") or getattr(nf, "award_method", "") or "")
        goods_kind = "제조" if "제조" in method else "공급" if "구매" in method else None
        licenses = [re.sub(r"\s*[/(]\s*\d{4}\)?\s*$", "", name) for name in getattr(nf, "licenses", None) or []]
        service = v.get("work") == "용역"
        procure = "조달청" in str(raw.get("ntceInsttNm") or "")
        sido = next((s for s in (sido_of(str(raw.get(k) or "")) for k in ("dminsttNm", "ntceInsttNm")) if s), None) \
            or sido_of(nf.institution or v.get("institution"))
        tech = service and str(raw.get("srvceDivNm") or "").strip() == "기술용역"
        pq = str(raw.get("pqEvalYn") or "").upper() == "Y"
        if service:
            template = service_template(templates, sido, est or (basis / 1.1 if basis else None),
                                        any("정보통신" in n for n in licenses), procure,
                                        str(raw.get("swBizObjYn") or "").upper() == "Y" or any("소프트웨어" in n for n in licenses),
                                        tech, pq)
            if template is None:
                return [f"[적격심사] 용역 적격심사 표준틀 중 맞는 것이 없어 만들지 않음 — 용역구분 {raw.get('srvceDivNm') or '모름'}, "
                        f"조달청 공고 {'예' if procure else '아니오'}, 수요기관 시·도 {sido or '모름'}, "
                        f"추정가격 {f'{est:,.0f}원' if est else '모름'}(틀: 일반용역 강원·경기·경남·조달청 / 기술용역 행안부 — 조달청 기술용역 틀은 없음)"]
        else:
            template = qual_template(templates, v.get("work", ""), est or (basis / 1.1 if basis else None))
        if template is None:
            return [f"[적격심사] {v.get('work') or '업무 미확인'} 적격심사 표준틀이 {templates}에 없어 만들지 않음"]
        a = res.get("aValue") if res.get("aValue") is not None else e.get("aValue")
        done = fill_qual(template, target, {
            "no": key, "title": nf.title or v.get("title"), "institution": nf.institution or v.get("institution"), "basis": basis,
            "opening": (e.get("openingAt") or str(raw.get("opengDt") or ""))[:16], "bid": e.get("bid"), "planned": res.get("plannedPrice"),
            "a": round(a) if a else None, "posted": (nf.posted or "")[:10], "est": est,
            "classno": str(raw.get("dtilPrdctClsfcNo") or ""), "qty": " ".join(str(raw.get(k) or "").strip() for k in ("prdctQty", "prdctUnit")).strip(),
            "site": (str(raw.get("cnstrtsiteRgnNm") or "").split() or [""])[-1],
            "trade": licenses[0] if len(licenses) == 1 else None,  # 업종이 여럿이면 담당자가 고름
            "kind": goods_kind if v.get("work") == "물품" else None if service else
                    (("종합공사" if licenses[0].replace("·", "") in GENERAL_TRADES else "전문·그밖") if len(licenses) == 1 else None),
            # 발주기관 = 공고기관(조달청 대행이면 조달청) — 조달청 용역 틀은 기본값 '조달청장' 그대로
            "to": None if service and procure else str(raw.get("ntceInsttNm") or "") or nf.institution,
            "rate": num("indstrytyEvlRt")})
        return [f"[적격심사] 0_적격심사/{QUAL_DOC} ← {template.name} — 자동으로 채운 칸: " + ", ".join(done)] + \
            (["  용역 틀은 수요기관 시·도로 골랐음 — 공고문이 인용한 적격심사 세부기준(별표)과 틀 이름이 같은지 확인"] if service and not tech else []) +             ([f"  기술용역이라 행안부 예규 제393호 제2장의2 {'[별표 1] P.Q' if pq else '[별표 2] 기술용역'} 틀로 골랐음 — "
              "학술연구용역이면 학술 틀로 바꾸고, 공고문이 인용한 기준·별표와 같은지 확인"] if tech else []) + [
                "  제출일·작성자 등 노란 칸과 구매관리번호는 직접 넣으세요" + ("" if est else " — 추정가격을 못 받아 금액 구간 틀을 기초금액으로 골랐으니 대조")] +             (["  구매관리번호: " + QUAL_NOTES["구매관리번호"]] if v.get("work") == "물품" else [])

    def _fill_template(self, key: str, folder: Path, nf, templates: Path) -> list[str]:
        """착공(공사)·착수(물품·용역)서류를 단계 폴더에 복사하고 기본사항을 채움. 이미 있으면 담당자 파일이라 그대로. 안내 줄을 돌려줌."""
        v = self._read().get(key) or {}
        group = group_of(v.get("work", ""))
        if group not in TEMPLATES:
            return ["[기본사항] 업무(공사/물품/용역)를 몰라 착공·착수서류를 만들지 않음"]
        name, stage, doc = TEMPLATES[group]
        target = folder / stage / doc
        if target.exists():
            return [f"[기본사항] {stage}/{doc}가 이미 있어 그대로 둠(다시 채우려면 그 파일을 지우고 폴더 만들기)"]
        if not (templates / name).exists():
            return [f"[기본사항] 표준틀 '{name}'이 {templates}에 없어 만들지 않음"]
        from quantdocs.notice import doc_text

        texts = []
        for f in nf.files:
            if f.suffix.lower() in (".hwp", ".hwpx", ".pdf", ".docx"):
                try:
                    texts.append(doc_text(f))
                except Exception:  # noqa: BLE001 — 한 첨부를 못 읽어도 나머지로
                    pass
        values, notes = basic_values(v, nf.title or v.get("title", ""), nf.institution or v.get("institution", ""),
                                     nf.contract_method, texts)
        fill_basic(templates / name, target, values)
        return ([f"[기본사항] {stage}/{doc} — 자동으로 채운 칸(하늘색): " + ", ".join(src.split(" — ")[0] for _, src in values.values()),
                 "  계약번호·계약일·착수일·수신·문서번호·제출일·인력은 직접 넣으세요"] + [f"  {n}" for n in notes])

    def _fill_done(self, key: str, folder: Path, nf, templates: Path, contract_lookup=None) -> list[str]:
        """3_준공(준공계·납품계·완료계)·4_청구(청구서) 기본사항 파란 칸 — 공고·낙찰 값(수요기관·공고명·낙찰금액) <
        나라장터 계약정보(contract_lookup(공고) → 계약정보 한 줄: 계약일·착수일·준공예정일·금액·담당부서, 계약 뒤에 생김) <
        담당자가 착공·착수서류에 적은 값 순으로(11/18). 담당자가 고친 칸은 안 건드림. 안내 줄을 돌려줌."""
        v = self._read().get(key) or {}
        work = v.get("work", "")
        if work not in DONE_TEMPLATES:
            return ["[준공·청구] 업무(공사/물품/용역)를 몰라 준공·청구 서류를 만들지 않음"]
        raw = getattr(nf, "raw", None) or {}
        title = nf.title or v.get("title", "")
        inst = str(raw.get("dminsttNm") or "").strip() or nf.institution or v.get("institution", "")
        amount = _digits(str((v.get("winner") or {}).get("amount") or "").split(".")[0])
        values: dict[str, Any] = {"to": inst, "title": title, "amount": int(amount) if amount else None, "gwiha": inst}
        if work == "용역":  # 설계 용역이면 공문 붙임에 납품서(11/18 사용자)
            values["nap"] = "예" if "설계" in title else "아니오"
        lines = []
        # 나라장터 계약정보(계약 뒤에 생김) — 계약일·착수일·준공예정일·계약금액·계약기관 담당부서
        cv: dict[str, Any] = {}
        if contract_lookup is not None:
            try:
                row = contract_lookup(v)
            except Exception as err:  # noqa: BLE001 — 나라장터가 안 돼도 나머지는 그대로
                row = None
                lines.append(f"  나라장터 계약정보를 받지 못함({err.__class__.__name__}) — 나중에 '첨부 다시 받기'로 다시")
            else:
                if row:
                    cv = contract_values(row)
                    with self._lock:
                        items = self._read()
                        items[key]["contract"] = {"no": str(row.get("untyCntrctNo") or ""), "url": str(row.get("cntrctDtlInfoUrl") or "")}
                        self._write(items)
                    lines.append(f"  나라장터 계약정보 찾음(통합계약번호 {row.get('untyCntrctNo')}) — 계약일·착수일·준공예정일 등을 채움")
                else:
                    lines.append("  나라장터 계약정보: 아직 계약이 등록되지 않음 — 계약 뒤 '첨부 다시 받기'를 누르면 계약일·착수일·준공예정일이 들어감")
        values.update(cv)
        _, stage0, doc0 = TEMPLATES[group_of(work)]
        start = folder / stage0 / doc0
        got: dict[str, Any] = {}
        if start.exists():
            try:
                got = start_values(start)
            except Exception as err:  # noqa: BLE001 — 착공·착수서류를 못 읽어도 나라장터 값으로
                lines.append(f"  {stage0}/{doc0}을 읽지 못함({err.__class__.__name__}) — 나라장터 값만 넣음")
        # 담당자가 착공·착수서류에 적은 값이 우선(계약서 보고 적음). 나라장터 계약정보와 다르면 알림(홍천: 계약일 하루·완료기한 다름)
        for k, val in got.items():
            if k in cv and k != "gwiha" and _key(cv[k]) != _key(val):
                lines.append(f"  확인: {DONE_NAMES[k]} — {stage0}/{doc0} '{_key(val)}', 나라장터 계약정보 '{_key(cv[k])}' (계약서로 확인)")
        values.update(got)
        auto, head = dict(v.get("autoFill") or {}), []
        for name, stage, doc in (DONE_TEMPLATES[work], CLAIM_TEMPLATE):
            rel = f"{stage}/{doc}"
            if not (templates / name).exists():
                head.append(f"[준공·청구] 표준틀 '{name}'이 {templates}에 없어 {rel}를 만들지 않음")
                continue
            try:
                done, _ = fill_done(templates / name, folder / stage / doc, values, auto.get(rel, {}))
            except PermissionError:
                head.append(f"[준공·청구] {rel}이 열려 있어 채우지 못함 — 엑셀에서 닫고 '첨부 다시 받기'")
                continue
            auto[rel] = done
            kept = [DONE_NAMES[k] for k in values if values[k] not in (None, "") and k not in done
                    and any(lab in DONE_LABELS[k] for lab in _labels(folder / stage / doc))]
            head.append(f"[준공·청구] {rel} — 자동으로 채운 파란 칸: " + ", ".join(DONE_NAMES[k] for k in done)
                         + (f" · 담당자가 고친 칸은 그대로: {', '.join(kept)}" if kept else ""))
        if got:
            lines.append(f"  {stage0}/{doc0}에 담당자가 적은 값이 있으면 그 값을 먼저 씀")
        with self._lock:
            items = self._read()
            items[key]["autoFill"] = auto
            self._write(items)
        return head + lines

    def check(self, client: Any, base_url: str, keys: list[str] | None = None) -> dict[str, int]:
        """'대기' 공고의 낙찰자를 확인. keys를 주면 그 공고만(상태와 상관없이 다시)."""
        now = self.now()
        out = {"won": 0, "lost": 0, "waiting": 0, "gaveUp": 0}
        with self._lock:
            items = self._read()
            for key, v in items.items():
                if keys is not None and key not in keys:
                    continue
                if keys is None and v.get("status") != "대기":
                    continue
                work, row = lookup(client, base_url, v["no"], v.get("ord", ""), v.get("work", ""))
                v["checkedAt"] = now.isoformat(timespec="seconds")
                if row:
                    mine = bool(self.own) and _digits(row.get("bidwinnrBizno")) == self.own
                    v.update(work=work, status="낙찰" if mine else "다른 업체 낙찰",
                             winner={"name": row.get("bidwinnrNm") or "", "amount": row.get("sucsfbidAmt") or "",
                                     "rate": row.get("sucsfbidRate") or "", "date": row.get("fnlSucsfDate") or ""})
                    v["title"] = v.get("title") or row.get("bidNtceNm") or ""
                    v["institution"] = v.get("institution") or row.get("dminsttNm") or ""
                    out["won" if mine else "lost"] += 1
                elif now - datetime.fromisoformat(v["added"]) > timedelta(days=GIVE_UP_DAYS):
                    v["status"] = "결과 없음"
                    out["gaveUp"] += 1
                else:
                    out["waiting"] += 1
            self._write(items)
        return out


def selfcheck() -> None:
    """가짜 낙찰현황 응답으로 목록·판정 확인(tests/test_postaward.py, python -m webapp.postaward)."""
    import tempfile

    class _Log:
        def entries(self):
            return [{"noticeNo": "R26BK00000001", "noticeOrd": "000", "title": "A 공사", "workType": "공사"},
                    {"noticeNo": "R26BK00000002", "noticeOrd": "000", "title": "B 물품", "workType": "물품",
                     "result": {"status": "완료", "bidderCount": 422, "own": {"rank": None, "underRank": -108, "remark": "낙찰하한선 미달"}}}]

    class _Client:
        def fetch_all_pages(self, url, op, params, label):
            if params["bidNtceNo"] == "R26BK00000001" and op.endswith("Cnstwk"):
                return [{"bidNtceOrd": "000", "bidwinnrBizno": "206-86-36338", "bidwinnrNm": "(주)지일"}]
            if params["bidNtceNo"] == "R26BK00000003" and op.endswith("Servc"):
                return [{"bidNtceOrd": "000", "bidwinnrBizno": "1112233333", "bidwinnrNm": "다른곳"}]
            return []

    with tempfile.TemporaryDirectory() as d:
        pa = PostAward(Path(d), _Log(), "2068636338")
        assert pa.sync_bidlog() == 2 and pa.sync_bidlog() == 0
        pa.add("R26BK00000003-000")
        r = pa.check(_Client(), "u")
        assert r == {"won": 1, "lost": 1, "waiting": 1, "gaveUp": 0}, r
        by = {v["no"]: v for v in pa.items()}
        assert by["R26BK00000001"]["status"] == "낙찰" and by["R26BK00000001"]["group"] == "공사"
        assert by["R26BK00000003"]["status"] == "다른 업체 낙찰" and by["R26BK00000003"]["group"] == "물품·용역"
        assert by["R26BK00000002"]["status"] == "대기"
        assert by["R26BK00000002"]["opening"]["underRank"] == -108 and "opening" not in by["R26BK00000001"]
        f = pa.make_folder("R26BK00000001-000", Path(d) / "out")
        assert f.parent.name == "공사" and (f / "2_착공").is_dir(), f
        f = pa.make_folder("R26BK00000003-000", Path(d) / "out")
        assert f.parent.name == "물품·용역" and (f / "2_착수").is_dir(), f

        # 적격심사 표준틀: 이름이 맞는 칸만 채우고, 빈 노란 칸·추정가격은 '채울 칸'(선택 칸 '비우면'은 뺌)
        from openpyxl import Workbook
        from openpyxl.styles import PatternFill

        wb = Workbook()
        ws = wb.active
        ws.title = "기본사항"
        for r, (name, hint) in enumerate([("입찰공고번호", ""), ("공사명", ""), ("추정가격", ""), ("제출일", "날짜"), ("작성자 전화", "비우면 회사 전화")], 4):
            ws.cell(r, 1).value, ws.cell(r, 3).value = name, hint
            ws.cell(r, 2).fill = PatternFill("solid", fgColor=_YELLOW if r >= 7 else "FFDDEBF7")
        (Path(d) / "qtpl").mkdir()
        wb.save(Path(d) / "qtpl" / "표준틀_적격심사_공사_10억미만.xlsx")
        assert qual_template(Path(d) / "qtpl", "공사", 5e8) and not qual_template(Path(d) / "qtpl", "공사", 2e9) and not qual_template(Path(d) / "qtpl", "용역", 1)
        out = Path(d) / "q.xlsx"
        assert fill_qual(qual_template(Path(d) / "qtpl", "공사", None), out, {"no": "R1-000", "title": "A 공사", "bid": None}) == ["입찰공고번호", "공사명"]
        assert qual_todo(out) == (["추정가격", "제출일"], [["입찰공고번호", "R1-000"], ["공사명", "A 공사"]]), qual_todo(out)
        # 용역 틀(11/17) — 조달청 공고면 조달청 틀, 아니면 시·도·정보통신·금액 구간
        for n in ("용역_강원_일반_2억미만", "용역_강원_일반_10억이상", "용역_경남_정보통신_2억이상5억미만", "용역_경남_정보통신_5억이상",
                  "용역_경기_SW_2억이상5억미만", "용역_경기_일반_2억이상5억미만", "용역_경기_SW·일반_5억이상10억미만", "용역_조달청_수요기관지정형"):
            wb.save(Path(d) / "qtpl" / f"표준틀_적격심사_{n}.xlsx")
        st = lambda *a: (service_template(Path(d) / "qtpl", *a) or Path("없음")).stem
        assert st("강원", 1e8, False, False).endswith("강원_일반_2억미만") and st("강원", 3e8, False, False) == "없음"
        assert st("강원", 2e9, False, False, True).endswith("강원_일반_10억이상")  # SW 사업이어도 강원은 일반 틀
        assert st("경남", 3e8, True, False).endswith("정보통신_2억이상5억미만") and st("경남", 1e8, False, False) == "없음"
        assert st("경남", 9e8, True, False).endswith("정보통신_5억이상")
        assert st("경기", 3e8, False, False, True).endswith("경기_SW_2억이상5억미만")
        assert st("경기", 3e8, False, False).endswith("경기_일반_2억이상5억미만")
        assert st("경기", 6e8, False, False, True).endswith("SW·일반_5억이상10억미만") and st("경기", 6e8, False, False).endswith("SW·일반_5억이상10억미만")
        assert st(None, None, False, True).endswith("조달청_수요기관지정형") and st("경기", None, False, False) == "없음"
        # 기술용역(11/17) — 행안부 틀, P.Q 여부·금액 구간, 조달청이면 없음, 일반용역 고르기엔 안 끼어듦
        for n in ("용역_행안부_기술_1억미만", "용역_행안부_기술_2억이상5억미만", "용역_행안부_PQ_5억미만", "용역_행안부_학술_1억미만"):
            wb.save(Path(d) / "qtpl" / f"표준틀_적격심사_{n}.xlsx")
        assert st("강원", 5e7, False, False, False, True).endswith("행안부_기술_1억미만")
        assert st("경남", 3e8, True, False, False, True).endswith("행안부_기술_2억이상5억미만")
        assert st("강원", 3e8, False, False, False, True, True).endswith("행안부_PQ_5억미만")
        assert st(None, 3e8, False, True, False, True) == "없음" and st("강원", 6e8, False, False, False, True) == "없음"
        assert st("강원", 5e7, False, False).endswith("강원_일반_2억미만")

        import io as _io
        import zipfile as _zip

        zf = pa.make_folder("R26BK00000001-000", Path(d) / "out")
        (zf / "0_적격심사" / "a.txt").write_text("x", encoding="utf-8")
        name, body = pa.zip("R26BK00000001-000", "0_적격심사")
        assert name == "R26BK00000001-000_적격심사.zip" and _zip.ZipFile(_io.BytesIO(body)).namelist() == ["a.txt"], name
        name, body = pa.zip("R26BK00000001-000", "")  # 공고 폴더 전체 묶음(폴더 만들기 때, 11/17)
        assert name == "R26BK00000001-000_전체.zip" and "0_적격심사/a.txt" in _zip.ZipFile(_io.BytesIO(body)).namelist(), name
        (zf / "0_적격심사" / "a.txt").unlink()
        try:
            pa.zip("R26BK00000001-000", "..")
            raise AssertionError("폴더 밖은 막아야 함")
        except ValueError:
            pass

        # 기본사항 채우기(시험판) — 공고문 글에서 공사위치·기간·안전관리자, 낙찰금액은 낙찰현황 값
        vals, notes = basic_values({"work": "공사", "winner": {"amount": "184131570"}}, "A 공사", "정선군", "",
                                   ["3. 공사위치 : 강원특별자치도 정선군 고한읍 1\n착공일로부터 60일 이내\n안전관리자 선임"])
        assert vals[12][0] == 184131570 and vals[21][0] == "강원특별자치도 정선군 고한읍 1" and 11 not in vals, vals
        assert any("60일" in n for n in notes) and any("안전관리자 선임계" in n for n in notes), notes
        vals, notes = basic_values({"work": "용역"}, "B 용역", "아산시", "장기계속계약", ["현장대리인을 선임"])
        assert vals[11][0] == "장기계속" and 12 not in vals and any("현장대리인 서류" in n for n in notes), (vals, notes)

        from openpyxl import Workbook, load_workbook

        tpl = Path(d) / "tpl"
        tpl.mkdir()
        for name, _, _ in TEMPLATES.values():
            wb = Workbook()
            wb.active.title = "기본사항"
            wb.active["A6"], wb.active["A8"] = "수신", "공사명"
            wb.save(tpl / name)

        class _NF:
            title, institution, contract_method, files = "A 공사", "정선군", "", []
            work_type, award_method, skipped = "공사", "", []

        # 낙찰 전 적격심사 단계: 0_적격심사·공고문서만, 낙찰 뒤 같은 폴더에 나머지 단계를 더함
        class _NF2(_NF):
            title, work_type = "B 물품", "물품"

        q, _ = pa.prepare("R26BK00000002-000", Path(d) / "out", fetch=lambda k, t: _NF2(), templates=tpl, qual_only=True)
        assert sorted(x.name for x in q.iterdir()) == ["0_적격심사", "공고문서"] and pa._read()["R26BK00000002-000"]["qualOnly"] is True
        q, _ = pa.prepare("R26BK00000002-000", Path(d) / "out", fetch=lambda k, t: _NF2(), templates=tpl)
        assert (q / "2_착수").is_dir() and pa._read()["R26BK00000002-000"]["qualOnly"] is False
        # 수의계약: 적격심사 폴더는 막고, 낙찰 서류엔 0_적격심사 단계가 없음
        class _NF3(_NF):
            title, contract_method = "C 공사", "수의계약"

        pa.add("R26BK00000004-000")
        try:
            pa.prepare("R26BK00000004-000", Path(d) / "out", fetch=lambda k, t: _NF3(), templates=tpl, qual_only=True)
            raise AssertionError("수의계약은 적격심사 폴더를 막아야 함")
        except ValueError:
            assert pa._read()["R26BK00000004-000"]["private"] is True
        p3, _ = pa.prepare("R26BK00000004-000", Path(d) / "out", fetch=lambda k, t: _NF3(), templates=tpl)
        assert not (p3 / "0_적격심사").exists() and (p3 / "1_계약").is_dir()
        pa.remove("R26BK00000004-000")
        folder, _ = pa.prepare("R26BK00000001-000", Path(d) / "out", fetch=lambda k, t: _NF(), templates=tpl)
        ws = load_workbook(folder / "2_착공" / "착공서류.xlsx")["기본사항"]
        assert ws["B8"].value == "A 공사" and ws["B5"].value == "정선군" and ws["C8"].value.startswith("[자동]"), ws["C8"].value
        assert "자동으로 채운 칸" in (folder / "공고문서" / "서류_확인.txt").read_text(encoding="utf-8")
        _, _ = pa.prepare("R26BK00000001-000", Path(d) / "out", fetch=lambda k, t: _NF(), templates=tpl)  # 두 번째는 담당자 파일이라 그대로
        assert "이미 있어 그대로" in (folder / "공고문서" / "서류_확인.txt").read_text(encoding="utf-8")

        # 서류 현황 — 착공 단계는 채울 칸(비어 있는 '수신', 인력 1번)과 자동 칸, 준공은 아직 틀 없음
        st = pa.status("R26BK00000001-000")
        by_dir = {s["dir"]: s for s in st["stages"]}
        assert by_dir["2_착공"]["doc"] == "착공서류.xlsx" and "수신" in by_dir["2_착공"]["todo"], by_dir["2_착공"]
        assert "공사명" not in by_dir["2_착공"]["todo"] and ["공사명", "A 공사"] in by_dir["2_착공"]["auto"]
        todo = by_dir["2_착공"]["todo"]   # 인력: 필수 줄 1번은 이름이 없어도 9칸 전부, 참여기술자 2번·해당 시 5번은 이름이 없으면 안 셈
        assert any(t.startswith("1번: ") and t.count("·") == 8 for t in todo), todo
        assert not any(t.startswith(("2번", "5번")) for t in todo), todo
        assert by_dir["3_준공"]["ready"] is True and by_dir["3_준공"]["files"] == []  # 준공 틀이 tpl에 없어 안 만듦
        assert "표준틀_준공계.xlsx" in (folder / "공고문서" / "서류_확인.txt").read_text(encoding="utf-8")
        assert pa.file("R26BK00000001-000", "2_착공/착공서류.xlsx").name == "착공서류.xlsx"
        for bad in ("../../postaward.json", "공고문서/없는파일.hwp"):
            try:
                pa.file("R26BK00000001-000", bad)
                raise AssertionError("폴더 밖·없는 파일은 막아야 함: " + bad)
            except ValueError:
                pass
        # 준공·청구(11/18) — 처음엔 나라장터 값, 착수서류를 채운 뒤 다시 하면 계약 값으로. 담당자가 고친 칸은 그대로
        from openpyxl.styles import PatternFill as _PF

        tpl2 = Path(d) / "tpl2"
        tpl2.mkdir()
        for name, _, _ in TEMPLATES.values():
            (tpl2 / name).write_bytes((tpl / name).read_bytes())

        def fake(path, rows):
            wb = Workbook()
            ws = wb.active
            ws.title = "기본사항"
            for r, (a, b) in enumerate(rows, 5):
                ws.cell(r, 1, a), ws.cell(r, 2, b)
                ws.cell(r, 2).fill = _PF("solid", fgColor=_YELLOW if a in ("제출일", "납품서 붙임", "계약년월일", "착공년월일", "완료기한") else _BLUE)
            wb.save(path)

        fake(tpl2 / "표준틀_완료계.xlsx", [("납품서 붙임", "예"), ("수신자", None), ("용역명", None), ("계약금액", None),
                                       ("준공금액", '=IF(B8="","",B8)'), ("계약년월일", None), ("착공년월일", None),
                                       ("완료기한", None), ("제출일", None), ("귀하", None)])
        fake(tpl2 / "표준틀_대금청구서.xlsx", [("수신자", None), ("사업명", None), ("계약금액", None), ("귀하(재무관)", None)])

        class _NF4(_NF):
            title, work_type, institution, raw = "D 설계 용역", "용역", "홍천군", {"dminsttNm": "강원특별자치도 홍천군"}

        k5 = "R26BK00000005-000"
        pa.add(k5)
        items = pa._read()
        items[k5]["winner"] = {"amount": "19710000"}
        pa._write(items)
        f5, _ = pa.prepare(k5, Path(d) / "out", fetch=lambda k, t: _NF4(), templates=tpl2, contract_lookup=lambda v: None)
        assert "아직 계약이 등록되지 않음" in (f5 / "공고문서" / "서류_확인.txt").read_text(encoding="utf-8")
        ws = load_workbook(f5 / "3_준공" / "완료계.xlsx")["기본사항"]
        assert [ws.cell(r, 2).value for r in (5, 6, 7, 8, 14)] == ["예", "강원특별자치도 홍천군", "D 설계 용역", 19710000, "강원특별자치도 홍천군"]
        assert ws["B10"].value is None and ws["B9"].value.startswith("=")
        # 담당자: 착수서류에 계약 값, 완료계 수신자는 손으로 고침
        sx = load_workbook(f5 / "2_착수" / "착수서류.xlsx")
        s = sx["기본사항"]
        for r, (a, b) in {6: ("수신", "홍천군 분임재무관"), 12: ("계약금액(금차)", 19700000), 15: ("계약일", datetime(2025, 3, 11)),
                          16: ("착수일", datetime(2025, 3, 12)), 17: ("준공예정일", datetime(2025, 8, 18))}.items():
            s.cell(r, 1, a), s.cell(r, 2, b)
        sx.save(f5 / "2_착수" / "착수서류.xlsx")
        w = load_workbook(f5 / "3_준공" / "완료계.xlsx")
        w["기본사항"]["B6"] = "홍천군 세무회계과"
        w.save(f5 / "3_준공" / "완료계.xlsx")
        row5 = {"untyCntrctNo": "R25TE1", "cntrctNm": "D 설계 용역", "thtmCntrctAmt": "19700000", "cntrctDate": "2025-03-12",
                "wbgnDate": "2025-03-12", "thtmScmpltDate": "2025-07-09", "cntrctInsttNm": "강원특별자치도 홍천군", "cntrctInsttChrgDeptNm": "세무회계과"}
        pa.prepare(k5, Path(d) / "out", fetch=lambda k, t: _NF4(), templates=tpl2, contract_lookup=lambda v: row5)
        ws = load_workbook(f5 / "3_준공" / "완료계.xlsx")["기본사항"]
        assert ws["B6"].value == "홍천군 세무회계과" and ws["B8"].value == 19700000 and ws["B14"].value == "홍천군 분임재무관"
        assert [ws.cell(r, 2).value.date().isoformat() for r in (10, 11, 12)] == ["2025-03-11", "2025-03-12", "2025-08-18"]
        assert ws["B9"].value.startswith("=") and ws["B13"].value is None
        c = load_workbook(f5 / "4_청구" / "청구서.xlsx")["기본사항"]
        assert c["B6"].value == "D 설계 용역" and c["B7"].value == 19700000 and c["B8"].value == "홍천군 분임재무관"
        text = (f5 / "공고문서" / "서류_확인.txt").read_text(encoding="utf-8")
        assert "담당자가 고친 칸은 그대로: 수신자" in text and "통합계약번호 R25TE1" in text, text
        # 착수서류(담당자)가 우선 — 나라장터 계약정보와 다른 날짜는 알림
        assert "확인: 계약년월일" in text and "'2025-03-11', 나라장터 계약정보 '2025-03-12'" in text and "확인: 준공예정일" in text, text
        assert pa._read()[k5]["contract"]["no"] == "R25TE1"
        # 착수서류를 안 채워도 나라장터 계약정보로 날짜·수신자(계약기관+담당부서), 조달청 대행이면 수신자는 그대로
        k6 = "R26BK00000006-000"
        pa.add(k6)
        f6, _ = pa.prepare(k6, Path(d) / "out", fetch=lambda k, t: _NF4(), templates=tpl2, contract_lookup=lambda v: row5)
        ws = load_workbook(f6 / "3_준공" / "완료계.xlsx")["기본사항"]
        assert ws["B6"].value == "강원특별자치도 홍천군 세무회계과" and ws["B8"].value == 19700000, ws["B6"].value
        assert [ws.cell(r, 2).value.date().isoformat() for r in (10, 11, 12)] == ["2025-03-12", "2025-03-12", "2025-07-09"]
        assert ws["B10"].fill.fgColor.rgb == _BLUE and ws["B13"].fill.fgColor.rgb == _YELLOW  # 자동으로 채운 노란 칸만 파랑
        assert "to" not in contract_values({**row5, "cntrctInsttNm": "조달청"})
        assert contract_values({"cntrctInsttNm": "경기도 경기도축산진흥센터", "cntrctInsttChrgDeptNm": "경기도축산진흥센터"})["to"] == "경기도 경기도축산진흥센터"
        def boom(v):
            raise RuntimeError("down")
        pa.prepare(k6, Path(d) / "out", fetch=lambda k, t: _NF4(), templates=tpl2, contract_lookup=boom)
        assert "계약정보를 받지 못함(RuntimeError)" in (f6 / "공고문서" / "서류_확인.txt").read_text(encoding="utf-8")

        # 계약 찾기 — 공고번호 → 통합공개(계약번호·계약일) → 계약정보 목록(하루씩)에서 같은 공고, 다음엔 통합계약번호로
        class _CC:
            calls = []

            def fetch_all_pages(self, url, op, params, label):
                self.calls.append((op, dict(params)))
                if op == INTG_OP:
                    return [{"cntrctInfoList": "[1^R25TA0001^D^홍천군^홍천군^수의계약^19710000^2025-03-12]"}] if params["bidNtceNo"] == "R25BK1" else []
                if params.get("inqryDiv") == "2":
                    return [{"untyCntrctNo": params["untyCntrctNo"], "cntrctNm": "D"}]
                if params["inqryBgnDt"].startswith("20250314"):
                    return [{"untyCntrctNo": "X", "ntceNo": "", "cntrctRefNo": ""},
                            {"untyCntrctNo": "R25TE9", "ntceNo": "", "cntrctRefNo": "R25TA0001", "corpList": "[1^주계약업체^단독^㈜지일^^^^^^2068636338]"}]
                return [{"untyCntrctNo": "Y", "ntceNo": "", "cntrctRefNo": ""}]

        cc = _CC()
        assert find_contract(cc, "R25BK1", "용역", "206-86-36338")["untyCntrctNo"] == "R25TE9"
        assert [p["inqryBgnDt"][:8] for op, p in cc.calls if op != INTG_OP] == ["20250312", "20250313", "20250314"]
        assert find_contract(cc, "R25BK2", "용역", "") is None and find_contract(cc, "R25BK1", "모름", "") is None
        assert find_contract(cc, "R25BK1", "용역", "", known="R25TE9")["cntrctNm"] == "D"
        by5 = {x["dir"]: x for x in pa.status(k5)["stages"]}
        assert by5["3_준공"]["doc"] == "완료계.xlsx" and by5["3_준공"]["todo"] == ["제출일"], by5["3_준공"]
        assert ["계약금액", "19,700,000"] in by5["3_준공"]["auto"] and "수신자" not in [a for a, _ in by5["3_준공"]["auto"]]
        assert by5["4_청구"]["doc"] == "청구서.xlsx" and by5["4_청구"]["todo"] == []
        assert share_path("/work/낙찰후서류/공사/A") == "\\\\jiilserver\\jiil-webapp\\낙찰후서류\\공사\\A"


if __name__ == "__main__":
    selfcheck()
    print("ok")
