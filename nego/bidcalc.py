"""투찰 계산기용 공고 조회 — 공고번호 하나로 기초금액·예비가격 범위·낙찰하한율·A값·순공사원가를 모은다.

계산(모의 추첨·예정가격·투찰금액)은 화면(webapp/static/bidcalc.js)이 한다. 여기는 나라장터에서 값만 가져온다.
- 본공고(입찰공고정보서비스): 공고명·기관·낙찰하한율(`sucsfbidLwltRate`)·낙찰자 결정기준(`sucsfbidMthdAppStd`)
- 기초금액(같은 서비스의 별도 오퍼레이션): `bssamt`·`rsrvtnPrceRngBgnRate/EndRate`·A값 항목·`bssAmtPurcnstcst`
  칸 이름은 2026-10-12 Run #94 실응답으로 확인. 단 **공고번호 조회(inqryDiv=2 + bidNtceNo)는 이 서비스에선 아직 실측 전**
  — 응답이 엉뚱한 공고를 주더라도 공고번호가 같은 행만 쓴다. 못 찾으면 화면에서 직접 입력.
"""

from __future__ import annotations

import dataclasses
import logging
import re
from typing import Callable

import requests

from . import attachments
from . import fields as F
from .http_client import ApiError, DataGoKrClient

log = logging.getLogger(__name__)

WORK_TYPES = ("공사", "용역", "물품")
# 물품 기초금액 오퍼레이션은 이름 추정(--verify에도 없음) — 없으면 그냥 못 찾음으로 끝난다
BASIS_OPERATIONS = {**F.BASIS_AMOUNT_OPERATIONS, "물품": "getBidPblancListInfoThngBsisAmount"}

# A값(적격심사 하한선 계산에서 깎지 않는 법정 비용) 항목 — 기초금액 API 칸 → 화면 이름
A_VALUE_FIELDS = {
    "npnInsrprm": "국민연금보험료",
    "mrfnHealthInsrprm": "국민건강보험료",
    "odsnLngtrmrcprInsrprm": "노인장기요양보험료",
    "rtrfundNon": "퇴직급여충당금",
    "sftyMngcst": "산업안전보건관리비",
    "sftyChckMngcst": "안전관리비",
    "qltyMngcst": "품질관리비",  # qltyMngcstAObjYn=Y일 때만 A값에 넣음
}


def _num(value) -> float | None:
    try:
        text = str(value).replace(",", "").replace("+", "").strip()
        return float(text) if text else None
    except (TypeError, ValueError):
        return None


def _latest(rows: list[dict], notice_no: str) -> dict | None:
    """공고번호가 같은 행 중 마지막 차수."""
    same = [r for r in rows if str(r.get("bidNtceNo") or "").strip() == notice_no]
    if not same:
        return None
    return max(same, key=lambda r: str(r.get("bidNtceOrd") or ""))


def a_value(row: dict) -> tuple[float, list[dict]]:
    """기초금액 행 → (A값 합계, 항목 목록[{name, field, amount}])."""
    items = []
    for field, name in A_VALUE_FIELDS.items():
        amount = _num(row.get(field))
        if not amount:
            continue
        if field == "qltyMngcst" and str(row.get("qltyMngcstAObjYn") or "").upper() != "Y":
            continue
        items.append({"name": name, "field": field, "amount": amount})
    return sum(i["amount"] for i in items), items


def parse_basis(row: dict) -> dict:
    total, items = a_value(row)
    return {
        "basis": _num(row.get("bssamt")),
        "range_lo": _num(row.get("rsrvtnPrceRngBgnRate")),
        "range_hi": _num(row.get("rsrvtnPrceRngEndRate")),
        "a_value": total,
        "a_items": items,
        # 공사만 오는 칸. Y면 A값을 하한선 계산에 넣는 공고
        "a_applies": str(row.get("bidPrceCalclAYn") or "").upper() == "Y",
        "net_cost": _num(row.get("bssAmtPurcnstcst")),
        "basis_open_at": str(row.get("bssamtOpenDt") or ""),
    }


