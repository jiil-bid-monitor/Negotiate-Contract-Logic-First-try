// 도우미가 직접 적은 질문을 얼마나 맞히는지 재기 (2026-11-25).
// 쓰는 법: node scripts/help_eval.js [시험세트.json ...] [--rows] [--grida]   (기본: scripts/help_evalset.dev.json, .hold.json, .blind.json)
// 화면 코드(webapp/static/index.html의 HELP-PURE-BEGIN ~ HELP-PURE-END)를 그대로 읽어 돌린다. 화면 코드를 따로 옮겨 적지 않음.
// 시험 항목: {id, tab, q, expect(질문 제목 또는 null), expectTab, altExpect["제목" 또는 "탭::제목"], prev, prevTitle, type}
// 가장 나쁜 결과는 wrong(엉뚱한 답을 바로 냄). 도우미 답·낱말을 고친 뒤엔 wrong이 늘지 않았는지 볼 것.
const fs = require("fs"), path = require("path"), vm = require("vm");
const root = path.join(__dirname, "..");
const html = fs.readFileSync(path.join(root, "webapp/static/index.html"), "utf8");
const a = html.indexOf("// HELP-PURE-BEGIN"), b = html.indexOf("// HELP-PURE-END");
if (a < 0 || b < a) throw new Error("index.html에서 HELP-PURE-BEGIN/END를 찾지 못함");
const box = {};
const GRIDA = process.argv.includes("--grida");  // 그리다 화면의 질문으로 재기
vm.runInNewContext((GRIDA ? 'var CO = "grida";\n' : "") + html.slice(a, b) + "\n;Object.assign(out, { HELP_SETS, HS, helpDecide, helpExact, helpRank, HELP_DOCS, HELP_P });", { out: box });
if (process.env.HELP_P) Object.assign(box.HELP_P, JSON.parse(process.env.HELP_P));  // 문턱값을 바꿔 가며 잴 때
const { HELP_SETS, HS, helpDecide } = box;
const has = (tab, title) => !!HELP_SETS[tab] && HS(tab).qa.some((h) => h[1] === title);

// 화면 성질: 질문 버튼의 글을 그대로 적으면 그 답이 나와야 함
let bad = 0;
for (const tab of Object.keys(HELP_SETS)) for (const h of HS(tab).qa) {
  const d = helpDecide(h[1], tab, null);
  if (!(d.kind === "answer" && d.list[0].h[1] === h[1])) { bad++; console.log(`제목 그대로인데 다른 결과: [${tab}] ${h[1]} → ${d.kind} ${d.list.map((x) => x.h[1]).join(" / ")}`); }
}

const args = process.argv.slice(2), rows = args.includes("--rows");
const files = args.filter((x) => !x.startsWith("--"));
const sets = files.length ? files : ["help_evalset.dev.json", "help_evalset.hold.json", "help_evalset.blind.json"].map((f) => path.join(__dirname, f)).filter(fs.existsSync);
const KEYS = ["correct", "wrong", "choicesHit", "choicesMiss", "falseNone", "correctNone", "shownOutOfScope", "special"];
let wrongTotal = 0;
for (const file of sets) {
  let items = JSON.parse(fs.readFileSync(file, "utf8"));
  if (GRIDA) items = items.filter((x) => HELP_SETS[x.tab] && (x.expect == null || has(x.expectTab || x.tab, x.expect)) && (!x.prevTitle || has(x.tab, x.prevTitle)));  // 없는 탭·질문은 뺌
  const count = Object.fromEntries(KEYS.map((k) => [k, 0])), byType = {}, lines = [];
  for (const it of items) {
    const want = it.expect == null ? [] : [{ tab: it.expectTab || it.tab, title: it.expect },
      ...(it.altExpect || []).map((x) => (x.includes("::") ? { tab: x.split("::")[0], title: x.split("::")[1] } : { tab: it.tab, title: x }))];
    want.filter((w) => !has(w.tab, w.title)).forEach((w) => console.log(`시험 세트의 제목이 지금 도우미에 없음: ${it.id} [${w.tab}] ${w.title}`));
    const d = helpDecide(it.q, it.tab, it.prevTitle ? { prevTitle: it.prevTitle } : null);
    const hit = d.list.some((x) => want.some((w) => w.tab === x.tab && w.title === x.h[1]));
    const single = d.kind === "answer" || d.kind === "othertab", list = d.kind === "choices";
    const o = d.kind === "special" ? "special"
      : !want.length ? (d.kind === "none" ? "correctNone" : single ? "wrong" : "shownOutOfScope")
      : single ? (hit ? "correct" : "wrong") : list ? (hit ? "choicesHit" : "choicesMiss") : "falseNone";
    count[o]++;
    (byType[it.type] ||= {})[o] = (byType[it.type][o] || 0) + 1;
    lines.push(`${o}\t${it.id}\t${it.tab}\t${it.type}\t${it.q}\t→ ${d.kind}(${d.why}) ${d.list.map((x) => (x.tab !== it.tab ? x.tab + "::" : "") + x.h[1]).join(" / ") || "-"}\t| ${it.expect ?? "(없음)"}`);
  }
  const scoped = items.filter((x) => x.expect != null).length, pct = (n, m) => (m ? Math.round((n / m) * 1000) / 10 : 0);
  wrongTotal += count.wrong;
  console.log(`\n${path.basename(file)}: ${items.length}건 (답이 있는 질문 ${scoped})`);
  console.log(`  바로 맞음 ${count.correct} (${pct(count.correct, scoped)}%) · 고르기에 정답 ${count.choicesHit} · 닿음 ${pct(count.correct + count.choicesHit, scoped)}%`);
  console.log(`  엉뚱한 답 ${count.wrong} · 고르기에 정답 없음 ${count.choicesMiss} · 답이 있는데 없다고 함 ${count.falseNone}`);
  console.log(`  답이 없는 질문: 없다고 함 ${count.correctNone} · 후보를 보임 ${count.shownOutOfScope} · 특수(공고·가능성) ${count.special}`);
  console.log("  종류별: " + Object.entries(byType).map(([t, c]) => `${t} ${Object.entries(c).map(([k, v]) => k + " " + v).join(", ")}`).join(" | "));
  if (rows) console.log(lines.sort().join("\n"));
}
process.exit(bad || wrongTotal ? 1 : 0);
