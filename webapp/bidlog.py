"""투찰 내역 — 투찰 계산기에서 정한 금액으로 실제 투찰한 공고를 남긴다(2026-11-06 사용자 요청).

투찰 계산기 탭의 '투찰내역' 화면이 이 기록을 보여 준다. 한 줄 = 한 번 투찰(공고번호·투찰금액·기준 사정률·
추천 방식 — 11/06 사용자 요청으로 기록자·메모는 뺌). 같은 공고를 다시 기록하면 줄이 하나 더 생긴다(정정공고·재투찰을 덮어쓰지 않게) — 잘못 넣은 줄은 지운다.

파일은 서버 PC `data/bid_log.json`(저장소에 올리지 않는 폴더 — 지일 투찰 이력은 공개 금지). 판단 기록처럼
`BACKUP_DIR`이 있으면 저장할 때마다 백업 폴더에도 한 부 쓰고, 이 PC에 파일이 없으면 백업에서 되살린다.
나중에 개찰 결과로 실제 순위를 채울 자리(`result`)를 비워 둔다(11/05 대표님 미팅 ② — 투찰 이력으로 경향 보정, 요청 받은 뒤 구현).
"""

from __future__ import annotations

import csv
import io
import json
import re
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .backup import backup_content, restore_if_missing

KST = timezone(timedelta(hours=9))
FILE_NAME = "bid_log.json"
BIDDERS_DIR = "bid_log_bidders"

# 화면이 보내는 값 → 저장할 이름과 종류. 그 밖의 값은 버린다
_TEXT = {"noticeNo": 20, "noticeOrd": 3, "title": 200, "institution": 100, "workType": 10, "method": 80, "openingAt": 25, "closingAt": 25}
_NUMBER = ("basis", "lowerRate", "rangeLo", "rangeHi", "aValue", "bid", "rate", "planned", "netCost")
_NO_RE = re.compile(r"^[A-Z0-9]{8,14}$")

CSV_COLUMNS = [("recordedAt", "기록일시"), ("noticeNo", "공고번호"), ("noticeOrd", "차수"), ("title", "공고명"),
               ("institution", "발주처"), ("workType", "업무"), ("basis", "기초금액"), ("lowerRate", "하한율(%)"),
               ("aValue", "A값"), ("bid", "투찰금액"), ("rate", "기준 사정률(%)"), ("method", "추천 방식")]
# 개찰 결과 칸(CSV 뒤쪽) — result 안의 값
RESULT_COLUMNS = [("status", "개찰 결과"), ("ownRank", "지일 순위"), ("bidderCount", "참가 업체 수"), ("assessmentRate", "실제 사정률(%)"),
                  ("plannedPrice", "예정가격"), ("winnerName", "1순위 업체"), ("winnerAmount", "1순위 투찰금액"), ("winnerRate", "1순위 투찰률(%)"),
                  ("gapToWinner", "1순위와 금액 차이"), ("rateDiff", "기준 사정률 − 실제(%p)")]


def _now() -> str:
    return datetime.now(KST).isoformat(timespec="seconds")


def _num(v: Any) -> float | None:
    if v in (None, ""):
        return None
    try:
        n = float(str(v).replace(",", "").strip())
    except ValueError:
        return None
    return n if n == n and abs(n) < 1e15 else None  # NaN·터무니없는 값은 버림


def clean_entry(data: dict[str, Any]) -> dict[str, Any]:
    """화면이 보낸 한 줄을 저장할 모양으로. 공고번호·투찰금액이 없으면 ValueError."""
    out: dict[str, Any] = {}
    for key, limit in _TEXT.items():
        out[key] = str(data.get(key) or "").strip()[:limit]
    for key in _NUMBER:
        out[key] = _num(data.get(key))
    out["noticeNo"] = re.sub(r"\s", "", out["noticeNo"]).upper()
    if not _NO_RE.match(out["noticeNo"]):
        raise ValueError("공고번호가 없거나 형식이 맞지 않습니다 — 공고를 불러온 뒤 기록하세요")
    if not out["bid"] or out["bid"] <= 0:
        raise ValueError("투찰금액이 없습니다 — 금액을 넣으세요")
    out["bid"] = round(out["bid"])
    return out


