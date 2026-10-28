"""투찰 경향 분야 — 공고명 키워드로 나눈다(2026-10-28 사용자 결정: 분야 = 공고명 키워드 기준, 설정 `config/bid_fields.json`)."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

CONFIG = Path(__file__).resolve().parent.parent / "config" / "bid_fields.json"
OTHER = "기타"
NO_TITLE = "공고명 없음"


@lru_cache(maxsize=1)
def fields() -> tuple[tuple[str, tuple[str, ...]], ...]:
    try:
        data = json.loads(CONFIG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ()
    return tuple((f["name"], tuple(_squash(k) for k in f.get("keywords", []))) for f in data.get("fields", []))


def _squash(text: str) -> str:
    return re.sub(r"\s+", "", text or "").upper()


def classify(title: str) -> str:
    """공고명 → 분야 이름. 위에서부터 처음 맞는 분야, 없으면 '기타', 공고명을 모르면 '공고명 없음'."""
    text = _squash(title)
    if not text:
        return NO_TITLE
    for name, keywords in fields():
        if any(k and k in text for k in keywords):
            return name
    return OTHER
