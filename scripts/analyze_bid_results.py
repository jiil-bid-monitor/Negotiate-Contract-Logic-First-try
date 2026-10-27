"""개찰결과(참가업체 전체 투찰률·예비가격) 투찰 경향 사전분석 — 묶음별 중앙값·분포 비율만 출력(업체명·사업자번호·금액 X).

서버 PC에서 실행하고 결과 파일(output/bid_results_trend.txt)을 전해 준다(10/27 사용자 결정 — 원본은 서버 PC 밖으로 안 냄).

    py scripts/analyze_bid_results.py data/g2b_all_targets.csv
    py scripts/analyze_bid_results.py data/g2b_all_targets.csv --history data/bid_history   # API 하한율이 0·빈칸이면 투찰 이력 하한율로

값(모두 나라장터 원문에서 계산, 추정 없음):
- 실제 사정률 = 예정가격(plnprc) ÷ 기초금액(bssamt) × 100
- 투찰률 − 하한율: 업체마다 투찰률(bidprcrt, 예정가격 대비 %) − 낙찰하한율. 0보다 작으면 하한 미달
- 개찰 1위 = opengRank 1. 하한 이상 최저 = 하한율 이상 투찰률 중 가장 낮은 것
묶음: 업무(목록 칸 그대로), 참가 규모(받은 참가업체 수), 입력일 연도, 낙찰방법(협상 / 그 외 / 모름 — 나라장터 낙찰방법 원문에 "협상"이
있는지만 봄, "공고서참조"·없음은 모름). 하한율을 못 구한 공고는 하한율 표에서만 빼고 건수를 적는다.

하한율 없이 보는 표(1부, 받은 공고 전부 — 10/27 사용자 요청):
- 업체 투찰금액 ÷ 기초금액 × 100: 업체가 공개된 기초금액을 보고 넣은 값(하한율·A값을 빼지 않은 비율 — 사정률과 같은 값 아님)
- 투찰률(bidprcrt), 개찰 1위·2위 투찰률 차이, 개찰순위가 빈 업체 비율(순위 빈칸 = 나라장터 원문 그대로, 이유는 해석 안 함)
- 지일: 투찰금액 ÷ 기초금액, 개찰 1위와의 차이, 순위 백분위(순위 ÷ 순위 있는 업체 수)
"""

from __future__ import annotations

import argparse
import json
import os
import statistics as st
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from check_bid_results import read_results, read_targets  # noqa: E402
from nego.bidprice import parse_bidders, parse_prepare_prices  # noqa: E402

LOWER_COL = 6  # 투찰 이력 표의 하한율 칸
# 투찰률 − 하한율(%p) 구간
BINS = [(-1e9, -1, "−1 미만"), (-1, -0.1, "−1~−0.1"), (-0.1, 0, "−0.1~0"), (0, 0.05, "0~0.05"), (0.05, 0.1, "0.05~0.1"),
        (0.1, 0.3, "0.1~0.3"), (0.3, 1, "0.3~1"), (1, 3, "1~3"), (3, 1e9, "3 이상")]


def jiil_bizno() -> str:
    env = os.environ.get("JIIL_BIZNO", "").strip()
    if env:
        return env.replace("-", "")
    held = json.loads((ROOT / "config" / "held_qualifications.json").read_text(encoding="utf-8"))
    return str(held.get("companyBizno", "")).replace("-", "")


def history_lower(folder: Path) -> dict[str, float]:
    """투찰 이력 → 공고번호 → 하한율(0·빈칸은 뺌)."""
    import re

    from lxml import html

    out: dict[str, float] = {}
    for f in sorted(folder.glob("*/*.xls")):
        for tr in html.fromstring(f.read_text(encoding="utf-8")).iter("tr"):
            cells = [re.sub(r"\s+", " ", td.text_content()).strip() for td in tr.findall("td")]
            if len(cells) <= LOWER_COL or not cells[0].isdigit() or not cells[2].strip():
                continue
            try:
                v = float(cells[LOWER_COL].replace(",", ""))
            except ValueError:
                continue
            if v > 0:
                out[cells[2].replace("호", " ").split()[-1]] = v
    return out


def _float(v) -> float | None:
    try:
        f = float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None
    return f if f > 0 else None


def size_group(n: int) -> str:
    return "1~9곳" if n < 10 else "10~99곳" if n < 100 else "100~299곳" if n < 300 else "300곳 이상"


