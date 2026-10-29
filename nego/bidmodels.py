"""논문 검토(2026-10-29)에서 차용한 추천 방식 세 가지 — `bidrecommend`의 표·`bidbacktest`의 검증에 다른 방식과 나란히 나온다.

세 방식 모두 "예정가격 하나를 맞히기"가 아니라(논문 4편·21라인 이력 시험에서 찍기와 같았음) 같은 질문에 답한다:
**사정률 r로 쓰면 1순위일 가능성** = P(s ≤ r < s + g), s = 실제 사정률, g = 그 공고의 1순위 구간 폭(1순위 업체 사정률 − s).
방식마다 s·g를 어디서 가져오는지만 다르다(`best_window`가 공통 계산).

- 추첨 모형(`draw_rate`, 논문 ①·②의 "추첨 과정 재현"): 과거 공고의 번호별 선택 횟수(`drwtNum`)로 업체들이 고르는 번호 경향을 배우고,
  비슷한 과거 공고의 예비가격 15개로 모의 추첨(업체마다 번호 2개, 많이 뽑힌 4개 평균)을 되풀이해 s 분포를 만든다. g는 비슷한 공고의 실제 폭.
- 가중 KNN(`knn_rate`, 논문 ③·④): 비슷한 공고를 "같으면 넣고 다르면 빼기" 대신 닮은 정도만큼 무게 — 분야·업무·변동폭·하한율·발주 방식·
  지역·금액 규모 + 최근 공고. s·g 모두 무게를 준 과거 공고에서.
- 간격 예측(`gap_rate`, 논문 ③의 부스팅 나무를 '1순위 간격' 예측에): s는 비슷한 공고에서, g는 부스팅 모델이 공고 특성으로 예측
  (사정률 자체는 공고 특성으로 못 맞히지만 — 21라인 시험 — 1순위가 하한선에서 얼마나 떨어지나는 참가 규모·업무에 따라 달라서).
"""

from __future__ import annotations

import math
import statistics
from collections import Counter
from datetime import date
from typing import Iterable

DRAW_NAME = "추첨 모형 추천(번호 선택 경향 → 모의 추첨)"
KNN_NAME = "가중 KNN 추천(닮은 공고에 무게)"
GAP_NAME = "간격 예측 추천(1순위 간격 부스팅)"
MODEL_NAMES = (DRAW_NAME, KNN_NAME, GAP_NAME)

STEP = 0.002  # 계산 격자(%p)
MIN_RECORDS = 10  # 이보다 적으면 그 방식은 쉰다
GAP_CLIP = (0.0005, 5.0)  # 1순위 구간 폭(%p) — 너무 작거나(0) 큰 값(참가 1~2곳) 자르기
DRAW_SIMS = 3000  # 모의 추첨 횟수
DRAW_POOL = 300  # 모의 추첨에 쓰는 비슷한 과거 공고(최근순)
KNN_KEEP = 300  # 무게가 큰 과거 공고 이만큼만
KNN_HALF_LIFE = 730  # 가중 KNN 최근 무게 반감기(일)
GAP_MIN_TRAIN = 60  # 간격 예측 모델 학습에 필요한 과거 공고 수


def _np():
    import numpy as np

    return np


def _date(rec) -> str | None:
    """개찰일(공고마다 한 번만 계산해 둠)."""
    cached = rec.__dict__.get("_date_cache")
    if cached is None or cached[0] != rec.opened_at:
        from .bidbacktest import date_of

        cached = (rec.opened_at, date_of(rec))
        rec.__dict__["_date_cache"] = cached
    return cached[1]


def gap_of(rec) -> float:
    """그 공고의 1순위 구간 폭(%p) = 1순위 업체를 이겼을 사정률 상한 − 실제 사정률."""
    lo, hi = GAP_CLIP
    try:
        return min(hi, max(lo, rec.win_ceiling - rec.assessment))
    except (TypeError, ZeroDivisionError):
        return lo


