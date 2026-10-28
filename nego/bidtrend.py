"""투찰 경향 — 과거 개찰 결과에서 분야(공고명 키워드)별로 1~5위 업체와 지일이 어떤 가격을 냈는지 모은다
(2026-10-28 사용자 결정: 근거 = 과거 개찰의 상위 1~5위 업체 투찰가와 지일 투찰가 경향, 분야 = 공고명 키워드).

가격은 공고마다 기초금액·하한율이 달라 금액 그대로는 못 견준다. 그래서 두 가지로 바꿔 본다.
  - **사정률 차(%p)** = 그 금액을 내려면 가정했어야 할 사정률 − 실제 사정률(`PastBid.rate_for`). 0이면 하한선에 딱 붙은 금액,
    +0.1이면 실제보다 0.1%p 높게 가정한 금액, 음수면 하한 미달. 새 공고에선 "예상 사정률 + 이만큼"으로 쓴다.
  - **투찰률 − 하한율(%p)** = 금액 ÷ 예정가격 × 100 − 하한율 — 업계에서 흔히 보는 값(A값이 있으면 사정률 차와 비례하지 않음).

서버 PC에서:

    py -m nego.bidtrend                      # output/ 의 과거 개찰 자료 전부
    py -m nego.bidtrend --from 2024-01-01    # 2024년 이후 개찰만

결과: 화면 + `output/bid_trend_<날짜>.txt`(분야별 요약, 업체 이름 없음) + `.csv`(공고별 — 지일 투찰 공고 목록이라 공개 금지).
"""

from __future__ import annotations

import argparse
import csv
import os
import statistics
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

from .bidrecommend import LARGE_COMPETITION, TOP_N, PastBid, _pct, default_own_bizno, history_paths, load_history


def _spread(values: list[float]) -> dict:
    if not values:
        return {"n": 0}
    return {"n": len(values), "p25": _pct(values, 0.25), "p50": statistics.median(values), "p75": _pct(values, 0.75)}


def trend(records: list[PastBid], k: int = TOP_N) -> dict:
    """공고 묶음의 1~k위·지일 경향. ranks[i] = (i+1)위 업체의 사정률 차·투찰률−하한율 분포, own = 지일."""
    ranks = []
    for i in range(k):
        has = [r for r in records if len(r.top_amounts) > i]
        ranks.append({
            "rank": i + 1,
            "gap": _spread([r.rate_for(r.top_amounts[i]) - r.assessment for r in has]),
            "bid_gap": _spread([r.top_amounts[i] / r.planned * 100 - r.lower_rate for r in has]),
        })
    own = [r for r in records if r.own_amount]
    out: dict = {"n": len(records), "ranks": ranks, "own": None,
                 "bidders": statistics.median(r.bidder_count for r in records) if records else None}
    if own:
        ranked = [r.own_rank for r in own if r.own_rank is not None]
        vs_first = [r.rate_for(r.own_amount) - r.rate_for(r.top_amounts[0]) for r in own if r.top_amounts]
        out["own"] = {
            "gap": _spread([r.rate_for(r.own_amount) - r.assessment for r in own]),
            "bid_gap": _spread([r.own_amount / r.planned * 100 - r.lower_rate for r in own]),
            "tried": len(own), "valid": len(ranked), "top": sum(x <= k for x in ranked), "win": sum(x == 1 for x in ranked),
            "rank_p50": statistics.median(ranked) if ranked else None,
            # 같은 공고에서 지일이 1위보다 사정률을 몇 %p 높게(+)·낮게(−) 잡았나 — 하한 미달 공고도 포함
            "vs_first": _spread(vs_first),
        }
    return out


def by_field(records: list[PastBid], k: int = TOP_N) -> list[dict]:
    """분야별 경향(건수 많은 순) — 맨 앞은 전체."""
    counts = Counter(r.field for r in records)
    out = [{"field": "전체", **trend(records, k)}]
    for name, _ in counts.most_common():
        out.append({"field": name, **trend([r for r in records if r.field == name], k)})
    return out


def _fmt(s: dict, key: str = "p50") -> str:
    return f"{s[key]:+.3f}" if s.get("n") else "-"


def _block(title: str, t: dict, k: int) -> list[str]:
    lines = [f"\n[{title}] 공고 {t['n']}건 · 참가 중앙 {t['bidders']:g}곳",
             f"  {'구분':<8} {'건수':>5}  사정률 차(%p) 25%·중앙·75%        투찰률−하한율 중앙"]
    for row in t["ranks"]:
        g = row["gap"]
        lines.append(f"  {str(row['rank']) + '위':<8} {g['n']:>5}  {_fmt(g, 'p25')} · {_fmt(g)} · {_fmt(g, 'p75')}"
                     f"        {_fmt(row['bid_gap'])}")
    own = t.get("own")
    if own:
        g = own["gap"]
        pct = lambda n: f"{n}건({n / own['tried']:.0%})"  # noqa: E731
        lines.append(f"  {'지일':<8} {own['tried']:>5}  {_fmt(g, 'p25')} · {_fmt(g)} · {_fmt(g, 'p75')}"
                     f"        {_fmt(own['bid_gap'])}")
        lines.append(f"    지일: 유효 {pct(own['valid'])} · {k}위 안 {pct(own['top'])} · 1순위 {pct(own['win'])}"
                     + (f" · 순위 중앙 {own['rank_p50']:g}위" if own["rank_p50"] is not None else "")
                     + f" · 1위보다 사정률 {_fmt(own['vs_first'])}%p(중앙)")
    return lines


