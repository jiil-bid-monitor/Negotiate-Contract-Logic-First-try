"use strict";
// 입찰 공고 싱크로율 탐색기 — 화면 스크립트. 서버(webapp/server.py) JSON API만 부른다.

const $ = (s, el = document) => el.querySelector(s);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
// 이 화면이 기대하는 수집 결과 형식 — webapp/collect.py RESULT_FORMAT과 같이 올린다
const APP_FORMAT = 26;
const store = {
  get(k, d = null) { try { return localStorage.getItem(k) ?? d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* 저장 불가여도 동작 */ } },
};

// ── 접속 토큰(?t=) — 서버를 사내망에 열었을 때만 쓰인다 ──
const urlToken = new URLSearchParams(location.search).get("t");
if (urlToken) store.set("token", urlToken);
const token = urlToken || store.get("token") || "";

async function api(path, body) {
  const opt = { headers: { "X-App-Token": token } };
  if (body !== undefined) { opt.method = "POST"; opt.headers["Content-Type"] = "application/json"; opt.body = JSON.stringify(body); }
  const res = await fetch(path, opt);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

// ── 테마 ──
function applyTheme(mode) {
  if (mode === "system") document.documentElement.removeAttribute("data-theme");
  else document.documentElement.setAttribute("data-theme", mode);
  document.querySelectorAll("[data-theme-set]").forEach((b) => b.classList.toggle("on", b.dataset.themeSet === mode));
  store.set("theme", mode);
}
document.querySelectorAll("[data-theme-set]").forEach((b) => b.addEventListener("click", () => applyTheme(b.dataset.themeSet)));
applyTheme(store.get("theme", "system"));

// ── 내 이름 ──
const nameInput = $("#myName");
nameInput.value = store.get("myName", "");
nameInput.addEventListener("input", () => store.set("myName", nameInput.value.trim()));
nameInput.addEventListener("input", debounce(render, 300));
const myName = () => nameInput.value.trim();
function needName() {
  if (myName()) return false;
  alert("상단 '내 이름'을 먼저 입력하세요.");
  nameInput.focus();
  return true;
}

// ── 상태 ──
let META = null, RUN = null, STATES = {}, COUNTS = {}, PAST = null;
const F = {
  kind: "본공고+사전규격",
  cat: new Set(JSON.parse(store.get("f.cat", "[]"))),
  part: new Set(),
  ai: new Set(),
  sync: new Set(),
  flag: new Set(),
};
// "확인 필요" 칩 분류(2026-10-01 요청) — 버튼 이름 → text_flags의 kind. 여러 개 고르면 **모두 있는** 공고(좁혀 가며 보기,
// 2026-10-01 제보: 실적+현장설명회를 눌렀는데 실적만 있는 공고가 남아 오류로 보임)
const FLAG_FILTER = { "실적 요건": "실적", "현장설명회": "현장설명회", "기술인력": "인력", "건축사사무소": "건축사사무소" };

// ── 탭 ──
document.querySelectorAll(".tabs button[data-tab]").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));
function showTab(name) {
  document.querySelectorAll(".tabs button[data-tab]").forEach((b) => b.classList.toggle("on", b.dataset.tab === name));
  document.querySelectorAll(".tabsearch input").forEach((i) => { i.hidden = i.dataset.tab !== name; });
  document.querySelectorAll(".tab").forEach((s) => { s.hidden = s.id !== `tab-${name}`; });
  store.set("tab", name);
  if (name === "past") loadPast();
  // 제외·검색 탭은 보일 때만 그린다 — 안 보이는 탭까지 매번 다시 그리면 느려진다
  if (name === "rejected") renderRejected();
  if (name === "search") renderSearch();
}
const tabOn = (name) => !$(`#tab-${name}`).hidden;
// 글자를 칠 때마다 다시 그리지 않고 입력이 멈추면 한 번만(0.2초)
function debounce(fn, ms = 200) {
  let t = null;
  return () => { clearTimeout(t); t = setTimeout(fn, ms); };
}

// ── 필터 칩 ──
function chipRow(label, key, options, multi, disabled = {}) {
  const row = document.createElement("div");
  row.className = "frow";
  row.innerHTML = `<span class="lbl">${esc(label)}</span>`;
  for (const opt of options) {
    const b = document.createElement("button");
    b.className = "chip";
    b.textContent = opt;
    const on = multi ? F[key].has(opt) : F[key] === opt;
    b.classList.toggle("on", on);
    if (disabled[opt]) { b.disabled = true; b.title = disabled[opt]; }
    b.addEventListener("click", () => {
      if (multi) { F[key].has(opt) ? F[key].delete(opt) : F[key].add(opt); }
      else F[key] = opt;
      // "확인 필요"의 "없음"은 다른 버튼과 함께 고를 수 없다(요건이 있는 공고 + 없는 공고는 0건)
      if (key === "flag" && F.flag.has(opt)) {
        if (opt === "없음") F.flag = new Set(["없음"]);
        else F.flag.delete("없음");
      }
      if (key === "cat") store.set("f.cat", JSON.stringify([...F.cat]));
      render();
    });
    row.appendChild(b);
  }
  return row;
}
function renderFilters() {
  const box = $("#filters");
  box.replaceChildren(
    chipRow("공고", "kind", ["본공고+사전규격", "본공고", "사전규격"], false),
    chipRow("낙찰방법", "cat", META ? META.categories : ["협상", "규격가격동시입찰", "입찰"], true),
    chipRow("참가여부", "part", ["미지정", ...(META ? META.statuses : [])], true),
    chipRow("AI 판단", "ai", ["적합", "검토필요", "부적합", "미판정"], true),
    chipRow("싱크로율", "sync", ["높음", "경계선", "낮음", "판정 불가"], true),
    chipRow("확인 필요", "flag", [...Object.keys(FLAG_FILTER), "없음"], true),
  );
}

function stateOf(c) { return STATES[c.key] || {}; }
// 탭 안 검색(2026-10-01 요청) — 공고명·수요기관·공고기관·공고번호에 검색어가 들어 있으면 통과. 띄어쓰기로 나누면 모두 들어 있어야 함
function textHit(c, q) {
  const words = q.toLowerCase().split(/\s+/).filter(Boolean);
  if (!words.length) return true;
  const hay = [c.title, c.demand_institution, c.notice_institution, c.notice_no].map((v) => (v || "").toLowerCase()).join(" ");
  return words.every((w) => hay.includes(w));
}
function passes(c) {
  if (!textHit(c, $("#liveQ").value)) return false;
  if (F.kind !== "본공고+사전규격" && c.kind !== F.kind) return false;
  if (F.cat.size && !F.cat.has(c.category)) return false;
  if (F.part.size && !F.part.has(stateOf(c).status || "미지정")) return false;
  if (F.ai.size && !F.ai.has(c.ai ? c.ai.label : "미판정")) return false;
  if (F.sync.size && !F.sync.has(c.sync.level)) return false;
  if (F.flag.size) {
    const kinds = new Set((c.text_flags || []).map((f) => f.kind));
    const hit = F.flag.has("없음") ? !kinds.size : [...F.flag].every((opt) => kinds.has(FLAG_FILTER[opt]));
    if (!hit) return false;
  }
  return true;
}