class BidLog:
    def __init__(self, folder: Path, backup_dir: Path | None = None):
        folder.mkdir(parents=True, exist_ok=True)
        self.path = folder / FILE_NAME
        self.bidders_dir = folder / BIDDERS_DIR   # 투찰 업체 목록(공고마다 수백~수천 곳 — 화면 목록 응답이 무거워지지 않게 따로)
        self.backup_dir = backup_dir
        self._lock = threading.Lock()
        restore_if_missing(self.path, backup_dir)

    def _read(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        return data if isinstance(data, list) else []

    def _write(self, rows: list[dict[str, Any]]) -> None:
        text = json.dumps(rows, ensure_ascii=False, indent=1)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(self.path)  # 반쯤 쓴 파일이 남지 않게
        backup_content(FILE_NAME, text, self.backup_dir)

    def entries(self) -> list[dict[str, Any]]:
        """최근 기록이 먼저(같은 초에 기록했으면 나중에 넣은 것이 먼저)."""
        with self._lock:
            rows = self._read()
        return [r for _, r in sorted(enumerate(rows), key=lambda x: (x[1].get("recordedAt") or "", x[0]), reverse=True)]

    def add(self, data: dict[str, Any]) -> dict[str, Any]:
        entry = clean_entry(data)
        entry.update(id=uuid.uuid4().hex[:12], recordedAt=_now(), result=None)
        with self._lock:
            rows = self._read()
            rows.append(entry)
            self._write(rows)
        return entry

    def update(self, entry_id: str, data: dict[str, Any]) -> dict[str, Any]:
        """투찰금액만 고친다(잘못 넣은 금액 바로잡기)."""
        with self._lock:
            rows = self._read()
            row = next((r for r in rows if r.get("id") == entry_id), None)
            if row is None:
                raise ValueError("그 기록을 찾지 못했습니다 — 화면을 새로 고치세요")
            if "bid" in data:
                bid = _num(data.get("bid"))
                if not bid or bid <= 0:
                    raise ValueError("투찰금액이 맞지 않습니다")
                row["bid"] = round(bid)
            row["updatedAt"] = _now()
            self._write(rows)
            return row

    def set_result(self, entry_id: str, result: dict[str, Any]) -> None:
        """개찰 결과(`webapp/bidresult.py`)를 그 기록에 저장 — 지운 기록이면 아무것도 안 함."""
        with self._lock:
            rows = self._read()
            row = next((r for r in rows if r.get("id") == entry_id), None)
            if row is None:
                return
            bidders = result.pop("bidders", None)
            if bidders is not None:
                result["bidderListCount"] = len(bidders)
                self._write_bidders(entry_id, bidders)
            row["result"] = result
            self._write(rows)

    def _bidders_path(self, entry_id: str) -> Path:
        return self.bidders_dir / f"{re.sub(r'[^0-9A-Za-z]', '', entry_id)}.json"

    def _write_bidders(self, entry_id: str, bidders: list[dict[str, Any]]) -> None:
        self.bidders_dir.mkdir(parents=True, exist_ok=True)
        path = self._bidders_path(entry_id)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(bidders, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)

    def bidders(self, entry_id: str) -> list[dict[str, Any]] | None:
        """그 기록 공고의 투찰 업체 전부(개찰 순위 순) — 아직 안 받았으면 None."""
        path = self._bidders_path(entry_id)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return data if isinstance(data, list) else None

    def set_times(self, entry_id: str, times: dict[str, str]) -> None:
        """개찰 일시·투찰 마감을 그 기록에 저장(예전 기록 채우기) — 못 찾은 칸도 빈 글자로 남겨 다시 찾지 않음."""
        with self._lock:
            rows = self._read()
            row = next((r for r in rows if r.get("id") == entry_id), None)
            if row is None:
                return
            for key in ("openingAt", "closingAt"):
                if times.get(key) or key not in row:
                    row[key] = str(times.get(key) or "")[:25]
            self._write(rows)

    def delete(self, entry_id: str) -> None:
        with self._lock:
            rows = self._read()
            left = [r for r in rows if r.get("id") != entry_id]
            if len(left) == len(rows):
                raise ValueError("그 기록을 찾지 못했습니다 — 화면을 새로 고치세요")
            self._write(left)
            self._bidders_path(entry_id).unlink(missing_ok=True)

    def table(self) -> tuple[list[str], list[list[Any]]]:
        """CSV·투찰 이력 엑셀(`webapp/bidnightly.py`)에 같이 쓰는 표 — (머리글, 줄들), 최근 것부터."""
        rows = []
        for r in self.entries():
            res = r.get("result") or {}
            own, win = res.get("own") or {}, res.get("winner") or {}
            flat = {**res, "ownRank": own.get("rank") if own else ("참가 기록 없음" if res.get("status") == "완료" else None),
                    "winnerName": win.get("name"), "winnerAmount": win.get("amount"), "winnerRate": win.get("rate")}
            rows.append([r.get(k) for k, _ in CSV_COLUMNS] + [flat.get(k) for k, _ in RESULT_COLUMNS])
        return [label for _, label in CSV_COLUMNS + RESULT_COLUMNS], rows

    def csv(self) -> str:
        buf = io.StringIO()
        w = csv.writer(buf)
        head, rows = self.table()
        w.writerow(head)
        for row in rows:
            w.writerow(["" if v is None else v for v in row])
        return "\ufeff" + buf.getvalue()  # 엑셀이 한글을 깨뜨리지 않게