# 지방자치단체·교육청 — 이름에 이 낱말이 있으면 지방계약법 기관(2026-10-22 제보: 울산광역시 울산안전체험관이 '조달청'으로 나왔음).
# "경기도 …"·"정선군"·"종로구청"처럼 낱말 끝이 시·도·군·구여야 함("부산지방국토관리청"·"한국도로공사" 같은 국가기관은 안 걸림).
# 지방공기업(○○도시공사 등)은 이름으로 가리기 어려워 안 봄 — 그때는 아래 결정기준·범위 규칙으로.
_LOCAL_GOV_RE = re.compile(r"(특별시|광역시|특별자치시|특별자치도|교육청|교육지원청|[가-힣]{1,4}(?:도|시|군|구)(?:청)?)(?=\s|$|\()")


def method_reason(standard: str, range_lo: float | None, range_hi: float | None,
                  institutions: list[str] | None = None) -> tuple[str, str]:
    """예비가격 방식 추정 — (지방계약(행안부) 'mois' / 국가계약(조달청) 'pps', 근거 문구).
    ① 수요기관·공고기관이 지방자치단체·교육청이면 지방 ② 낙찰자 결정기준에 '지방' ③ 범위 ±3% ④ 아니면 국가."""
    for name in institutions or []:
        if name and (m := _LOCAL_GOV_RE.search(name)):
            return "mois", f"발주기관 {name.strip()}"
    if "지방" in (standard or ""):
        return "mois", "낙찰자 결정기준"
    if range_lo is not None and range_hi is not None and abs(range_lo) >= 3 and abs(range_hi) >= 3:
        return "mois", "기초금액 ±3%"
    return "pps", "지방자치단체 표시 없음"


# 조달청·지방조달청(○○지방조달청) — 공고기관이 이것이고 수요기관이 따로 있으면 조달청이 대신 낸(대행) 공고
_PPS_AGENCY_RE = re.compile(r"조달청")


def agency_judgment(demand: str, notice: str, method: str = "", reason: str = "") -> tuple[str | None, str]:
    """공고기관·수요기관을 함께 보고 발주기관을 최종 판단 — ('local' 지방계약 / 'national' 국가계약 / None 모름, 화면 문구).
    기관 이름이 먼저(지방자치단체·교육청), 이름으로 못 가리면 예비가격 방식 추정(`method_reason` — 결정기준 '지방'·±3%)을 따른다.
    조달청이 지자체 대신 낸 공고(공고기관 조달청 + 수요기관 지자체)는 지금처럼 **수요기관 기준**(지방계약) —
    공고기관을 먼저 볼지는 사용자 결정 대기(사례가 나오면)."""
    demand, notice = (demand or "").strip(), (notice or "").strip()
    if not (demand or notice):
        return None, "공고기관·수요기관 정보 없음"
    both = f"공고기관 {notice or '-'} · 수요기관 {demand or '-'}"
    local = lambda name: bool(name and _LOCAL_GOV_RE.search(name))
    if local(demand) and notice != demand and _PPS_AGENCY_RE.search(notice):
        return "local", f"{both} → 지방계약 (조달청 대행 공고 — 수요기관 기준)"
    if local(demand):
        return "local", f"{both} → 지방계약 (수요기관이 지방자치단체·교육청)"
    if local(notice):
        return "local", f"{both} → 지방계약 (공고기관이 지방자치단체·교육청)"
    if method == "mois":
        return "local", f"{both} → 지방계약으로 추정 (기관 이름엔 지자체 표시 없음 — {reason})"
    return "national", f"{both} → 국가계약 (지방자치단체·교육청 아님)"


def guess_method(standard: str, range_lo: float | None, range_hi: float | None,
                 institutions: list[str] | None = None) -> str:
    return method_reason(standard, range_lo, range_hi, institutions)[0]


# 첨부 공고문에서 읽는 값 — 기초금액 API가 비거나(물품 등) 하한율이 없을 때만 쓴다.
# "※ 기초금액 : 금 | 139,000,000원"(hwpx 표 칸 구분 |), "기초금액(부가세 포함): 139,000,000원" 등
_BASIS_TEXT_RE = re.compile(r"기초\s*금액\s*(?:\([^)]{0,20}\))?[\s|:：]*(?:금\s*)?[\s|]*([\d,]{7,})\s*원")
_RATE_TEXT_RE = re.compile(r"낙찰\s*하한율[^\d\n]{0,15}(\d{2}(?:\.\d+)?)\s*%")
_RANGE_TEXT_RE = re.compile(r"기초\s*금액의?\s*[±+\-]\s*(\d(?:\.\d+)?)\s*%")
# 먼저 읽을 첨부 — 공고문이 기초금액·하한율을 적는 곳
_NOTICE_FILE_RE = re.compile(r"공고")
MAX_TEXT_FILES = 3


