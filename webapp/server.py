"""사내 웹앱 서버 — 파이썬 표준 라이브러리만 쓴다 (추가 설치 없음).

    python -m webapp                     # http://localhost:8765
    HOST=0.0.0.0 python -m webapp        # 사내망 공유 — 접속 토큰(?t=...)을 자동 발급해 출력

나라장터 서비스키(NARA_SERVICE_KEY)는 이 서버 프로세스 환경변수에만 있고 브라우저로
절대 내려보내지 않는다.
"""

from __future__ import annotations

import argparse
import hmac
import json
import logging
import mimetypes
import os
import secrets
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from nego import scope

from .collect import MAX_CUSTOM_DAYS, PERIODS, RESULT_FORMAT, Collector
from .store import STATUSES, Store
from .sync import BORDER, HIGH, PastIndex

STATIC = Path(__file__).resolve().parent / "static"
MAX_BODY = 200_000


class App:
    def __init__(self, store: Store, past: PastIndex, collector: Collector, token: str | None):
        self.store = store
        self.past = past
        self.collector = collector
        self.token = token


def make_handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        server_version = "SyncExplorer/1"

        def log_message(self, fmt: str, *args: Any) -> None:  # 토큰이 든 URL을 로그에 남기지 않는다
            logging.getLogger("webapp.http").debug("%s %s", self.command, urlparse(self.path).path)

        # ── 공통 ──────────────────────────────────────────
        def _authorized(self) -> bool:
            if not app.token:
                return True
            given = self.headers.get("X-App-Token") or parse_qs(urlparse(self.path).query).get("t", [""])[0]
            return hmac.compare_digest(given.encode(), app.token.encode())

        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, data: Any, status: int = 200) -> None:
            self._send(status, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

        def _error(self, status: int, message: str) -> None:
            self._json({"error": message}, status)

        def _body(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                raise ValueError("요청이 너무 큽니다")
            raw = self.rfile.read(length) if length else b"{}"
            data = json.loads(raw.decode("utf-8") or "{}")
            if not isinstance(data, dict):
                raise ValueError("JSON 객체가 필요합니다")
            return data

        @staticmethod
        def _name(data: dict[str, Any]) -> str:
            name = str(data.get("name") or "").strip()[:30]
            if not name:
                raise ValueError("상단 '내 이름'을 먼저 입력하세요")
            return name

        # ── GET ───────────────────────────────────────────
        def do_GET(self) -> None:
            url = urlparse(self.path)
            if not url.path.startswith("/api/"):
                return self._static(url.path)
            if not self._authorized():
                return self._error(HTTPStatus.UNAUTHORIZED, "접속 토큰이 필요합니다 (서버 창에 출력된 주소로 접속)")
            q = parse_qs(url.query)
            if url.path == "/api/meta":
                years: dict[str, int] = {}
                for p in app.past.projects:
                    years[str(p.get("year"))] = years.get(str(p.get("year")), 0) + 1
                return self._json({
                    "past_count": len(app.past.projects), "past_years": years,
                    "past_error": app.past.error, "periods": PERIODS, "max_custom_days": MAX_CUSTOM_DAYS, "statuses": STATUSES,
                    "result_format": RESULT_FORMAT,
                    "categories": scope.CATEGORIES, "sync_thresholds": {"high": HIGH, "border": BORDER},
                    "has_service_key": bool(os.environ.get("NARA_SERVICE_KEY", "").strip()),
                    "has_ai_key": bool(os.environ.get("ANTHROPIC_API_KEY", "").strip()),
                })
            if url.path == "/api/results":
                # ?run=번호 — 그 브라우저가 보고 있는(직접 불러온) 결과. 없거나 지워진 번호면 최신 결과(missing=True)
                wanted = q.get("run", [""])[0]
                # ?have=번호 — 브라우저가 이미 그 결과를 들고 있으면(1분마다 새로 조회) 결과 본문은 다시 보내지 않는다.
                # 제외 공고가 수천 건이면 본문이 수 MB라 매번 보내고 다시 그리면 화면이 느려진다(2026-10-01 제보)
                have = q.get("have", [""])[0]
                if have.isdigit():
                    current = app.store.run_id_if_exists(int(wanted) if wanted.isdigit() else None)
                    if current is not None and current == int(have) and (not wanted.isdigit() or int(wanted) == current):
                        return self._json({
                            "run": None, "same": True, "missing": False, "states": app.store.states(),
                            "comment_counts": app.store.comment_counts(), "job": app.collector.job.snapshot(),
                        })
                run = app.store.run(int(wanted)) if wanted.isdigit() else None
                return self._json({
                    "run": run or app.store.latest_run(), "missing": bool(wanted) and run is None,
                    "states": app.store.states(),
                    "comment_counts": app.store.comment_counts(), "job": app.collector.job.snapshot(),
                })
            if url.path == "/api/job":
                return self._json(app.collector.job.snapshot())
            if url.path == "/api/comments":
                key = q.get("key", [""])[0]
                return self._json({"comments": app.store.comments(key)})
            if url.path == "/api/past":
                return self._json({"projects": app.past.public_list(), "error": app.past.error})
            return self._error(HTTPStatus.NOT_FOUND, "없는 주소")

        def _static(self, path: str) -> None:
            name = "index.html" if path in ("", "/") else path.lstrip("/")
            target = (STATIC / name).resolve()
            if STATIC not in target.parents or not target.is_file():
                return self._error(HTTPStatus.NOT_FOUND, "없는 파일")
            ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype.endswith("javascript"):
                ctype += "; charset=utf-8"
            self._send(200, target.read_bytes(), ctype)

        # ── POST ──────────────────────────────────────────
        def do_POST(self) -> None:
            url = urlparse(self.path)
            if not self._authorized():
                return self._error(HTTPStatus.UNAUTHORIZED, "접속 토큰이 필요합니다")
            try:
                data = self._body()
                if url.path == "/api/collect":
                    cats = data.get("categories") or None
                    job = app.collector.start(
                        days=int(data["days"]) if data.get("days") else None,
                        begin=data.get("begin") or None,
                        end=data.get("end") or None,
                        attachments=bool(data.get("attachments", True)),
                        ai=bool(data.get("ai", False)),
                        categories=list(cats) if cats else None,
                        prespec=bool(data.get("prespec", True)),
                    )
                    return self._json(job.snapshot(), HTTPStatus.ACCEPTED)
                if url.path == "/api/state":
                    name = self._name(data)
                    key = str(data.get("key") or "")
                    if not key:
                        raise ValueError("key가 필요합니다")
                    state = app.store.set_state(
                        key, by=name,
                        status=data.get("status") or None,
                        assignee=(name if data.get("take") else None),
                        clear_status=bool(data.get("clear_status")),
                        clear_assignee=bool(data.get("release")),
                    )
                    return self._json({"key": key, "state": state})
                if url.path == "/api/comments":
                    name = self._name(data)
                    key = str(data.get("key") or "")
                    body = str(data.get("body") or "").strip()[:2000]
                    if not key or not body:
                        raise ValueError("key와 내용이 필요합니다")
                    app.store.add_comment(key, name, body)
                    return self._json({"comments": app.store.comments(key)})
                if url.path == "/api/match":
                    title = str(data.get("title") or "").strip()
                    text = str(data.get("text") or "")
                    if not title and not text.strip():
                        raise ValueError("공고명이나 과업 원문을 입력하세요")
                    return self._json(app.past.score(title, text, top=10))
                return self._error(HTTPStatus.NOT_FOUND, "없는 주소")
            except RuntimeError as err:
                return self._error(HTTPStatus.CONFLICT, str(err))
            except (ValueError, json.JSONDecodeError) as err:
                return self._error(HTTPStatus.BAD_REQUEST, str(err))

    return Handler


def build_app(db_path: Path, token: str | None, past: PastIndex | None = None, collector: Collector | None = None) -> App:
    store = Store(db_path)
    past = past or PastIndex.load()
    return App(store, past, collector or Collector(store, past), token)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="webapp", description="입찰 공고 싱크로율 탐색기 (사내 웹앱)")
    parser.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8765")))
    parser.add_argument("--db", default=os.environ.get("WEBAPP_DB", "data/webapp.sqlite3"))
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")

    token = os.environ.get("APP_TOKEN", "").strip() or None
    local_only = args.host in ("127.0.0.1", "localhost", "::1")
    if not token and not local_only:
        # 사내망에 열 때는 토큰 없이 열지 않는다 — 누구나 수집(API 호출)을 돌리지 못하게.
        token = secrets.token_urlsafe(9)

    app = build_app(Path(args.db), token)
    if app.past.error:
        logging.warning(app.past.error)
    else:
        logging.info("과거 실적 %d건 로드 (%s)", len(app.past.projects), app.past.repo)
    if not os.environ.get("NARA_SERVICE_KEY"):
        logging.warning("NARA_SERVICE_KEY 없음 — 화면은 뜨지만 '나라장터에서 불러오기'는 실패합니다")

    httpd = ThreadingHTTPServer((args.host, args.port), make_handler(app))
    shown = "localhost" if local_only else args.host
    print(f"\n  입찰 공고 싱크로율 탐색기: http://{shown}:{args.port}/" + (f"?t={token}" if token else ""))
    if not local_only:
        print("  (0.0.0.0이면 이 PC의 사내 IP로 바꿔 팀원에게 공유. 토큰이 든 주소는 사내에서만 공유하세요)")
    print("  종료: Ctrl+C\n")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0
