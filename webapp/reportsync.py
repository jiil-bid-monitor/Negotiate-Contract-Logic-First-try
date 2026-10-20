"""보고 기록 → 판단 기록 (2026-10-20, 팀원 레포 10/19 '4단계' 이식).

매일·주간 텔레그램 보고에 나간 공고를 판단 기록에 '미판단'으로 쌓는다. 보고는 GitHub Actions에서 돌아 이 PC에
직접 쓸 수 없으니, 보고가 상태 전용 브랜치 `bot-state`의 `reports/`에 남긴 목록(`nego/report_log.py`)을
몇 분마다 받아 합친다. 받아오기(git fetch)는 원격 기록(refs/remotes/origin/bot-state)만 갱신하고 작업 폴더·브랜치는
건드리지 않는다. PC가 꺼져 있던 동안의 보고는 켜진 뒤 한꺼번에 들어온다. 이미 판단한 공고는 판단을 그대로 둔다.
"""

from __future__ import annotations

import logging
import re
import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable

from nego import report_log

log = logging.getLogger(__name__)

REPO = Path(__file__).resolve().parents[1]
REF = "refs/remotes/origin/bot-state"
POLL_SECONDS = 5 * 60
FIRST_DELAY_SECONDS = 15
_TREE_LINE = re.compile(r"^\d+ blob ([0-9a-f]+)\t(reports/[^/]+\.json)$")


def _git(*args: str) -> str:
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0  # 창이 잠깐씩 뜨지 않게
    done = subprocess.run(["git", *args], cwd=REPO, capture_output=True, timeout=60, creationflags=flags, check=True)
    return done.stdout.decode("utf-8", "replace")


class ReportSync:
    """bot-state의 보고 기록을 판단 기록에 합친다. 이미 합친 파일(git blob 해시)은 다시 읽지 않는다 —
    다시 합쳐도 결과는 같다(`report_log.merge`)."""

    def __init__(self, merge: Callable[[dict], int], git: Callable[..., str] = _git):
        self._merge = merge
        self._git = git
        self._seen: set[str] = set()
        self._last_error = ""

    def pull(self) -> int:
        """한 번 받아 합친다. 새로 생긴 공고 수를 돌려준다. 실패하면(인터넷 끊김, 첫 보고 전이라 브랜치 없음 등) 0 —
        같은 오류는 5분마다 되풀이해 남기지 않는다."""
        try:
            self._git("fetch", "--quiet", "origin", f"+refs/heads/bot-state:{REF}")
            added = 0
            for line in self._git("ls-tree", REF, "reports/").splitlines():
                m = _TREE_LINE.match(line.strip())
                if not m or m.group(1) in self._seen:
                    continue
                entry = report_log.parse(self._git("cat-file", "-p", m.group(1)))
                self._seen.add(m.group(1))
                if entry:
                    added += self._merge(entry)
            if added:
                log.info("보고된 공고 %d건을 판단 기록에 '미판단'으로 쌓았습니다", added)
            self._last_error = ""
            return added
        except (OSError, subprocess.SubprocessError) as err:
            stderr = getattr(err, "stderr", b"") or b""
            msg = (stderr.decode("utf-8", "replace").strip() or str(err)).splitlines()[0]
            if msg != self._last_error:
                log.warning("보고 기록을 받아오지 못했습니다 (다음에 다시 시도): %s", msg)
            self._last_error = msg
            return 0

    def start(self) -> None:
        """서버가 뜨고 잠시 뒤 한 번, 그 뒤로 POLL_SECONDS마다 (데몬 스레드 — 서버를 끄면 같이 끝난다)."""
        stop = threading.Event()

        def loop() -> None:
            if stop.wait(FIRST_DELAY_SECONDS):
                return
            while True:
                self.pull()
                if stop.wait(POLL_SECONDS):
                    return

        threading.Thread(target=loop, name="report-sync", daemon=True).start()
