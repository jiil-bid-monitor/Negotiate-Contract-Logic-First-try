#!/usr/bin/env bash
# 입찰 공고 싱크로율 탐색기 — 서버를 한 줄로 켠다 (Git Bash).
#
#   bash webapp/start.sh
#
# - 처음 한 번은 서비스키를 물어보고 레포 루트 .env에 저장한다(.env는 깃에 안 올라감).
# - 8765 포트에 예전 서버가 남아 있으면 정리하고 켠다.
# - 켜진 뒤 실제로 접속되는지 확인해서 "접속 확인됨"과 팀원에게 줄 주소를 보여준다.
# - 서버가 오류로 꺼지면 5초 뒤 자동으로 다시 켠다. 끝낼 때는 Ctrl+C.

cd "$(dirname "$0")/.." || exit 1
ENV_FILE=".env"

# ── 설정 ────────────────────────────────────────────────
if [ ! -f "$ENV_FILE" ]; then
  echo "처음 실행입니다. 공공데이터포털 인증키(Decoding)를 붙여넣고 Enter (화면엔 안 보임):"
  read -rs KEY
  echo
  if [ -z "$KEY" ]; then echo "인증키가 비어 있어 끝냅니다."; exit 1; fi
  {
    echo "# 서버 설정 — webapp/start.sh가 읽는다. 깃에 올라가지 않는다."
    echo "NARA_SERVICE_KEY='$KEY'"
    echo "HOST='0.0.0.0'"
    echo "PORT='8765'"
    echo "APP_TOKEN='jiil2026'"
    echo "# AI 판단을 쓸 때만: ANTHROPIC_API_KEY='...'"
  } > "$ENV_FILE"
  echo "설정을 $ENV_FILE 에 저장했습니다 (서비스키를 바꾸려면 이 파일을 메모장으로 고치면 됨)."
fi
set -a
# shellcheck disable=SC1090
. "./$ENV_FILE"
set +a
export PYTHONUTF8=1
HOST="${HOST:-0.0.0.0}"; PORT="${PORT:-8765}"; APP_TOKEN="${APP_TOKEN:-jiil2026}"
export HOST PORT APP_TOKEN

if [ -z "$NARA_SERVICE_KEY" ]; then
  echo "경고: $ENV_FILE 에 NARA_SERVICE_KEY가 없습니다 — 화면은 뜨지만 불러오기는 실패합니다."
fi

PY="${PYTHON:-}"
if [ -z "$PY" ]; then
  if command -v py >/dev/null 2>&1; then PY=py; else PY=python; fi
fi

# ── 예전 서버 정리 ──────────────────────────────────────
port_pids() {
  if command -v netstat >/dev/null 2>&1; then
    netstat -ano 2>/dev/null | grep -E "[:.]$PORT[[:space:]].*LISTEN" | awk '{print $NF}' | sort -u
  fi
}
stop_old() {
  for pid in $(port_pids); do
    [ -n "$pid" ] && [ "$pid" != "0" ] || continue
    echo "8765 포트를 쓰던 예전 서버(PID $pid)를 정리합니다."
    if command -v taskkill >/dev/null 2>&1; then
      taskkill //PID "$pid" //F >/dev/null 2>&1
    else
      kill "$pid" 2>/dev/null
    fi
  done
}

lan_ip() {
  "$PY" - <<'EOF' 2>/dev/null
import socket
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
try:
    s.connect(("192.168.0.1", 80))
    print(s.getsockname()[0])
except OSError:
    print("이PC의IP")
finally:
    s.close()
EOF
}

check_up() {
  # 포트가 응답해도 그게 방금 켠 서버인지 확인한다 — 다른 프로그램이 포트를 쥐고 있으면
  # 우리 서버는 켜지다 죽는데, 남의 응답을 보고 "접속 확인됨"이라고 하면 안 된다.
  for _ in $(seq 1 40); do
    kill -0 "$SERVER_PID" 2>/dev/null || return 1
    if curl -s -m 2 -o /dev/null "http://127.0.0.1:$PORT/"; then
      sleep 1
      kill -0 "$SERVER_PID" 2>/dev/null && return 0
      return 1
    fi
    sleep 0.5
  done
  return 1
}

STOPPING=0
on_stop() {
  STOPPING=1
  echo
  echo "서버를 끕니다."
  kill "$SERVER_PID" 2>/dev/null
  stop_old
  exit 0
}
trap on_stop INT TERM

stop_old
IP="$(lan_ip)"
while true; do
  "$PY" -u -m webapp &
  SERVER_PID=$!
  if check_up; then
    echo
    echo "=================================================================="
    echo "  접속 확인됨. 팀원에게 줄 주소:  http://$IP:$PORT/?t=$APP_TOKEN"
    echo "  이 창은 닫지 말고 최소화해 두세요. 끝낼 때는 Ctrl+C."
    echo "=================================================================="
    echo
  else
    echo "!! 서버가 응답하지 않습니다 — 위의 오류 메시지를 확인하세요."
    echo "   ('Address already in use' / '액세스 권한' 오류면 8765 포트를 다른 프로그램이 쓰는 중입니다.)"
  fi
  wait "$SERVER_PID"
  code=$?
  [ "$STOPPING" = 1 ] && exit 0
  echo "!! $(date '+%H:%M:%S') 서버가 꺼졌습니다(종료코드 $code). 5초 뒤 다시 켭니다... (그만두려면 Ctrl+C)"
  sleep 5
  stop_old
done
