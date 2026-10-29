"""추천 사정률 검증 — 과거 개찰 결과로 "그때 이 추천을 따랐으면 몇 번 유효·1순위였나"를 공고 순서대로 다시 계산한다.

공고마다 **그 공고 개찰일 전에 열린 공고만**으로 추천(`bidrecommend`과 같은 계산)을 만들고, 그 공고의 실제 사정률·1순위로 채점한다
(미래 자료를 안 씀). 같은 공고에 지일이 실제로 투찰했으면(외주 추천값 그대로 투찰 — 10/12 분석) 그 결과도 나란히 센다.

서버 PC에서 실행한다(과거 개찰 자료·지일 투찰 이력은 저장소에 안 올림):

    py -m nego.bidbacktest                       # output/ 의 bid_history_*.json · bid_results*.jsonl 전부
    py -m nego.bidbacktest --from 2025-01-01     # 2025년 이후 공고만 채점(그 전 공고는 학습에만)
    py -m nego.bidbacktest --scale actual --fixed 100.55

결과: 화면 요약 + `output/bid_backtest_<날짜>.csv`(공고별 — 공개 저장소에 올리지 말 것).
"""

from __future__ import annotations

import argparse
import bisect
import csv
import os
import re
import statistics
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import bidmodels
from .bidrecommend import (CROWD_NAME, FIXED_RATE, LARGE_COMPETITION, MIN_PICK_TRIED, MIN_SAMPLES, PICK_MIN_VALID,
                           PICK_MIN_WIN, TOP_N, TRACK_LIMIT, TREND_NAME, PastBid, _best, _pct, _rate, _ratio, _scale_ok,
                           coverage, crowd_rate, default_own_bizno, history_paths, load_history, pick_option, similar,
                           trend_rate)

OWN = "지일 실제 투찰(외주)"
TOP_NAME = f"추천(과거 {TOP_N}위 안 최다)"
BASELINE = "기준선(비교용): 늘 100%"  # 논문 검토(10/29) — 어떤 방식이 '아무렇게나 찍기'보다 나은지 늘 함께 보려고
STRATEGIES = ("추천(과거 1순위 최다)", TOP_NAME, TREND_NAME, CROWD_NAME, *bidmodels.MODEL_NAMES, "사정률 중앙값", "유효 70%",
              "유효 90%", BASELINE)
HALF_LIVES = (365, 730, 1095, None)  # --tune: 대규모 추천 반감기 후보(일, None = 무게 없음) — 날짜 순서 백테스트로 고름(그리드 서치)
_DATE = re.compile(r"(\d{4})[-./]?(\d{1,2})[-./]?(\d{1,2})")


def date_of(rec: PastBid) -> str | None:
    """개찰일 'YYYY-MM-DD' — 모르면 None(순서를 못 정해 검증에서 뺌)."""
    m = _DATE.search(rec.opened_at or "")
    if not m:
        # 옛 공고번호(2020~23 "20201217355")는 앞 8자리가 공고일 — 개찰일 칸이 없을 때만 대신 씀
        m = re.match(r"(20\d{2})(\d{2})(\d{2})\d{3}$", rec.notice_no or "")
        if not m or not (1 <= int(m.group(2)) <= 12 and 1 <= int(m.group(3)) <= 31):
            return None
    return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"


def score(rate: float, rec: PastBid) -> tuple[bool, bool]:
    """(유효, 1순위) — `bidrecommend`과 같은 식: 유효 ⇔ r ≥ s, 1순위 ⇔ s ≤ r < 1순위 상한."""
    valid = rate >= rec.assessment - 1e-9
    return valid, valid and rate < rec.win_ceiling


def in_top(rate: float, rec: PastBid, k: int = TOP_N) -> bool:
    """이 사정률로 썼으면 금액 기준 개찰 k위 안이었나(하한선 이상 + k위 업체보다 쌈)."""
    return rate >= rec.assessment - 1e-9 and rate < rec.rank_ceiling(k)


def amount_for(rec: PastBid, rate: float) -> float:
    """이 공고에 가정 사정률 rate로 냈을 투찰금액 — `PastBid.amount_at`(하한선·순공사원가 선 중 높은 쪽)."""
    return rec.amount_at(rate)