def method_group(rec: dict) -> str:
    award = (rec.get("notice") or {}).get("award_method", "")
    if "협상" in award:
        return "협상"
    if award and award != "공고서참조":
        return "그 외"
    return "모름"


def notice_stats(rec: dict, lower: float, own: str) -> dict | None:
    planned, basis, _ = parse_prepare_prices(rec.get("prices") or [])
    bidders = [b for b in parse_bidders(rec.get("bidders") or []) if b.rate is not None]
    if not bidders:
        return None
    gaps = [b.rate - lower for b in bidders]
    first = [b.rate - lower for b in bidders if b.rank == 1]
    above = [g for g in gaps if g >= 0]
    mine = [b for b in bidders if b.bizno.replace("-", "") == own]
    return {
        "n": len(bidders),
        "act": planned / basis * 100 - 100 if planned and basis else None,
        "gaps": gaps,
        "first": first[0] if first else None,
        "lowest_ok": min(above) if above else None,
        "under": sum(g < 0 for g in gaps) / len(gaps),
        "near": sum(0 <= g < 0.1 for g in gaps) / len(gaps),
        "own_gap": mine[0].rate - lower if mine else None,
        "own_first": bool(mine) and mine[0].rank == 1,
    }


# 투찰금액 ÷ 기초금액(%) 구간
RATIO_BINS = [(-1e9, 84, "84 미만"), (84, 86, "84~86"), (86, 87, "86~87"), (87, 88, "87~88"), (88, 89, "88~89"),
              (89, 90, "89~90"), (90, 92, "90~92"), (92, 1e9, "92 이상")]


def plain_stats(rec: dict, own: str) -> dict | None:
    """하한율 없이 계산하는 공고 통계. 투찰률·금액이 있는 참가업체가 없으면 None."""
    planned, basis, _ = parse_prepare_prices(rec.get("prices") or [])
    bidders = parse_bidders(rec.get("bidders") or [])
    if not any(b.rate is not None or b.amount for b in bidders):
        return None
    ratios = [b.amount / basis * 100 for b in bidders if b.amount and basis]
    by_rank = {b.rank: b for b in bidders if b.rank is not None}
    first, second = by_rank.get(1), by_rank.get(2)
    ranked = len(by_rank)
    mine = [b for b in bidders if b.bizno.replace("-", "") == own]
    me = mine[0] if mine else None
    first_ratio = first.amount / basis * 100 if first and first.amount and basis else None
    own_ratio = me.amount / basis * 100 if me and me.amount and basis else None
    return {
        "n": len(bidders),
        "act": planned / basis * 100 - 100 if planned and basis else None,
        "ratios": ratios,
        "ratio_med": st.median(ratios) if ratios else None,
        "first_ratio": first_ratio,
        "first_rate": first.rate if first else None,
        "gap12": second.rate - first.rate if first and second and first.rate is not None and second.rate is not None else None,
        "no_rank": sum(b.rank is None for b in bidders) / len(bidders),
        "own": me is not None,
        "own_ratio": own_ratio,
        "own_vs_first": own_ratio - first_ratio if own_ratio is not None and first_ratio is not None else None,
        "own_pct": me.rank / ranked if me and me.rank and ranked else None,
        "own_no_rank": me is not None and me.rank is None,
        "own_first": me is not None and me.rank == 1,
    }


def fmt(vals: list, sign: bool = True) -> str:
    v = sorted(x for x in vals if x is not None)
    if not v:
        return "-"
    f = "{:+.3f}" if sign else "{:.3f}"
    if len(v) < 4:
        return f.format(st.median(v))
    q = st.quantiles(v, n=4)
    return f"{f.format(st.median(v))} ({f.format(q[0])}~{f.format(q[2])})"


