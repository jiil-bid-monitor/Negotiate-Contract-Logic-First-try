"""첨부파일 원문에서 "입찰 참가자격" 절을 찾아 항목 단위로 나눈다.

API의 면허제한정보가 비어 있을 때(예: 발주기관이 나라장터에 구조화된 형태로
등록하지 않은 경우 — 부안청자박물관 공고(R26BK01719858)로 실측 확인함) 사람이
원문으로 직접 자격요건을 확인해야 한다. 표를 파싱하는 게 아니라, 절 전체와
번호/기호가 붙은 개별 항목을 나눠서 읽기 쉽게 만드는 정도다.

실측한 다섯 관행을 지원한다:
  - 지자체식: "5. 입찰 참가자격" 아래 "가. / 나. / 다. ..." (정선군 공고문)
  - 조달청식: "3. 입찰참가자격" 아래 "3.1. / 3.2. / 3.3. ..." (세종 입찰설명서)
  - "가." 하나 아래 실제 요건은 "1) / 2) / 3) ..."로 나열 (정선군 제안요청서,
    부안청자박물관 공고 첨부파일에서도 같은 형태 확인)
  - "o" / "○" 불릿으로 나열 (실측: 두바이 의료기기전시회 한국관 공고문)
  - 원문자 "①②③④..."로 나열 (실측: 한국항공우주연구원 나로우주센터 공고)
다섯 다 아니면 절 전체를 항목 1개로 반환한다 (fail-open — 잘못 쪼개느니
통째로 보여주는 편이 낫다).

한계: PDF는 문단 구분이 없어서(페이지 레이아웃 기준으로만 줄바꿈됨) 다음
절 제목이 이전 문장 끝에 붙어버리는 경우가 있고, 그러면 절 경계를 못 찾아
다음 절까지 통째로 딸려온다 (실측: 세종 공사입찰설명서 PDF). 같은 공고에
HWPX본이 있으면 그쪽이 훨씬 안정적이다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# 　(전각 공백)은 HWP류 문서에서 들여쓰기로 흔히 쓰인다(실측: 두바이
# 의료기기전시회 한국관 공고문의 "o" 불릿 들여쓰기) — 일반 스페이스/탭과
# 같이 줄 앞머리 공백으로 취급해야 항목을 놓치지 않는다.

# 줄 끝을 요구하지 않는다 — "4. 입찰참가자격 : 아래 자격을 모두 충족…"처럼
# 콜론 뒤에 같은 줄에 안내문이 붙는 문서가 있다(실측: 두바이 의료기기전시회
# 한국관 공고문). 그 안내문은 본문 첫 줄로 들어갈 뿐이라 판정에는 지장 없다.

# 한글 문서는 절 제목의 글자 사이마다 공백을 넣어 자간을 벌리는 관행이 있다
# (실측: 한식진흥원 공고 — "2. 입 찰 참 가 자 격"처럼 한 글자씩 사이가 벌어져
# 있었다). 기존 정규식은 "입찰"/"참가"/"자격" 2글자 묶음 사이의 공백만 허용해서
# 이런 표기를 놓쳤다 — 아래에서 한 글자씩 사이에 공백류를 허용하도록 만든다.
def _spaced(word: str) -> str:
    """한글 제목의 자간 벌림 표기(글자 사이마다 공백)를 허용하도록, 문자마다
    사이에 공백류(스페이스/탭/전각공백)를 넣어도 매치되는 정규식 조각을 만든다."""
    return r"[ \t\u3000]*".join(re.escape(ch) for ch in word)


_WS = r"[ \t\u3000]*"

# 절 제목 앞 번호·기호. 실측(과거 제안요청서 206건): "2." / "나." / "3)" / "Ⅱ." / "□" / 번호 없음.
_MARKER = (
    r"(?P<marker>\d{1,2}\.\d{1,2}\.?|\d{1,2}\.(?!\d)|\d{1,2}\)|[가나다라마바사아자차카타파하]\.|[가나다라마바사아자차카타파하]\)"
    r"|[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]\.?|[□■◎◇◆▶●○]|제" + _WS + r"\d{1,2}" + _WS + r"[장절조])?"
)

# "입찰참가자격" / "입찰 참가 자격" / "참가자격"(입찰 없이, 실측 27건) — 제목 **줄 전체**여야 한다.
# 뒤에는 요건·조건·사항, 쪽 번호(목차), 콜론·괄호로 시작하는 안내문만 올 수 있다 — 그래야
# "1. 입찰 참가자격을 증명하는 서류 사본 1통"(제출서류 목록) 같은 문장을 제목으로 잡지 않는다.
# 제목 뒤 꼬리말: "요건·조건·사항", "및 제한"·"및 관련사항"(실측 정선 남면, 양양군 청사)
_TAIL = _WS + r"(?:요건|조건|사항)?" + _WS + r"(?:및" + _WS + r"[가-힣]{1,6})?"
_HEADING_RE = re.compile(
    r"^" + _WS + _MARKER + _WS + r"(?P<bid>" + _spaced("입찰") + _WS + r")?" + _spaced("참가자격")
    + r"(?=" + _TAIL + _WS + r"(?:[:：(（\[][^\n]*|\d{1,3})?" + _WS + r"$)"
    + _TAIL + _WS + r"[:：]?",
    re.MULTILINE,
)

# "6. 입찰내용"처럼 새 최상위 절이 시작되는 줄. "3.1. ..."(소항목)과 구분하기
# 위해, 번호 뒤에 공백이 최소 1개 있고 그다음이 숫자가 아니어야 한다
# ("3.1."은 번호 다음이 공백 없이 바로 숫자라 여기 안 걸림).
_NEXT_TOP_HEADING_RE = re.compile(r"^[ \t\u3000]*\d{1,2}\.[ \t\u3000]+[^\d\s]", re.MULTILINE)
_ROMAN_HEADING_RE = re.compile(r"^[ \t\u3000]*[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]\.?[ \t\u3000]*[^\d\s]", re.MULTILINE)
_KOREAN_DOT_HEADING_RE = re.compile(r"^[ \t\u3000]*[가나다라마바사아자차카타파하]\.[ \t\u3000]+", re.MULTILINE)
_DECIMAL_HEADING_RE = re.compile(r"^[ \t\u3000]*\d{1,2}\.\d{1,2}\.?[ \t\u3000]+[^\d\s]", re.MULTILINE)
_PAREN_HEADING_RE = re.compile(r"^[ \t\u3000]*\d{1,2}\)[ \t\u3000]+", re.MULTILINE)

# 제목 번호 종류별로 절이 끝나는 곳 — 같은 급(또는 더 윗급) 제목이 다시 나오는 줄.
# "나. 입찰참가자격" 절은 "다."에서, "3) 입찰참가자격" 절은 "4)"·"다."·"4."에서 끝난다.
_SECTION_END = {
    "digit": (_NEXT_TOP_HEADING_RE, _ROMAN_HEADING_RE),
    "decimal": (_DECIMAL_HEADING_RE, _NEXT_TOP_HEADING_RE, _ROMAN_HEADING_RE),
    "korean": (_KOREAN_DOT_HEADING_RE, _NEXT_TOP_HEADING_RE, _ROMAN_HEADING_RE),
    "paren": (_PAREN_HEADING_RE, _KOREAN_DOT_HEADING_RE, _NEXT_TOP_HEADING_RE, _ROMAN_HEADING_RE),
    "roman": (_ROMAN_HEADING_RE,),
    "none": (_NEXT_TOP_HEADING_RE, _ROMAN_HEADING_RE),
}
# 상자형 기호만 — "○ 참가자격" 아래 항목도 "○"로 다는 문서가 있어 동그라미는 끝 표시로 쓰면 첫 항목에서 잘린다
_SYMBOL_MARKERS = set("□■◇◆")
# 번호 없는 제목·기호 제목은 끝이 불분명해 문서 끝까지 삼킬 수 있다 — 길이 상한을 둔다.
_MAX_SECTION_CHARS = 4000


def _marker_kind(marker: str | None) -> str:
    if not marker:
        return "none"
    if marker[0].isdigit():
        if marker.endswith(")"):
            return "paren"
        return "decimal" if re.match(r"\d{1,2}\.\d", marker) else "digit"
    if marker[0] in "가나다라마바사아자차카타파하":
        return "paren" if marker.endswith(")") else "korean"
    if marker[0] in "ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ":
        return "roman"
    return "none"

_KOREAN_LETTER_ITEM_RE = re.compile(r"^[ \t\u3000]*([가나다라마바사아자차카타파하])\.[ \t\u3000]*", re.MULTILINE)
_DECIMAL_ITEM_RE = re.compile(r"^[ \t\u3000]*(\d{1,2}\.\d{1,2}\.)[ \t\u3000]*", re.MULTILINE)
# "가." 하나 아래 "1) 2) 3) ..."로 개별 요건을 나열하는 문서도 있다 (실측:
# 정선군 제안요청서 — 요건 자체는 이 번호 목록에 있고 "가."는 도입 문장뿐).
_NUMBERED_PAREN_ITEM_RE = re.compile(r"^[ \t\u3000]*(\d{1,2})\)[ \t\u3000]*", re.MULTILINE)
_BULLET_ITEM_RE = re.compile(r"^[ \t\u3000]*[o○][ \t\u3000]+", re.MULTILINE)
# 원문자(①②③...) 목록도 실측 확인됨(한국항공우주연구원 나로우주센터 공고
# R26BK01710751 — "2. 입찰참가자격" 절 아래 ①②③④). 줄 맨 앞에서만 항목
# 시작으로 본다 — 같은 줄 안에 여러 개가 나열되는 절(예: "1. 입찰에 부치는
# 사항"의 "③ 기초금액 ... ④ 입찰방법 ...")은 참가자격 절이 아니라 관여하지
# 않고, 참가자격 절은 실측상 항목마다 줄이 나뉘어 있었다.
_CIRCLED_NUMBER_ITEM_RE = re.compile(
    r"^[ \t\u3000]*([①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳])[ \t\u3000]*", re.MULTILINE
)


@dataclass
class QualificationSection:
    heading: str
    body: str
    items: list[str]


# 목차 줄과 진짜 절을 가르는 기준 — 목차 항목은 제목 뒤에 쪽 번호만 있고 곧바로 다음 항목이
# 이어진다(실측: 울산박물관 R26BK01748232 "2. 입찰참가자격   2" → 본문이 "2" 한 글자).
_MIN_SECTION_HANGUL = 10


def _hangul_count(text: str) -> int:
    return sum(1 for ch in text if "가" <= ch <= "힣")


# PDF에서 글자마다 띄워 뽑힌 번호("3 .  입찰참가자격", "3 . 1 . 1 .") — 붙여야 제목·항목으로 알아본다
# (2026-10-19 제보: 구미시 스타트업 필드 공용공간 리모델링 공사 설계용역 공고문 PDF — 절을 못 찾아 3.1.2·3.1.3 요건이 빠짐)
_SPACED_NUMBER_RE = re.compile(r"(?m)^([ \t]*)(\d{1,2}(?: \. \d{1,2})*) \.(?=\s)")


def _join_spaced_numbering(text: str) -> str:
    return _SPACED_NUMBER_RE.sub(lambda m: m.group(1) + m.group(2).replace(" . ", ".") + ".", text)


def find_qualification_section(text: str) -> QualificationSection | None:
    """원문에서 입찰참가자격 절을 찾아 같은 급의 다음 제목 직전까지 잘라낸다.

    제안요청서·공고문은 앞에 **목차**가 있어 같은 제목이 두 번 나온다. 처음 나온 제목만
    보면 목차 줄을 절로 착각해 본문(쪽 번호) 한 줄만 잡힌다 — 그래서 제목이 나올 때마다
    본문에 한글이 충분히 있는지 보고, 처음으로 내용이 있는 절을 고른다. 번호가 붙은 제목을
    먼저 보고, 없을 때만 번호 없는 제목("입찰참가자격" 한 줄)을 쓴다(표 칸 제목과 헷갈리지 않게).
    전부 짧으면(목차만 있는 문서) 그중 가장 긴 것을 돌려준다.
    """
    # 우선순위: ① 번호 붙은 "입찰참가자격" ② 번호 붙은 "참가자격" ③ 번호 없는 제목.
    # 한 문서에 둘 다 있으면(실측: 노원수학문화관 — 앞쪽 요약의 "5. 참가자격"과 본문의
    # "2. 입찰참가자격") 입찰참가자격 절이 더 온전하다.
    text = _join_spaced_numbering(text)
    tiers: list[list[tuple[re.Match, str]]] = [[], [], []]
    best: tuple[int, re.Match, str] | None = None
    for match in _HEADING_RE.finditer(text):
        kind = _marker_kind(match.group("marker"))
        start = match.end()
        end = len(text)
        marker = match.group("marker") or ""
        ends = _SECTION_END[kind]
        if marker in _SYMBOL_MARKERS:
            # "□ 참가자격" 절은 다음 "□ 제안서 접수"에서 끝난다 — 예전엔 번호 제목("3.")만 끝으로 봐서
            # 접수·평가 방법·배점표까지 삼켰음(2026-10-02 실측: 안성 고삼호수 R26BK01719354 — 배점표 "전문인력
            # 보유현황 (6점)"이 기술인력 칩으로 잡힘)
            ends = ends + (re.compile(r"^" + _WS + re.escape(marker), re.MULTILINE),)
        for rx in ends:
            nxt = rx.search(text, pos=start)
            if nxt and nxt.start() < end:
                end = nxt.start()
        body = text[start:min(end, start + _MAX_SECTION_CHARS)].strip("\n")
        tier = 2 if kind == "none" else (0 if match.group("bid") else 1)
        tiers[tier].append((match, body))
        size = _hangul_count(body)
        if best is None or size > best[0]:
            best = (size, match, body)
    for candidates in tiers:
        for match, body in candidates:
            if _hangul_count(body) >= _MIN_SECTION_HANGUL:
                return QualificationSection(heading=match.group(0).strip(), body=body, items=split_items(body))
    block = _find_item_block(text)
    if block is not None:
        return block
    if best is None:
        return None
    _, match, body = best
    return QualificationSection(heading=match.group(0).strip(), body=body, items=split_items(body))


_LETTERS = "가나다라마바사아자차카타파하"
_LETTER_LINE_RE = re.compile(r"^[ \t\u3000]*([가나다라마바사아자차카타파하])[.)][ \t\u3000]*(.*)$")
# 첫 항목이 참가자격 목록의 첫 줄임을 알려주는 말 — 계약법 시행령 제12조(국가)·제13조(지자체) 인용.
# "참가자격"이란 말만으로는 안 된다: "가. 제안서 평가는 참가자격 요건을 갖춘 업체를 대상으로…"
# (평가 방법 절, 실측: 어린이해양환경 제안요청서)까지 잡힌다.
_FIRST_ITEM_HINT_RE = re.compile(r"시행령[」』｣]?[ \t]*(?:[」』｣][ \t]*)?제[ \t]*1[23][ \t]*조")
# 제목이 앞 항목 끝에 붙어 나온 경우 떼어낸다: "...있어야 함2. 입찰참가자격"
_GLUED_HEADING_RE = re.compile(r"[ \t\u3000]*\d{1,2}\.[ \t\u3000]*" + _spaced("입찰참가자격") + r"[ \t\u3000]*$")


def _find_item_block(text: str) -> QualificationSection | None:
    """제목으로 절을 못 찾았을 때의 마지막 수단 — "가. …시행령 제13조…참가자격을 갖춘 자"로
    시작하는 가나다 항목 묶음을 참가자격 절로 본다.

    실측(2026-09-30, 울산박물관 R26BK01748232): 한글 파일에서 절 제목("2. 입찰참가자격")이
    글상자 등에 들어 있어 본문 **뒤에** 붙어 나왔다("…있어야 함2. 입찰참가자격"). 제목으로는
    목차 줄밖에 못 찾아서, 첨부 자격판정이 계속 비어 있었다.
    """
    lines = text.splitlines()
    for i, line in enumerate(lines):
        m = _LETTER_LINE_RE.match(line)
        if not m or m.group(1) != "가" or not _FIRST_ITEM_HINT_RE.search(m.group(2)):
            continue
        body_lines = [line]
        last = 0
        items = 1
        for nxt in lines[i + 1 :]:
            if _NEXT_TOP_HEADING_RE.match(nxt) or _ROMAN_HEADING_RE.match(nxt) or _DECIMAL_HEADING_RE.match(nxt):
                break
            lm = _LETTER_LINE_RE.match(nxt)
            if lm:
                idx = _LETTERS.index(lm.group(1))
                if idx <= last:  # "가."로 다시 시작 = 다른 목록
                    break
                last = idx
                items += 1
            body_lines.append(nxt)
            if sum(len(x) for x in body_lines) > _MAX_SECTION_CHARS:
                break
        if items < 2:
            continue
        body = _GLUED_HEADING_RE.sub("", "\n".join(body_lines)).strip("\n")
        return QualificationSection(heading="(제목 없이 찾은 참가자격 항목)", body=body, items=split_items(body))
    return None


def split_items(body: str) -> list[str]:
    """절 본문을 항목 단위로 나눈다. 아는 패턴이 없으면 통째로 1개 항목."""
    for pattern in (
        _KOREAN_LETTER_ITEM_RE,
        _DECIMAL_ITEM_RE,
        _NUMBERED_PAREN_ITEM_RE,
        _BULLET_ITEM_RE,
        _CIRCLED_NUMBER_ITEM_RE,
    ):
        markers = list(pattern.finditer(body))
        if len(markers) < 2:
            continue
        items = []
        for i, marker in enumerate(markers):
            end = markers[i + 1].start() if i + 1 < len(markers) else len(body)
            item = body[marker.start() : end].strip()
            if item:
                items.append(item)
        return items

    stripped = body.strip()
    return [stripped] if stripped else []
