"""팀이 같이 보는 상태 — 최근 수집 결과, 참가여부·담당, 대화. SQLite 파일 하나.

같은 공고는 공고번호-차수(`key`)로 묶는다. 재수집해도 참가여부·대화는 그대로 남는다.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

STATUSES = ["검토중", "참가", "보류", "불참"]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    params TEXT NOT NULL,
    payload TEXT
);
CREATE TABLE IF NOT EXISTS notice_state (
    key TEXT PRIMARY KEY,
    status TEXT,
    assignee TEXT,
    updated_by TEXT,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT NOT NULL,
    author TEXT NOT NULL,
    body TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS comments_key ON comments(key);
-- 참가·보류·불참 판단 기록(2026-10-13 요청) — 바꿀 때마다 한 줄씩 쌓는다. 수집 결과는 최근 30회만 남으므로
-- 판단할 때의 공고 정보(snapshot: 공고명·기관·금액·유형·걸린 키워드 등)를 같이 남겨 나중에도 집계할 수 있게 한다
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT NOT NULL,
    status TEXT,
    reasons TEXT NOT NULL,
    memo TEXT,
    decided_by TEXT NOT NULL,
    decided_at TEXT NOT NULL,
    snapshot TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS decisions_key ON decisions(key);
"""

# 판단 사유 칩(화면이 이 목록을 그대로 쓴다) — 집계가 되도록 정해진 칩 + 자유 메모
DECISION_REASONS = {
    "참가": ["주력 분야", "실적 쌓기", "후속 사업 연계", "금액 적정", "자격 충족", "공동수급 가능"],
    "보류": ["공고문 확인 필요", "내부 협의 중", "공동수급 상대 찾는 중", "일정 확인 필요"],
    "불참": ["과업 성격 다름", "운영·유지보수 위주", "자격·면허 부족", "실적 요건 미달", "지역 제한",
             "금액 작음", "마감 촉박", "공동수급 상대 없음", "인력·일정 부족"],
}
# 판단 기록이 이만큼 쌓이면 "키워드 조정 집계"를 연다(사용자 결정 — 기록이 쌓인 뒤에 B 단계)
DECISION_SUMMARY_MIN = 30
_SNAPSHOT_KEYS = ("notice_no", "notice_ord", "title", "kind", "category", "work_type", "budget",
                  "demand_institution", "notice_institution", "matched_keywords", "review_exclude",
                  "excluded_reason", "is_candidate", "posted_at", "sync_level", "qual_summary")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._db.executescript(_SCHEMA)
            # 제외된 공고를 담당자가 실제 공고로 올린 표시(2026-10-13 요청) — 예전 DB 파일엔 칸이 없어 여기서 더한다
            cols = {r[1] for r in self._db.execute("PRAGMA table_info(notice_state)")}
            for col in ("promoted_by", "promoted_at"):
                if col not in cols:
                    self._db.execute(f"ALTER TABLE notice_state ADD COLUMN {col} TEXT")
            self._db.commit()

    def _exec(self, sql: str, args: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._db.execute(sql, args)
            self._db.commit()
            return cur

    def _query(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._db.execute(sql, args).fetchall()

    # ── 수집 결과 ─────────────────────────────────────────────
    # 최근 KEEP_RUNS회분을 남긴다 — 내가 불러온 결과를 다른 팀원이 새로 불러오기 해도 내 화면에서 계속 보도록(2026-10-01 요청).
    KEEP_RUNS = 30

    def save_run(self, started_at: str, params: dict[str, Any], payload: dict[str, Any]) -> int:
        cur = self._exec(
            "INSERT INTO runs(started_at, finished_at, params, payload) VALUES (?,?,?,?)",
            (started_at, _now(), json.dumps(params, ensure_ascii=False), json.dumps(payload, ensure_ascii=False)),
        )
        run_id = int(cur.lastrowid)
        self._exec("DELETE FROM runs WHERE id <= ?", (run_id - self.KEEP_RUNS,))
        return run_id

    def _run_row(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
            "params": json.loads(row["params"]),
            **json.loads(row["payload"]),
        }

    def latest_run(self) -> dict[str, Any] | None:
        rows = self._query("SELECT * FROM runs WHERE payload IS NOT NULL ORDER BY id DESC LIMIT 1")
        return self._run_row(rows[0]) if rows else None

    def run_id_if_exists(self, run_id: int | None = None) -> int | None:
        """결과 내용을 읽지 않고 번호만 확인 — run_id가 없으면 최신 결과 번호."""
        if run_id is None:
            rows = self._query("SELECT id FROM runs WHERE payload IS NOT NULL ORDER BY id DESC LIMIT 1")
        else:
            rows = self._query("SELECT id FROM runs WHERE id=? AND payload IS NOT NULL", (run_id,))
        return rows[0]["id"] if rows else None

    def run(self, run_id: int) -> dict[str, Any] | None:
        rows = self._query("SELECT * FROM runs WHERE id=? AND payload IS NOT NULL", (run_id,))
        return self._run_row(rows[0]) if rows else None

    # ── 참가여부·담당 ─────────────────────────────────────────
    def set_state(self, key: str, by: str, status: str | None = None, assignee: str | None = None,
                  clear_status: bool = False, clear_assignee: bool = False,
                  promote: bool | None = None) -> dict[str, Any]:
        """`promote`: True면 제외된 공고를 실제 공고로 올림(누가·언제 기록), False면 올림 취소, None이면 그대로."""
        if status is not None and status not in STATUSES:
            raise ValueError(f"참가여부는 {', '.join(STATUSES)} 중 하나: {status}")
        current = self.states().get(key, {})
        new_status = None if clear_status else (status if status is not None else current.get("status"))
        new_assignee = None if clear_assignee else (assignee if assignee is not None else current.get("assignee"))
        now = _now()
        # 참가여부·담당을 모두 비우면(재클릭 취소 등) 누가·언제 기록도 지움
        upd_by, upd_at = (by, now) if (new_status or new_assignee) else (None, None)
        if promote is None:
            promoted_by, promoted_at = current.get("promoted_by"), current.get("promoted_at")
        else:
            promoted_by, promoted_at = (by, now) if promote else (None, None)
        self._exec(
            "INSERT INTO notice_state(key, status, assignee, updated_by, updated_at, promoted_by, promoted_at) "
            "VALUES (?,?,?,?,?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET status=excluded.status, assignee=excluded.assignee, "
            "updated_by=excluded.updated_by, updated_at=excluded.updated_at, "
            "promoted_by=excluded.promoted_by, promoted_at=excluded.promoted_at",
            (key, new_status, new_assignee, upd_by, upd_at, promoted_by, promoted_at),
        )
        return self.states()[key]

    def states(self) -> dict[str, dict[str, Any]]:
        return {
            r["key"]: {"status": r["status"], "assignee": r["assignee"],
                       "updated_by": r["updated_by"], "updated_at": r["updated_at"],
                       "promoted_by": r["promoted_by"], "promoted_at": r["promoted_at"]}
            for r in self._query("SELECT * FROM notice_state")
        }

    # ── 판단 기록 ─────────────────────────────────────────────
    def add_decision(self, key: str, by: str, status: str | None, reasons: list[str], memo: str,
                     snapshot: dict[str, Any]) -> dict[str, Any]:
        """참가여부를 정할 때 사유와 함께 한 줄 남긴다. status=None은 "판단 해제"(참가여부를 지움)."""
        if status is not None:
            if status not in DECISION_REASONS:
                raise ValueError(f"사유를 남기는 참가여부는 {', '.join(DECISION_REASONS)} 중 하나: {status}")
            reasons = [r for r in reasons if r in DECISION_REASONS[status]]
            if not reasons and not memo.strip():
                raise ValueError("사유를 하나 이상 고르거나 메모를 적어 주세요")
        snap = {k: snapshot.get(k) for k in _SNAPSHOT_KEYS if k in snapshot}
        cur = self._exec(
            "INSERT INTO decisions(key, status, reasons, memo, decided_by, decided_at, snapshot) VALUES (?,?,?,?,?,?,?)",
            (key, status, json.dumps(reasons, ensure_ascii=False), memo.strip()[:500] or None, by, _now(),
             json.dumps(snap, ensure_ascii=False)),
        )
        return {"id": cur.lastrowid}

    def decisions(self, latest_only: bool = True) -> list[dict[str, Any]]:
        """공고마다 마지막 판단(해제된 공고는 뺌). latest_only=False면 바꾼 이력 전부(내려받기용)."""
        rows = self._query("SELECT * FROM decisions ORDER BY id")
        out = [{
            "id": r["id"], "key": r["key"], "status": r["status"], "reasons": json.loads(r["reasons"] or "[]"),
            "memo": r["memo"], "decided_by": r["decided_by"], "decided_at": r["decided_at"],
            "snapshot": json.loads(r["snapshot"] or "{}"),
        } for r in rows]
        if not latest_only:
            return out
        last: dict[str, dict[str, Any]] = {}
        for d in out:
            last[d["key"]] = d
        return [d for d in last.values() if d["status"]]

    # ── 대화 ─────────────────────────────────────────────────
    def add_comment(self, key: str, author: str, body: str) -> dict[str, Any]:
        cur = self._exec(
            "INSERT INTO comments(key, author, body, created_at) VALUES (?,?,?,?)", (key, author, body, _now())
        )
        return {"id": cur.lastrowid, "key": key, "author": author, "body": body}

    def comments(self, key: str) -> list[dict[str, Any]]:
        return [dict(r) for r in self._query("SELECT * FROM comments WHERE key=? ORDER BY id", (key,))]

    def comment_counts(self) -> dict[str, int]:
        return {r["key"]: r["n"] for r in self._query("SELECT key, COUNT(*) AS n FROM comments GROUP BY key")}