// ── 표시 도우미 ──
const won = (v) => v == null ? "금액 정보 없음" : (v >= 1e8 ? `${(v / 1e8).toFixed(v >= 1e9 ? 1 : 2).replace(/\.?0+$/, "")}억원` : `${Math.round(v / 1e4).toLocaleString()}만원`);
const fmtDt = (s) => s ? s.replace("T", " ").slice(0, 16) : "일정 미상";
const pct = (s) => s == null ? "–" : `${Math.round(s * 100)}%`;
// 칩 팝업 — 커서를 대면 브라우저 기본 말풍선 대신 다른 칩과 같은 팝업(2026-10-01 요청)
function chip(label, cls, head, note, headCls = "") {
  const body = `<div class="tip-title${headCls ? " " + headCls : ""}">${esc(head)}</div>` + (note ? `<div class="tip-note">${note}</div>` : "");
  return qualButton(label, cls, `${label} — ${head}`, body);
}
// 입찰 일정 팝업 본문 — 본문 마감 날짜("연-월-일 시:분")에 커서를 대면 뜬다(2026-10-02 요청, 윗줄 "마감 D-N" 칩엔 안 붙임)
function deadlineTipBody(c) {
  // 예전 버전(RESULT_FORMAT 22 미만)으로 수집한 결과엔 일정 목록(deadlines)이 없다 — 팀원 브라우저가 기억한 옛 결과(myRun)를 보고 있으면
  // 팝업이 아예 안 떠서 헷갈렸음(10/2). 카드 마감 한 줄 + "다시 불러오기" 안내라도 띄운다.
  if (!c.deadlines && c.deadline)
    return `<div class="tip-title">입찰 일정</div><dl class="kv"><dt>${esc(c.deadline_label || "마감")} ★</dt><dd>${esc(fmtDt(c.deadline))}</dd></dl>`
      + `<div class="tip-note">이 결과는 예전 버전으로 수집돼 나머지 일정(공고 게시·공동수급협정·입찰·개찰 등)이 없습니다. "나라장터에서 불러오기"를 다시 하면 전부 보입니다.</div>`;
  const list = c.deadlines || [];
  if (!list.length) return "";
  {
    const today = new Date(); today.setHours(0, 0, 0, 0);
    const dn = (iso) => { const d = new Date(iso); d.setHours(0, 0, 0, 0); const n = Math.round((d - today) / 864e5); return n < 0 ? "지남" : n === 0 ? "D-day" : `D-${n}`; };
    const isCard = (label, iso) => label === c.deadline_label && fmtDt(iso) === fmtDt(c.deadline);
    const rows = list.map(([label, iso]) => {
      const tag = label === "공고 게시" || label === "개찰" ? "" : ` <span class="why-inline">${dn(iso)}</span>`;
      return `<dt>${esc(label)}${isCard(label, iso) ? " ★" : ""}</dt><dd${isCard(label, iso) ? "" : ' class="sub"'}>${esc(fmtDt(iso))}${tag}</dd>`;
    }).join("");
    const labels = list.map(([label]) => label);
    const notes = ["★ = 카드 D-N 기준. 입찰참가자격등록 마감이 우선이고, 없으면 입찰 마감 → 첨부 공고문 마감 → 공동수급협정 마감 순입니다."];
    if (labels.includes("공동수급협정 마감")) notes.push("공동수급협정 마감은 공동수급으로 참가할 때만 해당합니다.");
    if (c.deadline_label === "공동수급협정 마감")
      notes.push("나라장터에는 공동수급협정서 제출 마감만 등록돼 있습니다. 단독 참가 기한은 첨부 공고문에서 확인하세요.");
    const body = `<div class="tip-title">입찰 일정</div><dl class="kv">${rows}</dl>`
      + `<div class="tip-note">${notes.map(esc).join("<br>")}</div>`;
    return body;
  }
}
// 본문 마감 날짜 줄 — 날짜 글자에 커서를 대면 "입찰 일정" 팝업
function deadlineCell(c, dd, note) {
  const date = esc(fmtDt(c.deadline));
  const body = deadlineTipBody(c);
  const shown = body ? `<button type="button" class="qual dl-cell" aria-label="입찰 일정 보기">${date}<span class="tip" role="tooltip">${body}</span></button>` : date;
  return `${shown}${dd} ${note}`;
}
function dday(c) {
  if (c.days_left == null) return chip("일정 미상", "", "마감 정보 없음", "나라장터 공고와 첨부파일 어디에서도 마감 일시를 찾지 못했습니다.");
  // 지난 공고는 "마감 마감"이 되지 않게 "마감됨" 한 번만
  const text = c.days_left < 0 ? "마감됨" : c.days_left === 0 ? "마감 D-day" : `마감 D-${c.days_left}`;
  // 카드 D-N은 입찰참가자격등록 마감 우선(없으면 입찰 마감 → 의견등록 → 첨부 등록·제출 → 공동수급협정, 2026-10-02 사용자 결정).
  // 이 칩엔 팝업 없음 — 입찰 일정 팝업은 본문 날짜("연-월-일")에 커서를 댈 때만(2026-10-02 사용자 요청)
  return `<span class="b dday${c.days_left > 7 ? " far" : ""}">${esc(text)}</span>`;
}
const QUAL_NOTES = {
  industry: "자격요건 = 업종·면허(업종코드 4자리). ✓ 보유 · ✗ 미보유 · – 이미 충족해서 없어도 됨.",
  product: "세부품명번호 = 직접생산확인 등 품목 요건(10자리). 첨부 공고문을 읽었을 때만 판정됩니다. ✓ 보유 · ✗ 미보유.",
};
const QUAL_EMPTY = {
  industry: { "제한 없음": "면허제한정보·첨부 참가자격에 업종 요건이 없습니다.", "미확인": "업종 요건을 확인할 정보가 없습니다 (사전규격 등)." },
  product: { "제한 없음": "첨부 공고문에 세부품명번호 요건이 없습니다.", "미확인": "첨부 공고문을 읽지 않아 확인하지 못했습니다 ('첨부 자격판정'을 켜고 불러오기)." },
};
const KIND_TAG = { industry: "업종", product: "품명" };
// 같은 이름이 한 요건 안에 두 번 나오면 한 번만 — 수집 쪽에서 막았어도, 예전에 저장된 결과까지 깨끗하게 보이도록 화면에서도 거른다
const uniqBy = (list, key) => list.filter((x, i) => list.findIndex((y) => key(y) === key(x)) === i);
// 이름 안의 "또는"(공고문이 "A 또는 B"로 적은 대신 인정 업종)은 판정 색(초록·붉은) 대신 일반 글자색으로 — 항목 사이 "또는"과 같은 모양
const labelHtml = (label) => esc(label).replace(/ 또는 /g, ` <span class="or-word">또는</span> `);
// 조합 안 면허를 잇는 조사 — 앞 이름의 마지막 글자 받침 있으면 "과", 없으면 "와"(맨 뒤 "(코드)"는 빼고 봄)
function joinParticle(label) {
  const ch = [...label.replace(/\s*\([^()]*\)\s*$/, "").replace(/[^가-힣]/g, "")].pop();
  if (!ch) return "과(와)";
  return (ch.charCodeAt(0) - 0xac00) % 28 ? "과" : "와";
}
// 필수 보유 요건(하나만 있는 요건)은 한 칸에 모아 보여준다(2026-10-01 요청) — 미보유가 있으면 그 칸이 맨 앞
function reqBlocks(all, mixed) {
  const isMust = (r) => !r.any_of && !r.combos && !r.why;
  const must = all.filter(isMust);
  if (must.length < 2) return all.map((r) => reqHtml(r, mixed)).join("");
  const items = uniqBy(must.map((r) => r.items[0]), (i) => i.label)
    .sort((a, b) => Number(a.held) - Number(b.held));
  const line = (i) => `<div class="tip-sub ${i.held ? "held" : ""}">${i.held ? "✓" : "✗"} ${mixed ? `<span class="kind">${KIND_TAG[i.kind]}</span> ` : ""}${labelHtml(i.label)}${i.via ? ` <small>← ${esc(i.via)}</small>` : ""}</div>`;
  const block = `<div class="tip-item"><span class="why">필수 보유 — ${items.length}개 모두 필요</span>${items.map(line).join("")}</div>`;
  // 필수 칸은 첫 필수 요건 자리에 — 요건 목록이 미달 먼저라, 필수 중 미보유가 있으면 미달 쪽에 온다
  let placed = false;
  return all.map((r) => {
    if (!isMust(r)) return reqHtml(r, mixed);
    if (placed) return "";
    placed = true;
    return block;
  }).join("");
}
function reqHtml(req, mixed) {
  if (req.combos) req = { ...req, combos: req.combos.map((c) => ({ ...c, rows: uniqBy(c.rows, (r) => r.label) })) };
  else if (req.items) req = { ...req, items: uniqBy(req.items, (i) => i.label) };
  // 이미 충족한 "또는" 요건에서 필요 없게 된 항목은 회색 "–" — 붉은색은 실제로 채워야 할 것에만(2026-09-30 요청)
  const done = req.combos ? req.combos.some((c) => c.held) : req.any_of && req.items.some((i) => i.held);
  const mark = (held) => held ? "✓" : done ? "–" : "✗";
  const cls = (held) => held ? "held" : done ? "moot" : "";
  const item = (i) => `<div class="tip-sub ${cls(i.held)}">${mark(i.held)} ${mixed ? `<span class="kind">${KIND_TAG[i.kind]}</span> ` : ""}${labelHtml(i.label)}${i.via ? ` <small>← ${esc(i.via)}</small>` : ""}</div>`;
  if (req.combos) {
    // 나라장터 원문 "[A]과 [B] 업종 또는 [C]과 [D] 업종"처럼 조합(세트)으로 — 조합 하나를 다 갖추면 충족
    // 면허 사이는 "+" 대신 앞 줄 끝에 조사 "과/와"를 조금 띄어서(2026-10-01 요청)
    const row = (r, j, rows) => `<div class="combo-row ${cls(r.held)}">`
      + `${mark(r.held)} ${labelHtml(r.label)}${r.via ? ` <small>← ${esc(r.via)}</small>` : ""}`
      + `${j < rows.length - 1 ? `<span class="or-word join">${joinParticle(r.label)}</span>` : ""}</div>`;
    const circled = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮";
    return `<div class="tip-item"><span class="why">아래 ${req.combos.length}개 조합 중 하나를 모두 갖추면 충족${done ? " — 보유로 충족" : ""}</span>`
      + req.combos.map((c, i) => `<div class="tip-sub combo"><span class="no">${circled[i] || `${i + 1}.`}</span><div>${c.rows.map(row).join("")}</div></div>`).join("") + `</div>`;
  }
  // 미보유는 전부 붉은 ✗로 — 흰 글씨면 보유한 자격으로 착각한다(2026-09-30 제보)
  if (!req.any_of) return `<div class="tip-item"><span class="why">${esc(req.why || "필수 보유")}</span>${item(req.items[0])}</div>`;
  // "미달 1건"이 면허 1개가 없다는 뜻으로 읽히지 않게, 요건 1건 = 아래 N개 중 택1임을 풀어 쓴다
  const n = req.items.length;
  const none = req.items.every((i) => !i.held);
  const why = `아래 ${n}개 중 1개 이상 보유하면 충족${none ? ` — ${n === 2 ? "둘 다" : "모두"} 미보유` : " — 보유로 충족"}`;
  // 항목 사이에 "또는"을 끼워 공고문 "A 또는 B" 모양이 보이게 한다(2026-10-01 요청)
  return `<div class="tip-item"><span class="why">${why}</span>${req.items.map(item).join(`<div class="or-sep">또는</div>`)}</div>`;
}
// 팝업 위치 — 열릴 때 남은 공간을 재서 아래가 모자라면 위로 연다(2026-10-01 제보: 화면 아래쪽 카드의 팝업이 잘림).
// 위아래 모두 모자라면 넓은 쪽으로 열고 그 높이에 맞춰 스크롤한다.
function placeTip(btn) {
  const tip = btn.querySelector(".tip");
  if (!tip) return;
  tip.classList.remove("up", "right");
  tip.style.maxHeight = "";
  const gap = 8, b = btn.getBoundingClientRect(), t = tip.getBoundingClientRect();
  const below = innerHeight - b.bottom - gap - 6, above = b.top - gap - 6;
  if (t.height > below && above > below) {
    tip.classList.add("up");
    if (t.height > above) tip.style.maxHeight = `${Math.floor(above)}px`;
  } else if (t.height > below) tip.style.maxHeight = `${Math.max(120, Math.floor(below))}px`;
  if (t.right > innerWidth - gap) tip.classList.add("right");
}
for (const ev of ["mouseover", "focusin"])
  document.addEventListener(ev, (e) => {
    const btn = e.target.closest && e.target.closest(".qual");
    if (btn && !btn.contains(e.relatedTarget)) requestAnimationFrame(() => placeTip(btn));
  });
