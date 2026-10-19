"""지역 제한 판정 — 공고가 요구하는 업체 소재지(시·도)와 지일 소재지를 비교한다.

2026-09-30 사용자 요청: 경기북부어린이박물관 공고가 "주된 영업소의 소재지가 경기도에 있는 업체"만
받는데(공동수급 불허) 자격판정은 "자격 충족"으로 나왔다. 면허·품목만 보고 소재지는 안 봤기 때문.

- 근거는 두 가지: 나라장터 참가가능지역 API(`candidate.regions`)와 첨부 공고문의 참가자격 문장.
- 결과는 충족 / 미달 / 미확인 3상태. **API 판정이 "미달"이면 후보에서 뺀다**(2026-10-14 사용자 결정, `pipeline.drop_region_unmet`) —
  지역제한 입찰은 공동수급을 허용해도 구성원 모두가 소재지 요건을 채워야 해서 지일은 못 들어간다. "미확인"은 표시만.
- **첨부 공고문 판정은 제외에 쓰지 않고 "지역 제한 의심" 표시만 한다**(2026-10-19 통합 결정 7번 — 첨부 파싱 정확도가
  아직 완전하지 않아 공고가 조용히 사라지는 걸 막으려고). 예전엔 첨부가 API보다 우선했다(`combine`, 지금은 안 씀).
- **시·군 단위까지 본다**(통합 결정 7번): 지일 본사 '강원특별자치도 정선군' 기준으로 "강원 춘천시"만 받는 공고는 미달.
  "동두천시 관내"처럼 시·도 없이 시·군만 적힌 경우는 어느 도인지 몰라 미확인(지일 시·군과 같으면 충족).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# 시·도 표준명 → 문서에 나오는 표기들 (옛 이름·줄임말 포함)
SIDO_ALIASES: dict[str, tuple[str, ...]] = {
    "서울": ("서울특별시", "서울시", "서울"),
    "부산": ("부산광역시", "부산시", "부산"),
    "대구": ("대구광역시", "대구시", "대구"),
    "인천": ("인천광역시", "인천시", "인천"),
    "광주": ("광주광역시", "광주시"),  # "광주"만 쓰면 경기도 광주시와 헷갈려서 뺀다
    "대전": ("대전광역시", "대전시", "대전"),
    "울산": ("울산광역시", "울산시", "울산"),
    "세종": ("세종특별자치시", "세종시", "세종"),
    "경기": ("경기도", "경기"),
    "강원": ("강원특별자치도", "강원도", "강원"),
    "충북": ("충청북도", "충북"),
    "충남": ("충청남도", "충남"),
    "전북": ("전북특별자치도", "전라북도", "전북"),
    "전남": ("전라남도", "전남"),
    "경북": ("경상북도", "경북"),
    "경남": ("경상남도", "경남"),
    "제주": ("제주특별자치도", "제주도", "제주"),
}
_ALIAS_TO_SIDO = sorted(
    ((alias, sido) for sido, aliases in SIDO_ALIASES.items() for alias in aliases), key=lambda x: -len(x[0])
)
# 두 글자 줄임말("경기", "서울")은 뒤에 다른 한글이 붙으면 다른 낱말이다("경기장", "서울대") —
# "경기 지역", "경기 내", "경기권"처럼 띄어 쓰거나 내·권·지역이 올 때만 인정한다.
_SIDO_RE = re.compile(
    "|".join(
        re.escape(a) + (r"(?:(?=[내권])|(?![가-힣]))" if len(a) == 2 else "")
        for a, _ in _ALIAS_TO_SIDO
    )
)

# 소재지 요건 문장의 신호어 — "주된 영업소 소재지", "본점(본사) 소재지", "~에 소재한", "관내 업체"
_LOCATION_HINT_RE = re.compile(r"소재지|소재한|소재하는|주된\s*영업소|본점|본사|관내")
# 지역 제한이 아니라 다른 뜻으로 시·도 이름이 나오는 문장
# (실측: 남자현지사 제안요청서 "대구·경북 소재 업체와 공동으로 참여를 권장함" — 권장은 제한이 아니다)
_NOT_LIMIT_RE = re.compile(r"공동수급|공동으로|분담|지역의무|구성원|하도급|납품\s*장소|설치\s*장소|현장|권장|권고|우대|가점|가산")


_SIGUNGU_RE = re.compile(r"([가-힣]{1,6}?(?:시|군|구))(?:(?![가-힣])|(?=에|의|내|소재))")  # "정선군에 있는", "정선군 내"
# 시·군 나열의 다음 항목 — "속초시·고성군", "속초시, 고성군", "속초시 및 고성군"
_SIGUNGU_LIST_RE = re.compile(r"\s*(?:[·ㆍ‧,、/]|및|또는)\s*([가-힣]{1,6}?(?:시|군|구))(?:(?![가-힣])|(?=에|의|내|소재))")


def split_location(name: str | None) -> tuple[str | None, str | None]:
    """'강원특별자치도 정선군' → ('강원', '정선군'), '경기도' → ('경기', None), '강원' → ('강원', None)."""
    if not name:
        return None, None
    sido = sido_of(name)
    if sido is None:
        return None, None
    rest = re.sub(r"\s+", " ", name).strip()
    for alias in sorted(SIDO_ALIASES[sido], key=len, reverse=True):
        if rest.replace(" ", "").startswith(alias):
            rest = rest.replace(" ", "")[len(alias):]
            break
    m = _SIGUNGU_RE.match(rest)
    return sido, (m.group(1) if m else None)


def _label(sido: str, sigungu: str | None) -> str:
    return f"{sido} {sigungu}" if sigungu else sido


def sido_of(name: str | None) -> str | None:
    """'경기도', '강원특별자치도 정선군', '서울특별시 강남구' → 표준 시·도명."""
    if not name:
        return None
    text = re.sub(r"\s+", "", name)
    for alias, sido in _ALIAS_TO_SIDO:
        if text.startswith(re.sub(r"\s+", "", alias)):
            return sido
    return None


@dataclass
class RegionCheck:
    status: str = "미확인"  # 충족 / 미달 / 미확인
    required: list[str] = field(default_factory=list)  # 요구 시·도(표준명)
    company: str | None = None
    source: str | None = None  # "나라장터 참가가능지역" / "첨부 공고문"
    evidence: str | None = None

    def to_dict(self) -> dict:
        return {"status": self.status, "required": self.required, "company": self.company,
                "source": self.source, "evidence": self.evidence,
                "summary": summarize(self.evidence) if self.source == "첨부 공고문" else None}


def company_sido(held_raw: dict) -> str | None:
    return sido_of((held_raw or {}).get("companyRegion"))


def company_location(held_raw: dict) -> str | None:
    """지일 본사 소재지 원문('강원특별자치도 정선군') — 시·군 단위 판정에 쓴다."""
    return (held_raw or {}).get("companyRegion") or None


def _judge(required: set[tuple[str, str | None]], company: str | None, source: str, evidence: str) -> RegionCheck:
    """required = {(시·도, 시·군 또는 None)}. 하나라도 지일 소재지를 포함하면 충족."""
    order = list(SIDO_ALIASES)
    ordered = [_label(*r) for r in sorted(required, key=lambda r: (order.index(r[0]), r[1] or ""))]
    company_sido_, company_sigungu = split_location(company)
    if company_sido_ is None:
        return RegionCheck("미확인", ordered, None, source, evidence)
    ok = any(sido == company_sido_ and (sigungu is None or sigungu == company_sigungu) for sido, sigungu in required)
    return RegionCheck("충족" if ok else "미달", ordered, _label(company_sido_, company_sigungu), source, evidence)


def from_api(regions: list[str], company: str | None) -> RegionCheck:
    """나라장터 참가가능지역 목록. 비어 있으면 '제한 없음'이 아니라 '정보 없음'이다.
    '강원특별자치도 춘천시'처럼 시·군까지 적힌 항목은 시·군까지 맞아야 한다."""
    required = {loc for loc in (split_location(r) for r in regions) if loc[0]}
    if not required:
        sido, sigungu = split_location(company)
        return RegionCheck(company=_label(sido, sigungu) if sido else None)
    return _judge(required, company, "나라장터 참가가능지역", ", ".join(dict.fromkeys(regions)))


def from_text(items: list[str], company: str | None) -> RegionCheck | None:
    """첨부 참가자격 항목에서 '주된 영업소의 소재지가 경기도에 있는 업체' 같은 문장을 찾는다."""
    for item in items:
        for sentence in re.split(r"(?<=[.。])\s+|\n", item):
            if not _LOCATION_HINT_RE.search(sentence) or _NOT_LIMIT_RE.search(sentence):
                continue
            # 정규식이 긴 표기부터 맞춰서 "경기도"를 "경기"로 두 번 세지 않는다. 바로 뒤에 시·군이 붙으면 시·군까지
            required = set()
            for m in _SIDO_RE.finditer(sentence):
                sido = sido_of(m.group(0))
                if sido is None:
                    continue
                rest = re.sub(r"^\s+", "", sentence[m.end():])
                after = _SIGUNGU_RE.match(rest)
                if not after or after.group(1).endswith("구"):
                    required.add((sido, None))
                    continue
                required.add((sido, after.group(1)))
                # "강원특별자치도 속초시·고성군·양양군·강릉시"처럼 이어 적은 시·군도 전부(2026-10-19 제보 — 속초시만 보였음)
                rest = rest[after.end():]
                while (more := _SIGUNGU_LIST_RE.match(rest)) and not more.group(1).endswith("구"):
                    required.add((sido, more.group(1)))
                    rest = rest[more.end():]
            if not required:
                continue
            evidence = re.sub(r"\s+", " ", sentence).strip()[:300]
            return _judge(required, company, "첨부 공고문", evidence)
    return None


def combine(api: RegionCheck, text: RegionCheck | None) -> RegionCheck:
    """첨부 공고문 쪽을 우선한다 — 공고문이 원문이고, API는 발주기관이 비워 두는 일이 많다."""
    if text is not None and text.status != "미확인":
        return text
    return api


# ── 팝업용 요약 (2026-10-02 사용자 요청: 원문 표기가 공고마다 제각각이라 핵심 낱말로 같은 모양의 문장을 만든다) ──
# 결과: {"basis": "법인등기부상 본점 소재지", "period": "입찰공고일 전일 ~ 입찰일 (낙찰자: 계약체결일)", "individual": True}
_WSP = r"\s*"
_START_RE = re.compile(r"(입찰" + _WSP + r")?공고" + _WSP + r"일" + _WSP + r"(전일|현재|기준)?")
_END_RE = re.compile(
    r"(입찰" + _WSP + r"참가" + _WSP + r"(?:등록|신청)" + _WSP + r"마감" + _WSP + r"일|입찰" + _WSP + r"일|개찰" + _WSP
    + r"일|계약" + _WSP + r"체결" + _WSP + r"일)" + r"[^.]{0,40}?까지"
)
_WINNER_CONTRACT_RE = re.compile(r"낙찰자" + _WSP + r"(?:는|의\s*경우)?" + _WSP + r"계약" + _WSP + r"체결" + _WSP + r"일")


def _basis(s: str) -> str:
    if "법인등기" in s and ("본점" in s or "본사" in s):
        return "법인등기부상 본점 소재지"
    if re.search(r"주된" + _WSP + r"영업소", s):
        return "주된 영업소 소재지"
    if "본점" in s or "본사" in s:
        return "본점(본사) 소재지"
    if "사업장" in s:
        return "사업장 소재지"
    if "관내" in s:
        return "관내 업체"
    return "업체 소재지"


def _period(s: str) -> str | None:
    start = _START_RE.search(s)
    if not start:
        return None
    label = ("입찰공고일" if start.group(1) else "공고일") + (f" {start.group(2)}" if start.group(2) else "")
    end = _END_RE.search(s, start.end())
    if not end:
        return label + ("" if start.group(2) in ("현재", "기준") else " 기준")
    end_label = re.sub(r"\s+", "", end.group(1))
    end_label = {"입찰참가등록마감일": "입찰참가등록 마감일", "입찰참가신청마감일": "입찰참가신청 마감일",
                 "계약체결일": "계약체결일"}.get(end_label, end_label)
    winner = " (낙찰자: 계약체결일)" if end_label != "계약체결일" and _WINNER_CONTRACT_RE.search(s) else ""
    return f"{label} ~ {end_label}{winner}"


def summarize(evidence: str | None) -> dict | None:
    if not evidence:
        return None
    return {"basis": _basis(evidence), "period": _period(evidence), "individual": "개인사업자" in evidence}
