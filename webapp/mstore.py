"""참가여부·담당·대화·판단 사유와 "목록에 올리기" — 팀원 화면(narajangter-bid-monitor `scripts/ui.ts`)과 같은 모양
(2026-10-19 통합: 화면을 팀원 것으로 바꾸면서 저장 모양도 그쪽에 맞춤).

공고번호(차수 없이)로 묶는다 — 정정공고로 차수가 올라도 판단·대화가 그대로 이어진다.
파일 두 개(`data/participation.json`, `data/promoted.json`)에 둔다. 예전 화면의 sqlite 기록(`data/webapp.sqlite3`의
notice_state·decisions·comments)이 있으면 **처음 한 번만** 옮겨 온다(sqlite 파일은 지우지 않음).

백업(2026-10-20, 팀원 레포 10/19 이식): `backup_dir`(환경변수 `BACKUP_DIR` — 구글 드라이브 동기화 폴더)이 있으면 저장할 때마다
두 파일을 그 폴더에 복사하고(날짜별 사본 30일), 판단 기록 표 `판단기록.csv`도 함께 쓴다. 이 PC에 파일이 없으면 백업에서
되살린다. 텔레그램 보고에 나간 공고는 `merge_report`로 판단 기록에 '미판단'으로 합친다(`webapp/reportsync.py`).
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from nego import report_log

from .backup import backup_content, restore_if_missing

STATUSES = ["검토중", "참가", "보류", "불참"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _clean_tags(v: Any) -> list[str]:
    if not isinstance(v, list):
        return []
    out: list[str] = []
    for x in v:
        t = str(x).strip()[:30]
        if t and t not in out:
            out.append(t)
    return out[:12]


def _snapshot(p: dict[str, Any]) -> dict[str, Any]:
    return {"status": p.get("status"), "reason": p.get("reason", ""), "tags": p.get("tags", []),
            "owner": p.get("owner", ""), "autoOwner": p.get("autoOwner", False),
            "decidedBy": p.get("decidedBy", ""), "decidedAt": p.get("decidedAt")}


class TeamStore:
    def __init__(self, folder: Path, legacy_db: Path | None = None, titles: Callable[[], dict[str, str]] | None = None,
                 backup_dir: Path | None = None):
        folder.mkdir(parents=True, exist_ok=True)
        self.part_path = folder / "participation.json"
        self.promo_path = folder / "promoted.json"
        self.backup_dir = backup_dir
        self._lock = threading.Lock()
        # 새 PC에 설치했거나 data가 지워졌으면 백업에서 되살린다 (이 PC에 파일이 있으면 건드리지 않는다)
        restore_if_missing(self.part_path, backup_dir)
        restore_if_missing(self.promo_path, backup_dir)
        self.participation: dict[str, dict[str, Any]] = self._read(self.part_path)
        self.promoted: dict[str, dict[str, Any]] = self._read(self.promo_path)
        if not self.part_path.exists() and legacy_db is not None and legacy_db.exists():
            self._migrate(legacy_db, titles() if titles else {})

    @staticmethod
    def _read(path: Path) -> dict[str, Any]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _save(self) -> None:
        part = json.dumps(self.participation, ensure_ascii=False, indent=1)
        promo = json.dumps(self.promoted, ensure_ascii=False, indent=1)
        self.part_path.write_text(part, encoding="utf-8")
        self.promo_path.write_text(promo, encoding="utf-8")
        self._backup(part, promo)

    def _backup(self, part: str, promo: str) -> None:
        if self.backup_dir is None:
            return
        now = datetime.now(timezone.utc)
        backup_content(self.part_path.name, part, self.backup_dir, now)
        backup_content(self.promo_path.name, promo, self.backup_dir, now)
        # 팀원이 구글 드라이브에서 표로 보는 판단 기록 — 화면 'CSV 내려받기'와 같은 표 (보기용이라 날짜별 사본은 두지 않는다)
        backup_content("판단기록.csv", report_log.decisions_csv(self.participation, now), self.backup_dir, now, daily=False)

    def backup_now(self) -> None:
        """지금 기록을 백업한다 — 서버가 뜰 때(판단을 새로 누르지 않아도 백업이 최신이 되게)."""
        with self._lock:
            self._backup(json.dumps(self.participation, ensure_ascii=False, indent=1),
                         json.dumps(self.promoted, ensure_ascii=False, indent=1))

    def merge_report(self, entry: dict[str, Any]) -> int:
        """보고 기록 하나를 판단 기록에 합친다 (`report_log.merge`). 새로 생긴 공고 수를 돌려준다."""
        with self._lock:
            before = len(self.participation)
            if report_log.merge(self.participation, entry, _now()):
                self._save()
            return len(self.participation) - before

    def _migrate(self, db: Path, titles: dict[str, str]) -> None:
        """예전 화면 기록(공고번호-차수 키) → 공고번호 키. 같은 공고의 여러 차수는 마지막 것으로."""
        try:
            con = sqlite3.connect(str(db))
            con.row_factory = sqlite3.Row
            tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "notice_state" not in tables:
                return
            cols = {r[1] for r in con.execute("PRAGMA table_info(notice_state)")}
            states = list(con.execute("SELECT * FROM notice_state ORDER BY updated_at"))
            comments = list(con.execute("SELECT * FROM comments ORDER BY created_at")) if "comments" in tables else []
            decisions = list(con.execute("SELECT * FROM decisions ORDER BY decided_at")) if "decisions" in tables else []
        except sqlite3.Error:
            return
        finally:
            try:
                con.close()
            except Exception:  # noqa: BLE001
                pass
        no_of = lambda key: str(key).rsplit("-", 1)[0]  # noqa: E731
        for s in states:
            no = no_of(s["key"])
            p = self.participation.setdefault(no, {"noticeNo": no, "title": titles.get(no, ""), "status": None,
                                                   "owner": "", "updatedAt": s["updated_at"] or _now(), "comments": []})
            if s["status"]:
                p["status"] = s["status"]
            if s["assignee"]:
                p["owner"] = s["assignee"]
            p["updatedAt"] = s["updated_at"] or p["updatedAt"]
            if "promoted_by" in cols and s["promoted_by"]:
                self.promoted[no] = {"by": s["promoted_by"], "at": s["promoted_at"] or _now(),
                                     "title": titles.get(no, ""), "keyword": "", "reason": "예전 화면에서 올림"}
        for d in decisions:
            no = no_of(d["key"])
            snap = json.loads(d["snapshot"] or "{}")
            p = self.participation.setdefault(no, {"noticeNo": no, "title": snap.get("title") or titles.get(no, ""),
                                                   "status": None, "owner": "", "updatedAt": d["decided_at"], "comments": []})
            p["title"] = p.get("title") or snap.get("title") or ""
            p["tags"] = json.loads(d["reasons"] or "[]")
            p["reason"] = d["memo"] or ""
            p["decidedBy"], p["decidedAt"] = d["decided_by"], d["decided_at"]
            p["meta"] = {"sourceType": snap.get("kind"), "businessType": snap.get("work_type"),
                         "institution": snap.get("demand_institution") or "", "budgetAmount": snap.get("budget"),
                         "bidMethod": snap.get("category"), "keywords": snap.get("matched_keywords") or []}
        for c in comments:
            no = no_of(c["key"])
            p = self.participation.setdefault(no, {"noticeNo": no, "title": titles.get(no, ""), "status": None,
                                                   "owner": "", "updatedAt": c["created_at"], "comments": []})
            p["comments"].append({"by": c["author"], "text": c["body"], "at": c["created_at"]})
        if self.participation or self.promoted:
            self._save()

    # ── 참가여부 (ui.ts POST /api/participation과 같은 규칙) ──
    def update(self, b: dict[str, Any]) -> dict[str, Any]:
        no = str(b.get("noticeNo") or "").strip()
        by = str(b.get("by") or "").strip()[:40]
        if not no:
            raise ValueError("공고번호가 없습니다")
        if not by:
            raise ValueError("이름을 먼저 입력하세요")
        now = _now()
        with self._lock:
            p = self.participation.get(no) or {"noticeNo": no, "title": str(b.get("title") or "")[:300],
                                               "status": None, "owner": "", "updatedAt": now, "comments": []}
            if b.get("undo"):
                if not p.get("prev"):
                    raise ValueError("되돌릴 판단이 없습니다")
                p.update(p["prev"])
                p["prev"] = None
            if "status" in b and b["status"] is not None:
                status = b["status"] or None
                if status is not None and status not in STATUSES:
                    raise ValueError("알 수 없는 상태입니다")
                if status != p.get("status"):
                    p["prev"] = _snapshot(p)
                    p["status"] = status
                    p["reason"] = str(b.get("reason") or "").strip()[:500]
                    p["tags"] = _clean_tags(b.get("tags"))
                    p["decidedBy"] = by if status else ""
                    p["decidedAt"] = now if status else None
                    meta = b.get("meta")
                    if isinstance(meta, dict) and len(json.dumps(meta, ensure_ascii=False)) <= 4000:
                        p["meta"] = meta
                    # 담당은 마지막으로 참가여부를 고른 사람 — 해제하면 담당도 지운다(팀원 화면 10/15 규칙)
                    p["owner"] = by if status else ""
                    p["autoOwner"] = bool(status)
            else:
                if "reason" in b:
                    p["reason"] = str(b["reason"] or "").strip()[:500]
                if "tags" in b:
                    p["tags"] = _clean_tags(b["tags"])
            if "owner" in b:
                owner = str(b["owner"] or "").strip()[:40]
                if owner != p.get("owner"):
                    p["prev"] = _snapshot(p)
                    p["owner"] = owner
                p["autoOwner"] = False
            comment = str(b.get("comment") or "").strip()[:1000]
            if comment:
                p["comments"].append({"by": by, "text": comment, "at": now})
            target = b.get("editComment") or b.get("deleteComment")
            if isinstance(target, dict):
                found = next((c for c in p["comments"] if c.get("at") == target.get("at") and not c.get("system")), None)
                if not found:
                    raise ValueError("대화를 찾을 수 없습니다. 화면을 새로고침해 주세요")
                if found.get("by") != by:
                    raise ValueError("내가 쓴 대화만 고치거나 지울 수 있습니다")
                if b.get("deleteComment"):
                    p["comments"] = [c for c in p["comments"] if c is not found]
                else:
                    text = str((b.get("editComment") or {}).get("text") or "").strip()[:1000]
                    if not text:
                        raise ValueError("내용이 비어 있습니다. 지우려면 '삭제'를 누르세요")
                    found["text"], found["editedAt"] = text, now
            p["updatedAt"] = now
            self.participation[no] = p
            self._save()
            return p

    # ── 목록에 올리기·다시 빼기 ──
    def promote(self, b: dict[str, Any]) -> dict[str, Any]:
        no = str(b.get("noticeNo") or "").strip()
        by = str(b.get("by") or "").strip()[:40]
        if not no:
            raise ValueError("공고번호가 없습니다")
        if not by:
            raise ValueError("이름을 먼저 입력하세요")
        with self._lock:
            if b.get("on"):
                entry = {"by": by, "at": _now(), "title": str(b.get("title") or "")[:300],
                         "keyword": str(b.get("keyword") or "")[:60]}
                reason = str(b.get("reason") or "")[:200]
                if reason:
                    entry["reason"] = reason
                self.promoted[no] = entry
            else:
                self.promoted.pop(no, None)
            self._save()
            return self.promoted