function qualButton(label, cls, aria, body) {
  return `<button type="button" class="b qual ${cls}" aria-label="${esc(aria)}">${esc(label)}<span class="tip" role="tooltip">${body}</span></button>`;
}
function qualBadge(q) {
  // 자격요건(업종 4자리) / 세부품명번호(10자리) 배지 두 개. 커서를 대면 미보유·보유 목록 팝업
  const parts = q.parts || [];
  return parts.map((p) => {
    let label = `${p.name} ${p.status}`, cls = "", body = "";
    // 부족 개수("최소 N개")는 여러 뜻으로 읽혀서 표시하지 않는다(2026-09-30 사용자 결정) — 요건 목록의 ✗로 본다
    if (p.status === "미달") cls = "bad";
    else if (p.status === "충족") cls = "good";
    else cls = "muted";  // 제한 없음·미확인은 흐리게 — 눈에 띄어야 할 칩은 문제·확인 필요 칩(2026-10-01 배치 정리)
    // 요건 전체를 한 목록으로 — 미달 요건 먼저, 충족 요건 뒤. 보유 ✓ 초록, 미보유 ✗ 붉은색
    const ok = p.satisfied || [];
    const all = [...p.missing, ...ok];
    if (all.length) {
      const mixed = all.some((r) => new Set((r.items || []).map((i) => i.kind)).size > 1);
      body += `<div class="tip-title">${esc(p.name)} ${all.length}건 — <span class="good">충족 ${ok.length}</span> · <span class="${p.missing.length ? "bad" : ""}">미달 ${p.missing.length}</span></div>`
        + reqBlocks(all, mixed);
    } else if (p.held.length)
      body += `<div class="tip-title good">보유로 충족한 자격 ${p.held.length}건</div>` + p.held.map((n) => `<div class="tip-item">✓ ${esc(n)}</div>`).join("");
    if (!body) body = `<div class="tip-title">${esc(p.name)} ${esc(p.status)}</div><div class="tip-note">${esc(QUAL_EMPTY[p.key][p.status] || "")}</div>`;
    else body += `<div class="tip-note">${esc(QUAL_NOTES[p.key])}</div>`;
    return qualButton(label, cls, `${p.name} ${p.status}`, body);
  }).join("");
}
// "검토 필요" 공고를 첨부 과업 내용으로 가늠한다 — 첨부 원문 기준 싱크로율(지일 과거 실적과의 유사도).
// 표시·정렬에만 쓰고 후보에서 빼거나 올리지는 않는다(기준값 0.65/0.45가 실제 공고로 검증되기 전, 2026-10-01 A안).
function taskFit(c) {
  const s = c.sync || {}, best = (s.top || [])[0];
  const near = best ? ` · 가장 비슷한 실적: (${best.year}) ${best.title}` : "";
  if (s.basis !== "과업 원문")
    return { rank: 0, cls: "", label: "첨부 미확인", tip: "첨부 과업 내용을 읽지 못해 공고명으로만 비교했습니다" };
  const score = s.score == null ? "" : ` ${Math.round(s.score * 100)}%`;
  if (s.level === "높음") return { rank: 3, cls: "good", label: "과업 유사", tip: `첨부 과업 내용 기준 싱크로율${score}${near}` };
  if (s.level === "경계선") return { rank: 2, cls: "info", label: "과업 일부 유사", tip: `첨부 과업 내용 기준 싱크로율${score}${near}` };
  if (s.level === "낮음") return { rank: 1, cls: "bad", label: "과업 무관 가능성", tip: `첨부 과업 내용 기준 싱크로율${score}${near}` };
  return { rank: 0, cls: "", label: "판정 불가", tip: "과거 실적과 비교할 수 없습니다" };
}
// 검토 필요 카드의 "최다 유사" 줄 앞에 붙는 과업 유사 칩 (2026-10-01 요청 — 위쪽 배지 줄에서 옮김)
function fitChip(c) {
  // 팝업엔 공고문의 "사업 범위" 목록만 보여준다(2026-10-01 사용자 요청 — 실적명·태그 발췌는 뺌)
  const f = taskFit(c), scope = c.scope_items || [];
  let body = `<div class="tip-title${f.cls ? " " + f.cls : ""}">${esc(f.label)} — 공고문 사업 범위</div>`;
  if (scope.length) body += scope.map((x) => `<div class="tip-item quote">· ${esc(x)}</div>`).join("");
  else {
    const note = !("scope_items" in c) ? "이 수집 결과엔 사업 범위 목록이 없습니다 — 서버를 다시 켠 뒤 '나라장터에서 불러오기'를 다시 눌러 주세요."
      : !(c.sync && c.sync.basis === "과업 원문") ? "첨부 과업 내용을 읽지 못했습니다."
      : "첨부 공고문에서 사업 범위 목록을 찾지 못했습니다.";
    body += `<div class="tip-note">${note}</div>`;
  }
  return qualButton(f.label, f.cls, f.label, body);
}
function byTaskFit(list) {
  return [...list].sort((a, b) => (taskFit(b).rank - taskFit(a).rank) || ((b.sync?.score ?? -1) - (a.sync?.score ?? -1)));
}
function badges(c) {
  const out = [dday(c)];
  if (c.review_exclude) {
    out.push(chip(`제외 키워드 "${c.review_exclude}" · 관심 "${(c.matched_keywords || []).join(", ")}"`, "warn", "검토 필요",
      `공고명에 제외 키워드 「${esc(c.review_exclude)}」와 관심 키워드 「${esc((c.matched_keywords || []).join(", "))}」가 함께 있어 빼지 않고 남겼습니다. 참가여부를 남겨 주시면 판단 기준을 고치는 데 씁니다.`));
  }
  // 강력추천/참고용 표시는 칩·추천 필터·요약줄·탭 건수 모두 뺐다(2026-10-01 사용자 요청)
  if (c.kind === "사전규격") {
    out.push(chip("사전규격", "prespec", "사전규격", "입찰공고 전 규격 공개 단계 — 의견등록 마감까지 규격 의견을 낼 수 있습니다."));
    if (c.linked_bid_notices && c.linked_bid_notices.length)
      out.push(chip("본공고 게시됨", "good", "본공고 게시됨", `이 사전규격으로 나온 본공고: ${esc(c.linked_bid_notices.join(", "))}`, "good"));
  } else if (c.category) out.push(chip(c.category, "info", `유형: ${c.category}`, c.award_method ? `낙찰 방법: ${esc(c.award_method)}` : ""));
  out.push(chip(c.work_type, "", `업무 구분: ${c.work_type}`, "나라장터가 분류한 업무 종류(용역·물품·공사)입니다."));
  if (c.is_re_notice) out.push(chip("재공고", "warn", "재공고", "유찰 등으로 다시 낸 공고입니다."));
  if (c.ai) {
    const cls = c.ai.label === "적합" ? "good" : c.ai.label === "부적합" ? "bad" : "warn";
    out.push(chip(`AI ${c.ai.label}`, cls, `AI 판단: ${c.ai.label}`, esc(c.ai.reason || ""), cls));
  }
  return out.join("");
}
const SIDO_FULL = { 서울: "서울특별시", 부산: "부산광역시", 대구: "대구광역시", 인천: "인천광역시", 광주: "광주광역시",
  대전: "대전광역시", 울산: "울산광역시", 세종: "세종특별자치시", 경기: "경기도", 강원: "강원특별자치도", 충북: "충청북도",
  충남: "충청남도", 전북: "전북특별자치도", 전남: "전라남도", 경북: "경상북도", 경남: "경상남도", 제주: "제주특별자치도" };
