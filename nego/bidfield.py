"""투찰 경향 분야 — 공고명 키워드로 나눈다(2026-10-28 사용자 결정: 분야 = 공고명 키워드 기준, 설정 `config/bid_fields.json`).

키워드 비교는 수집 로직(`nego.screen`)과 같다 — 띄어쓰기를 지운 공고명(`_canonical_title`)에 키워드가 들어 있으면 맞음,
다른 낱말 속 글자로 잡히는 경우는 그 낱말을 지우고 본다(`keywordExceptions` — 제외 키워드 예외 `excludeKeywordExceptions`와 같은 방식).
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
def _config() -> tuple[tuple[tuple[str, tuple[str, ...]], ...], dict[str, tuple[str, ...]]]:
    try:
        data = json.loads(CONFIG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return (), {}
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