def rank_of(rec: PastBid, amount: float, skip_own: bool = True) -> int | None:
    """그 금액의 개찰 순위(하한선·순공사원가 기준선 아래면 None). 지일 금액은 지일이 그 금액 대신 냈다고 보고 경쟁에서 뺀다."""
    floor = rec.valid_floor
    if amount < floor - 0.5:
        return None
    others = list(rec.amounts)
    if skip_own and rec.own_amount:
        try:
            others.remove(rec.own_amount)
        except ValueError:
            pass
    return 1 + sum(1 for a in others if floor - 0.5 <= a < amount)


def rank_share(rate: float, rec: PastBid) -> float | None:
    """이 사정률로 냈을 때 참가업체 중 상위 몇 %(1위 = 1/참가 수, **하한선 미달은 꼴찌 = 1.0**). 하한선이 틀린 공고·참가업체 금액이
    없으면 None. 1순위 비율로 방식이 안 가려질 때 고르는 기준(10/29 사용자 결정 2안).
    미달을 빼고 '통과한 공고끼리만' 평균하면 사정률을 높게 쓰는 방식이 유리해진다 — 통과가 어려운 공고(실제 사정률이 높음)까지
    통과해 순위가 매겨지고, 낮게 쓰는 방식은 실제 사정률이 낮아 1위와 멀리 떨어진 공고에서만 통과해 순위가 나빠 보임(가짜 자료로 확인)."""
    if not rec.amounts or not rec.window_ok:
        return None
    if rate < rec.assessment - 1e-9:
        return 1.0
    rank = rank_of(rec, amount_for(rec, rate))
    return 1.0 if rank is None else min(1.0, rank / max(len(rec.amounts), 1))


def own_rate(rec: PastBid) -> float | None:
    """지일 투찰금액에서 거꾸로 구한 가정 사정률(하한선 기준 — `PastBid.rate_for`)."""
    if not rec.own_amount:
        return None
    return rec.rate_for(rec.own_amount)


def strategy_rates(train: list[PastBid], target: PastBid, scale: str, min_train: int = MIN_SAMPLES) -> dict[str, float]:
    """개찰일 전 공고(train)로 만든 후보 사정률들. 비슷한 공고가 min_train건 미만이면 비움(추천 안 함)."""
    if scale == "actual":
        scale = "large" if target.bidder_count >= LARGE_COMPETITION else "small"
    picked, _ = similar(train, target.work, target.range_width, scale, target.field)
    if len(picked) < min_train:
        return {}
    rates = [r.assessment for r in picked]
    out = {"사정률 중앙값": statistics.median(rates), "유효 70%": _pct(rates, 0.7), "유효 90%": _pct(rates, 0.9)}
    best = _best(coverage(picked)[0])
    if best is not None:
        out["추천(과거 1순위 최다)"] = _rate(best)
    top = _best(coverage(picked, lambda r: r.rank_ceiling(TOP_N))[0], lowest=True)
    if top is not None:
        out[TOP_NAME] = _rate(top)
    guess = trend_rate(picked)
    if guess is not None:
        out[TREND_NAME] = guess[0] + guess[1]
    out[BASELINE] = 100.0
    out.update(model_rates(train, picked, target, scale))
    if sum(r.bidder_count >= LARGE_COMPETITION for r in picked) > len(picked) / 2:  # 화면(`recommend`)과 같은 조건
        crowd = crowd_rate(picked)
        if crowd is not None:
            out[CROWD_NAME] = crowd["rate"]
    return out


def target_info(rec: PastBid) -> dict:
    """투찰 전에 아는 그 공고 특성(참가 수는 모름) — 가중 KNN·간격 예측에 씀."""
    return {"field": rec.field, "work": rec.work, "width": rec.range_width, "lower_rate": rec.lower_rate,
            "method": rec.method, "region": bidmodels.region_of(rec.institution), "basis": rec.basis}


def model_rates(train: list[PastBid], picked: list[PastBid], target: PastBid, scale: str) -> dict[str, float]:
    """논문 차용 세 방식(`bidmodels`)의 사정률 — 모두 개찰일 전 자료(train)만. 간격 예측 모델은 달마다 한 번 학습(그 달 1일 전 자료)."""
    out: dict[str, float] = {}
    day = date_of(target) or ""
    info = target_info(target)
    got = bidmodels.draw_rate(picked)
    if got:
        out[bidmodels.DRAW_NAME] = got["rate"]
    pool = [r for r in train if _scale_ok(r, scale)]
    got = bidmodels.knn_rate(pool, info, ref=day or None)
    if got:
        out[bidmodels.KNN_NAME] = got["rate"]
    month = day[:7]
    before = [r for r in train if (bidmodels._date(r) or "") < f"{month}-01"] if month else train
    model = bidmodels.gap_model(before, key=("bt", month, len(before), id(train[0]) if train else 0))
    got = bidmodels.gap_rate(picked, model, info)
    if got:
        out[bidmodels.GAP_NAME] = got["rate"]
    return out