def parse_notice_text(text: str) -> dict:
    """공고문 원문 → {basis, lower_rate, range_lo, range_hi} 중 찾은 것만."""
    out: dict = {}
    for m in _BASIS_TEXT_RE.finditer(text):
        amount = _num(m.group(1))
        if amount and amount >= 1_000_000:
            out["basis"] = amount
            break
    if m := _RATE_TEXT_RE.search(text):
        rate = _num(m.group(1))
        if rate and 50 <= rate < 100:
            out["lower_rate"] = rate
    if m := _RANGE_TEXT_RE.search(text):
        width = _num(m.group(1))
        if width and width <= 5:
            out["range_lo"], out["range_hi"] = -width, width
    return out


def _from_attachments(notice: dict, out: dict, fetch: Callable) -> None:
    """기초금액·하한율·범위 중 빈 칸을 첨부 공고문에서 채운다(파일명에 '공고'가 든 것부터, 최대 3개)."""
    files = F.extract_attachments(notice)
    files.sort(key=lambda a: not _NOTICE_FILE_RE.search(a.get("file_name", "")))
    session = requests.Session()
    for att in files[:MAX_TEXT_FILES]:
        result = fetch(session, att, timeout=30.0)
        if not result.ok or not result.text:
            continue
        found = parse_notice_text(result.text)
        filled = [k for k in ("basis", "lower_rate", "range_lo", "range_hi") if out.get(k) is None and k in found]
        for k in filled:
            out[k] = found[k]
        if "basis" in filled:
            out["basis_source"] = f"첨부 {att.get('file_name') or '공고문'}"
        if filled:
            out["notes"].append(f"{', '.join(_FIELD_NAMES[k] for k in filled if k in _FIELD_NAMES)}: 첨부 '{att.get('file_name')}'에서 읽음 — 원문과 확인하세요")
        if all(out.get(k) is not None for k in ("basis", "lower_rate", "range_lo")):
            break


_FIELD_NAMES = {"basis": "기초금액", "lower_rate": "낙찰하한율", "range_lo": "변동폭"}


def _notice_day(notice: dict) -> str | None:
    """공고 등록일(YYYYMMDD) — 면허제한 API는 공고번호로 못 찾고 등록일 범위로만 조회된다."""
    for key in ("rgstDt", "bidNtceDt"):
        digits = re.sub(r"\D", "", str(notice.get(key) or ""))
        if len(digits) >= 8:
            return digits[:8]
    return None


def _licenses(client: DataGoKrClient, notice: dict, notice_no: str, notice_ord: str) -> list[str]:
    """면허제한 업종 이름(21라인 '업종제한' 칸) — 실패하면 빈 목록.

    이 오퍼레이션은 공고번호 조회(inqryDiv=2)를 받지 않는다(2026-10-15 실측: resultCode=08 필수값 입력 에러).
    수집과 같이 inqryDiv=1 + 등록일 하루치로 받고 공고번호로 거른다."""
    day = _notice_day(notice)
    if not day:
        return []
    params = {"inqryDiv": "1", "inqryBgnDt": f"{day}0000", "inqryEndDt": f"{day}2359"}
    try:
        rows = client.fetch_all_pages(F.BID_NOTICE_BASE_URL, F.LICENSE_LIMIT_OPERATION, params, "계산기 면허제한")
    except ApiError as err:
        log.info("계산기 면허제한 조회 실패 %s: %s", notice_no, err)
        return []
    rows = [r for r in rows if str(r.get("bidNtceNo") or "").strip() == notice_no]
    if notice_ord:
        rows = [r for r in rows if str(r.get("bidNtceOrd") or "") in ("", notice_ord)] or rows
    names: list[str] = []
    for r in rows:
        name = str(r.get("lcnsLmtNm") or "").split("/")[0].strip()
        if name and name not in names:
            names.append(name)
    return names


