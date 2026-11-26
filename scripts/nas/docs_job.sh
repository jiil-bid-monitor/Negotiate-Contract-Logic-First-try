#!/bin/sh
# 회사 서류 정리·갱신 알림 한 번 — 공유 폴더 under100m/run.sh에서 부른다:
#   sh /work/Negotiate-Contract-Logic-First-try/scripts/nas/docs_job.sh
# 회사서류_최신이 지일·그리다 폴더로 나뉘었으면(2026-11-26) under100m.docs가 폴더마다 정리한다.
cd /work/Negotiate-Contract-Logic-First-try || exit 1
exec python scripts/nas/run_with_env.py python -m under100m.docs --dir /work/회사서류_최신 "$@"