def plain_table(say, title: str, items: list[tuple[str, dict]]) -> None:
    groups: dict[str, list[dict]] = defaultdict(list)
    for key, s in items:
        groups[key].append(s)
    say(f"\n## [하한율 없이] {title}")
    say("묶음 | 공고 | 참가 중앙 | 실제 사정률−100 | 업체 투찰금액÷기초금액(공고별 중앙값의 중앙) | 개찰1위 투찰금액÷기초금액 | 개찰1위 투찰률 | 1·2위 투찰률 차이 | 순위 빈칸 업체 비율")
    for k in sorted(groups):
        g = groups[k]
        say(f"{k} | {len(g)} | {int(st.median(s['n'] for s in g))} | {fmt([s['act'] for s in g])} | {fmt([s['ratio_med'] for s in g], False)} | "
            f"{fmt([s['first_ratio'] for s in g], False)} | {fmt([s['first_rate'] for s in g], False)} | {fmt([s['gap12'] for s in g], False)} | "
            f"{st.median(s['no_rank'] for s in g):.0%}")
    say(f"\n[[하한율 없이] {title} — 업체 투찰금액÷기초금액(%) 구간별 비율(전체 업체 기준)]")
    say("묶음 | 업체 수 | " + " | ".join(b[2] for b in RATIO_BINS))
    for k in sorted(groups):
        vals = [x for s in groups[k] for x in s["ratios"]]
        if not vals:
            say(f"{k} | 0 | -")
            continue
        cnt = Counter(next(label for lo, hi, label in RATIO_BINS if lo <= x < hi) for x in vals)
        say(f"{k} | {len(vals)} | " + " | ".join(f"{cnt[b[2]] / len(vals):.0%}" for b in RATIO_BINS))


def med(vals: list) -> str:
    v = sorted(x for x in vals if x is not None)
    if not v:
        return "-"
    if len(v) < 4:
        return f"{st.median(v):+.3f}"
    q = st.quantiles(v, n=4)
    return f"{st.median(v):+.3f} ({q[0]:+.3f}~{q[2]:+.3f})"