PICK = "추천 칸 선정(지금 규칙)"  # --pick: 그 공고 개찰 전 자료로 추천 칸이 골랐을 방식(`pick_option`)
LEGACY = "추천 칸 선정(이전 규칙)"  # 10/29 2안 전 규칙 — 같은 공고로 나란히 비교하려고 남김


def legacy_pick(options: list[dict]) -> dict | None:
    """10/29 2안 전 `pick_option`(비교용): 통과 60% 이상 중 대규모 추천 줄이 있으면 1순위 비율 최고, 없으면 1순위 비율 최고가
    10% 이상일 때 그 방식(경향·대규모 제외), 아니면 경향 추천."""
    if not options:
        return None
    rank = lambda o: (_ratio(o["track"]), _ratio(o["track"], "top"), _ratio(o["track"], "valid"))  # noqa: E731
    tried = lambda o: (o.get("track") or {}).get("tried", 0) >= MIN_PICK_TRIED  # noqa: E731
    passes = lambda o: tried(o) and _ratio(o["track"], "valid") >= PICK_MIN_VALID  # noqa: E731
    every = [o for o in options if passes(o)]
    ok = [o for o in every if o["name"] not in (TREND_NAME, CROWD_NAME)]
    best = max(ok, key=rank) if ok else None
    trend = next((o for o in options if o["name"] == TREND_NAME), None)
    crowd = next((o for o in options if o["name"] == CROWD_NAME), None)
    measured = [o for o in options if tried(o)]
    if crowd is not None and every:
        return max(every, key=rank)
    if best is not None and _ratio(best["track"]) >= PICK_MIN_WIN:
        return best
    if trend is not None and (passes(trend) or not tried(trend)):
        return trend
    if every:
        return max(every, key=rank)
    if measured:
        return max(measured, key=lambda o: (_ratio(o["track"], "valid"), _ratio(o["track"])))
    return next((o for o in options if o["name"].startswith("고정")), options[0])


def add_picks(rows: list[dict], records: list[PastBid]) -> None:
    """공고마다 '추천 칸이 골랐을 방식'을 채점 줄에 더한다 — 웹앱과 같은 순서: 개찰일 전 공고 중 비슷한 공고(경쟁 규모 자동)를 고르고,
    그 공고들의 채점 줄(각자 자기 개찰일 전 자료로 매긴 것, 최근 TRACK_LIMIT건)로 방식별 성적을 낸 뒤 `pick_option`.
    rows는 그 비슷한 공고들까지 채점돼 있어야 한다(`main --pick`이 처음부터 채점)."""
    by_no = {r["공고번호"]: r for r in rows}
    dated = sorted(((date_of(r), r) for r in records if date_of(r)), key=lambda x: (x[0], x[1].notice_no))
    dates = [d for d, _ in dated]
    rec_by_no = {r.notice_no: r for _, r in dated}
    for row in rows:
        rec = rec_by_no.get(row["공고번호"])
        if rec is None or not row["rates"]:
            continue
        train = [r for _, r in dated[:bisect.bisect_left(dates, row["개찰일"])]]
        base, _ = similar(train, rec.work, rec.range_width, "all", rec.field)
        scale = "all"
        if base:
            scale = "small" if statistics.median(r.bidder_count for r in base) < LARGE_COMPETITION else "large"
        picked, _ = similar(train, rec.work, rec.range_width, scale, rec.field)
        tracked = sorted((by_no[p.notice_no] for p in picked if p.notice_no in by_no), key=lambda r: r["개찰일"])[-TRACK_LIMIT:]
        track = track_records(tracked)
        options = [{"name": n, "rate": rate, "track": track.get(n)} for n, rate in row["rates"].items()]
        for label, chosen in ((PICK, pick_option(options)), (LEGACY, legacy_pick(options))):
            if chosen is None:
                continue
            row["results"][label] = row["results"][chosen["name"]]
            row["ranks"][label] = row["ranks"].get(chosen["name"])
            row.setdefault("picked", {})[label] = chosen["name"]


