#!/usr/bin/env bash
# 발송 기록 파일들을 상태 전용 브랜치 bot-state에 올린다 — GitHub Actions에서만 돈다(.github/workflows/report-*.yml, daily.yml).
#
#   bash scripts/push-bot-state.sh state/sent_bids.json state/sent_report_daily.json
#
# 파일은 이름만 따서 브랜치 맨 위에 둔다. 브랜치의 다른 파일은 건드리지 않는다 — 예전 daily.yml은 sent_bids.json 하나로
# 브랜치를 새로 만들어 강제로 올렸는데, 기록 파일이 여럿이 되면 서로 지운다. 월요일엔 매일·주간 보고가 거의 같이 올리므로
# 밀리면 받아서 다시 올린다(팀원 수집기 scripts/push-report-log.sh와 같은 방식).
#
#   필요: GH_TOKEN (contents: write), GITHUB_REPOSITORY
set -euo pipefail

files=()
for f in "$@"; do
  [ -f "$f" ] && files+=("$(cd "$(dirname "$f")" && pwd)/$(basename "$f")")
done
if [ ${#files[@]} -eq 0 ]; then
  echo "올릴 기록 파일이 없습니다."
  exit 0
fi

# BOT_STATE_REMOTE: 시험용 — 로컬 임시 저장소에 올려 볼 때만 쓴다
url="${BOT_STATE_REMOTE:-https://x-access-token:${GH_TOKEN:-}@github.com/${GITHUB_REPOSITORY:-}.git}"
dir="${RUNNER_TEMP:-/tmp}/bot-state-push"
rm -rf "$dir"
if git ls-remote --exit-code --heads "$url" bot-state >/dev/null 2>&1; then
  git clone --quiet --depth 1 --branch bot-state "$url" "$dir"
else
  git init --quiet "$dir"
  git -C "$dir" checkout --quiet --orphan bot-state
  git -C "$dir" remote add origin "$url"
fi

cd "$dir"
git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
for f in "${files[@]}"; do cp "$f" .; done
git add -- $(for f in "${files[@]}"; do basename "$f"; done)
if git diff --cached --quiet; then
  echo "기록에 바뀐 것이 없습니다."
  exit 0
fi
git commit --quiet -m "발송 기록 갱신 (run ${GITHUB_RUN_ID:-local})"

for attempt in 1 2 3; do
  if git push --quiet origin HEAD:bot-state; then
    echo "bot-state에 올렸습니다: $(for f in "${files[@]}"; do basename "$f"; done | tr '\n' ' ')"
    exit 0
  fi
  echo "올리다 밀렸습니다 — 받아서 다시 올립니다 (${attempt}/3)"
  sleep $((attempt * 5))
  git pull --quiet --rebase origin bot-state
done
echo "bot-state에 기록을 올리지 못했습니다." >&2
exit 1
