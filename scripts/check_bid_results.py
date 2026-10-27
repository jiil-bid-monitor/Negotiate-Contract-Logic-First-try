"""개찰결과 수집 결과 확인 — 건수·칸 구성만 내보낸다(업체명·사업자번호·금액은 출력하지 않음).

서버 PC에서 실행하고 결과 파일(output/bid_results_check.txt)을 사용자가 전해 준다(10/27 사용자 결정 — 원본은 서버 PC 밖으로 안 냄).

    py scripts/check_bid_results.py data/g2b_all_targets.csv
    py scripts/check_bid_results.py data/g2b_all_targets.csv --history data/bid_history   # 투찰 이력 참여수와 참가업체 수 대조

보는 것: 목록 대비 받음·실패·안 받음(입력일 연도·업무별), 예비가격·참가업체 건수, 낙찰방법·계약방법 값(나라장터 원문 그대로),
참가업체·예비가격 행의 칸 이름과 채워진 비율, (--history) 이력 참여수와 받은 참가업체 수가 같은지.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NO_COL, COUNT_COL = 2, 12  # 투찰 이력 표의 공고번호 칸, 참여수 칸


def read_targets(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        rows = [r for r in csv.reader(f) if r and r[0].strip() and r[0].strip() != "공고번호"]
    return [{"no": r[0].strip(), "work": r[1].strip() if len(r) > 1 else "", "date": r[3].strip() if len(r) > 3 else ""} for r in rows]


def read_results(path: Path) -> tuple[dict[str, dict], dict[str, str]]:
    """공고번호 → 성공한 마지막 기록, 공고번호 → 마지막 실패 사유(성공 기록이 없는 공고만)."""
    ok: dict[str, dict] = {}
    err: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if rec.get("error"):
            err[rec["input"]] = rec["error"]
        else:
            ok[rec["input"]] = rec
    return ok, {k: v for k, v in err.items() if k not in ok}


def history_counts(folder: Path) -> dict[str, str]:
    """투찰 이력 .xls(HTML 표) → 공고번호 → 참여수 칸 원문."""
    from lxml import html

    out: dict[str, str] = {}
    for f in sorted(folder.glob("*/*.xls")):
        for tr in html.fromstring(f.read_text(encoding="utf-8")).iter("tr"):
            cells = [re.sub(r"\s+", " ", td.text_content()).strip() for td in tr.findall("td")]
            if len(cells) <= COUNT_COL or not cells[0].isdigit():
                continue
            no = cells[NO_COL].replace("호", " ").split()[-1] if cells[NO_COL].strip() else ""
            out[no] = cells[COUNT_COL]
    return out


def field_fill(rows: list[dict]) -> list[str]:
    """행 목록의 칸 이름별 '채워진 행 / 전체 행'(값은 안 보임)."""
    filled: Counter = Counter()
    for r in rows:
        for k, v in r.items():
            if v not in (None, ""):
                filled[k] += 1
    keys = sorted({k for r in rows for k in r})
    return [f"    {k}: {filled[k]}/{len(rows)}" for k in keys]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="개찰결과 수집 결과 확인(건수·칸 구성만)")
    parser.add_argument("targets", help="수집 목록 CSV (공고번호, 업무, 기관, 입력일)")
    parser.add_argument("--results", default=str(ROOT / "output" / "bid_results.jsonl"), help="수집 결과 JSONL")
    parser.add_argument("--history", default="", help="투찰 이력 폴더(주면 참여수와 참가업체 수 대조)")
    parser.add_argument("--out", default=str(ROOT / "output" / "bid_results_check.txt"), help="확인 결과 파일")
    args = parser.parse_args(argv)

    targets = read_targets(Path(args.targets))
    ok, err = read_results(Path(args.results))
    lines: list[str] = []
    say = lines.append

    status = {t["no"]: ("받음" if t["no"] in ok else "실패" if t["no"] in err else "안 받음") for t in targets}
    total = Counter(status.values())
    say(f"목록 {len(targets)}건 — 받음 {total['받음']} · 실패 {total['실패']} · 안 받음 {total['안 받음']}")
    say(f"결과 파일의 성공 공고 {len(ok)}건 중 목록에 없는 공고 {len(set(ok) - set(status))}건")

    say("\n[입력일 연도별] 목록 / 받음 / 실패 / 안 받음")
    by_year: dict[str, Counter] = defaultdict(Counter)
    for t in targets:
        by_year[t["date"][:4] or "모름"][status[t["no"]]] += 1
    for y in sorted(by_year):
        c = by_year[y]
        say(f"  {y}: {sum(c.values())} / {c['받음']} / {c['실패']} / {c['안 받음']}")

    say("\n[업무별(목록의 업무 칸 그대로)] 목록 / 받음 / 실패 / 안 받음")
    by_work: dict[str, Counter] = defaultdict(Counter)
    for t in targets:
        by_work[t["work"] or "빈칸"][status[t["no"]]] += 1
    for w, c in sorted(by_work.items()):
        say(f"  {w}: {sum(c.values())} / {c['받음']} / {c['실패']} / {c['안 받음']}")

    got = [ok[t["no"]] for t in targets if t["no"] in ok]
    say(f"\n[받은 {len(got)}건의 내용]")
    say("  예비가격 행 수: " + ", ".join(f"{k}행 {v}건" for k, v in sorted(Counter(len(r.get("prices") or []) for r in got).items())))
    say(f"  참가업체 0곳: {sum(1 for r in got if not r.get('bidders'))}건")
    with_notice = [r for r in got if r.get("notice")]
    say(f"  낙찰방법 받음: {len(with_notice)}건 · 없음 {len(got) - len(with_notice)}건 (10/27 전에 받은 기록에는 낙찰방법 칸이 없음)")
    say("\n[낙찰방법 — 나라장터 원문 그대로]")
    for v, n in Counter(r["notice"].get("award_method", "") or "(빈칸)" for r in with_notice).most_common():
        say(f"  {n:5d}  {v}")
    say("\n[계약방법 — 나라장터 원문 그대로]")
    for v, n in Counter(r["notice"].get("contract_method", "") or "(빈칸)" for r in with_notice).most_common():
        say(f"  {n:5d}  {v}")

    say("\n[실패 사유] 건수")
    for v, n in Counter(e[:60] for no, e in err.items() if no in status).most_common():
        say(f"  {n:5d}  {v}")

    say("\n[참가업체 행의 칸 — 채워진 행 / 전체 행]")
    lines.extend(field_fill([b for r in got for b in r.get("bidders") or []]))
    say("\n[예비가격 행의 칸 — 채워진 행 / 전체 행]")
    lines.extend(field_fill([p for r in got for p in r.get("prices") or []]))

    if args.history:
        counts = history_counts(Path(args.history))
        same, diff, missing = 0, [], 0
        for r in got:
            raw = counts.get(r["input"])
            if raw is None:
                missing += 1
            elif raw.replace(",", "") == str(len(r.get("bidders") or [])):
                same += 1
            else:
                diff.append(f"{r['input']} (이력 {raw} · 받음 {len(r.get('bidders') or [])})")
        say(f"\n[투찰 이력 참여수 대조] 같음 {same} · 다름 {len(diff)} · 이력에 없음 {missing}")
        for d in diff[:30]:
            say(f"  {d}")
        if len(diff) > 30:
            say(f"  … 외 {len(diff) - 30}건")

    text = "\n".join(lines) + "\n"
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(text, encoding="utf-8")
    print(text)
    print(f"저장: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
