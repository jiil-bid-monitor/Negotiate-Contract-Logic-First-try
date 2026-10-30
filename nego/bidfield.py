"""투찰 경향 분야 — 공고명 키워드로 나눈다(2026-10-28 사용자 결정: 분야 = 공고명 키워드 기준, 설정 `config/bid_fields.json`).

키워드 비교는 수집 로직(`nego.screen`)과 같다 — 띄어쓰기를 지운 공고명(`_canonical_title`)에 키워드가 들어 있으면 맞음,
다른 낱말 속 글자로 잡히는 경우는 그 낱말을 지우고 본다(`keywordExceptions` — 제외 키워드 예외 `excludeKeywordExceptions`와 같은 방식).
공고명으로 '기타'인 이번 공고는 첨부 공고문 사업 내용의 키워드 횟수 → 면허제한 업종 순으로 보완한다(`classify_notice`, 2026-10-30 사용자 결정).
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from .screen import _canonical_title, _squash

CONFIG = Path(__file__).resolve().parent.parent / "config" / "bid_fields.json"
OTHER = "기타"
NO_TITLE = "공고명 없음"


@lru_cache(maxsize=1)
def _data() -> dict:
    try:
        return json.loads(CONFIG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


@lru_cache(maxsize=1)
def _config() -> tuple[tuple[tuple[str, tuple[str, ...]], ...], dict[str, tuple[str, ...]]]:
    data = _data()
    fields = tuple((f["name"], tuple(k for k in f.get("keywords", []) if _squash(k))) for f in data.get("fields", []))
    exceptions = {k: tuple(v) for k, v in (data.get("keywordExceptions") or {}).items()}
    return fields, exceptions


def fields() -> list[str]:
    """분야 이름(설정 순서, 중복 없이)."""
    return list(dict.fromkeys(name for name, _ in _config()[0]))


def _hit(word: str, title: str, exceptions: dict[str, tuple[str, ...]]) -> bool:
    haystack = title
    for allowed in exceptions.get(word, ()):
        if _squash(allowed):
            haystack = haystack.replace(_squash(allowed), " ")
    return _squash(word).upper() in haystack.upper()


def classify(title: str) -> str:
    """공고명 → 분야 이름. 위에서부터 처음 맞는 분야, 없으면 '기타', 공고명을 모르면 '공고명 없음'."""
    text = _canonical_title(title)
    if not text:
        return NO_TITLE
    rules, exceptions = _config()
    for name, keywords in rules:
        if any(_hit(k, text, exceptions) for k in keywords):
            return name
    return OTHER


def keyword_hits(text: str) -> dict[str, dict[str, int]]:
    """글(사업 내용 등) → {분야: {키워드: 나온 횟수}}. 띄어쓰기 무시·예외 낱말은 `classify`와 같다."""
    haystack = _squash(text).upper()
    rules, exceptions = _config()
    out: dict[str, dict[str, int]] = {}
    for name, keywords in rules:
        for word in keywords:
            hay = haystack
            for allowed in exceptions.get(word, ()):
                if _squash(allowed):
                    hay = hay.replace(_squash(allowed).upper(), " ")
            count = hay.count(_squash(word).upper())
            if count:
                out.setdefault(name, {})[word] = out.get(name, {}).get(word, 0) + count
    return out


def classify_text(text: str) -> tuple[str, dict[str, int]]:
    """여러 줄 글 → (키워드가 가장 많이 나온 분야, 그 분야 키워드별 횟수). 같으면 설정 위쪽 분야, 하나도 없으면 '기타'.
    공고명은 짧아 첫 키워드로 정하지만(`classify`), 사업 내용은 여러 분야 낱말이 섞여 횟수로 정한다."""
    hits = keyword_hits(text)
    if not hits:
        return OTHER, {}
    order = fields()
    best = max(hits, key=lambda name: (sum(hits[name].values()), -order.index(name)))
    return best, hits[best]


def classify_notice(title: str, summary: str = "", licenses: list[str] | tuple[str, ...] = ()) -> tuple[str, str]:
    """이번 공고 분야 → (분야, 근거 문구). 공고명으로 '기타'일 때만 ① 첨부 공고문의 사업 내용(`summary` — 사업개요 줄·
    사업 범위 목록, `bidcalc.parse_notice_text`)에서 분야 키워드를 세어 가장 많은 분야(10/30 사용자 요청: 첨부 키워드 분석)
    ② 그래도 기타면 면허제한 업종 이름(설정 `licenseFields` — 업종 이름에 낱말이 들어 있으면 그 분야)으로 정한다.
    근거 문구는 공고명으로 정했으면 빈 값. 과거 개찰 자료는 공고명만 있어 `classify` 그대로."""
    field = classify(title)
    if field != OTHER:
        return field, ""
    if summary:
        by_text, words = classify_text(summary)
        if by_text != OTHER:
            found = " · ".join(f"{w} {n}" for w, n in sorted(words.items(), key=lambda kv: -kv[1]))
            lines = summary.strip().splitlines()
            more = f" 외 {len(lines) - 1}줄" if len(lines) > 1 else ""
            return by_text, f"공고문 사업 내용 키워드 {found} 기준(공고명엔 분야 낱말 없음 — '{lines[0][:60]}'{more})"
    for name in licenses:
        squashed = _squash(name)
        for word, mapped in (_data().get("licenseFields") or {}).items():
            if _squash(word) and _squash(word) in squashed:
                return mapped, f"면허제한 업종 '{name}' 기준(공고명엔 분야 낱말 없음)"
    return field, ""
