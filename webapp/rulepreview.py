"""수집 조건 영향 미리 보기 — 키워드 규칙을 바꾸면 어떤 공고가 빠지고 들어오는지 (2026-11-16 사용자 요청).

판단 기록으로 수집 조건을 조금씩 보정할 때, 바꾸기 전에 서버에 저장된 최근 수집 결과에 지금 규칙과 바꾼 규칙을 둘 다 적용해 본다.
나라장터에 다시 묻지 않는다. 다시 보는 것은 공고명·세부품명 단계(관심·제외·무조건 제외 키워드, 해외 개최)뿐 —
면허·지역·마감 단계는 수집 때 결과를 그대로 쓴다(새로 들어오는 공고 중 그 단계를 거친 적 없는 것은 '확인 필요').

    py -m webapp.rulepreview --add-exclude 영인 --add-exclude 배부
    py -m webapp.rulepreview --remove-keyword 통신 --run 12
    py -m webapp.rulepreview --keyword-work 통신=공사 --license-work 공사     (11/23 — 관심 키워드·보유 면허 경로를 업무로 좁힘)

결과: 화면에 요약, `output/규칙미리보기_날짜.csv`(BOM — 엑셀에서 바로 열림, 공고 정보와 판단 기록이 들어 있어 공개 저장소에 올리지 말 것).
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from nego import screen as S
from nego.config import load_config
from nego.models import Notice

from .mview import stage_of
from .store import Store

# 공고명·세부품명 단계에서 빠진 공고 — 규칙을 바꾸면 다시 들어올 수 있다
SCREEN_STAGES = {"제외키워드", "키워드·품목", "해외개최"}


@dataclass
class Change:
    add_keyword: list[str] = field(default_factory=list)
    remove_keyword: list[str] = field(default_factory=list)
    add_exclude: list[str] = field(default_factory=list)
    remove_exclude: list[str] = field(default_factory=list)
    add_hard: list[str] = field(default_factory=list)
    remove_hard: list[str] = field(default_factory=list)
    keyword_work: list[str] = field(default_factory=list)  # "통신=공사" — 그 관심 키워드를 그 업무 공고에서만(11/23)
    license_work: list[str] = field(default_factory=list)  # "공사" — 보유 면허 경로를 그 업무 공고에서만(11/23)

    def empty(self) -> bool:
        return not any(vars(self).values())

    def describe(self) -> str:
        names = {"add_keyword": "관심 키워드 추가", "remove_keyword": "관심 키워드 삭제",
                 "add_exclude": "제외 키워드 추가", "remove_exclude": "제외 키워드 삭제",
                 "add_hard": "무조건 제외 추가", "remove_hard": "무조건 제외 삭제",
                 "keyword_work": "관심 키워드 업무 한정", "license_work": "보유 면허 경로 업무 한정"}
        return " · ".join(f"{names[k]} {', '.join(v)}" for k, v in vars(self).items() if v)


def _edit(words: list[str], add: list[str], remove: list[str]) -> list[str]:
    gone = {S._squash(w) for w in remove}
    out = [w for w in words if S._squash(w) not in gone]
    return out + [w for w in add if S._squash(w) not in {S._squash(x) for x in out}]


def _work_map(items: list[str]) -> dict[str, list[str]]:
    """["통신=공사", "공간=공사,물품"] → {"통신": ["공사"], "공간": ["공사", "물품"]}."""
    out: dict[str, list[str]] = {}
    for it in items:
        word, _, works = it.partition("=")
        if word.strip() and works.strip():
            out[word.strip()] = [w.strip() for w in works.split(",") if w.strip()]
    return out


def apply_change(config: S.ScreenConfig, change: Change) -> S.ScreenConfig:
    return replace(config,
                   keywords=_edit(config.keywords, change.add_keyword, change.remove_keyword),
                   exclude_keywords=_edit(config.exclude_keywords, change.add_exclude, change.remove_exclude),
                   hard_exclude_keywords=_edit(config.hard_exclude_keywords, change.add_hard, change.remove_hard),
                   keyword_work_types={**config.keyword_work_types, **_work_map(change.keyword_work)},
                   license_work_types=list(change.license_work) or list(config.license_work_types))


def _license_kept(c: dict[str, Any], cfg: S.ScreenConfig) -> bool:
    """수집 때 보유 면허 경로로 남은 공고가 이 규칙에서도 그 경로로 남나 — 업무 한정(11/23)이 있으면 그 업무만."""
    return bool(c.get("license_match")) and (not cfg.license_work_types or (c.get("work_type") or "") in cfg.license_work_types)


def notice_of(c: dict[str, Any]) -> Notice:
    """저장된 수집 결과 한 줄 → 공고명·세부품명 단계에 필요한 만큼의 Notice."""
    codes = [str(x) for x in c.get("product_codes") or [] if x]
    return Notice(work_type=c.get("work_type") or "", notice_no=c.get("notice_no") or "",
                  notice_ord=c.get("notice_ord") or "", title=c.get("title") or "",
                  product_class_name=c.get("product_name"), product_class_no=codes[0] if codes else None,
                  product_codes=codes, kind=c.get("kind") or "본공고")


def _place(result: S.ScreenResult) -> str:
    """공고명·세부품명 단계 결과 → 어디로 가나(목록 / 검토 필요 / 빠짐 사유)."""
    if result.matched:
        return "검토 필요" if result.confidence == "검토필요" else "목록"
    reason = result.excluded_by or "미매칭"
    return f"빠짐 — {reason}{f' ({result.excluded_reason})' if reason != '미매칭' and result.excluded_reason else ''}"


def compare(payload: dict[str, Any], old_cfg: S.ScreenConfig, new_cfg: S.ScreenConfig) -> list[dict[str, Any]]:
    """이번 변경 때문에 바뀌는 공고만 — 지금 규칙과 바꾼 규칙의 판정이 다른 것(수집 뒤 설정이 바뀌어 생긴 차이는 뺌).
    결과 줄: 공고번호·공고명·지금·바꾼 뒤·확인 필요."""
    rows: list[dict[str, Any]] = []
    for c in payload.get("candidates") or []:
        n = notice_of(c)
        old, new = S.screen(n, old_cfg), S.screen(n, new_cfg)
        if not old.matched and not new.matched and c.get("license_match") and _license_kept(c, old_cfg) \
                and not _license_kept(c, new_cfg) and not c.get("local_match"):
            # 키워드 없이 보유 면허로만 들어온 공고 — 경로를 업무로 좁히면 빠짐(11/23)
            rows.append({"c": c, "before": "목록", "after": f"빠짐 — 보유 면허 경로 업무 한정({c.get('work_type') or '?'})", "note": ""})
            continue
        if _place(old) == _place(new):
            continue
        before = "검토 필요" if c.get("confidence") == "검토필요" else "목록"
        after = _place(new)
        if not new.matched and _license_kept(c, new_cfg) and new.excluded_by in ("미매칭", "제외키워드") \
                and not S._match_exclude(n, new_cfg.hard_exclude_keywords, new_cfg.exclude_exceptions):
            after = "목록(보유 면허로 남음)"
        if not new.matched and c.get("local_match") and new.excluded_by in ("미매칭", "제외키워드") \
                and not S._match_exclude(n, new_cfg.hard_exclude_keywords, new_cfg.exclude_exceptions):
            after = "목록(정선 사업으로 남음)"
        if before == after or after.startswith(("목록(보유", "목록(정선")):
            continue
        rows.append({"c": c, "before": before, "after": after, "note": ""})
    for c in payload.get("rejected") or []:
        stage, _ = stage_of(c.get("excluded_reason"))
        if stage not in SCREEN_STAGES:
            continue  # 면허·지역·마감·본공고 게시로 빠진 공고는 키워드를 바꿔도 그대로 빠진다
        n = notice_of(c)
        new = S.screen(n, new_cfg)
        if not new.matched or S.screen(n, old_cfg).matched:
            continue
        sure = stage == "키워드·품목" and c.get("only_keyword_miss")
        rows.append({"c": c, "before": f"빠짐 — {c.get('excluded_reason') or stage}", "after": _place(new),
                     "note": "" if sure else "면허·지역·마감은 수집 때 다시 확인(이 단계를 거친 적 없음)"})
    return rows


def _participation(data_dir: Path) -> dict[str, dict[str, Any]]:
    try:
        return json.loads((data_dir / "participation.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _judged(p: dict[str, Any] | None) -> str:
    if not p:
        return ""
    if p.get("status"):
        why = ", ".join(p.get("tags") or []) or p.get("reason") or ""
        return f"{p['status']}{f' ({why})' if why else ''}"
    return "보고됨·미판단" if p.get("reports") else ""


HEAD = ["바뀜", "공고번호", "공고명", "구분", "업무", "수요기관", "사업금액(원)", "지금", "바꾼 뒤", "판단 기록", "확인 필요"]


def report(rows: list[dict[str, Any]], part: dict[str, dict[str, Any]]) -> list[list[Any]]:
    out = []
    for r in rows:
        c = r["c"]
        kind = "새로 들어옴" if r["before"].startswith("빠짐") else "새로 빠짐" if r["after"].startswith("빠짐") else "구역 바뀜"
        out.append([kind, c.get("notice_no"), c.get("title"), c.get("kind"), c.get("work_type"),
                    c.get("demand_institution") or c.get("notice_institution") or "", c.get("budget"),
                    r["before"], r["after"], _judged(part.get(c.get("notice_no") or "")), r["note"]])
    order = {"새로 빠짐": 0, "구역 바뀜": 1, "새로 들어옴": 2}
    return sorted(out, key=lambda x: (order[x[0]], x[1] or ""))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="py -m webapp.rulepreview", description="키워드 규칙을 바꾸면 최근 수집 결과에서 무엇이 달라지나")
    for flag in ("add-keyword", "remove-keyword", "add-exclude", "remove-exclude", "add-hard", "remove-hard"):
        ap.add_argument(f"--{flag}", action="append", default=[], metavar="낱말")
    ap.add_argument("--keyword-work", action="append", default=[], metavar="낱말=업무",
                    help="그 관심 키워드를 그 업무 공고에서만 셈 (예: 통신=공사, 여러 업무는 쉼표)")
    ap.add_argument("--license-work", action="append", default=[], metavar="업무",
                    help="보유 면허 경로를 그 업무 공고에서만 (예: 공사)")
    ap.add_argument("--db", default=os.environ.get("WEBAPP_DB", "data/webapp.sqlite3"))
    ap.add_argument("--run", type=int, help="수집 결과 번호 (없으면 최신)")
    ap.add_argument("--out", default=os.environ.get("OUTPUT_DIR", "output"))
    a = ap.parse_args(argv)
    change = Change(a.add_keyword, a.remove_keyword, a.add_exclude, a.remove_exclude, a.add_hard, a.remove_hard,
                    a.keyword_work, a.license_work)
    if change.empty():
        ap.error("바꿀 조건을 하나 이상 주세요 (예: --add-exclude 영인)")
    db = Path(a.db)
    if not db.exists():
        print(f"수집 결과 파일이 없습니다: {db}", file=sys.stderr)
        return 1
    store = Store(db)
    run = store.run(a.run) if a.run else store.latest_run()
    if not run:
        print("저장된 수집 결과가 없습니다 — 웹앱에서 '나라장터에서 불러오기'를 먼저 하세요.", file=sys.stderr)
        return 1
    old_cfg = replace(load_config().screen, review_conflicts=True)  # 웹앱과 같은 규칙(검토 필요 구역 켬)
    new_cfg = apply_change(old_cfg, change)
    rows = report(compare(run, old_cfg, new_cfg), _participation(db.parent))

    payload = run
    print(f"바꿀 조건: {change.describe()}")
    print(f"수집 결과 #{run.get('id')} ({run.get('started_at')}) — 후보 {len(payload.get('candidates') or [])}건 · 제외 {len(payload.get('rejected') or [])}건")
    counts = {k: sum(1 for r in rows if r[0] == k) for k in ("새로 빠짐", "구역 바뀜", "새로 들어옴")}
    joined = sum(1 for r in rows if r[0] == "새로 빠짐" and str(r[9]).startswith("참가"))
    print(" · ".join(f"{k} {v}건" for k, v in counts.items()) + (f"  ⚠ 새로 빠지는 공고 중 참가로 판단한 공고 {joined}건" if joined else ""))
    for r in rows[:40]:
        print(f"  [{r[0]}] {r[1]} {r[2]} — {r[7]} → {r[8]}{f' · 판단 {r[9]}' if r[9] else ''}{f' · {r[10]}' if r[10] else ''}")
    if len(rows) > 40:
        print(f"  … 외 {len(rows) - 40}건 (CSV에 전부)")
    out_dir = Path(a.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"규칙미리보기_{datetime.now():%Y%m%d_%H%M}.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow([f"바꿀 조건: {change.describe()}"])
        w.writerow(HEAD)
        w.writerows(rows)
    print(f"CSV: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