@dataclass
class Tally:
    tried: int = 0
    valid: int = 0
    win: int = 0
    top: int = 0  # 5위 안
    gaps: list[float] = field(default_factory=list)  # 가정 사정률 − 실제 사정률(%p)
    ranks: list[float] = field(default_factory=list)  # 참가업체 중 상위 몇 %(0~1, 미달 = 1.0)

    def add(self, rate: float, actual: float, valid: bool, win: bool, top: bool = False, rank: float | None = None) -> None:
        self.tried += 1
        self.valid += valid
        self.win += win
        self.top += top
        self.gaps.append(rate - actual)
        if rank is not None:
            self.ranks.append(rank)

    def line(self, name: str) -> str:
        if not self.tried:
            return f"  {name:<22} 대상 없음"
        pct = lambda n: f"{n:>4}건 ({n / self.tried:5.1%})"  # noqa: E731
        gap = statistics.median(self.gaps)
        miss = statistics.median(abs(g) for g in self.gaps)
        return (f"  {name:<22} {self.tried:>5}건 · 유효 {pct(self.valid)} · 1순위 {pct(self.win)} · {TOP_N}위 안 {pct(self.top)}"
                f" · 실제보다 {gap:+.3f}%p(중앙) · 오차 {miss:.3f}%p(중앙 |·|)"
                + (f" · 순위 평균 상위 {statistics.mean(self.ranks):.0%}(미달=꼴찌) · 상위 10% 안 {sum(x <= 0.1 for x in self.ranks)}건" if self.ranks else ""))


def backtest(records: list[PastBid], start: str | None = None, scale: str = "all", fixed: tuple[float, ...] = (),
             min_train: int = MIN_SAMPLES, targets: set[str] | None = None, end: str | None = None) -> tuple[list[dict], int]:
    """공고별 결과 줄과, 개찰일을 몰라 뺀 공고 수. targets(공고번호)를 주면 그 공고만 채점(웹앱 추천 칸 — 비슷한 공고만)."""
    dated = sorted(((date_of(r), r) for r in records if date_of(r)), key=lambda x: (x[0], x[1].notice_no))
    dates = [d for d, _ in dated]
    rows = []
    for d, rec in dated:
        if (start and d < start) or (end and d > end) or (targets is not None and rec.notice_no not in targets):
            continue
        train = [r for _, r in dated[:bisect.bisect_left(dates, d)]]  # 같은 날 열린 공고도 안 씀
        rates = strategy_rates(train, rec, scale, min_train)
        rates.update({f"고정 {f:g}%": f for f in fixed})
        row = {"공고번호": rec.notice_no, "개찰일": d, "분야": rec.field, "업무": rec.work, "변동폭": rec.range_width, "참가": rec.bidder_count,
               "학습 공고": len(train), "실제 사정률": round(rec.assessment, 4), "1순위 상한": round(rec.win_ceiling, 4),
               "하한선": rec.floor_source, "rates": rates, "results": {}}
        row["ranks"] = {}
        for name, rate in rates.items():
            row["results"][name] = (rate, *score(rate, rec), in_top(rate, rec))
            row["ranks"][name] = rank_share(rate, rec)
        if rec.own_amount:
            row["results"][OWN] = (own_rate(rec), rec.own_rank is not None, rec.own_rank == 1,
                                   rec.own_rank is not None and rec.own_rank <= TOP_N)
            if rec.amounts and rec.window_ok:
                row["ranks"][OWN] = min(1.0, rec.own_rank / len(rec.amounts)) if rec.own_rank is not None else 1.0
        rows.append(row)
    return rows, len(records) - len(dated)


