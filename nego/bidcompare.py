"""투찰한 공고 목록 → 방식별 추천 투찰가와 그 공고 실제 개찰 순위(외주 투찰가 순위와 나란히) — 추천 방식 고르는 규칙을 다시 정하는 자료.

10/29 사용자 요청: "다른 방식의 추천가를 썼다면 더 좋은 순위였을 공고가 꽤 많다 — 공고번호·외주 순위·추천 순위로 선정 방식을 다시 보자".
**서버 PC에서 실행**한다(개찰 자료·공고번호 목록 = 지일 투찰 이력 — 공개 저장소에 올리지 않음).

    py scripts/fetch_bid_results.py output/투찰목록.csv --out output/bid_results_recent.jsonl   # 개찰 결과 받기(이미 받은 건 건너뜀)
    py -m nego.bidcompare output/투찰목록.csv                                                  # → output/bid_compare_<날짜>.csv/.txt

공고마다 **그 공고 개찰일 전에 열린 자료만으로** 웹앱 추천 칸과 같은 계산(`bidrecommend.recommend`, 경쟁 규모 자동)을 다시 해서
표의 모든 방식 사정률 → 그 공고에 냈을 투찰금액 → 실제 참가업체 금액 사이 순위를 센다. 추천 칸이 골랐을 방식(`pick_option`)도 함께.
순위 = 하한선 이상 업체 중 그 금액보다 싼 곳 수 + 1(같은 금액은 앞에 둠), 하한선 아래면 '미달'. 하한선은 `PastBid.floor`(A값 → 참가업체로 가늠 → A=0).
화면 계산기의 '모의 예정가격 하한선' 대조(무작위 추첨)는 다시 못 하므로 뺀다 — 추천 칸이 고른 방식 그대로 채점.
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

from .bidbacktest import amount_for, date_of, rank_of
from .bidrecommend import (
    History,
    PastBid,
    default_own_bizno,
    from_result_record,
    history_paths,
    load_history,
    recommend,
    skip_reason,
)

OWN_COL = "외주(지일 실제)"


def read_numbers(path: Path) -> list[str]:
    """목록 CSV(첫 칸 공고번호, 차수 '-000'은 떼어 비교) — fetch_bid_results.py에 넣은 파일 그대로."""
    out: list[str] = []
    with path.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.reader(f):
            cell = (row[0] if row else "").strip()
            if cell and cell not in ("공고번호", "no"):
                no = cell.split()[0].partition("-")[0]
                if no not in out:
                    out.append(no)
    return out


def _rank_text(rank: int | None) -> str:
    return "미달" if rank is None else str(rank)


def compare_one(history: History, rec: PastBid) -> dict:
    """한 공고 — 개찰일 전 자료로 추천 칸을 다시 만들어 방식별 순위."""
    day = date_of(rec)
    train = [r for r in history.records if (date_of(r) or "9999") < (day or "0000") and r.notice_no != rec.notice_no]
    width = rec.range_width
    out = recommend(History(records=train), work=rec.work or None, lo=-width if width else None, hi=width if width else None,
                    scale="auto", basis=rec.basis, lower_rate=rec.lower_rate, title=rec.title,
                    institution=rec.institution, method=rec.method)  # 방식 값은 백테스트(`target_info`)와 같게 원문 그대로
    row: dict = {"공고번호": rec.notice_no, "개찰일": day or "", "공고명": rec.title, "업무": rec.work, "분야": rec.field,
                 "변동폭": f"±{width}" if width else "", "참가": rec.bidder_count, "하한선 출처": rec.floor_source,
                 "실제 사정률": round(rec.assessment, 4), "학습 공고": len(train), "비슷한 공고": out.get("samples", 0),
                 "경쟁 규모": (out.get("auto_scale") or out.get("scale") or ""), "methods": {}}
    for o in out.get("options") or []:
        amount = amount_for(rec, o["rate"])
        row["methods"][o["name"]] = (o["rate"], round(amount), rank_of(rec, amount))
    if rec.own_amount:
        own_rate = rec.rate_for(rec.own_amount)
        row["methods"][OWN_COL] = (own_rate, round(rec.own_amount), rank_of(rec, rec.own_amount, skip_own=False))
        row["외주 나라장터 순위"] = "미달" if rec.own_rank is None else rec.own_rank
    pick = out.get("pick")
    if pick:
        row["추천 방식"] = pick["name"]
        row["추천 모드"] = pick.get("mode", "")
    ranked = [(r, n) for n, (_, _, r) in row["methods"].items() if n != OWN_COL and r is not None]
    if ranked:
        best = min(r for r, _ in ranked)
        row["가장 좋은 순위"] = best
        row["가장 좋은 방식"] = " / ".join(n for r, n in ranked if r == best)
    return row


def compare(history: History, numbers: list[str], progress=print) -> tuple[list[dict], list[tuple[str, str]]]:
    by_no = {r.notice_no: r for r in history.records}
    rows, missing = [], []
    for i, no in enumerate(numbers, 1):
        rec = by_no.get(no)
        if rec is None:
            missing.append((no, ""))
            continue
        if not date_of(rec):
            missing.append((no, "개찰일 모름"))
            continue
        started = time.time()
        rows.append(compare_one(history, rec))
        progress(f"  [{i}/{len(numbers)}] {no} — {time.time() - started:.1f}초")
    return rows, missing


def missing_reasons(paths: list[Path], numbers: list[tuple[str, str]], own_bizno: str) -> list[tuple[str, str]]:
    """목록엔 있는데 채점 못 한 공고의 이유 — 받은 줄이 없음 / 못 쓴 줄(`skip_reason`)."""
    lines: dict[str, dict] = {}
    for path in paths:
        if path.suffix != ".jsonl":
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                item = json.loads(line)
            except ValueError:
                continue
            key = item.get("no") or str(item.get("input", "")).partition("-")[0]
            if key:
                lines[key] = item
    out = []
    for no, reason in numbers:
        if reason:
            out.append((no, reason))
        elif no not in lines:
            out.append((no, "개찰 결과를 아직 안 받음(fetch_bid_results.py로 받을 것)"))
        elif from_result_record(lines[no], own_bizno=own_bizno) is None:
            out.append((no, skip_reason(lines[no])))
        else:
            out.append((no, "알 수 없음"))
    return out


def method_names(rows: list[dict]) -> list[str]:
    names: list[str] = []
    for r in rows:
        names += [n for n in r["methods"] if n not in names and n != OWN_COL]
    return [OWN_COL] + names if any(OWN_COL in r["methods"] for r in rows) else names


BASE = ["공고번호", "개찰일", "공고명", "업무", "분야", "변동폭", "참가", "경쟁 규모", "하한선 출처", "실제 사정률",
        "학습 공고", "비슷한 공고", "외주 나라장터 순위", "추천 방식", "추천 모드", "추천 순위", "가장 좋은 순위", "가장 좋은 방식"]


def write_csv(rows: list[dict], missing: list[tuple[str, str]], path: Path) -> None:
    names = method_names(rows)
    head = BASE + [f"{n} {k}" for n in names for k in ("사정률", "투찰금액", "순위")]
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(head)
        for r in rows:
            pick = r["methods"].get(r.get("추천 방식", ""))
            cells = [r.get(k, "") for k in BASE]
            cells[BASE.index("추천 순위")] = _rank_text(pick[2]) if pick else ""
            for n in names:
                got = r["methods"].get(n)
                cells += [round(got[0], 4), got[1], _rank_text(got[2])] if got else ["", "", ""]
            w.writerow(cells)
        for no, reason in missing:
            w.writerow([no] + [""] * (len(BASE) - 2) + [f"채점 못 함: {reason}"])


def summarize(rows: list[dict], missing: list[tuple[str, str]]) -> str:
    """방식별 순위 요약(업체 이름 없음) — 공고별 숫자는 CSV에."""
    lines = [f"채점 공고 {len(rows)}건 · 못 한 공고 {len(missing)}건"]
    for reason, n in Counter(r for _, r in missing).most_common():
        lines.append(f"  못 한 이유: {reason} {n}건")
    if not rows:
        return "\n".join(lines)
    lines.append("")
    lines.append("방식별 — 채점 건수 · 하한선 통과 · 1순위 · 5위 안 · 10위 안 · 순위 중앙(통과한 공고) · 외주보다 좋음/같음/나쁨")
    for name in method_names(rows):
        got = [(r, r["methods"][name][2]) for r in rows if name in r["methods"]]
        if not got:
            continue
        ranks = [k for _, k in got if k is not None]
        n = len(got)
        line = (f"  {name:<34} {n:>3}건 · 통과 {len(ranks):>3} · 1순위 {sum(k == 1 for k in ranks):>2} · 5위 안 {sum(k <= 5 for k in ranks):>3}"
                f" · 10위 안 {sum(k <= 10 for k in ranks):>3} · 순위 중앙 {statistics.median(ranks) if ranks else '-'}")
        if name != OWN_COL:
            better = same = worse = 0
            for r, k in got:
                own = r["methods"].get(OWN_COL)
                if own is None:
                    continue
                a, b = (k if k is not None else 10**9), (own[2] if own[2] is not None else 10**9)
                better += a < b
                same += a == b
                worse += a > b
            line += f" · 외주보다 {better}/{same}/{worse}"
        lines.append(line)
    picked = [r for r in rows if r.get("추천 방식") in r["methods"]]
    if picked:
        lines.append("")
        lines.append(f"추천 칸이 고른 방식(지금 규칙) — {len(picked)}건: "
                     + " · ".join(f"{n} {c}" for n, c in Counter(r["추천 방식"] for r in picked).most_common()))
        worse = sum(1 for r in picked if r.get("가장 좋은 순위") is not None
                    and (r["methods"][r["추천 방식"]][2] or 10**9) > r["가장 좋은 순위"])
        lines.append(f"  다른 방식이 더 좋은 순위였던 공고 {worse}건")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="투찰한 공고 — 방식별 추천 투찰가의 실제 개찰 순위(외주와 나란히)")
    parser.add_argument("targets", help="공고번호 목록 CSV(첫 칸 공고번호 — fetch_bid_results.py에 넣은 파일)")
    parser.add_argument("--dir", default="output", help="과거 개찰 자료 폴더(기본 output)")
    parser.add_argument("--out", default="", help="결과 CSV(기본 <dir>/bid_compare_<오늘>.csv)")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")

    directory = Path(args.dir)
    paths = history_paths(directory)
    if not paths:
        print(f"{directory}에 bid_history_*.json · bid_results*.jsonl이 없습니다.", file=sys.stderr)
        return 1
    numbers = read_numbers(Path(args.targets))
    own = default_own_bizno()
    history = load_history(paths, own)
    print(f"과거 개찰 {len(history.records)}건 · 대상 공고 {len(numbers)}건 — 공고마다 수십 초 걸릴 수 있음")
    rows, missing = compare(history, numbers)
    missing = missing_reasons(paths, missing, own)
    text = summarize(rows, missing)
    print("\n" + text)
    stamp = f"{datetime.now():%Y%m%d}"
    out = Path(args.out) if args.out else directory / f"bid_compare_{stamp}.csv"
    write_csv(rows, missing, out)
    out.with_suffix(".txt").write_text(text + "\n", encoding="utf-8")
    print(f"\n공고별 결과: {out} (공개 저장소에 올리지 말 것)\n요약: {out.with_suffix('.txt')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
