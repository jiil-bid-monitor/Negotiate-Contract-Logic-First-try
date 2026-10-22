#!/usr/bin/env bash
# 서버 PC → 클라우드 서버 옮기기 (서버 PC의 Git Bash에서, 레포 폴더에서 한 번). 안내: docs/cloud_server_setup.md
#
#   bash deploy/upload_from_pc.sh <클라우드 서버 공인IP>
#
# 이 PC의 판단 기록(data/), 과거 실적 폴더(jiil-past-contracts), 구글 키, .env의 인증키를 한 묶음으로 만들어
# 서버에 올리고 deploy/setup_server.sh를 돌린다. 서버 root 비밀번호를 두 번 묻는다(올리기 1번, 설치 1번).
# 이 PC 웹앱은 먼저 꺼야 한다 — 켜 둔 채 옮기면 그 뒤에 누른 판단이 빠지고, 두 서버가 같은 시트에 쓴다.

set -euo pipefail
cd "$(dirname "$0")/.." || exit 1

IP="${1:-}"
die() { printf '\n!! %s\n' "$*" >&2; exit 1; }
[[ "$IP" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "서버 공인 IP를 주세요 — 예: bash deploy/upload_from_pc.sh 110.165.1.2"
[ -f .env ] || die "레포 폴더에 .env가 없습니다 — 웹앱을 켜던 폴더에서 실행하세요"

set -a
# shellcheck disable=SC1091
. ./.env
set +a
PORT="${PORT:-8765}"

if curl -s -m 2 -o /dev/null "http://127.0.0.1:$PORT/"; then
  die "이 PC 웹앱이 아직 켜져 있습니다 — 웹앱 창에서 Ctrl+C로 끈 뒤 다시 실행하세요"
fi
[ -n "${NARA_SERVICE_KEY:-}" ] || die ".env에 NARA_SERVICE_KEY가 없습니다"

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
STAGE="$TMP/bundle"
mkdir -p "$STAGE"

echo "== 묶음 만들기"
mkdir -p "$STAGE/data"
for f in participation.json promoted.json webapp.sqlite3 doc_products.json; do
  if [ -f "data/$f" ]; then cp "data/$f" "$STAGE/data/" && echo "  판단 기록: data/$f"; fi
done
[ -f data/participation.json ] || echo "  (data/participation.json 없음 — 판단 기록 없이 시작합니다)"

PAST="${JIIL_REPO:-../jiil-past-contracts}"
if [ -f "$PAST/docs/summaries/past_task_profiles.json" ]; then
  mkdir -p "$STAGE/jiil-past-contracts"
  (cd "$PAST" && tar --exclude=.git -cf - .) | (cd "$STAGE/jiil-past-contracts" && tar -xf -)
  echo "  과거 실적: $PAST"
else
  echo "  (과거 실적 폴더를 못 찾음: $PAST — 싱크로율 없이 켜집니다)"
fi

if [ -n "${GOOGLE_SERVICE_ACCOUNT_FILE:-}" ]; then
  [ -f "$GOOGLE_SERVICE_ACCOUNT_FILE" ] || die "구글 키 파일이 없습니다: $GOOGLE_SERVICE_ACCOUNT_FILE"
  cp "$GOOGLE_SERVICE_ACCOUNT_FILE" "$STAGE/keys.json"
  echo "  구글 키: $GOOGLE_SERVICE_ACCOUNT_FILE"
elif [ -n "${GOOGLE_SERVICE_ACCOUNT_JSON:-}" ]; then
  printf '%s' "$GOOGLE_SERVICE_ACCOUNT_JSON" > "$STAGE/keys.json"
  echo "  구글 키: .env의 GOOGLE_SERVICE_ACCOUNT_JSON"
else
  echo "  (구글 키 없음 — 피드백 시트 쓰기는 꺼진 채로 켜집니다)"
fi

# 서버 .env에 옮길 값 (접속 토큰·주소·경로는 서버에서 새로 정한다)
{
  echo "NARA_SERVICE_KEY='$NARA_SERVICE_KEY'"
  [ -n "${FEEDBACK_SHEET_ID:-}" ] && [ -f "$STAGE/keys.json" ] && echo "FEEDBACK_SHEET_ID='$FEEDBACK_SHEET_ID'"
  [ -n "${FEEDBACK_SHEET_NAME:-}" ] && echo "FEEDBACK_SHEET_NAME='$FEEDBACK_SHEET_NAME'"
  [ -n "${ANTHROPIC_API_KEY:-}" ] && echo "ANTHROPIC_API_KEY='$ANTHROPIC_API_KEY'"
  true
} > "$STAGE/server.env"

BUNDLE="$TMP/jiil-upload.tgz"
(cd "$STAGE" && tar -czf "$BUNDLE" .)
echo "  묶음 크기: $(du -h "$BUNDLE" | cut -f1)"

SSH_OPTS=(-o StrictHostKeyChecking=accept-new)
echo
echo "== 서버에 올리기 — root 비밀번호를 입력하세요 (화면엔 안 보임)"
scp "${SSH_OPTS[@]}" "$BUNDLE" deploy/setup_server.sh "root@$IP:/root/"
echo
echo "== 서버 설치 — root 비밀번호를 한 번 더 입력하세요"
ssh -t "${SSH_OPTS[@]}" "root@$IP" "bash /root/setup_server.sh $IP /root/jiil-upload.tgz"

echo
echo "이 PC 웹앱은 이제 켜지 마세요 (판단 기록이 서버와 갈라지고, 두 곳이 같은 시트에 씁니다)."
