"""나라장터 응답 필드 정의.

data.go.kr은 서비스 개정 시 필드명이 바뀌는 일이 있어, 기존 TypeScript 시스템과 같이
**후보 리스트에서 첫 유효값을 고르는** 방식을 쓴다. 필드가 비어 보이면 코드를 고치지 말고
여기 후보 목록에 실제 필드명을 추가하면 된다 (`scripts/verify_api.py`로 원본 키 확인 가능).
"""

from __future__ import annotations

from typing import Any, Iterable

RawItem = dict[str, Any]

BID_NOTICE_BASE_URL = "https://apis.data.go.kr/1230000/ad/BidPublicInfoService"

# 업무구분별 오퍼레이션. 협상에 의한 계약은 용역·물품에 집중되지만,
# 전시물 제작설치가 공사로 발주되는 경우도 있어 3종을 모두 조회한다.
BID_NOTICE_OPERATIONS = {
    "용역": "getBidPblancListInfoServc",
    "물품": "getBidPblancListInfoThng",
    "공사": "getBidPblancListInfoCnstwk",
}

LICENSE_LIMIT_OPERATION = "getBidPblancListInfoLicenseLimit"

# ── 사전규격 (조달청_나라장터 사전규격정보서비스) ──────────────────
# 본공고와 **별도 서비스**다. 공공데이터포털에서 이 서비스도 따로 활용신청해야 같은
# 서비스키로 호출된다(안 하면 SERVICE KEY IS NOT REGISTERED). 주소가 바뀌면 코드 대신
# 환경변수 PRESPEC_BASE_URL로 덮어쓴다. 필드명은 활용가이드 기준이며 실제 응답으로는
# 아직 확인 전 — `python -m nego --verify`에 사전규격도 넣어 두었다.
PRESPEC_BASE_URL = "https://apis.data.go.kr/1230000/ao/HrcspSsstndrdInfoService"
PRESPEC_OPERATIONS = {
    "용역": "getPublicPrcureThngInfoServc",
    "물품": "getPublicPrcureThngInfoThng",
    "공사": "getPublicPrcureThngInfoCnstwk",
}
PRESPEC_FIELDS: dict[str, list[str]] = {
    "notice_no": ["bfSpecRgstNo"],
    "title": ["prdctClsfcNoNm", "bsnsNm", "bfSpecNm"],
    "notice_institution": ["orderInsttNm", "ntceInsttNm"],
    "demand_institution": ["rlDminsttNm", "dminsttNm"],
    "assigned_budget": ["asignBdgtAmt"],
    "posted_at": ["rcptDt", "rgstDt"],
    "opinion_deadline": ["opinionRgstClseDt", "opninRgstClseDt"],
    "detail_url": ["bfSpecDtlUrl", "dtlUrl"],
    "product_class_no": ["dtilPrdctClsfcNo", "prdctClsfcNo"],
    "linked_bid_notices": ["bidNtceNoList"],
    "sw_business_yn": ["swBizObjYn"],
}
PRESPEC_ATTACHMENT_URL_PREFIX = "specDocFileUrl"
PRESPEC_ATTACHMENT_NAME_PREFIX = "specDocFileNm"
PRESPEC_ATTACHMENT_MAX = 5
REGION_LIMIT_OPERATION = "getBidPblancListInfoPrtcptPsblRgn"

# ── 투찰가격 분석용 (아직 수집에는 안 씀 — `--verify`로 응답 칸 확인부터) ─────────
# 기초금액은 입찰공고정보서비스 안의 별도 오퍼레이션, 개찰 결과·예비가격은
# **별도 서비스(조달청_나라장터 낙찰정보서비스)** 라 공공데이터포털에서 따로 활용신청해야 한다.
# 주소·오퍼레이션 이름은 활용가이드 기준 추정 — 주소가 다르면 환경변수 SCSBID_BASE_URL로 덮어쓴다.
BASIS_AMOUNT_OPERATIONS = {
    "용역": "getBidPblancListInfoServcBsisAmount",
    "공사": "getBidPblancListInfoCnstwkBsisAmount",
}
SCSBID_BASE_URL = "https://apis.data.go.kr/1230000/as/ScsbidInfoService"
SCSBID_OPERATIONS = {
    "개찰결과/용역": "getOpengResultListInfoServc",
    "개찰결과/공사": "getOpengResultListInfoCnstwk",
    "개찰결과/물품": "getOpengResultListInfoThng",
    "예비가격/용역": "getOpengResultListInfoServcPreparPcDetail",
    "예비가격/공사": "getOpengResultListInfoCnstwkPreparPcDetail",
    "낙찰현황/용역": "getScsbidListSttusServc",
    "낙찰현황/공사": "getScsbidListSttusCnstwk",
}

