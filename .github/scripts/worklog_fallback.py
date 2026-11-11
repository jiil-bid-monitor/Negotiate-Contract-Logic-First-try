#!/usr/bin/env python3
"""그날 작업 기록(docs/worklog/날짜*.md)이 없으면 GitHub 기록으로 대신 정리해 PR로 올린다.

.github/workflows/worklog-fallback.yml이 평일 18:30(KST) 뒤에 부른다.

- 이미 main에 그날 작업 기록이 있거나, 열린 PR에 들어 있으면 아무것도 하지 않는다.
- 그날 병합된 PR·main에 바로 올린 커밋·그날 손댄 열린 PR을 사람별로 나열한다
  (작성자가 "Claude"인 커밋은 그 커밋을 main에 올린 사람으로 붙인다).
  AI 요약이 아니라 기록을 모은 초안이다 — 무엇을 왜 했는지, 결정, 내일 할 일은 사람이 채운다.
- main에 바로 넣지 않고 `worklog-auto/날짜` 브랜치와 PR로 올린다 (확인하고 고친 뒤 병합).

환경 변수: GITHUB_REPOSITORY(필수), GH_TOKEN(gh CLI용), WORKLOG_DATE(정리할 날짜, 비우면 가장 최근에 18:30이 지난 날),
DRY_RUN=1(올리지 않고 내용만 출력), FORCE=1(이미 있어도 만든다 — 시험용).
"""
import datetime as dt
import json
import os
import subprocess
import sys

KST = dt.timezone(dt.timedelta(hours=9))
REPO = os.environ["GITHUB_REPOSITORY"]
DIR = "docs/worklog"
NAMES = {"iamhiki": "히키", "kdh-007": "HJE"}   # GitHub 로그인 → 작업 기록에 쓸 이름 (없으면 로그인 그대로)
DRY = os.environ.get("DRY_RUN") == "1"
FORCE = os.environ.get("FORCE") == "1"


def run(*cmd, check=True):
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    if check and r.returncode:
        sys.exit(f"실패: {' '.join(cmd)}\n{r.stderr}")
    return r.stdout


def gh_json(*args):
    return json.loads(run("gh", *args) or "null")


def target_date():
    """정리할 날짜. 예약 실행이 늦어 자정을 넘겨도 같은 날을 가리키게, 18:30부터 다음 날 18:29까지는 같은 날로 본다."""
    given = (os.environ.get("WORKLOG_DATE") or "").strip()
    if given:
        return dt.date.fromisoformat(given)
    return (dt.datetime.now(KST) - dt.timedelta(hours=18, minutes=30)).date()


def name_of(login):
    return NAMES.get(login, login or "알 수 없음")


def hhmm(iso):
    return dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(KST).strftime("%H:%M")


