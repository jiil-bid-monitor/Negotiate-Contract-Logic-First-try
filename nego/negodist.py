"""협상 공고의 '다른 업체 최저가' 분포 — 계산기 협상 가격 점수표 열을 근거 있게 고르려고(2026-11-12 사용자 요청).

협상에의한계약의 가격 점수는 **참가업체 중 최저 가격 ÷ 내 가격**으로 정해진다(`nego.negoprice`). 그래서 점수표의 '다른 업체 최저가'
열은 과거 협상 공고에서 실제 최저 투찰가가 기준 금액(기초금액, 없으면 추정가격 × 1.1, 그것도 없으면 배정예산)의 몇 %였는지로 고른다.

자료: 서버 PC `output/`(또는 `BID_HISTORY_DIR`)의 `bid_results*.jsonl`·`nego_results*.jsonl`(`scripts/fetch_bid_results.py`).
낙찰방법·기초금액은 2026-11-12부터 받는 줄에만 있다 — 예전 줄은 `--fill-award`로 채운다. 협상 공고 목록은
`scripts/find_bid_targets.py --nego`로 만든다.

    py -m nego.negodist                    # 분포 요약 → output/nego_dist_<날짜>.txt · .csv (공개 금지)
    py -m nego.negodist --from 2025-01-01  # 개찰일 이 날부터만

판정은 하지 않는다 — 분포만 보여 주고, 열을 어떻게 쓸지는 사람이 정한다.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import statistics
import sys
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any

from .bidfield import classify

PATTERNS = ("bid_results*.jsonl", "nego_results*.jsonl")
BACKUP_MARK = "_채우기전"
RATE_MIN, RATE_MAX = 30.0, 130.0  # 이 밖의 최저가 비율은 금액·기준 금액 오류로 보고 뺌
MIN_GROUP = 15  # 분야·업무로 좁힌 표본이 이보다 적으면 넓힘
QUANTILES = (10, 25, 50, 75, 90)


@dataclass
class NegoBid:
    no: str
    work: str
    title: str
    field: str
    institution: str
    opened_at: str
    base: float
    base_source: str
    bidders: int
    lowest: float
    lowest_rate: float  # 최저가 ÷ 기준 금액 × 100
    second_rate: float | None  # 두 번째로 싼 업체(최저가 업체가 우리일 때의 '다른 업체 최저가')
    rates: list[float]  # 모든 업체 비율(오름차순)


def _num(value: Any) -> float | None:
    try:
        num = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return num if num > 0 else None


def is_nego(info: dict) -> bool | None:
    """협상 공고인가 — 낙찰방법을 모르면(예전 줄) None."""
    award = "".join(str(info.get("award_method") or "").split())
    if not award:
        return None
    return award.startswith("협상에의한계약")


def base_amount(info: dict) -> tuple[float | None, str]:
    """기준 금액 — 기초금액 → 추정가격 × 1.1(부가세 포함) → 배정예산."""
    if _num(info.get("basis")):
        return _num(info["basis"]), "기초금액"
    if _num(info.get("est_price")):
        return round(_num(info["est_price"]) * 1.1), "추정가격×1.1"
    if _num(info.get("budget")):
        return _num(info["budget"]), "배정예산"
    return None, ""


def from_record(rec: dict, own_bizno: str = "") -> tuple[NegoBid | None, str]:
    """개찰 결과 줄 1개 → (협상 공고 자료, 못 쓴 이유)."""
    if rec.get("error"):
        return None, "조회 오류"
    info = rec.get("notice") or {}
    kind = is_nego(info)
    if kind is None:
        return None, "낙찰방법 모름(--fill-award 필요)"
    if not kind:
        return None, ""  # 협상 아님 — 이유로 세지 않음
    if "지명" in str(info.get("contract_method") or ""):
        return None, "지명경쟁"
    base, source = base_amount(info)
    if not base:
        return None, "기준 금액 없음"
    own = "".join(ch for ch in own_bizno if ch.isdigit())
    amounts = []
    for row in rec.get("bidders") or []:
        amount = _num(row.get("bidprcAmt"))
        if amount is None:
            continue
        if own and "".join(ch for ch in str(row.get("prcbdrBizno") or "") if ch.isdigit()) == own:
            continue  # 지일 금액은 '다른 업체'에서 뺌
        amounts.append(amount)
    if not amounts:
        return None, "투찰 금액 없음"
    rates = sorted(round(a / base * 100, 3) for a in amounts)
    if not RATE_MIN <= rates[0] <= RATE_MAX:
        return None, "최저가 비율 이상값"
    title = str(info.get("title") or "")
    return NegoBid(
        no=str(rec.get("no") or ""), work=str(rec.get("work") or ""), title=title, field=classify(title),
        institution=str(info.get("institution") or ""), opened_at=str(info.get("opened_at") or "")[:10],
        base=base, base_source=source, bidders=len(rates), lowest=min(amounts), lowest_rate=rates[0],
        second_rate=rates[1] if len(rates) > 1 else None, rates=rates,
    ), ""


def history_paths(directory: Path) -> list[Path]:
    out: list[Path] = []
    for pattern in PATTERNS:
        out.extend(p for p in sorted(directory.glob(pattern)) if BACKUP_MARK not in p.stem)
    return out


def load(paths: list[Path], own_bizno: str = "", since: str = "") -> tuple[list[NegoBid], dict[str, int]]:
    """여러 파일을 한 줄씩 읽어 협상 공고만 — 같은 공고는 업체가 많이 든 줄 하나. (자료, 못 쓴 이유별 건수)"""
    found: dict[str, NegoBid] = {}
    skipped: dict[str, int] = {}
    for path in paths:
        with path.open(encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                bid, why = from_record(rec, own_bizno)
                if bid is None:
                    if why:
                        skipped[why] = skipped.get(why, 0) + 1
                    continue
                if since and bid.opened_at and bid.opened_at < since:
                    continue
                old = found.get(bid.no)
                if old is None or old.bidders < bid.bidders:
                    found[bid.no] = bid
    return sorted(found.values(), key=lambda b: b.opened_at), skipped


def quantiles(values: list[float]) -> dict[int, float]:
    """10·25·50·75·90번째 백분위(선형 보간)."""
    if not values:
        return {}
    if len(values) == 1:
        return {q: values[0] for q in QUANTILES}
    cuts = statistics.quantiles(values, n=100, method="inclusive")
    return {q: round(cuts[q - 1], 2) for q in QUANTILES}


def _bucket(n: int) -> str:
    return "2~4곳" if n < 5 else "5~9곳" if n < 10 else "10곳 이상"


def summarize(bids: list[NegoBid], work: str | None = None, title: str = "") -> dict:
    """화면용 요약 — 같은 분야(공고명 키워드)·업무로 좁히되 MIN_GROUP건 미만이면 업무 → 전체로 넓힘.
    '다른 업체 최저가' 분포는 참가 2곳 이상 공고의 최저가 비율(우리를 뺀 경쟁의 최저가를 대신함)."""
    field_name = classify(title) if title else ""
    pool = [b for b in bids if b.bidders >= 2]
    steps = []
    if field_name and work:
        steps.append((f"분야 '{field_name}' · {work}", [b for b in pool if b.field == field_name and b.work == work]))
    if work:
        steps.append((f"업무 {work}", [b for b in pool if b.work == work]))
    steps.append(("협상 공고 전체", pool))
    scope, chosen = steps[-1]
    for name, group in steps:
        if len(group) >= MIN_GROUP:
            scope, chosen = name, group
            break
    lows = sorted(b.lowest_rate for b in chosen)
    q = quantiles(lows)
    counts = [b.bidders for b in chosen]
    return {
        "n": len(chosen), "total": len(bids), "scope": scope, "quantiles": q,
        "median_bidders": statistics.median(counts) if counts else None,
        "below80": sum(1 for v in lows if v < 80), "below70": sum(1 for v in lows if v < 70),
        # 표 열 — 높은 가격부터(90 → 10번째 백분위), 0.5%p 단위로 둥글림, 같은 값은 한 번만
        "columns": _columns(q),
        "first": min((b.opened_at for b in chosen if b.opened_at), default=""),
        "last": max((b.opened_at for b in chosen if b.opened_at), default=""),
    }


def _columns(q: dict[int, float]) -> list[dict]:
    out: list[dict] = []
    for p in sorted(q, reverse=True):  # 90 → 10: 금액 높은 열이 왼쪽(점수표 내 가격 열과 같은 방향)
        rate = round(q[p] * 2) / 2
        if out and out[-1]["rate"] == rate:
            continue
        out.append({"rate": rate, "pct": p, "median": p == 50})  # pct: 과거 공고 중 최저가가 이 값 이하였던 비율
    return out


def group_table(bids: list[NegoBid]) -> list[tuple[str, int, dict[int, float]]]:
    """보고서 표 — 전체·업무별·참가 규모별·기준 금액 출처별 최저가 비율 분포."""
    pool = [b for b in bids if b.bidders >= 2]
    rows = [("전체(참가 2곳 이상)", pool)]
    for work in ("용역", "물품", "공사"):
        rows.append((f"업무 {work}", [b for b in pool if b.work == work]))
    for name in ("2~4곳", "5~9곳", "10곳 이상"):
        rows.append((f"참가 {name}", [b for b in pool if _bucket(b.bidders) == name]))
    for source in ("기초금액", "추정가격×1.1", "배정예산"):
        rows.append((f"기준 {source}", [b for b in pool if b.base_source == source]))
    fields: dict[str, list[NegoBid]] = {}
    for b in pool:
        fields.setdefault(b.field, []).append(b)
    for name, group in sorted(fields.items(), key=lambda kv: -len(kv[1])):
        if len(group) >= 5:
            rows.append((f"분야 {name}", group))
    return [(name, len(group), quantiles(sorted(b.lowest_rate for b in group))) for name, group in rows if group]


def report(bids: list[NegoBid], skipped: dict[str, int], single: int) -> str:
    lines = [f"협상 공고 최저 투찰가 ÷ 기준 금액 분포 ({date.today():%Y-%m-%d})", ""]
    lines.append(f"쓸 수 있는 협상 공고 {len(bids)}건 (참가 1곳뿐 {single}건은 분포에서 뺌)")
    if skipped:
        lines.append("못 쓴 줄: " + " · ".join(f"{k} {v}" for k, v in sorted(skipped.items(), key=lambda kv: -kv[1])))
    lines += ["", f"{'묶음':<22}{'건수':>6}  " + "  ".join(f"{q}%".rjust(7) for q in QUANTILES)]
    for name, n, q in group_table(bids):
        lines.append(f"{name:<22}{n:>6}  " + "  ".join(f"{q[p]:7.2f}" for p in QUANTILES))
    pool = [b for b in bids if b.bidders >= 2]
    if pool:
        lines += ["", f"최저가가 기준 금액의 80% 미만인 공고 {sum(b.lowest_rate < 80 for b in pool)}건 · 70% 미만 "
                      f"{sum(b.lowest_rate < 70 for b in pool)}건 (참가 2곳 이상 {len(pool)}건 중)",
                  "읽는 법: 50%(가운데) 열이 '다른 업체 최저가'로 가장 흔한 값, 10%는 열 번 중 한 번은 이보다 더 싸게 들어온 값."]
    return "\n".join(lines) + "\n"


def write_csv(bids: list[NegoBid], path: Path) -> None:
    head = ["no", "work", "field", "title", "institution", "opened_at", "base", "base_source", "bidders", "lowest", "lowest_rate", "second_rate"]
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(head)
        for b in bids:
            d = asdict(b)
            w.writerow([d[k] for k in head])


_CACHE: dict[str, Any] = {}


def from_dir(directory: Path) -> list[NegoBid]:
    """웹앱용 — 파일이 바뀌었을 때만 다시 읽음."""
    from .bidrecommend import default_own_bizno

    paths = history_paths(directory)
    key = tuple((str(p), p.stat().st_mtime_ns) for p in paths)
    if _CACHE.get("key") != key:
        _CACHE.update(key=key, bids=load(paths, default_own_bizno())[0])
    return _CACHE["bids"]


def main(argv: list[str] | None = None) -> int:
    from .bidrecommend import default_own_bizno

    parser = argparse.ArgumentParser(description="협상 공고 '다른 업체 최저가' 분포(서버 PC 과거 개찰 자료)")
    parser.add_argument("--dir", default=os.environ.get("BID_HISTORY_DIR") or os.environ.get("OUTPUT_DIR") or "output")
    parser.add_argument("--from", dest="since", default="", help="개찰일 이 날(YYYY-MM-DD)부터만")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    directory = Path(args.dir)
    paths = history_paths(directory)
    if not paths:
        print(f"과거 개찰 파일이 없습니다: {directory} (bid_results*.jsonl · nego_results*.jsonl)", file=sys.stderr)
        return 1
    bids, skipped = load(paths, default_own_bizno(), args.since)
    single = sum(1 for b in bids if b.bidders < 2)
    text = report(bids, skipped, single)
    print(text)
    stamp = f"{date.today():%Y%m%d}"
    (directory / f"nego_dist_{stamp}.txt").write_text(text, encoding="utf-8")
    write_csv(bids, directory / f"nego_dist_{stamp}.csv")
    print(f"저장: {directory / f'nego_dist_{stamp}.txt'} · .csv (공개 금지)")
    if skipped.get("낙찰방법 모름(--fill-award 필요)"):
        print("\n낙찰방법을 모르는 줄이 있습니다 — 협상 공고가 더 있을 수 있음:")
        for p in paths:
            print(f"  py scripts/fetch_bid_results.py --fill-award --out {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
