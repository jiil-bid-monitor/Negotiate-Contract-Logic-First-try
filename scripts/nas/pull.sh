#!/bin/sh
# 코드를 GitHub main 최신으로 받는다 — 웹앱을 켤 때·1억 미만을 돌릴 때마다(2026-11-17, 병합한 고침이 NAS에 안 들어가 수집 실패가 이어졌음).
# 이미지에 git이 있을 때만(Dockerfile). 받기에 실패해도 지금 코드로 그대로 실행한다.
# requirements.txt가 바뀐 병합은 여전히 이미지를 다시 만들어야 함.
# 결과는 공유 폴더 control/code-pull.txt 한 줄(컨테이너 로그를 못 보는 사람도 확인할 수 있게).
LOG="${CONTROL_DIR:-/work/control}/code-pull.txt"
now() { date '+%Y-%m-%d %H:%M:%S'; }
if ! command -v git >/dev/null 2>&1; then
  echo "$(now) git 없음 — 이미지를 다시 만들어야 코드를 스스로 받음" > "$LOG" 2>/dev/null
  exit 0
fi
cd /work/Negotiate-Contract-Logic-First-try || exit 0
if out=$(git pull --ff-only -q origin main 2>&1); then
  echo "$(now) 받기 성공 — $(git log -1 --format='%h %s')" > "$LOG" 2>/dev/null
else
  echo "코드 받기 실패 — 지금 코드로 실행"
  printf '%s 받기 실패 — 지금 코드로 실행\n%s\n' "$(now)" "$out" > "$LOG" 2>/dev/null
fi
exit 0
