"use strict";
// 투찰 계산기 — 복수예비가격 만들기·예정가격·투찰금액 계산 (화면과 분리한 순수 계산, node로 테스트).
// 화면 구성은 21라인 "복수예가산출프로그램 v0.1"을 따른다(2026-10-15 사용자 요청).
//
// 공식 규칙(확인됨): 기초금액 ±범위(국가 ±2%, 지방 ±3% — 공고마다 기초금액 API가 범위를 줌)에서 서로 다른 15개,
// 입찰자마다 번호 2개 선택 → 가장 많이 뽑힌 4개 → 산술평균 = 예정가격. 지방계약(행안부)은 특별한 사유가 없으면
// 0~+3%에 7개, 0~-3%에 8개.
// 15개 구간은 「대한민국 조달제도 기본서」 집행기관별 상세기준 표 그대로(2026-10-15 사용자 제공, 2005.4.1 작성분부터):
// 조달청 ±2% 15구간(1~8 위쪽·8은 기초금액 걸침, 9~15 아래, 0.001% 단위), 행정자치부 ±3% 15구간(1~8 아래, 9~15 위,
// 0.00000001% 단위). 구간 안 어디에 뽑히는지는 공개 안 됨 — 구간 안 균등 난수로 둠.
// 예비가격 소수점 처리(같은 기본서, "현행과 동일"): 물품/일반용역(총액) 1원 미만 올림 · (단가) 소수점 셋째 자리에서 올림 ·
// 시설공사/감리 및 설계 용역 100원 미만 올림.
//
// 투찰금액(적격심사 하한선) = A값 + (예정가격 − A값) × 낙찰하한율. A값 적용 공고가 아니면 A값 0.

