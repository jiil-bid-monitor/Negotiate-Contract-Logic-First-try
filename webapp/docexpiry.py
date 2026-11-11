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


def same_doc(cert_name: str, kind: str) -> bool:
    """차장님 표 이름(cert_name)과 서류 종류(kind)가 같은 서류인지 — 지일은 지일끼리, (그리다)는 그리다끼리."""
    if cert_name.startswith(GRIDA_TAG) != kind.startswith(GRIDA_TAG):
        return False
    return CERT_ALIASES.get(_norm(cert_name), _norm(cert_name)) in _norm(kind)


def set_cert(path: Path, kind: str, value: date | str) -> bool:
    """인증서갱신.xlsx에서 kind 줄의 만료일 칸을 바꾼다 — /upload(새 날짜)·/remove('사용 안 함') (2026-11-11 사용자 요청).
    날짜는 표와 같은 '27.3.31' 모양. 맞는 줄이 없으면 False. 표가 엑셀에서 열려 있어 못 쓰면 OSError."""
    import openpyxl

    if not path.exists():
        return False
    text = f"{value.year % 100}.{value.month}.{value.day}" if isinstance(value, date) else value
    book = openpyxl.load_workbook(path)
    ws, grida_cols, hit = book.worksheets[0], False, False
    for row in ws.iter_rows(max_col=4):
        a, b, c, d = (list(row) + [None] * 4)[:4]
        if c is not None and str(c.value or "").strip() == GRIDA:
            grida_cols = True
            continue
        name = " ".join(str(a.value or "").split())
        if name and b is not None and _cert_date(b.value):
            tagged = GRIDA_TAG + name if d is not None and str(d.value or "").strip() == GRIDA else name
            if same_doc(tagged, kind):
                b.value, hit = text, True
        if grida_cols and c is not None and c.value and d is not None and _cert_date(d.value):
            if same_doc(GRIDA_TAG + " ".join(str(c.value).split()), kind):
                d.value, hit = text, True
    if hit:
        book.save(path)
    book.close()
    return hit


def read_docs(folder: Path) -> list[tuple[str, date]]:
    """(종류, 유효기간 끝) — 인증서갱신.xlsx 날짜가 우선(2026-11-11 사용자 결정), 그 표에 없는 서류는 서류목록.xlsx 날짜."""
    cert = read_cert(folder / CERT_FILE) if (folder / CERT_FILE).exists() else []
    rest = [(k, d) for k, d in read_list(folder) if not any(same_doc(c, k) for c, _ in cert)]
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
    """7일 안에 만료되는데 아직 안 알린 서류 → (알림 글, 새로 알린 키). 없으면 (None, []).
    만료일 당일에도 날짜가 그대로면 한 번 더 알린다 — 키 '종류|날짜|당일' (2026-11-11 사용자 요청)."""
    def key(k, d):
        return f"{k}|{d}|당일" if d == today else f"{k}|{d}"

    rows = [(k, d) for k, d in docs if 0 <= (d - today).days <= ALERT_DAYS and key(k, d) not in sent]
    if not rows:
        return None, []
    head = "오늘 유효기간이 끝납니다" if all(d == today for _, d in rows) else "7일 안에 유효기간이 끝납니다"
    lines = [f"<b>📄 회사 서류 갱신 알림</b>", head, ""]
    for k, d in rows:
        lines += [f"• <b>{html.escape(k)}</b>", f"   {d} · {'오늘 만료' if d == today else f'D-{(d - today).days}'}"]
    lines += ["", "갱신했으면 → /upload 로 새 서류를 보내 주세요",
              "   (만료일을 물어보고 인증서갱신.xlsx도 고칩니다)",
              "안 쓰는 서류면 → /remove 로 빼 주세요"]
    return "\n".join(lines), [key(k, d) for k, d in rows]


def load_sent(path: Path) -> set[str]:
    try:
        return set(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return set()


def save_sent(path: Path, sent: set[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sorted(sent), ensure_ascii=False), encoding="utf-8")
