"""회사 서류 유효기간 — 텔레그램 /docs 와 만료 7일 전 알림 (2026-11-09 사용자 요청).

공유 폴더 `회사서류_최신/인증서갱신.xlsx`(차장님 관리표)의 지일 만료일이 우선(2026-11-11 사용자 결정), 그 표에 없는 서류는
`서류목록.xlsx`의 '유효기간(기준)' 칸. 날짜가 적힌 서류만 본다.
'발급 후 3개월'·'없음' 같은 글은 날짜가 아니라 알리지 않는다. 같은 서류(종류 + 유효기간)는 한 번만 알린다 —
새 서류로 바꿔 날짜가 바뀌면 다시 알린다. 이미 지난 서류는 알리지 않고 /docs에만 보인다(11/05 결정과 같음).
"""

from __future__ import annotations

import html
import json
import os
import re
from datetime import date, datetime
from pathlib import Path

ALERT_DAYS = 7
ALERT_HOUR = 9  # 아침 9시 넘어서 하루 한 번


def docs_dir() -> Path:
    # NAS: 코드 폴더(/work/Negotiate-…) 옆 /work/회사서류_최신
    return Path(os.environ.get("COMPANY_DOCS_DIR") or Path.cwd().parent / "회사서류_최신")


CERT_FILE = "인증서갱신.xlsx"
_YMD = re.compile(r"(\d{2,4})\s*\.\s*(\d{1,2})\s*\.\s*(\d{1,2})")


def _cert_date(value) -> date | None:
    """'27.3.31'·'2027.03.31'·엑셀 날짜 → date. '2월,4월'·4.3(숫자)처럼 날짜가 아니면 None."""
    if isinstance(value, datetime):
        return value.date()
    m = _YMD.fullmatch(str(value or "").strip()) if isinstance(value, str) else None
    if not m:
        return None
    y = int(m.group(1))
    try:
        return date(y + 2000 if y < 100 else y, int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


GRIDA = "그리다"  # 지일 직원 자회사 — 같이 챙김(2026-11-11 사용자)
GRIDA_TAG = "(그리다) "  # 지일 자격과 헷갈리지 않게 이름 앞에(11/11 사용자 — /docs·알림·/upload 메뉴 모두)


def read_cert(path: Path) -> list[tuple[str, date]]:
    """인증서갱신.xlsx — A열 이름·B열 지일 만료일. D열이 '그리다'인 줄과 직접생산 표의 C·D열(그리다)은 '(그리다) 이름'."""
    import openpyxl

    out, grida_cols = [], False
    book = openpyxl.load_workbook(path, read_only=True, data_only=True)
    rows = list(book.worksheets[0].iter_rows(values_only=True))
    book.close()  # read_only는 파일을 열어 둠 — 공유 폴더 파일을 잠그지 않게
    for row in rows:
        row = (list(row) + [None] * 4)[:4]
        name = " ".join(str(row[0] or "").split())
        if str(row[2] or "").strip() == GRIDA:  # 직접생산 머리 줄 '지일 | 만료일 | 그리다 | 만료일' — 아래 C·D열은 그리다
            grida_cols = True
            continue
        end = _cert_date(row[1])
        if name and end:
            out.append((GRIDA_TAG + name if str(row[3] or "").strip() == GRIDA else name, end))
        end = _cert_date(row[3]) if grida_cols else None
        if row[2] and end:
            out.append((GRIDA_TAG + " ".join(str(row[2]).split()), end))
    return sorted(out, key=lambda d: d[1])


# 차장님 표 이름 → 서류목록 이름이 글자로 안 겹치는 것만 (나머지는 표 이름이 서류목록 이름 안에 들어 있으면 같은 서류)
CERT_ALIASES = {"중소기업확인서": "중소기업소상공인확인서"}


def _norm(text: str) -> str:
    return re.sub(r"[\W_]+", "", text or "")


def read_docs(folder: Path) -> list[tuple[str, date]]:
    """(종류, 유효기간 끝) — 인증서갱신.xlsx 날짜가 우선(2026-11-11 사용자 결정), 그 표에 없는 서류는 서류목록.xlsx 날짜."""
    cert = read_cert(folder / CERT_FILE) if (folder / CERT_FILE).exists() else []
    names = [(CERT_ALIASES.get(_norm(k), _norm(k)), k.startswith(GRIDA_TAG)) for k, _ in cert]
    # 지일 줄은 지일 표 이름으로만, (그리다) 줄은 그리다 표 이름으로만 같은 서류를 가림
    rest = [(k, d) for k, d in read_list(folder)
            if not any(g == k.startswith(GRIDA_TAG) and n in _norm(k) for n, g in names)]
    return sorted(cert + rest, key=lambda d: d[1])


def read_list(folder: Path) -> list[tuple[str, date]]:
    """서류목록.xlsx '유효기간(기준)' 칸에 날짜가 적힌 줄. 파일이 없으면 빈 목록."""
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
        return "인증서갱신.xlsx·서류목록.xlsx에서 만료일이 적힌 서류를 찾지 못했습니다."
    expired = [(k, d) for k, d in docs if d < today]
    soon = [(k, d) for k, d in docs if 0 <= (d - today).days <= 30]
    later = [(k, d) for k, d in docs if (d - today).days > 30]
    out = ["<b>회사 서류 유효기간</b> (인증서갱신.xlsx 우선, 없는 서류는 서류목록.xlsx)"]
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
    lines.append("갱신하면 회사서류_최신 폴더의 인증서갱신.xlsx 만료일을 고쳐 주세요.")
    return "\n".join(lines), [f"{k}|{d}" for k, d in rows]


def load_sent(path: Path) -> set[str]:
    try:
        return set(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return set()


def save_sent(path: Path, sent: set[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sorted(sent), ensure_ascii=False), encoding="utf-8")
