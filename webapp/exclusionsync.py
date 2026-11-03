"""예외 낱말 판단 파일 지켜보기 (2026-11-03 사용자 결정 — 파일을 주고받지 않게).

이 PC 다운로드 폴더(`EXCLUSION_INBOX`, 없으면 ~/Downloads)에 예외 낱말 후보 HTML의 '제출' 파일
(`예외낱말_판단_*.json`)이 새로 생기면 `python -m nego.exclusion_apply`를 따로 띄워 반영한다(반영·병합·개발자 방 알림은
그쪽에서). 한 번 처리한 파일(내용 해시, `data/exclusion_applied.json`)은 다시 안 함 — 체크를 고쳐 다시 제출하면
내용이 달라져 새로 반영되고, 이미 반영한 판단은 건너뛴다. 한 번에 하나씩.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from nego import exclusion_apply

log = logging.getLogger(__name__)

REPO = Path(__file__).resolve().parents[1]
PATTERN = "예외낱말_판단_*.json"
POLL_SECONDS = 60
SETTLE_SECONDS = 5  # 내려받는 중인 파일은 건드리지 않게


def inbox() -> Path:
    return Path(os.environ.get("EXCLUSION_INBOX") or Path.home() / "Downloads")


def pending(folder: Path, done: dict, now: float | None = None) -> list[Path]:
    """아직 처리 안 한 판단 파일(오래된 것부터)."""
    now = time.time() if now is None else now
    out = []
    for path in sorted(folder.glob(PATTERN), key=lambda p: p.stat().st_mtime):
        if now - path.stat().st_mtime < SETTLE_SECONDS:
            continue
        try:
            if exclusion_apply.file_hash(path) not in done:
                out.append(path)
        except OSError:
            continue
    return out


class ExclusionSync:
    def __init__(self, folder: Path | None = None):
        self.folder = folder or inbox()

    def check(self) -> int:
        if not self.folder.is_dir():
            return 0
        files = pending(self.folder, exclusion_apply.load_applied())
        for path in files:
            log.info("예외 낱말 판단 파일 반영 시작: %s", path.name)
            flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            done = subprocess.run([sys.executable, "-m", "nego.exclusion_apply", str(path)], cwd=REPO,
                                  capture_output=True, creationflags=flags, timeout=3600,
                                  env=dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8"))
            out = (done.stdout + done.stderr).decode("utf-8", "replace").strip()
            (log.info if done.returncode == 0 else log.warning)("예외 낱말 반영 %s: %s", path.name, out[-400:])
        return len(files)

    def start(self) -> None:
        def loop() -> None:
            while True:
                try:
                    self.check()
                except Exception as err:  # noqa: BLE001 — 지켜보기는 서버를 멈추지 않는다
                    log.warning("예외 낱말 판단 파일 확인 실패: %s", err)
                time.sleep(POLL_SECONDS)

        threading.Thread(target=loop, name="exclusion-sync", daemon=True).start()
