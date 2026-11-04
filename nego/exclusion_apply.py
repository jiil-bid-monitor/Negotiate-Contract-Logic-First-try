"""예외 낱말 판단 파일 자동 반영 (2026-11-03 사용자 결정 — 파일을 주고받지 않게, 병합까지 자동).

예외 낱말 후보 HTML(`nego/exclusion_report.py`)에서 '제출'하면 내려받아지는 `예외낱말_판단_날짜.json`을
① jiil 레포 판단 기록(`evaluation/exclusion_feedback.json`)·예외 낱말 목록(`evaluation/exclude_words.json`)에 넣고
② 과거 150건 프로필을 다시 만들고 테스트를 돌려 통과하면 PR을 만들어 병합하고
③ 이 레포의 싱크로율 묶음(`corpus/sync_bundle.enc`)을 다시 잠가 PR을 만들어 병합한 뒤
④ 개발자 방(TELEGRAM_DEV_CHAT_ID)에 결과를 알린다. 어느 단계든 실패하면 병합하지 않고 실패만 알린다.

두 레포 모두 임시 작업 폴더(git worktree)에서 하므로 지금 열려 있는 작업 폴더·브랜치는 건드리지 않는다.
웹앱이 다운로드 폴더를 지켜보다 새 파일이 생기면 부른다(`webapp/exclusionsync.py`). 손으로도:

    python -m nego.exclusion_apply 예외낱말_판단_20261103.json
    python -m nego.exclusion_apply 파일.json --mark-done   # 이미 손으로 반영한 파일 — 기록만 남김

규칙(키워드 정규식)은 바꾸지 않는다 — 영문 낱말이 승인되면 낱말로 넣고 "규칙으로 처리하는 게 나을 수 있음"으로 알린다.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from . import corpus
from .sync import default_jiil_repo

REPO = Path(__file__).resolve().parents[1]
APPLIED = REPO / "data" / "exclusion_applied.json"
FEEDBACK = Path("evaluation/exclusion_feedback.json")
WORDS = Path("evaluation/exclude_words.json")
TASK_PROFILE = Path("evaluation/task_profile.py")
_LATIN = re.compile(r"^[A-Za-z0-9]+$")


class ApplyError(Exception):
    def __init__(self, step: str, detail: str):
        super().__init__(f"{step}: {detail}")
        self.step, self.detail = step, detail


def file_hash(path: Path) -> str:
    return hashlib.sha1(path.read_bytes()).hexdigest()


def load_applied() -> dict:
    try:
        return json.loads(APPLIED.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def record(path: Path, status: str, note: str = "") -> None:
    done = load_applied()
    done[file_hash(path)] = {"file": path.name, "status": status, "note": note,
                             "at": datetime.now().isoformat(timespec="seconds")}
    APPLIED.parent.mkdir(parents=True, exist_ok=True)
    APPLIED.write_text(json.dumps(done, ensure_ascii=False, indent=1), encoding="utf-8")


def read_decision(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data.get("approved"), list) or not isinstance(data.get("rejected"), list):
        raise ApplyError("판단 파일", "approved·rejected 목록이 없음")
    return data


def merge(feedback: dict, words: list[str], decision: dict, by: str, known_words: set[str]) -> dict:
    """판단을 판단 기록·낱말 목록에 더한다(그 자리에서 바꿈). 이미 판단한 (낱말, 키워드)는 건너뛴다 —
    같은 파일을 두 번 넣어도 결과는 같다. 돌려주는 값은 건수 요약."""
    have = {(x["word"], x["keyword"]) for x in feedback["approved"] + feedback["rejected"]}
    stats = {"approved": 0, "rejected": 0, "duplicate": 0, "new_words": [], "latin": []}
    for kind in ("approved", "rejected"):
        for item in decision[kind]:
            word, keyword = str(item.get("word", "")).strip(), str(item.get("keyword", "")).strip()
            if not word or not keyword:
                continue
            if (word, keyword) in have:
                stats["duplicate"] += 1
                continue
            have.add((word, keyword))
            feedback[kind].append({"word": word, "keyword": keyword, "tag": item.get("tag", ""), "by": by})
            stats[kind] += 1
            if kind == "approved" and word not in known_words and word not in words:
                words.append(word)
                stats["new_words"].append(word)
                if _LATIN.match(word):
                    stats["latin"].append(word)
    return stats


def _run(args: list[str], cwd: Path, step: str, timeout: int = 900) -> str:
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    try:
        done = subprocess.run(args, cwd=cwd, capture_output=True, timeout=timeout, creationflags=flags, env=env)
    except (OSError, subprocess.SubprocessError) as err:
        raise ApplyError(step, str(err)) from err
    out = (done.stdout + done.stderr).decode("utf-8", "replace")
    if done.returncode != 0:
        raise ApplyError(step, out.strip()[-600:] or f"종료 코드 {done.returncode}")
    return out


def _gh() -> str:
    return shutil.which("gh") or "C:/Program Files/GitHub CLI/gh.exe"


def _pr_and_merge(wt: Path, branch: str, title: str, body: str, step: str) -> str:
    _run(["git", "push", "-q", "-u", "origin", branch], wt, f"{step} 올리기")
    url = _run([_gh(), "pr", "create", "--base", "main", "--head", branch, "--title", title, "--body", body],
               wt, f"{step} PR 만들기").strip().splitlines()[-1]
    _run([_gh(), "pr", "merge", url, "--merge", "--delete-branch"], wt, f"{step} 병합")
    return url


class Worktree:
    """origin/main에서 새 브랜치로 임시 작업 폴더를 만들고, 끝나면 지운다."""

    def __init__(self, repo: Path, branch: str):
        self.repo, self.branch = repo, branch
        self.path = Path(tempfile.mkdtemp(prefix="exapply_")) / repo.name

    def __enter__(self) -> Path:
        _run(["git", "fetch", "-q", "origin", "main"], self.repo, f"{self.repo.name} 받기")
        _run(["git", "worktree", "add", "-q", "-b", self.branch, str(self.path), "origin/main"],
             self.repo, f"{self.repo.name} 작업 폴더")
        return self.path

    def __exit__(self, *exc) -> None:
        subprocess.run(["git", "worktree", "remove", "--force", str(self.path)], cwd=self.repo, capture_output=True)
        subprocess.run(["git", "branch", "-D", self.branch], cwd=self.repo, capture_output=True)
        shutil.rmtree(self.path.parent, ignore_errors=True)


def apply(path: Path, jiil_repo: Path, passphrase: str, dry_run: bool = False) -> dict:
    decision = read_decision(path)
    if not (jiil_repo / ".git").exists():
        raise ApplyError("jiil 레포", f"git 작업 폴더가 아님: {jiil_repo}")
    if not passphrase:
        raise ApplyError("묶음", "CORPUS_PASSPHRASE 없음")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    branch = f"auto/exclusion-{stamp}"

    with Worktree(jiil_repo, branch) as wt:
        feedback = json.loads((wt / FEEDBACK).read_text(encoding="utf-8"))
        words_doc = json.loads((wt / WORDS).read_text(encoding="utf-8"))
        known = set(re.findall(r'"([^"\n]+)"', (wt / TASK_PROFILE).read_text(encoding="utf-8")))
        stats = merge(feedback, words_doc["words"], decision, f"user-{datetime.now():%Y-%m-%d}", known)
        if not stats["approved"] and not stats["rejected"]:
            return {**stats, "jiil_pr": "", "bundle_pr": ""}
        (wt / FEEDBACK).write_text(json.dumps(feedback, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        (wt / WORDS).write_text(json.dumps(words_doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        if stats["new_words"]:
            _run([sys.executable, "scripts/build_task_profiles.py"], wt, "과거 프로필 다시 만들기")
        _run([sys.executable, "-m", "unittest", "discover", "-s", "tests"], wt, "jiil 테스트")
        if dry_run:  # 시험 — 반영·테스트·묶음 잠그기까지만, 올리거나 병합하지 않음
            corpus.seal(corpus._tar(wt), passphrase)
            return {**stats, "jiil_pr": "(시험 — 안 올림)", "bundle_pr": "(시험 — 안 올림)"}
        _run(["git", "add", "-A", "evaluation", "docs/summaries"], wt, "jiil 커밋")
        msg = (f"예외 낱말 판단 자동 반영 ({path.name}) — 승인 {stats['approved']}·기각 {stats['rejected']}, "
               f"새 예외 낱말 {len(stats['new_words'])}개\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>")
        _run(["git", "commit", "-q", "-m", msg], wt, "jiil 커밋")
        body = (f"`{path.name}` 자동 반영(nego/exclusion_apply.py) — 승인 {stats['approved']} · 기각 {stats['rejected']}"
                f" · 이미 있던 판단 {stats['duplicate']}\n\n새 예외 낱말: {', '.join(stats['new_words']) or '없음'}"
                "\n\n🤖 Generated with [Claude Code](https://claude.com/claude-code)")
        jiil_pr = _pr_and_merge(wt, branch, f"예외 낱말 자동 반영 — {path.stem}", body, "jiil")
        bundle = corpus.seal(corpus._tar(wt), passphrase)

    with Worktree(REPO, branch) as nt:
        (nt / "corpus" / "sync_bundle.enc").write_bytes(bundle)
        _run(["git", "add", "corpus/sync_bundle.enc"], nt, "묶음 커밋")
        _run(["git", "commit", "-q", "-m",
              f"싱크로율 묶음 다시 잠금 — 예외 낱말 자동 반영({path.name})\n\n"
              "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"], nt, "묶음 커밋")
        bundle_pr = _pr_and_merge(nt, branch, f"싱크로율 묶음 다시 잠금 — {path.stem}",
                                  f"jiil {jiil_pr} 반영 묶음(자동).\n\n🤖 Generated with [Claude Code](https://claude.com/claude-code)",
                                  "묶음")

    # 웹앱이 읽는 jiil 작업 폴더도 최신으로(main이고 바뀐 게 없을 때만 — 손으로 작업 중이면 그대로 둠)
    try:
        if (_run(["git", "branch", "--show-current"], jiil_repo, "jiil").strip() == "main"
                and not _run(["git", "status", "--porcelain"], jiil_repo, "jiil").strip()):
            _run(["git", "pull", "-q", "--ff-only"], jiil_repo, "jiil 최신으로")
    except ApplyError:
        pass
    return {**stats, "jiil_pr": jiil_pr, "bundle_pr": bundle_pr}


def message(path: Path, result: dict | None, error: ApplyError | None) -> str:
    head = f"<b>예외 낱말 자동 반영</b> — {html.escape(path.name)}"
    if error is not None:
        return (f"{head}\n<b>실패</b> ({html.escape(error.step)}) — 병합하지 않았습니다.\n"
                f"<pre>{html.escape(error.detail[-500:])}</pre>")
    assert result is not None
    if not result["approved"] and not result["rejected"]:
        return f"{head}\n새 판단 없음 — 모두 이미 반영된 판단입니다(중복 {result['duplicate']})."
    lines = [head, f"승인 {result['approved']} · 기각 {result['rejected']} · 이미 있던 판단 {result['duplicate']}",
             f"새 예외 낱말 {len(result['new_words'])}개: {html.escape(', '.join(result['new_words'][:30])) or '없음'}"]
    if result["latin"]:
        lines.append(f"※ 영문 낱말 {len(result['latin'])}개({html.escape(', '.join(result['latin'][:10]))}) — "
                     "낱말로 넣었지만 키워드 규칙으로 처리하는 게 나을 수 있음(확인 필요)")
    lines.append(f"병합: jiil {html.escape(result['jiil_pr'])} · 묶음 {html.escape(result['bundle_pr'])}")
    lines.append("다음 Actions부터 적용 · 웹앱 싱크로율은 서버를 다시 켜면 적용")
    return "\n".join(lines)


def notify(text: str) -> None:
    from .telegram import TelegramConfig, send_messages

    config = TelegramConfig.from_env("TELEGRAM_DEV_CHAT_ID")
    if config is None:
        print("TELEGRAM_DEV_CHAT_ID 없음 → 알림 안 보냄")
        return
    for err in send_messages(config, [text]):
        print(f"알림 실패: {err}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m nego.exclusion_apply", description="예외 낱말 판단 파일 자동 반영")
    ap.add_argument("file", type=Path)
    ap.add_argument("--mark-done", action="store_true", help="반영하지 않고 처리한 파일로 기록만")
    ap.add_argument("--no-notify", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="반영·테스트까지만 — 올리거나 병합하지 않고 기록도 안 남김")
    args = ap.parse_args(argv)

    if args.mark_done:
        record(args.file, "manual")
        print(f"처리한 파일로 기록: {args.file.name}")
        return 0
    result, error = None, None
    try:
        result = apply(args.file, default_jiil_repo(), os.environ.get("CORPUS_PASSPHRASE", ""), args.dry_run)
    except ApplyError as err:
        error = err
    except Exception as err:  # noqa: BLE001 — 판단 파일이 깨진 경우 등도 개발자 방에 알림
        error = ApplyError("반영", f"{type(err).__name__}: {err}")
    if not args.dry_run:
        record(args.file, "failed" if error else "applied", str(error or ""))
    text = message(args.file, result, error)
    print(re.sub(r"<[^>]+>", "", text))
    if not args.no_notify:
        notify(text)
    return 1 if error else 0


if __name__ == "__main__":
    sys.exit(main())
