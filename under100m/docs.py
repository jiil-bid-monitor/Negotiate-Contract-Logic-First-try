"""회사 서류 정리·갱신 알림 (2026-11-05 사용자 요청) — OCR 없이 파일 이름만 본다.

담당자가 스캔 PDF를 `[최신 서류]`에 `종류_발급일.pdf`로 넣으면, 같은 종류의 옛날 파일을 `[이전 서류]`로 옮기고
`_목록.json`·`서류목록.xlsx`를 고친다. 늘 유효해야 하는 서류는 만료 7일 전에 한 번 개발자 방에 알리고,
공고 때 새로 발급하는 서류는 새 공고 알림에 함께 적는다(`issue_lines`). 지우지 않고 덮어쓰지 않는다.
서류에 유효기간이 적힌 종류는 이름에 `_유효2027-06-17`을 함께 적는다. 규칙에 안 맞으면 옮기지 않고 '확인 필요'로 알린다.

    python -m under100m.docs --dir <회사서류 폴더> [--no-notify]
"""

from __future__ import annotations

import argparse
import calendar
import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Callable

log = logging.getLogger("under100m.docs")

LATEST = "[최신 서류]"
OLD = "[이전 서류]"
LIST_JSON = "_목록.json"
LIST_XLSX = "서류목록.xlsx"
STATE = "_알림기록.json"
SETTLE_SECONDS = 120  # 복사 중인 파일을 건드리지 않게

_NAME = re.compile(r"(?P<kind>.+)_(?P<issued>\d{4}-\d{2}-\d{2})(?:_유효(?P<expires>\d{4}-\d{2}-\d{2}))?\.pdf", re.IGNORECASE)
# 공고 때 새로 발급하는 서류 → 공고문에서 찾을 때 쓰는 말(띄어쓰기 무시). 사용자 확인 목록(11/05, 과거 실적 서류에서 모은 것)
PER_NOTICE = {
    "국세 납세증명서": ("국세납세", "납세증명"),
    "지방세 납세증명서": ("지방세납세", "납세증명"),
    "4대보험 완납증명서": ("4대보험완납", "완납증명"),
    "4대보험 가입증명": ("4대보험가입", "가입증명", "가입자명부"),
    "법인등기사항전부증명서": ("법인등기",),
    "사업자등록증명": ("사업자등록증명",),
}
YEARLY = {"재무제표"}  # 해마다 새로 만듦 — 발급 1년 뒤를 유효기간으로 봄
ALERT_DAYS = 7
# 같은 종류가 여럿일 수 있어 옮기지 않음 — 사업마다 있는 실적증명서, 품목이 이름에 없는 직접생산확인증명서
NO_MOVE = {"실적증명서", "직접생산확인증명서"}
HEADERS = ["상태", "종류", "사유", "발급일", "유효기간(기준)", "대표자", "원본 위치"]


@dataclass(frozen=True)
class DocName:
    kind: str
    issued: date
    expires: date | None = None


def parse_name(name: str) -> DocName | None:
    match = _NAME.fullmatch(name)
    if not match:
        return None
    try:
        expires = date.fromisoformat(match["expires"]) if match["expires"] else None
        return DocName(match["kind"].strip(), date.fromisoformat(match["issued"]), expires)
    except ValueError:
        return None


def add_months(day: date, months: int) -> date:
    index = day.year * 12 + day.month - 1 + months
    year, month = index // 12, index % 12 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def expiry_of(record: dict) -> date | None:
    """유효기간 끝 — 공고 때 발급하는 서류는 None. 그 밖엔 서류에 적힌 날짜 > 1년 규칙, 없으면 None(기간 없음)."""
    if record["종류"] in PER_NOTICE:
        return None
    if record.get("유효기간"):
        return date.fromisoformat(record["유효기간"])
    if record["종류"] in YEARLY:
        return add_months(date.fromisoformat(record["발급일"]), 12)
    return None


def days_left_to_alert(record: dict, today: date) -> int | None:
    """만료까지 ALERT_DAYS일 안이면(이미 지났으면 음수) 남은 날, 아니면 None."""
    end = expiry_of(record)
    if end is None or (end - today).days > ALERT_DAYS:
        return None
    return (end - today).days


