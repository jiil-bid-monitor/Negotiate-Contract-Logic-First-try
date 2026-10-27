"""예외 낱말 후보 보고서 → 개발자 방 텔레그램 (키워드 학습 자동화, 2026-10-27 사용자 결정).

주간 수집이 남긴 첨부 원문(output/attachment_text)에서 싱크로율 키워드가 더 긴 낱말 속에 잡힌 곳을 모아
오탐 가능성 순으로 보고서를 만들고(jiil-past-contracts `evaluation/exclusion_suggest.py`), 개발자 방
(TELEGRAM_DEV_CHAT_ID)에 요약 + HTML로 보낸다. 담당자 방엔 안 보낸다(웹앱은 담당자가 보는 곳이라 — 사용자 결정).
규칙은 바꾸지 않는다 — 사람이 보고 승인한 낱말만 jiil 레포 EXCLUDE_WORDS·판단 기록에 넣는다.

    python -m nego.exclusion_report output/attachment_text [--out output/예외낱말_후보.html] [--no-send]

과거 실적 자료(JIIL_REPO)나 텔레그램 설정이 없으면 건너뛰고 0으로 끝낸다(수집 결과에 영향 없음).
"""

from __future__ import annotations

import argparse
import html
import sys
from collections import defaultdict
from pathlib import Path

from .sync import default_jiil_repo

TOP_IN_MESSAGE = 10


def load_docs(folder: Path) -> list[tuple[str, str, str]]:
    """첨부 원문 폴더(`{공고번호}_{차수}_{순번}_{파일명}.txt`)를 공고별로 묶는다. 참가자격 요약 파일은 뺀다."""
    parts: dict[str, list[str]] = defaultdict(list)
    for path in sorted(folder.glob("*.txt")):
        if path.name.endswith("_참가자격.txt"):
            continue
        parts[path.name.split("_", 1)[0]].append(path.read_text(encoding="utf-8", errors="replace"))
    return [(no, "", "\n".join(texts)) for no, texts in parts.items()]


def summary_message(cands: list, mode: str, n_docs: int, title: str) -> str:
    lines = [f"<b>{html.escape(title)}</b>", f"공고 {n_docs}건 · 후보 {len(cands)}개 · 순위 {mode}", ""]
    for i, c in enumerate(cands[:TOP_IN_MESSAGE], 1):
        tag = c.tag.split("/", 1)[1]
        lines.append(f"{i}. {html.escape(c.word)} ← '{html.escape(c.keyword)}' ({html.escape(tag)}) · 공고 {len(c.notices)}")
    lines += ["", "오탐이면 승인, 제 뜻이면 기각 — 번호로 알려 주면 예외 낱말에 반영합니다. 전체는 첨부 HTML."]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m nego.exclusion_report", description="예외 낱말 후보 보고서")
    ap.add_argument("folder", type=Path, help="첨부 원문 폴더 (output/attachment_text)")
    ap.add_argument("--out", type=Path, default=Path("output/예외낱말_후보.html"))
    ap.add_argument("--title", default="예외 낱말 후보 (주간)")
    ap.add_argument("--limit", type=int, default=150)
    ap.add_argument("--no-send", action="store_true", help="보고서만 만들고 텔레그램엔 안 보냄")
    args = ap.parse_args(argv)

    repo = default_jiil_repo()
    if not (repo / "evaluation" / "exclusion_suggest.py").exists():
        print(f"예외 낱말 후보 도구 없음({repo}) → 건너뜀")
        return 0
    docs = load_docs(args.folder) if args.folder.is_dir() else []
    if not docs:
        print(f"첨부 원문 없음({args.folder}) → 건너뜀")
        return 0
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    from evaluation import exclusion_suggest as es  # jiil 레포

    cands, mode = es.suggest(docs, limit=args.limit)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(es.render_html(cands, mode, len(docs), args.title), encoding="utf-8")
    print(f"예외 낱말 후보: 공고 {len(docs)}건 → 후보 {len(cands)}개 ({mode}) → {args.out}")
    if args.no_send or not cands:
        return 0

    from .telegram import TelegramConfig, migration_notes, send_messages

    config = TelegramConfig.from_env("TELEGRAM_DEV_CHAT_ID")
    if config is None:
        print("TELEGRAM_DEV_CHAT_ID 없음 → 보내지 않음")
        return 0
    errors = send_messages(config, [summary_message(cands, mode, len(docs), args.title)], args.out)
    for note in migration_notes(config, "TELEGRAM_DEV_CHAT_ID"):
        print(f"::warning::{note}")
    for err in errors:
        print(f"::warning::예외 낱말 후보 발송 실패 {err}")
    if not errors:
        print("예외 낱말 후보를 개발자 방에 보냈습니다")
    return 0


if __name__ == "__main__":
    sys.exit(main())
