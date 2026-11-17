#!/bin/sh
# 코드를 GitHub main 최신으로 받는다 — 웹앱을 켤 때·1억 미만을 돌릴 때마다(2026-11-17, 병합한 고침이 NAS에 안 들어가 수집 실패가 이어졌음).
# 이미지에 git이 있을 때만(Dockerfile). 받기에 실패해도 지금 코드로 그대로 실행한다.
# requirements.txt가 바뀐 병합은 여전히 이미지를 다시 만들어야 함.
command -v git >/dev/null 2>&1 || exit 0
cd /work/Negotiate-Contract-Logic-First-try || exit 0
git pull --ff-only -q origin main || echo "코드 받기 실패 — 지금 코드로 실행"
exit 0