def status_of(record: dict, today: date) -> tuple[str, str]:
    """서류목록 상태·사유."""
    if record.get("메모"):
        return "재발급 필요", record["메모"]
    if record["종류"] in PER_NOTICE:
        return "공고 때 발급", "공고문이 요구하면 새로 발급"
    end = expiry_of(record)
    if end is None:
        return "그대로 사용", "기간 없음 — 상호·대표자·주소·업종이 바뀌었으면 새로"
    if end < today:
        return "재발급 필요", f"유효기간 {end} 지남"
    return ("곧 만료" if (end - today).days <= ALERT_DAYS else "그대로 사용"), f"유효기간 {end}까지"


def issue_lines(folder: Path, notice_text: str = "") -> list[str]:
    """새 공고 알림에 적을 '공고 때 새로 발급' 서류 — 보유본 날짜, 공고문에 이름이 있으면 표시. 목록을 못 읽으면 빈 목록."""
    records = _load_json(folder / LIST_JSON, [])
    if not records:
        return []
    issued = {r.get("종류"): r.get("발급일") for r in records}
    text = re.sub(r"\s+", "", notice_text or "")
    lines = []
    for kind, words in PER_NOTICE.items():
        have = f"보유본 {issued[kind]}" if issued.get(kind) else "보유본 없음"
        mark = " · 공고문에 있음" if any(w in text for w in words) else ""
        lines.append(f"- {kind} ({have}){mark}")
    return lines


def _unique(path: Path) -> Path:
    if not path.exists():
        return path
    number = 2
    while True:
        other = path.with_name(f"{path.stem}_{number}{path.suffix}")
        if not other.exists():
            return other
        number += 1


def _load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _save_json(path: Path, data) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def write_xlsx(path: Path, records: list[dict], today: date) -> None:
    """첫 시트만 다시 쓴다(구분 설명 등 다른 시트는 그대로)."""
    from openpyxl import Workbook, load_workbook

    book = load_workbook(path) if path.exists() else Workbook()
    sheet = book.worksheets[0]
    if sheet.max_row:
        sheet.delete_rows(1, sheet.max_row)
    sheet.append(HEADERS)
    order = {"재발급 필요": 0, "곧 만료": 1, "공고 때 발급": 2, "그대로 사용": 3}
    rows = []
    for record in records:
        status, reason = status_of(record, today)
        end = expiry_of(record)
        rows.append([status, record["종류"], reason, record["발급일"],
                     str(end) if end else ("공고 때 발급" if record["종류"] in PER_NOTICE else "없음"),
                     record.get("대표자") or "", record.get("원본 위치") or ""])
    for row in sorted(rows, key=lambda r: (order[r[0]], r[1])):
        sheet.append(row)
    tmp = path.with_name(path.stem + "_작성중" + path.suffix)
    book.save(tmp)
    os.replace(tmp, path)


def tidy(folder: Path, known: set[str], settle: float, now: float) -> tuple[list[tuple[DocName, Path]], list[str], list[str]]:
    """옛날 파일을 옮긴다. 돌려주는 값: (최신 폴더에 남은 파일, 옮긴 안내, 확인 필요 안내)."""
    latest, old = folder / LATEST, folder / OLD
    moved: list[str] = []
    issues: list[str] = []
    groups: dict[str, list[tuple[DocName, Path]]] = {}
    for path in sorted(latest.iterdir()):
        if not path.is_file() or path.suffix.lower() != ".pdf" or now - path.stat().st_mtime < settle:
            continue
        doc = parse_name(path.name)
        if doc is None:
            issues.append(f"{path.name}: 이름이 '종류_발급일.pdf' 규칙과 다름 — 목록에 넣지 않음")
            continue
        groups.setdefault(doc.kind, []).append((doc, path))
    kept: list[tuple[DocName, Path]] = []
    for kind, files in groups.items():
        files.sort(key=lambda item: item[0].issued)
        newest = files[-1][0].issued
        known_dates = [doc.issued for doc, path in files if path.name in known]
        if len(files) == 1 or kind in NO_MOVE:
            kept += files
            if kind in NO_MOVE and len(files) > 1 and any(path.name not in known for _, path in files):
                issues.append(f"{kind}: 같은 종류가 {len(files)}개 — 자동으로 옮기지 않음, 담당자 확인")
            continue
        if sum(doc.issued == newest for doc, _ in files) > 1:
            kept += files
            issues.append(f"{kind}: 발급일이 같은 파일이 있음 ({newest}) — 옮기지 않음")
            continue
        if any(path.name not in known and known_dates and doc.issued < max(known_dates) for doc, path in files):
            kept += files
            issues.append(f"{kind}: 새 파일의 발급일이 기존 파일보다 오래됨 — 옮기지 않음")
            continue
        old.mkdir(exist_ok=True)
        for doc, path in files[:-1]:
            target = _unique(old / path.name)
            os.replace(path, target)
            moved.append(f"{path.name} → {OLD}")
        kept.append(files[-1])
    return kept, moved, issues


