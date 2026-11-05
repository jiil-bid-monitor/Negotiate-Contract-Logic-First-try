"""서버 PC의 수집 결과를 NAS 웹앱으로 보낸다 (2026-11-05 사용자 결정).

30일치 수집은 메모리 최고 약 2.5GB라 NAS 웹앱 제한(2GB)을 넘어 서버 PC에서 돌리고, 팀원은 NAS 화면에서 결과를 본다.
서버 PC `.env`에 RESULT_PUSH_URL(NAS 웹앱 주소)이 있을 때만 켜진다. 수집과 공고문 세부품명 확인이 모두 끝나면 보내고,
실패하면(NAS가 꺼짐·재시작 중 등) POLL_SECONDS 뒤 다시 시도한다. 받는 쪽은 `server.py`의 /api/import-run —
본문을 다 받아 한 번에 저장하므로 보내다 끊겨도 NAS는 이전 결과를 그대로 보여 준다.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import requests

log = logging.getLogger(__name__)

POLL_SECONDS = 60
FIRST_DELAY_SECONDS = 15
TIMEOUT_SECONDS = 600  # 30일치 본문은 수십 MB — NAS가 받아 저장하는 시간까지
RUN_META = ("id", "started_at", "finished_at", "params")


class ResultPush:
    def __init__(self, url: str, token: str | None, store: Any, docscan: Any, busy: Callable[[], bool],
                 state_path: Path, post: Callable[..., Any] = requests.post):
        self.url = url.rstrip("/") + "/api/import-run"
        self.token = token
        self.store = store
        self.docscan = docscan
        self.busy = busy
        self.state_path = state_path
        self._post = post
        # {"run": 마지막으로 보낸 결과 번호, "at": 보낸 시각, "error": 마지막 실패 사유}
        self.state: dict[str, Any] = self._read()

    def _read(self) -> dict[str, Any]:
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"run": None, "at": None, "error": None}
        return data if isinstance(data, dict) else {"run": None, "at": None, "error": None}

    def _save(self) -> None:
        self.state_path.write_text(json.dumps(self.state, ensure_ascii=False), encoding="utf-8")

    def step(self) -> bool:
        """보낼 결과가 있으면 한 번 보낸다. 보냈으면 True, 보낼 게 없거나 실패하면 False."""
        if self.busy():  # 세부품명 확인까지 끝나야 '세부품명 공고' 탭 자료가 함께 간다
            return False
        run_id = self.store.run_id_if_exists()
        if run_id is None or run_id == self.state.get("run"):
            return False
        run = self.store.run(run_id)
        if run is None:
            return False
        body = {"started_at": run["started_at"], "finished_at": run["finished_at"], "params": run["params"],
                "payload": {k: v for k, v in run.items() if k not in RUN_META},
                "doc_products": self.docscan.results}
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        del run, body
        try:
            res = self._post(self.url, data=data, timeout=TIMEOUT_SECONDS,
                             headers={"Content-Type": "application/json", "X-App-Token": self.token or ""})
            if res.status_code != 200:
                raise OSError(f"NAS 응답 {res.status_code}: {res.text[:200]}")
        except (OSError, requests.RequestException) as err:  # requests 오류의 주소·토큰은 싣지 않는다
            message = str(err) if isinstance(err, OSError) and not isinstance(err, requests.RequestException) \
                else f"NAS에 연결하지 못했습니다 ({type(err).__name__})"
            if message != self.state.get("error"):
                log.warning("수집 결과를 NAS로 보내지 못했습니다 — 1분 뒤 다시 시도: %s", message)
            self.state["error"] = message
            self._save()
            return False
        self.state = {"run": run_id, "at": datetime.now().isoformat(timespec="seconds"), "error": None}
        self._save()
        log.info("수집 결과 %d번을 NAS로 보냈습니다 (%.1fMB)", run_id, len(data) / 1024 ** 2)
        return True

    def start(self) -> None:
        stop = threading.Event()

        def loop() -> None:
            if stop.wait(FIRST_DELAY_SECONDS):
                return
            while True:
                try:
                    self.step()
                except Exception:  # noqa: BLE001 — 보내기 실패가 웹앱을 멈추면 안 된다
                    log.exception("수집 결과 보내기 실패")
                if stop.wait(POLL_SECONDS):
                    return

        threading.Thread(target=loop, name="result-push", daemon=True).start()
