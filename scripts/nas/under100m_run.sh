#!/bin/sh
# NAS 1억 미만 컨테이너 실행 스크립트 — 프로그램이 완성되기 전까지는 대기만 한다.
# 공유 폴더 jiil-webapp/under100m/run.sh가 있으면 일정 간격(기본 1시간)마다 실행한다. 실패해도 다음 차례에 다시.
# run.sh가 있어야 시작하므로, 공유 폴더에 넣고 빼는 것으로 켜고 끈다. 내용은 scripts/nas/under100m_job.sh 참고.
JOB="${UNDER100M_JOB:-/work/under100m/run.sh}"
INTERVAL="${UNDER100M_INTERVAL_SEC:-3600}"
pid=""
trap '[ -n "$pid" ] && kill "$pid" 2>/dev/null; exit 0' TERM INT

while :; do
  date "+%Y-%m-%d %H:%M" > /work/under100m/.alive 2>/dev/null  # 대기 중에도 컨테이너가 살아 있는지 바깥에서 확인용
  # NAS가 바쁘면(CPU 60%·RAM 75% 넘음 — webapp/nasload.py) 이번 회차는 건너뛰고 다음 시간에 다시.
  # 토요일 03시~일요일 03시는 NAS HyperBackup 시간 — 실행 안 함(2026-11-09 부장님 요청). %u: 토 6·일 7
  now="$(date +%u%H)"
  if [ -f "$JOB" ] && ! { [ "$now" -ge 603 ] && [ "$now" -lt 703 ]; } && python -m webapp.nasload; then
    sh "$JOB" &
    pid=$!
    wait "$pid" || echo "1억 미만 작업 실패(종료 코드 $?) — 다음 차례에 다시 실행"
  fi
  sleep "$INTERVAL" &
  pid=$!
  wait "$pid"
done
