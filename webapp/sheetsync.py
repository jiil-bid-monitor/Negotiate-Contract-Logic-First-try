"""판단 기록 → 피드백 구글 시트 S~V열 (2026-10-21 사용자 결정 — 팀이 시트 하나로 공고와 판단을 같이 보게).

보고(GitHub Actions)가 A~R열에 쌓은 공고 줄에, 웹앱에서 고른 판단(참가·보류·불참·미판단·놓침)·사유·판단자·판단일을
옮겨 쓴다(`nego.feedback_sheet.write_verdicts`). 판단 기록이 바뀌면(`TeamStore.version`) CHECK_SECONDS 안에, 바뀐 게 없어도
RESYNC_SECONDS마다 한 번(그사이 Actions가 새 줄을 붙였거나 '놓침'으로 넘어간 공고) 맞춘다.
시트가 안 되면(인터넷·권한) 판단 저장은 그대로 되고 경고만 남긴다 — 같은 오류는 되풀이해 남기지 않는다.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable

from nego import feedback_sheet

log = logging.getLogger(__name__)

CHECK_SECONDS = 60
RESYNC_SECONDS = 30 * 60
FIRST_DELAY_SECONDS = 20


class SheetSync:
    def __init__(self, snapshot: Callable[[], tuple[int, dict[str, Any]]],
                 write: Callable[..., dict[str, int] | None] = feedback_sheet.write_verdicts,
                 clock: Callable[[], float] = time.monotonic):
        self._snapshot = snapshot
        self._write = write
        self._clock = clock
        self._done_version: int | None = None
        self._done_at = float("-inf")
        self._prepared = False
        self._last_error = ""

    def tick(self) -> dict[str, int] | None:
        """바뀐 게 있거나 RESYNC_SECONDS가 지났으면 한 번 맞춘다. 맞췄으면 결과, 안 했거나 실패면 None."""
        version, records = self._snapshot()
        if version == self._done_version and self._clock() - self._done_at < RESYNC_SECONDS:
            return None
        try:
            result = self._write(records, datetime.now(timezone.utc), prepare=not self._prepared)
        except (feedback_sheet.SheetsError, OSError, ValueError) as err:
            msg = str(err).splitlines()[0][:300]
            if msg != self._last_error:
                log.warning("피드백 시트에 판단을 쓰지 못했습니다 (다음에 다시 시도): %s", msg)
            self._last_error = msg
            return None
        except Exception as err:  # noqa: BLE001 — requests 오류 등. 스레드가 죽으면 시트가 영영 안 맞는다
            msg = f"{type(err).__name__}: {err}"[:300]
            if msg != self._last_error:
                log.warning("피드백 시트에 판단을 쓰지 못했습니다 (다음에 다시 시도): %s", msg)
            self._last_error = msg
            return None
        self._prepared = True
        self._done_version, self._done_at, self._last_error = version, self._clock(), ""
        if result and result.get("updated"):
            log.info("피드백 시트에 판단 %d줄을 옮겨 썼습니다", result["updated"])
        return result

    def start(self) -> None:
        """서버가 뜨고 잠시 뒤부터 CHECK_SECONDS마다 (데몬 스레드 — 서버를 끄면 같이 끝난다)."""
        stop = threading.Event()

        def loop() -> None:
            if stop.wait(FIRST_DELAY_SECONDS):
                return
            while True:
                self.tick()
                if stop.wait(CHECK_SECONDS):
                    return

        threading.Thread(target=loop, name="sheet-sync", daemon=True).start()
