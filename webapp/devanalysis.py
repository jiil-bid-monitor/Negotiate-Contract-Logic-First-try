"""개발자 탭 '분석하기'(2026-11-27 사용자) — 외부 AI 없이 서버가 계산한다(판단·투찰 기록을 밖으로 보내지 않음).

- 규칙을 바꾸면 최근 수집 결과에서 무엇이 빠지고 들어오나: rulepreview와 같은 계산(나라장터는 다시 안 부름)
- 투찰 보정 방향: 월요일 성적표와 같은 판단(bidnightly.direction)
어떤 변경을 볼지(조정 후보)는 화면이 판단 기록으로 골라 보낸다 — 판단 기록은 화면이 이미 가지고 있어서.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any

from nego.config import load_config

from .bidnightly import direction
from .rulepreview import Change, apply_change, compare

FIELDS = ("remove_keyword", "keyword_work", "license_work", "add_exclude", "add_hard")
MAX_CHANGES = 8  # 한 번에 보는 변경 수(변경마다 최근 수집 결과 전체를 다시 거름)


def analyze(store, company: str | None, bid_entries: list[dict[str, Any]], changes: list[dict[str, Any]]) -> dict[str, Any]:
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
                         "add": sum(1 for r in rows if r["before"].startswith("빠짐"))})
    return {"runAt": (run or {}).get("finished_at"), "previews": previews, "bid": direction(bid_entries),
            "config": {"keywords": list(cfg.keywords), "keywordWork": cfg.keyword_work_types, "licenseWork": cfg.license_work_types}}
