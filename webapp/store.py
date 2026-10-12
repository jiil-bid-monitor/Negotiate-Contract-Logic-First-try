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
"""


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
                  clear_status: bool = False, clear_assignee: bool = False) -> dict[str, Any]:
        if status is not None and status not in STATUSES:
            raise ValueError(f"참가여부는 {', '.join(STATUSES)} 중 하나: {status}")
        current = self.states().get(key, {})
        new_status = None if clear_status else (status if status is not None else current.get("status"))
        new_assignee = None if clear_assignee else (assignee if assignee is not None else current.get("assignee"))
        self._exec(
            "INSERT INTO notice_state(key, status, assignee, updated_by, updated_at) VALUES (?,?,?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET status=excluded.status, assignee=excluded.assignee, "
            "updated_by=excluded.updated_by, updated_at=excluded.updated_at",
            (key, new_status, new_assignee, by, _now()),
        )
        return self.states()[key]

    def states(self) -> dict[str, dict[str, Any]]:
        return {
            r["key"]: {"status": r["status"], "assignee": r["assignee"],
                       "updated_by": r["updated_by"], "updated_at": r["updated_at"]}
            for r in self._query("SELECT * FROM notice_state")
        }

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