def summarize(records: list[PastBid], k: int = TOP_N, min_count: int = 5) -> str:
    if not records:
        return "과거 개찰 자료가 없습니다."
    lines = ["사정률 차 = 그 금액을 내려면 가정했어야 할 사정률 − 실제 사정률(0 = 하한선에 딱 붙음, 음수 = 하한 미달)."]
    for item in by_field(records, k):
        if item["n"] < min_count:
            lines.append(f"\n[{item['field']}] 공고 {item['n']}건 — {min_count}건 미만이라 생략")
            continue
        lines += _block(item["field"], item, k)
        subset = records if item["field"] == "전체" else [r for r in records if r.field == item["field"]]
        small = [r for r in subset if r.bidder_count < LARGE_COMPETITION]
        large = [r for r in subset if r.bidder_count >= LARGE_COMPETITION]
        if len(small) >= min_count and len(large) >= min_count:
            lines += _block(f"{item['field']} · 참가 {LARGE_COMPETITION}곳 미만", trend(small, k), k)
            lines += _block(f"{item['field']} · 참가 {LARGE_COMPETITION}곳 이상", trend(large, k), k)
    lines.append(f"\n※ 순위는 금액 기준 개찰 순위(적격심사 서류 심사 전). 분야는 공고명 키워드(config/bid_fields.json) — "
                 "공고명을 모르는 공고는 '공고명 없음'.")
    return "\n".join(lines)


def write_csv(records: list[PastBid], path: Path, k: int = TOP_N) -> None:
    from .bidbacktest import date_of

    head = ["공고번호", "개찰일", "분야", "공고명", "업무", "참가", "실제 사정률", "하한선"]
    head += [f"{i}위 사정률 차" for i in range(1, k + 1)] + ["지일 사정률 차", "지일 순위"]
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(head)
        for r in sorted(records, key=lambda x: date_of(x) or ""):
            cells = [r.notice_no, date_of(r) or "", r.field, r.title, r.work, r.bidder_count, round(r.assessment, 4),
                     r.floor_source]
            cells += [round(r.rate_for(r.top_amounts[i]) - r.assessment, 4) if len(r.top_amounts) > i else ""
                      for i in range(k)]
            cells += [round(r.rate_for(r.own_amount) - r.assessment, 4) if r.own_amount else "",
                      r.own_rank if r.own_rank is not None else ("미달" if r.own_amount else "")]
            w.writerow(cells)


def main(argv: list[str] | None = None) -> int:
    from .bidbacktest import date_of

    parser = argparse.ArgumentParser(description="투찰 경향 — 분야별 1~5위 업체·지일 투찰가")
    parser.add_argument("--dir", default=os.environ.get("BID_HISTORY_DIR") or os.environ.get("OUTPUT_DIR") or "output",
                        help="과거 개찰 자료 폴더(기본 output)")
    parser.add_argument("--from", dest="start", default="", help="이 날짜(YYYY-MM-DD) 이후 개찰만")
    parser.add_argument("--min", type=int, default=5, help="이보다 적은 분야는 생략(기본 5)")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")

    directory = Path(args.dir)
    paths = history_paths(directory)
    if not paths:
        print(f"{directory}에 bid_history_*.json · bid_results*.jsonl이 없습니다.", file=sys.stderr)
        return 1
    history = load_history(paths, default_own_bizno())
    records = [r for r in history.records if not args.start or (date_of(r) or "") >= args.start]
    no_title = sum(not r.title for r in records)
    head = f"과거 개찰 {len(records)}건 (못 쓴 공고 {history.skipped}건) — {', '.join(history.files)}"
    if no_title:
        head += (f"\n  공고명 모름 {no_title}건 — 분야를 못 나눔('공고명 없음'). "
                 "`py scripts/fetch_bid_results.py --fill-notice`로 공고명을 채우면 들어감")
    text = head + "\n" + summarize(records, min_count=args.min)
    print(text)
    stamp = f"{datetime.now():%Y%m%d}"
    (directory / f"bid_trend_{stamp}.txt").write_text(text + "\n", encoding="utf-8")
    out = directory / f"bid_trend_{stamp}.csv"
    write_csv(records, out)
    print(f"\n요약: {directory / f'bid_trend_{stamp}.txt'}\n공고별: {out} (공개 저장소에 올리지 말 것)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
