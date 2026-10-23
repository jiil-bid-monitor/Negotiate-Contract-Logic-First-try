"use strict";
// 투찰 계산기 탭 화면 — 21라인 "복수예가산출프로그램 v0.1" 모양. 계산은 bidcalc.js(BidCalc),
// 공고 값 불러오기는 서버 /api/bidcalc. 화면(index.html) 본 스크립트와 이름이 겹치지 않게 쓰는 도구를 여기 안에 둔다
// (2026-10-19 화면 이식 — 본 스크립트의 $는 getElementById라 모양이 다르다).

(function () {
  const C = window.BidCalc;
  const $ = (sel) => document.querySelector(sel);
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  // 접속 토큰은 쿠키로 간다(처음 ?t= 주소로 들어올 때 서버가 심음)
  const api = async (path) => {
    const res = await fetch(path, { credentials: "same-origin" });
    const data = await res.json().catch(() => ({ error: `서버 응답을 읽지 못했습니다 (HTTP ${res.status})` }));
    if (!res.ok || data.error) throw new Error(data.error || `HTTP ${res.status}`);
    return data;
  };
  // 금액 — 단가(소수점) 계산이면 소수점 둘째 자리까지 보임
  const won = (v) => (v == null || Number.isNaN(v) ? "-" : Number(v).toLocaleString("ko-KR", { maximumFractionDigits: 2 }));
  // 소수 넷째 자리까지 늘 표시(21라인 표기 — 97.0000 · -3.0000 · 100.4286, 10/22 캡처), -0은 0.0000
  const p4 = (v) => (v == null || Number.isNaN(v) ? "-" : (Number(v.toFixed(4)) || 0).toFixed(4));
  // 번호 동그라미 — ①② 같은 글자는 글꼴에 따라 너무 작게 나와(10/15 제보) 숫자에 원 테두리를 그린다
  // 가운데 정렬은 글자 폭 기준이라 "1"처럼 잉크가 한쪽에 쏠린 숫자는 치우쳐 보임(10/22 제보) — 실제 글꼴로 잉크 중심을 재서 그만큼 옮김
  const NUM_SHIFT = {};
  const numShift = (n) => {
    if (n in NUM_SHIFT) return NUM_SHIFT[n];
    let shift = 0;
    try {
      const ctx = document.createElement("canvas").getContext("2d");
      ctx.font = "700 105px Arial, Helvetica, sans-serif"; // .calc-num 글자의 10배 — 소수점 정밀도
      const m = ctx.measureText(String(n));
      shift = ((m.actualBoundingBoxRight - m.actualBoundingBoxLeft) / 2 - m.width / 2) / 10;
    } catch (e) { /* 못 재면 그대로 */ }
    return (NUM_SHIFT[n] = Math.round(shift * 100) / 100);
  };
  const circled = (n) => `<span class="calc-num"><span style="transform:translateX(${-numShift(n)}px)">${n}</span></span>`;
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
  // 기타설정(21라인과 같은 세 상자): 소수점 종류 · 예정가격/투찰금액 절상·절사·사사오입 · 끝자리 단위
  // 단위는 예정가격·투찰금액 둘 다에 적용. 단가도 "원미만" = 1원(10/22 21라인 대조 — ④⑧⑩⑬ 예정가격 123,853,611, 총액과 같음)
  function rounding() {
    const [planned, bid] = $("#cRoundMode").value.split("/");
    const unit = parseInt($("#cRoundUnit").value, 10);
    return { planned, bid, unit };
  }

  function generate(method) {
    $("#cMsg").textContent = "";
    try {
      // 21라인은 행자부·조달청 버튼을 누르면 변동폭을 표 범위로 바꿈(행자부 −3~3, 조달청 −2~2 — 10/22 캡처). 15개만(표가 15구간)
      if (radio("cN") === 15 && (method === "mois" || method === "pps")) {
        const w = method === "mois" ? 3 : 2;
        $("#cLo").value = -w; $("#cHi").value = w;
      }
      const prices = C.makePrices({
        basis: numIn("#cBasis"), lo: numIn("#cLo"), hi: numIn("#cHi"), n: radio("cN"), method,
        down: parseInt($("#cDown").value, 10), up: parseInt($("#cUp").value, 10), decimal: $("#cDecimal").value,
      });
      SLOTS = prices;  // 21라인처럼 만든 순서 그대로 번호(섞지 않음 — 10/22 캡처 대조). 섞기는 랜덤배치 버튼
      SELECTED.clear(); AUTO = []; SHOW_SORTED = false;
      render();
    } catch (err) { $("#cMsg").textContent = err.message; }
  }
  document.querySelectorAll(".calc-btns [data-method]").forEach((b) => b.addEventListener("click", () => generate(b.dataset.method)));

  // 반전배치(21라인 버튼, 10/22 캡처): 지금 번호 순서를 거꾸로 — ①↔⑮. 21라인은 반전 직후 백분율 칸만 예전 순서로 남는데
  // (번호를 고르면 다시 맞춰짐) 그건 따라 하지 않는다
  $("#cReverse").addEventListener("click", () => {
    if (!SLOTS.length) return;
    SLOTS = SLOTS.slice().reverse();
    SELECTED.clear(); AUTO = [];
    render();
  });
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
    $("#cAuto").innerHTML = AUTO.map(circled).join("");
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
    $("#cPicked").innerHTML = nos.length ? nos.map(circled).join("") : "-";
    $("#cAuto").innerHTML = AUTO.length ? AUTO.map(circled).join("") : "-";
    const done = SLOTS.length && nos.length === take;
    const r = rounding();
    const picks = nos.map((no) => SLOTS[no - 1]);
    const planned = done ? C.plannedPrice(picks, r.planned, r.unit) : null;
    // 투찰금액은 화면 단위(백원·천원…)로 자른 예정가격이 아니라 **원 단위 예정가격**(같은 절상·절사)에 하한율을 곱한 뒤 단위 처리 —
    // 21라인 캡처(10/22): 절사·천원 123,853,000 → 투찰 108,675,000, 절상·백원 123,853,700 → 108,675,400 (자른 값이면 108,674,000·108,675,500)
    const plannedWon = done ? C.plannedPrice(picks, r.planned, 1) : null;
    const bid = planned ? C.bidAmount({ planned: plannedWon, lowerRate: numIn("#cRate"), a: aValue(), how: r.bid, unit: r.unit }) : null;
    const average = done ? nos.reduce((sum, no) => sum + SLOTS[no - 1], 0) / nos.length : null;  // 끝자리 처리 전 평균
    const netBid = planned ? C.netCostBid({ average, basis, netCost: numIn("#cNet") }) : null;
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

  // 금액 칸(기초금액·A값·순공사원가)은 칠 때마다 천 단위 쉼표(10/22 요청 — 21라인 "30,000,000"처럼). 커서는 같은 숫자 뒤에 둔다
  function commaInput(el) {
    const caret = el.selectionStart ?? el.value.length;
    const digitsBefore = el.value.slice(0, caret).replace(/[^\d.]/g, "").length;
    const raw = el.value.replace(/[^\d.]/g, "");
    if (!raw) { el.value = ""; return; }
    const [int, frac] = raw.split(".");
    const out = Number(int || 0).toLocaleString("ko-KR") + (frac != null ? "." + frac : "");
    el.value = out;
    let seen = 0, pos = 0;
    while (pos < out.length && seen < digitsBefore) { if (/[\d.]/.test(out[pos])) seen++; pos++; }
    el.setSelectionRange(pos, pos);
  }
  ["#cBasis", "#cA", "#cNet"].forEach((id) => $(id).addEventListener("input", (e) => commaInput(e.target)));
  ["#cRate", "#cA", "#cNet"].forEach((id) => $(id).addEventListener("input", renderResult));
  ["#cAOn", "#cDecimal", "#cRoundMode", "#cRoundUnit"].forEach((id) => $(id).addEventListener("change", renderResult));
  $("#cBasis").addEventListener("input", render);

  // 공고번호로 불러오기
  const lookupMsg = (msg, err = false) => { $("#cLookupMsg").textContent = msg; $("#cLookupMsg").classList.toggle("err", err); };
  // 공고번호 모양(R26BK01778736, 차수 "-000"이 붙어도 됨) — 글자 수·문자가 틀리면 나라장터에 묻기 전에 다시 입력하게
  const NOTICE_NO_RE = /^[A-Z0-9]{10,14}(-\d{1,3})?$/;
  const badNo = (msg) => { lookupMsg(msg, true); $("#cNo").focus(); $("#cNo").select(); };
  // 못 찾은 공고면 앞 공고 값이 남지 않게 위 표·입력칸을 비움
  const clearForm = () => {
    ["#cTitle", "#cInst", "#cNoShow", "#cLicense", "#cEst", "#cStd", "#cContract"].forEach((id) => { $(id).textContent = "-"; });
    ["#cBasis", "#cRate", "#cA", "#cNet"].forEach((id) => { $(id).value = ""; });
    $("#cAOn").checked = false;
    $("#cAItems").innerHTML = "";
    $("#cNotes").innerHTML = "";
    SLOTS = []; SELECTED.clear(); AUTO = []; $("#cAuto").textContent = "-";
    render();
  };
  async function lookup() {
    const no = $("#cNo").value.replace(/\s/g, "").toUpperCase();
    if (!no) { badNo("공고번호를 입력하세요"); return; }
    if (!NOTICE_NO_RE.test(no)) { badNo(`공고번호 "${no}" 형식이 맞지 않습니다 — 다시 입력하세요 (예: R26BK01778736)`); return; }
    $("#cNo").value = no;
    lookupMsg("불러오는 중…");
    $("#cLookup").disabled = true;
    try {
      const d = await api(`/api/bidcalc?no=${encodeURIComponent(no)}`);
      if (d.lookup_error) { lookupMsg("나라장터 조회 실패 — 잠시 뒤 다시 불러오세요", true); return; }
      if (d.found === false) { clearForm(); badNo(`공고번호 ${d.notice_no || no}를 나라장터에서 찾지 못했습니다 — 공고번호를 확인해 다시 입력하세요`); return; }
      const text = (id, v) => { $(id).textContent = v || "-"; };
      text("#cTitle", d.title);
      text("#cInst", d.institution);
      text("#cNoShow", d.notice_no ? `${d.notice_no}${d.notice_ord ? "-" + d.notice_ord : ""}` : "");
      text("#cLicense", (d.licenses || []).join(", "));
      text("#cEst", d.est_price ? `${won(d.est_price)}원` : "");
      text("#cStd", d.award_method);
      text("#cContract", d.contract_method);
      // 못 찾은 값은 비움 — 앞 공고 값이 남으면 그 값으로 계산됨(10/22 휘문고: 제천 하한율 85.495가 남음)
      setNum("#cBasis", d.basis);
      // 변동폭을 못 찾으면 예가 방식의 기본 범위(기본서 — 행자부 ±3%, 조달청 ±2%) — 이전 공고 값이 남지 않게
      const rangeGuess = (d.range_lo == null || d.range_hi == null) && d.method ? (d.method === "mois" ? 3 : 2) : null;
      $("#cLo").value = rangeGuess != null ? -rangeGuess : d.range_lo ?? $("#cLo").value;
      $("#cHi").value = rangeGuess != null ? rangeGuess : d.range_hi ?? $("#cHi").value;
      $("#cRate").value = d.lower_rate ?? "";
      // 예비가격 소수점 처리기준(기본서): 시설공사·감리/설계 용역은 100원 미만 올림, 그 밖은 총액(1원 미만 올림)
      $("#cDecimal").value = d.work_type === "공사" || /감리|설계\s*용역/.test(d.title || "") ? "cnst" : "total";
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
      // 발주기관 최종 판단(공고기관·수요기관을 함께 봄 — 서버 agency_judgment). 예비가격 방식·자동 계산이 이걸 따른다
      if (d.agency_note) notes.push(`발주기관: ${d.agency_note}`);
      if (d.basis != null) notes.push(`기초금액 출처: ${d.basis_source || "나라장터"}${d.basis_open_at ? ` (공개 ${d.basis_open_at})` : ""}`);
      if (d.method) {
        const why = d.method_reason ? ` (${d.method_reason})` : "";
        notes.push(`예비가격 방식: ${d.method === "mois" ? `지방계약${why} — 행자부난수추출` : `국가계약${why} — 조달청난수추출`} 권장`);
      }
      // 예가방법이 복수예가가 아니면(단일예가·비예가) 15개 추첨이 없는 공고 — 계산기가 맞지 않음
      if (rangeGuess != null) notes.push(`변동폭 정보 없음 — ${d.method === "mois" ? "행자부" : "조달청"} 기준 ±${rangeGuess}%로 채움, 공고문 확인`);
      if (d.price_method) notes.push(`예가방법: ${d.price_method}${/복수/.test(d.price_method) ? "" : " — 복수예비가격 추첨이 없는 공고, 계산기 결과는 참고만"}`);
      notes.push(...(d.notes || []));
      $("#cNotes").innerHTML = notes.map((n) => `<div>${esc(n)}</div>`).join("");
      lookupMsg(d.basis != null ? "불러옴 — 값을 확인하세요" : "일부만 불러옴");
      SLOTS = []; SELECTED.clear(); AUTO = []; $("#cAuto").textContent = "-";
      render();
      const auto = autoRun(d.method);
      if (auto) $("#cNotes").insertAdjacentHTML("beforeend", `<div class="calc-auto">${esc(auto)}</div>`);
    } catch (err) {
      lookupMsg(`실패: ${err.message}`, true);
    } finally {
      $("#cLookup").disabled = false;
    }
  }
  // 불러온 뒤 자동 계산(2026-10-23 사용자 요청): 발주기관 판단에 맞는 추출 버튼 → 무작위 번호(선택개수만큼) → 예정가격·투찰금액.
  // 모의 추첨 1회일 뿐 — 실제 예정가격은 개찰 때 업체들이 고른 번호로 정해짐. 기초금액이 없으면 하지 않음
  function autoRun(method) {
    if (numIn("#cBasis") == null) return "";
    const plan = C.autoPlan({ method, lo: numIn("#cLo"), hi: numIn("#cHi"), n: radio("cN") });
    generate(plan.method);
    if (!SLOTS.length) return "";
    AUTO = C.pickRandom(SLOTS.length, radio("cTake"));
    select(AUTO);
    return `자동 계산: ${plan.name}(${plan.why}) → 무작위 번호 ${AUTO.join("·")}번 — 모의 추첨 1회라 실제 예정가격과 다름. `
      + "다시 뽑으려면 자동선택 '시작 → 적용'";
  }

  $("#cLookup").addEventListener("click", lookup);
  $("#cNo").addEventListener("keydown", (e) => { if (e.key === "Enter") lookup(); });

  render();
})();
