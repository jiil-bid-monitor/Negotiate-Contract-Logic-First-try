"""회사 서류 유효기간 — 텔레그램 /docs 와 만료 7일 전 알림 (2026-11-09 사용자 요청).

공유 폴더 `회사서류_최신/서류목록.xlsx`(담당자가 정리한 표)의 '유효기간(기준)' 칸에 날짜가 적힌 서류만 본다.
'발급 후 3개월'·'없음' 같은 글은 날짜가 아니라 알리지 않는다. 같은 서류(종류 + 유효기간)는 한 번만 알린다 —
새 서류로 바꿔 날짜가 바뀌면 다시 알린다. 이미 지난 서류는 알리지 않고 /docs에만 보인다(11/05 결정과 같음).
"""

from __future__ import annotations

import html
import json
import os
from datetime import date, datetime
from pathlib import Path

ALERT_DAYS = 7
ALERT_HOUR = 9  # 아침 9시 넘어서 하루 한 번


def docs_dir() -> Path:
    # NAS: 코드 폴더(/work/Negotiate-…) 옆 /work/회사서류_최신
    return Path(os.environ.get("COMPANY_DOCS_DIR") or Path.cwd().parent / "회사서류_최신")


def read_docs(folder: Path) -> list[tuple[str, date]]:
    """(종류, 유효기간 끝) — 날짜가 적힌 줄만. 파일이 없으면 빈 목록."""
    import openpyxl

    path = folder / "서류목록.xlsx"
    if not path.exists():
        return []
    ws = openpyxl.load_workbook(path, read_only=True, data_only=True).worksheets[0]
    rows = ws.iter_rows(values_only=True)
    head = [str(c or "").strip() for c in next(rows, ())]
    if "종류" not in head or "유효기간(기준)" not in head:
        return []
    k, e = head.index("종류"), head.index("유효기간(기준)")
    out = []
    for row in rows:
        kind, end = (row[k] if k < len(row) else None), (row[e] if e < len(row) else None)
        if isinstance(end, datetime):
            end = end.date()
        elif isinstance(end, str):
            try:
                end = date.fromisoformat(end.strip()[:10])
            except ValueError:
                continue
        if kind and isinstance(end, date):
            out.append((str(kind).strip(), end))
    return sorted(out, key=lambda d: d[1])


def docs_reply(docs: list[tuple[str, date]], today: date) -> str:
    if not docs:
        return "서류목록.xlsx에서 유효기간 날짜가 적힌 서류를 찾지 못했습니다."
    expired = [(k, d) for k, d in docs if d < today]
    soon = [(k, d) for k, d in docs if 0 <= (d - today).days <= 30]
    later = [(k, d) for k, d in docs if (d - today).days > 30]
    out = ["<b>회사 서류 유효기간</b> (서류목록.xlsx 기준)"]
    for title, rows, fmt in (("30일 안 만료", soon, lambda d: f"D-{(d - today).days}"),
                             ("유효기간 지남 — 재발급 필요", expired, lambda d: "지남"),
                             ("그 뒤", later, lambda d: "")):
        if rows:
            out.append(f"\n<b>{title} {len(rows)}건</b>")
            out += [f"· {html.escape(k)} — {d}" + (f" ({fmt(d)})" if fmt(d) else "") for k, d in rows]
    return "\n".join(out)


def due_alert(docs: list[tuple[str, date]], today: date, sent: set[str]) -> tuple[str | None, list[str]]:
    """7일 안에 만료되는데 아직 안 알린 서류 → (알림 글, 새로 알린 키). 없으면 (None, [])."""
    rows = [(k, d) for k, d in docs if 0 <= (d - today).days <= ALERT_DAYS and f"{k}|{d}" not in sent]
    if not rows:
        return None, []
    lines = ["<b>회사 서류 갱신 알림</b> — 7일 안에 유효기간이 끝납니다"]
    lines += [f"· {html.escape(k)} — {d} (D-{(d - today).days})" for k, d in rows]
    lines.append("새 서류를 받으면 회사서류_최신 폴더와 서류목록.xlsx를 바꿔 주세요.")
    return "\n".join(lines), [f"{k}|{d}" for k, d in rows]


def load_sent(path: Path) -> set[str]:
    try:
        return set(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return set()


def save_sent(path: Path, sent: set[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sorted(sent), ensure_ascii=False), encoding="utf-8")