// 원문을 보여주는 팝업 공통 모양(2026-10-02 요청): 핵심 항목 표를 먼저, 원문은 아래 작게(출처와 함께)
function summaryHtml(rows, quote, sourceLabel) {
  const kv = (rows || []).length ? `<dl class="kv">${rows.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("")}</dl>` : "";
  const q = quote ? `<div class="quote-src${kv ? "" : " solo"}"><span class="why">원문${sourceLabel ? ` · ${esc(sourceLabel)}` : ""}</span>${esc(quote)}</div>` : "";
  return kv + q;
}
function regionBadge(c) {
  // 지역 제한 판정(업체 소재지 vs 지일 소재지). 후보에서 빼지는 않고 표시만 한다.
  // 자격 배지처럼 커서를 대면 팝업(2026-09-30 요청) — 판정 근거 문장과 지일 소재지를 보여준다
  const r = c.region_check || { status: "미확인", required: [] };
  const req = (r.required || []).map((s) => SIDO_FULL[s] || s);
  const company = r.company ? SIDO_FULL[r.company] || r.company : "";
  const pop = (label, cls, title, lines, note) => qualButton(label, cls, label,
    `<div class="tip-title ${cls}">${esc(title)}</div>` + lines.filter(Boolean).map((l) => `<div class="tip-item">${l}</div>`).join("")
    + (note ? `<div class="tip-note">${esc(note)}</div>` : ""));
  // 2026-10-02 요청: 팝업엔 참가 가능 지역과 지일 소재지만(요건·기간·개인사업자 칸, 원문, 안내 문구는 뺌).
  // region_check의 summary·evidence는 수집 결과에 그대로 남아 있음 — 다시 보여줄 땐 여기서만 바꾸면 됨
  const kv = [["참가 가능 지역", req.join("·")]];
  if (company) kv.push(["지일 소재지", company]);
  const basis = [summaryHtml(kv, "", "")];
  if (r.status === "미달") return pop(`지역 미달 (${req.join("·")}만)`, "bad", "지역 미달", basis);
  if (r.status === "충족")
    return pop(`지역 충족 (${req.slice(0, 2).join("·")}${req.length > 2 ? " 외" : ""})`, "good", "지역 충족", basis);
  if (c.regions && c.regions.length)
    return pop(`지역제한 ${c.regions.slice(0, 2).join("·")}${c.regions.length > 2 ? " 외" : ""}`, "warn", "나라장터 참가가능지역",
      [esc(c.regions.join(", ")), company ? `<span class="why">지일 소재지</span>${esc(company)}` : ""],
      "시·도 이름을 알아볼 수 없어 충족 여부를 판정하지 못했습니다.");
  return pop("지역제한 정보 없음", "muted", "지역제한 정보 없음", [],
    "나라장터 참가가능지역·첨부 공고문 모두 지역 요건을 찾지 못했습니다 (제한이 없거나 등록되지 않은 것).");
}
// 판정하지 않는 글로 된 요건 — 실적·현장설명회·기술인력. 커서를 대면 팝업으로 원문 문장(현장설명회는 일시도)
const FLAG_LABEL = { "실적": "실적 요건", "현장설명회": "현장설명회 참가 필수", "인력": "기술인력 요건", "건축사사무소": "건축사사무소 요건" };
const FLAG_CHIP = { "현장설명회": "현장설명회" };
function flagBadges(c) {
  return (c.text_flags || []).map((f) => {
    // 칩은 이름만("현장설명회" — 10/2 요청, 날짜는 팝업 일시 칸에). 팝업 제목은 FLAG_LABEL 그대로
    const label = FLAG_CHIP[f.kind] || FLAG_LABEL[f.kind] || f.kind;
    // 요약(f.summary: [[항목, 값]…])이 있으면 표를 먼저, 원문은 아래 작게. 예전 결과(요약 없음)는 일시 + 원문만
    const rows = f.summary && f.summary.length ? f.summary : (f.date ? [["일시", f.date]] : []);
    const body = `<div class="tip-title warn">${esc(FLAG_LABEL[f.kind] || f.kind)} — 확인 필요</div>`
      + `<div class="tip-item flag-text">${summaryHtml(rows, f.text, "")}</div>`
      + `<div class="tip-note">자동 판정하지 않습니다 — 공고문에서 직접 확인하세요.</div>`
      + (f.source ? `<div class="tip-src">출처: ${esc(f.source)}</div>` : "");  // 출처는 맨 아래(10/2 요청 그대로)
    return qualButton(label, "warn", label, body);
  });
}
function jointBadge(c) {
  const allowed = c.joint.allowed;
  const body = `<div class="tip-title ${allowed ? "good" : ""}">공동수급 ${esc(c.joint.label)}</div>`
    + `<div class="tip-note">${allowed
      ? "자격·실적이 모자라면 공동수급(공동이행·분담이행)으로 보완할 수 있습니다. 구성원 수·지역 조건은 공고문을 확인하세요."
      : "나라장터 공동수급 정보 기준입니다. 공고문에 따로 적힌 조건이 있는지 확인하세요."}</div>`;
  return qualButton(`공동수급 ${c.joint.label}`, allowed ? "info" : "", `공동수급 ${c.joint.label}`, body);
}
// 참가 조건 칩을 묶음별로 — 자격 / 지역·공동수급 / 확인 필요. 공고 기본 정보(맨 위 줄)와 섞이지 않게 아래 칸으로 뺐다
// (2026-10-01 요청: "한 공고에 정보가 너무 많다 — 전부 필요하니 배치를 정리해 달라")
function conditions(c) {
  const groups = [["자격", qualBadge(c.qualification)]];
  // 사전규격엔 참가가능지역·공동수급 API 정보가 없다 — 첨부에서 지역 요건을 찾았을 때만 표시
  if (c.kind === "사전규격") {
    if (c.region_check && c.region_check.status !== "미확인") groups.push(["지역", regionBadge(c)]);
  } else groups.push(["지역·공동수급", regionBadge(c) + jointBadge(c)]);
  const flags = flagBadges(c);
  if (flags.length) groups.push(["확인 필요", flags.join("")]);
  return `<div class="conds">${groups.filter(([, h]) => h)
    .map(([label, html]) => `<div class="cg"><span class="cl">${label}</span><span class="cc">${html}</span></div>`).join("")}</div>`;
}

