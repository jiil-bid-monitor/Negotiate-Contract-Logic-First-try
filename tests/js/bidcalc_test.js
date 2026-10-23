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
// 사사오입
assert.strictEqual(C.roundAmount(1000.5, "round"), 1001);
assert.strictEqual(C.roundAmount(1000.49, "round"), 1000);
assert.strictEqual(C.roundAmount(1234567, "round", 10000), 1230000);
// 만원단위 절상
assert.strictEqual(C.roundAmount(1234567, "ceil", 100000), 1300000);
// 단가: 소수점 둘째 자리
assert.strictEqual(C.roundAmount(1234.5671, "ceil", 0.01), 1234.57);
assert.strictEqual(C.roundAmount(1234.5679, "floor", 0.01), 1234.56);
assert.strictEqual(C.roundAmount(1234.5, "ceil", 0.01), 1234.5);
// 예정가격 사사오입 / 투찰금액 절사
assert.strictEqual(C.plannedPrice([1000, 1001], "round"), 1001);
assert.strictEqual(C.bidAmount({ planned: 1000000, lowerRate: 87.7457, how: "floor" }), 877457);

// 행자부: 기초금액 아래 8개·위 7개, 범위 ±3% 안, 서로 다름, 금액순
for (let seed = 1; seed <= 50; seed++) {
  const p = C.makePrices({ basis: 1e9, lo: -3, hi: 3, n: 15, method: "mois", down: 8, up: 7, rng: C.seeded(seed) });
  assert.strictEqual(p.length, 15);
  assert.strictEqual(p.filter((x) => x >= 1e9).length, 7);
  assert.strictEqual(new Set(p).size, 15);
  p.forEach((x) => assert.ok(x >= 0.97e9 && x <= 1.03e9, String(x)));
  assert.deepStrictEqual(p, p.slice().sort((a, b) => a - b));
}
// 15개면 추출범위를 바꿔도 표대로 아래 8 · 위 7(21라인 −7/+8 캡처), 10개는 추출범위대로
{
  const p = C.makePrices({ basis: 1e9, lo: -3, hi: 3, method: "mois", down: 7, up: 8, rng: C.seeded(3) });
  assert.strictEqual(p.filter((x) => x >= 1e9).length, 7);
  const q = C.makePrices({ basis: 1e9, lo: -3, hi: 3, n: 10, method: "mois", down: 4, up: 6, rng: C.seeded(3) });
  assert.strictEqual(q.filter((x) => x > 1e9).length, 6);
  assert.deepStrictEqual(q, q.slice().sort((a, b) => a - b));
}

// 조달청(기본서 표, ±2%): 구간 i의 예비가격이 그 구간 안 — 1원 미만 올림이라 하한 이상·상한 + 1원 이내
for (let seed = 1; seed <= 30; seed++) {
  const r = C.rates("pps", 15, -2, 2, C.seeded(seed));
  r.forEach((x, i) => {
    const [a, b] = C.PPS_TABLE.slots[i];
    assert.ok(x >= a - 100 - 1e-9 && x <= b - 100 + 1e-9, `${i}: ${x}`);
    assert.ok(Math.abs(x * 1e4 - Math.round(x * 1e4)) < 1e-6, "0.0001% 단위");
  });
  const p = C.makePrices({ basis: 1e9, lo: -2, hi: 2, method: "pps", rng: C.seeded(seed) });
  assert.strictEqual(new Set(p).size, 15);
  p.forEach((x) => assert.ok(x >= 0.98e9 && x <= 1.02e9));
  // 21라인처럼 ①이 1구간(가장 높음) → ⑮ 가장 낮음, 섞지 않음
  assert.deepStrictEqual(p, p.slice().sort((a, b) => b - a));
}
// 조달청 표인데 공고 범위가 ±3%면 같은 비율로 늘림: 1번 구간(101.735~102.000) → 102.6025~103
{
  const r = C.rates("pps", 15, -3, 3, C.seeded(4));
  assert.ok(r[0] >= 2.6025 - 1e-9 && r[0] <= 3 + 1e-9, String(r[0]));
  assert.ok(r[14] >= -3 - 1e-9 && r[14] <= -2.6025 + 1e-9, String(r[14]));
}
// 행자부(기본서 표, ±3%): 1~8 아래 · 9~15 위, 구간 안
for (let seed = 1; seed <= 30; seed++) {
  const r = C.rates("mois", 15, -3, 3, C.seeded(seed), 8, 7);
  r.forEach((x, i) => {
    const [a, b] = C.MOIS_TABLE.slots[i];
    assert.ok(x >= a - 100 - 1e-9 && x <= b - 100 + 1e-9, `${i}: ${x}`);
    assert.ok(Math.abs(x * 1e4 - Math.round(x * 1e4)) < 1e-6, "0.0001% 단위");
  });
}
// 표 끝값이 기본서 그대로
assert.deepStrictEqual(C.PPS_TABLE.slots[7], [99.867, 100.133]);
assert.deepStrictEqual(C.MOIS_TABLE.slots[13], [102.14285715, 102.57142857]);
// 9번 하한은 100.00000001(기본서 표 — 8번 상한 100.00000000과 겹치지 않음, 10/19 원본 캡처로 확인)
assert.deepStrictEqual(C.MOIS_TABLE.slots[8], [100.00000001, 100.42857142]);
// 10개 공고는 표가 없어 예전 칸 나누기
{
  const p = C.makePrices({ basis: 1e9, lo: -2, hi: 2, n: 10, method: "pps", rng: C.seeded(5) });
  assert.strictEqual(p.length, 10);
}