def main():
    day = target_date()
    start = dt.datetime.combine(day, dt.time(0, 0), KST)
    end = start + dt.timedelta(days=1)
    span = f"{start.isoformat()}..{end.isoformat()}"
    branch = f"worklog-auto/{day}"
    path = f"{DIR}/{day}_자동정리.md"

    # 1. 이미 있으면 끝 — main, 열린 PR, 예전에 만든 자동 브랜치
    if not FORCE:
        have = [f for f in run("git", "-c", "core.quotepath=false", "ls-files", DIR).splitlines() if f.split("/")[-1].startswith(str(day))]
        if have:
            print(f"{day} 작업 기록이 이미 main에 있음: {', '.join(have)} — 할 일 없음")
            return
        for pr in gh_json("pr", "list", "-R", REPO, "--state", "open", "--limit", "100", "--json", "number,files"):
            hit = [f["path"] for f in pr.get("files") or [] if f["path"].startswith(f"{DIR}/{day}")]
            if hit:
                print(f"{day} 작업 기록이 열린 PR #{pr['number']}에 있음: {', '.join(hit)} — 할 일 없음")
                return
        if run("git", "ls-remote", "--heads", "origin", branch).strip():
            print(f"{branch} 브랜치가 이미 있음 — 할 일 없음")
            return

    # 2. 그날 기록 모으기
    merged = gh_json("pr", "list", "-R", REPO, "--state", "merged", "--search", f"merged:{span}", "--limit", "200",
                     "--json", "number,title,author,mergedAt")
    merged.sort(key=lambda p: p["mergedAt"])
    for p in merged:   # 커밋은 PR마다 따로 — 한꺼번에 물으면 GitHub가 너무 큰 조회라며 거절함
        p["commits"] = gh_json("pr", "view", str(p["number"]), "-R", REPO, "--json", "commits")["commits"]
    in_prs = {c["oid"] for p in merged for c in p.get("commits") or []}
    # PR 없이 main에 바로 올린 커밋 — 작성자가 "Claude"인 커밋이 많아, 그 커밋을 main에 올린 사람(push 기록)으로 붙임
    pages = json.loads(run("gh", "api", "--paginate", "--slurp", f"repos/{REPO}/activity?ref=refs/heads/main&time_period=week&per_page=100"))
    pushes = sorted((a["timestamp"], a["actor"]["login"]) for page in pages for a in page if a.get("actor"))
    def pusher(when_utc):
        return next((who for t, who in pushes if t >= when_utc), "")
    direct = {}   # 로그인 → [(시각, 제목)]
    log = run("git", "log", "origin/main", "--no-merges", f"--since={start.isoformat()}", f"--until={end.isoformat()}",
              "--format=%H%x09%cI%x09%an%x09%s")
    for line in log.splitlines():
        sha, when, author, subject = line.split("\t", 3)
        if sha in in_prs or "[bot]" in author:
            continue
        utc = dt.datetime.fromisoformat(when).astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        direct.setdefault(pusher(utc), []).append((when, subject))
    n_direct = sum(len(v) for v in direct.values())
    opened = gh_json("pr", "list", "-R", REPO, "--state", "open", "--search", f"updated:>={start.isoformat()}", "--limit", "100",
                     "--json", "number,title,author,updatedAt")
    opened = [p for p in opened if p["updatedAt"] < end.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
              and not p["author"].get("is_bot")]
    if not merged and not n_direct and not opened:
        print(f"{day}에는 병합된 PR·커밋·손댄 PR이 없음 — 작업 기록을 만들지 않음")
        return

    # 3. 작업 기록 형식으로 쓰기 — "## 번호. 섹션" 아래 목록 (docs/worklog의 다른 파일과 같은 모양). 사람별로 PR 먼저, 그다음 바로 올린 커밋
    out = [f"# {day} 작업 정리 (자동)", "",
           "> 18:30까지 이 날 작업 기록이 올라오지 않아, GitHub 기록(병합된 PR·커밋)을 모아 자동으로 만든 초안입니다.",
           "> 무엇을 왜 했는지, 결정한 것, 내일 할 일은 들어 있지 않습니다 — 확인하고 고친 뒤 병합해 주세요.", ""]
    n = 0
    prs_by = {}
    for p in merged:
        prs_by.setdefault(p["author"]["login"], []).append(p)
    for login in list(dict.fromkeys([*prs_by, *direct])):
        prs, commits = prs_by.get(login, []), sorted(direct.get(login, []))
        n += 1
        counts = " · ".join(x for x in (f"병합한 PR {len(prs)}건" if prs else "", f"main에 바로 올린 커밋 {len(commits)}건" if commits else "") if x)
        out += [f"## {n}. {name_of(login) if login else '올린 사람을 알 수 없는 커밋'} — {counts}", ""]
        for p in prs:
            out.append(f"- {p['title']} (#{p['number']}, {hhmm(p['mergedAt'])})")
            heads = [c["messageHeadline"] for c in p.get("commits") or []]
            if len(heads) > 1:   # 커밋이 하나면 제목과 같은 말이라 생략
                out.extend(f"  - {h}" for h in heads)
        out.extend(f"- {subject} ({hhmm(when)})" for when, subject in commits)
        out.append("")
    if opened:
        n += 1
        out += [f"## {n}. 아직 열려 있는 PR (이 날 손댄 것) {len(opened)}건", ""]
        out.extend(f"- {p['title']} (#{p['number']}, {name_of(p['author']['login'])})" for p in opened)
        out.append("")
    out += ["## 내일 할 일", "", "- (직접 적어 주세요)", ""]
    text = "\n".join(out)

    if DRY:
        print(f"[DRY_RUN] {path} ({branch})\n{'-' * 60}\n{text}")
        return

    # 4. 브랜치에 올리고 PR 열기
    os.makedirs(DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    run("git", "config", "user.name", "github-actions[bot]")
    run("git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com")
    run("git", "checkout", "-b", branch)
    run("git", "add", path)
    run("git", "commit", "-m", f"{day} 작업 정리 (자동) — 작업 기록이 없어 GitHub 기록으로 만든 초안")
    run("git", "push", "origin", branch)
    body = (f"18:30까지 {day} 작업 기록이 올라오지 않아 자동으로 만든 초안입니다.\n\n"
            f"- 병합된 PR {len(merged)}건, main에 바로 올린 커밋 {n_direct}건, 손댄 열린 PR {len(opened)}건을 사람별로 모았습니다.\n"
            "- AI 요약이 아니라 기록을 모은 것입니다. 무엇을 왜 했는지·결정·내일 할 일을 채운 뒤 병합해 주세요.\n"
            "- 필요 없으면 PR을 닫고 브랜치를 지우면 됩니다 (같은 날짜로 다시 만들지 않습니다).\n")
    print(run("gh", "pr", "create", "-R", REPO, "--base", "main", "--head", branch,
              "--title", f"{day} 작업 정리 (자동 초안)", "--body", body))


if __name__ == "__main__":
    main()
