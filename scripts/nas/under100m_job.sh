#!/bin/sh
# 1억 미만 공고 폴더 한 번 만들기 — 공유 폴더 under100m/run.sh에서 부른다:
#   sh /work/Negotiate-Contract-Logic-First-try/scripts/nas/under100m_job.sh
# 처리 기록은 컨테이너를 다시 만들어도 남게 공유 폴더에 둔다.
cd /work/Negotiate-Contract-Logic-First-try || exit 1
exec python scripts/nas/run_with_env.py python -m under100m --state /work/under100m/state.json
