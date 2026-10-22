#!/usr/bin/env bash
# 클라우드 서버(우분투) 설치 — 웹앱을 24시간 켜 두고 https로 연다. 안내: docs/cloud_server_setup.md
#
#   bash setup_server.sh <공인IP> [jiil-upload.tgz]
#
# 보통은 PC에서 `bash deploy/upload_from_pc.sh <공인IP>`가 이 파일과 묶음(판단 기록·과거 실적·키)을 올리고 부른다.
# 여러 번 돌려도 된다: 이미 있는 판단 기록·.env(접속 토큰)는 그대로 두고 코드·패키지·서비스만 다시 맞춘다.
#
# 놓는 곳 (사용자 jiil):
#   /home/jiil/negotiate-contract-logic-first-try   이 레포 (data/ = 판단 기록, .env)
#   /home/jiil/jiil-past-contracts                  과거 실적 (레포 옆 — JIIL_REPO 기본 경로)
#   /home/jiil/jiil-keys/keys.json                  구글 서비스 계정 키
#   /home/jiil/backup                               판단 기록 날짜별 사본 30일 (webapp/backup.py)
# 웹앱은 127.0.0.1:8765에서만 듣고, 바깥은 Caddy가 https(443)로 받아 넘긴다. 인증서는 Let's Encrypt 자동.

set -euo pipefail

IP="${1:-}"
BUNDLE="${2:-}"
REPO_URL="https://github.com/jiil-bid-monitor/negotiate-contract-logic-first-try"
USER_NAME=jiil
HOME_DIR=/home/$USER_NAME
APP=$HOME_DIR/negotiate-contract-logic-first-try
VENV=$HOME_DIR/venv
PORT=8765