function card(c, withActions = true) {
  // 가로 전체를 쓰는 리스트 한 줄: [배지·공고명·정보 3칸 | 싱크로율] + 아래 참가여부·담당·대화
  const st = stateOf(c);
  const el = document.createElement("article");
  const urgent = c.days_left != null && c.days_left <= 3;
  el.className = "card" + (st.assignee && st.assignee === myName() ? " mine" : "") + (urgent ? " urgent" : "");
  const s = c.sync;
  // 머리글은 "마감" — 무슨 마감인지는 날짜 옆 글자로(카드 기준이 자격등록 마감 우선이라 "입찰 마감"이라 적으면 틀림)
  const note = c.deadline_label && c.deadline_label !== "의견등록 마감" ? `<span class="why">${esc(c.deadline_label)}</span>` : "";
  const dd = c.days_left == null ? "" : ` <span class="dd">${c.days_left < 0 ? "마감" : c.days_left === 0 ? "D-day" : `D-${c.days_left}`}</span>`;
  el.innerHTML = `
    <div class="card-body">
      <div class="card-main">
        <div class="badges">${badges(c)}</div>
        <div class="title">${c.detail_url ? `<a href="${esc(c.detail_url)}" target="_blank" rel="noopener" title="나라장터에서 공고 열기">${esc(c.title)} <span class="ext" aria-hidden="true">↗</span></a>` : esc(c.title)}</div>
        <dl class="meta">
          <div><dt>${c.kind === "사전규격" ? "의견등록 마감" : "마감"}</dt><dd>${deadlineCell(c, dd, note)}</dd></div>
          <div><dt>수요기관</dt><dd>${esc(c.demand_institution || "-")}</dd></div>
          <div><dt>사업금액</dt><dd>${esc(won(c.budget))}</dd></div>
          ${c.excluded_reason ? `<div class="wide"><dt>제외 사유</dt><dd>${esc(c.excluded_reason)}${(c.match_explain || []).length ? `<ul class="explain">${c.match_explain.map((l) => `<li>${esc(l)}</li>`).join("")}</ul>` : ""}</dd></div>` : ""}
        </dl>
        ${conditions(c)}
        ${c.review_exclude ? `<div class="simline">${fitChip(c)}</div>` : ""}
      </div>
      <button type="button" class="card-side lv-${esc(s.level)}" title="눌러서 비슷한 과거 실적 보기">
        <span class="pct">${pct(s.score)}</span>
        <span class="bar"><i class="lv-${esc(s.level)}" style="width:${Math.round((s.score || 0) * 100)}%"></i></span>
        <span class="why">싱크로율 · ${esc(s.level)}</span>
      </button>
    </div>`;
  $(".card-side", el).addEventListener("click", () => openDetail(c));
  if (withActions) {
    const foot = document.createElement("div");
    foot.className = "foot";
    const part = document.createElement("div");
    part.className = "part";
    part.innerHTML = `<span class="lbl">참가여부</span>`;
    for (const status of META.statuses) {
      const b = document.createElement("button");
      b.dataset.s = status;
      b.textContent = status;
      b.classList.toggle("on", st.status === status);
      b.addEventListener("click", () => setState(c.key, st.status === status ? { clear_status: true } : { status }));
      part.appendChild(b);
    }
    const mine = st.assignee && st.assignee === myName();
    const right = document.createElement("div");
    right.className = "who";
    right.innerHTML = `<span>담당 <b>${esc(st.assignee || "없음")}</b>${st.updated_by ? ` <span class="why">· ${esc(st.updated_by)} ${esc(fmtDt(st.updated_at))}</span>` : ""}</span>`;
    const take = document.createElement("button");
    take.textContent = mine ? "담당 해제" : "내가 맡기";
    take.addEventListener("click", () => setState(c.key, mine ? { release: true } : { take: true }));
    const talk = document.createElement("button");
    talk.textContent = `대화 ${COUNTS[c.key] || 0}건`;
    talk.addEventListener("click", () => openThread(c));
    right.append(take, talk);
    foot.append(part, right);
    el.append(foot);
  }
  return el;
}

function grid(list, withActions) {
  const g = document.createElement("div");
  g.className = "grid";
  list.forEach((c) => g.appendChild(card(c, withActions)));
  return g;
}
function emptyBox(text) {
  const d = document.createElement("div");
  d.className = "empty";
  d.textContent = text;
  return d;
}

