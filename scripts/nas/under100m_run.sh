#!/bin/sh
# NAS 1억 미만 컨테이너 실행 스크립트 — 프로그램이 완성되기 전까지는 대기만 한다.
# 공유 폴더 jiil-webapp/under100m/run.sh가 있으면 일정 간격(기본 1시간)마다 실행한다. 실패해도 다음 차례에 다시.
# 프로그램은 회사 정보가 들어 있어 이 공개 레포에 넣지 않는다.
JOB="${UNDER100M_JOB:-/work/under100m/run.sh}"
INTERVAL="${UNDER100M_INTERVAL_SEC:-3600}"
pid=""
trap '[ -n "$pid" ] && kill "$pid" 2>/dev/null; exit 0' TERM INT

while :; do
  if [ -f "$JOB" ]; then
    sh "$JOB" &
    pid=$!
    wait "$pid" || echo "1억 미만 작업 실패(종료 코드 $?) — 다음 차례에 다시 실행"
  fi
  sleep "$INTERVAL" &
  pid=$!
  wait "$pid"
done