// 예비가격 소수점 처리 — 2026-10-22 21라인 캡처(등비추출 ±3% 15개)와 15개 모두 같게
{
  const g = (basis, decimal) => C.makePrices({ basis, lo: -3, hi: 3, method: "geometric", decimal });
  const total = [119753086, 120282222, 120811234, 121340370, 121869506, 122398641, 122927654, 123456789,
    123985925, 124514938, 125044073, 125573209, 126102345, 126631357, 127160493];
  assert.deepStrictEqual(g(123456789, "total"), total);
  // 단가: 조달 기본서대로 소수점 셋째 자리에서 올림(소수 둘째 자리까지 — 10/23 사용자 결정, 21라인은 총액과 같은 정수)
  // ⑤ 98.7143% × 123,456,789 = 121,869,505.065… → 121,869,505.07, ⑫ 125,573,208.735… → 125,573,208.74
  assert.deepStrictEqual(g(123456789, "unit"), [119753085.33, 120282221.13, 120811233.47, 121340369.27, 121869505.07, 122398640.87,
    122927653.21, 123456789, 123985924.8, 124514937.14, 125044072.94, 125573208.74, 126102344.54, 126631356.88, 127160492.67]);
  g(123456789, "unit").forEach((v, i) => assert.ok(Math.abs(Math.ceil(v) - total[i]) <= 1, String(v)));
  // 시설공사/감리및설계: 10원 미만 버린 뒤 100원 미만 올림 — ⑤ 121,869,505.06 → …500, ⑫ 125,573,208.73 → …200
  assert.deepStrictEqual(g(123456789, "cnst"), [119753100, 120282300, 120811300, 121340400, 121869500, 122398700, 122927700,
    123456800, 123986000, 124515000, 125044100, 125573200, 126102400, 126631400, 127160500]);
  // 기초금액 123,456,798 캡처 — ⑮ 127,160,501.94 → 127,160,500
  assert.deepStrictEqual(g(123456798, "cnst"), [119753100, 120282300, 120811300, 121340400, 121869600, 122398700, 122927700,
    123456800, 123986000, 124515000, 125044100, 125573300, 126102400, 126631400, 127160500]);
  assert.deepStrictEqual(g(123456798, "total"), [119753095, 120282230, 120811243, 121340379, 121869514, 122398650, 122927663,
    123456798, 123985934, 124514947, 125044083, 125573218, 126102354, 126631367, 127160502]);
}

// 예정가격 — 21라인 ④⑧⑩⑬ 선택, 절상·원미만: 총액 123,853,611 (495,414,442 ÷ 4 = …610.5), 시설공사 123,853,650,
// 단가는 소수 둘째 자리(0.01원) 절상 123,853,609.99 → 투찰금액 108,675,350.09
{
  const g = (decimal) => C.makePrices({ basis: 123456789, lo: -3, hi: 3, method: "geometric", decimal });
  const pick = (p) => [4, 8, 10, 13].map((i) => p[i - 1]);
  assert.strictEqual(C.plannedPrice(pick(g("total")), "ceil", 1), 123853611);
  assert.strictEqual(C.plannedPrice(pick(g("unit")), "ceil", C.UNIT_PRICE_STEP), 123853609.99);
  assert.strictEqual(C.bidAmount({ planned: 123853609.99, lowerRate: 87.745, how: "ceil", unit: C.UNIT_PRICE_STEP }), 108675350.09);
  assert.strictEqual(C.plannedPrice(pick(g("cnst")), "ceil", 1), 123853650);
}

// 순공사원가 투찰가격 — 21라인 캡처: 기초금액 123,456,789·등비·④⑧⑩⑬(평균 123,853,610.5), 기타설정과 무관
{
  const p = C.makePrices({ basis: 123456789, lo: -3, hi: 3, method: "geometric" });
  const average = [4, 8, 10, 13].reduce((s, i) => s + p[i - 1], 0) / 4;
  assert.strictEqual(average, 123853610.5);
  const net = (netCost) => C.netCostBid({ average, basis: 123456789, netCost });
  assert.strictEqual(net(100000000), 98314997);
  assert.strictEqual(net(50000000), 49157499);
  assert.strictEqual(net(20000000), 19663000);
  // 투찰금액은 원 단위 예정가격(같은 절상·절사)으로 계산한 뒤 단위 처리(화면 예정가격 123,853,000·…700과 별개)
  const picks = [4, 8, 10, 13].map((i) => p[i - 1]);
  assert.strictEqual(C.bidAmount({ planned: C.plannedPrice(picks, "floor", 1), lowerRate: 87.745, how: "floor", unit: 1000 }), 108675000);
  assert.strictEqual(C.bidAmount({ planned: C.plannedPrice(picks, "ceil", 1), lowerRate: 87.745, how: "ceil", unit: 100 }), 108675400);
  assert.strictEqual(C.plannedPrice(picks, "ceil", 100), 123853700);  // 원 단위 예정가격(…611)으로 반올림하면 19,662,999 — 평균 그대로·올림이 맞음
}