FIELD_CANDIDATES: dict[str, list[str]] = {
    # ── 식별 ──────────────────────────────────────────────
    "notice_no": ["bidNtceNo"],
    "notice_ord": ["bidNtceOrd"],
    "unified_notice_no": ["untyNtceNo"],
    "title": ["bidNtceNm"],
    "notice_institution": ["ntceInsttNm"],
    "demand_institution": ["dminsttNm", "dmndInsttNm"],
    "detail_url": ["bidNtceDtlUrl", "bidNtceUrl"],
    # ── 계약 방식 (오늘 작업의 핵심) ─────────────────────────
    # 낙찰자결정방법. "협상에 의한 계약"은 여기에 들어온다.
    "award_method": ["sucsfbidMthdNm"],
    "award_method_code": ["sucsfbidMthdCd"],
    "award_method_std": ["sucsfbidMthdAppStd"],
    # 계약체결방법(일반경쟁/제한경쟁/수의계약…). 낙찰자결정방법과 직교한다.
    "contract_method": ["cntrctCnclsMthdNm"],
    # 입찰방식(전자입찰/직찰/직찰·우편). 직찰은 전자입찰 일정 필드가 비어 있는 게 정상이다.
    "bid_method": ["bidMethdNm"],
    # ── 공고 상태 ────────────────────────────────────────
    "notice_kind": ["ntceKindNm"],  # 등록공고 / 변경공고 / 재공고 / 취소공고
    "re_notice_yn": ["reNtceYn"],
    "change_reason": ["chgNtceRsn"],
    "changed_at": ["chgDt"],
    # ── 금액 ────────────────────────────────────────────
    "estimated_price": ["presmptPrce"],
    "assigned_budget": ["asignBdgtAmt"],
    # ── 일정 ────────────────────────────────────────────
    "posted_at": ["bidNtceDt", "bidNtceDate", "rgstDt"],
    "qualification_deadline": ["bidQlfctRgstDt"],
    "bid_deadline": ["bidClseDt", "bidClseDate"],
    "opening_at": ["opengDt"],
    # 예가방법(예정가격 결정방법). 실측(2026-09-17 `nego --verify`, Run #46):
    # 용역/물품/공사 세 오퍼레이션 모두에 있고 값도 실제로 채워져 있다
    # (예: 용역="비예가", 물품/공사="단일예가"). "복수예가"도 나올 수 있다.
    "estimate_price_method": ["prearngPrceDcsnMthdNm"],
    # ── 공동수급 ─────────────────────────────────────────
    "joint_method_code": ["cmmnSpldmdMethdCd"],
    "joint_method_name": ["cmmnSpldmdMethdNm"],
    "joint_agreement_deadline": ["cmmnSpldmdAgrmntClseDt"],
    "joint_member_region_limit": ["cmmnSpldmdCorpRgnLmtYn"],
    # ── 제한 플래그 ───────────────────────────────────────
    "industry_limit_yn": ["indstrytyLmtYn"],
    "region_limit_basis": ["rgnLmtBidLocplcJdgmBssNm"],
    "region_duty_rate": ["rgnDutyJntcontrctRt"],
    "duty_region_1": ["jntcontrctDutyRgnNm1"],
    "duty_region_2": ["jntcontrctDutyRgnNm2"],
    "duty_region_3": ["jntcontrctDutyRgnNm3"],
    # ── 평가 ────────────────────────────────────────────
    "performance_competition_yn": ["arsltCmptYn"],
    # ── 분류 ────────────────────────────────────────────
    "industry_text": ["bidprcPsblIndstrytyNm"],
    "product_class_no": ["prdctClsfcNo"],
    "product_class_name": ["prdctClsfcNoNm", "pubPrcrmntClsfcNm"],
    "service_division": ["srvceDivNm"],
}

