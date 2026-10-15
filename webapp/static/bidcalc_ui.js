"use strict";
// 투찰 계산기 탭 화면 — 계산은 bidcalc.js(BidCalc), 공고 값 불러오기는 서버 /api/bidcalc. app.js 다음에 읽힌다($·esc·api 사용).

(function () {
  const C = window.BidCalc;
  const won = (v) => (v == null || Number.isNaN(v) ? "-" : Math.round(v).toLocaleString("ko-KR"));
  const p4 = (v) => (v == null || Number.isNaN(v) ? "-" : v.toFixed(4));
  const circled = (n) => (n >= 1 && n <= 20 ? String.fromCharCode(0x2460 + n - 1) : String(n));
  const numIn = (id) => {
    const v = parseFloat(String($(id).value).replace(/[,\s원%+]/g, ""));
    return Number.isFinite(v) ? v : null;
  };
  const setNum = (id, v) => { $(id).value = v == null ? "" : Number(v).toLocaleString("ko-KR", { maximumFractionDigits: 4 }); };

  let PRICES = [], COUNTS = null;
  const SELECTED = new Set();

  // 선택지 채우기
  $("#cMethod").innerHTML = Object.entries(C.METHODS).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("");
  $("#cMethod").value = "mois";
  const roundOpts = Object.entries(C.ROUNDING).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("");
  $("#cRoundPlanned").innerHTML = roundOpts;
  $("#cRoundBid").innerHTML = roundOpts;

  function opts() {
    const n = parseInt($("#cN").value, 10);
    return {
      basis: numIn("#cBasis"), lo: numIn("#cLo"), hi: numIn("#cHi"), n,
      method: $("#cMethod").value, up: Math.min(n, Math.max(0, parseInt($("#cUp").value, 10) || 0)),
      order: $("#cOrder").value, take: parseInt($("#cTake").value, 10),
      bidders: Math.max(1, parseInt($("#cBidders").value, 10) || 100), perBidder: 2,
      plannedRounding: $("#cRoundPlanned").value,
    };
  }
  const aValue = () => ($("#cAOn").checked ? numIn("#cA") || 0 : 0);

  function bidFor(planned) {
    return C.bidAmount({
      planned, lowerRate: numIn("#cRate"), a: aValue(), basis: numIn("#cBasis"),
      netCost: numIn("#cNet"), netCostRate: numIn("#cNetRate") ?? 98, rounding: $("#cRoundBid").value,
    });
  }

  function syncUp() {
    // 기초금액 위 개수는 행안부 방식에서만 쓴다
    const on = $("#cMethod").value === "mois";
    $("#cUp").disabled = !on;
    $("#cUp").closest("label").classList.toggle("off", !on);
  }
  $("#cMethod").addEventListener("change", syncUp);
  syncUp();

  function renderPrices() {
    const box = $("#cPrices");
    if (!PRICES.length) { box.innerHTML = ""; return; }
    const basis = numIn("#cBasis");
    box.style.setProperty("--rows", Math.ceil(PRICES.length / 3));
    box.innerHTML = PRICES.map((p) => {
        const on = SELECTED.has(p.no);
        const pct = basis ? (p.price / basis) * 100 : p.pct;
        return `<button type="button" class="calc-price${on ? " on" : ""}${p.price < basis ? " below" : ""}" data-no="${p.no}" aria-pressed="${on}">
          <span class="no">${circled(p.no)}</span><span class="amt">${won(p.price)}</span>
          <span title="기초금액 대비">${p4(pct)}</span><span title="변동률">${(pct - 100 >= 0 ? "+" : "") + p4(pct - 100)}</span>${COUNTS ? `<span class="cnt" title="모의 입찰자가 고른 횟수">${COUNTS[p.no]}회</span>` : ""}</button>`;
      }).join("");
  }

  $("#cPrices").addEventListener("click", (e) => {
    const b = e.target.closest(".calc-price");
    if (!b) return;
    const no = Number(b.dataset.no);
    const take = parseInt($("#cTake").value, 10);
    if (SELECTED.has(no)) SELECTED.delete(no);
    else if (SELECTED.size < take) SELECTED.add(no);
    else { $("#cMsg").textContent = `추첨 개수(${take}개)만큼 골랐습니다 — 하나를 빼고 고르세요`; return; }
    $("#cMsg").textContent = "";
    renderPrices();
    renderResult();
  });

  function resultRows(planned) {
    const basis = numIn("#cBasis");
    const bid = bidFor(planned);
    const rows = [
      ["예정가격", `${won(planned)}원`],
      ["사정률 (예정가격 ÷ 기초금액)", basis ? `${p4((planned / basis) * 100)}%` : "-"],
    ];
    if (!bid) rows.push(["투찰금액", "낙찰하한율을 입력하세요"]);
    else {
      rows.push([`하한선 = ${aValue() ? "A값 + (예정가격 − A값)" : "예정가격"} × 하한율`, `${won(bid.byRate)}원`]);
      if (bid.byNetCost != null) rows.push([`순공사원가 ${numIn("#cNetRate") ?? 98}% 기준`, `${won(bid.byNetCost)}원`]);
      rows.push(["투찰금액" + (bid.usedNetCost ? " (순공사원가 기준이 더 높음)" : ""), `<strong>${won(bid.amount)}원</strong>`]);
    }
    return rows.map(([k, v]) => `<div><span>${esc(k)}</span><b>${v}</b></div>`).join("");
  }

  function renderResult() {
    const out = $("#cResult");
    const take = parseInt($("#cTake").value, 10);
    if (!PRICES.length) { out.innerHTML = `<p class="why">예비가격을 만든 뒤 번호를 ${take}개 고르거나 "모의 추첨"을 누르세요.</p>`; return; }
    if (SELECTED.size < take) {
      out.innerHTML = `<p class="why">번호를 ${take}개 고르세요 (${SELECTED.size}/${take}) — 고른 번호의 예비가격 평균이 예정가격입니다.</p>`;
      return;
    }
    const nos = [...SELECTED].sort((a, b) => a - b);
    const planned = C.plannedPrice(nos.map((no) => PRICES[no - 1].price), $("#cRoundPlanned").value);
    out.innerHTML = `<p class="calc-picked">추첨 번호 ${nos.map(circled).join(" ")}</p><div class="calc-kv">${resultRows(planned)}</div>`;
  }

  function renderAssume() {
    const r = numIn("#cAssume"), basis = numIn("#cBasis");
    const out = $("#cAssumeOut");
    if (r == null || !basis) { out.innerHTML = ""; return; }
    const planned = C.roundAmount((basis * r) / 100, $("#cRoundPlanned").value);
    const bid = bidFor(planned);
    out.innerHTML = `예정가격 ${won(planned)}원 → 투찰금액 <strong>${bid ? won(bid.amount) + "원" : "하한율 필요"}</strong>`;
  }
  $("#cAssume").addEventListener("input", renderAssume);

  function guard(fn) {
    return () => {
      $("#cMsg").textContent = "";
      try { fn(); } catch (err) { $("#cMsg").textContent = err.message; }
    };
  }

  $("#cMake").addEventListener("click", guard(() => {
    PRICES = C.makePrices(opts());
    COUNTS = null;
    SELECTED.clear();
    renderPrices();
    renderResult();
  }));

  $("#cDraw").addEventListener("click", guard(() => {
    if (!PRICES.length) PRICES = C.makePrices(opts());
    const o = opts();
    COUNTS = C.simulatePicks(PRICES.length, o.bidders, o.perBidder, Math.random);
    SELECTED.clear();
    C.drawNumbers(COUNTS, o.take, Math.random).forEach((no) => SELECTED.add(no));
    renderPrices();
    renderResult();
  }));

  $("#cClear").addEventListener("click", () => { SELECTED.clear(); COUNTS = null; renderPrices(); renderResult(); });

  $("#cMonte").addEventListener("click", guard(() => {
    const o = { ...opts(), order: "random" };
    const s = C.monteCarlo(o, 10000);
    const basis = o.basis;
    const at = (rate) => {
      const planned = C.roundAmount((basis * rate) / 100, o.plannedRounding);
      const bid = bidFor(planned);
      return bid ? `${won(bid.amount)}원` : "-";
    };
    const rows = [["중앙값 (50%)", s.p50], ["가운데 절반 (25~75%)", null, `${p4(s.p25)} ~ ${p4(s.p75)}%`],
      ["10~90%", null, `${p4(s.p10)} ~ ${p4(s.p90)}%`], ["최소 ~ 최대", null, `${p4(s.min)} ~ ${p4(s.max)}%`]];
    $("#cMonteOut").innerHTML = `<h4 class="calc-h">모의 추첨 1만 번 — 사정률 분포 <span class="why">(${esc(C.METHODS[o.method])}, 입찰자 ${o.bidders}명이 2개씩 선택)</span></h4>
      <div class="calc-kv">${rows.map(([k, v, txt]) => `<div><span>${esc(k)}</span><b>${txt || p4(v) + "%"}</b></div>`).join("")}
      <div><span>중앙값 사정률이면 투찰금액</span><b>${at(s.p50)}</b></div></div>
      <p class="why">규칙대로 무작위로 돌린 결과일 뿐 실제 공고의 사정률 분포가 아닙니다. 실제 분포는 과거 개찰 결과로 따로 봐야 합니다.</p>`;
  }));

  // 입력이 바뀌면 결과 다시 계산(예비가격은 그대로)
  ["#cRate", "#cA", "#cNet", "#cNetRate"].forEach((id) => $(id).addEventListener("input", () => { renderResult(); renderAssume(); }));
  $("#cTake").addEventListener("change", () => { SELECTED.clear(); renderPrices(); renderResult(); });
  ["#cAOn", "#cRoundPlanned", "#cRoundBid"].forEach((id) => $(id).addEventListener("change", () => { renderResult(); renderAssume(); }));
  $("#cBasis").addEventListener("input", () => { renderPrices(); renderResult(); renderAssume(); });

  // 공고번호로 불러오기
  async function lookup() {
    const no = $("#cNo").value.trim();
    if (!no) { $("#cLookupMsg").textContent = "공고번호를 입력하세요"; return; }
    $("#cLookupMsg").textContent = "불러오는 중…";
    $("#cLookup").disabled = true;
    try {
      const d = await api(`/api/bidcalc?no=${encodeURIComponent(no)}`);
      if (d.basis != null) setNum("#cBasis", d.basis);
      if (d.range_lo != null) $("#cLo").value = d.range_lo;
      if (d.range_hi != null) $("#cHi").value = d.range_hi;
      if (d.lower_rate != null) $("#cRate").value = d.lower_rate;
      setNum("#cA", d.a_value || null);
      $("#cAOn").checked = !!d.a_applies && (d.a_value || 0) > 0;
      setNum("#cNet", d.net_cost || null);
      if (d.method) { $("#cMethod").value = d.method; syncUp(); }
      if (d.price_count === 10 || d.price_count === 15) $("#cN").value = String(d.price_count);
      const items = d.a_items || [];
      $("#cAItems").innerHTML = items.length
        ? `A값 구성: ${items.map((i) => `${esc(i.name)} ${won(i.amount)}`).join(" + ")} = ${won(d.a_value)}원${d.a_applies ? "" : " — 이 공고는 A값 적용 표시가 없어 '적용'을 꺼 둠"}`
        : "";
      const info = [d.title, d.institution, d.work_type, d.award_method, d.standard].filter(Boolean).map(esc).join(" · ");
      const notes = (d.notes || []).map((n) => `<li>${esc(n)}</li>`).join("");
      $("#cNotice").innerHTML = (info ? `<p>${info}</p>` : "") + (d.basis_open_at ? `<p class="why">기초금액 공개 ${esc(d.basis_open_at)}</p>` : "") + (notes ? `<ul class="why">${notes}</ul>` : "");
      $("#cNotice").hidden = !info && !notes;
      $("#cLookupMsg").textContent = d.basis != null ? "불러옴 — 값을 확인하세요" : "일부만 불러옴";
      PRICES = []; COUNTS = null; SELECTED.clear(); renderPrices(); renderResult(); renderAssume();
    } catch (err) {
      $("#cLookupMsg").textContent = `실패: ${err.message}`;
    } finally {
      $("#cLookup").disabled = false;
    }
  }
  $("#cLookup").addEventListener("click", lookup);
  $("#cNo").addEventListener("keydown", (e) => { if (e.key === "Enter") lookup(); });

  renderResult();
})();