say() { printf '\n== %s\n' "$*"; }
die() { printf '\n!! %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" = 0 ] || die "root로 실행하세요 (sudo bash setup_server.sh …)"
[[ "$IP" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "첫 값으로 서버 공인 IP를 주세요 — 예: bash setup_server.sh 110.165.1.2"
command -v apt-get >/dev/null || die "우분투 서버에서만 됩니다 (apt-get 없음)"
DOMAIN="${IP//./-}.sslip.io"   # 도메인을 사지 않아도 https 인증서를 받을 수 있는 주소 (sslip.io가 IP로 풀어 줌)

say "시간대·스왑"
timedatectl set-timezone Asia/Seoul || true
if ! swapon --show | grep -q .; then
  # 메모리 1GB 서버 — 패키지 설치·큰 PDF를 읽을 때 모자라지 않게 2GB 스왑
  fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile >/dev/null && swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

say "패키지 설치 (몇 분 걸림)"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q git curl ca-certificates gnupg python3 python3-venv python3-pip debian-keyring debian-archive-keyring apt-transport-https
PY=python3
if ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))'; then
  # 우분투 20.04는 파이썬 3.8 — 코드가 3.10 이상을 씀
  apt-get install -y -q software-properties-common
  add-apt-repository -y ppa:deadsnakes/ppa
  apt-get update -q
  apt-get install -y -q python3.11 python3.11-venv
  PY=python3.11
fi
if ! command -v caddy >/dev/null; then
  curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/gpg.key | gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -q
  apt-get install -y -q caddy
fi

say "사용자·코드"
id "$USER_NAME" >/dev/null 2>&1 || useradd -m -s /bin/bash "$USER_NAME"
as_user() { sudo -u "$USER_NAME" -H "$@"; }
if [ -d "$APP/.git" ]; then
  as_user git -C "$APP" pull --ff-only
else
  as_user git clone --depth 50 "$REPO_URL" "$APP"
fi
as_user mkdir -p "$APP/data" "$HOME_DIR/jiil-keys" "$HOME_DIR/backup"
chmod 700 "$HOME_DIR/jiil-keys"

if [ -n "$BUNDLE" ]; then
  say "PC에서 올린 묶음 풀기"
  [ -f "$BUNDLE" ] || die "묶음 파일이 없습니다: $BUNDLE"
  TMP=$(mktemp -d)
  tar -xzf "$BUNDLE" -C "$TMP"
  if [ -f "$APP/data/participation.json" ] && [ "${FORCE_DATA:-0}" != 1 ]; then
    echo "서버에 판단 기록이 이미 있어 그대로 둡니다 (PC 기록으로 덮으려면 FORCE_DATA=1)."
  else
    for f in participation.json promoted.json webapp.sqlite3 doc_products.json; do
      [ -f "$TMP/data/$f" ] && install -o "$USER_NAME" -g "$USER_NAME" -m 600 "$TMP/data/$f" "$APP/data/$f" && echo "  판단 기록: $f"
    done
  fi
  if [ -d "$TMP/jiil-past-contracts" ]; then
    rm -rf "$HOME_DIR/jiil-past-contracts"
    mv "$TMP/jiil-past-contracts" "$HOME_DIR/jiil-past-contracts"
    chown -R "$USER_NAME:$USER_NAME" "$HOME_DIR/jiil-past-contracts"
    echo "  과거 실적: $HOME_DIR/jiil-past-contracts"
  fi
  if [ -f "$TMP/keys.json" ]; then
    install -o "$USER_NAME" -g "$USER_NAME" -m 600 "$TMP/keys.json" "$HOME_DIR/jiil-keys/keys.json"
    echo "  구글 키: $HOME_DIR/jiil-keys/keys.json"
  fi
  [ -f "$TMP/server.env" ] && cp "$TMP/server.env" /root/jiil-server.env
  rm -rf "$TMP"
  rm -f "$BUNDLE"   # 키가 든 묶음은 남기지 않는다
fi

say ".env"
ENV_FILE="$APP/.env"
if [ ! -f "$ENV_FILE" ]; then
  [ -f /root/jiil-server.env ] || die "PC 설정(server.env)이 없습니다 — PC에서 upload_from_pc.sh로 올려 주세요"
  TOKEN=$("$PY" -c 'import secrets; print(secrets.token_urlsafe(24))')
  {
    echo "# 클라우드 서버 설정 — deploy/setup_server.sh가 만듦. 깃에 올라가지 않는다."
    cat /root/jiil-server.env
    echo "HOST='127.0.0.1'"
    echo "PORT='$PORT'"
    echo "APP_TOKEN='$TOKEN'"
    echo "COOKIE_SECURE='1'"
    echo "GOOGLE_SERVICE_ACCOUNT_FILE='$HOME_DIR/jiil-keys/keys.json'"
    echo "BACKUP_DIR='$HOME_DIR/backup'"
    echo "PYTHONUTF8='1'"
  } > "$ENV_FILE"
  echo "새 접속 토큰을 만들었습니다."
else
  echo "있던 .env(접속 토큰 포함)를 그대로 씁니다."
fi
rm -f /root/jiil-server.env
chown "$USER_NAME:$USER_NAME" "$ENV_FILE"
chmod 600 "$ENV_FILE"

say "파이썬 패키지"
[ -x "$VENV/bin/python" ] || as_user "$PY" -m venv "$VENV"
as_user "$VENV/bin/pip" install -q --upgrade pip
as_user "$VENV/bin/pip" install -q -r "$APP/requirements.txt"

say "자동 실행 (systemd)"
cat > /etc/systemd/system/jiil-webapp.service <<EOF
[Unit]
Description=JIIL 나라장터 공고 수집 웹앱
After=network-online.target
Wants=network-online.target

[Service]
User=$USER_NAME
WorkingDirectory=$APP
EnvironmentFile=$ENV_FILE
ExecStart=$VENV/bin/python -u -m webapp
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable jiil-webapp >/dev/null
systemctl restart jiil-webapp

say "https (Caddy)"
cat > /etc/caddy/Caddyfile <<EOF
$DOMAIN {
	encode gzip
	header Strict-Transport-Security "max-age=31536000"
	reverse_proxy 127.0.0.1:$PORT
}
EOF
systemctl enable caddy >/dev/null
systemctl restart caddy

say "확인"
ok=0
for _ in $(seq 1 30); do
  if curl -s -m 3 -o /dev/null "http://127.0.0.1:$PORT/"; then ok=1; break; fi
  sleep 1
done
[ "$ok" = 1 ] || { journalctl -u jiil-webapp -n 30 --no-pager; die "웹앱이 켜지지 않았습니다 — 위 로그를 보내 주세요"; }
journalctl -u jiil-webapp -n 40 --no-pager | grep -E "과거 실적|피드백 시트|판단 기록 백업|NARA_SERVICE_KEY" || true

code=000
for _ in $(seq 1 24); do
  code=$(curl -s -m 5 -o /dev/null -w '%{http_code}' "https://$DOMAIN/" || true)
  [ "$code" = 200 ] && break
  sleep 5
done
TOKEN=$(sed -n "s/^APP_TOKEN='\(.*\)'$/\1/p" "$ENV_FILE")
echo
echo "=================================================================="
if [ "$code" = 200 ]; then
  echo "  설치 끝. 팀원에게 줄 주소 (어디서나 열림):"
else
  echo "  웹앱은 켜졌지만 https 확인이 아직입니다(응답 $code). 방화벽(ACG)에 80·443이"
  echo "  열려 있는지 확인하고 1~2분 뒤 아래 주소를 열어 보세요:"
fi
echo
echo "    https://$DOMAIN/?t=$TOKEN"
echo
echo "  이 주소는 비밀번호와 같습니다 — 팀 안에서만 공유하세요."
echo "=================================================================="