def run(folder: Path, today: date | None = None, notify: Callable[[str], object] = lambda text: None,
        settle: float = SETTLE_SECONDS, now: float | None = None) -> dict:
    today = today or date.today()
    now = time.time() if now is None else now
    if not (folder / LATEST).is_dir():
        raise FileNotFoundError(f"{folder / LATEST} 폴더가 없습니다")
    list_path = folder / LIST_JSON
    records = {r["파일"]: r for r in _load_json(list_path, [])}
    state = _load_json(folder / STATE, {})
    sent = set(state.get("sent", []))
    kept, moved, issues = tidy(folder, set(records), settle, now)

    latest_names = {path.name for path in (folder / LATEST).iterdir() if path.is_file()}
    updated: dict[str, dict] = {name: r for name, r in records.items() if name in latest_names}
    added: list[str] = []
    for doc, path in kept:
        if path.name in updated:
            continue
        before = next((r for r in records.values() if r.get("종류") == doc.kind), {})
        record = {"종류": doc.kind, "파일": path.name, "발급일": str(doc.issued), "유효기간": str(doc.expires) if doc.expires else "",
                  "대표자": "", "원본 위치": LATEST}
        updated[path.name] = record
        added.append(f"{doc.kind} ({doc.issued})")
        if before.get("유효기간") and not doc.expires and doc.kind not in PER_NOTICE:
            issues.append(f"{doc.kind}: 서류에 유효기간이 있는 종류 — 이름 끝에 _유효YYYY-MM-DD를 붙여 주세요")
    changed = list(updated) != list(records) or bool(moved)
    rows = sorted(updated.values(), key=lambda r: r["종류"])
    if changed:
        _save_json(list_path, rows)

    statuses = {r["파일"]: status_of(r, today) for r in rows}
    if changed or state.get("statuses") != {k: list(v) for k, v in statuses.items()}:
        try:
            write_xlsx(folder / LIST_XLSX, rows, today)
            state["statuses"] = {k: list(v) for k, v in statuses.items()}
        except OSError as err:  # 누가 파일을 열어 둔 경우 — 다음 차례에 다시
            log.warning("서류목록을 쓰지 못함: %s", err)

    due: list[str] = []
    for record in rows:
        left = days_left_to_alert(record, today)
        if left is None or record["파일"] in sent:
            continue
        sent.add(record["파일"])
        label = f"{-left}일 지남" if left < 0 else f"{left}일 남음"
        due.append(f"{record['종류']}: {expiry_of(record)} ({label})")
    new_issues = [i for i in issues if f"확인|{i}" not in sent]
    sent.update(f"확인|{i}" for i in new_issues)
    state["sent"] = sorted(sent)
    _save_json(folder / STATE, state)

    parts = [("갱신 필요", due), ("새 서류", added), ("옮김", moved), ("확인 필요", new_issues)]
    lines = [f"{title} {len(items)}건\n" + "\n".join(f"- {item}" for item in items) for title, items in parts if items]
    if lines:
        notify("[회사 서류]\n" + "\n\n".join(lines))
    return {"due": due, "added": added, "moved": moved, "issues": new_issues}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="회사 서류 정리·갱신 알림")
    parser.add_argument("--dir", default=os.environ.get("COMPANY_DOCS_DIR", ""), help="회사 서류 폴더")
    parser.add_argument("--no-notify", action="store_true", help="알림 없이 실행")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%m-%d %H:%M")
    if not args.dir:
        print("회사 서류 폴더가 없습니다 — --dir 또는 COMPANY_DOCS_DIR", file=sys.stderr)
        return 2

    def notify(text: str) -> None:
        if args.no_notify:
            print(text)
            return
        from nego.telegram import send_dev_alert

        errors = send_dev_alert(text)
        if errors is None:
            log.warning("텔레그램 설정이 없어 알림을 보내지 못함")
        elif errors:
            log.warning("텔레그램 알림 실패: %s", str(errors)[:200])

    result = run(Path(args.dir), datetime.now().date(), notify)
    log.info("끝 — 갱신 필요 %d건, 새 서류 %d건, 옮김 %d건, 확인 필요 %d건", len(result["due"]), len(result["added"]),
             len(result["moved"]), len(result["issues"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
