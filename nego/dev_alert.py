"""개발자 방 알림 — 정기 보고 실행이 실패·회복했을 때 (2026-10-20, 팀원 수집기 `devAlert.ts` 방식).

담당자 방에는 실패 알림을 보내지 않는다(2026-10-12 결정 그대로). 개발자 방(TELEGRAM_DEV_CHAT_ID)에만:
  - 첫 실패: 무슨 종류의 실패인지 한 줄(`describe_failure`) + 마지막 오류 줄 + "N분 뒤 다시 시도"
  - 재시도 소진: "N번 모두 실패 — 이번 보고가 빠졌다"
  - 재시도 끝에 성공: "해결됨"
가운데 재시도 실패는 알리지 않는다(5번 재시도면 알림이 5개 온다). 팀원 수집기는 "해결됨"을 상태 파일로 가리지만,
여기선 재시도 회차(attempt)가 그 역할을 한다 — 1회차 실패 뒤의 성공만 회복이다.

    python -m nego.dev_alert --outcome failure --attempt 1 --max-attempts 5 --retry-minutes 60 \
        --mode "매일 입찰 보고" --log collect.log --run-url URL
알림이 실패해도 0으로 끝낸다(알림 때문에 워크플로 결과가 바뀌지 않게).
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

# (원인 한 줄, 오류 원문에서 찾을 표현) — 위에서부터 먼저 맞는 것. 텔레그램·메일을 먼저 본다 —
# 텔레그램 오류도 "HTTP 403"이라 나라장터 키 거절로 잘못 읽힌다.
_CAUSES = [
    ("텔레그램 발송 실패", re.compile(r"sendMessage|sendDocument|텔레그램|telegram", re.I)),
    ("이메일 발송 실패", re.compile(r"SMTP|메일", re.I)),
    ("나라장터 조회 키 거절 — 키 확인 필요",
     re.compile(r"HTTP 40[13]|SERVICE_?KEY|SERVICE KEY|인증키|등록되지 않은|NARA_SERVICE_KEY 환경변수", re.I)),
    # 2026-10-06·07: GitHub 서버(미국)→나라장터 접속이 몇 시간 안 됨. 대개 저절로 풀린다(팀원 수집기 실측)
    ("나라장터 접속 실패 — 대개 일시적",
     re.compile(r"연결 실패|ConnectionError|ConnectTimeout|ReadTimeout|timed?\s?out|Max retries|ECONN|"
                r"RemoteDisconnected|재시도 소진|HTTP 5\d\d", re.I)),
]

_ERROR_LINE = re.compile(r" ERROR |Error|Traceback|조회 실패|실패:")


def describe_failure(text: str) -> str:
    """실패 원인 한 줄 — 오류 원문 위에 붙여 무슨 종류의 실패인지 바로 보이게."""
    for label, pattern in _CAUSES:
        if pattern.search(text or ""):
            return label
    return "예상하지 못한 오류"


def last_errors(log_text: str, count: int = 5, width: int = 300) -> str:
    lines = [line[:width] for line in (log_text or "").splitlines() if _ERROR_LINE.search(line)]
    return "\n".join(lines[-count:]) or "(오류 줄 없음 — Actions 로그 확인)"


def build_text(outcome: str, attempt: int, max_attempts: int, retry_minutes: int, mode: str,
               log_text: str = "", run_url: str = "") -> str | None:
    """보낼 글. 알리지 않을 경우(가운데 재시도 실패, 첫 시도 성공)는 None."""
    tail = f"\n\n로그: {run_url}" if run_url else ""
    if outcome == "success":
        if attempt <= 1:
            return None
        return f"[개발자 알림] 해결됨 — {mode}\n{attempt}번째 시도에서 성공했습니다.{tail}"
    errors = last_errors(log_text)
    cause = describe_failure(errors)
    if attempt >= max_attempts:
        waited = (attempt - 1) * retry_minutes
        head = (f"[개발자 알림] {mode} — {attempt}번 모두 실패\n원인: {cause}\n"
                f"첫 시도 후 약 {waited}분 동안 다시 시도했지만 실패해, 이번 보고는 담당자 방에 나가지 않았습니다.")
    elif attempt == 1:
        head = (f"[개발자 알림] {mode} 실패\n원인: {cause}\n"
                f"{retry_minutes}분 뒤 다시 시도합니다(최대 {max_attempts}번). 담당자 방엔 알리지 않습니다.")
    else:
        return None
    return f"{head}\n\n마지막 오류:\n{errors}{tail}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="nego.dev_alert")
    parser.add_argument("--outcome", choices=("success", "failure"), required=True)
    parser.add_argument("--attempt", type=int, default=1)
    parser.add_argument("--max-attempts", type=int, default=5)
    parser.add_argument("--retry-minutes", type=int, default=60)
    parser.add_argument("--mode", default="정기 보고")
    parser.add_argument("--log", help="수집 실행 로그 파일 (마지막 오류 줄을 붙인다)")
    parser.add_argument("--run-url", default="")
    args = parser.parse_args(argv)

    log_text = ""
    if args.log and Path(args.log).exists():
        log_text = Path(args.log).read_text(encoding="utf-8", errors="replace")
    text = build_text(args.outcome, args.attempt, args.max_attempts, args.retry_minutes, args.mode,
                      log_text, args.run_url)
    if text is None:
        print("이번엔 개발자 알림을 보내지 않습니다 (첫 시도 성공 또는 가운데 재시도 실패).")
        return 0
    from .telegram import send_dev_alert

    errors = send_dev_alert(text)
    if errors is None:
        print("TELEGRAM_DEV_CHAT_ID 없음 → 개발자 알림 건너뜀")
    elif errors:
        for err in errors:
            print(f"::warning::개발자 알림 실패 {err}")
    else:
        print("개발자 알림 발송")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
