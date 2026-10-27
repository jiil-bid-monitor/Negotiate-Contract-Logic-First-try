"""개찰결과(참가업체 전체 투찰률·예비가격) 투찰 경향 사전분석 — 묶음별 중앙값·분포 비율만 출력(업체명·사업자번호·금액 X).

서버 PC에서 실행하고 결과 파일(output/bid_results_trend.txt)을 전해 준다(10/27 사용자 결정 — 원본은 서버 PC 밖으로 안 냄).

    py scripts/analyze_bid_results.py data/g2b_all_targets.csv
    py scripts/analyze_bid_results.py data/g2b_all_targets.csv --history data/bid_history   # API 하한율이 0·빈칸이면 투찰 이력 하한율로
    (A값은 수집 결과의 basis, 없으면 output/bid_basis.jsonl — fetch_bid_results.py --basis-only로 받은 것.
     낙찰방법·하한율은 수집 결과의 notice, 없으면 output/bid_notices.jsonl — --notice-only로 받은 것. 10/23 수집 383건 보충용)

받은 공고를 전부 한 표에 넣되, 공고마다 하한선을 구했는지에 맞는 비율로 계산해 칸을 나눈다(10/27 사용자 결정 — 두 비율은 단위가 달라 섞지 않음):
- 하한선 확인 공고: 업체 투찰률(bidprcrt, 예정가격 대비 %) − 하한선 투찰률. 0보다 작으면 하한 미달
  하한선 투찰률 = A값 적용 공고(기초금액 조회 bidPrceCalclAYn=Y)면 (A값 + (예정가격 − A값) × 하한율) ÷ 예정가격 × 100,
  아니면 하한율 그대로. A값 공고를 하한율만으로 계산하면 하한선이 낮게 잡힘(10/27 공고문 3건으로 확인 — 2020년 이상치의 원인)
  하한율(API 0·빈칸이면 --history 투찰 이력 값)과 기초금액 조회 결과(수집 결과의 basis 또는 --basis 파일)가 둘 다 있어야 확인
- 하한선 미확인 공고: 업체 투찰금액 ÷ 기초금액 × 100(하한율·A값을 빼지 않은 비율 — 사정률과 같은 값 아님)
- 공통: 실제 사정률 = 예정가격(plnprc) ÷ 기초금액(bssamt) × 100, 개찰 1위(opengRank 1)·2위 투찰률 차이,
  개찰순위 빈칸 업체 비율(나라장터 원문 그대로, 이유는 해석 안 함)
묶음: 업무(목록 칸 그대로), 참가 규모(받은 참가업체 수), 입력일 연도, 낙찰방법(협상 / 그 외 / 모름 — 나라장터 낙찰방법 원문에 "협상"이
있는지만 봄, "공고서참조"·없음은 모름). 모두 나라장터 원문에서 계산, 추정 없음.
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
    """rec = 수집 결과 한 줄 또는 {"notice": …}."""
    award = (rec.get("notice") or {}).get("award_method", "")
    if "협상" in award:
        return "협상"
    if award and award != "공고서참조":
        return "그 외"
    return "모름"


# 투찰금액 ÷ 기초금액(%) 구간
RATIO_BINS = [(-1e9, 84, "84 미만"), (84, 86, "84~86"), (86, 87, "86~87"), (87, 88, "87~88"), (88, 89, "88~89"),
              (89, 90, "89~90"), (90, 92, "90~92"), (92, 1e9, "92 이상")]


def floor_rate(lower: float | None, basis: dict | None, planned: float | None) -> tuple[float | None, str]:
    """하한선 투찰률(예정가격 대비 %)과 출처. A값 적용 공고는 A값을 깎지 않으므로 하한율보다 높다."""
    if lower is None:
        return None, "하한율 없음"
    if basis is None:
        return None, "기초금액(A값) 정보 없음"
    a = basis.get("a_value") or 0
    if basis.get("a_applies") and a > 0:
        if not planned:
            return None, "A값 공고인데 예정가격 없음"
        return (a + (planned - a) * lower / 100) / planned * 100, "A값 반영"
    return lower, "하한율 그대로(A값 미적용)"


def notice_stats(rec: dict, lower: float | None, own: str) -> dict | None:
    """공고 하나의 통계. lower(하한선 투찰률)가 있으면 투찰률−하한선, 없으면 투찰금액÷기초금액 칸을 채움. 쓸 참가업체가 없으면 None."""
    planned, basis, _ = parse_prepare_prices(rec.get("prices") or [])
    bidders = parse_bidders(rec.get("bidders") or [])
    if not any(b.rate is not None or b.amount for b in bidders):
        return None
    by_rank = {b.rank: b for b in bidders if b.rank is not None}
    first, second = by_rank.get(1), by_rank.get(2)
    mine = [b for b in bidders if b.bizno.replace("-", "") == own]
    me = mine[0] if mine else None
    s = {
        "n": len(bidders),
        "has_lower": lower is not None,
        "act": planned / basis * 100 - 100 if planned and basis else None,
        "gap12": second.rate - first.rate if first and second and first.rate is not None and second.rate is not None else None,
        "no_rank": sum(b.rank is None for b in bidders) / len(bidders),
        "own": me is not None,
        "own_pct": me.rank / len(by_rank) if me and me.rank else None,
        "own_first": me is not None and me.rank == 1,
        "gaps": [], "first": None, "lowest_ok": None, "under": None, "near": None, "own_gap": None,
        "ratios": [], "ratio_med": None, "first_ratio": None, "own_ratio": None, "own_vs_first": None,
    }
    if lower is not None:
        gaps = [b.rate - lower for b in bidders if b.rate is not None]
        above = [g for g in gaps if g >= 0]
        s.update(
            gaps=gaps,
            first=first.rate - lower if first and first.rate is not None else None,
            lowest_ok=min(above) if above else None,
            under=sum(g < 0 for g in gaps) / len(gaps) if gaps else None,
            near=sum(0 <= g < 0.1 for g in gaps) / len(gaps) if gaps else None,
            own_gap=me.rate - lower if me and me.rate is not None else None,
        )
    elif basis:
        ratios = [b.amount / basis * 100 for b in bidders if b.amount]
        first_ratio = first.amount / basis * 100 if first and first.amount else None
        own_ratio = me.amount / basis * 100 if me and me.amount else None
        s.update(
            ratios=ratios,
            ratio_med=st.median(ratios) if ratios else None,
            first_ratio=first_ratio,
            own_ratio=own_ratio,
            own_vs_first=own_ratio - first_ratio if own_ratio is not None and first_ratio is not None else None,
        )
    return s


def fmt(vals: list, sign: bool = True) -> str:
    v = sorted(x for x in vals if x is not None)
    if not v:
        return "-"
    f = "{:+.3f}" if sign else "{:.3f}"
    if len(v) < 4:
        return f.format(st.median(v))
    q = st.quantiles(v, n=4)
    return f"{f.format(st.median(v))} ({f.format(q[0])}~{f.format(q[2])})"


def share(vals: list) -> str:
    v = [x for x in vals if x is not None]
    return f"{st.median(v):.0%}" if v else "-"


def bins_line(key: str, vals: list[float], bins: list) -> str:
    if not vals:
        return f"{key} | 0 | -"
    cnt = Counter(next(label for lo, hi, label in bins if lo <= x < hi) for x in vals)
    return f"{key} | {len(vals)} | " + " | ".join(f"{cnt[b[2]] / len(vals):.0%}" for b in bins)


def table(say, title: str, items: list[tuple[str, dict]]) -> None:
    groups: dict[str, list[dict]] = defaultdict(list)
    for key, s in items:
        groups[key].append(s)
    say(f"\n## {title}")
    say("묶음 | 공고 | 참가 중앙 | 실제 사정률−100 | 1·2위 투찰률 차이 | 순위 빈칸 업체 비율"
        " ‖ [하한선 확인] 공고 | 개찰1위 투찰률−하한선 | 하한 이상 최저 | 하한 미달 업체 비율 | 하한~+0.1 업체 비율"
        " ‖ [하한선 미확인] 공고 | 업체 투찰금액÷기초금액(공고별 중앙값의 중앙) | 개찰1위 투찰금액÷기초금액")
    for k in sorted(groups):
        g = groups[k]
        lo = [s for s in g if s["has_lower"]]
        no = [s for s in g if not s["has_lower"]]
        say(f"{k} | {len(g)} | {int(st.median(s['n'] for s in g))} | {fmt([s['act'] for s in g])} | {fmt([s['gap12'] for s in g], False)} | "
            f"{share([s['no_rank'] for s in g])}"
            f" ‖ {len(lo)} | {fmt([s['first'] for s in lo])} | {fmt([s['lowest_ok'] for s in lo])} | {share([s['under'] for s in lo])} | "
            f"{share([s['near'] for s in lo])}"
            f" ‖ {len(no)} | {fmt([s['ratio_med'] for s in no], False)} | {fmt([s['first_ratio'] for s in no], False)}")
    say(f"\n[{title} — 하한선 확인 공고: 업체 투찰률−하한선(%p) 구간별 비율(전체 업체 기준)]")
    say("묶음 | 업체 수 | " + " | ".join(b[2] for b in BINS))
    for k in sorted(groups):
        say(bins_line(k, [x for s in groups[k] if s["has_lower"] for x in s["gaps"]], BINS))
    say(f"\n[{title} — 하한선 미확인 공고: 업체 투찰금액÷기초금액(%) 구간별 비율(전체 업체 기준)]")
    say("묶음 | 업체 수 | " + " | ".join(b[2] for b in RATIO_BINS))
    for k in sorted(groups):
        say(bins_line(k, [x for s in groups[k] if not s["has_lower"] for x in s["ratios"]], RATIO_BINS))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="개찰결과 투찰 경향 사전분석(묶음별 통계만)")
    parser.add_argument("targets", help="수집 목록 CSV (공고번호, 업무, 기관, 입력일)")
    parser.add_argument("--results", default=str(ROOT / "output" / "bid_results.jsonl"), help="수집 결과 JSONL")
    parser.add_argument("--history", default="", help="투찰 이력 폴더(API 하한율이 0·빈칸일 때만 씀)")
    parser.add_argument("--basis", default=str(ROOT / "output" / "bid_basis.jsonl"),
                        help="A값·기초금액 결과(fetch_bid_results.py --basis-only). 수집 결과에 basis가 없는 공고만 씀")
    parser.add_argument("--notices", default=str(ROOT / "output" / "bid_notices.jsonl"),
                        help="낙찰방법·하한율 결과(fetch_bid_results.py --notice-only). 수집 결과에 notice가 없는 공고만 씀")
    parser.add_argument("--out", default=str(ROOT / "output" / "bid_results_trend.txt"), help="결과 파일")
    args = parser.parse_args(argv)

    targets = {t["no"]: t for t in read_targets(Path(args.targets))}
    ok, _ = read_results(Path(args.results))
    basis_file, _ = read_results(Path(args.basis)) if Path(args.basis).exists() else ({}, {})
    notice_file, _ = read_results(Path(args.notices)) if Path(args.notices).exists() else ({}, {})
    hist = history_lower(Path(args.history)) if args.history else {}
    own = jiil_bizno()
    lines: list[str] = []
    say = lines.append

    items = []
    skipped = Counter()
    src = Counter()
    floor_src = Counter()
    mismatch = 0
    for no, rec in ok.items():
        t = targets.get(no)
        if t is None:
            skipped["목록에 없는 공고"] += 1
            continue
        notice = rec.get("notice") or (notice_file.get(no) or {}).get("notice") or {}
        api_lower = _float(notice.get("lower_rate"))
        lower = api_lower or hist.get(no)
        if api_lower and no in hist and abs(api_lower - hist[no]) > 1e-9:
            mismatch += 1
        basis = rec.get("basis") or (basis_file.get(no) or {}).get("basis")
        planned, _, _ = parse_prepare_prices(rec.get("prices") or [])
        floor, how = floor_rate(lower, basis, planned)
        s = notice_stats(rec, floor, own)
        if s is None:
            skipped["투찰률·금액 있는 참가업체 없음"] += 1
            continue
        src["API" if api_lower else "투찰 이력" if lower else "없음"] += 1
        floor_src[how] += 1
        s.update(work=t["work"] or "빈칸", year=t["date"][:4] or "모름", method=method_group({"notice": notice}), size=size_group(s["n"]))
        items.append(s)

    say("사전분석 — 지금 저장된 개찰결과 기준(전수 수집 전이라 목록 전체를 대표하지 않음)")
    say(f"성공 공고 {len(ok)}건 중 분석 {len(items)}건 · 뺀 공고 {dict(skipped)}")
    say(f"하한율 출처: {dict(src)} · API와 투찰 이력 하한율이 다른 공고 {mismatch}건(API 값을 씀)")
    say(f"하한선: {dict(floor_src)}")
    say("하한선 확인 공고는 투찰률−하한선(%p, A값 공고는 A값을 반영한 실제 하한선), 미확인 공고는 투찰금액÷기초금액"
        "(%, 하한율·A값을 빼지 않은 비율 — 사정률 아님)으로 칸을 나눔.")
    say("값은 중앙값(가운데 50% 범위)")

    table(say, "낙찰방법별", [(s["method"], s) for s in items])
    plain = [s for s in items if s["method"] != "협상"]
    say(f"\n※ 아래 표는 낙찰방법 '협상'을 뺀 {len(plain)}건 — '모름'(낙찰방법 공고서참조·없음)은 포함")
    table(say, "업무별", [(s["work"], s) for s in plain])
    table(say, "참가 규모별", [(s["size"], s) for s in plain])
    table(say, "업무 × 참가 규모", [(f"{s['work']} · {s['size']}", s) for s in plain])
    table(say, "입력일 연도별", [(s["year"], s) for s in plain])

    mine = [s for s in plain if s["own"]]
    say(f"\n## 지일(사업자번호 {own[:3]}-…) — 참가 확인 {len(mine)}건")
    say("참가 규모 | 공고 | 지일 순위 백분위 | 개찰 1위"
        " ‖ [하한선 확인] 공고 | 지일 투찰률−하한선 | 하한 미달"
        " ‖ [하한선 미확인] 공고 | 지일 투찰금액÷기초금액 | 지일 − 개찰1위(투찰금액÷기초금액)")
    by: dict[str, list[dict]] = defaultdict(list)
    for s in mine:
        by[s["size"]].append(s)
    for k in sorted(by):
        g = by[k]
        lo = [s for s in g if s["has_lower"] and s["own_gap"] is not None]
        no = [s for s in g if not s["has_lower"]]
        under = f"{sum(s['own_gap'] < 0 for s in lo) / len(lo):.0%}" if lo else "-"
        say(f"{k} | {len(g)} | {fmt([s['own_pct'] for s in g], False)} | {sum(s['own_first'] for s in g)}"
            f" ‖ {len(lo)} | {fmt([s['own_gap'] for s in lo])} | {under}"
            f" ‖ {len(no)} | {fmt([s['own_ratio'] for s in no], False)} | {fmt([s['own_vs_first'] for s in no])}")

    text = "\n".join(lines) + "\n"
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(text, encoding="utf-8")
    print(text)
    print(f"저장: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
