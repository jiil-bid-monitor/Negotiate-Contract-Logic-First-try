"""과거사업 목록에 지금 하는 사업도 더함(11/20 사용자 — '지금 사업도 나중엔 과거 사업').

낙찰 후 서류에서 **나라장터에 계약이 올라온 공고**를 과거사업 목록에 넣는다(`data/past_live.json`, 기존 155건 파일은 안 건드림).
- 진행 중 = 계약 등록 뒤, 준공(완료·납품)예정일 전 / 끝난 사업 = 준공예정일이 지남(사용자: 기준은 준공일).
  준공예정일은 착공·착수서류 기본사항(담당자가 적은 값) → 나라장터 계약정보 순, 둘 다 없으면 진행 중.
- 과업 내용(분야·과업 태그)은 그 폴더 `공고문서`의 제안요청서·과업지시서·공고문으로 만든다 — 싱크로율에 둘 다 씀.
- 정량평가서 실적 서식은 회사 실적목록(준공된 것만)을 그대로 씀 — 여기 목록과 무관.
NAS(낙찰 후 서류가 있는 곳)에서 만들고, 서버 PC는 수집 싱크로율에 쓰도록 NAS에서 받아 온다(`/api/past-live`).
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

LIVE_FILE = "past_live.json"
DOC_SUFFIXES = (".hwp", ".hwpx", ".pdf", ".docx")
log = logging.getLogger(__name__)


def _due(v: dict, folder: Path | None) -> str:
    """준공(완료·납품)예정일 'YYYY-MM-DD' — 착공·착수서류에 담당자가 적은 값 먼저, 없으면 나라장터 계약정보."""
    from .postaward import TEMPLATES, start_values

    if folder is not None and v.get("work") in TEMPLATES:
        _, stage, doc = TEMPLATES[v["work"]]
        xlsx = folder / stage / doc
        if xlsx.exists():
            try:
                due = start_values(xlsx, (v.get("autoFill") or {}).get(f"{stage}/{doc}")).get("due")
            except Exception:  # noqa: BLE001 — 담당자가 열어 둔 파일 등
                due = None
            if isinstance(due, datetime):
                return due.strftime("%Y-%m-%d")
    return str((v.get("contract") or {}).get("due") or "")


def _texts(folder: Path | None) -> tuple[list[str], list[str]]:
    from quantdocs.notice import doc_text

    if folder is None or not (folder / "공고문서").is_dir():
        return [], []
    texts, names = [], []
    for f in sorted((folder / "공고문서").iterdir()):
        if f.suffix.lower() in DOC_SUFFIXES and f.name != "공고정보_요약.pdf":
            try:
                texts.append(doc_text(f)); names.append(f.name)
            except Exception:  # noqa: BLE001 — 한 첨부를 못 읽어도 나머지로
                pass
    return texts, names


def build(postaward, data_dir: Path, tp, today: datetime | None = None) -> list[dict[str, Any]]:
    """계약이 올라온 낙찰 공고 → 과거사업 목록 줄. 같은 문서면 지난번 태그를 그대로 씀(첨부를 매번 다시 읽지 않게)."""
    today = today or datetime.now()
    path = Path(data_dir) / LIVE_FILE
    try:
        old = {r["key"]: r for r in json.loads(path.read_text(encoding="utf-8"))}
    except (OSError, ValueError, KeyError):
        old = {}
    rows = []
    for key, v in postaward._read().items():
        c = v.get("contract") or {}
        if v.get("status") != "낙찰" or not c.get("date"):
            continue
        folder = Path(v["folder"]) if v.get("folder") else None
        folder = folder if folder is not None and folder.is_dir() else None
        docs = sorted(f.name for f in (folder / "공고문서").iterdir()) if folder and (folder / "공고문서").is_dir() else []
        prev = old.get(key)
        title = v.get("title") or key
        if prev and prev.get("docs") == docs and prev.get("title") == title:
            tags, counts, evidence = prev["tags"], prev.get("counts", {}), prev.get("evidence", {})
        else:
            texts, _ = _texts(folder)
            prof = tp.build_profile(texts, title=title)
            tags, counts, evidence = prof.tags, prof.counts, {k: s[:200] for k, s in prof.evidence.items()}
        due = _due(v, folder)
        done = bool(due) and due < today.strftime("%Y-%m-%d")
        # 연도는 기존 목록과 같은 숫자로(연도 묶음이 갈리지 않게)
        rows.append({"key": key, "year": int(c["date"][:4]), "title": title, "status": "끝남" if done else "진행 중",
                     "contractDate": c["date"], "dueDate": due, "institution": v.get("institution", ""),
                     "overview": "", "exhibition": "", "docs": docs, "tags": tags, "counts": counts, "evidence": evidence,
                     "source": "낙찰 후 서류"})
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    return rows


def refresh(app) -> int:
    """NAS: 다시 만들어 과거사업 목록(싱크로율 포함)에 반영. 줄 수."""
    if not getattr(app.past, "available", False):
        return 0
    rows = build(app.postaward, app.store.path.parent, app.past._tp)
    app.past.set_live(rows)
    return len(rows)


def start(app, every: int = 3600) -> None:
    """한 시간마다 — NAS는 직접 만들고, 서버 PC(NAS 주소를 아는 곳)는 NAS에서 받아 온다."""
    def loop():
        import requests

        while True:
            try:
                if app.team_remote:
                    base, token = app.team_remote
                    res = requests.get(base + "/api/past-live", headers={"X-App-Token": token}, timeout=30)
                    if res.ok:
                        app.past.set_live(res.json().get("rows") or [])
                else:
                    refresh(app)
            except Exception:  # noqa: BLE001 — 다음 시간에 다시
                log.exception("과거사업 목록(진행 중 사업) 갱신 실패")
            threading.Event().wait(every)

    threading.Thread(target=loop, daemon=True, name="pastlive").start()
