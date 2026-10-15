// webapp/static/bidcalc.js 계산 테스트 — tests/test_bidcalc.py가 node로 실행한다. 실패하면 예외(종료코드 1).
"use strict";
const assert = require("assert");
const path = require("path");
const C = require(path.join(__dirname, "..", "..", "webapp", "static", "bidcalc.js"));

// 끝자리 처리
assert.strictEqual(C.roundAmount(1000.2, "ceil1"), 1001);
assert.strictEqual(C.roundAmount(1000.0000001, "ceil1"), 1000);  // 부동소수 오차로 1원 올라가지 않게
assert.strictEqual(C.roundAmount(1000.9, "floor1"), 1000);
assert.strictEqual(C.roundAmount(1234567, "ceil1000"), 1235000);
assert.strictEqual(C.roundAmount(1234567, "floor1000"), 1234000);

// 행안부 방식: 기초금액 위 7개·아래 8개, 범위 ±3% 안, 서로 다름
for (let seed = 1; seed <= 50; seed++) {
  const p = C.makePrices({ basis: 1e9, lo: -3, hi: 3, n: 15, method: "mois", up: 7, rng: C.seeded(seed) });
  assert.strictEqual(p.length, 15);
  assert.strictEqual(p.filter((x) => x.price >= 1e9).length, 7);
  assert.strictEqual(new Set(p.map((x) => x.price)).size, 15);
  p.forEach((x) => assert.ok(x.price >= 0.97e9 && x.price <= 1.03e9, String(x.price)));
  assert.deepStrictEqual(p.map((x) => x.no), Array.from({ length: 15 }, (_, i) => i + 1));
}

// 조달청 방식: 범위를 15칸으로 나눠 칸마다 1개 → 금액순으로 늘어놓으면 i번째가 i번째 칸 안
{
  const p = C.makePrices({ basis: 1e9, lo: -2, hi: 2, method: "pps", order: "asc", rng: C.seeded(7) });
  p.forEach((x, i) => {
    const a = 1e9 * (1 + (-2 + (4 / 15) * i) / 100), b = 1e9 * (1 + (-2 + (4 / 15) * (i + 1)) / 100);
    assert.ok(x.price >= Math.floor(a) && x.price <= b, `${i}: ${x.price}`);
  });
}

// 등간격: 양 끝이 범위 끝
{
  const p = C.makePrices({ basis: 1e8, lo: -2, hi: 2, method: "even", order: "asc", rng: C.seeded(1) });
  assert.strictEqual(p[0].price, 98000000);
  assert.strictEqual(p[14].price, 102000000);
}

// 추첨: 많이 뽑힌 순, 같으면 낮은 번호
{
  const counts = [0, 3, 5, 5, 1, 0, 2, 5, 0, 0, 0, 0, 0, 0, 0, 4];
  assert.deepStrictEqual(C.drawNumbers(counts, 4, C.seeded(1)), [2, 3, 7, 15]);
  // 동률이 겹쳐도 낮은 번호부터: 5회가 2·3·7, 4회가 15 → 위 그대로. 3회 이하 동률 확인
  const tie = [0, 2, 2, 2, 2, 2, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0];
  assert.deepStrictEqual(C.drawNumbers(tie, 4, C.seeded(1)), [1, 2, 3, 4]);
}
// 뽑힌 번호가 4개 미만이면 안 뽑힌 번호에서 채움(서로 다름)
{
  const counts = new Array(16).fill(0);
  counts[9] = 1;
  const d = C.drawNumbers(counts, 4, C.seeded(3));
  assert.strictEqual(d.length, 4);
  assert.ok(d.includes(9));
  assert.strictEqual(new Set(d).size, 4);
}

// 입찰자 선택: 한 사람이 같은 번호를 두 번 고르지 않음 → 합계 = 입찰자 × 2
{
  const counts = C.simulatePicks(15, 37, 2, C.seeded(5));
  assert.strictEqual(counts.reduce((s, v) => s + v, 0), 74);
}

// 예정가격·투찰금액: 예정가격 1,002,500,000 · 하한율 87.745% · A값 0 → 879,643,625
{
  const planned = C.plannedPrice([1005000000, 998000000, 1012000000, 995000000]);
  assert.strictEqual(planned, 1002500000);
  const bid = C.bidAmount({ planned, lowerRate: 87.745 });
  assert.strictEqual(bid.amount, 879643625);
  // A값 1,000만 원 → 10,000,000 + (992,500,000 × 0.87745) = 880,869,125
  assert.strictEqual(C.bidAmount({ planned, lowerRate: 87.745, a: 10000000 }).amount, 880869125);
  // 순공사원가(기초 9억)가 더 높으면 그쪽: 9억 × 1.0025 × 98% = 884,205,000
  const withNet = C.bidAmount({ planned, lowerRate: 87.745, basis: 1e9, netCost: 9e8 });
  assert.strictEqual(withNet.byNetCost, 884205000);
  assert.strictEqual(withNet.amount, 884205000);
  assert.ok(withNet.usedNetCost);
  assert.strictEqual(C.bidAmount({ planned, lowerRate: null }), null);
}

// 모의 1만 번: 같은 씨앗이면 같은 결과, 사정률은 범위 안
{
  const o = { basis: 1e9, lo: -3, hi: 3, method: "mois", up: 7, bidders: 50 };
  const a = C.monteCarlo(o, 500, C.seeded(11)), b = C.monteCarlo(o, 500, C.seeded(11));
  assert.strictEqual(a.p50, b.p50);
  assert.ok(a.min >= 97 && a.max <= 103);
  assert.ok(a.p10 <= a.p50 && a.p50 <= a.p90);
}

// 잘못된 입력
assert.throws(() => C.makePrices({ basis: 0, lo: -3, hi: 3 }), /기초금액/);
assert.throws(() => C.makePrices({ basis: 1e8, lo: 3, hi: -3 }), /변동폭/);

console.log("bidcalc ok");
