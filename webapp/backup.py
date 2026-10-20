"""판단 기록을 PC 밖 폴더에 한 부 더 둔다 (2026-10-20, 팀원 레포 10/19 이식 — PC가 고장 나면 기록이 사라지는 문제).

백업 폴더는 구글 드라이브 데스크톱이 동기화하는 폴더를 가리킨다(환경변수 `BACKUP_DIR`). 그 폴더에 파일을 쓰면
구글 드라이브가 알아서 올린다 — 구글 API는 쓰지 않는다. 드라이브가 파일의 예전 버전을 30일 보관하고, 여기서도
날짜별 사본을 KEEP_DAYS일 남겨 실수로 지운 기록을 되살릴 수 있게 한다.

백업이 실패해도 저장은 그대로 된다 — 경고만 남긴다 (드라이브가 잠깐 끊겨도 판단을 못 누르면 안 된다).
"""

from __future__ import annotations

import logging
import re
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger(__name__)

KEEP_DAYS = 30
DAILY_DIR = "날짜별"
KST = timezone(timedelta(hours=9))
_DATED_RE = re.compile(r"-(\d{4}-\d{2}-\d{2})\.[^.]+$")


def _kst_date(now: datetime) -> str:
    return (now.astimezone(KST) if now.tzinfo else now).strftime("%Y-%m-%d")


def backup_content(file_name: str, content: str, backup_dir: Path | None, now: datetime | None = None,
                   daily: bool = True) -> None:
    """방금 저장한 내용을 백업 폴더에 쓴다: 최신본(같은 이름) + 그날 날짜 사본(하루 동안 덮어씀).
    원본 파일을 다시 읽지 않고 저장한 내용을 그대로 받는다 — 복사 도중 다른 저장이 끼어들어도 반쯤 쓴 파일이 올라가지 않게.
    daily=False면 최신본만 쓴다 (판단기록.csv처럼 보기용 파일)."""
    if backup_dir is None:
        return
    now = now or datetime.now(timezone.utc)
    try:
        (backup_dir / DAILY_DIR).mkdir(parents=True, exist_ok=True)
        (backup_dir / file_name).write_text(content, encoding="utf-8")
        if not daily:
            return
        stem, dot, ext = file_name.rpartition(".")
        dated = f"{stem}-{_kst_date(now)}.{ext}" if dot else f"{file_name}-{_kst_date(now)}"
        (backup_dir / DAILY_DIR / dated).write_text(content, encoding="utf-8")
        _prune_daily(backup_dir / DAILY_DIR, now)
    except OSError as err:
        log.warning("백업 폴더에 쓰지 못했습니다 — 이 PC에는 저장됐습니다 (%s, %s): %s", backup_dir, file_name, err)


def _prune_daily(folder: Path, now: datetime) -> None:
    """날짜별 사본 중 KEEP_DAYS일보다 오래된 것을 지운다 (파일 이름의 날짜 기준)."""
    cutoff = _kst_date(now - timedelta(days=KEEP_DAYS))
    for f in folder.iterdir():
        m = _DATED_RE.search(f.name)
        if m and m.group(1) < cutoff:
            f.unlink(missing_ok=True)


def restore_if_missing(local: Path, backup_dir: Path | None) -> bool:
    """이 PC에 파일이 없고 백업 폴더에 있으면 백업을 가져온다 — 새 PC에 설치했거나 data 폴더가 지워졌을 때.
    이 PC에 파일이 있으면 건드리지 않는다 (이 PC 쪽이 항상 최신이다). 가져왔으면 True."""
    if backup_dir is None or local.exists():
        return False
    source = backup_dir / local.name
    try:
        if not source.exists() or source.stat().st_size == 0:
            return False
        local.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, local)
        log.info("이 PC에 기록이 없어 백업 폴더에서 가져왔습니다: %s → %s", source, local)
        return True
    except OSError as err:
        log.warning("백업 폴더에서 기록을 가져오지 못했습니다 (%s): %s", source, err)
        return False
