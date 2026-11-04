"""수집이 끝나면 웹앱을 다시 켜 메모리를 비운다 — NAS에서만 (2026-11-04 사용자 요청).

NAS 실측: 수집 뒤 RAM 46%가 내려가지 않다가 다시 켜니 25%. 수집 결과는 디스크에 있어 잃는 것이 없다.
`scripts/nas/run_with_env.py`가 WEBAPP_RESTART_FILE을 줄 때만 켜지고(서버 PC는 꺼짐), 수집·세부품명 확인·
정량평가서 작업이 모두 끝나 IDLE_SECONDS 동안 조용하면 그 파일을 만든다 — `scripts/nas/webapp_run.sh`가 보고 다시 켠다.
같은 폴더 webapp-memory.txt에 메모리 제한·사용량을 남긴다(관리자 화면 없이 확인, 재시작 직전 값은 webapp-memory-수집후.txt).
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

POLL_SECONDS = 15
IDLE_SECONDS = 60  # 화면이 수집 결과를 받아 갈 여유 + 새 수집을 바로 누르는 경우
MEMORY_SECONDS = 60
NO_LIMIT = 2 ** 60  # cgroup v1은 제한이 없으면 아주 큰 수를 준다

CGROUP = Path("/sys/fs/cgroup")
# (제한, 현재, 최고) — cgroup v2, v1 순서
CGROUP_FILES = (("memory.max", "memory.current", "memory.peak"),
                ("memory/memory.limit_in_bytes", "memory/memory.usage_in_bytes", "memory/memory.max_usage_in_bytes"))


def _read_bytes(path: Path) -> int | None:
    try:
        return int(path.read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        return None


def _gb(value: int | None) -> str:
    return "확인 안 됨" if value is None else f"{value / 1024 ** 3:.2f}GB"


def memory_report(cgroup: Path = CGROUP, proc_status: Path = Path("/proc/self/status")) -> str:
    """컨테이너 메모리 제한·사용량(디스크 캐시 포함)과 웹앱 자체 사용량을 사람이 읽는 글로."""
    limit_text = "확인 안 됨"
    current = peak = None
    for limit_name, current_name, peak_name in CGROUP_FILES:
        try:
            raw = (cgroup / limit_name).read_text(encoding="ascii").strip()
        except OSError:
            continue
        if raw == "max" or (raw.isdigit() and int(raw) >= NO_LIMIT):
            limit_text = "제한 없음"
        elif raw.isdigit():
            limit_text = _gb(int(raw))
        current = _read_bytes(cgroup / current_name)
        peak = _read_bytes(cgroup / peak_name)
        break
    rss = rss_peak = None
    try:
        for line in proc_status.read_text(encoding="ascii", errors="replace").splitlines():
            key, _, rest = line.partition(":")
            if key in ("VmRSS", "VmHWM") and rest.split():
                value = int(rest.split()[0]) * 1024
                if key == "VmRSS":
                    rss = value
                else:
                    rss_peak = value
    except OSError:
        pass
    return (f"컨테이너 메모리 제한: {limit_text}\n"
            f"컨테이너 사용량(디스크 캐시 포함): 현재 {_gb(current)} · 최고 {_gb(peak)}\n"
            f"웹앱 자체: 현재 {_gb(rss)} · 최고 {_gb(rss_peak)}\n")


class RestartAfterCollect:
    def __init__(self, request_file: Path, collected: Callable[[], bool], busy: Callable[[], bool],
                 report: Callable[[], str] = memory_report):
        self.request_file = request_file
        self.collected = collected
        self.busy = busy
        self.report = report
        self.memory_file = request_file.with_name("webapp-memory.txt")
        self.requested = False
        self._idle_since: float | None = None
        self._memory_at: float | None = None

    def write_memory(self, now: float) -> None:
        if self._memory_at is not None and now - self._memory_at < MEMORY_SECONDS:
            return
        self._memory_at = now
        try:
            self.memory_file.write_text(self.report(), encoding="utf-8")
        except OSError as err:
            log.warning("메모리 기록 실패: %s", err)

    def check(self, now: float) -> bool:
        """이번에 재시작을 요청했으면 True."""
        self.write_memory(now)
        if self.requested:
            return False
        if not self.collected() or self.busy():
            self._idle_since = None
            return False
        if self._idle_since is None:
            self._idle_since = now
        if now - self._idle_since < IDLE_SECONDS:
            return False
        try:
            self.request_file.with_name("webapp-memory-수집후.txt").write_text(self.report(), encoding="utf-8")
            self.request_file.write_text("", encoding="utf-8")
        except OSError as err:
            log.warning("재시작 요청 실패 — 다음 확인 때 다시: %s", err)
            return False
        self.requested = True
        log.info("수집 뒤 메모리를 비우려고 웹앱을 다시 켭니다")
        return True

    def start(self) -> None:
        def loop() -> None:
            while True:
                try:
                    self.check(time.monotonic())
                except Exception:  # noqa: BLE001 — 재시작 확인이 웹앱을 멈추면 안 된다
                    log.exception("재시작 확인 실패")
                time.sleep(POLL_SECONDS)

        threading.Thread(target=loop, name="restart-after-collect", daemon=True).start()
