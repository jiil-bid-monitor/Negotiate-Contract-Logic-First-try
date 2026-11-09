"""NAS가 바쁠 때는 새 작업을 시작하지 않는다(2026-11-09 부장님 요청 — 전체 CPU 60% 넘거나 RAM 75% 넘으면 건너뜀).

컨테이너 안의 /proc/stat·/proc/meminfo는 NAS 전체 값이다. CPU에는 I/O 대기도 넣는다(백업 때 대부분이 I/O 대기였음).
RAM은 DSM '이용'처럼 캐시를 뺀 값(MemAvailable 기준). 시작할 때만 보고, 돌고 있는 작업은 멈추지 않는다.

    python -m webapp.nasload   # 바쁘면 이유를 찍고 종료 코드 1
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

CPU_LIMIT = 60
RAM_LIMIT = 75


def _cpu_times(stat: Path) -> tuple[int, int]:
    """(쉬는 시간, 전체 시간) — 첫 줄 cpu: user nice system idle iowait irq softirq steal …"""
    values = [int(v) for v in stat.read_text().splitlines()[0].split()[1:9]]
    return values[3], sum(values)


def cpu_percent(stat: Path = Path("/proc/stat"), seconds: float = 2.0) -> float:
    idle1, total1 = _cpu_times(stat)
    time.sleep(seconds)
    idle2, total2 = _cpu_times(stat)
    return 100.0 * (1 - (idle2 - idle1) / max(1, total2 - total1))


def ram_percent(meminfo: Path = Path("/proc/meminfo")) -> float:
    info = {line.split(":")[0]: int(line.split()[1]) for line in meminfo.read_text().splitlines() if ":" in line}
    return 100.0 * (1 - info["MemAvailable"] / info["MemTotal"])


def busy_reason(seconds: float = 2.0) -> str | None:
    """바쁘면 사람이 읽는 이유, 아니면 None. 값을 못 읽으면 None(막지 않음)."""
    try:
        cpu, ram = cpu_percent(seconds=seconds), ram_percent()
    except (OSError, ValueError, KeyError, IndexError):
        return None
    if cpu > CPU_LIMIT or ram > RAM_LIMIT:
        return f"NAS 사용량이 높아 이번엔 건너뜀 (CPU {cpu:.0f}% · RAM {ram:.0f}%, 기준 CPU {CPU_LIMIT}% · RAM {RAM_LIMIT}%)"
    return None


if __name__ == "__main__":
    reason = busy_reason(seconds=5)
    if reason:
        print(reason)
    sys.exit(1 if reason else 0)
