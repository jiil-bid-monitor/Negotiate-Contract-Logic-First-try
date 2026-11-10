"""서버 PC 투찰 기록 받아 오기(NAS, 2026-11-10 사용자 결정).

팀원은 지금처럼 서버 PC 웹앱에서 투찰 기록을 남기고, NAS 웹앱이 평일 8~19시 1시간마다 서버 PC 웹앱의 /api/bidlog를 읽어
NAS의 따로 된 파일(data/bidlog_서버PC/bid_log.json)에 사본으로 둔다. 서버 PC 기록은 읽기만 한다.
NAS의 22시 작업·낙찰 후 서류·투찰내역 화면은 NAS 기록 + 사본을 함께 본다(CombinedLog).
켜기: NAS `.env`에 BIDLOG_PULL_URL=http://서버PC주소:8765 (토큰이 다르면 BIDLOG_PULL_TOKEN). 끄기: 그 줄을 지움.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import requests

from .bidlog import BidLog

log = logging.getLogger(__name__)
KST = timezone(timedelta(hours=9))
MIRROR_DIR = "bidlog_서버PC"
EVERY = timedelta(hours=1)
WORK_HOURS = range(8, 20)  # 8~19시 — 서버 PC가 켜져 있을 때
# 이 칸들은 NAS가 받아 채우는 값이라 서버 PC 사본을 다시 받아도 NAS 쪽 것을 남긴다
_KEEP = ("result", "openingAt", "closingAt")


class MirrorLog(BidLog):
    """서버 PC 기록 사본 — 결과 칸은 NAS가 채우고, 나머지는 받아 올 때마다 서버 PC 값으로."""

    def replace_from(self, remote: list[dict[str, Any]]) -> int:
        with self._lock:
            mine = {r.get("id"): r for r in self._read()}
            rows = []
            for r in remote:
                if not isinstance(r, dict) or not r.get("id") or not r.get("noticeNo"):
                    continue
                old = mine.get(r["id"]) or {}
                rows.append({**r, **{k: old[k] for k in _KEEP if old.get(k)}, "from": "서버 PC"})
            self._write(rows)
        return len(rows)


class CombinedLog:
    """NAS 기록(새 기록은 여기로) + 서버 PC 사본. 사본 기록은 고치거나 지울 수 없음(서버 PC에서)."""

    table = BidLog.table
    csv = BidLog.csv

    def __init__(self, own: BidLog, mirror: MirrorLog):
        self.own, self.mirror = own, mirror

    def entries(self) -> list[dict[str, Any]]:
        rows = self.own.entries() + self.mirror.entries()
        return sorted(rows, key=lambda r: r.get("recordedAt") or "", reverse=True)

    def _owner(self, entry_id: str) -> BidLog:
        return self.mirror if any(r.get("id") == entry_id for r in self.mirror.entries()) else self.own

    def add(self, data: dict[str, Any]) -> dict[str, Any]:
        return self.own.add(data)

    def update(self, entry_id: str, data: dict[str, Any]) -> dict[str, Any]:
        if self._owner(entry_id) is self.mirror:
            raise ValueError("서버 PC에서 받아 온 기록입니다 — 서버 PC 웹앱에서 고치세요")
        return self.own.update(entry_id, data)

    def delete(self, entry_id: str) -> None:
        if self._owner(entry_id) is self.mirror:
            raise ValueError("서버 PC에서 받아 온 기록입니다 — 서버 PC 웹앱에서 지우세요")
        self.own.delete(entry_id)

    def set_result(self, entry_id: str, result: dict[str, Any]) -> None:
        self._owner(entry_id).set_result(entry_id, result)

    def set_own_field(self, entry_id: str, key: str, value: Any) -> None:
        self._owner(entry_id).set_own_field(entry_id, key, value)

    def set_times(self, entry_id: str, times: dict[str, str]) -> None:
        self._owner(entry_id).set_times(entry_id, times)

    def bidders(self, entry_id: str) -> list[dict[str, Any]] | None:
        return self._owner(entry_id).bidders(entry_id)


class BidLogPull:
    def __init__(self, mirror: MirrorLog, url: str, token: str, get: Callable[..., Any] = requests.get,
                 now: Callable[[], datetime] = lambda: datetime.now(KST)):
        self.mirror, self.url, self.token, self.get, self.now = mirror, url.rstrip("/"), token, get, now
        self.last: datetime | None = None
        self.error = ""

    def pull(self) -> bool:
        """한 번 받아 오기. 실패하면 사본을 그대로 두고 False(서버 PC가 꺼져 있을 때 등)."""
        try:
            res = self.get(f"{self.url}/api/bidlog", headers={"X-App-Token": self.token}, timeout=15)
            res.raise_for_status()
            count = self.mirror.replace_from(res.json().get("entries") or [])
        except (requests.RequestException, ValueError, AttributeError) as err:
            self.error = type(err).__name__  # 주소·토큰이 든 원문은 남기지 않음
            log.info("서버 PC 투찰 기록 받아 오기 실패: %s", self.error)
            return False
        self.last, self.error = self.now(), ""
        log.info("서버 PC 투찰 기록 %d건 받아 옴", count)
        return True

    def due(self) -> bool:
        now = self.now()
        return now.weekday() < 5 and now.hour in WORK_HOURS and (self.last is None or now - self.last >= EVERY)

    def start(self, poll_seconds: int = 60) -> None:
        def loop() -> None:
            while True:
                try:
                    if self.due():
                        self.pull()
                except Exception:  # noqa: BLE001 — 받아 오기가 웹앱을 멈추지 않게
                    log.exception("서버 PC 투찰 기록 받아 오기 오류")
                threading.Event().wait(poll_seconds)

        threading.Thread(target=loop, name="bidlog-pull", daemon=True).start()


def selfcheck() -> None:
    """가짜 서버 PC 응답으로 사본·합치기 확인(tests/test_bidlogpull.py)."""
    import tempfile

    class _Res:
        def __init__(self, data):
            self.data = data

        def raise_for_status(self):
            pass

        def json(self):
            return self.data

    remote = [{"id": "s1", "noticeNo": "R26BK00000001", "bid": 100, "recordedAt": "2026-11-10T09:00:00+09:00", "result": None}]
    with tempfile.TemporaryDirectory() as d:
        own = BidLog(Path(d))
        mirror = MirrorLog(Path(d) / MIRROR_DIR)
        both = CombinedLog(own, mirror)
        pull = BidLogPull(mirror, "http://x", "t", get=lambda *a, **k: _Res({"entries": remote}),
                          now=lambda: datetime(2026, 11, 10, 9, tzinfo=KST))
        assert pull.due() and pull.pull() and not pull.due()
        own.add({"noticeNo": "R26BK00000002", "bid": 200})
        assert sorted(e["noticeNo"] for e in both.entries()) == ["R26BK00000001", "R26BK00000002"]
        both.set_result("s1", {"status": "완료"})
        assert mirror.entries()[0]["result"] == {"status": "완료"}
        pull.pull()  # 다시 받아도 NAS가 받은 결과는 남음
        assert mirror.entries()[0]["result"] == {"status": "완료"}
        try:
            both.delete("s1")
            raise AssertionError("사본 기록이 지워짐")
        except ValueError:
            pass
        bad = BidLogPull(mirror, "http://x", "t", get=lambda *a, **k: (_ for _ in ()).throw(requests.ConnectionError("x")))
        assert not bad.pull() and len(mirror.entries()) == 1  # 실패해도 사본 그대로
        assert not BidLogPull(mirror, "u", "t", now=lambda: datetime(2026, 11, 10, 21, tzinfo=KST)).due()  # 업무 시간 밖
        assert not BidLogPull(mirror, "u", "t", now=lambda: datetime(2026, 11, 8, 10, tzinfo=KST)).due()  # 일요일


if __name__ == "__main__":
    selfcheck()
    print("ok")
