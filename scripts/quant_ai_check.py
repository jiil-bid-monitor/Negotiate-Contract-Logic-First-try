"""정량평가서 — 규칙이 못 읽은 곳을 Claude에게 물어 보는 시험(2026-11-11 사용자 결정, 서버 PC의 Claude Code Max로).

과거 제안요청서(`quant_fill_check.py`와 같은 docs/raw 사업 폴더)마다 규칙 검사(`quantdocs.health.check`)를 돌리고,
  - '배점기준 못 읽음'이면 제안요청서 글을 Claude에 보내 배점기준을 받고(`aiassist.criteria_from_text`), 그 기준으로 다시 검사해
    '배점기준 못 읽음·평가표 못 읽음·기준 없음'이 줄었는지 셈
  - '모르는 이름표'가 있으면 이름표 낱말만 보내 회사 정보 항목을 받음(`aiassist.map_labels`)
결과는 사람이 검토할 표 output/quant_ai_check.csv(공개 금지 폴더). 회사 자료는 보내지 않음(가짜 회사 값으로 검사, 보내는 건 공개 공고 글·이름표).
웹앱 동작은 바꾸지 않음 — 결과를 보고 붙일지 정함.

    py scripts/quant_ai_check.py --jiil ../jiil-past-contracts --dry-run      # Claude 안 부르고 물을 곳·글 길이만
    py scripts/quant_ai_check.py --jiil ../jiil-past-contracts --limit 5      # 5개 사업만 시험
    py scripts/quant_ai_check.py --jiil ../jiil-past-contracts                # 전부(Max 사용량을 씀)

Claude 부르는 횟수는 --max-calls(기본 200)까지. 한 번에 수십 초 걸릴 수 있음.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from quant_fill_check import projects, to_hwpx  # noqa: E402

from quantdocs import aiassist, health  # noqa: E402

CRIT_KINDS = ("배점기준 못 읽음", "평가표 못 읽음", "기준 없음")


def crit_brief(c) -> str:
    """사람이 읽을 기준 한 줄 — '참여기술자 3점 [staff] 특급 0.5·고급 0.4 / 구간: 50점 이상→5 …'."""
    parts = [f"{c.name} {c.points:g}점 [{c.kind or '기타'}]"]
    if c.per_grade:
        parts.append(" · ".join(f"{g} {v:g}" for g, v in c.per_grade.items()))
    if c.bands:
        parts.append("구간: " + " / ".join(f"{b.text}→{b.score:g}" for b in c.bands[:6]) + (" …" if len(c.bands) > 6 else ""))
    return " ".join(parts)


def rfp_text(h: health.Health) -> str:
    """제안요청서 글 먼저(없으면 과업지시서·공고문 순 — check가 이미 그 순서로 모음)."""
    return "\n\n".join(f"=== {name} ===\n{text}" for name, text in h.texts.items() if text.strip())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--jiil", default=str(ROOT.parent / "jiil-past-contracts"))
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--max-calls", type=int, default=200)
    ap.add_argument("--model", default=None, help="claude 모델(기본 .env CLAUDE_MODEL 또는 sonnet)")
    ap.add_argument("--dry-run", action="store_true", help="Claude를 부르지 않고 물을 곳·보낼 글 길이만 셈")
    ap.add_argument("--out", default=str(ROOT / "output" / "quant_ai_check.csv"))
    args = ap.parse_args(argv)
    raw = Path(args.jiil) / "docs" / "raw"
    groups = projects(raw)
    if args.limit:
        groups = groups[: args.limit]
    rows: list[list] = []
    calls = 0
    before, after = Counter(), Counter()
    asked = {"배점기준": 0, "배점기준 받음": 0, "이름표 사업": 0, "이름표": 0, "이름표 항목 받음": 0, "실패": 0}
    fields = Counter()
    sent_chars = 0
    started = time.time()
    for i, group in enumerate(groups, 1):
        folder = "/".join(group[0].relative_to(raw).parts[:2])
        with tempfile.TemporaryDirectory() as tmp:
            h = health.check(group, Path(tmp), convert=to_hwpx)
        kinds = h.counts()
        need_crit = kinds.get("배점기준 못 읽음", 0) > 0
        labels = list(dict.fromkeys(it.detail for it in h.issues if it.kind == "모르는 이름표"))
        for k in CRIT_KINDS:
            before[k] += kinds.get(k, 0)
        if need_crit:
            asked["배점기준"] += 1
            text = aiassist._trim(rfp_text(h))
            sent_chars += len(text)
            if args.dry_run or calls >= args.max_calls:
                rows.append([folder, "배점기준", "물을 곳" if args.dry_run else "횟수 한도로 건너뜀", f"글 {len(text):,}자", ""])
                for k in CRIT_KINDS:
                    after[k] += kinds.get(k, 0)
            else:
                calls += 1
                try:
                    crit = aiassist.criteria_from_text(text, model=args.model)
                except aiassist.AiError as err:
                    asked["실패"] += 1
                    rows.append([folder, "배점기준", "실패", str(err)[:200], ""])
                    crit = []
                if crit:
                    asked["배점기준 받음"] += 1
                    with tempfile.TemporaryDirectory() as tmp:
                        h2 = health.check(group, Path(tmp), convert=to_hwpx, criteria=crit)
                    k2 = h2.counts()
                    for k in CRIT_KINDS:
                        after[k] += k2.get(k, 0)
                    change = " · ".join(f"{k} {kinds.get(k, 0)}→{k2.get(k, 0)}" for k in CRIT_KINDS if kinds.get(k, 0) or k2.get(k, 0))
                    for c in crit:
                        rows.append([folder, "배점기준", "받음", crit_brief(c), change])
                else:
                    for k in CRIT_KINDS:
                        after[k] += kinds.get(k, 0)
                    if not any(r[0] == folder and r[2] == "실패" for r in rows):
                        rows.append([folder, "배점기준", "빈 답", "Claude도 정량 배점기준을 찾지 못함", ""])
        else:
            for k in CRIT_KINDS:
                after[k] += kinds.get(k, 0)
        if labels:
            asked["이름표 사업"] += 1
            asked["이름표"] += len(labels)
            sent_chars += sum(len(x) for x in labels)
            if args.dry_run or calls >= args.max_calls:
                rows.append([folder, "이름표", "물을 곳" if args.dry_run else "횟수 한도로 건너뜀", " / ".join(labels)[:300], ""])
            else:
                calls += 1
                try:
                    got = aiassist.map_labels(labels, model=args.model)
                except aiassist.AiError as err:
                    asked["실패"] += 1
                    rows.append([folder, "이름표", "실패", str(err)[:200], ""])
                    got = {}
                for lab in labels:
                    f = got.get(lab, "(답 없음)")
                    fields[f] += 1
                    asked["이름표 항목 받음"] += f not in ("none", "(답 없음)")
                    rows.append([folder, "이름표", "받음", lab, f])
        print(f"  {i}/{len(groups)} {folder} · Claude {calls}회 · {time.time() - started:.0f}초", flush=True)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["사업 폴더", "물은 것", "결과", "내용(기준·이름표)", "다시 검사 / 받은 항목"])
        w.writerows(rows)
    print(f"\n사업 {len(groups)}개 · Claude {calls}회{' (부르지 않음 — dry-run)' if args.dry_run else ''} · 보낸 글 약 {sent_chars:,}자 · "
          f"{time.time() - started:.0f}초")
    print(json.dumps(asked, ensure_ascii=False))
    if not args.dry_run:
        print("배점기준 관련 문제(규칙만 → Claude 기준으로 다시):")
        for k in CRIT_KINDS:
            print(f"  {k}: {before[k]} → {after[k]}")
        if fields:
            print("이름표 → 항목:", dict(fields.most_common()))
    print(f"자세히(사람이 검토): {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
