#!/usr/bin/env bash
# 클라우드 서버 코드 업데이트 — PC에서 서버 PC 때의 "Ctrl+C → git pull → start.sh" 대신:
#
#   ssh root@<공인IP> bash /home/jiil/negotiate-contract-logic-first-try/deploy/update_server.sh
#
# 최신 main을 받고 패키지를 맞춘 뒤 웹앱을 다시 켠다. 판단 기록·.env는 그대로. 팀원은 F5.

set -euo pipefail
APP=/home/jiil/negotiate-contract-logic-first-try
[ "$(id -u)" = 0 ] || { echo "root로 실행하세요"; exit 1; }
sudo -u jiil -H git -C "$APP" pull --ff-only
sudo -u jiil -H /home/jiil/venv/bin/pip install -q -r "$APP/requirements.txt"
systemctl restart jiil-webapp
sleep 3
systemctl is-active --quiet jiil-webapp && echo "업데이트 끝: $(sudo -u jiil git -C "$APP" log -1 --format='%h %s')" \
  || { journalctl -u jiil-webapp -n 30 --no-pager; exit 1; }
