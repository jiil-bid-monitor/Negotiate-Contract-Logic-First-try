"""업역 스크리닝 + 일정 계산.

키워드·제외키워드·최소예산·코드 매칭 규칙은 **기존 시스템과 동일하게 유지한다**
(`matching/matchEngine.ts`, `keywordMatcher.ts`, `codeMatcher.ts`).

  - 제외키워드가 제목에 하나라도 있으면 무조건 제외
  - 예산이 확인되는 공고 중 minBudgetAmount 미만이면 제외 (예산 미상은 통과)
  - 세부품명번호(물품)는 정확일치 → 단독으로도 인정
  - 업종코드는 '투찰가능업종명' 텍스트 부분일치라 정밀도가 낮음 → 제목 키워드가
    함께 있을 때만 인정 (단독 매칭은 결과에서 제외)
  - 코드+키워드 둘 다 → '강력추천', 하나만 → '참고용'
  - 키워드 비교 시 공백은 무시 ("운영 용역" == "운영용역")

**해외 전시회 한국관/단체관 판별** — 기존 시스템에는 없던 신규 규칙(2026-09
결정). "한국관"이라는 단어만 보고 국내 공고로 오인하기 쉽다(실측:
R26BK01717819 UAE 두바이 의료기기전시회, R26BK01714892 두바이 — 둘 다
해외 개최인데 "한국관" 제목 때문에 국내로 오인됨). "한국관"은 그 자체로
해외 국가관 부스를 뜻하므로 제목에 있으면 단독으로도 자동 제외한다.
"단체관"은 국내 행사에도 쓰이는 말이라 "전시회/박람회/엑스포"가 같이
있을 때만 신호로 인정한다. 단, 회사가 실제로 확장 중인 몽골만 예외 —
자동 제외 대신 플래그(🌐)만 남겨 담당자가 직접 확인하게 한다(이땐
통상적인 키워드/코드 불일치 제외도 건너뛴다 — 플래그가 붙는데 조용히
빠지면 안 되므로).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from datetime import datetime

from .models import Notice


def _squash(text: str | None) -> str:
    return re.sub(r"\s+", "", text or "")


@dataclass
class ScreenConfig:
    keywords: list[str] = field(default_factory=list)
    exclude_keywords: list[str] = field(default_factory=list)
    # 무조건 제외 — 관심 키워드가 함께 있어도 "검토 필요"로 올리지 않고 바로 뺀다(2026-10-01 사용자 결정, 특별전·순회전)
    hard_exclude_keywords: list[str] = field(default_factory=list)
    # 제외 키워드의 예외 — {"건축": ["실내건축"]}이면 공고명의 "실내건축"은 "건축"으로 안 본다(2026-10-13 대리님 답변:
    # "건축"은 일반 건축공사를 거르려던 것이고 실내건축 공고까지 빼려던 건 아님). 다른 곳에 "건축"이 또 있으면 그대로 제외.
    exclude_exceptions: dict[str, list[str]] = field(default_factory=dict)
    min_budget_amount: float | None = None
    product_codes: list[dict[str, str]] = field(default_factory=list)
    industry_codes: list[dict[str, str]] = field(default_factory=list)
    # 제외 키워드와 관심 키워드가 공고명에 함께 있으면 빼지 않고 "검토 필요"로 남긴다(2026-09-30 사용자 결정 —
    # 담당자 판단을 쌓아 제외 키워드를 고치려고). 웹앱만 켠다 — CLI·자동 발송은 예전처럼 제외.
    review_conflicts: bool = False


@dataclass
class ScreenResult:
    matched: bool
    confidence: str | None  # 강력추천 / 참고용
    matched_keywords: list[str] = field(default_factory=list)
    matched_product_codes: list[str] = field(default_factory=list)
    matched_industry_codes: list[str] = field(default_factory=list)
    excluded_by: str | None = None
    excluded_reason: str | None = None
    # 해외 전시회 한국관/단체관인데 개최지가 몽골이라 자동 제외 대신 통과시킨 경우.
    overseas_flag: bool = False
    # 위 플래그를 왜 붙였는지 — 리포트에서 배지에 마우스를 올리면 보여줄 근거.
    overseas_evidence: str | None = None
    # "미매칭"으로 빠졌을 때 무엇을 무엇과 비교해서 안 맞았는지 (사람이 읽는 줄 목록).
    # 판정에는 쓰지 않는다 — 제외된 공고 화면에서 "왜 빠졌지?"를 바로 보려고 남긴다.
    match_explain: list[str] = field(default_factory=list)
    # "검토 필요" — 관심 키워드가 있는데 제외 키워드(이 값)도 있어서 사람이 판단할 공고
    review_exclude: str | None = None
    # 미매칭 근거를 문장 대신 항목으로(2026-10-13 요청: 문장이 길어 읽기 어려움) — 화면이 짧은 표로 그린다.
    # 키워드 목록 자체는 공고마다 같아서 넣지 않는다(웹앱은 결과에 한 번만 담음)
    match_detail: dict = field(default_factory=dict)


def _match_exclude(
    notice: Notice, exclude_keywords: list[str], exceptions: dict[str, list[str]] | None = None
) -> str | None:
    title = _squash(notice.title)
    for word in exclude_keywords:
        haystack = title
        for allowed in (exceptions or {}).get(word, []):
            if _squash(allowed):
                haystack = haystack.replace(_squash(allowed), " ")
        if _squash(word) and _squash(word) in haystack:
            return word
    return None


def _match_keywords(notice: Notice, keywords: list[str]) -> list[str]:
    haystack = _squash(notice.title) + _squash(notice.product_class_name)
    return [w for w in keywords if _squash(w) and _squash(w) in haystack]


_OVERSEAS_EVENT_RE = re.compile(r"전시회|박람회|엑스포")
# "한국관"은 그 단어만으로도 해외 국가관 부스라는 뜻이라 단독으로도 신호로 본다.
# "단체관"은 국내 행사에도 쓰이는 좀 더 일반적인 말이라, 오탐을 줄이기 위해
# 전시회/박람회/엑스포 키워드가 같이 있을 때만 신호로 인정한다.
_KOREA_PAVILION_STRONG_RE = re.compile(r"한국관")
_GROUP_PAVILION_RE = re.compile(r"단체관")
_MONGOLIA_RE = re.compile(r"몽골")


def _overseas_exhibition_evidence(notice: Notice) -> str | None:
    """해외 전시회·박람회·엑스포에 한국 기업이 참가할 때 짓는 한국관/단체관
    부스 설치 공고인지 제목으로 판별하고, 맞으면 사람이 알아볼 근거 문구를
    돌려준다(리포트 배지의 마우스오버 툴팁용). 아니면 None."""
    haystack = _squash(notice.title) + _squash(notice.product_class_name)

    pavilion = _KOREA_PAVILION_STRONG_RE.search(haystack)
    if pavilion:
        return f"제목에 '{pavilion.group(0)}' 포함"

    event = _OVERSEAS_EVENT_RE.search(haystack)
    group_pavilion = _GROUP_PAVILION_RE.search(haystack)
    if event and group_pavilion:
        return f"제목에 '{event.group(0)}'와 '{group_pavilion.group(0)}' 포함"

    return None


def _is_mongolia(notice: Notice) -> bool:
    haystack = _squash(notice.title) + _squash(notice.product_class_name)
    return bool(_MONGOLIA_RE.search(haystack))


def _match_codes(notice: Notice, config: ScreenConfig) -> tuple[list[str], list[str]]:
    product_hits = [
        entry["name"]
        for entry in config.product_codes
        if notice.product_class_no and entry.get("code") == notice.product_class_no
    ]
    industry_text = _squash(notice.industry_text)
    industry_hits = [
        entry["name"]
        for entry in config.industry_codes
        if entry.get("name") and _squash(entry["name"]) in industry_text
    ]
    return product_hits, sorted(set(industry_hits))


def _explain_no_match(notice: Notice, config: ScreenConfig, industry_hits: list[str]) -> list[str]:
    """미매칭 판정의 근거 — 공고의 어느 값을 우리 목록의 무엇과 비교했는지 그대로 적는다."""
    lines = []
    target = f"공고명 「{notice.title}」"
    if notice.product_class_name:
        target += f" + 대표 세부품명 「{notice.product_class_name}」"
    shown = ", ".join(config.keywords[:20]) + (" 외" if len(config.keywords) > 20 else "")
    lines.append(f"키워드: {target}에 관심 키워드 {len(config.keywords)}개({shown}) 중 들어 있는 것이 없음")
    if notice.product_class_no:
        name = f" {notice.product_class_name}" if notice.product_class_name else ""
        lines.append(
            f"품명코드: 공고 세부품명번호 {notice.product_class_no}{name} — 관심 품명코드 {len(config.product_codes)}개 목록에 없음"
        )
    else:
        lines.append("품명코드: 공고에 세부품명번호가 없음 (용역·공사 공고는 대부분 없음) — 품명코드로는 비교 불가")
    if industry_hits:
        lines.append(
            f"업종: {', '.join(industry_hits)}는 일치하지만, 업종만 맞는 공고는 후보로 보지 않음 (키워드나 품명코드가 함께 맞아야 함)"
        )
    elif notice.industry_text:
        lines.append(f"업종: 공고 참가가능 업종 「{notice.industry_text[:80]}」 — 관심 업종과 겹치지 않음")
    return lines


def _no_match_detail(notice: Notice, config: ScreenConfig, industry_hits: list[str]) -> dict:
    """`_explain_no_match`와 같은 내용을 항목으로 — 비교한 대표 세부품명, 세부품명번호, 업종."""
    return {
        "product_class_name": notice.product_class_name or None,
        "product_class_no": notice.product_class_no or None,
        "keyword_count": len(config.keywords),
        "product_code_count": len(config.product_codes),
        "industry_hits": list(industry_hits),
        "industry_text": (notice.industry_text or "")[:80] or None,
    }


def screen(notice: Notice, config: ScreenConfig) -> ScreenResult:
    hard_word = _match_exclude(notice, config.hard_exclude_keywords, config.exclude_exceptions)
    if hard_word:
        return ScreenResult(matched=False, confidence=None, excluded_by="제외키워드", excluded_reason=hard_word)
    excluded_word = _match_exclude(notice, config.exclude_keywords, config.exclude_exceptions)
    if excluded_word and config.review_conflicts and _match_keywords(notice, config.keywords):
        # 관심 키워드도 있다 — 제외하지 않고 아래 예산·해외 조건만 거친 뒤 "검토 필요"로 둔다.
        # (품명코드 일치만으로 올리는 건 2026-10-01 넣었다가 사용자 결정으로 뺌 — 관심 키워드가 있을 때만.)
        result = screen(notice, replace(config, exclude_keywords=[], review_conflicts=False))
        if result.matched:
            result.confidence = "검토필요"
            result.review_exclude = excluded_word
        return result
    if excluded_word:
        return ScreenResult(
            matched=False,
            confidence=None,
            excluded_by="제외키워드",
            excluded_reason=excluded_word,
        )

    overseas_evidence = _overseas_exhibition_evidence(notice)
    overseas_booth = overseas_evidence is not None
    mongolia = overseas_booth and _is_mongolia(notice)
    if overseas_booth and not mongolia:
        return ScreenResult(
            matched=False,
            confidence=None,
            excluded_by="해외개최",
            excluded_reason=f"해외 전시회·박람회·엑스포 한국관/단체관 — 국내 공고 아님 ({overseas_evidence})",
        )

    budget = notice.budget
    if config.min_budget_amount is not None and budget is not None and budget < config.min_budget_amount:
        return ScreenResult(
            matched=False,
            confidence=None,
            excluded_by="최소예산",
            excluded_reason=f"{budget:,.0f}원 < {config.min_budget_amount:,.0f}원",
        )

    product_hits, industry_hits = _match_codes(notice, config)
    keyword_hits = _match_keywords(notice, config.keywords)

    # 업종코드 단독 매칭은 결과에 포함하지 않는다 (기존 정책). 단, 몽골 해외관은
    # 키워드·코드가 하나도 안 맞아도 조용히 빼지 않는다 — 플래그를 달아 사람이
    # 보게 하는 게 목적이라 "미매칭"으로 걸러지면 그 목적 자체가 무산된다.
    if not product_hits and not keyword_hits and not mongolia:
        return ScreenResult(
            matched=False,
            confidence=None,
            excluded_by="미매칭",
            excluded_reason="키워드·품명코드 모두 불일치",
            match_explain=_explain_no_match(notice, config, industry_hits),
            match_detail=_no_match_detail(notice, config, industry_hits),
        )

    code_matched = bool(product_hits or industry_hits)
    return ScreenResult(
        matched=True,
        confidence="강력추천" if code_matched and keyword_hits else "참고용",
        matched_keywords=keyword_hits,
        matched_product_codes=product_hits,
        matched_industry_codes=industry_hits,
        overseas_flag=mongolia,
        overseas_evidence=(
            f"{overseas_evidence} · '몽골' 감지 — 확장 중인 시장이라 자동 배제 대신 표시" if mongolia else None
        ),
    )


# ── 일정 ────────────────────────────────────────────────────

_DATE_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y%m%d%H%M", "%Y%m%d")


def parse_datetime(text: str | None) -> datetime | None:
    if not text:
        return None
    cleaned = str(text).strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(cleaned, fmt)
        except ValueError:
            continue
    return None


@dataclass
class Schedule:
    """협상계약에는 서로 다른 마감이 여럿이다. 가장 이른 것이 실질 마감이다."""

    qualification_deadline: datetime | None
    joint_agreement_deadline: datetime | None
    bid_deadline: datetime | None
    # API 세 필드가 전부 비었을 때만 쓰는 최후 수단 — 첨부파일 원문에서 뽑은
    # 제출기한(`attachments.save_attachment_texts` → `schedule_text.extract_deadline`).
    # API 값이 하나라도 있으면 이 필드는 아예 안 쓰인다.
    attachment_deadline: datetime | None = None
    # 첨부에서 찾은 마감의 종류 — "첨부파일 제출기한" 또는 "응모신청 등록 마감"(2026-10-02)
    attachment_deadline_kind: str = "첨부파일 제출기한"
    # 사전규격의 의견등록 마감. 본공고에는 없다(항상 None) — 입찰 마감이 없는 사전규격에서만 쓰인다.
    opinion_deadline: datetime | None = None
    # 팝업에 같이 보여줄 일시(카드 마감 계산엔 안 씀) — 공고 게시·개찰, 첨부 공고문에서 찾은 참가등록·제출 마감 전부(2026-10-02 요청)
    posted_at: datetime | None = None
    opening_at: datetime | None = None
    attachment_deadlines: list = field(default_factory=list)

    @property
    def earliest(self) -> tuple[str, datetime] | None:
        """카드 마감(D-N 기준). 이름은 예전 그대로지만 "가장 이른 것"이 아니라 **우선순위**로 고른다(2026-10-02 사용자 결정):
        ① 입찰참가자격등록 마감 ② 입찰 마감 ③ 사전규격 의견등록 마감 ④ 첨부 공고문의 참가등록·제출 마감
        ⑤ 공동수급협정 마감(공동수급으로 참가할 때만 해당해서 맨 뒤). 나머지 마감은 팝업(`all_deadlines`)에 날짜순으로 보인다."""
        for label, dt in (
            ("자격등록 마감", self.qualification_deadline),
            ("입찰 마감", self.bid_deadline),
            ("의견등록 마감", self.opinion_deadline),
            (self.attachment_deadline_kind, self.attachment_deadline),
            ("공동수급협정 마감", self.joint_agreement_deadline),
        ):
            if dt is not None:
                return (label, dt)
        return None

    @property
    def needs_attachment_deadline(self) -> bool:
        """자격등록·입찰·의견등록 마감이 모두 빈 공고 — 첨부 공고문에서 참가등록·제출 마감을 찾는다
        (공동수급협정 마감만 있는 공고 포함: 2026-10-02 고흥분청문화박물관)."""
        return self.qualification_deadline is None and self.bid_deadline is None and self.opinion_deadline is None

    @property
    def joint_only(self) -> bool:
        """나라장터 마감 필드 중 공동수급협정 마감만 있고 자격등록·입찰 마감은 빈 공고."""
        return (self.joint_agreement_deadline is not None and self.qualification_deadline is None
                and self.bid_deadline is None)

    @property
    def all_deadlines(self) -> list[tuple[str, datetime]]:
        """팝업용 일시 전부를 날짜순으로(2026-10-02 요청: 카드 마감 말고 나머지 일시도 팝업에서) — 공고 게시, 자격등록·공동수급협정·
        입찰·의견등록 마감, 개찰, 첨부 공고문의 참가등록·제출 마감. 카드 마감(`earliest`)과 같은 (이름, 일시)가 그대로 들어 있어
        화면이 그 줄을 굵게 표시한다."""
        items = [
            ("공고 게시", self.posted_at),
            ("자격등록 마감", self.qualification_deadline),
            ("공동수급협정 마감", self.joint_agreement_deadline),
            ("입찰 마감", self.bid_deadline),
            ("의견등록 마감", self.opinion_deadline),
            ("개찰", self.opening_at),
        ]
        found = [(label, dt) for label, dt in items if dt is not None]
        extra = list(self.attachment_deadlines)
        if self.attachment_deadline is not None and (self.attachment_deadline_kind, self.attachment_deadline) not in extra:
            extra.append((self.attachment_deadline_kind, self.attachment_deadline))
        found.extend(pair for pair in extra if pair not in found)
        return sorted(found, key=lambda pair: pair[1])

    def days_left(self, now: datetime) -> int | None:
        earliest = self.earliest
        if earliest is None:
            return None
        return (earliest[1] - now).days


def build_schedule(notice: Notice) -> Schedule:
    return Schedule(
        qualification_deadline=parse_datetime(notice.qualification_deadline),
        joint_agreement_deadline=parse_datetime(notice.joint_agreement_deadline),
        bid_deadline=parse_datetime(notice.bid_deadline),
        opinion_deadline=parse_datetime(notice.opinion_deadline),
        posted_at=parse_datetime(getattr(notice, "posted_at", None)),
        opening_at=parse_datetime(getattr(notice, "opening_at", None)),
    )