def tune_crowd(records: list[PastBid], start: str | None = None, end: str | None = None, scale: str = "all",
               half_lives: tuple = HALF_LIVES, min_train: int = MIN_SAMPLES) -> dict:
    """대규모 추천의 반감기 후보마다 같은 공고들을 채점(공고마다 개찰일 전 자료만) — {반감기: Tally}.
    비슷한 공고 절반 넘게 100곳 이상인 공고만(화면에 대규모 추천이 나오는 조건과 같음)."""
    dated = sorted(((date_of(r), r) for r in records if date_of(r)), key=lambda x: (x[0], x[1].notice_no))
    dates = [d for d, _ in dated]
    out = {hl: Tally() for hl in half_lives}
    for d, rec in dated:
        if (start and d < start) or (end and d > end):
            continue
        sc = ("large" if rec.bidder_count >= LARGE_COMPETITION else "small") if scale == "actual" else scale
        picked, _ = similar([r for _, r in dated[:bisect.bisect_left(dates, d)]], rec.work, rec.range_width, sc, rec.field)
        if len(picked) < min_train or sum(r.bidder_count >= LARGE_COMPETITION for r in picked) <= len(picked) / 2:
            continue
        for hl in half_lives:
            got = crowd_rate(picked, today=d, half_life=hl)
            if got is not None:
                valid, win = score(got["rate"], rec)
                out[hl].add(got["rate"], rec.assessment, valid, win, in_top(got["rate"], rec))
    return out


def tune_summary(result: dict) -> str:
    lines = ["대규모 추천 반감기 비교(그리드 서치 — 같은 공고, 개찰일 전 자료만):"]
    for hl, tally in result.items():
        lines.append(tally.line(f"반감기 {hl}일" if hl else "무게 없음"))
    return "\n".join(lines)


def track_records(rows: list[dict]) -> dict[str, dict]:
    """방식별 검증 성적 {이름: {tried, valid, win, top, ranked, rank}} — 웹앱 추천 칸에 그대로 싣는다(top = 5위 안,
    rank = 평균 상위 몇 %(0~1, 미달은 1.0), ranked = 그 순위를 센 공고 수)."""
    out: dict[str, dict] = {}
    for row in rows:
        ranks = row.get("ranks") or {}
        for name, (rate, valid, win, top) in row["results"].items():
            if rate is None:
                continue
            t = out.setdefault(name, {"tried": 0, "valid": 0, "win": 0, "top": 0, "ranked": 0, "rank_sum": 0.0})
            t["tried"] += 1
            t["valid"] += bool(valid)
            t["win"] += bool(win)
            t["top"] += bool(top)
            if ranks.get(name) is not None:
                t["ranked"] += 1
                t["rank_sum"] += ranks[name]
    for t in out.values():
        total = t.pop("rank_sum")
        t["rank"] = total / t["ranked"] if t["ranked"] else None
    return out


def _names(rows: list[dict]) -> list[str]:
    names = [PICK, LEGACY, *STRATEGIES]
    for row in rows:
        names += [n for n in row["results"] if n not in names]
    return [n for n in names if any(n in r["results"] for r in rows)]


