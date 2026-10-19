"""첨부파일 원문에서 제출기한/마감일시를 찾는다.

API의 자격등록마감·공동수급협정마감·입찰마감 세 필드가 전부 비어 리포트에
"일정 미상"으로 뜨는 공고가 있다(실측: 경상남도관광재단 "「K-거상」기획전시관
및 상설주제관 콘텐츠 개선·개발 용역" R26BK01707504 — 세 필드 모두 없지만,
첨부 제안요청서에는 "제출기간 : 2026. 9. 14.(월) 9:00~18:00"가 명시돼
있었다). 이럴 때만(API 필드가 전부 비었을 때만) 보충하는 최후 수단이다 —
API 값이 하나라도 있으면 이 모듈은 아예 쓰이지 않는다
(`screen.Schedule.earliest` 참고).

라벨(제출기한/제출기간 등) 뒤 가까이(문자 수 기준)에서 날짜를, 그 뒤
가까이에서 시각(단일 또는 "시작~종료" 범위)을 찾는다. 범위면 종료 시각을
마감으로 본다. 시각을 못 찾으면 그날 23:59을 마감으로 본다. 라벨 하나가
날짜를 못 찾으면(예: 절 제목만 있고 실제 값은 몇 줄 뒤) 다음 라벨 매치로
넘어간다 — fail-open, 못 찾으면 그냥 None이라 기존 "일정 미상" 그대로다.
"""

from __future__ import annotations

import re
from datetime import datetime

_S = r"\s*"  # 표 머리글은 "제 출 일 시"처럼 글자 사이를 띄운다(2026-10-02 고흥분청문화박물관)
_DEADLINE_LABEL_RE = re.compile(
    r"(?:제안서\s*)?제" + _S + r"출" + _S + r"(?:기" + _S + r"한|기" + _S + r"간|일" + _S + r"시)|마감\s*(?:일시|일자|기한)?|접수\s*마감"
)
_DATE_RE = re.compile(
    r"(?P<year>20\d{2})\s*[.\-년]\s*(?P<month>1[0-2]|0?[1-9])\s*[.\-월]\s*(?P<day>3[01]|[12]\d|0?[1-9])\s*일?\.?"
)
_TIME_RE = re.compile(
    r"(?P<h1>[01]?\d|2[0-3])\s*[:시]\s*(?P<m1>[0-5]?\d)\s*분?"
    r"(?:\s*[~∼-]\s*(?P<h2>[01]?\d|2[0-3])\s*[:시]\s*(?P<m2>[0-5]?\d)\s*분?)?"
)
# 라벨→날짜, 날짜→시각까지 허용하는 거리(문자 수). 절 제목처럼 라벨만 있고
# 값이 멀리 떨어진 경우를 걸러내는 용도라 넉넉하게 잡을 필요는 없다.
_LABEL_TO_DATE_WINDOW = 20
# 표에서는 머리글 줄("제 출 일 시 제출장소 제출방법 제 출 자 제출서류") 다음 줄에 값이 온다 — 그만큼 더 본다
_TABLE_WINDOW = 60
_DATE_TO_TIME_WINDOW = 25
# 기간 표시 "~" 뒤에 바로 끝 날짜가 오는지 — 요일 괄호("(금)")는 건너뜀
_RANGE_TO_DATE_RE = re.compile(r"\s*(?:\([^)\n]{1,3}\))?\s*[~∼～]\s*")
# 끝 날짜는 연도를 빼고 쓰기도 한다("~ 10. 20.(화) 10:00")
_MONTH_DAY_RE = re.compile(r"(?P<month>1[0-2]|0?[1-9])\s*[.\-월]\s*(?P<day>3[01]|[12]\d|0?[1-9])\s*일?\.?")


# 응모·참가 신청 등록 마감 — 제안서 제출보다 먼저 끝나는 경우가 있다(2026-10-02 실측: 거제 지심도 산마루문화놀이터
# 제안공모 "응모신청서 등록 일시 : 2026. 10. 13.(화) 13:00 ~ 14:00" — 제안서 제출은 11/17이라 D-46으로 보였음)
_REGISTRATION_LABEL_RE = re.compile(
    r"(?:응모|참가|입찰\s*참가)\s*신청\s*서?\s*(?:등록|접수|제출)\s*(?:일시|기간|기한|마감)?|(?:응모|참가)\s*등록\s*(?:일시|기간|기한|마감)?"
)
REGISTRATION = "첨부 참가등록 마감"
SUBMISSION = "첨부파일 제출기한"