// A값 30,000,000 + 단위·소수점 — 21라인 캡처(10/22) 4가지 모두 같음
{
  const pick = (decimal) => { const p = C.makePrices({ basis: 123456789, lo: -3, hi: 3, method: "geometric", decimal }); return [4, 8, 10, 13].map((i) => p[i - 1]); };
  const bid = (decimal, pm, bm, unit) => C.bidAmount({ planned: C.plannedPrice(pick(decimal), pm, 1), lowerRate: 87.745, a: 30000000, how: bm, unit });
  assert.strictEqual(bid("total", "ceil", "ceil", 1), 112351851);
  assert.strictEqual(bid("total", "ceil", "ceil", 100), 112351900);  // 자른 예정가격(…700)으로 하면 112,352,000
  assert.strictEqual(bid("total", "floor", "floor", 1000), 112351000);
  assert.strictEqual(bid("cnst", "ceil", "ceil", 1), 112351886);
}

// 등비추출 = 21라인과 같은 등간격 — 2026-10-22 21라인 캡처(울산안전체험관 R26BK01763222, 기초금액 10.5억 ±3% 15개) 그대로
{
  const p = C.makePrices({ basis: 1050000000, lo: -3, hi: 3, method: "geometric" });
  assert.deepStrictEqual(p, [
    1018500000, 1023000300, 1027499550, 1031999850, 1036500150, 1041000450, 1045499700, 1050000000,
    1054500300, 1058999550, 1063499850, 1068000150, 1072500450, 1076999700, 1081500000,
  ]);
  const r = C.rates("geometric", 15, -3, 3);
  assert.ok(Math.abs(r[1] - r[0] - 6 / 14) < 1e-12 && Math.abs(r[14] - r[13] - 6 / 14) < 1e-12, "같은 간격");
}

// 난수추출적용(21라인 캡처 대조): 아래 8개·위 7개, 구간 없이 범위 안, 0.0001% 단위, ① 가장 낮음 → ⑮ 가장 높음
for (let seed = 1; seed <= 30; seed++) {
  const r = C.rates("uniform", 15, -3, 3, C.seeded(seed), 8, 7);
  assert.strictEqual(r.filter((x) => x < 0).length, 8);
  assert.strictEqual(r.filter((x) => x > 0).length, 7);
  r.forEach((x) => assert.ok(x >= -3 - 1e-9 && x <= 3 + 1e-9 && Math.abs(x * 1e4 - Math.round(x * 1e4)) < 1e-6, String(x)));
  const p = C.makePrices({ basis: 1e8, lo: -3, hi: 3, method: "uniform", down: 8, up: 7, rng: C.seeded(seed) });
  assert.deepStrictEqual(p, p.slice().sort((a, b) => a - b));
  assert.strictEqual(p.filter((x) => x < 1e8).length, 8);
}

// 자동선택: 서로 다른 번호 k개, 1~n
{
  const d = C.pickRandom(15, 4, C.seeded(2));
  assert.strictEqual(d.length, 4);
  assert.strictEqual(new Set(d).size, 4);
  d.forEach((x) => assert.ok(x >= 1 && x <= 15));
}

// 불러온 뒤 자동 계산 버튼: 지방 → 행자부, 국가 → 조달청, 변동폭이 그 방식 기준과 다르면(15개) 난수추출적용
assert.strictEqual(C.autoPlan({ method: "mois", lo: -3, hi: 3 }).method, "mois");
assert.strictEqual(C.autoPlan({ method: "pps", lo: -2, hi: 2 }).method, "pps");
assert.strictEqual(C.autoPlan({ method: "mois", lo: -2, hi: 2 }).method, "uniform");
assert.strictEqual(C.autoPlan({ method: "pps", lo: -3, hi: 3 }).method, "uniform");
assert.strictEqual(C.autoPlan({ method: "mois", lo: -2, hi: 2, n: 10 }).method, "mois");  // 10개는 표를 안 써서 범위를 안 바꿈
assert.strictEqual(C.autoPlan({ method: "mois", lo: null, hi: null }).method, "mois");
assert.strictEqual(C.autoPlan({ method: "", lo: -2, hi: 2 }).method, "pps");

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
  assert.strictEqual(C.netCostBid({ average: planned, basis: 1e9, netCost: 9e8 }), 884205000);
  assert.strictEqual(C.netCostBid({ average: planned, basis: 1e9, netCost: null }), null);
}

// 잘못된 입력
assert.throws(() => C.makePrices({ basis: 0, lo: -3, hi: 3 }), /기초금액/);
assert.throws(() => C.makePrices({ basis: 1e8, lo: 3, hi: -3 }), /변동폭/);

console.log("bidcalc ok");
