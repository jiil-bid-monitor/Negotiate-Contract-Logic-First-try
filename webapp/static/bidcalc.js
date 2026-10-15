"use strict";
// 투찰 계산기 — 복수예비가격 모의 추첨·예정가격·투찰금액 계산 (화면과 분리한 순수 계산, node로 테스트).
//
// 공식 규칙(확인됨): 기초금액 ±범위(국가 ±2%, 지방 ±3% — 공고마다 기초금액 API가 범위를 줌)에서 서로 다른 15개,
// 입찰자마다 번호 2개 선택 → 가장 많이 뽑힌 4개(같으면 낮은 번호부터, 4개가 안 되면 안 뽑힌 번호에서 무작위로 채움)
// → 4개 산술평균 = 예정가격. 지방계약(행안부)은 특별한 사유가 없으면 0~+3%에 7개, 0~-3%에 8개.
// 확인 안 된 것: 나라장터 내부 난수 방식(구간 분할·분포). 그래서 이 계산기는 **모의 계산**이지 실제 예측이 아니다.
//
// 투찰금액(적격심사 하한선) = A값 + (예정가격 − A값) × 낙찰하한율. A값 적용 공고가 아니면 A값 0.

(function (root) {
  // 예비가격 만드는 방식 — 이름은 화면에 그대로 나온다
  const METHODS = {
    pps: "조달청 (구간마다 1개)",
    mois: "행안부 (위 7 · 아래 8)",
    uniform: "단순 난수",
    even: "등간격",
  };
  const ROUNDING = {
    ceil1: "원 미만 절상",
    floor1: "원 미만 절사",
    ceil1000: "천원 미만 절상",
    floor1000: "천원 미만 절사",
  };

  function roundAmount(value, mode) {
    // 부동소수 오차(예: 1000000.0000001)로 1원이 올라가지 않게 아주 작은 차이는 먼저 정리
    const v = Math.round(value * 1e6) / 1e6;
    switch (mode) {
      case "floor1": return Math.floor(v);
      case "ceil1000": return Math.ceil(v / 1000) * 1000;
      case "floor1000": return Math.floor(v / 1000) * 1000;
      default: return Math.ceil(v);
    }
  }

  // 한 칸 [a, b) 안에서 무작위 비율(%) 하나
  const inSlot = (a, b, rng) => a + (b - a) * rng();

  // 변동률(%) 목록 n개 — lo~hi는 기초금액 대비 %(예: -3, 3)
  function rates(method, n, lo, hi, rng, up) {
    if (method === "even") {
      if (n === 1) return [(lo + hi) / 2];
      return Array.from({ length: n }, (_, i) => lo + ((hi - lo) * i) / (n - 1));
    }
    if (method === "uniform") return Array.from({ length: n }, () => inSlot(lo, hi, rng));
    if (method === "mois") {
      // 위 up개는 0~hi, 아래 n-up개는 lo~0 — 각 쪽을 개수만큼 칸으로 나눠 칸마다 1개
      const upN = Math.max(0, Math.min(n, up ?? Math.floor(n / 2)));
      const downN = n - upN;
      const out = [];
      for (let i = 0; i < upN; i++) out.push(inSlot((hi * i) / upN, (hi * (i + 1)) / upN, rng));
      for (let i = 0; i < downN; i++) out.push(inSlot((lo * (i + 1)) / downN, (lo * i) / downN, rng));
      return out;
    }
    // pps: 범위 전체를 n칸으로
    const w = (hi - lo) / n;
    return Array.from({ length: n }, (_, i) => inSlot(lo + w * i, lo + w * (i + 1), rng));
  }

  function shuffle(list, rng) {
    const a = list.slice();
    for (let i = a.length - 1; i > 0; i--) {
      const j = Math.floor(rng() * (i + 1));
      [a[i], a[j]] = [a[j], a[i]];
    }
    return a;
  }

  // 예비가격 n개 → [{no, price, pct(기초 대비 %), rate(변동률 %)}], no는 1부터
  // order: "random"(나라장터처럼 번호와 금액이 무관) | "asc"(금액 오름차순)
  function makePrices({ basis, lo, hi, n = 15, method = "pps", up, order = "random", rng = Math.random }) {
    if (!(basis > 0)) throw new Error("기초금액을 입력하세요");
    if (!(hi > lo)) throw new Error("변동폭(최소 < 최대)을 확인하세요");
    let prices = [];
    for (let tries = 0; tries < 20; tries++) {
      prices = rates(method, n, lo, hi, rng, up).map((r) => Math.floor(basis * (1 + r / 100)));
      if (new Set(prices).size === prices.length) break;  // 서로 다른 n개 (금액이 작아 같은 값이 나오면 다시)
    }
    prices.sort((a, b) => a - b);
    if (order === "random") prices = shuffle(prices, rng);
    return prices.map((price, i) => ({ no: i + 1, price, pct: (price / basis) * 100, rate: (price / basis - 1) * 100 }));
  }

  // 입찰자 bidders명이 번호를 perBidder개씩 고름 → 번호별 선택 횟수(0번 칸은 안 씀)
  function simulatePicks(n, bidders, perBidder, rng) {
    const counts = new Array(n + 1).fill(0);
    const k = Math.min(perBidder, n);
    for (let b = 0; b < bidders; b++) {
      const chosen = new Set();
      while (chosen.size < k) chosen.add(1 + Math.floor(rng() * n));
      chosen.forEach((no) => { counts[no] += 1; });
    }
    return counts;
  }

  // 선택 횟수로 추첨 번호 take개 — 많이 뽑힌 순, 같으면 낮은 번호. 뽑힌 번호가 모자라면 안 뽑힌 번호에서 무작위로 채움
  function drawNumbers(counts, take, rng) {
    const n = counts.length - 1;
    const picked = [];
    for (let no = 1; no <= n; no++) if (counts[no] > 0) picked.push(no);
    picked.sort((a, b) => counts[b] - counts[a] || a - b);
    const drawn = picked.slice(0, take);
    if (drawn.length < take) {
      const rest = shuffle(Array.from({ length: n }, (_, i) => i + 1).filter((no) => !drawn.includes(no)), rng);
      drawn.push(...rest.slice(0, take - drawn.length));
    }
    return drawn.sort((a, b) => a - b);
  }

  // 고른 예비가격들의 평균 → 예정가격(끝자리 처리)
  function plannedPrice(selectedPrices, rounding = "ceil1") {
    if (!selectedPrices.length) return null;
    return roundAmount(selectedPrices.reduce((s, p) => s + p, 0) / selectedPrices.length, rounding);
  }

  // 예정가격 → 투찰금액. lowerRate는 %(예: 87.745). netCost(순공사원가, 기초금액 기준)를 주면 예정가격 비율만큼 맞춰
  // 98%와 비교해 높은 쪽 — 순공사원가 98% 미만 투찰은 낙찰에서 빠지는 공사(추정가격 300억 미만) 기준
  function bidAmount({ planned, lowerRate, a = 0, basis, netCost, netCostRate = 98, rounding = "ceil1" }) {
    if (!(planned > 0) || !(lowerRate > 0)) return null;
    const aVal = a > 0 ? a : 0;
    const byRate = roundAmount(aVal + (planned - aVal) * (lowerRate / 100), rounding);
    let byNetCost = null;
    if (netCost > 0) {
      const scaled = basis > 0 ? netCost * (planned / basis) : netCost;
      byNetCost = roundAmount(scaled * (netCostRate / 100), rounding);
    }
    const amount = byNetCost != null && byNetCost > byRate ? byNetCost : byRate;
    return { amount, byRate, byNetCost, usedNetCost: amount === byNetCost && byNetCost !== byRate };
  }

  // 한 번 모의 추첨: 예비가격 만들기 → 입찰자 선택 → 추첨 → 예정가격
  function simulateOnce(opts, rng) {
    const prices = makePrices({ ...opts, rng });
    const counts = simulatePicks(prices.length, opts.bidders ?? 100, opts.perBidder ?? 2, rng);
    const drawn = drawNumbers(counts, opts.take ?? 4, rng);
    const planned = plannedPrice(drawn.map((no) => prices[no - 1].price), opts.plannedRounding);
    return { prices, counts, drawn, planned, rate: (planned / opts.basis) * 100 };
  }

  const pct = (sorted, q) => sorted[Math.min(sorted.length - 1, Math.max(0, Math.round(q * (sorted.length - 1))))];

  // 여러 번 모의 추첨 → 사정률(%) 분포 요약
  function monteCarlo(opts, runs = 10000, rng = Math.random) {
    const out = [];
    for (let i = 0; i < runs; i++) out.push(simulateOnce(opts, rng).rate);
    out.sort((a, b) => a - b);
    const mean = out.reduce((s, v) => s + v, 0) / out.length;
    return {
      runs, mean, min: out[0], max: out[out.length - 1],
      p10: pct(out, 0.1), p25: pct(out, 0.25), p50: pct(out, 0.5), p75: pct(out, 0.75), p90: pct(out, 0.9),
      rates: out,
    };
  }

  // 재현 가능한 난수(테스트·같은 결과 다시 보기용) — mulberry32
  function seeded(seed) {
    let t = seed >>> 0;
    return function () {
      t = (t + 0x6d2b79f5) >>> 0;
      let r = Math.imul(t ^ (t >>> 15), 1 | t);
      r = (r + Math.imul(r ^ (r >>> 7), 61 | r)) ^ r;
      return ((r ^ (r >>> 14)) >>> 0) / 4294967296;
    };
  }

  const api = { METHODS, ROUNDING, roundAmount, rates, makePrices, simulatePicks, drawNumbers, plannedPrice, bidAmount, simulateOnce, monteCarlo, seeded };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.BidCalc = api;
})(typeof window !== "undefined" ? window : globalThis);