(function (root) {
  // 21라인 버튼 이름과 같게
  const METHODS = {
    uniform: "난수추출",
    geometric: "등비추출",  // 실제 동작은 등간격(21라인과 같게)
    pps: "조달청난수추출",
    mois: "행자부난수추출",
  };

  // how: "ceil"(절상) | "floor"(절사) | "round"(사사오입), unit: 끝자리 단위
  //   0.01(단가 — 소수점 둘째 자리까지) · 1(원미만) · 10(원단위) · 100(백원단위) · 1,000(천원단위) · 10,000(만원단위)
  function roundAmount(value, how = "ceil", unit = 1) {
    // 부동소수 오차(1000000.0000001 같은)로 1원이 올라가지 않게 아주 작은 차이는 먼저 정리
    const v = Math.round((value / unit) * 1e6) / 1e6;
    const r = how === "floor" ? Math.floor(v) : how === "round" ? Math.round(v) : Math.ceil(v);
    return unit < 1 ? Math.round(r * unit * 100) / 100 : r * unit;
  }

  // 난수 버튼은 21라인처럼 백분율을 0.0001% 단위로 뽑는다(10/22 21라인 캡처 3장씩 대조 — 97.2569·99.7969·101.8141 …,
  // 예전엔 행자부 0.00000001%·조달청 0.001%·난수 연속값). a~b 안(양끝 포함)의 0.0001 배수 하나
  const STEP = 1e-4;
  const inSlot = (a, b, rng) => {
    const ia = Math.ceil(a / STEP - 1e-6), ib = Math.floor(b / STEP + 1e-6);
    return (ia + Math.floor(rng() * (ib - ia + 1))) * STEP;
  };

  // 기본서 구간표 — [하한, 상한] 기초금액 대비 %. 표에 적힌 숫자 그대로(번호 순서 = 표의 구간 1~15)
  const PPS_TABLE = {
    range: 2, step: 0.001, slots: [
      [101.735, 102.000], [101.469, 101.734], [101.202, 101.468], [100.935, 101.201], [100.668, 100.934],
      [100.401, 100.667], [100.134, 100.400], [99.867, 100.133], [99.600, 99.866], [99.333, 99.599],
      [99.066, 99.332], [98.799, 99.065], [98.532, 98.798], [98.266, 98.531], [98.000, 98.265],
    ],
  };
  const MOIS_TABLE = {
    range: 3, step: 1e-8, slots: [
      [97.00000000, 97.37500000], [97.37500001, 97.75000001], [97.75000002, 98.12500002], [98.12500003, 98.50000003],
      [98.50000004, 98.87500004], [98.87500005, 99.25000005], [99.25000006, 99.62500006], [99.62500007, 100.00000000],
      [100.00000001, 100.42857142], [100.42857143, 100.85714285], [100.85714286, 101.28571428], [101.28571429, 101.71428571],
      [101.71428572, 102.14285714], [102.14285715, 102.57142857], [102.57142858, 103.00000000],
    ],
  };

  // 구간마다 1개(0.0001% 단위) → 변동률(%), 표의 구간 순서 그대로(행자부 ① 가장 낮음, 조달청 ① 가장 높음 — 21라인과 같음).
  // 공고 범위가 표와 다르면(예: 조달청인데 ±3%) 같은 비율로 늘이고 줄임
  function tableRates(table, lo, hi, rng) {
    return table.slots.map(([a, b]) => {
      const pct = inSlot(a, b, rng) - 100;
      return pct >= 0 ? (pct * hi) / table.range : (pct * -lo) / table.range;
    });
  }
  // 15개면 늘 표 — 21라인 행자부는 추출범위(−7/+8 등)를 바꿔도 표대로 아래 8·위 7(10/22 캡처). 10개는 21라인이 못 골라 기존 칸 나누기
  const usesTable = (method, n) => n === 15 && (method === "mois" || method === "pps");

  // 예비가격 끝자리 처리 — 소수점 종류(화면 기타설정 첫 상자)별. 2026-10-22 21라인 캡처(기초금액 123,456,789·123,456,798, 등비 15개) 대조:
  // 총액·단가 = 1원 미만 올림(단가도 예비가격은 정수 — 소수 둘째 자리 아님), 시설공사·감리및설계 = **10원 미만 버린 뒤** 100원 미만 올림
  // (125,573,208.73 → 125,573,200 · 121,869,505.06 → 121,869,500 — 그냥 100원 올림이면 …300·…600)
  function roundPrice(value, decimal) {
    if (decimal === "cnst") return roundAmount(roundAmount(value, "floor", 10), "ceil", 100);
    return roundAmount(value, "ceil", 1);
  }

  // 변동률(%) 목록 n개 — lo~hi는 기초금액 대비 %(예: -3, 3). down/up = 기초금액 아래/위 개수(행자부)
  function rates(method, n, lo, hi, rng, down, up) {
    if (method === "geometric") {
      // 21라인 "등비추출"은 이름과 달리 변동률을 **같은 간격**으로 나눈다(2026-10-22 21라인 캡처 대조:
      // ±3%·15개 → -3, -2.5714, … 0 … 2.5714, 3 — 6/14씩). 예전엔 금액을 같은 비율로 나눴음
      if (n === 1) return [(lo + hi) / 2];
      return Array.from({ length: n }, (_, i) => lo + ((hi - lo) * i) / (n - 1));
    }
    if (method === "uniform") {
      // 21라인 "난수추출적용": 추출범위대로 아래 down개는 lo~0, 위 up개는 0~hi 안 아무 데서(구간 없음) — 캡처 3장 모두 8/7
      const upN = Math.max(0, Math.min(n, up ?? n - (down ?? Math.ceil(n / 2))));
      return [...Array.from({ length: n - upN }, () => inSlot(lo, -STEP, rng)),
        ...Array.from({ length: upN }, () => inSlot(STEP, hi, rng))];
    }
    if (method === "mois" && usesTable(method, n)) return tableRates(MOIS_TABLE, lo, hi, rng);
    if (method === "pps" && usesTable(method, n)) return tableRates(PPS_TABLE, lo, hi, rng);
    if (method === "mois") {
      // 표와 다른 개수(10개·추출범위 변경) — 아래 down개는 lo~0, 위 up개는 0~hi — 각 쪽을 개수만큼 칸으로 나눠 칸마다 1개
      const upN = Math.max(0, Math.min(n, up ?? n - (down ?? Math.ceil(n / 2))));
      const downN = n - upN;
      const out = [];
      // 0(기초금액 그대로)은 어느 쪽에도 안 넣음 — 아래·위 개수가 흔들리지 않게
      for (let i = 0; i < downN; i++) out.push(inSlot((lo * (i + 1)) / downN, i ? (lo * i) / downN : -STEP, rng));
      for (let i = 0; i < upN; i++) out.push(inSlot(i ? (hi * i) / upN : STEP, (hi * (i + 1)) / upN, rng));
      return out;
    }
    // pps 10개: 범위 전체를 n칸으로
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

  // 예비가격 n개 — 배열 순서가 곧 번호(①부터). 표 방식은 구간 순서, 그 밖은 금액 오름차순
  // decimal: "total"·"unit"(1원 미만 올림) | "cnst"(10원 미만 버림 → 100원 미만 올림) — roundPrice
  function makePrices({ basis, lo, hi, n = 15, method = "mois", down, up, decimal = "total", rng = Math.random }) {
    if (!(basis > 0)) throw new Error("기초금액을 입력하세요");
    if (!(hi > lo)) throw new Error("변동폭(최소 < 최대)을 확인하세요");
    let prices = [];
    for (let tries = 0; tries < 20; tries++) {
      // 등비추출은 21라인처럼 백분율을 소수 넷째 자리로 반올림한 뒤 기초금액을 곱한다(97.4286% × 10.5억 = 1,023,000,300 — 캡처와 같음)
      const pctOf = method === "geometric" ? (r) => Math.round((100 + r) * 1e4) / 1e4 : (r) => 100 + r;
      prices = rates(method, n, lo, hi, rng, down, up).map((r) => roundPrice((basis * pctOf(r)) / 100, decimal));
      if (new Set(prices).size === prices.length) break;  // 서로 다른 n개 (금액이 작아 같은 값이 나오면 다시)
    }
    // 번호 순서 = 21라인: 표 방식은 구간 순서 그대로, 나머지는 금액순(① 가장 낮음). 섞는 건 "랜덤배치" 버튼만
    return usesTable(method, n) ? prices : prices.sort((a, b) => a - b);
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
  // 순공사원가 98% 미만 투찰이 낙찰에서 빠지므로 투찰금액과 비교해 높은 쪽으로 투찰.
  // 21라인과 같게(10/22 캡처 4장): 끝자리 처리 **전** 고른 예비가격 평균(average)으로 계산하고 원 미만 올림 —
  // 기타설정(절상·절사·단위)은 따르지 않음. 순공사원가 2천만·5천만·1억 → 19,663,000 · 49,157,499 · 98,314,997
  function netCostBid({ average, basis, netCost, rate = 98 }) {
    if (!(netCost > 0) || !(average > 0)) return null;
    const scaled = basis > 0 ? netCost * (average / basis) : netCost;
    return roundAmount(scaled * (rate / 100), "ceil", 1);
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

  const api = { METHODS, PPS_TABLE, MOIS_TABLE, roundPrice, roundAmount, rates, makePrices, shuffle, pickRandom, plannedPrice, bidAmount, netCostBid, seeded };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.BidCalc = api;
})(typeof window !== "undefined" ? window : globalThis);