def table(say, title: str, items: list[tuple[str, dict]]) -> None:
    groups: dict[str, list[dict]] = defaultdict(list)
    for key, s in items:
        groups[key].append(s)
    say(f"\n## {title}")
    say("묶음 | 공고 | 참가 중앙 | 실제 사정률−100 | 개찰1위 투찰률−하한율 | 하한 이상 최저 | 하한 미달 업체 비율 | 하한~+0.1 업체 비율")
    for k in sorted(groups):
        g = groups[k]
        say(f"{k} | {len(g)} | {int(st.median(s['n'] for s in g))} | {med([s['act'] for s in g])} | {med([s['first'] for s in g])} | "
            f"{med([s['lowest_ok'] for s in g])} | {st.median(s['under'] for s in g):.0%} | {st.median(s['near'] for s in g):.0%}")
    say(f"\n[{title} — 업체 투찰률−하한율 구간별 비율(전체 업체 기준)]")
    say("묶음 | 업체 수 | " + " | ".join(b[2] for b in BINS))
    for k in sorted(groups):
        gaps = [x for s in groups[k] for x in s["gaps"]]
        cnt = Counter(next(label for lo, hi, label in BINS if lo <= x < hi) for x in gaps)
        say(f"{k} | {len(gaps)} | " + " | ".join(f"{cnt[b[2]] / len(gaps):.0%}" for b in BINS))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="개찰결과 투찰 경향 사전분석(묶음별 통계만)")
    parser.add_argument("targets", help="수집 목록 CSV (공고번호, 업무, 기관, 입력일)")
    parser.add_argument("--results", default=str(ROOT / "output" / "bid_results.jsonl"), help="수집 결과 JSONL")
    parser.add_argument("--history", default="", help="투찰 이력 폴더(API 하한율이 0·빈칸일 때만 씀)")
    parser.add_argument("--out", default=str(ROOT / "output" / "bid_results_trend.txt"), help="결과 파일")
    args = parser.parse_args(argv)

    targets = {t["no"]: t for t in read_targets(Path(args.targets))}
    ok, _ = read_results(Path(args.results))
    hist = history_lower(Path(args.history)) if args.history else {}
    own = jiil_bizno()
    lines: list[str] = []
    say = lines.append

    items = []
    plain_items = []
    plain_skipped = Counter()
    skipped = Counter()
    src = Counter()
    mismatch = 0
    for no, rec in ok.items():
        t = targets.get(no)
        if t is None:
            skipped["목록에 없는 공고"] += 1
            plain_skipped["목록에 없는 공고"] += 1
            continue
        ps = plain_stats(rec, own)
        if ps is None:
            plain_skipped["투찰률·금액 있는 참가업체 없음"] += 1
        else:
            ps.update(work=t["work"] or "빈칸", year=t["date"][:4] or "모름", method=method_group(rec), size=size_group(ps["n"]))
            plain_items.append(ps)
        api_lower = _float((rec.get("notice") or {}).get("lower_rate"))
        lower = api_lower or hist.get(no)
        if api_lower and no in hist and abs(api_lower - hist[no]) > 1e-9:
            mismatch += 1
        if lower is None:
            skipped["하한율 없음(API 0·빈칸, 이력에도 없음)"] += 1
            continue
        src["API" if api_lower else "투찰 이력"] += 1
        s = notice_stats(rec, lower, own)
        if s is None:
            skipped["투찰률 있는 참가업체 없음"] += 1
            continue
        s.update(work=t["work"] or "빈칸", year=t["date"][:4] or "모름", method=method_group(rec), size=size_group(s["n"]))
        items.append(s)

    say(f"사전분석 — 지금 저장된 개찰결과 기준(전수 수집 전이라 목록 전체를 대표하지 않음)")
    say("\n# 1부. 하한율 없이 보는 경향")
    say(f"성공 공고 {len(ok)}건 중 분석 {len(plain_items)}건 · 뺀 공고 {dict(plain_skipped)}")
    say("투찰금액÷기초금액은 하한율·A값을 빼지 않은 비율(%), 사정률과 같은 값 아님. 투찰률은 나라장터 원문(예정가격 대비 %)")
    plain_table(say, "낙찰방법별", [(s["method"], s) for s in plain_items])
    pplain = [s for s in plain_items if s["method"] != "협상"]
    say(f"\n※ 아래 표는 낙찰방법 '협상'을 뺀 {len(pplain)}건 — '모름'은 포함")
    plain_table(say, "업무별", [(s["work"], s) for s in pplain])
    plain_table(say, "참가 규모별", [(s["size"], s) for s in pplain])
    plain_table(say, "업무 × 참가 규모", [(f"{s['work']} · {s['size']}", s) for s in pplain])
    plain_table(say, "입력일 연도별", [(s["year"], s) for s in pplain])
    pmine = [s for s in pplain if s["own"]]
    say(f"\n## [하한율 없이] 지일 — 참가 확인 {len(pmine)}건")
    say("참가 규모 | 공고 | 지일 투찰금액÷기초금액 | 지일 − 개찰1위(투찰금액÷기초금액) | 지일 순위 백분위 | 지일 순위 빈칸 | 개찰 1위")
    pby: dict[str, list[dict]] = defaultdict(list)
    for s in pmine:
        pby[s["size"]].append(s)
    for k in sorted(pby):
        g = pby[k]
        say(f"{k} | {len(g)} | {fmt([s['own_ratio'] for s in g], False)} | {fmt([s['own_vs_first'] for s in g])} | "
            f"{fmt([s['own_pct'] for s in g], False)} | {sum(s['own_no_rank'] for s in g) / len(g):.0%} | {sum(s['own_first'] for s in g)}")

    say("\n# 2부. 하한율 기준 경향(하한율을 구한 공고만)")
    say(f"성공 공고 {len(ok)}건 중 분석 {len(items)}건 · 뺀 공고 {dict(skipped)}")
    say(f"하한율 출처: {dict(src)} · API와 투찰 이력 하한율이 다른 공고 {mismatch}건(API 값을 씀)")
    say("값 단위 %p, 중앙값(가운데 50% 범위)")

    table(say, "낙찰방법별", [(s["method"], s) for s in items])
    plain = [s for s in items if s["method"] != "협상"]
    say(f"\n※ 아래 표는 낙찰방법 '협상'을 뺀 {len(plain)}건 — '모름'(낙찰방법 공고서참조·없음)은 포함")
    table(say, "업무별", [(s["work"], s) for s in plain])
    table(say, "참가 규모별", [(s["size"], s) for s in plain])
    table(say, "업무 × 참가 규모", [(f"{s['work']} · {s['size']}", s) for s in plain])
    table(say, "입력일 연도별", [(s["year"], s) for s in plain])

    mine = [s for s in plain if s["own_gap"] is not None]
    say(f"\n## 지일(사업자번호 {own[:3]}-…) — 참가 확인 {len(mine)}건")
    by: dict[str, list[dict]] = defaultdict(list)
    for s in mine:
        by[s["size"]].append(s)
    say("참가 규모 | 공고 | 지일 투찰률−하한율 | 하한 미달 | 개찰 1위")
    for k in sorted(by):
        g = by[k]
        say(f"{k} | {len(g)} | {med([s['own_gap'] for s in g])} | {sum(s['own_gap'] < 0 for s in g) / len(g):.0%} | {sum(s['own_first'] for s in g)}")

    text = "\n".join(lines) + "\n"
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(text, encoding="utf-8")
    print(text)
    print(f"저장: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
