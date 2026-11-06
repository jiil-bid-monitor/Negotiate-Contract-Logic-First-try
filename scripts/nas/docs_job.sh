#!/bin/sh
# 회사 서류 정리·갱신 알림 한 번 — 공유 폴더 under100m/run.sh에서 부른다:
#   sh /work/Negotiate-Contract-Logic-First-try/scripts/nas/docs_job.sh
cd /work/Negotiate-Contract-Logic-First-try || exit 1
exec python scripts/nas/run_with_env.py python -m under100m.docs --dir /work/회사서류_최신 "$@"
