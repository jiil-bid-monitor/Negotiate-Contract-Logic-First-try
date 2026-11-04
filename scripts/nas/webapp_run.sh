#!/bin/sh
# NAS 웹앱 컨테이너 실행 스크립트 — 관리자 없이 웹앱을 다시 켤 수 있게 한다(코드 업데이트 뒤).
# 요청 파일(공유 폴더 jiil-webapp/control/restart-webapp)이 생기면 지우고 웹앱만 다시 켠다. 내용은 안 본다.
# requirements.txt가 바뀌면 이미지를 다시 만들어야 하므로 이걸로는 안 됨.
CONTROL="${CONTROL_DIR:-/work/control}"
REQUEST="$CONTROL/restart-webapp"
pid=""
trap '[ -n "$pid" ] && kill "$pid" 2>/dev/null; exit 0' TERM INT
mkdir -p "$CONTROL"

while :; do
  python -m webapp &
  pid=$!
  while kill -0 "$pid" 2>/dev/null; do
    # 못 지우면 다시 켜지 않음 — 파일이 남아 재시작을 되풀이하지 않게
    if [ -f "$REQUEST" ] && rm -f "$REQUEST"; then
      echo "재시작 요청 — 웹앱을 다시 켭니다"
      kill "$pid" 2>/dev/null
      break
    fi
    sleep 5
  done
  wait "$pid"
  sleep 3
done