// ── 렌더 ──
// 마감된 공고(오늘 기준 D-N이 지남)는 각 구역 맨 뒤로 — 나머지 순서는 그대로(2026-10-01 요청)
const isClosed = (c) => c.days_left != null && c.days_left < 0;
const closed = (list) => list.filter(isClosed).length;
const openFirst = (list) => [...list.filter((c) => !isClosed(c)), ...list.filter(isClosed)];
function render() {
  if (!META) return;
  renderFilters();
  const all = RUN ? RUN.candidates : [];
  const shown = all.filter(passes);
  const lv = (l) => shown.filter((c) => c.sync.level === l).length;
  // 예전 버전 코드로 수집한 결과면 판정·팝업이 최신 규칙과 다를 수 있다 — 다시 불러오라고 알린다
  const stale = RUN && META && META.result_format && (RUN.format || 0) < Math.max(META.result_format, APP_FORMAT);
  // 화면 파일은 git pull만으로 바로 새 버전이 되지만 서버(수집·판정 코드)는 다시 켜야 바뀐다 — 서버가 예전 코드면 알린다
  const oldServer = META && (META.result_format || 0) < APP_FORMAT;
  $("#summary").innerHTML = RUN
    ? (oldServer ? `<span class="job err">⚠ 서버가 예전 코드로 실행 중입니다 — Git Bash에서 Ctrl+C 후 'bash webapp/start.sh'로 다시 켜고 '나라장터에서 불러오기'를 다시 눌러 주세요.</span><br>`
      : stale ? `<span class="job err">⚠ 이 결과는 이전 버전으로 수집됐습니다 — 자격 판정·표시가 최신 규칙과 다를 수 있으니 '나라장터에서 불러오기'를 다시 눌러 주세요.</span><br>` : "")
      + `후보 <b>${all.length}</b>건${closed(all) ? ` <span class="why">(마감 ${closed(all)}건)</span>` : ""} 중 <b>${shown.length}</b>건 표시 · 싱크로율 높음 <b>${lv("높음")}</b> / 경계선 ${lv("경계선")} / 낮음 ${lv("낮음")}`
      + ` · 조회 ${esc(fmtDt(RUN.stats.period_begin))} ~ ${esc(fmtDt(RUN.stats.period_end))} · 수집 ${esc(fmtDt(RUN.finished_at))}`
      + (RUN.params.attachments ? "" : " · <span title='첨부 참가자격 미반영'>첨부 자격판정 안 함</span>")
      + (RUN.stats.license_error ? ` · <span class="job err">면허제한정보 조회 실패</span>` : "")
      + (RUN.stats.prespec_requested ? ` · 사전규격 ${RUN.stats.prespec_fetched}건 수집` : " · 사전규격 미수집")
      + (RUN.stats.prespec_error ? ` · <span class="job err" title="${esc(RUN.stats.prespec_error)}">사전규격 조회 실패 (공공데이터포털 활용신청 확인)</span>` : "")
    : "아직 수집 결과가 없습니다. 기간을 고르고 '나라장터에서 불러오기'를 누르세요.";
  const live = $("#liveList");
  if (!RUN) live.replaceChildren();
  else if (!shown.length) live.replaceChildren(emptyBox("조건에 맞는 공고가 없습니다."));
  else {
    const parts = [];
    // "검토 필요"(관심·제외 키워드 공존)는 본공고·사전규격과 섞지 않고 맨 아래 따로 모은다
    const sections = [
      ["본공고", openFirst(shown.filter((c) => c.kind === "본공고" && !c.review_exclude)), ""],
      ["사전규격", openFirst(shown.filter((c) => c.kind === "사전규격" && !c.review_exclude)), ""],
      ["검토 필요", openFirst(byTaskFit(shown.filter((c) => c.review_exclude))),
       "관심 키워드와 제외 키워드가 공고명에 함께 있는 공고 — 첨부 과업 내용이 지일 실적과 비슷한 순서. 참가여부를 남겨 주시면 판단 기준을 고치는 데 씁니다"],
    ];
    for (const [kind, list, note] of sections) {
      if (!list.length) continue;
      if (parts.length) {
        const hr = document.createElement("hr");
        hr.className = "section-divider";
        parts.push(hr);
      }
      const h = document.createElement("h2");
      h.className = "section" + (note ? " review" : "");
      h.innerHTML = note
        ? `${kind} ${list.length}건<small>${esc(note)}</small>`
        : `${kind} ${list.length}건`;
      parts.push(h, grid(list, true));
    }
    live.replaceChildren(...parts);
  }

  const rej = RUN ? RUN.rejected : [];
  const reasons = countBy(rej, rejReason);
  $("#rejSummary").textContent = RUN
    ? `수집 범위(수의계약 제외) ${RUN.stats.in_scope}건 중 필터에서 빠진 ${rej.length}건 — ` + Object.entries(reasons).map(([k, v]) => `${k} ${v}`).join(" · ")
    : "수집 결과가 없습니다.";
  if (tabOn("rejected")) renderRejected();
  if (tabOn("search")) renderSearch();
}

// ── 제외 공고 분류 (제외 사유 · 공고 · 낙찰방법 · 업무구분) ──
// 공고(본공고/사전규격)·낙찰방법은 실제 공고 탭과 같은 구분(2026-10-01 요청). 사전규격엔 낙찰방법이 없어 그 줄에선 안 셈
const REJ_KEYS = [
  { key: "reason", label: "제외 사유", get: (c) => rejReason(c) },
  { key: "kind", label: "공고", get: (c) => c.kind === "사전규격" ? "사전규격" : "본공고" },
  { key: "category", label: "낙찰방법", get: (c) => c.kind === "사전규격" ? null : c.category || "미상" },
  { key: "work", label: "업무", get: (c) => c.work_type || "미상" },
];
const REJ_FILTER = { reason: "", kind: "", category: "", work: "" };
function rejReason(c) { return (c.excluded_reason || "기타").split(" (")[0]; }
function countBy(list, fn) {
  const out = {};
  list.forEach((c) => { const k = fn(c); if (k != null) out[k] = (out[k] || 0) + 1; });
  return Object.fromEntries(Object.entries(out).sort((a, b) => b[1] - a[1]));
}
// 제외 공고는 수천 건일 수 있어 한 번에 50건씩만 그린다(2026-10-01 제보: 4천 건이면 요소 30만 개, 다시 그릴 때마다 1초 넘게 멈춤)
const REJ_PAGE = 50;
let rejLimit = REJ_PAGE;
function renderRejected() {
  const q = $("#rejQ").value;
  const rej = (RUN ? RUN.rejected : []).filter((c) => textHit(c, q));
  const matches = (c, skip) => REJ_KEYS.every((k) => k.key === skip || !REJ_FILTER[k.key] || k.get(c) === REJ_FILTER[k.key]);
  // 각 줄의 건수는 "다른 줄에서 고른 조건" 안에서 센다 — 고르면 몇 건이 남는지 바로 보이게
  $("#rejFilters").innerHTML = rej.length ? REJ_KEYS.map((k) => {
    const counts = countBy(rej.filter((c) => matches(c, k.key)), k.get);
    const chip = (val, text, n) => `<button type="button" class="rej-chip${REJ_FILTER[k.key] === val ? " on" : ""}" data-k="${k.key}" data-v="${esc(val)}">${esc(text)} <b>${n}</b></button>`;
    const total = Object.values(counts).reduce((a, b) => a + b, 0);
    return `<div class="rej-row"><span class="rej-key">${k.label}</span>${chip("", "전체", total)}${Object.entries(counts).map(([v, n]) => chip(v, v, n)).join("")}</div>`;
  }).join("") : "";
  const shown = rej.filter((c) => matches(c));
  if (!shown.length) {
    $("#rejList").replaceChildren(emptyBox(rej.length ? "고른 분류에 해당하는 공고가 없습니다." : q.trim() ? "검색어와 일치하는 제외 공고가 없습니다." : "제외된 공고가 없습니다."));
    return;
  }
  const parts = [grid(shown.slice(0, rejLimit), false)];
  if (shown.length > rejLimit) {
    const more = document.createElement("button");
    more.className = "more";
    more.textContent = `더 보기 (${rejLimit}건 표시 · ${shown.length - rejLimit}건 남음)`;
    more.addEventListener("click", () => { rejLimit += REJ_PAGE * 2; renderRejected(); });
    parts.push(more);
  }
  $("#rejList").replaceChildren(...parts);
}
$("#rejFilters").addEventListener("click", (e) => {
  const b = e.target.closest(".rej-chip");
  if (!b) return;
  REJ_FILTER[b.dataset.k] = REJ_FILTER[b.dataset.k] === b.dataset.v ? "" : b.dataset.v;
  rejLimit = REJ_PAGE;
  renderRejected();
});