def summarize(rows: list[dict], undated: int = 0) -> str:
    if not rows:
        if undated:
            return (f"채점할 공고가 없습니다 — 개찰일을 찾지 못한 공고 {undated}건. 개찰 결과 파일의 날짜 칸 이름을 확인해야 합니다.")
        return "채점할 공고가 없습니다 — `--from` 날짜 이후 공고가 없거나 과거 개찰 자료가 비었습니다."
    names = _names(rows)
    lines = [f"채점 공고 {len(rows)}건 (개찰일 모름 {undated}건 제외) · {rows[0]['개찰일']} ~ {rows[-1]['개찰일']}"]
    no_rec = sum("추천(과거 1순위 최다)" not in r["results"] for r in rows)
    if no_rec:
        lines.append(f"  그중 {no_rec}건은 개찰일 전 비슷한 공고가 {MIN_SAMPLES}건 미만이라 추천 없음(추천 줄에서 빠짐)")

    def block(title: str, subset: list[dict]) -> None:
        src = {k: sum(r["하한선"] == k for r in subset) for k in ("A값", "추정", "A=0")}
        lines.append(f"\n[{title}] {len(subset)}건 (하한선: A값 {src['A값']} · 추정 {src['추정']} · A=0 {src['A=0']})")
        for name in names:
            t = Tally()
            for r in subset:
                if name in r["results"]:
                    rate, valid, win, top = r["results"][name]
                    if rate is not None:
                        t.add(rate, r["실제 사정률"], valid, win, top, (r.get("ranks") or {}).get(name))
            lines.append(t.line(name))

    picks = [r["picked"] for r in rows if r.get("picked")]
    if picks:
        for label in (PICK, LEGACY):
            lines.append(f"  {label}: " + " · ".join(f"{n} {c}" for n, c in Counter(p[label] for p in picks if label in p).most_common()))
        lines.append(f"  두 규칙이 다른 방식을 고른 공고 {sum(p.get(PICK) != p.get(LEGACY) for p in picks)}건")
    block("전체", rows)
    own = [r for r in rows if OWN in r["results"]]
    if own:
        block("지일이 실제로 투찰한 공고 — 외주 추천과 같은 공고끼리 비교", own)
    small = [r for r in rows if r["참가"] < LARGE_COMPETITION]
    large = [r for r in rows if r["참가"] >= LARGE_COMPETITION]
    if small and large:
        block(f"참가 {LARGE_COMPETITION}곳 미만", small)
        block(f"참가 {LARGE_COMPETITION}곳 이상(운의 영역)", large)
    counts = Counter(r["분야"] for r in rows)
    for name, n in counts.most_common():
        block(f"분야 {name}(공고명 키워드)", [r for r in rows if r["분야"] == name])
    for year in sorted({r["개찰일"][:4] for r in rows}):
        block(f"{year}년", [r for r in rows if r["개찰일"].startswith(year)])
    # 변동폭 × 연도(대규모 경쟁) — 2026년 ±3% 공고에서 외주 가정이 실제보다 낮아진 것 같은 변화를 바로 보려고(10/29)
    for width in (2, 3):
        for year in sorted({r["개찰일"][:4] for r in large}):
            subset = [r for r in large if r["변동폭"] == width and r["개찰일"].startswith(year)]
            if len(subset) >= MIN_SAMPLES:
                block(f"참가 {LARGE_COMPETITION}곳 이상 · 변동폭 ±{width}% · {year}년", subset)
    by = {k: sum(r["하한선"] == k for r in rows) for k in ("A값", "추정", "A=0")}
    lines.append(f"\n※ 하한선: 공고 A값 {by['A값']}건 · 참가업체 금액으로 추정 {by['추정']}건 · A=0 {by['A=0']}건")
    if by["A=0"]:
        lines.append(f"  A=0으로 채점한 {by['A=0']}건은 1순위 판정이 실제보다 후할 수 있음"
                     "(지일 실제 투찰의 유효·1순위는 나라장터 순위 그대로라 해당 없음)")
    lines.append(f"※ 순위는 금액 기준 개찰 순위(적격심사 서류 심사 전). 1순위 = 하한선 이상 중 가장 낮은 금액, {TOP_N}위 안 = {TOP_N}위 업체보다 쌈.")
    return "\n".join(lines)


