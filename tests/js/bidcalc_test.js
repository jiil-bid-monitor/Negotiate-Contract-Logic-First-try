// webapp/static/bidcalc.js 계산 테스트 — tests/test_bidcalc.py가 node로 실행한다. 실패하면 예외(종료코드 1).
"use strict";
const assert = require("assert");
const path = require("path");
const C = require(path.join(__dirname, "..", "..", "webapp", "static", "bidcalc.js"));

// 끝자리 처리
assert.strictEqual(C.roundAmount(1000.2), 1001);
assert.strictEqual(C.roundAmount(1000.0000001), 1000);  // 부동소수 오차로 1원 올라가지 않게
assert.strictEqual(C.roundAmount(1000.9, "floor"), 1000);
assert.strictEqual(C.roundAmount(1234561, "ceil", 10), 1234570);
assert.strictEqual(C.roundAmount(1234567, "ceil", 1000), 1235000);
assert.strictEqual(C.roundAmount(1234567, "floor", 1000), 1234000);

// 행자부: 기초금액 아래 8개·위 7개, 범위 ±3% 안, 서로 다름, 금액순
for (let seed = 1; seed <= 50; seed++) {
  const p = C.makePrices({ basis: 1e9, lo: -3, hi: 3, n: 15, method: "mois", down: 8, up: 7, rng: C.seeded(seed) });
  assert.strictEqual(p.length, 15);
  assert.strictEqual(p.filter((x) => x >= 1e9).length, 7);
  assert.strictEqual(new Set(p).size, 15);
  p.forEach((x) => assert.ok(x >= 0.97e9 && x <= 1.03e9, String(x)));
  assert.deepStrictEqual(p, p.slice().sort((a, b) => a - b));
}
// 추출범위를 바꾸면 그대로: 아래 5 · 위 10
{
  const p = C.makePrices({ basis: 1e9, lo: -3, hi: 3, method: "mois", down: 5, up: 10, rng: C.seeded(3) });
  assert.strictEqual(p.filter((x) => x >= 1e9).length, 10);
}

// 조달청: 범위를 15칸으로 나눠 칸마다 1개 → 금액순 i번째가 i번째 칸 안
{
  const p = C.makePrices({ basis: 1e9, lo: -2, hi: 2, method: "pps", rng: C.seeded(7) });
  p.forEach((x, i) => {
    const a = 1e9 * (1 + (-2 + (4 / 15) * i) / 100), b = 1e9 * (1 + (-2 + (4 / 15) * (i + 1)) / 100);
    assert.ok(x >= Math.floor(a) && x <= b, `${i}: ${x}`);
  });
}

// 등비: 양 끝이 범위 끝, 이웃 비율이 같음
{
  const p = C.makePrices({ basis: 1e8, lo: -2, hi: 2, method: "geometric" });
  assert.strictEqual(p[0], 98000000);
  assert.ok(Math.abs(p[14] - 102000000) <= 1);
  const r1 = p[1] / p[0], r2 = p[14] / p[13];
  assert.ok(Math.abs(r1 - r2) < 1e-6);
}

// 단순 난수: 범위 안
{
  const p = C.makePrices({ basis: 1e8, lo: -3, hi: 3, method: "uniform", rng: C.seeded(9) });
  p.forEach((x) => assert.ok(x >= 97e6 && x <= 103e6));
}

// 자동선택: 서로 다른 번호 k개, 1~n
{
  const d = C.pickRandom(15, 4, C.seeded(2));
  assert.strictEqual(d.length, 4);
  assert.strictEqual(new Set(d).size, 4);
  d.forEach((x) => assert.ok(x >= 1 && x <= 15));
}

// 예정가격·투찰금액: 예정가격 1,002,500,000 · 하한율 87.745% · A값 0 → 879,643,625
{
  const planned = C.plannedPrice([1005000000, 998000000, 1012000000, 995000000]);
  assert.strictEqual(planned, 1002500000);
  assert.strictEqual(C.bidAmount({ planned, lowerRate: 87.745 }), 879643625);
  // A값 1,000만 원 → 10,000,000 + (992,500,000 × 0.87745) = 880,869,125
  assert.strictEqual(C.bidAmount({ planned, lowerRate: 87.745, a: 10000000 }), 880869125);
  // 천원 미만 절상
  assert.strictEqual(C.bidAmount({ planned, lowerRate: 87.745, unit: 1000 }), 879644000);
  assert.strictEqual(C.bidAmount({ planned, lowerRate: null }), null);
  // 순공사원가 투찰가격: 9억 × 1.0025 × 98% = 884,205,000
  assert.strictEqual(C.netCostBid({ planned, basis: 1e9, netCost: 9e8 }), 884205000);
  assert.strictEqual(C.netCostBid({ planned, basis: 1e9, netCost: null }), null);
}

// 잘못된 입력
assert.throws(() => C.makePrices({ basis: 0, lo: -3, hi: 3 }), /기초금액/);
assert.throws(() => C.makePrices({ basis: 1e8, lo: 3, hi: -3 }), /변동폭/);

console.log("bidcalc ok");