function renderSearch() {
  const q = $("#qText").value.trim().toLowerCase();
  const box = $("#searchList");
  if (!q) { box.replaceChildren(); return; }
  const pool = RUN ? [...RUN.candidates, ...RUN.rejected] : [];
  const hit = pool.filter((c) => [c.title, c.demand_institution, c.notice_institution, c.notice_no].some((v) => (v || "").toLowerCase().includes(q)));
  box.replaceChildren(hit.length ? grid(hit.slice(0, 60), true) : emptyBox("수집된 공고 중 일치하는 공고가 없습니다."));
}
$("#qText").addEventListener("input", debounce(renderSearch));
$("#liveQ").addEventListener("input", debounce(render));
$("#rejQ").addEventListener("input", debounce(() => { rejLimit = REJ_PAGE; renderRejected(); }));

// ── 서버 호출 ──
// ── 보는 수집 결과 — 내가 불러오기 한 결과는 다른 팀원이 나중에 불러오기를 해도 내 화면에서 그대로 유지한다.
// MY_RUN(브라우저에 기억): 내가 마지막으로 불러온 결과 번호. 없으면 가장 최근 결과.
// SHOWN: 지금 화면에 띄운 결과 번호 — 보는 도중엔(진행 확인·새로 조회) 다른 결과로 바뀌지 않는다.
let SHOWN = null;
async function loadResults(quiet = false) {
  const want = SHOWN || store.get("myRun", "");
  // 1분마다 하는 새로 조회(quiet)는 들고 있는 결과 번호를 알려 같으면 본문(수 MB)을 안 받는다 — 참가여부·대화 건수만 갱신
  const params = new URLSearchParams();
  if (want) params.set("run", want);
  if (quiet && RUN) params.set("have", String(RUN.id));
  const qs = params.toString();
  let data = await api(`/api/results${qs ? "?" + qs : ""}`);
  if (data.missing) { store.set("myRun", ""); SHOWN = null; }  // 오래돼 지워진 결과(최근 30회만 보관)면 최신으로
  if (!data.same) { RUN = data.run; rejLimit = REJ_PAGE; }
  STATES = data.states; COUNTS = data.comment_counts;
  if (RUN && !data.missing) SHOWN = String(RUN.id);
  showJob(data.job);
  render();
}
async function setState(key, patch) {
  if (needName()) return;
  try {
    const r = await api("/api/state", { key, name: myName(), ...patch });
    STATES[key] = r.state;
    render();
  } catch (e) { alert(e.message); }
}

let pollTimer = null;
function showJob(job) {
  const st = $("#jobStatus"), lg = $("#jobLog"), btn = $("#btnCollect");
  btn.disabled = job.running;
  st.classList.toggle("err", !!job.error);
  const span = job.params.begin ? `${job.params.begin} ~ ${job.params.end}` : `최근 ${job.params.days}일`;
  // 수집은 서버에서 한 번에 하나 — 다른 팀원이 누른 수집도 모든 화면에 보인다. 내 결과는 그대로라는 걸 같이 알린다
  const mine = job.started_at && job.started_at === store.get("myJob", "");
  if (job.running) st.textContent = `수집 중… (${fmtDt(job.started_at)} 시작, ${span})${mine ? "" : " — 다른 팀원이 시작한 수집입니다. 지금 보는 결과는 바뀌지 않습니다"}`;
  else if (job.error) st.textContent = `실패: ${job.error}`;
  else if (job.finished_at) st.textContent = `완료 ${fmtDt(job.finished_at)}`;
  else st.textContent = "";
  lg.hidden = !(job.running || job.error);
  lg.textContent = job.log.join("\n");
  lg.scrollTop = lg.scrollHeight;
  if (job.running && !pollTimer) pollTimer = setInterval(pollJob, 2000);
  if (!job.running && pollTimer) { clearInterval(pollTimer); pollTimer = null; }
}
async function pollJob() {
  const job = await api("/api/job").catch(() => null);
  if (!job) return;
  showJob(job);
  if (job.running) return;
  // 내가 누른 수집이 끝났을 때만 새 결과로 바꾼다 — 다른 팀원의 수집이 끝나도 내 화면은 그대로
  if (job.run_id && job.started_at && job.started_at === store.get("myJob", "")) {
    store.set("myRun", String(job.run_id));
    store.set("myJob", "");
    SHOWN = String(job.run_id);
    return loadResults();
  }
  // 남이 누른 수집이 끝난 경우 — 내 결과는 그대로이니 본문을 다시 받지 않는다(제외 공고가 많으면 수십 MB)
  loadResults(true);
}
$("#btnCollect").addEventListener("click", async () => {
  // 불러오기는 누가 돌렸는지 남기지 않으므로 이름 없이도 된다 (참가여부·담당·대화만 이름 필요)
  try {
    const custom = $("#period").value === "custom";
    const range = custom ? { begin: $("#rangeBegin").value, end: $("#rangeEnd").value } : { days: Number($("#period").value) };
    if (custom && (!range.begin || !range.end)) return alert("시작일과 종료일을 모두 고르세요");
    const job = await api("/api/collect", {
      ...range, attachments: $("#optAttach").checked, ai: $("#optAi").checked,
      prespec: $("#optPrespec").checked,
    });
    store.set("myJob", job.started_at);
    showJob(job);
  } catch (e) { alert(e.message); }
});
$("#btnReload").addEventListener("click", () => loadResults().catch((e) => alert(e.message)));

// ── 대화 ──
let threadKey = null;
async function openThread(c) {
  threadKey = c.key;
  $("#threadTitle").textContent = c.title;
  $("#thread").showModal();
  await loadThread();
}
async function loadThread(data) {
  const list = data || (await api(`/api/comments?key=${encodeURIComponent(threadKey)}`)).comments;
  COUNTS[threadKey] = list.length;
  const body = $("#threadBody");
  body.replaceChildren(...(list.length ? list.map((m) => {
    const d = document.createElement("div");
    d.className = "msg";
    d.innerHTML = `<span class="who">${esc(m.author)}</span><span class="when">${esc(fmtDt(m.created_at))}</span><div class="txt">${esc(m.body)}</div>`;
    return d;
  }) : [emptyBox("아직 대화가 없습니다.")]));
  body.scrollTop = body.scrollHeight;
}
async function sendThread() {
  const text = $("#threadInput").value.trim();
  if (!text || needName()) return;
  try {
    const r = await api("/api/comments", { key: threadKey, name: myName(), body: text });
    $("#threadInput").value = "";
    await loadThread(r.comments);
  } catch (e) { alert(e.message); }
}
$("#threadSend").addEventListener("click", sendThread);
$("#threadInput").addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) sendThread(); });
$("#thread").addEventListener("close", render);

// ── 싱크로율 근거 ──
function syncDetailHtml(s) {
  const tags = Object.entries(s.tags || {}).map(([f, ts]) => `<div><span class="why">${esc(f)}</span><div class="tags">${ts.map((t) => `<span>${esc(t)}</span>`).join("")}</div></div>`).join("");
  const top = (s.top || []).map((r) => `<li><span class="sc">${pct(r.score)}</span>(${esc(r.year)}) ${esc(r.title)}<div class="tags">${r.shared.map((t) => `<span>${esc(t)}</span>`).join("")}</div></li>`).join("");
  return `<p>싱크로율 <b class="lv-${esc(s.level)}">${pct(s.score)} (${esc(s.level)})</b> — 근거: ${esc(s.basis)}`
    + `${s.basis === "공고명만" ? " <span class='why'>(첨부 원문 없이 공고명만으로 계산해 정확도가 낮습니다)</span>" : ""}</p>`
    + `<h3>비슷한 과거 실적</h3><ul class="toplist">${top || "<li>없음</li>"}</ul>`
    + `<h3>이 공고의 과업 프로필</h3>${tags || "<p class='why'>추출된 항목 없음</p>"}`;
}
function openDetail(c) {
  $("#detailTitle").textContent = c.title;
  const ai = c.ai ? `<p><b>AI 판단: ${esc(c.ai.label)}</b> (${esc(c.ai.overall)})${c.ai.best ? ` · 가장 비슷: ${esc(c.ai.best)}` : ""}<br><span class="why">${esc(c.ai.reason)}</span></p>` : "";
  const q = c.qualification;
  const qual = `<p><b>자격</b>: ${esc(q.summary)}${(q.held || []).length ? `<br><span class="why">보유로 충족: ${esc(q.held.join(", "))}</span>` : ""}</p>`;
  $("#detailBody").innerHTML = qual + ai + syncDetailHtml(c.sync);
  $("#detail").showModal();
}

