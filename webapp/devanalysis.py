"""개발자 탭 '분석하기'(2026-11-27 사용자) — 외부 AI 없이 서버가 계산한다(판단·투찰 기록을 밖으로 보내지 않음).

- 규칙을 바꾸면 최근 수집 결과에서 무엇이 빠지고 들어오나: rulepreview와 같은 계산(나라장터는 다시 안 부름)
- 투찰 보정 방향: 월요일 성적표와 같은 판단(bidnightly.direction)
- 기준 사정률 폭: 지난 개찰마다 저장된 투찰 업체 금액으로 '기준을 Δ%p 옮겼다면 몇 등이었나'를 다시 셈 —
  앞 절반 개찰로 고른 폭이 뒤 절반에서도 나아질 때만 제안(과적합 방지)
- 제안 기록: 숨긴 제안·적용 요청(data/<회사>/dev_proposals.json)
어떤 규칙 변경을 볼지(조정 후보)는 화면이 판단 기록으로 골라 보낸다 — 판단 기록은 화면이 이미 가지고 있어서.
"""
from __future__ import annotations

import json
import threading
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

from nego.bidrecommend import bid_amount
from nego.config import load_config

from .bidnightly import direction
from .bidresult import simulate
from .rulepreview import Change, apply_change, compare

FIELDS = ("remove_keyword", "keyword_work", "license_work", "add_exclude", "add_hard")
MAX_CHANGES = 12  # 한 번에 보는 변경 수(변경마다 최근 수집 결과 전체를 다시 거름)
OFFSETS = [round(-0.5 + 0.05 * i, 2) for i in range(21)]  # 기준 사정률을 옮겨 볼 폭(%p)
OFFSET_MIN = 30  # 업체 목록이 있는 개찰이 이만큼은 돼야 폭을 제안(앞 절반 15 · 뒤 절반 15)
_LOCK = threading.Lock()


def _score(rows: list[dict[str, Any]], d: float) -> dict[str, int]:
    ranks = [r["ranks"][d] for r in rows]
    return {"wins": sum(1 for k in ranks if k == 1), "top5": sum(1 for k in ranks if k and 1 <= k <= 5),
            "pass": sum(1 for k in ranks if k and k > 0), "n": len(rows)}


def _key(s: dict[str, int], d: float) -> tuple:
    return (s["wins"], s["top5"], s["pass"], -abs(d))  # 1순위 → 5위 안 → 하한선 통과, 같으면 덜 옮긴 쪽


def _best(rows: list[dict[str, Any]]) -> float:
    return max(OFFSETS, key=lambda d: _key(_score(rows, d), d))


def rate_offsets(entries: list[dict[str, Any]], bidders_of) -> dict[str, Any]:
    """웹앱 기준 사정률을 Δ%p 옮겼다면 — 같은 계산식(bid_amount)으로 Δ=0과 비교해 끝자리 처리 차이는 서로 지움."""
    rows, missing = [], 0
    for e in entries:
        res = e.get("result") or {}
        if res.get("status") != "완료":
            continue
        bidders = bidders_of(e["id"])
        need = [e.get("basis"), e.get("lowerRate"), e.get("rate")]
        if bidders is None or any(v is None for v in need):
            missing += 1
            continue
        a = res.get("aValue") if res.get("aValue") is not None else (e.get("aValue") or 0)
        ranks = {}
        for d in OFFSETS:
            amount = bid_amount(float(e["basis"]), float(e["rate"]) + d, float(e["lowerRate"]), float(a or 0))
            ranks[d] = simulate(bidders, e, res, amount).get("rank")
        rows.append({"at": str(e.get("openingAt") or e.get("recordedAt") or ""), "ranks": ranks})
    rows.sort(key=lambda r: r["at"])
    out: dict[str, Any] = {"n": len(rows), "missing": missing, "min": OFFSET_MIN,
                           "grid": [{"d": d, **_score(rows, d)} for d in OFFSETS] if rows else []}
    if not rows:
        return out
    best = _best(rows)
    out["best"] = best
    half = len(rows) // 2
    if half >= 5:
        train, test = rows[:half], rows[half:]
        pick = _best(train)
        base, cand = _score(test, 0.0), _score(test, pick)
        better = (cand["wins"], cand["top5"]) > (base["wins"], base["top5"]) and cand["pass"] >= base["pass"] - 1
        out["holdout"] = {"pick": pick, "trainN": len(train), "testN": len(test), "base": base, "cand": cand, "better": better}
        out["ok"] = len(rows) >= OFFSET_MIN and best != 0 and better and (pick > 0) == (best > 0)
    else:
        out["ok"] = False
    return out


def _proposals_path(store) -> Path:
    return store.path.parent / "dev_proposals.json"


def proposals(store) -> dict[str, Any]:
    with _LOCK:
        try:
            return json.loads(_proposals_path(store).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}


def set_proposal(store, pid: str, action: str, n: int | None = None) -> dict[str, Any]:
    """숨기기(dismiss: 그때 자료 수 n과 함께 — 자료가 더 쌓이면 다시 보임) · 적용 요청(request) · 되돌리기(undo)."""
    pid = str(pid)[:120]
    with _LOCK:
        path = _proposals_path(store)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        if action == "undo":
            data.pop(pid, None)
        elif action in ("dismiss", "request"):
            data[pid] = {"status": "dismissed" if action == "dismiss" else "requested", "n": int(n or 0),
                         "at": datetime.now().isoformat(timespec="seconds")}
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        return data


def analyze(store, company: str | None, bid_entries: list[dict[str, Any]], changes: list[dict[str, Any]], bidders_of=None) -> dict[str, Any]:
    cfg = replace(load_config(company=company).screen, review_conflicts=True)  # 웹앱과 같은 규칙
    run = store.latest_run()
    previews: list[dict[str, Any] | None] = []
    for raw in changes[:MAX_CHANGES]:
        change = Change(**{k: [str(x) for x in raw.get(k) or []][:5] for k in FIELDS if raw.get(k)})
        if change.empty() or not run:
            previews.append(None)
            continue
        rows = compare(run, cfg, apply_change(cfg, change))
        drop = [r["c"] for r in rows if r["after"].startswith("빠짐")]
        previews.append({"change": change.describe(), "drop": [c.get("notice_no") for c in drop],
                         "dropTitles": [c.get("title") or "" for c in drop[:5]],
                         "moved": [r["c"].get("notice_no") for r in rows if r["before"] == "목록" and r["after"] == "검토 필요"],
                         "add": sum(1 for r in rows if r["before"].startswith("빠짐"))})
    return {"runAt": (run or {}).get("finished_at"), "previews": previews, "bid": direction(bid_entries),
            "offset": rate_offsets(bid_entries, bidders_of) if bidders_of else None, "proposals": proposals(store),
            "config": {"keywords": list(cfg.keywords), "keywordWork": cfg.keyword_work_types, "licenseWork": cfg.license_work_types,
                       "excludes": list(cfg.exclude_keywords) + list(cfg.hard_exclude_keywords)}}
