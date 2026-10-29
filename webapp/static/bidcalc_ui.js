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
    WORK = ""; $("#cRec").textContent = "";
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
      // 나라장터·공고문 어디에도 없는 값은 비워서 "-"로 보임(21라인과 같게, 10/23 사용자 요청 — 10/22의 "방식 기본값으로 채움"을 대체)
      $("#cLo").value = d.range_lo ?? "";
      $("#cHi").value = d.range_hi ?? "";
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
      // 발주기관 최종 판단(서버 judge — 낙찰자 결정기준 문구 → 공고문 인용 법령 → 기관 이름). 예비가격 방식·자동 계산이 이걸 따른다
      if (d.agency_note) notes.push(`발주기관: ${d.agency_note}`);
      if (d.basis != null) notes.push(`기초금액 출처: ${d.basis_source || "나라장터"}${d.basis_open_at ? ` (공개 ${d.basis_open_at})` : ""}`);
      if (d.method) {
        const why = d.method_reason ? ` (${d.method_reason})` : "";
        notes.push(`예비가격 방식: ${d.method === "mois" ? `지방계약${why} — 행자부난수추출` : `국가계약${why} — 조달청난수추출`} 권장`);
      }
      // 예가방법이 복수예가가 아니면(단일예가·비예가) 15개 추첨이 없는 공고 — 계산기가 맞지 않음
      if (d.price_method) notes.push(`예가방법: ${d.price_method}${/복수/.test(d.price_method) ? "" : " — 복수예비가격 추첨이 없는 공고, 계산기 결과는 참고만"}`);
      notes.push(...(d.notes || []));
      $("#cNotes").innerHTML = notes.map((n) => `<div>${esc(n)}</div>`).join("");
      lookupMsg(d.basis != null ? "불러옴 — 값을 확인하세요" : "일부만 불러옴");
      SLOTS = []; SELECTED.clear(); AUTO = []; $("#cAuto").textContent = "-";
      render();
      const auto = autoRun(d.method);
      if (auto) $("#cNotes").insertAdjacentHTML("beforeend", `<div class="calc-auto">${esc(auto)}</div>`);
      WORK = d.work_type || "";
      if (d.basis != null && d.lower_rate != null) recommend();
    } catch (err) {
      lookupMsg(`실패: ${err.message}`, true);
    } finally {
      $("#cLookup").disabled = false;
    }
  }
  // 불러온 뒤 자동 계산(2026-10-23 사용자 요청): 발주기관 판단에 맞는 추출 버튼 → 무작위 번호(선택개수만큼) → 예정가격·투찰금액.
  // 모의 추첨 1회일 뿐 — 실제 예정가격은 개찰 때 업체들이 고른 번호로 정해짐. 기초금액이 없으면 하지 않음
  function autoRun(method) {
    const missing = [["기초금액", "#cBasis"], ["변동폭", "#cLo"], ["변동폭", "#cHi"]].filter(([, id]) => numIn(id) == null).map(([n]) => n);
    if (missing.length) return `자동 계산 안 함 — ${[...new Set(missing)].join("·")} 정보 없음(-). 직접 입력한 뒤 추출 버튼을 누르세요`;
    const plan = C.autoPlan({ method, lo: numIn("#cLo"), hi: numIn("#cHi"), n: radio("cN") });
    generate(plan.method);
    if (!SLOTS.length) return "";
    AUTO = C.pickRandom(SLOTS.length, radio("cTake"));
    select(AUTO);
    return `자동 계산: ${plan.name}(${plan.why}) → 무작위 번호 ${AUTO.join("·")}번 — 모의 추첨 1회라 실제 예정가격과 다름. `
      + "다시 뽑으려면 자동선택 '시작 → 적용'";
  }

  // 추천 사정률(2026-10-28) — 위 입력칸(기초금액·하한율·변동폭·A값)과 불러온 공고의 업무로 서버에 묻는다.
  // 투찰금액은 계산기와 같은 기타설정(절상·절사·단위)으로 자름: 원 단위 예정가격(기초금액 × 사정률) → 하한율 → 끝자리
  let WORK = "";
  const pct = (v, d = 3) => (v == null ? "-" : `${Number(v).toFixed(d)}%`);
  async function recommend() {
    const title = $("#cTitle").textContent.trim();
    const qs = new URLSearchParams({ scale: $("#cRecScale").value, work: WORK, title: title === "-" ? "" : title });
    [["basis", "#cBasis"], ["lower", "#cRate"], ["lo", "#cLo"], ["hi", "#cHi"]].forEach(([k, id]) => {
      const v = numIn(id); if (v != null) qs.set(k, v);
    });
    qs.set("a", aValue());
    $("#cRecMsg").textContent = "계산 중…";
    $("#cRecBtn").disabled = true;
    try {
      renderRecommend(await api(`/api/bidrecommend?${qs}`));
      $("#cRecMsg").textContent = "";
    } catch (err) {
      $("#cRecMsg").textContent = `실패: ${err.message}`;
    } finally {
      $("#cRecBtn").disabled = false;
    }
  }
  function recBid(rate) {
    const basis = numIn("#cBasis"), lower = numIn("#cRate");
    if (!(basis > 0) || !(lower > 0)) return null;
    const r = rounding();
    const plannedWon = C.roundAmount((basis * rate) / 100, r.planned, 1);
    return C.bidAmount({ planned: plannedWon, lowerRate: lower, a: aValue(), how: r.bid, unit: r.unit });
  }
  // ── 추천 칸 화면(10/29 사용자 요청: 보기 쉽게 — 긴 설명은 ? 팝업으로) ──
  // 팝업은 공고 카드와 같은 .qual/.qtip(마우스를 대거나 누르면 열림, index.html placeTip이 위·아래 자리를 잡음)
  const tip = (html, right = false) =>
    `<span class="qual rec-i" tabindex="0" aria-label="설명"><span class="ico">?</span><span class="qtip${right ? " right" : ""}">${html}</span></span>`;
  // 서버 방식 이름 → 화면 이름·설명(다른 부서 사람도 읽게 쉬운 말로)
  const METHOD = {
    "추천(과거 1순위 최다)": ["과거 1순위 최다", "비슷한 과거 공고마다 '이 사정률이었으면 1순위'인 구간을 겹쳐 보고, 가장 많이 겹친 값이에요."],
    "추천(과거 5위 안 최다)": ["과거 5위 안 최다", "비슷한 과거 공고에서 5위 안에 가장 많이 들었을 값 중 가장 싼 값이에요."],
    "사정률 중앙값": ["과거 예정가격 가운데", "비슷한 과거 공고의 실제 사정률(예정가격 ÷ 기초금액)을 줄 세웠을 때 한가운데 값이에요. 하한선 통과는 절반쯤이에요."],
    "유효 70%": ["하한선 통과 70% 선", "과거 비슷한 공고 10건 중 7건에서 하한선을 넘었을 값이에요."],
    "유효 90%": ["하한선 통과 90% 선", "과거 비슷한 공고 10건 중 9건에서 하한선을 넘었을 값이에요. 대신 금액이 높아 순위는 밀려요."],
    "경향 추천(사정률 중앙 + 1~5위 간격)": ["경향 추천", "과거 예정가격 가운데 값에, 1~5위 업체들이 실제보다 얼마나 위에 썼는지(가운데 값)를 더한 값이에요."],
    "대규모 추천(사정률 분포 ÷ 경쟁 밀집)": ["대규모 경쟁 추천", "참가 업체가 많은 공고용이에요. 과거에 예정가격이 자주 나온 범위 안에서, 다른 업체들이 덜 몰린 금액을 골랐어요."],
    "지일 실제 투찰(외주)": ["지일 실제 투찰(참고)", "같은 과거 공고에 지일이 실제로 낸 금액의 나라장터 결과예요. 비교용 참고 줄이에요."],
  };
  const label = (name) => (METHOD[name] ? METHOD[name][0] : name.startsWith("고정") ? name : name);
  const about = (name) => (METHOD[name] ? METHOD[name][1] : name.startsWith("고정")
    ? "늘 같은 사정률로 쓰는 기준값이에요(과거 지일 소규모 공고로 고른 값)." : "");
  const rate = (t, key) => (t && t.tried ? t[key] / t.tried : null);
  const pctOf = (v) => (v == null ? "-" : `${Math.round(v * 100)}%`);
  const cnt = (t, key) => (t && t.tried ? `${t[key]}/${t.tried}건` : "");

  function renderRecommend(d) {
    const notes = (d.notes || []);
    if (!d.samples) {
      $("#cRec").innerHTML = notes.length ? notes.map((n) => `<div class="rec-warn">${esc(n)}</div>`).join("") : "비슷한 과거 공고가 없습니다.";
      return;
    }
    const p = d.pick, a = d.assessment, b = d.bidders;
    const large = b && b.median >= 100;
    // ① 추천 투찰금액 카드 — 금액 · 방식 · 핵심 숫자 3개(하한선 통과 / 5위 안 / 1순위), 설명은 ? 팝업
    let card = "";
    if (p) {
      const bid = recBid(p.rate), t = p.track;
      const how = p.mode === "crowd"
        ? `${about(p.name)}<br><br>· 하한선을 넘을 가능성(계산): 약 ${pctOf(p.valid_p)}<br>· 1순위가 될 가능성(계산): 약 ${(p.win_p * 100).toFixed(2)}%`
          + (p.crowd_bidders ? ` — 아무 금액이나 썼을 때의 평균(1 ÷ ${Math.round(p.crowd_bidders)}곳)은 ${(100 / p.crowd_bidders).toFixed(2)}%` : "")
        : p.mode === "trend"
          ? `과거 예정가격 가운데 값 ${pct(p.base)}에, 1~5위 업체들이 실제보다 위에 쓴 정도(가운데 값) ${p.gap >= 0 ? "+" : ""}${Number(p.gap).toFixed(3)}%p를 더했어요.<br><br>방식별 성적에 뚜렷한 차이가 없을 때 쓰는 기본 추천이에요.`
          : p.mode === "fixed"
            ? `비슷한 과거 공고가 적어 기준값(${esc(p.name)})으로 두었어요.`
            : `${about(p.name)}<br><br>비슷한 과거 공고에 적용해 봤을 때 1순위가 가장 많았던 방식이에요.`;
      const stat = (name, value, sub, help) => `<div class="rec-stat"><div class="rec-stat-k">${name} ${tip(help)}</div>
        <div class="rec-stat-v">${value}</div><div class="rec-stat-s">${sub}</div></div>`;
      const stats = t && t.tried ? `<div class="rec-stats">
          ${stat("하한선 통과", pctOf(rate(t, "valid")), cnt(t, "valid"), "과거 비슷한 공고에 이 방법을 그대로 썼다면 하한선을 넘었을(탈락하지 않았을) 비율이에요. 그 공고 개찰 전 자료만으로 계산했어요.")}
          ${stat("5위 안", pctOf(rate(t, "top")), cnt(t, "top"), "같은 방법으로 금액 기준 5위 안에 들었을 비율이에요.")}
          ${stat("1순위", pctOf(rate(t, "win")), cnt(t, "win"), "같은 방법으로 금액 기준 1순위였을 비율이에요(적격심사 서류 심사 전 순위).")}
        </div>` : "";
      const luck = large ? `<div class="rec-flag">참가 업체가 많은 공고(약 ${Math.round(b.median)}곳) — 1순위는 운의 비중이 커요 ${tip(
        "업체가 수백 곳이면 1순위는 하한선 바로 위(금액으로 몇천~몇만 원 차이)에서 갈려서, 어떤 방법도 1순위 비율이 1~2% 안팎이에요. 하한선 통과와 5위 안을 함께 보세요.")}</div>` : "";
      card = `<div class="rec-pick${p.mode === "fixed" ? " weak" : ""}">
        <div class="rec-pick-top">
          <div><div class="rec-pick-label">추천 투찰금액</div>
            <div class="rec-pick-amt">${bid != null ? `${won(bid)}<span class="rec-unit">원</span>` : "기초금액·하한율을 넣어 주세요"}</div>
            <div class="rec-pick-sub">가정 사정률 ${pct(p.rate)}</div></div>
          <div class="rec-method">${esc(label(p.name))} ${tip(`<h4>어떻게 골랐나</h4>${how}`, true)}</div>
        </div>
        ${stats}${luck}
      </div>`;
    }
    // ② 비슷한 과거 공고 한 줄 + ? 팝업(범위·조건)
    const cond = (d.conditions || []).map((c) => `<span class="rec-chip">${esc(c)}</span>`).join("");
    const basis = `<div class="rec-basis">비슷한 과거 공고 <b>${d.samples}건</b> 기준 ${cond} ${tip(`<h4>비슷한 과거 공고</h4>
      과거 개찰 자료 ${d.total}건 중 분야·업무·변동폭·경쟁 규모가 같은 공고예요. 모자라면 조건을 하나씩 풀어요.
      <ul><li>실제 사정률(예정가격 ÷ 기초금액): 10% ${pct(a.p10)} · 가운데 ${pct(a.p50)} · 90% ${pct(a.p90)}</li>
      <li>1순위 업체 투찰률 − 하한율: 가운데 ${d.winner_gap >= 0 ? "+" : ""}${Number(d.winner_gap).toFixed(3)}%p</li>
      <li>참가 업체: 가운데 ${b.median}곳 · 최대 ${b.max}곳</li></ul>${d.auto_scale ? `<p>${esc(d.auto_scale)}</p>` : ""}`, true)}</div>`;
    // ③ 방식별 비교 — 퍼센트를 크게, 건수는 작게. 추천으로 고른 줄 표시
    const opts = d.options || [];
    const cell = (t, key) => (t && t.tried ? `<b>${pctOf(rate(t, key))}</b><small>${t[key]}건</small>` : "-");
    const row = (o, own = false) => {
      const bid = own ? null : recBid(o.rate);
      const chosen = p && !own && o.name === p.name;
      return `<tr class="${chosen ? "chosen" : ""}${own ? " own" : ""}">
        <td>${esc(label(o.name))}${chosen ? '<span class="rec-tag">추천</span>' : ""} ${tip(about(o.name))}</td>
        <td>${own ? "-" : bid != null ? won(bid) : "-"}</td><td>${pct(o.rate)}${own ? "<small>과거 가운데</small>" : ""}</td>
        <td>${cell(o.track, "valid")}</td><td>${cell(o.track, "top")}</td><td>${cell(o.track, "win")}</td></tr>`;
    };
    const tried = opts.map((o) => o.track && o.track.tried).find(Boolean);
    const table = `<details class="rec-more" open><summary>방식별 비교${tried ? ` — 과거 비슷한 공고 ${tried}건에 적용해 봤다면` : ""}
        ${tip(`<h4>방식별 비교 읽는 법</h4>과거 비슷한 공고마다 <b>그 공고 개찰 전 자료만으로</b> 이 방법대로 금액을 정했다면 어땠을지 센 결과예요.
        <ul><li>하한선 통과: 탈락하지 않았을 비율</li><li>5위 안·1순위: 금액 기준 개찰 순위(적격심사 서류 심사 전)</li></ul>
        <p>건수가 수십 건 이하면 우연일 수 있어요. 투찰금액 끝자리는 위 기타설정대로 잘라요.</p>`, true)}</summary>
      <div class="rec-scroll"><table class="rec-table"><thead><tr><th>방식</th><th>투찰금액</th><th>가정 사정률</th><th>하한선 통과</th><th>5위 안</th><th>1순위</th></tr></thead>
      <tbody>${opts.map((o) => row(o)).join("")}${d.outsourced ? row(d.outsourced, true) : ""}</tbody></table></div></details>`;
    const warn = notes.filter((n) => !/대규모 경쟁이 많습니다/.test(n)).map((n) => `<div class="rec-warn">${esc(n)}</div>`).join("");
    $("#cRec").innerHTML = `${card}${basis}${table}${trendHtml(d)}${warn}`;
  }
  // 투찰 경향(같은 분야 과거 1~5위 업체·지일) — 접어 두고 한 줄 요약만
  function trendHtml(d) {
    const t = d.trend;
    if (!t || !t.n) return "";
    const g = (s, k = "p50") => (s && s.n ? `${s[k] >= 0 ? "+" : ""}${Number(s[k]).toFixed(3)}%p` : "-");
    const range = (s) => (s && s.n ? `${g(s, "p25")} ~ ${g(s, "p75")}` : "-");
    const ranked = t.ranks.filter((r) => r.gap && r.gap.n);
    const sum = ranked.length ? `1~${ranked[ranked.length - 1].rank}위 업체는 실제 사정률보다 ${g(ranked[0].gap)} ~ ${g(ranked[ranked.length - 1].gap)} 위에 썼어요` : "";
    const rows = t.ranks.map((r) => `<tr><td>${r.rank}위 업체</td><td>${g(r.gap)}</td><td>${range(r.gap)}</td><td>${r.gap.n || 0}건</td></tr>`).join("");
    const o = t.own;
    const ownRow = o ? `<tr class="own"><td>지일(참고)</td><td>${g(o.gap)}</td><td>${range(o.gap)}</td><td>${o.tried}건</td></tr>` : "";
    return `<details class="rec-more"><summary>투찰 경향 — ${esc(d.field || "비슷한 공고")} ${t.n}건${sum ? ` · ${sum}` : ""}
        ${tip(`<h4>투찰 경향 읽는 법</h4>각 순위 업체가 쓴 금액을 사정률로 바꿨을 때, 실제 사정률보다 얼마나 위였는지예요.
        <ul><li>0에 가까울수록 하한선에 바짝 붙은 금액</li><li>음수면 하한선 아래(탈락)</li></ul>
        <p>새 공고에선 "예상 사정률 + 이 값"이 그 순위대 금액이에요. 분야는 공고명 키워드로 나눠요.</p>`, true)}</summary>
      <div class="rec-scroll"><table class="rec-table"><thead><tr><th>구분</th><th>실제보다 위(가운데)</th><th>가운데 절반 범위</th><th>건수</th></tr></thead>
      <tbody>${rows}${ownRow}</tbody></table></div></details>`;
  }
  $("#cRecBtn").addEventListener("click", recommend);
  $("#cRecScale").addEventListener("change", recommend);

  $("#cLookup").addEventListener("click", lookup);
  $("#cNo").addEventListener("keydown", (e) => { if (e.key === "Enter") lookup(); });

  render();
})();
