"use strict";
// 투찰 계산기 탭 화면 — 21라인 "복수예가산출프로그램 v0.1" 모양. 계산은 bidcalc.js(BidCalc),
// 공고 값 불러오기는 서버 /api/bidcalc. app.js 다음에 읽힌다($·esc·api 사용).

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
  const radio = (name) => parseInt(document.querySelector(`input[name="${name}"]:checked`).value, 10);

  // SLOTS[i] = i+1번 칸의 예비가격(배치 순서). SORTED = 금액순
  let SLOTS = [], AUTO = [], SHOW_SORTED = false;
  const SELECTED = new Set();

  // 추출범위(기초금액 아래 -N개 / 위 +N개) — 합이 예가개수가 되게 서로 맞춘다
  function fillRange(down) {
    const n = radio("cN");
    const d = Math.max(0, Math.min(n, down));
    $("#cDown").innerHTML = Array.from({ length: n + 1 }, (_, i) => `<option value="${i}"${i === d ? " selected" : ""}>-${i}개</option>`).join("");
    $("#cUp").innerHTML = Array.from({ length: n + 1 }, (_, i) => `<option value="${i}"${i === n - d ? " selected" : ""}>+${i}개</option>`).join("");
  }
  const defaultDown = () => (radio("cN") === 15 ? 8 : 5);  // 행자부: 아래 8 · 위 7
  fillRange(defaultDown());
  $("#cDown").addEventListener("change", () => fillRange(parseInt($("#cDown").value, 10)));
  $("#cUp").addEventListener("change", () => fillRange(radio("cN") - parseInt($("#cUp").value, 10)));
  document.querySelectorAll('input[name="cN"]').forEach((r) => r.addEventListener("change", () => {
    fillRange(defaultDown());
    SLOTS = []; SELECTED.clear(); render();
  }));
  document.querySelectorAll('input[name="cTake"]').forEach((r) => r.addEventListener("change", () => { SELECTED.clear(); AUTO = []; render(); }));

  const aValue = () => ($("#cAOn").checked ? numIn("#cA") || 0 : 0);
  function rounding() {
    const unit = parseInt($("#cRoundUnit").value, 10);
    const target = $("#cRoundTarget").value;
    return { planned: target === "planned" ? unit : 1, bid: target === "bid" ? unit : 1 };
  }

  function generate(method) {
    $("#cMsg").textContent = "";
    try {
      const prices = C.makePrices({
        basis: numIn("#cBasis"), lo: numIn("#cLo"), hi: numIn("#cHi"), n: radio("cN"), method,
        down: parseInt($("#cDown").value, 10), up: parseInt($("#cUp").value, 10),
      });
      SLOTS = C.shuffle(prices);  // 나라장터처럼 번호와 금액은 무관하게
      SELECTED.clear(); AUTO = []; SHOW_SORTED = false;
      render();
    } catch (err) { $("#cMsg").textContent = err.message; }
  }
  document.querySelectorAll(".calc-btns [data-method]").forEach((b) => b.addEventListener("click", () => generate(b.dataset.method)));

  $("#cRandom").addEventListener("click", () => {
    if (!SLOTS.length) return;
    SLOTS = C.shuffle(SLOTS);
    SELECTED.clear(); AUTO = [];
    render();
  });
  $("#cShowAll").addEventListener("click", () => { SHOW_SORTED = !SHOW_SORTED; render(); });
  $("#cPrint").addEventListener("click", () => window.print());

  function select(nos) {
    const n = SLOTS.length, take = radio("cTake");
    const valid = [...new Set(nos.filter((x) => x >= 1 && x <= n))];
    if (valid.length !== take) { $("#cMsg").textContent = `1~${n} 사이 서로 다른 번호 ${take}개를 넣으세요`; return; }
    $("#cMsg").textContent = "";
    SELECTED.clear(); valid.forEach((x) => SELECTED.add(x));
    render();
  }
  $("#cAnalyze").addEventListener("click", () => {
    if (!SLOTS.length) { $("#cMsg").textContent = "예비가격을 먼저 만드세요"; return; }
    select(["#cA1", "#cA2", "#cA3", "#cA4"].map((id) => parseInt($(id).value, 10)).filter(Number.isFinite));
  });
  $("#cAutoStart").addEventListener("click", () => {
    if (!SLOTS.length) { $("#cMsg").textContent = "예비가격을 먼저 만드세요"; return; }
    AUTO = C.pickRandom(SLOTS.length, radio("cTake"));
    $("#cAuto").textContent = AUTO.map(circled).join(" ");
  });
  $("#cAutoApply").addEventListener("click", () => { if (AUTO.length) select(AUTO); });

  $("#cPrices").addEventListener("change", (e) => {
    const box = e.target.closest("input[data-no]");
    if (!box) return;
    const no = Number(box.dataset.no), take = radio("cTake");
    if (box.checked && SELECTED.size >= take) {
      box.checked = false;
      $("#cMsg").textContent = `선택개수(${take}개)만큼 골랐습니다 — 하나를 빼고 고르세요`;
      return;
    }
    $("#cMsg").textContent = "";
    if (box.checked) SELECTED.add(no); else SELECTED.delete(no);
    render();
  });

  function renderGrid() {
    const box = $("#cPrices");
    // 예비가격을 만들기 전에도 빈 표(번호만)를 보여 준다 — 21라인처럼
    const empty = !SLOTS.length;
    const slots = empty ? new Array(radio("cN")).fill(null) : SLOTS;
    const basis = numIn("#cBasis");
    const rows = Math.ceil(slots.length / 3);
    const cell = (price, no) => {
      if (price == null) {
        return `<div class="calc-price blank"><span class="no">${circled(no)}</span><span></span><span></span><span></span>
          <input type="checkbox" disabled aria-label="${no}번 (예비가격 없음)"></div>`;
      }
      const pct = basis ? (price / basis) * 100 : null;
      const on = no != null && SELECTED.has(no);
      return `<div class="calc-price${on ? " on" : ""}">
        <span class="no">${no != null ? circled(no) : ""}</span><span class="amt">${won(price)}</span>
        <span>${p4(pct)}</span><span>${pct == null ? "-" : p4(pct - 100)}</span>
        ${no != null ? `<input type="checkbox" data-no="${no}" aria-label="${no}번 선택"${on ? " checked" : ""}>` : "<span></span>"}</div>`;
    };
    const head = `<div class="calc-phead"><span>NO</span><span>예비가격</span><span>백분율</span><span>변동률</span><span>선택</span></div>`;
    let html = "";
    for (let c = 0; c < 3; c++) {
      html += `<div class="calc-pcol">${head}`;
      for (let r = 0; r < rows; r++) {
        const i = c * rows + r;
        if (i < slots.length) html += cell(slots[i], i + 1);
      }
      html += "</div>";
    }
    if (SHOW_SORTED && !empty) {
      const sorted = SLOTS.map((p, i) => ({ p, no: i + 1 })).sort((a, b) => a.p - b.p);
      html += `<div class="calc-sorted"><h4 class="calc-h">전체 예가 (금액순)</h4><ol>${sorted.map((s) =>
        `<li><span>${circled(s.no)}</span><span>${won(s.p)}</span><span>${basis ? p4((s.p / basis) * 100) : "-"}</span></li>`).join("")}</ol></div>`;
    }
    box.innerHTML = html;
  }

  function renderResult() {
    const take = radio("cTake");
    const basis = numIn("#cBasis");
    const nos = [...SELECTED].sort((a, b) => a - b);
    $("#cPicked").textContent = nos.length ? nos.map(circled).join(" ") : "-";
    const done = SLOTS.length && nos.length === take;
    const r = rounding();
    const planned = done ? C.plannedPrice(nos.map((no) => SLOTS[no - 1]), "ceil", r.planned) : null;
    const bid = planned ? C.bidAmount({ planned, lowerRate: numIn("#cRate"), a: aValue(), unit: r.bid }) : null;
    const netBid = planned ? C.netCostBid({ planned, basis, netCost: numIn("#cNet"), unit: r.bid }) : null;
    const higher = bid != null && netBid != null && netBid > bid;
    $("#cPlanned").textContent = planned ? won(planned) : "-";
    $("#cBid").innerHTML = bid != null ? `<span${higher ? "" : ' class="calc-final"'}>${won(bid)}</span>` : (planned ? "하한율 필요" : "-");
    $("#cNetBid").innerHTML = netBid != null ? `<span${higher ? ' class="calc-final"' : ""}>${won(netBid)}</span>` : "-";
    if (!planned) {
      $("#cSummary").innerHTML = `<tr><td colspan="3" class="why">${SLOTS.length ? `번호를 ${take}개 고르세요 (${nos.length}/${take})` : "예비가격을 만들고 번호를 고르면 나옵니다."}</td></tr>`;
      return;
    }
    const rows = [
      ["기초금액", basis, basis ? "100.0000" : "-"],
      ["예정가격", planned, basis ? p4((planned / basis) * 100) : "-"],
      ["투찰금액", bid, ""],
    ];
    if (netBid != null) rows.push(["순공사원가 투찰가격", netBid, ""]);
    $("#cSummary").innerHTML = rows.map(([k, v, rate]) => `<tr><td>${esc(k)}</td><td class="num">${won(v)}</td><td class="num">${rate}</td></tr>`).join("");
  }

  function render() { renderGrid(); renderResult(); }

  ["#cRate", "#cA", "#cNet"].forEach((id) => $(id).addEventListener("input", renderResult));
  ["#cAOn", "#cRoundTarget", "#cRoundUnit"].forEach((id) => $(id).addEventListener("change", renderResult));
  $("#cBasis").addEventListener("input", render);

  // 공고번호로 불러오기
  async function lookup() {
    const no = $("#cNo").value.trim();
    if (!no) { $("#cLookupMsg").textContent = "공고번호를 입력하세요"; return; }
    $("#cLookupMsg").textContent = "불러오는 중…";
    $("#cLookup").disabled = true;
    try {
      const d = await api(`/api/bidcalc?no=${encodeURIComponent(no)}`);
      const text = (id, v) => { $(id).textContent = v || "-"; };
      text("#cTitle", d.title);
      text("#cInst", d.institution);
      text("#cNoShow", d.notice_no ? `${d.notice_no}${d.notice_ord ? "-" + d.notice_ord : ""}` : "");
      text("#cLicense", (d.licenses || []).join(", "));
      text("#cEst", d.est_price ? `${won(d.est_price)}원` : "");
      text("#cStd", d.award_method);
      text("#cContract", d.contract_method);
      if (d.basis != null) setNum("#cBasis", d.basis);
      if (d.range_lo != null) $("#cLo").value = d.range_lo;
      if (d.range_hi != null) $("#cHi").value = d.range_hi;
      if (d.lower_rate != null) $("#cRate").value = d.lower_rate;
      setNum("#cA", d.a_value || null);
      $("#cAOn").checked = !!d.a_applies && (d.a_value || 0) > 0;
      setNum("#cNet", d.net_cost || null);
      if (d.price_count === 10 || d.price_count === 15) {
        document.querySelector(`input[name="cN"][value="${d.price_count}"]`).checked = true;
        fillRange(defaultDown());
      }
      const items = d.a_items || [];
      $("#cAItems").innerHTML = items.length
        ? `${items.map((i) => `${esc(i.name)} ${won(i.amount)}`).join(" + ")}${d.a_applies ? "" : " — A값 적용 표시가 없는 공고"}`
        : "";
      const notes = [];
      if (d.basis != null) notes.push(`기초금액 출처: ${d.basis_source || "나라장터"}${d.basis_open_at ? ` (공개 ${d.basis_open_at})` : ""}`);
      if (d.method) notes.push(`예비가격 방식: ${d.method === "mois" ? "지방계약 — 행자부난수추출" : "국가계약 — 조달청난수추출"} 권장`);
      notes.push(...(d.notes || []));
      $("#cNotes").innerHTML = notes.map((n) => `<div>${esc(n)}</div>`).join("");
      $("#cLookupMsg").textContent = d.basis != null ? "불러옴 — 값을 확인하세요" : "일부만 불러옴";
      SLOTS = []; SELECTED.clear(); AUTO = []; $("#cAuto").textContent = "-";
      render();
    } catch (err) {
      $("#cLookupMsg").textContent = `실패: ${err.message}`;
    } finally {
      $("#cLookup").disabled = false;
    }
  }
  $("#cLookup").addEventListener("click", lookup);
  $("#cNo").addEventListener("keydown", (e) => { if (e.key === "Enter") lookup(); });

  render();
})();