def extract_deadline(text: str) -> datetime | None:
    """제출기한/마감 표기를 찾아 datetime으로 반환한다. 못 찾으면 None."""
    return _first_deadline(text, _DEADLINE_LABEL_RE)


def extract_deadlines(text: str) -> list[tuple[str, datetime]]:
    """제출기한과 응모신청 등록 마감을 [(종류, 일시)]로. 화면 마감은 이 중 가장 이른 것을 쓴다."""
    out = []
    for kind, rx in ((REGISTRATION, _REGISTRATION_LABEL_RE), (SUBMISSION, _DEADLINE_LABEL_RE)):
        found = _first_deadline(text, rx)
        if found is not None:
            out.append((kind, found))
    return out


def _first_deadline(text: str, label_re: re.Pattern) -> datetime | None:
    for label_match in label_re.finditer(text):
        date_match = _DATE_RE.search(text, label_match.end())
        # 넓게 보는 건 글자를 띄운 표 머리글("제 출 일 시")뿐 — "도장 마감\n…(2022.6.30.)", "마감일로부터 …\n 선정 : 2021.10.22"
        # 처럼 흔한 낱말 뒤 아무 날짜나 잡지 않게(과거 문서로 확인)
        spaced_header = bool(re.fullmatch(r"(?:\S\s+){3,}\S", label_match.group(0).strip()))  # 한 글자씩 띄운 머리글만
        window = _TABLE_WINDOW if spaced_header else _LABEL_TO_DATE_WINDOW
        if not date_match or date_match.start() - label_match.end() > window:
            continue

        parsed = _date_time_at(text, date_match)
        if parsed is None:
            continue
        found, end = parsed
        # 날짜가 바뀌는 기간("2026.10.16.(금) 00:00 ~ 2026.10.20.(화) 10:00")이면 끝 일시가 마감 —
        # 시작 일시를 마감으로 잡던 것(2026-10-19 실측: 구미시 스타트업 필드 공용공간 리모델링 공사 설계용역,
        # 나라장터 입찰서제출 10/16 00:00 ~ 10/20 10:00인데 "첨부파일 제출기한 10/16 00:00 · 지남"으로 보였음)
        range_match = _RANGE_TO_DATE_RE.match(text, end)
        if range_match:
            end_date = _DATE_RE.match(text, range_match.end()) or _MONTH_DAY_RE.match(text, range_match.end())
            if end_date:
                later = _date_time_at(text, end_date, default_year=found.year)
                if later is not None and later[0] < found and "year" not in end_date.groupdict():  # 해 넘김 "12.30 ~ 1.5"
                    later = _date_time_at(text, end_date, default_year=found.year + 1)
                if later is not None and later[0] >= found:
                    found = later[0]
        return found
    return None


def _date_time_at(text: str, date_match: re.Match, default_year: int | None = None) -> tuple[datetime, int] | None:
    """날짜 매치 + 뒤 가까운 시각(범위면 종료 시각) → (일시, 읽은 끝 위치). 시각이 없으면 그날 23:59."""
    try:
        year = int(date_match.groupdict().get("year") or default_year or 0)
        month = int(date_match.group("month"))
        day = int(date_match.group("day"))
    except (TypeError, ValueError):
        return None

    hour, minute = 23, 59
    end = date_match.end()
    time_match = _TIME_RE.search(text, date_match.end())
    # "2026.10.16 ~ 2026.10.20 10:00"처럼 시작 날짜에 시각이 없으면 "~" 너머 끝 날짜의 시각을 가져오지 않는다
    if (time_match and time_match.start() - date_match.end() <= _DATE_TO_TIME_WINDOW
            and not re.search(r"[~∼～]", text[date_match.end():time_match.start()])):
        end = time_match.end()
        if time_match.group("h2") is not None:
            hour, minute = int(time_match.group("h2")), int(time_match.group("m2"))
        else:
            hour, minute = int(time_match.group("h1")), int(time_match.group("m1"))

    try:
        return datetime(year, month, day, hour, minute), end
    except ValueError:
        return None
