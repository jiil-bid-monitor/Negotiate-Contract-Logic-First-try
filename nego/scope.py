"""수집 범위(공고 유형) 판별.

2026-09-28부터 협상에 의한 계약만이 아니라 **수의계약을 뺀 경쟁입찰 전부**를 수집한다.
공고마다 세 유형 중 하나로 분류한다 — 발송 주기가 유형마다 다르기 때문이다
(입찰은 마감이 7일 안쪽인 경우가 있어 매일, 나머지는 주 1회).

  - 협상            : 낙찰자결정방법 `sucsfbidMthdNm`이 "협상에의한계약"으로 시작
  - 규격가격동시입찰 : 낙찰자결정방법이 "규격가격동시입찰"로 시작
  - 입찰            : 그 밖의 경쟁입찰 전부 (적격심사, 최저가, 설계공모 등)
  - 범위 밖         : 계약체결방법 `cntrctCnclsMthdNm`(또는 낙찰자결정방법)에 "수의"가 든 공고

협상 여부는 **계약체결방법이 아니라 낙찰자결정방법**으로 판별한다
(계약체결방법 = 일반경쟁 / 제한경쟁 / … 는 협상 여부와 직교).
접미 괄호가 붙은 변형이 여러 개라 완전일치가 아닌 **접두어 매칭**을 쓴다.
  협상에의한계약-협상에 의한 낙찰자 결정
  협상에의한계약-협상에 의한 낙찰자 결정(SW사업)
  협상에의한계약-협상에 의한 낙찰자 결정(건설엔지니어링)
  협상에의한계약-협상에 의한 낙찰자 결정(엔지니어링)
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .models import Notice

NEGOTIATED_PREFIX = "협상에의한계약"
SPEC_PRICE_PREFIX = "규격가격동시입찰"
PRIVATE_CONTRACT_MARK = "수의"
# 설계공모(일반·제안·간이) — 작품 공모로 설계자만 뽑는 발주라 수집하지 않는다(2026-10-16 통합 결정 5번, 팀원 수집기 방식).
# 공고명에 '설계공모'가 없고 낙찰방법에만 적힌 공고가 있다(신안갯벌박물관 리모델링 기본 및 실시설계 용역).
DESIGN_CONTEST_MARK = "설계공모"

CATEGORY_NEGOTIATED = "협상"
CATEGORY_SPEC_PRICE = "규격가격동시입찰"
CATEGORY_BID = "입찰"
CATEGORIES = (CATEGORY_NEGOTIATED, CATEGORY_SPEC_PRICE, CATEGORY_BID)
# 사전규격은 낙찰방법이 정해지기 전 단계라 위 세 유형으로 나눌 수 없다 — 따로 분류한다.
# `--categories`(발송 주기용 유형 선택)와는 무관하게, 사전규격을 수집한 실행이면 항상 남는다.
CATEGORY_PRESPEC = "사전규격"

# 협상은 아니지만 제안서로 겨루는 방식이라 업역이 겹친다.
# 제외하지 않고 태그만 달아 둔다 — 나중에 판단할 수 있게.
ADJACENT_PREFIXES = ("설계공모", "규격가격동시입찰")

CANCELLED_KIND = "취소공고"


def _normalize(text: str | None) -> str:
    return re.sub(r"\s+", "", text or "")


def is_negotiated(notice: Notice) -> bool:
    return _normalize(notice.award_method).startswith(NEGOTIATED_PREFIX)


def is_adjacent(notice: Notice) -> bool:
    normalized = _normalize(notice.award_method)
    return any(normalized.startswith(p) for p in ADJACENT_PREFIXES)


def is_private_contract(notice: Notice) -> bool:
    """수의계약(소액수의 등 포함). 계약체결방법에 "수의"가 들어가면 범위 밖이다.
    낙찰자결정방법 쪽에 수의가 적힌 경우도 있을 수 있어 함께 본다."""
    return PRIVATE_CONTRACT_MARK in _normalize(notice.contract_method) or _normalize(
        notice.award_method
    ).startswith(PRIVATE_CONTRACT_MARK)


def is_design_contest(notice: Notice) -> bool:
    """낙찰방법·계약방법·입찰방식 칸 어디든 '설계공모'가 있으면 설계공모 발주다(가운뎃점·공백 무시)."""
    texts = (notice.award_method, notice.contract_method, notice.bid_method)
    return any(DESIGN_CONTEST_MARK in re.sub(r"[\s·ㆍ.,]", "", t or "") for t in texts)


def bid_category(notice: Notice) -> str | None:
    """공고 유형. 수의계약·설계공모면 None(수집 범위 밖). 순서: 수의 → 설계공모 → 협상 → 규격가격동시 → 입찰."""
    if notice.kind == CATEGORY_PRESPEC:
        return CATEGORY_PRESPEC
    if is_private_contract(notice) or is_design_contest(notice):
        return None
    award = _normalize(notice.award_method)
    if award.startswith(NEGOTIATED_PREFIX):
        return CATEGORY_NEGOTIATED
    if award.startswith(SPEC_PRICE_PREFIX):
        return CATEGORY_SPEC_PRICE
    return CATEGORY_BID


def parse_categories(text: str | None) -> set[str] | None:
    """"입찰,협상" 같은 쉼표 목록 → 유형 집합. 비어 있으면 None(전 유형)."""
    if not text or not text.strip():
        return None
    chosen = {part.strip() for part in text.split(",") if part.strip()}
    unknown = chosen - set(CATEGORIES)
    if unknown:
        raise ValueError(f"알 수 없는 공고 유형: {', '.join(sorted(unknown))} (가능: {', '.join(CATEGORIES)})")
    return chosen


def is_cancelled(notice: Notice) -> bool:
    return (notice.notice_kind or "").strip() == CANCELLED_KIND


def is_re_notice(notice: Notice) -> bool:
    """재공고 = 직전 회차 유찰. 경쟁이 적다는 신호라 별도로 표시한다."""
    return (notice.re_notice_yn or "").strip().upper() == "Y"


def negotiation_variant(notice: Notice) -> str | None:
    """'(SW사업)' 같은 접미 변형만 뽑아낸다. 없으면 '일반'."""
    if not is_negotiated(notice):
        return None
    match = re.search(r"\(([^)]+)\)\s*$", notice.award_method or "")
    return match.group(1) if match else "일반"


def latest_ordinals(notices: list[Notice]) -> list[Notice]:
    """같은 공고번호는 최신 차수만 남긴다.

    차수는 '000', '001' 같은 문자열이라 정수로 비교한다.
    변경공고로 덮이더라도 변경사유(change_reason)는 최신 차수 쪽에 남아 있다.
    """
    best: dict[tuple[str, str], Notice] = {}
    for notice in notices:
        key = (notice.work_type, notice.notice_no)
        current = best.get(key)
        if current is None or _ord_value(notice.notice_ord) > _ord_value(current.notice_ord):
            best[key] = notice
    return list(best.values())


def _ord_value(text: str | None) -> int:
    try:
        return int(str(text).strip())
    except (TypeError, ValueError):
        return -1


@dataclass
class ScopeResult:
    kept: list[Notice]
    adjacent: list[Notice]
    dropped_cancelled: int
    dropped_private: int
    dropped_old_ordinal: int
    # 이번 실행에서 고른 유형(`categories`) 밖이라 뺀 공고 수 (예: 매일 실행에서 협상 공고)
    dropped_other_category: int = 0
    # 설계공모라 뺀 공고 수 (dropped_private와 따로 센다)
    dropped_design_contest: int = 0


def apply_scope(notices: list[Notice], categories: set[str] | None = None) -> ScopeResult:
    """수집된 전체 공고에서 수집 범위(수의계약 제외 경쟁입찰)만 남긴다.
    `categories`를 주면 그 유형만 남긴다 (None이면 전 유형).

    **순서가 중요하다. 최신 차수를 먼저 고르고, 그 다음에 취소 여부를 본다.**
    취소를 먼저 걷어내면 "000 등록공고 → 001 취소공고"인 사업에서 001만 사라지고
    000이 살아남아, 취소된 사업이 검토 목록에 그대로 올라온다.
    반대로 "001 취소공고 → 002 재공고"처럼 되살아난 건은 최신 차수가 002라 정상 유지된다.
    """
    before = len(notices)

    latest = latest_ordinals(notices)
    dropped_old_ordinal = before - len(latest)

    alive = [n for n in latest if not is_cancelled(n)]
    dropped_cancelled = len(latest) - len(alive)

    competitive = [n for n in alive if bid_category(n) is not None]
    # 설계공모는 당선자와 수의계약을 맺어 계약방법 칸이 "수의계약"인 경우가 많다 — 수의계약보다 먼저 센다
    # (10/19 실수집 로그 "설계공모 0건"). 빠지는 공고는 같고 건수 나누기만 바뀐다.
    dropped_design_contest = sum(1 for n in alive if n.kind != CATEGORY_PRESPEC and is_design_contest(n))
    dropped_private = len(alive) - len(competitive) - dropped_design_contest

    kept = competitive
    if categories is not None:
        kept = [n for n in competitive if bid_category(n) in categories or n.kind == CATEGORY_PRESPEC]
    adjacent = [n for n in kept if is_adjacent(n)]

    return ScopeResult(
        kept=kept,
        adjacent=adjacent,
        dropped_cancelled=dropped_cancelled,
        dropped_private=dropped_private,
        dropped_old_ordinal=dropped_old_ordinal,
        dropped_other_category=len(competitive) - len(kept),
        dropped_design_contest=dropped_design_contest,
    )


# ── 사업 단위 그룹핑 ──────────────────────────────────────────
# untyNtceNo(통합공고번호)로 재공고가 묶일 거라 기대했지만, 실측 결과 공고번호와 1:1이라
# 묶이지 않는다. 그래서 발주기관 + 금액 + 제목 유사도로 묶는다.

_TITLE_NOISE = re.compile(r"\[[^\]]*\]|\([^)]*\)|긴급|재공고|변경|제\d+차|용역|공고")


def title_signature(title: str) -> str:
    """제목에서 부가 표기를 걷어내고 비교용 문자열을 만든다."""
    cleaned = _TITLE_NOISE.sub(" ", title or "")
    return re.sub(r"[^0-9A-Za-z가-힣]+", "", cleaned)


def _similarity(a: str, b: str) -> float:
    from difflib import SequenceMatcher

    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def group_projects(notices: list[Notice], threshold: float = 0.75) -> list[list[Notice]]:
    """같은 사업으로 보이는 공고를 묶는다 (재공고는 공고번호가 달라 차수로는 안 묶인다)."""
    groups: list[list[Notice]] = []

    for notice in sorted(notices, key=lambda n: (n.notice_institution or "", n.title)):
        signature = title_signature(notice.title)
        placed = False
        for group in groups:
            head = group[0]
            same_institution = (head.notice_institution or "") == (notice.notice_institution or "")
            same_budget = head.budget == notice.budget
            if same_institution and (same_budget or _similarity(signature, title_signature(head.title)) >= threshold):
                group.append(notice)
                placed = True
                break
        if not placed:
            groups.append([notice])

    return groups
