"""정기 보고(매일 입찰·주간)를 이번 기간에 이미 보냈는지 — 같은 보고가 두 번 나가지 않게 (2026-10-20, 팀원 수집기 방식).

보고 하나를 두 곳이 깨운다. 외부 예약 서비스(cron-job.org)가 정시에 먼저 깨우고, GitHub 예약 실행이 한 시간 뒤 예비로
한 번 더 돈다(GitHub 예약 실행은 몇 시간씩 밀리거나 건너뛴다 — 실측 10/13 08:00 → 12:21). 먼저 보낸 쪽이 여기 남기고,
뒤에 도는 쪽은 기록을 보고 수집도 하지 않고 끝난다. 실패 재시도 Run도 같은 기록으로 겹치지 않는다.

공고 단위 기록(`sent_state.py`, [정정] 다시 보내기)과는 따로 둔다 — 이건 "이번 보고를 보냈나"만 본다.
기록은 보고 종류마다 파일 하나(`{"last": "2026-10-20"}`)로, bot-state 브랜치에 커밋한다(.github/workflows/report-*.yml) —
Actions 캐시는 7일 안 쓰면 지워져 주간 기록이 딱 걸린다(팀원 수집기의 약점). 종류마다 파일을 나눠 월요일에 매일·주간
보고가 같이 올려도 서로 덮어쓰지 않는다.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

KST = ZoneInfo("Asia/Seoul")
KINDS = ("daily", "weekly")


def _kst(now: datetime) -> datetime:
    """실행 환경 시간대와 상관없이 한국 시각으로 (시간대 없는 값은 이미 한국 시각으로 본다 — 워크플로가 TZ=Asia/Seoul)."""
    return now.astimezone(KST) if now.tzinfo else now


def period_key(kind: str, now: datetime) -> str:
    """같은 보고로 치는 기간. daily는 그날, weekly는 그 주 월요일 (한국 날짜 YYYY-MM-DD)."""
    day = _kst(now).date()
    if kind == "weekly":
        day -= timedelta(days=day.weekday())
    return day.isoformat()


def is_weekend(now: datetime) -> bool:
    """한국 날짜로 토·일 — 매일 입찰 보고는 평일만(2026-10-19 담당자 요청, 월요일 보고가 금~일 게시분을 받는다)."""
    return _kst(now).weekday() >= 5


def load(path: Path) -> str | None:
    """마지막으로 보낸 기간. 파일이 없거나 깨졌으면 None(처음부터 — 한 번 더 나갈 수는 있어도 빠지진 않는다)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as err:
        log.warning("보고 발송 기록을 읽지 못해 새로 시작합니다 (%s): %s", path, err)
        return None
    last = data.get("last") if isinstance(data, dict) else None
    return last if isinstance(last, str) else None


def already_sent(path: Path, kind: str, now: datetime) -> bool:
    return load(path) == period_key(kind, now)


def mark(path: Path, kind: str, now: datetime) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"last": period_key(kind, now)}, ensure_ascii=False) + "\n", encoding="utf-8")