// ── 직접 계산 ──
$("#btnMatch").addEventListener("click", async () => {
  try {
    const s = await api("/api/match", { title: $("#mTitle").value, text: $("#mText").value });
    $("#matchResult").innerHTML = syncDetailHtml(s);
  } catch (e) { $("#matchResult").textContent = e.message; }
});

// ── 과거사업 목록 ──
async function loadPast() {
  if (!PAST) {
    const d = await api("/api/past");
    PAST = d.projects;
    if (d.error) { $("#pastList").replaceChildren(emptyBox(d.error)); return; }
    const years = [...new Set(PAST.map((p) => p.year))].sort();
    $("#pastYear").append(...years.map((y) => new Option(`${y}년`, y)));
  }
  renderPast();
}
// 2026-10-02 요청: 설명 글이 너무 많아 연도·공고명만 보이고, 누르면 과업·전시내용이 펼쳐지게.
// 연도별로 묶고(최신 연도 먼저), 과업은 쉼표 목록을 한 줄씩(괄호 안 쉼표는 그대로) 나눠 읽기 쉽게 한다.
function splitTopLevel(text) {
  const out = []; let depth = 0, cur = "";
  for (const ch of text || "") {
    if ("([{（".includes(ch)) depth++;
    else if (")]}）".includes(ch)) depth = Math.max(0, depth - 1);
    if (ch === "," && depth === 0) { if (cur.trim()) out.push(cur.trim()); cur = ""; } else cur += ch;
  }
  if (cur.trim()) out.push(cur.trim());
  return out;
}
function markHtml(text, q) {
  const t = esc(text || "");
  if (!q) return t;
  const i = (text || "").toLowerCase().indexOf(q);
  if (i < 0) return t;
  return esc(text.slice(0, i)) + `<mark>${esc(text.slice(i, i + q.length))}</mark>` + esc(text.slice(i + q.length));
}
function pastItem(p, q) {
  const d = document.createElement("details");
  d.className = "pastitem";
  const inBody = q && !(p.title || "").toLowerCase().includes(q);
  const tasks = splitTopLevel(p.overview);
  const tags = Object.values(p.tags || {}).flat();
  d.innerHTML = `<summary><span class="pt">${markHtml(p.title, q)}</span>`
    + (inBody ? `<span class="pasthit">내용에서 일치</span>` : "") + `</summary>`
    + `<div class="pastbody">`
    + (tasks.length ? `<h4>과업</h4><ul>${tasks.map((t) => `<li>${markHtml(t, q)}</li>`).join("")}</ul>` : "")
    + (p.exhibition ? `<h4>전시내용</h4><p>${markHtml(p.exhibition, q)}</p>` : "")
    + (tags.length ? `<details class="pasttags"><summary>분류 태그 ${tags.length}개</summary><div class="tags">${tags.map((t) => `<span>${esc(t)}</span>`).join("")}</div></details>` : "")
    + (!tasks.length && !p.exhibition ? `<p class="muted">등록된 세부 내용이 없습니다.</p>` : "")
    + `</div>`;
  if (inBody) d.open = true;  // 내용에서만 일치하면 어디가 맞았는지 바로 보이게
  return d;
}
function renderPast() {
  if (!PAST) return;
  const q = $("#pastQ").value.trim().toLowerCase(), y = $("#pastYear").value;
  const rows = PAST.filter((p) => (!y || String(p.year) === y) && (!q || [p.title, p.overview, p.exhibition].some((v) => (v || "").toLowerCase().includes(q))));
  $("#pastCount").textContent = `${rows.length}건`;
  $("#pastToggle").textContent = "모두 펼치기";
  if (!rows.length) { $("#pastList").replaceChildren(emptyBox("일치하는 과거 사업이 없습니다.")); return; }
  const byYear = new Map();
  for (const p of rows) { if (!byYear.has(p.year)) byYear.set(p.year, []); byYear.get(p.year).push(p); }
  $("#pastList").replaceChildren(...[...byYear.keys()].sort((a, b) => b - a).map((yr) => {
    const g = document.createElement("section");
    g.className = "pastyear";
    g.innerHTML = `<h3>${esc(yr)}<span>${byYear.get(yr).length}건</span></h3>`;
    const list = document.createElement("div");
    list.className = "pastlist";
    list.append(...byYear.get(yr).map((p) => pastItem(p, q)));
    g.append(list);
    return g;
  }));
}
$("#pastToggle").addEventListener("click", () => {
  const items = [...document.querySelectorAll("#pastList details.pastitem")];  // 안쪽 태그 접기는 건드리지 않음
  const open = items.some((d) => !d.open);
  items.forEach((d) => { d.open = open; });
  $("#pastToggle").textContent = open ? "모두 접기" : "모두 펼치기";
});
$("#pastQ").addEventListener("input", renderPast);
$("#pastYear").addEventListener("change", renderPast);

// ── 시작 ──
(async function init() {
  try {
    META = await api("/api/meta");
  } catch (e) {
    $("#summary").textContent = e.message;
    return;
  }
  const years = Object.entries(META.past_years).sort().map(([y, n]) => `${String(y).slice(2)}년 ${n}`).join(" · ");
  $("#pastInfo").textContent = META.past_error ? META.past_error : `과거 실적 ${META.past_count}건 (${years})`;
  const period = $("#period");
  META.periods.forEach((d) => period.append(new Option(`최근 ${d}일`, d)));
  period.append(new Option("직접 지정", "custom"));
  period.value = store.get("period", "7");
  if (!period.value) period.value = "7";
  // 직접 지정: 시작일·종료일 (오늘까지, 최대 N일). 기본값은 최근 7일
  // 달력 버튼·날짜 칸을 누르면 달력을 연다(showPicker가 없는 브라우저는 칸에 커서만)
  const openPicker = (input) => { try { input.showPicker(); } catch { input.focus(); } };
  document.querySelectorAll(".datebox .cal").forEach((b) => b.addEventListener("click", () => openPicker($("#" + b.dataset.for))));
  document.querySelectorAll(".datebox input").forEach((i) => i.addEventListener("click", () => openPicker(i)));
  const rb = $("#rangeBegin"), re_ = $("#rangeEnd"), ymd = (d) => new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
  const today = ymd(new Date());
  rb.max = re_.max = today;
  // 페이지를 열 때마다 시작일·종료일 모두 오늘로(2026-10-01 요청 — 예전엔 마지막에 고른 날짜를 기억했음)
  rb.value = re_.value = today;
  rb.title = re_.title = `최대 ${META.max_custom_days}일`;
  const syncRange = () => { $("#rangeWrap").hidden = period.value !== "custom"; };
  syncRange();
  period.addEventListener("change", () => { store.set("period", period.value); syncRange(); });
  if (!META.has_ai_key) { $("#optAi").disabled = true; $("#optAiWrap").title = "서버에 ANTHROPIC_API_KEY가 없어 AI 판단을 쓸 수 없습니다"; }
  if (!META.has_service_key) $("#jobStatus").textContent = "서버에 NARA_SERVICE_KEY가 없어 불러오기가 실패합니다";
  showTab(store.get("tab", "live"));
  await loadResults();
  setInterval(() => { if (!pollTimer && !document.hidden && !$("#thread").open) loadResults(true).catch(() => {}); }, 60000);
})();
