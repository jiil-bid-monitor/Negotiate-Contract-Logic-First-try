"""싱크로율은 수집(nego)과 메일 리포트도 쓰므로 `nego/sync.py`로 옮겼다(2026-10-19 통합). 예전 경로 호환용."""

from nego.sync import BORDER, HIGH, PastIndex, _is_task_line, default_jiil_repo, level  # noqa: F401
