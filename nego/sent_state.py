"""매일 '입찰' 보고의 공고 단위 중복 방지 (2026-10-19 통합 결정 14번).

보낸 공고의 공고번호·차수를 기록해 두고, 다음 보고부터는 **처음 보는 공고만** 보낸다. 이미 보낸 공고가 차수가 올라
다시 올라오면(정정공고) [정정] 표시를 붙여 다시 보낸다. 재공고는 공고번호가 새로 붙어 새 공고로 간다.
그래서 조회 기간을 '전날 하루'보다 넓게(3일) 잡아도 같은 공고가 다시 가지 않고, GitHub 예약 실행이 밀린 날이나
늦게 등록된 공고도 빠지지 않는다.

기록 파일은 Actions 캐시가 아니라 저장소의 상태 전용 브랜치(`bot-state`)에 커밋해 둔다(.github/workflows/daily.yml) —
캐시는 7일 안 쓰면 지워져 같은 공고가 다시 갈 수 있다(팀원 수집기의 약점). 90일 지난 기록은 지운다.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

KEEP_DAYS = 90


def _ord(text: str | None) -> int:
    try:
        return int(str(text).strip())
    except (TypeError, ValueError):
        return -1


def load(path: Path) -> dict[str, dict[str, str]]:
    """{공고번호: {"ord": "001", "at": "2026-10-19"}}. 파일이 없거나 깨졌으면 빈 기록(처음부터)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, json.JSONDecodeError) as err:
        log.warning("보낸 공고 기록을 읽지 못해 새로 시작합니다 (%s): %s", path, err)
        return {}
    return data if isinstance(data, dict) else {}


def select_new(candidates: list[Any], sent: dict[str, dict[str, str]]) -> list[Any]:
    """처음 보는 공고 + 차수가 오른 공고(정정 — `candidate.corrected = True`)만 남긴다."""
    out = []
    for c in candidates:
        prev = sent.get(c.notice.notice_no)
        if prev is None:
            out.append(c)
        elif _ord(c.notice.notice_ord) > _ord(prev.get("ord")):
            c.corrected = True
            out.append(c)
    return out


def mark(sent: dict[str, dict[str, str]], candidates: list[Any], now: datetime) -> dict[str, dict[str, str]]:
    today = now.strftime("%Y-%m-%d")
    for c in candidates:
        sent[c.notice.notice_no] = {"ord": c.notice.notice_ord, "at": today}
    cutoff = (now - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
    return {no: v for no, v in sent.items() if str(v.get("at", "")) >= cutoff}


def save(path: Path, sent: dict[str, dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sent, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
