"use strict";
// 투찰 계산기 — 복수예비가격 만들기·예정가격·투찰금액 계산 (화면과 분리한 순수 계산, node로 테스트).
// 화면 구성은 21라인 "복수예가산출프로그램 v0.1"을 따른다(2026-10-15 사용자 요청).
//
// 공식 규칙(확인됨): 기초금액 ±범위(국가 ±2%, 지방 ±3% — 공고마다 기초금액 API가 범위를 줌)에서 서로 다른 15개,
// 입찰자마다 번호 2개 선택 → 가장 많이 뽑힌 4개 → 산술평균 = 예정가격. 지방계약(행안부)은 특별한 사유가 없으면
// 0~+3%에 7개, 0~-3%에 8개. 나라장터 내부 난수 방식(구간 분할·분포)은 공개 안 됨 — 여기 방식은 모의용.
//
// 투찰금액(적격심사 하한선) = A값 + (예정가격 − A값) × 낙찰하한율. A값 적용 공고가 아니면 A값 0.

(function (root) {
  // 21라인 버튼 이름과 같게
  const METHODS = {
    uniform: "난수추출",
    geometric: "등비추출",
    pps: "조달청난수추출",
    mois: "행자부난수추출",
  };

  // how: "ceil"(절상) | "floor"(절사), unit: 1·10·100·1000 (원 미만·십원 미만·백원 미만·천원 미만)
  function roundAmount(value, how = "ceil", unit = 1) {
    // 부동소수 오차(1000000.0000001 같은)로 1원이 올라가지 않게 아주 작은 차이는 먼저 정리
    const v = Math.round(value * 1e6) / 1e6 / unit;
    return (how === "floor" ? Math.floor(v) : Math.ceil(v)) * unit;
  }

  const inSlot = (a, b, rng) => a + (b - a) * rng();

  // 변동률(%) 목록 n개 — lo~hi는 기초금액 대비 %(예: -3, 3). down/up = 기초금액 아래/위 개수(행자부)
  function rates(method, n, lo, hi, rng, down, up) {
    if (method === "geometric") {
      // 범위 양 끝을 같은 비율로 나눔
      if (n === 1) return [(lo + hi) / 2];
      const a = 1 + lo / 100, ratio = Math.pow((1 + hi / 100) / a, 1 / (n - 1));
      return Array.from({ length: n }, (_, i) => (a * Math.pow(ratio, i) - 1) * 100);
    }
    if (method === "uniform") return Array.from({ length: n }, () => inSlot(lo, hi, rng));
    if (method === "mois") {
      // 아래 down개는 lo~0, 위 up개는 0~hi — 각 쪽을 개수만큼 칸으로 나눠 칸마다 1개
      const upN = Math.max(0, Math.min(n, up ?? n - (down ?? Math.ceil(n / 2))));
      const downN = n - upN;
      const out = [];
      for (let i = 0; i < downN; i++) out.push(inSlot((lo * (i + 1)) / downN, (lo * i) / downN, rng));
      for (let i = 0; i < upN; i++) out.push(inSlot((hi * i) / upN, (hi * (i + 1)) / upN, rng));
      return out;
    }
    // pps: 범위 전체를 n칸으로
    const w = (hi - lo) / n;
    return Array.from({ length: n }, (_, i) => inSlot(lo + w * i, lo + w * (i + 1), rng));
  }

  function shuffle(list, rng = Math.random) {
    const a = list.slice();
    for (let i = a.length - 1; i > 0; i--) {
      const j = Math.floor(rng() * (i + 1));
      [a[i], a[j]] = [a[j], a[i]];
    }
    return a;
  }

  // 예비가격 n개(금액 오름차순) — 번호는 화면이 배치(랜덤배치 등)하며 붙인다
  function makePrices({ basis, lo, hi, n = 15, method = "mois", down, up, rng = Math.random }) {
    if (!(basis > 0)) throw new Error("기초금액을 입력하세요");
    if (!(hi > lo)) throw new Error("변동폭(최소 < 최대)을 확인하세요");
    let prices = [];
    for (let tries = 0; tries < 20; tries++) {
      prices = rates(method, n, lo, hi, rng, down, up).map((r) => Math.floor(basis * (1 + r / 100)));
      if (new Set(prices).size === prices.length) break;  // 서로 다른 n개 (금액이 작아 같은 값이 나오면 다시)
    }
    return prices.sort((a, b) => a - b);
  }

  // 1~n 중 서로 다른 번호 k개(자동선택)
  function pickRandom(n, k, rng = Math.random) {
    return shuffle(Array.from({ length: n }, (_, i) => i + 1), rng).slice(0, Math.min(k, n)).sort((a, b) => a - b);
  }

  // 고른 예비가격들의 평균 → 예정가격(끝자리 처리)
  function plannedPrice(selectedPrices, how = "ceil", unit = 1) {
    if (!selectedPrices.length) return null;
    return roundAmount(selectedPrices.reduce((s, p) => s + p, 0) / selectedPrices.length, how, unit);
  }

  // 예정가격 → 투찰금액. lowerRate는 %(예: 87.745)
  function bidAmount({ planned, lowerRate, a = 0, how = "ceil", unit = 1 }) {
    if (!(planned > 0) || !(lowerRate > 0)) return null;
    const aVal = a > 0 ? a : 0;
    return roundAmount(aVal + (planned - aVal) * (lowerRate / 100), how, unit);
  }

  // 순공사원가 투찰가격 — 순공사원가(기초금액 기준)를 예정가격 비율로 맞춰 98%. 300억 미만 공사는
  // 순공사원가 98% 미만 투찰이 낙찰에서 빠지므로 투찰금액과 비교해 높은 쪽으로 투찰
  function netCostBid({ planned, basis, netCost, rate = 98, how = "ceil", unit = 1 }) {
    if (!(netCost > 0) || !(planned > 0)) return null;
    const scaled = basis > 0 ? netCost * (planned / basis) : netCost;
    return roundAmount(scaled * (rate / 100), how, unit);
  }

  // 재현 가능한 난수(테스트용) — mulberry32
  function seeded(seed) {
    let t = seed >>> 0;
    return function () {
      t = (t + 0x6d2b79f5) >>> 0;
      let r = Math.imul(t ^ (t >>> 15), 1 | t);
      r = (r + Math.imul(r ^ (r >>> 7), 61 | r)) ^ r;
      return ((r ^ (r >>> 14)) >>> 0) / 4294967296;
    };
  }

  const api = { METHODS, roundAmount, rates, makePrices, shuffle, pickRandom, plannedPrice, bidAmount, netCostBid, seeded };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.BidCalc = api;
})(typeof window !== "undefined" ? window : globalThis);