def write_csv(rows: list[dict], path: Path) -> None:
    names = _names(rows)
    base = ["공고번호", "개찰일", "분야", "업무", "변동폭", "참가", "학습 공고", "실제 사정률", "1순위 상한", "하한선"]
    picks = [PICK, LEGACY] if any(r.get("picked") for r in rows) else []
    head = base + [f"{p} 방식" for p in picks] + [f"{n} {k}" for n in names for k in ("사정률", "유효", "1순위", f"{TOP_N}위 안", "순위 상위%(미달=100%)")]
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(head)
        for r in rows:
            cells = [r[k] for k in base] + [(r.get("picked") or {}).get(p, "") for p in picks]
            for n in names:
                rate, *marks = r["results"].get(n, (None, None, None, None))
                share = (r.get("ranks") or {}).get(n)
                cells += ([round(rate, 4) if rate is not None else ""] + ["" if m is None else ("O" if m else "X") for m in marks]
                          + [f"{share:.1%}" if share is not None else ""])
            w.writerow(cells)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="추천 사정률 검증 — 과거 개찰 결과로 공고 순서대로 다시 채점")
    parser.add_argument("--dir", default=os.environ.get("BID_HISTORY_DIR") or os.environ.get("OUTPUT_DIR") or "output",
                        help="과거 개찰 자료 폴더(기본 output)")
    parser.add_argument("--from", dest="start", default="", help="이 날짜(YYYY-MM-DD) 이후 개찰 공고만 채점 — 그 전은 학습에만")
    parser.add_argument("--to", dest="end", default="", help="이 날짜(YYYY-MM-DD)까지 개찰한 공고만 채점(예: 9월만 — --from 2026-09-01 --to 2026-09-30)")
    parser.add_argument("--scale", choices=("all", "actual"), default="all",
                        help="비슷한 공고 고를 때 경쟁 규모: all=구분 안 함, actual=그 공고의 실제 규모(100곳 미만/이상)")
    parser.add_argument("--fixed", type=float, action="append", default=[], help="같이 비교할 고정 사정률(%%), 여러 번 가능")
    parser.add_argument("--tune", action="store_true",
                        help="대규모 추천 반감기(1년·2년·3년·무게 없음)를 같은 공고로 비교 — 결과는 요약 끝에 붙음(오래 걸릴 수 있음)")
    parser.add_argument("--pick", action="store_true",
                        help="추천 칸이 골랐을 방식(지금 규칙·이전 규칙)도 채점 — 처음부터 다 채점해야 해서 오래 걸림, 고정 100.55%%도 함께")
    parser.add_argument("--min-train", type=int, default=MIN_SAMPLES, help=f"추천에 필요한 비슷한 과거 공고 수(기본 {MIN_SAMPLES})")
    parser.add_argument("--csv", default="", help="공고별 결과 CSV 경로(기본 <dir>/bid_backtest_<오늘>.csv)")
    args = parser.parse_args(argv)
    # 윈도우에서 `> 파일`로 돌리면 cp949로 쓰다가 '—' 같은 글자에서 멈춘다 — 못 쓰는 글자는 바꿔 찍고, 요약은 아래에서 UTF-8 파일로 따로 저장
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")

    directory = Path(args.dir)
    paths = history_paths(directory)
    if not paths:
        print(f"{directory}에 bid_history_*.json · bid_results*.jsonl이 없습니다.", file=sys.stderr)
        return 1
    history = load_history(paths, default_own_bizno())
    head = [f"과거 개찰 {len(history.records)}건 (못 쓴 공고 {history.skipped}건) — {', '.join(history.files)}"]
    if history.skip_reasons:
        head.append("  못 쓴 이유: " + " · ".join(f"{k} {n}건" for k, n in history.skip_reasons.most_common()))
    net = sum(1 for r in history.records if r.net_line)
    if net:
        head.append(f"  순공사원가 기준선(개찰 결과의 '순공사원가' 무효 업체로 정함)이 하한선보다 높은 공고 {net}건 — 통과·순위를 그 선으로 셈")
    odd = Counter()
    for r in history.records:
        odd.update(r.odd_remarks)
    if odd:  # 아직 반영 안 한 무효 조건이 있는지 보려고 — 하한선·순공사원가 선 위인데 순위가 없는 업체의 비고
        head.append("  유효 선 위인데 순위 없는 업체 비고(반영 안 한 조건 확인용): "
                    + " · ".join(f"{k} {n}" for k, n in odd.most_common(8)))
    head.append(f"  설정: --from {args.start or '(처음부터)'} · --to {args.end or '(끝까지)'} · --scale {args.scale}"
                + (f" · --fixed {', '.join(f'{f:g}' for f in args.fixed)}" if args.fixed else ""))
    if args.pick:
        # 비슷한 공고들의 채점 줄이 있어야 그 공고의 선정을 흉내 낼 수 있어 처음부터 채점한 뒤 기간으로 자름(웹앱처럼 고정 100.55%도 후보)
        fixed = tuple(dict.fromkeys((*args.fixed, FIXED_RATE)))
        rows, undated = backtest(history.records, None, args.scale, fixed, args.min_train)
        add_picks(rows, history.records)
        rows = [r for r in rows if (not args.start or r["개찰일"] >= args.start) and (not args.end or r["개찰일"] <= args.end)]
    else:
        rows, undated = backtest(history.records, args.start or None, args.scale, tuple(args.fixed), args.min_train,
                                 end=args.end or None)
    text = "\n".join(head) + "\n" + summarize(rows, undated)
    if args.tune:
        text += "\n\n" + tune_summary(tune_crowd(history.records, args.start or None, args.end or None, args.scale,
                                                    min_train=args.min_train))
    print(text)
    stamp = f"{datetime.now():%Y%m%d}"
    summary_path = directory / f"bid_backtest_{stamp}.txt"
    summary_path.write_text(text + "\n", encoding="utf-8")  # 요약만(업체 이름 없음) — 공유용
    print(f"\n요약: {summary_path}")
    if rows:
        out = Path(args.csv) if args.csv else directory / f"bid_backtest_{stamp}.csv"
        write_csv(rows, out)
        print(f"공고별 결과: {out} (공개 저장소에 올리지 말 것)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
