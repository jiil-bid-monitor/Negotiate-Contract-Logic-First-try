"""예외 낱말 후보 보고서 → 개발자 방 텔레그램 (키워드 학습 자동화, 2026-10-27 사용자 결정).

주간 수집이 남긴 첨부 원문(output/attachment_text)에서 싱크로율 키워드가 더 긴 낱말 속에 잡힌 곳을 모아
오탐 가능성 순으로 보고서를 만들고(jiil-past-contracts `evaluation/exclusion_suggest.py`), 개발자 방
(TELEGRAM_DEV_CHAT_ID)에 요약 + HTML로 보낸다. 담당자 방엔 안 보낸다(웹앱은 담당자가 보는 곳이라 — 사용자 결정).
규칙은 바꾸지 않는다 — 사람이 보고 승인한 낱말만 jiil 레포 EXCLUDE_WORDS·판단 기록에 넣는다.

    python -m nego.exclusion_report output/attachment_text [--out output/예외낱말_후보.html] [--no-send]

과거 실적 자료(JIIL_REPO)나 텔레그램 설정이 없으면 건너뛰고 0으로 끝낸다(수집 결과에 영향 없음).
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

from .sync import default_jiil_repo

TOP_IN_MESSAGE = 10


def load_docs(folder: Path) -> list[tuple[str, str, str]]:
    """첨부 원문 폴더(`{공고번호}_{차수}_{순번}_{파일명}.txt`)를 공고별로 묶는다. 참가자격 요약 파일은 뺀다."""
    parts: dict[str, list[str]] = defaultdict(list)
    for path in sorted(folder.glob("*.txt")):
        if path.name.endswith("_참가자격.txt"):
            continue
        parts[path.name.split("_", 1)[0]].append(path.read_text(encoding="utf-8", errors="replace"))
    return [(no, "", "\n".join(texts)) for no, texts in parts.items()]


def summary_message(cands: list, mode: str, n_docs: int, title: str) -> str:
    lines = [f"<b>{html.escape(title)}</b>", f"공고 {n_docs}건 · 후보 {len(cands)}개 · 순위 {mode}", ""]
    for i, c in enumerate(cands[:TOP_IN_MESSAGE], 1):
        tag = c.tag.split("/", 1)[1]
        lines.append(f"{i}. {html.escape(c.word)} ← '{html.escape(c.keyword)}' ({html.escape(tag)}) · 공고 {len(c.notices)}")
    lines += ["", "첨부 HTML에서 번호마다 승인(오탐)·기각(제 뜻)을 체크하고 '제출'을 누르면 판단 파일이 저장됩니다 — "
              "그 파일을 Claude에게 주면 예외 낱말에 반영합니다."]
    return "\n".join(lines)


# 보고서 HTML(jiil 레포 render_html — Actions에선 잠긴 묶음이라 고치려면 다시 잠가야 함)에 덧붙이는 체크 칸.
# 표 줄마다 승인·기각 체크, 체크는 브라우저에 남아 다시 열면 이어서 고칠 수 있고, '제출'은 판단 파일(json)을 내려받는다.
# 표시 안 한 번호는 '미정'으로 파일에 안 넣는다(판단 기록은 학습에 쓰여서 — 안 본 것을 기각으로 넣지 않으려고).
_FORM_CSS = """<style>
.rv{white-space:nowrap}.rv label{display:block;cursor:pointer;font-size:12px;margin:2px 0}
tr.ok td{background:#fdecea}tr.no td{background:#f1f3f4}
#rvbar{position:sticky;bottom:0;background:#fff;border-top:2px solid #333;padding:10px 8px;margin-top:12px;
display:flex;flex-wrap:wrap;gap:8px;align-items:center;font-size:14px}
#rvbar button{font:inherit;padding:6px 12px;cursor:pointer;border:1px solid #888;border-radius:4px;background:#f6f6f6}
#rvbar button.main{background:#1b6e43;color:#fff;border-color:#1b6e43}#rvmsg{color:#1b6e43;font-size:13px}
</style>"""

_FORM_JS = """<script>
(function(){
var D=JSON.parse(document.getElementById('rvdata').textContent);
var KEY='exfb:'+D.title+':'+D.items.map(function(x){return x.word}).join('|').slice(0,300);
var S={};try{S=JSON.parse(localStorage.getItem(KEY)||'{}')}catch(e){}
function save(){try{localStorage.setItem(KEY,JSON.stringify(S))}catch(e){}}
var rows=[].slice.call(document.querySelectorAll('table tr'));
rows[0].insertCell(0).outerHTML='<th>판단</th>';
rows.slice(1).forEach(function(tr,i){
  var it=D.items[i];if(!it)return;var td=tr.insertCell(0);td.className='rv';
  td.innerHTML='<label><input type=checkbox data-v=ok> 승인(오탐)</label><label><input type=checkbox data-v=no> 기각(제 뜻)</label>';
  [].forEach.call(td.querySelectorAll('input'),function(b){
    b.onchange=function(){if(b.checked)S[it.n]=b.dataset.v;else delete S[it.n];S._sent=0;save();paint()}});
});
function paint(){
  var a=0,r=0;
  rows.slice(1).forEach(function(tr,i){var it=D.items[i];if(!it)return;var v=S[it.n]||'';
    tr.className=v;[].forEach.call(tr.cells[0].querySelectorAll('input'),function(b){b.checked=b.dataset.v===v});
    if(v==='ok')a++;if(v==='no')r++});
  document.getElementById('rvcount').textContent='승인 '+a+' · 기각 '+r+' · 미정 '+(D.items.length-a-r);
  document.getElementById('rvmsg').textContent=S._sent?('제출함 '+S._sent+' — 체크를 바꾸면 다시 제출하세요'):'';
}
function result(){
  var out={report:D.title,made:D.made,submitted_at:new Date().toISOString(),approved:[],rejected:[]};
  D.items.forEach(function(it){var v=S[it.n];var x={n:it.n,word:it.word,keyword:it.keyword,tag:it.tag};
    if(v==='ok')out.approved.push(x);if(v==='no')out.rejected.push(x)});
  return out;
}
document.getElementById('rvsubmit').onclick=function(){
  var o=result();if(!o.approved.length&&!o.rejected.length){alert('체크한 번호가 없습니다');return}
  var a=document.createElement('a');a.href=URL.createObjectURL(new Blob([JSON.stringify(o,null,1)],{type:'application/json'}));
  a.download='예외낱말_판단_'+D.made+'.json';document.body.appendChild(a);a.click();a.remove();
  var t=new Date();S._sent=(t.getMonth()+1)+'/'+t.getDate()+' '+t.toTimeString().slice(0,5);save();paint();
};
document.getElementById('rvcopy').onclick=function(){
  var o=result();function f(l){return l.map(function(x){return x.n+' '+x.word}).join(', ')||'없음'}
  var s=D.title+'\\n승인(오탐): '+f(o.approved)+'\\n기각(제 뜻): '+f(o.rejected);
  (navigator.clipboard?navigator.clipboard.writeText(s):Promise.reject()).then(function(){alert('복사했습니다')},
    function(){prompt('아래 글을 복사하세요',s)});
};
document.getElementById('rvrest').onclick=function(){
  if(!confirm('표시 안 한 번호를 모두 기각(제 뜻)으로 할까요?'))return;
  D.items.forEach(function(it){if(!S[it.n])S[it.n]='no'});S._sent=0;save();paint();
};
document.getElementById('rvreset').onclick=function(){if(confirm('체크를 모두 지울까요?')){S={};save();paint()}};
paint();
})();
</script>"""


def add_review_form(page: str, cands: list, title: str, made: str) -> str:
    """보고서 HTML에 승인·기각 체크 칸과 제출 막대를 붙인다(표 줄 순서 = 후보 순서)."""
    items = [{"n": i, "word": c.word, "keyword": c.keyword, "tag": c.tag} for i, c in enumerate(cands, 1)]
    data = json.dumps({"title": title, "made": made, "items": items}, ensure_ascii=False).replace("</", "<\\/")
    bar = ('<div id="rvbar"><b id="rvcount"></b>'
           '<button class="main" id="rvsubmit">제출 (판단 파일 저장)</button>'
           '<button id="rvcopy">결과 복사</button><button id="rvrest">나머지 모두 기각</button>'
           '<button id="rvreset">초기화</button><span id="rvmsg"></span></div>')
    return (page + _FORM_CSS + bar
            + f'<script type="application/json" id="rvdata">{data}</script>' + _FORM_JS)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m nego.exclusion_report", description="예외 낱말 후보 보고서")
    ap.add_argument("folder", type=Path, help="첨부 원문 폴더 (output/attachment_text)")
    ap.add_argument("--out", type=Path, default=Path("output/예외낱말_후보.html"))
    ap.add_argument("--title", default="예외 낱말 후보 (주간)")
    ap.add_argument("--limit", type=int, default=150)
    ap.add_argument("--no-send", action="store_true", help="보고서만 만들고 텔레그램엔 안 보냄")
    args = ap.parse_args(argv)

    repo = default_jiil_repo()
    if not (repo / "evaluation" / "exclusion_suggest.py").exists():
        print(f"예외 낱말 후보 도구 없음({repo}) → 건너뜀")
        return 0
    docs = load_docs(args.folder) if args.folder.is_dir() else []
    if not docs:
        print(f"첨부 원문 없음({args.folder}) → 건너뜀")
        return 0
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    from evaluation import exclusion_suggest as es  # jiil 레포

    cands, mode = es.suggest(docs, limit=args.limit)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    page = es.render_html(cands, mode, len(docs), args.title)
    args.out.write_text(add_review_form(page, cands, args.title, date.today().strftime("%Y%m%d")), encoding="utf-8")
    print(f"예외 낱말 후보: 공고 {len(docs)}건 → 후보 {len(cands)}개 ({mode}) → {args.out}")
    if args.no_send or not cands:
        return 0

    from .telegram import TelegramConfig, migration_notes, send_messages

    config = TelegramConfig.from_env("TELEGRAM_DEV_CHAT_ID")
    if config is None:
        print("TELEGRAM_DEV_CHAT_ID 없음 → 보내지 않음")
        return 0
    errors = send_messages(config, [summary_message(cands, mode, len(docs), args.title)], args.out)
    for note in migration_notes(config, "TELEGRAM_DEV_CHAT_ID"):
        print(f"::warning::{note}")
    for err in errors:
        print(f"::warning::예외 낱말 후보 발송 실패 {err}")
    if not errors:
        print("예외 낱말 후보를 개발자 방에 보냈습니다")
    return 0


if __name__ == "__main__":
    sys.exit(main())
