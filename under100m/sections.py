"""공고 문서에서 '제출 서류'·'유의사항' 같은 절을 원문 그대로 잘라 온다(규칙만, AI 없음).

제목 줄(번호·기호 + 낱말) 아래를 같은 급 이상의 다음 제목 직전까지. 목차 줄은 본문이 짧아 빠진다.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

from nego.qualification_text import _hangul_count, _join_spaced_numbering, _spaced, find_qualification_section

TOPICS = {
    "제출 서류": ("제출서류", "구비서류", "제출할서류", "입찰참가신청서류", "제출서류목록", "입찰서류"),
    "유의사항·기타": ("유의사항", "주의사항", "기타사항", "참고사항", "특기사항", "기타"),
}
MAX_CHARS = 2500
_WS = r"[ \t\u3000]*"
# 급: 숫자가 작을수록 윗급. ※는 '가.' 아래 붙임 글 — 다음 '라.'나 다른 ※에서 끝난다.
_LEVELS = [
    (0, r"[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]\.?"), (1, r"[□■◆◇]"), (2, r"\d{1,2}\.(?!\d)"), (3, r"\d{1,2}\.\d{1,2}\.?|\d{1,2}-\d{1,2}\.?"),
    (4, r"[가나다라마바사아자차카타파하]\."), (5, r"※"), (6, r"\d{1,2}\)|[가나다라마바사아자차카타파하]\)"), (7, r"[○●◎o▶-]"),
]
_MARKER = "|".join(f"(?:{rx})" for _, rx in _LEVELS)


def _level(marker: str) -> int:
    return next(lv for lv, rx in _LEVELS if re.fullmatch(rx, marker))


def _heading(word: str) -> re.Pattern:
    # 제목 줄 전체: 번호·기호, 앞말(15자까지), 낱말, 꼬리(괄호 안내·쌍점 뒤 짧은 말)
    return re.compile(r"^" + _WS + r"(?P<m>" + _MARKER + r")?" + _WS + r"(?:[가-힣 ]{0,15}?)" + _spaced(word)
                      + _WS + r"(?:및" + _WS + r"[가-힣 ]{1,10})?" + _WS + r"(?:[(（\[][^\n]{0,30}[)）\]])?" + _WS
                      + r"[:：]?" + _WS + r"$", re.MULTILINE)


def _normalize(text: str) -> str:
    text = text.replace("\xa0", " ")
    text = re.sub(r"(?m)^([가-힣][가-힣 ]{1,30}?)(\d{1,2}\.)[ \t]*$", r"\2 \1", text)  # PDF '기타사항11.' — 번호가 뒤로 밀림
    text = re.sub(r"(?m)^([ \t\u3000]*[가나다라마바사아자차카타파하]) \.", r"\1.", text)  # PDF '가 .'
    return _join_spaced_numbering(text)


def _cut(text: str, match: re.Match) -> str:
    level = _level(match.group("m")) if match.group("m") else 2  # 번호 없는 제목은 다음 '1.'급 제목까지
    ends = "|".join(rx for lv, rx in _LEVELS if lv <= level)
    stop = len(text)
    for nxt in re.compile(r"^" + _WS + r"(?:" + ends + r")", re.MULTILINE).finditer(text, match.end()):
        if _hangul_count(text[match.end(): nxt.start()]) >= 10:  # 제목 바로 아래 '□ 아래 서류를 모두 제출' 머리말에서 끊지 않게
            stop = nxt.start()
            break
    body = text[match.end(): stop].strip("\n")
    return body if len(body) <= MAX_CHARS else body[:MAX_CHARS] + "\n… (뒤 생략 — 원문 확인)"


def _tidy(body: str) -> list[str]:
    lines = [re.sub(r"[ \t\u3000]+", " ", line).strip() for line in body.splitlines() if line.strip()]
    lines = [line for line in lines if re.search(r"[\uac00-\ud7a3A-Za-z0-9]", line)]  # '-----' \uad6c\ubd84\uc120 \ube7c\uae30
    end = next((i for i, line in enumerate(lines) if re.fullmatch(r"\d{4}\s*\.\s*\d{1,2}\s*\.(\s*\d{1,2}\s*\.)?", line)), len(lines))
    return lines[:end]  # \uacf5\uace0 \ub05d \ub0a0\uc9dc\u00b7\uae30\uad00\uc7a5 \uc904\uc740 \ube8c


def _same(a: str, b: str) -> bool:
    fa, fb = re.sub(r"\W", "", a), re.sub(r"\W", "", b)
    return fa in fb or fb in fa or SequenceMatcher(None, fa, fb).ratio() >= 0.8


def find_sections(docs: list[tuple[str, str]]) -> dict[str, list[tuple[str, str, list[str]]]]:
    """{주제: [(파일 이름, 제목 줄, 본문 줄들)]}. 한글·PDF 사본처럼 같은 내용은 한 번만(먼저 나온 것)."""
    out: dict[str, list[tuple[str, str, list[str]]]] = {"참가 자격": [], **{t: [] for t in TOPICS}}
    seen: list[str] = []
    ordered = sorted(docs, key=lambda d: d[0].lower().endswith(".pdf"))  # 한글 원본이 PDF 사본보다 줄이 덜 깨짐
    for name, raw in ordered:
        text = _normalize(raw)
        found = []
        for topic, words in TOPICS.items():
            for word in words:
                for m in _heading(word).finditer(text):
                    body = _cut(text, m)
                    if _hangul_count(body) >= 10:
                        found.append((topic, m.group(0).strip(), body))
        qual = find_qualification_section(text)
        if qual:
            body = qual.body
            for _, head, _ in found:  # '3. 입찰참가자격' 안의 '3-1. 제출서류'는 제출 서류 쪽에만
                body = body.split(head)[0]
            if _hangul_count(body) >= 10:
                found.insert(0, ("참가 자격", qual.heading, body[:MAX_CHARS]))
        for topic, head, body in found:
            if any(_same(body, s) for s in seen):
                continue
            seen.append(body)
            out[topic].append((name, head, _tidy(body)))
    return {k: v for k, v in out.items() if v}
