"""보고 기록 — 텔레그램 매일·주간 보고에 실제로 나간 공고 목록 (2026-10-20, 팀원 레포 10/19 '4단계' 이식).

보고는 GitHub Actions에서 돌고 사내 웹앱은 회사 PC에서 돌아, 보고가 웹앱의 판단 기록(`data/participation.json`)에
직접 쓸 수 없다. 그래서 보고가 이 기록을 `output/report-log/`에 남기면 워크플로가 상태 전용 브랜치 `bot-state`의
`reports/`에 올리고(sent_bids.json과 같은 브랜치), 웹앱이 몇 분마다 받아 판단 기록에 '미판단'으로 합친다
(`webapp/reportsync.py`). 판단 없이 마감이 지나면 '놓침'으로 보인다.

저장소가 공개라서 여기에는 나라장터에 공개된 공고 정보만 싣는다 — 담당자 이름·판단·사유는 절대 넣지 않는다.
판단 기록 CSV(`decisions_csv`)도 여기 둔다 — 웹앱 화면 'CSV 내려받기'(index.html downloadLogCsv)와 같은 열.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

KST = timezone(timedelta(hours=9))
KINDS = ("daily", "weekly")
KIND_LABEL = {"daily": "매일", "weekly": "주간"}
DECISIONS = ("참가", "보류", "불참")
UNDECIDED = ("미판단", "놓침")
# 화면 낙찰방법 분류 (webapp/mview.py METHOD와 같음)
METHOD = {"협상": "협상", "규격가격동시입찰": "규격가격동시", "입찰": "입찰"}


def kst_date(now: datetime) -> str:
    """한국 날짜 YYYY-MM-DD (now에 시간대가 없으면 한국 시각으로 본다 — Actions는 TZ=Asia/Seoul로 돈다)."""
    return (now.astimezone(KST) if now.tzinfo else now).strftime("%Y-%m-%d")


def _iso_minutes(dt: datetime | None) -> str | None:
    return dt.isoformat(timespec="minutes") if dt else None


def reported_notice(candidate: Any, held_names: dict[str, str]) -> dict[str, Any]:
    """Candidate 1건 → 판단 기록 meta와 같은 모양 (index.html decisionMeta). 공개된 공고 정보만."""
    n = candidate.notice
    sr = candidate.screen_result
    earliest = candidate.schedule.earliest
    sync = candidate.sync or {}
    score = sync.get("score")
    return {
        "noticeNo": n.notice_no,
        "title": n.title,
        "meta": {
            "sourceType": n.kind,
            "businessType": n.work_type,
            "institution": n.demand_institution or n.notice_institution or "",
            "budgetAmount": n.budget,
            "confidence": sr.confidence or "참고용",
            "bidMethod": METHOD.get(candidate.category or ""),
            "keywords": list(sr.matched_keywords or []),
            "products": [held_names.get(p, p) for p in (sr.matched_product_codes or [])],
            "score": round(score * 100) if isinstance(score, (int, float)) else None,
            # 기준 마감(통합 결정 4번) — 화면 카드 마감과 같은 값
            "deadline": _iso_minutes(earliest[1]) if earliest else None,
            "detailUrl": n.detail_url,
        },
    }


def build_entry(kind: str, candidates: list[Any], now: datetime, held_names: dict[str, str]) -> dict[str, Any]:
    if kind not in KINDS:
        raise ValueError(f"보고 종류는 daily 또는 weekly: {kind}")
    return {"v": 1, "kind": kind, "date": kst_date(now), "reportedAt": now.astimezone(timezone.utc).isoformat(timespec="seconds"),
            "notices": [reported_notice(c, held_names) for c in candidates]}


def file_name(entry: dict[str, Any]) -> str:
    """보고일-종류.json — 같은 날 같은 보고를 다시 보내면 같은 파일을 덮어쓴다."""
    return f"{entry['date']}-{entry['kind']}.json"


def write(directory: Path, entry: dict[str, Any]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / file_name(entry)
    path.write_text(json.dumps(entry, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def parse(text: str) -> dict[str, Any] | None:
    """bot-state에서 읽은 파일 하나를 검사한다 — 모양이 다르면 None (다른 파일·손상된 파일은 건너뛴다)."""
    try:
        e = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(e, dict) or e.get("v") != 1 or e.get("kind") not in KINDS or not isinstance(e.get("date"), str):
        return None
    notices = [n for n in e.get("notices") or [] if isinstance(n, dict) and str(n.get("noticeNo") or "").strip()
               and isinstance(n.get("title"), str)]
    return {"v": 1, "kind": e["kind"], "date": e["date"], "reportedAt": str(e.get("reportedAt") or ""), "notices": notices}


def merge(records: dict[str, dict[str, Any]], entry: dict[str, Any], now: str) -> bool:
    """보고 기록을 판단 기록에 합친다. 이미 있는 공고는 판단·담당·대화를 건드리지 않고 보고 표시만 더한다.
    같은 보고를 여러 번 합쳐도 결과가 같다. 바뀐 게 있으면 True (그때만 저장한다)."""
    changed = False
    for n in entry["notices"]:
        no = n["noticeNo"]
        ref = {"date": entry["date"], "kind": entry["kind"]}
        meta = dict(n.get("meta") or {})
        p = records.get(no)
        if p is None:
            records[no] = {"noticeNo": no, "title": n["title"], "status": None, "owner": "",
                           "updatedAt": entry.get("reportedAt") or now, "comments": [], "meta": meta, "reports": [ref]}
            changed = True
            continue
        reports = p.get("reports") or []
        if not any(r.get("date") == ref["date"] and r.get("kind") == ref["kind"] for r in reports):
            p["reports"] = sorted([*reports, ref], key=lambda r: r.get("date", ""))
            changed = True
        if not p.get("title") and n["title"]:
            p["title"] = n["title"]
            changed = True
        # 판단할 때 남긴 공고 정보가 있으면 그대로 둔다 — 없을 때만 보고 때 정보로 채운다
        if not p.get("meta"):
            p["meta"] = meta
            changed = True
    return changed


_DEADLINE_RE = re.compile(r"^(\d{4})[-./]?(\d{2})[-./]?(\d{2})(?:[ T]?(\d{2}):?(\d{2}))?")


def _deadline(value: Any) -> datetime | None:
    """"2026-10-24 10:00" · "2026-10-24T10:00" 같은 한국 시각. 시각이 없으면 그날 23:59. 형식을 모르면 None
    (화면 index.html deadlineAt과 같은 셈)."""
    m = _DEADLINE_RE.match(str(value or "").strip())
    if not m:
        return None
    hour = int(m.group(4)) if m.group(4) is not None else 23
    minute = int(m.group(5)) if m.group(5) is not None else 59
    return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), hour, minute, tzinfo=KST)


def log_status(p: dict[str, Any], now: datetime) -> str | None:
    """판단 기록 탭에 보이는 상태. 참가·보류·불참이 아니면, 보고된 공고는 마감 전이면 '미판단', 지났으면 '놓침'.
    보고되지 않았고 판단도 없으면 None (판단 기록 탭에 싣지 않는다). 화면 index.html logStatusOf와 같은 규칙."""
    if p.get("status") in DECISIONS:
        return p["status"]
    if not p.get("reports"):
        return None
    due = _deadline((p.get("meta") or {}).get("deadline"))
    return "놓침" if due is not None and due < now.astimezone(KST) else "미판단"


CSV_HEAD = ["판단일시", "판단", "주된 사유", "사유", "판단자", "담당", "공고번호", "공고명", "구분", "업무", "수요기관",
            "사업금액(원)", "추천등급", "낙찰방법", "관심 키워드", "등록 품목", "싱크로율(%)", "마감", "링크", "보고일", "보고"]


def _kst_datetime(iso: Any) -> str:
    try:
        t = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return ""
    return (t.astimezone(KST) if t.tzinfo else t).strftime("%Y-%m-%d %H:%M")


def _cell(v: Any) -> str:
    s = "" if v is None else str(v)
    return '"' + s.replace('"', '""') + '"' if re.search(r'[",\n\r]', s) else s


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def decisions_csv(records: dict[str, dict[str, Any]], now: datetime) -> str:
    """판단 기록 표 — 구글 드라이브 백업 폴더의 `판단기록.csv` (팀원이 드라이브에서 표로 본다). 판단 기록 탭과 같은 공고:
    참가·보류·불참 + 보고됐지만 판단 전(미판단·놓침). 최근 순 — 판단한 공고는 판단 시각, 판단 전 공고는 마지막 보고일.
    엑셀·구글 시트에서 한글이 깨지지 않게 BOM을 붙인다."""
    shown = [(p, st) for p in records.values() if (st := log_status(p, now))]

    def key(item: tuple[dict[str, Any], str]) -> str:
        p, st = item
        return (p.get("reports") or [{}])[-1].get("date", "") if st in UNDECIDED else str(p.get("decidedAt") or "")

    rows = []
    for p, st in sorted(shown, key=key, reverse=True):
        m = p.get("meta") or {}
        d = st not in UNDECIDED
        reports = p.get("reports") or []
        rows.append([
            _kst_datetime(p.get("decidedAt")) if d and p.get("decidedAt") else "", st,
            ", ".join(p.get("tags") or []) if d else "", (p.get("reason") or "") if d else "",
            (p.get("decidedBy") or "") if d else "", p.get("owner") or "",
            p.get("noticeNo"), p.get("title"), m.get("sourceType"), m.get("businessType"), m.get("institution"),
            m.get("budgetAmount"), m.get("confidence"), m.get("bidMethod"), ", ".join(m.get("keywords") or []),
            ", ".join(m.get("products") or []), m.get("score"), m.get("deadline"), m.get("detailUrl"),
            ", ".join(_unique([r.get("date", "") for r in reports])),
            ", ".join(_unique([KIND_LABEL.get(r.get("kind"), "") for r in reports])),
        ])
    return "﻿" + "\r\n".join(",".join(_cell(v) for v in r) for r in [CSV_HEAD, *rows])