def best_window(s_values, s_weights, gaps, gap_weights) -> tuple[float, float, float] | None:
    """P(r) = Σ w_s·w_g·1[s ≤ r < s + g] 가 가장 큰 r. (r, 1순위 가능성, 그 r의 하한선 통과 가능성).
    같은 값이 이어지면(소규모 경쟁 — 구간이 넓음) 가장 긴 구간의 가운데."""
    np = _np()
    s = np.asarray(list(s_values), float)
    g = np.asarray(list(gaps), float)
    if not len(s) or not len(g):
        return None
    ws = np.asarray(list(s_weights), float) if s_weights is not None else np.ones(len(s))
    wg = np.asarray(list(gap_weights), float) if gap_weights is not None else np.ones(len(g))
    ws, wg = ws / ws.sum(), wg / wg.sum()
    order = np.argsort(s)
    s, ws = s[order], ws[order]
    cum = np.concatenate([[0.0], np.cumsum(ws)])
    grid = np.arange(s.min(), s.max() + g.max() + STEP, STEP)
    if len(grid) > 20000:  # 구간이 아주 넓으면 격자를 성기게
        grid = np.linspace(grid[0], grid[-1], 20000)

    def F(x):  # 사정률 ≤ x 인 무게
        return cum[np.searchsorted(s, x, side="right")]

    if len(g) > 150:  # 폭이 많으면 무게 순 150개만(계산 시간 — 웹 첫 응답)
        keep = np.argsort(-wg)[:150]
        g, wg = g[keep], wg[keep] / wg[keep].sum()
    fr = F(grid)
    p = (fr[:, None] - F(grid[:, None] - g[None, :] + 1e-12)) @ wg
    top = p.max()
    if top <= 0:
        return None
    hit = np.flatnonzero(p >= top - 1e-12)
    runs = np.split(hit, np.flatnonzero(np.diff(hit) > 1) + 1)
    run = max(runs, key=len)
    k = run[len(run) // 2]
    return round(float(grid[k]), 3), float(p[k]), float(fr[k])


# ── 1) 추첨 모형 ─────────────────────────────────────────────────────────────

def pick_shares(records) -> list[float] | None:
    """번호(1~15)별 평균 선택 비율 — 공고마다 drwtNum ÷ 그 공고 전체 선택 수를 번호별로 평균."""
    sums, n = [0.0] * 15, 0
    for rec in records:
        draws = getattr(rec, "draws", None) or []
        if len(draws) != 15:
            continue
        total = sum(c for _, _, c in draws)
        if total <= 0:
            continue
        for no, _, c in draws:
            if 1 <= no <= 15:
                sums[no - 1] += c / total
        n += 1
    if n < MIN_RECORDS:
        return None
    return [v / n for v in sums]


def simulate_assessments(records, sims: int = DRAW_SIMS, seed: int = 7):
    """비슷한 과거 공고의 예비가격 15개(번호순)를 골라 모의 추첨 — 업체 N곳이 번호를 2개씩(배운 선택 비율대로) 고르고 가장 많이 뽑힌
    4개의 평균 사정률. 결과: 사정률 표본(numpy 배열) 또는 None(번호별 자료가 모자람)."""
    np = _np()
    pool = [r for r in records if len(getattr(r, "draws", None) or []) == 15]
    pool = sorted(pool, key=lambda r: _date(r) or "")[-DRAW_POOL:]
    shares = pick_shares(pool)
    if shares is None:
        return None
    p = np.asarray(shares, float)
    p = p / p.sum()
    rates = np.array([[rate for _, rate, _ in sorted(r.draws)] for r in pool])  # 공고 × 번호(1~15)
    bidders = int(min(400, max(5, statistics.median(r.bidder_count for r in pool))))
    rng = np.random.default_rng(seed)
    which = rng.integers(0, len(pool), sims)
    counts = rng.multinomial(2 * bidders, p, size=sims) + rng.random((sims, 15)) * 0.5  # 같은 수면 무작위로
    top4 = np.argpartition(-counts, 4, axis=1)[:, :4]
    return rates[which[:, None], top4].mean(axis=1)


def draw_rate(records) -> dict | None:
    sims = simulate_assessments(records)
    if sims is None:
        return None
    got = best_window(sims, None, [gap_of(r) for r in records if r.window_ok] or [gap_of(r) for r in records], None)
    if got is None:
        return None
    return {"rate": got[0], "win_p": got[1], "valid_p": got[2], "sims": len(sims)}


# ── 2) 가중 KNN ─────────────────────────────────────────────────────────────

def region_of(institution: str) -> str:
    """기관 이름 첫 낱말(시·도 수준) — '강원특별자치도 정선군' → '강원특별자치도'."""
    return (institution or "").split()[0] if (institution or "").split() else ""


def _age(day: str | None, ref: str | None) -> float:
    if not (day and ref):
        return KNN_HALF_LIFE * 2.0
    return max(0, (date.fromisoformat(ref) - date.fromisoformat(day)).days)


def knn_weights(records, target: dict, ref: str | None = None) -> list[tuple[object, float]]:
    """과거 공고마다 닮은 정도 무게(가중 KNN). target = {field, work, width, lower_rate, method, region, basis}.
    거리: 분야·업무·변동폭 다르면 각 1, 발주 방식·지역 다르면 각 0.5(모르면 0.25), 하한율 차 1%p당 0.5(최대 1),
    금액 규모(기초금액 log10) 차 1당 0.5(최대 1). 무게 = exp(−2·거리) × 최근 무게(반감기 KNN_HALF_LIFE일)."""
    out = []
    t_basis = math.log10(target["basis"]) if target.get("basis") else None
    for rec in records:
        d = 0.0
        if target.get("field") and rec.field != target["field"]:
            d += 1.0
        if target.get("work") and rec.work != target["work"]:
            d += 1.0
        if target.get("width") and rec.range_width != target["width"]:
            d += 1.0 if rec.range_width else 0.5
        for key, mine in (("method", getattr(rec, "method", "")), ("region", region_of(getattr(rec, "institution", "")))):
            if target.get(key):
                d += 0.5 if mine and mine != target[key] else (0.25 if not mine else 0.0)
        if target.get("lower_rate"):
            d += min(1.0, abs(rec.lower_rate - target["lower_rate"]) * 0.5)
        if t_basis is not None and rec.basis:
            d += min(1.0, abs(math.log10(rec.basis) - t_basis) * 0.5)
        w = math.exp(-2.0 * d) * 0.5 ** (_age(_date(rec), ref) / KNN_HALF_LIFE)
        out.append((rec, w))
    out.sort(key=lambda x: -x[1])
    return out[:KNN_KEEP]


def knn_rate(records, target: dict, ref: str | None = None) -> dict | None:
    if len(records) < MIN_RECORDS:
        return None
    if ref is None:
        ref = max((d for d in (_date(r) for r in records) if d), default=None)
    pairs = knn_weights(records, target, ref)
    recs, ws = [r for r, _ in pairs], [w for _, w in pairs]
    if not recs or sum(ws) <= 0:
        return None
    ok = [(r, w) for r, w in zip(recs, ws) if r.window_ok] or list(zip(recs, ws))  # 하한선이 틀린 공고의 구간 폭은 빼고
    got = best_window([r.assessment for r in recs], ws, [gap_of(r) for r, _ in ok], [w for _, w in ok])
    if got is None:
        return None
    return {"rate": got[0], "win_p": got[1], "valid_p": got[2], "neighbors": len(recs)}


# ── 3) 1순위 간격 예측 ───────────────────────────────────────────────────────

_GAP_CACHE: dict[tuple, object] = {}


MAX_CATEGORIES = 200  # 범주 칸마다 자주 나온 값만 번호를 주고 나머지는 '기타' 하나로 — HistGradientBoosting은 범주 255개까지만 받음
# (10/29 서버 PC: A값을 채워 학습 공고가 늘자 지역(기관 첫 낱말)이 256개가 되어 "cardinality <= 255" 오류로 백테스트가 멈춤)
OTHER = "\0기타"


def _cat(value: str, vocab: dict) -> int:
    """자주 나온 값의 번호, 없으면 '기타' 번호(학습에 없던 값도 같음)."""
    return vocab.get(value or "", vocab[OTHER])


def _vocab(values: Iterable[str]) -> dict:
    counts = Counter(v or "" for v in values)
    vocab = {v: i for i, (v, _) in enumerate(counts.most_common(MAX_CATEGORIES))}
    vocab[OTHER] = len(vocab)
    return vocab


def _features(rec_like: dict, vocabs: dict) -> list[float]:
    return [_cat(rec_like["work"], vocabs["work"]), _cat(rec_like["field"], vocabs["field"]),
            _cat(rec_like["method"], vocabs["method"]), _cat(rec_like["region"], vocabs["region"]),
            float(rec_like["width"] or 0), float(rec_like["lower_rate"] or 0),
            math.log10(rec_like["basis"]) if rec_like.get("basis") else 0.0,
            math.log10(max(1.0, rec_like["bidders"] or 1.0))]


def _as_target(rec) -> dict:
    return {"work": rec.work, "field": rec.field, "method": getattr(rec, "method", ""),
            "region": region_of(getattr(rec, "institution", "")), "width": rec.range_width,
            "lower_rate": rec.lower_rate, "basis": rec.basis, "bidders": rec.bidder_count}


def gap_model(records: Iterable, key: tuple | None = None):
    """공고 특성 → log10(1순위 구간 폭) 부스팅 나무(scikit-learn HistGradientBoosting, 오차 절댓값 기준). 학습 자료가 적거나
    scikit-learn이 없으면 None. key를 주면 같은 key는 다시 학습하지 않는다(백테스트에서 달마다 한 번)."""
    if key is not None and key in _GAP_CACHE:
        return _GAP_CACHE[key]
    records = [r for r in records if getattr(r, "window_ok", True)]  # 하한선이 틀린 공고(구간이 비정상으로 넓음)는 학습에서 뺌
    model = None
    if len(records) >= GAP_MIN_TRAIN:
        try:
            from sklearn.ensemble import HistGradientBoostingRegressor
        except ImportError:
            HistGradientBoostingRegressor = None
        if HistGradientBoostingRegressor is not None:
            np = _np()
            targets = [_as_target(r) for r in records]
            vocabs = {k: _vocab(t[k] for t in targets) for k in ("work", "field", "method", "region")}
            X = np.array([_features(t, vocabs) for t in targets])
            y = np.log10([gap_of(r) for r in records])
            reg = HistGradientBoostingRegressor(max_depth=3, max_iter=60, learning_rate=0.1, loss="absolute_error",
                                                categorical_features=[0, 1, 2, 3], random_state=0)
            reg.fit(X, y)
            model = (reg, vocabs)
    if key is not None:
        if len(_GAP_CACHE) > 128:
            _GAP_CACHE.clear()
        _GAP_CACHE[key] = model
    return model


def predict_gap(model, target: dict) -> float | None:
    if model is None:
        return None
    reg, vocabs = model
    x = _features(target, vocabs)
    return float(min(GAP_CLIP[1], max(GAP_CLIP[0], 10 ** reg.predict([x])[0])))


def gap_rate(similar_records, model, target: dict) -> dict | None:
    """s = 비슷한 공고의 실제 사정률, g = 모델이 예측한 이 공고의 1순위 구간 폭 하나."""
    if len(similar_records) < MIN_RECORDS:
        return None
    target = dict(target)
    if not target.get("bidders"):
        target["bidders"] = statistics.median(r.bidder_count for r in similar_records)
    g = predict_gap(model, target)
    if g is None:
        return None
    got = best_window([r.assessment for r in similar_records], None, [g], None)
    if got is None:
        return None
    return {"rate": got[0], "win_p": got[1], "valid_p": got[2], "gap": round(g, 4)}