# 첨부파일은 번호가 붙은 반복 필드다 (1~10).
ATTACHMENT_NAME_PREFIX = "ntceSpecFileNm"
ATTACHMENT_URL_PREFIX = "ntceSpecDocUrl"
ATTACHMENT_MAX = 10

LICENSE_LIMIT_FIELDS: dict[str, list[str]] = {
    "notice_no": ["bidNtceNo"],
    "notice_ord": ["bidNtceOrd"],
    "group_no": ["lmtGrpNo", "rstrctGroupNo", "prtcptLmtGroupNo", "lmtGroupNo"],
    "seq_no": ["lmtSno", "rstrctSeqNo", "lmtSeqNo"],
    "license_name": ["lcnsLmtNm", "licenseLmtNm", "lmtLicenseNm"],
    "allowed_industries": ["permsnIndstrytyList", "alwIndstrytyNm", "admisIndstrytyNm"],
}

REGION_LIMIT_FIELDS: dict[str, list[str]] = {
    "notice_no": ["bidNtceNo"],
    "notice_ord": ["bidNtceOrd"],
    "region_name": ["prtcptPsblRgnNm"],
}


def pick(raw: RawItem, candidates: Iterable[str]) -> str | None:
    """후보 필드명 중 값이 실제로 채워진 첫 번째를 반환한다."""
    for name in candidates:
        value = raw.get(name)
        if value is None:
            continue
        text = str(value).strip()
        if text and text.lower() not in {"null", "none"}:
            return text
    return None


def pick_field(raw: RawItem, key: str) -> str | None:
    return pick(raw, FIELD_CANDIDATES.get(key, []))


def pick_number(raw: RawItem, key: str) -> float | None:
    text = pick_field(raw, key)
    if text is None:
        return None
    cleaned = text.replace(",", "").replace("원", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return None


def pick_by(raw: RawItem, fields: dict[str, list[str]], key: str) -> str | None:
    return pick(raw, fields.get(key, []))


def extract_attachments(
    raw: RawItem,
    name_prefix: str = ATTACHMENT_NAME_PREFIX,
    url_prefix: str = ATTACHMENT_URL_PREFIX,
    max_count: int = ATTACHMENT_MAX,
) -> list[dict[str, str]]:
    """ntceSpecFileNm1~10 / ntceSpecDocUrl1~10 을 리스트로 정리한다.

    오늘 범위에서 첨부파일을 내려받지는 않지만, 목록은 지금부터 저장해 둔다.
    나중에 파싱 단계를 붙일 때 재수집이 필요 없어진다.
    """
    out: list[dict[str, str]] = []
    for i in range(1, max_count + 1):
        name = raw.get(f"{name_prefix}{i}")
        url = raw.get(f"{url_prefix}{i}")
        name_text = str(name).strip() if name is not None else ""
        url_text = str(url).strip() if url is not None else ""
        if not name_text and not url_text:
            continue
        ext = name_text.rsplit(".", 1)[-1].lower() if "." in name_text else ""
        out.append({"seq": str(i), "file_name": name_text, "url": url_text, "ext": ext})
    return out


# 수집 원문에는 발주기관 담당자 이름·전화·이메일이 들어 있다.
# 저장 전에 제거한다 (리포트에 필요하지 않고, 레포/아티팩트에 남으면 안 된다).
PERSONAL_FIELDS = (
    "ntceInsttOfclNm",
    "ntceInsttOfclTelNo",
    "ntceInsttOfclEmailAdrs",
    "dminsttOfclEmailAdrs",
    "exctvNm",
    "crdtrNm",
    # 사전규격 담당자
    "ofclNm",
    "ofclTelNo",
    "ofclEmail",
)


def strip_personal_fields(raw: RawItem) -> RawItem:
    return {k: v for k, v in raw.items() if k not in PERSONAL_FIELDS}
