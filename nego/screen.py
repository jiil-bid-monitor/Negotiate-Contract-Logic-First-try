"""업역 스크리닝 + 일정 계산.

키워드·제외키워드·코드 매칭 규칙은 **기존 시스템과 동일하게 유지한다**
(`matching/matchEngine.ts`, `keywordMatcher.ts`, `codeMatcher.ts`).

  - 제외키워드가 제목에 하나라도 있으면 무조건 제외
  - 예산(최소 금액)으로는 거르지 않음 (2026-10-27 회의 결정 — 예전엔 1억 미만 제외)
  - 세부품명번호(물품)는 정확일치 → 단독으로도 인정
  - 업종코드는 '투찰가능업종명' 텍스트 부분일치라 정밀도가 낮음 → 제목 키워드가
    함께 있을 때만 인정 (단독 매칭은 결과에서 제외)
  - 코드+키워드 둘 다 → '강력추천', 하나만 → '참고용'
  - 키워드 비교 시 공백은 무시 ("운영 용역" == "운영용역")

**해외 개최 판별** (2026-10-19 통합 결정 8번 — 팀원 수집기 규칙을 그대로 옮김, 목록은 `config/overseas.json`).
공고명에 아래 중 하나가 있으면 국내 공고가 아니라 해외 전시회 국가관·부스 공고로 보고 뺀다.
  ① '한국관'(통합한국관 포함 — 한국관광·한국관리 등 다른 낱말 제외) + 해외 행사 표시(연도·전시회/박람회/엑스포/페어·
     영문 국가 약자(KOR 포함)·해외 지명). 팀원 규칙은 '한국관'만으로 뺐는데 국내 '옥동 한국관~송현동' 교통 개선공사가 같이 빠져서
     (2026-10-20 사용자 결정) 표시가 없는 '한국관'은 빼지 않고 "해외 개최 의심" 표시만 한다.
  ② 전시회·박람회·엑스포 + 단체관  ③ 해외 지명 + 단체관  ④ 영문 국가 약자(USA·UAE 등, 대문자 단어)
단체관람·단체관광은 단체관이 아니다. 몽골(도시명 포함)이면 빼지 않고 플래그(🌐)만 남긴다(키워드 불일치 제외도 건너뜀).
위에 안 걸려도 공고명에 해외 지명이 있으면 "해외 개최 의심" 표시만 붙인다(`overseas_suspect`).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, replace
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from .models import Notice


def _squash(text: str | None) -> str:
    return re.sub(r"\s+", "", text or "")


# "공사 설계 용역"의 변형 — 공사 기본설계 용역, 공사 실시설계 용역, 공사 기본 및 실시설계 용역(기본·실시설계)
# (2026-10-16 통합 결정 3번). 띄어쓰기를 지운 공고명에서 "공사설계용역"으로 바꿔 놓으면 관심 키워드가 맞는다.
_DESIGN_SERVICE_VARIANT_RE = re.compile(r"공사(?:기본및실시|기본[·ㆍ.,]?실시|기본|실시)설계용역")


def _canonical_title(text: str | None) -> str:
    return _DESIGN_SERVICE_VARIANT_RE.sub("공사설계용역", _squash(text))


@dataclass
class ScreenConfig:
    keywords: list[str] = field(default_factory=list)
    exclude_keywords: list[str] = field(default_factory=list)
    # 무조건 제외 — 관심 키워드가 함께 있어도 "검토 필요"로 올리지 않고 바로 뺀다(2026-10-01 사용자 결정, 특별전·순회전)
    hard_exclude_keywords: list[str] = field(default_factory=list)
    # 제외 키워드의 예외 — {"건축": ["실내건축"]}이면 공고명의 "실내건축"은 "건축"으로 안 본다(2026-10-13 대리님 답변:
    # "건축"은 일반 건축공사를 거르려던 것이고 실내건축 공고까지 빼려던 건 아님). 다른 곳에 "건축"이 또 있으면 그대로 제외.
    exclude_exceptions: dict[str, list[str]] = field(default_factory=dict)
    # 관심 키워드를 무시할 조건 — {"공사 설계 용역": ["수리", "증축", …]}이면 공고명에 그 낱말이 있을 때 그 관심 키워드는 안 맞은 것으로 본다
    # (2026-10-14 사용자 요청: 공사 설계 용역은 새로 짓는 "신설"만 — 수리·증축 같은 부수적 설계 용역은 뺌).
    keyword_skip_when: dict[str, list[str]] = field(default_factory=dict)
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
    # 해외 개최인데 개최지가 몽골이라 자동 제외 대신 통과시킨 경우.
    overseas_flag: bool = False
    # 제외 규칙엔 안 걸렸지만 공고명에 해외 지명이 있다 — "🌐 해외 개최 의심" 표시만(2026-10-16 통합 결정 8번)
    overseas_suspect: str | None = None
    # 위 플래그를 왜 붙였는지 — 리포트에서 배지에 마우스를 올리면 보여줄 근거.
    overseas_evidence: str | None = None
    # 공고명에 몽골·몽골 지명이 있다 — "몽골 공고 유력" 표시 근거(2026-10-19 사용자 요청, 해외 의심 대신)
    mongolia: str | None = None
    # "미매칭"으로 빠졌을 때 무엇을 무엇과 비교해서 안 맞았는지 (사람이 읽는 줄 목록).
    # 판정에는 쓰지 않는다 — 제외된 공고 화면에서 "왜 빠졌지?"를 바로 보려고 남긴다.
    match_explain: list[str] = field(default_factory=list)
    # "검토 필요" — 관심 키워드가 있는데 제외 키워드(이 값)도 있어서 사람이 판단할 공고
    review_exclude: str | None = None
    # 미매칭 근거를 문장 대신 항목으로(2026-10-13 요청: 문장이 길어 읽기 어려움) — 화면이 짧은 표로 그린다.
    # 키워드 목록 자체는 공고마다 같아서 넣지 않는다(웹앱은 결과에 한 번만 담음)
    match_detail: dict = field(default_factory=dict)

    @property
    def section(self) -> str | None:
        """보고·화면 구역 — 관심 키워드가 맞으면 "관심", 키워드 없이 보유 세부품명만 맞으면 "세부품명"
        (2026-10-16 통합 결정 10·12번). 매칭 안 된 공고는 None."""
        if not self.matched:
            return None
        return "관심" if self.matched_keywords or not self.matched_product_codes else "세부품명"


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


def _skipped(word: str, notice: Notice, skip_when: dict[str, list[str]] | None) -> bool:
    title = _canonical_title(notice.title)
    return any(_squash(x) and _squash(x) in title for x in (skip_when or {}).get(word, []))


def _match_keywords(notice: Notice, keywords: list[str], skip_when: dict[str, list[str]] | None = None) -> list[str]:
    haystack = _canonical_title(notice.title) + _squash(notice.product_class_name)
    return [w for w in keywords if _squash(w) and _squash(w) in haystack and not _skipped(w, notice, skip_when)]


OVERSEAS_CONFIG = Path(__file__).resolve().parent.parent / "config" / "overseas.json"

# 한국관 뒤에 이 글자가 붙으면 다른 낱말이다 (한국관광·한국관리·한국관세·한국관계·한국관련 …)
_KOREA_PAVILION_RE = re.compile(r"한국관(?!광|리|세|계|련|측|심|람|찰|절|악|제|통|행|개|저)")
# 단체관람·단체관광·단체관리는 단체관이 아니다 — "박람회 단체관람 버스 임차"가 빠지지 않게
_GROUP = r"단체관(?!람|광|리|계|련|심)"
_EXPO_GROUP_RE = re.compile(rf"(전시회|박람회|엑스포).*{_GROUP}|{_GROUP}.*(전시회|박람회|엑스포)")
_GROUP_RE = re.compile(_GROUP)


@dataclass(frozen=True)
class OverseasRules:
    mongolia: tuple[str, ...] = ()
    places: tuple[str, ...] = ()
    flag_skip_places: tuple[str, ...] = ()
    abbreviations: tuple[str, ...] = ()


@lru_cache(maxsize=4)
def load_overseas_rules(path: Path = OVERSEAS_CONFIG) -> OverseasRules:
    if not path.exists():
        return OverseasRules(mongolia=("몽골",))
    data = json.loads(path.read_text(encoding="utf-8"))
    return OverseasRules(
        mongolia=tuple(data.get("mongoliaKeywords", [])),
        places=tuple(data.get("overseasPlaces", [])),
        flag_skip_places=tuple(data.get("flagSkipPlaces", [])),
        abbreviations=tuple(data.get("countryAbbreviations", [])),
    )


def _word_re(word: str, ignore_case: bool) -> re.Pattern:
    """영문은 앞뒤에 영문자가 붙지 않은 단어로만 — 'ASIA'가 'ASIAN'에, 'US'가 'BUSAN'에 걸리지 않게."""
    return re.compile(rf"(?<![A-Za-z]){re.escape(word)}(?![A-Za-z])", re.IGNORECASE if ignore_case else 0)


def _find_place(title: str, places: tuple[str, ...]) -> str | None:
    for place in places:
        if re.search(r"[A-Za-z]", place):
            if _word_re(place, True).search(title):
                return place
        elif place in title:
            return place
    return None


def _find_abbreviation(title: str, abbreviations: tuple[str, ...]) -> str | None:
    return next((a for a in abbreviations if _word_re(a, False).search(title)), None)


def _overseas_exhibition_evidence(notice: Notice, rules: OverseasRules | None = None) -> str | None:
    """공고명으로 해외 개최(국가관·부스) 공고인지 판별하고, 맞으면 근거 문구를 돌려준다. 아니면 None."""
    rules = rules or load_overseas_rules()
    title = notice.title or ""
    if _KOREA_PAVILION_RE.search(title) and _overseas_event_sign(title, rules):
        return "제목에 '한국관'"
    if _EXPO_GROUP_RE.search(title):
        return "전시회·박람회 단체관"
    if _GROUP_RE.search(title):
        place = _find_place(title, rules.places)
        if place:
            return f"해외 지명 '{place}' + 단체관"
    abbreviation = _find_abbreviation(title, rules.abbreviations)
    if abbreviation:
        return f"영문 국가 약자 '{abbreviation}'"
    return None


# 해외 박람회 국가관 공고명에 함께 오는 표시 — "CES 2027 통합한국관", "2027 UAE 두바이 의료기기전시회 한국관", "NANO TECH 2027 한국관".
# 연도는 "2027 …"처럼 행사명에 붙은 것만("2026년 …"은 국내 공고에도 흔해서 안 봄). 영문은 국가 약자만(KOR·JPN·USA … —
# 2026-10-20 사용자 결정: 아무 대문자 약어나 보면 "울산 KTX역 한국관" 같은 국내 공고가 걸려서).
_EVENT_SIGN_RE = re.compile(r"(?<![0-9])20\d{2}(?![0-9]|\s*년)|전시회|박람회|엑스포|페어")
_EVENT_WORD_RE = re.compile(r"(?<![A-Za-z])(?:expo|fair|show)(?![A-Za-z])", re.IGNORECASE)


def _overseas_event_sign(title: str, rules: OverseasRules) -> bool:
    """'한국관'이 해외 박람회 국가관인지 — 연도·전시회류·영문 국가 약자(KOR 포함)·해외 지명 중 하나가 함께 있어야 한다."""
    skip = set(rules.flag_skip_places)
    return bool(
        _EVENT_SIGN_RE.search(title)
        or _EVENT_WORD_RE.search(title)
        or _find_abbreviation(title, (*rules.abbreviations, "KOR"))
        or _find_place(title, tuple(p for p in rules.places if p not in skip))
    )


def _overseas_suspect(notice: Notice, rules: OverseasRules | None = None) -> str | None:
    """제외 규칙엔 안 걸린 공고명의 해외 지명 — 표시만 한다. 해외 행사 표시 없는 '한국관'도 여기(2026-10-20)."""
    rules = rules or load_overseas_rules()
    if _KOREA_PAVILION_RE.search(notice.title or ""):
        return "제목에 '한국관' (해외 행사 표시 없음)"
    skip = set(rules.flag_skip_places)
    place = _find_place(notice.title or "", tuple(p for p in rules.places if p not in skip))
    return f"제목에 해외 지명 '{place}'" if place else None


def mongolia_word(text: str | None, rules: OverseasRules | None = None) -> str | None:
    """글에 든 몽골·몽골 지명. "무릉"은 국내 무릉계곡·무릉도원과 겹쳐 "몽골"이 함께 있을 때만."""
    rules = rules or load_overseas_rules()
    text = text or ""
    for word in rules.mongolia:
        if word in text and (word != "무릉" or "몽골" in text):
            return word
    return None


def _is_mongolia(notice: Notice, rules: OverseasRules | None = None) -> bool:
    return mongolia_word(notice.title, rules) is not None


def _match_codes(notice: Notice, config: ScreenConfig) -> tuple[list[str], list[str]]:
    # 대표 세부품명번호 + 구매대상물품 목록의 번호 전부 — 하나만 맞아도 품명코드 일치
    codes = set(notice.product_codes or []) | ({notice.product_class_no} if notice.product_class_no else set())
    product_hits = [entry["name"] for entry in config.product_codes if entry.get("code") in codes]
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
            f"품명코드: 공고 세부품명번호 {notice.product_class_no}{name} — 보유 세부품명 {len(config.product_codes)}개 목록에 없음"
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
    if excluded_word and config.review_conflicts and _match_keywords(notice, config.keywords, config.keyword_skip_when):
        # 관심 키워드도 있다 — 제외하지 않고 아래 해외 조건만 거친 뒤 "검토 필요"로 둔다.
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
            excluded_reason=f"해외 개최 — 국내 공고 아님 ({overseas_evidence})",
        )

    product_hits, industry_hits = _match_codes(notice, config)
    keyword_hits = _match_keywords(notice, config.keywords, config.keyword_skip_when)

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
            f"{overseas_evidence} · 몽골 감지 — 확장 중인 시장이라 자동 배제 대신 표시" if mongolia else None
        ),
        # 몽골이면 "해외 개최 의심" 대신 "몽골 공고 유력"(2026-10-19 사용자 요청)
        overseas_suspect=None if overseas_booth or mongolia_word(notice.title) else _overseas_suspect(notice),
        mongolia=(f"{overseas_evidence} · 몽골" if mongolia else
                  (f"공고명에 '{mongolia_word(notice.title)}'" if mongolia_word(notice.title) else None)),
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
    # 첨부에서 찾은 마감의 종류 — "서류 제출기한" 또는 "응모신청 등록 마감"(2026-10-02)
    attachment_deadline_kind: str = "서류 제출기한"
    # 사전규격의 의견등록 마감. 본공고에는 없다(항상 None) — 입찰 마감이 없는 사전규격에서만 쓰인다.
    opinion_deadline: datetime | None = None
    # 팝업에 같이 보여줄 일시(카드 마감 계산엔 안 씀) — 공고 게시·개찰, 첨부 공고문에서 찾은 참가등록·제출 마감 전부(2026-10-02 요청)
    posted_at: datetime | None = None
    opening_at: datetime | None = None
    attachment_deadlines: list = field(default_factory=list)

    @property
    def earliest(self) -> tuple[str, datetime] | None:
        """카드 마감(D-N 기준) = 자격등록·입찰·의견등록(사전규격) 마감 중 **가장 이른 것**(2026-10-19 통합 결정 4번 —
        10/02의 우선순위 방식을 바꿈). 셋 다 비었을 때만 첨부 공고문의 참가등록·제출 마감을 쓴다.
        공동수급협정 마감은 공동수급으로 참가할 때만 의미가 있어 계산에서 뺀다(지나도 단독 참가는 가능) — 팝업(`all_deadlines`)에만 보인다.
        이 날짜가 지나면 수집 단계에서 뺀다(`pipeline.drop_expired`)."""
        api = [
            (label, dt)
            for label, dt in (
                ("자격등록 마감", self.qualification_deadline),
                ("입찰 마감", self.bid_deadline),
                ("의견등록 마감", self.opinion_deadline),
            )
            if dt is not None
        ]
        if api:
            return min(api, key=lambda pair: pair[1])
        if self.attachment_deadline is not None:
            return (self.attachment_deadline_kind, self.attachment_deadline)
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

    def is_expired(self, now: datetime) -> bool:
        """기준 마감(`earliest`)이 지났는가. 마감 정보가 없으면 False(남김)."""
        earliest = self.earliest
        return earliest is not None and earliest[1] < now


def build_schedule(notice: Notice) -> Schedule:
    return Schedule(
        qualification_deadline=parse_datetime(notice.qualification_deadline),
        joint_agreement_deadline=parse_datetime(notice.joint_agreement_deadline),
        bid_deadline=parse_datetime(notice.bid_deadline),
        opinion_deadline=parse_datetime(notice.opinion_deadline),
        posted_at=parse_datetime(getattr(notice, "posted_at", None)),
        opening_at=parse_datetime(getattr(notice, "opening_at", None)),
    )
