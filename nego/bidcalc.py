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


def guess_method(standard: str, range_lo: float | None, range_hi: float | None) -> str:
    """예비가격 방식 추정 — 지방계약(행안부) 'mois' / 국가계약(조달청) 'pps'.
    낙찰자 결정기준 문구에 '지방'이 있거나 범위가 ±3%면 지방, 아니면 국가."""
    if "지방" in (standard or ""):
        return "mois"
    if range_lo is not None and range_hi is not None and abs(range_lo) >= 3 and abs(range_hi) >= 3:
        return "mois"
    return "pps"


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


def lookup(client: DataGoKrClient, notice_no: str, fetch: Callable = attachments.fetch_attachment_text) -> dict:
    """공고번호 → 계산기 입력값. 못 찾은 칸은 None, 이유는 notes에."""
    notice_no = notice_no.strip().upper()
    if "-" in notice_no:  # "R26BK01744201-001"처럼 차수까지 붙여 넣은 경우
        notice_no = notice_no.split("-")[0]
    out: dict = {"notice_no": notice_no, "notes": []}
    params = {"inqryDiv": "2", "bidNtceNo": notice_no}

    notice, work_type = None, None
    for wt in WORK_TYPES:
        try:
            rows = client.fetch_all_pages(F.BID_NOTICE_BASE_URL, F.BID_NOTICE_OPERATIONS[wt], params, f"계산기 공고/{wt}")
        except ApiError as err:
            log.info("계산기 공고 조회 실패 %s %s: %s", wt, notice_no, err)
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
            "work_type": work_type,
            "lower_rate": _num(notice.get("sucsfbidLwltRate")),
            "standard": str(notice.get("sucsfbidMthdAppStd") or ""),
            "award_method": str(notice.get("sucsfbidMthdNm") or ""),
            "price_count": _num(notice.get("totPrdprcNum")),
        })
    else:
        out["notes"].append("공고를 찾지 못함 — 공고번호를 확인하거나 값을 직접 입력하세요")

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
    out["method"] = guess_method(out.get("standard", ""), out.get("range_lo"), out.get("range_hi"))
    return out


def make_client(api_config) -> DataGoKrClient:
    """화면에서 기다리는 조회라 재시도를 줄인 클라이언트."""
    return DataGoKrClient(dataclasses.replace(api_config, max_retries=1, max_pages=2))