def lookup(client: DataGoKrClient, notice_no: str, fetch: Callable = attachments.fetch_attachment_text) -> dict:
    """공고번호 → 계산기 입력값. 못 찾은 칸은 None, 이유는 notes에."""
    notice_no = notice_no.strip().upper()
    if "-" in notice_no:  # "R26BK01744201-001"처럼 차수까지 붙여 넣은 경우
        notice_no = notice_no.split("-")[0]
    out: dict = {"notice_no": notice_no, "notes": []}
    params = {"inqryDiv": "2", "bidNtceNo": notice_no}

    notice, work_type, failed = None, None, 0
    for wt in WORK_TYPES:
        try:
            rows = client.fetch_all_pages(F.BID_NOTICE_BASE_URL, F.BID_NOTICE_OPERATIONS[wt], params, f"계산기 공고/{wt}")
        except ApiError as err:
            log.info("계산기 공고 조회 실패 %s %s: %s", wt, notice_no, err)
            failed += 1
            continue
        notice = _latest(rows, notice_no)
        if notice:
            work_type = wt
            break
    if notice:
        out.update({
            "notice_ord": str(notice.get("bidNtceOrd") or ""),
            "title": str(notice.get("bidNtceNm") or ""),
            "institution": str(notice.get("dminsttNm") or notice.get("ntceInsttNm") or ""),
            "notice_institution": str(notice.get("ntceInsttNm") or ""),
            "demand_institution": str(notice.get("dminsttNm") or ""),
            # 예가방법(단일예가·복수예가·비예가) — 복수예가가 아니면 예비가격 추첨이 없는 공고
            "price_method": str(notice.get("prearngPrceDcsnMthdNm") or ""),
            "work_type": work_type,
            "lower_rate": _num(notice.get("sucsfbidLwltRate")),
            "standard": str(notice.get("sucsfbidMthdAppStd") or ""),
            "award_method": str(notice.get("sucsfbidMthdNm") or ""),
            "contract_method": str(notice.get("cntrctCnclsMthdNm") or ""),
            "est_price": _num(notice.get("presmptPrce")),
            "price_count": _num(notice.get("totPrdprcNum")),
        })
    else:
        out["notes"].append("공고를 찾지 못함 — 공고번호를 확인하거나 값을 직접 입력하세요")
    # 화면이 오입력(found=False)과 나라장터 조회 실패(lookup_error — 공고 조회가 전부 오류)를 구분해 경고
    out["found"] = notice is not None
    out["lookup_error"] = notice is None and failed == len(WORK_TYPES)

    if notice:
        out["licenses"] = _licenses(client, notice, notice_no, out.get("notice_ord", ""))

    basis_row = None
    for wt in ([work_type] if work_type else []) + [w for w in WORK_TYPES if w != work_type]:
        op = BASIS_OPERATIONS.get(wt)
        if not op:
            continue
        try:
            rows = client.fetch_all_pages(F.BID_NOTICE_BASE_URL, op, params, f"계산기 기초금액/{wt}")
        except ApiError as err:
            log.info("계산기 기초금액 조회 실패 %s %s: %s", wt, notice_no, err)
            continue
        basis_row = _latest(rows, notice_no)
        if basis_row:
            break
    if basis_row:
        out.update(parse_basis(basis_row))
        out["basis_source"] = "나라장터 기초금액"
    if notice and (out.get("basis") is None or out.get("lower_rate") is None):
        _from_attachments(notice, out, fetch)
    if out.get("basis") is None:
        out["notes"].append("기초금액을 나라장터·첨부 공고문에서 찾지 못함 (아직 비공개일 수 있음) — 직접 입력하세요")

    if notice and out.get("lower_rate") is None:
        out["notes"].append("낙찰하한율이 없는 공고 — 협상·최저가 등 하한율 방식이 아닐 수 있음")
    out["method"], out["method_reason"] = method_reason(
        out.get("standard", ""), out.get("range_lo"), out.get("range_hi"),
        [out.get("institution", ""), out.get("notice_institution", "")])
    if notice:
        out["agency"], out["agency_note"] = agency_judgment(out.get("demand_institution", ""), out.get("notice_institution", ""),
                                                         out["method"], out["method_reason"])
    return out


def make_client(api_config) -> DataGoKrClient:
    """화면에서 기다리는 조회라 재시도를 줄인 클라이언트."""
    return DataGoKrClient(dataclasses.replace(api_config, max_retries=1, max_pages=5))
