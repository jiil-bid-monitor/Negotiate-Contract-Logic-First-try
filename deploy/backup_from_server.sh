#!/usr/bin/env bash
# 클라우드 서버의 판단 기록 백업을 이 PC로 받아 온다 (Git Bash, 레포 폴더에서). 가끔(주 1회쯤) 돌리면 된다.
#
#   bash deploy/backup_from_server.sh <공인IP>
#
# 서버는 저장할 때마다 /home/jiil/backup에 최신본 + 날짜별 30일치를 남긴다(webapp/backup.py). 서버 디스크가
# 통째로 망가지는 경우에 대비해 이 PC로 한 부 더 받는다. .env의 BACKUP_DIR(구글 드라이브 폴더)이 있으면
# 그 아래 "클라우드서버" 폴더로, 없으면 레포 폴더의 server-backup/로.

set -euo pipefail
cd "$(dirname "$0")/.." || exit 1
IP="${1:-}"
[[ "$IP" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "서버 공인 IP를 주세요"; exit 1; }

BACKUP_DIR=""
if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi
BACKUP_DIR="${BACKUP_DIR//\\//}"   # G:\… → G:/…
DEST="${BACKUP_DIR:+$BACKUP_DIR/클라우드서버}"
DEST="${DEST:-server-backup}"
mkdir -p "$DEST"
scp -r -o StrictHostKeyChecking=accept-new "root@$IP:/home/jiil/backup/." "$DEST/"
echo "받은 곳: $DEST"
