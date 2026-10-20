"""공고문 세부품명 확인 — 수집이 끝난 뒤 뒤에서 (2026-10-20, 팀원 레포 10/19 이식).

'세부품명 미달'(키워드·세부품명 하나만 안 맞고 다른 조건은 통과, `Candidate.only_keyword_miss`)인 **용역** 공고의
첨부를 참가자격 절이 나오는 문서까지 읽어(`nego/doc_products.py`), 지일 등록 세부품명을 요구하면 '세부품명 공고' 탭에
싣는다(`mview.notices`의 doc_products). 수백 건이라 수집 작업 안에서 읽으면 수집이 몇 배 길어져, 결과를 저장해
화면에 띄운 **뒤에** 따로 돈다. 한 번 읽은 공고는 `data/doc_products.json`에 남겨 다음 수집 때 다시 읽지 않는다.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import requests

log = logging.getLogger(__name__)

# 나라장터·g2b 서버에 몰아서 요청하지 않는다
PAUSE_SECONDS = 0.3


class DocScan:
    def __init__(self, path: Path, finder: Callable[..., tuple[list[str], str | None]] | None = None):
        self.path = path
        self._finder = finder
        self._lock = threading.Lock()
        self.results: dict[str, dict[str, Any]] = self._read()
        self.state = {"running": False, "done": 0, "total": 0, "found": 0}

    def _read(self) -> dict[str, dict[str, Any]]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.results, ensure_ascii=False, indent=1), encoding="utf-8")

    def codes(self) -> dict[str, list[str]]:
        """{공고번호: 공고문이 요구한 지일 등록 세부품명} — 찾은 것만."""
        with self._lock:
            return {no: r["codes"] for no, r in self.results.items() if r.get("codes")}

    def start(self, notices: list[Any], held_products: set[str]) -> None:
        """아직 안 읽은 공고만 데몬 스레드로 읽는다. 이미 도는 중이면 이번 요청은 건너뛴다(다음 수집 때 이어서)."""
        with self._lock:
            if self.state["running"]:
                return
            todo = [n for n in notices if n.notice_no not in self.results]
            found = sum(1 for n in notices if (self.results.get(n.notice_no) or {}).get("codes"))
            self.state = {"running": bool(todo), "done": 0, "total": len(todo), "found": found}
        if not todo:
            return
        log.info("공고문 세부품명 확인 시작: 세부품명 미달 용역 공고 %d건", len(todo))
        threading.Thread(target=self._work, args=(todo, held_products), name="doc-scan", daemon=True).start()

    def _work(self, todo: list[Any], held_products: set[str]) -> None:
        finder = self._finder
        if finder is None:
            from nego.doc_products import required_held_products as finder
        session = requests.Session()
        try:
            for n in todo:
                try:
                    codes, file_name = finder(n, held_products, session)
                except Exception as err:  # noqa: BLE001 — 한 건이 실패해도 나머지는 계속, 다음 수집 때 다시 읽는다
                    log.warning("공고문 세부품명 확인 실패 [%s]: %s", n.notice_no, err)
                    self.state["done"] += 1
                    continue
                with self._lock:
                    self.results[n.notice_no] = {"codes": codes, "file": file_name,
                                                 "at": datetime.now().isoformat(timespec="seconds")}
                    self._save()
                    self.state["done"] += 1
                    if codes:
                        self.state["found"] += 1
                time.sleep(PAUSE_SECONDS)
        finally:
            self.state["running"] = False
            log.info("공고문 세부품명 확인 끝: %d건 중 %d건에서 지일 등록 세부품명 찾음", self.state["total"], self.state["found"])
