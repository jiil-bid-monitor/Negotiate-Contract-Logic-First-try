"""첨부 공고문에서 개최·사업 장소를 찾아 해외인지 본다 (2026-10-16 통합 결정 8번 ③).

공고명에 '한국관'·국가 약자가 없는 해외 전시회 부스 공고는 제목만으로 못 거른다. 공고문·과업지시서의
"개최 장소: 독일 뒤셀도르프 메쎄" 같은 줄에 해외 지명이 있으면 **제외하지 않고** "🌐 해외 개최 (첨부: …)" 표시만 붙인다
(첨부 파싱으로는 빼지 않는다는 6·7번 원칙과 같음). 지명 목록은 `config/overseas.json`(공고명 판정과 같은 파일).
"""

from __future__ import annotations

import re

from .screen import load_overseas_rules

# "개최 장소", "개최지", "행사 장소", "전시 장소", "사업 장소", "사업 위치", "설치 장소" + 구분자(: ： -)
_VENUE_LABEL_RE = re.compile(
    r"(?:개\s*최\s*(?:장\s*소|지|국가|도시)|행\s*사\s*장\s*소|전\s*시\s*(?:장\s*소|회\s*장)|사\s*업\s*(?:장\s*소|위\s*치|대\s*상\s*지)"
    r"|설\s*치\s*장\s*소|장\s*소)\s*[:：\-]\s*([^\n]{2,60})"
)
# 국내 지명이면 해외가 아니다 — 시·도 이름이 있으면 건너뛴다
_DOMESTIC_RE = re.compile(
    r"서울|부산|대구|인천|광주|대전|울산|세종|경기|강원|충북|충남|충청|전북|전남|전라|경북|경남|경상|제주|국내"
)


def overseas_venue(text: str | None) -> str | None:
    """첨부 원문에서 해외 개최·사업 장소를 찾으면 그 장소 표기(앞 40자), 없으면 None. 몽골도 그대로 돌려준다."""
    if not text:
        return None
    rules = load_overseas_rules()
    places = [p for p in rules.places if p not in rules.flag_skip_places]
    for m in _VENUE_LABEL_RE.finditer(text):
        value = re.sub(r"\s+", " ", m.group(1)).strip()
        if _DOMESTIC_RE.search(value):
            continue
        for place in places:
            if re.search(r"[A-Za-z]", place):
                if re.search(rf"(?<![A-Za-z]){re.escape(place)}(?![A-Za-z])", value, re.I):
                    return value[:40]
            elif place in value:
                return value[:40]
        if any(word in value for word in rules.mongolia):
            return value[:40]
    return None
