"""사내 웹앱 서버 — 파이썬 표준 라이브러리만 쓴다 (추가 설치 없음).

    python -m webapp                     # http://localhost:8765
    HOST=0.0.0.0 python -m webapp        # 사내망 공유 — 접속 토큰(?t=...)을 자동 발급해 출력

나라장터 서비스키(NARA_SERVICE_KEY)는 이 서버 프로세스 환경변수에만 있고 브라우저로
절대 내려보내지 않는다.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import logging
import mimetypes
import os
import secrets
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from nego import bidcalc, scope
from nego.config import ConfigError, load_config, redact
from nego.http_client import ApiError

from . import mview
from .collect import MAX_CUSTOM_DAYS, PERIODS, RESULT_FORMAT, Collector
from .docscan import DocScan
from .mstore import TeamStore
from .reportsync import ReportSync
from .store import DECISION_REASONS, DECISION_SUMMARY_MIN, STATUSES, Store
from .sync import BORDER, HIGH, PastIndex

STATIC = Path(__file__).resolve().parent / "static"
MAX_BODY = 200_000


def static_version() -> str:
    """화면 파일(index.html·js) 지문 — 서버를 새 코드로 다시 켜면 바뀐다. 열려 있던 페이지가 예전 화면 코드로
    새 결과를 그리는 걸 알아채고 "새로고침" 안내를 띄우는 데 쓴다(10/1·10/12 두 번 헷갈림)."""
    digest = hashlib.sha1()
    for path in sorted(STATIC.rglob("*")):
        if path.is_file():
            digest.update(path.name.encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


class App:
    def __init__(self, store: Store, past: PastIndex, collector: Collector, token: str | None,
                 team: TeamStore | None = None, backup_dir: Path | None = None, docscan: DocScan | None = None):
        self.store = store
        self.past = past
        self.collector = collector
        # 공고문으로 찾은 세부품명 공고 — 수집기가 뒤에서 채운다(webapp/docscan.py). 없으면 빈 결과
        self.docscan = docscan or getattr(collector, "docscan", None)
        self.token = token
        self.static_version = static_version()
        # 참가여부·올리기 — 팀원 화면 모양(2026-10-19 화면 이식). 예전 sqlite 기록은 처음 한 번 옮겨 온다
        # backup_dir: 판단 기록을 한 부 더 둘 구글 드라이브 동기화 폴더 (webapp/backup.py, 환경변수 BACKUP_DIR)
        self.team = team or TeamStore(store.path.parent, legacy_db=store.path, titles=self._titles, backup_dir=backup_dir)
        self._run_cache: tuple[int | None, dict[str, Any] | None] = (None, None)
        self._held_raw: dict[str, Any] | None = None

    def _titles(self) -> dict[str, str]:
        run = self.store.latest_run() or {}
        return {c.get("notice_no"): c.get("title") or "" for c in (run.get("candidates") or []) + (run.get("rejected") or [])}

    def latest_run(self) -> dict[str, Any] | None:
        """최신 결과(수 MB JSON)를 요청마다 다시 읽지 않게 결과 번호가 같으면 들고 있는 것을 쓴다."""
        run_id = self.store.run_id_if_exists()
        if run_id is None:
            return None
        if self._run_cache[0] != run_id:
            self._run_cache = (run_id, self.store.run(run_id))
        return self._run_cache[1]

    def held_raw(self) -> dict[str, Any]:
        if self._held_raw is None:
            try:
                self._held_raw = load_config().held_raw
            except ConfigError:
                self._held_raw = {}
        return self._held_raw


def make_handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        server_version = "SyncExplorer/1"

        def log_message(self, fmt: str, *args: Any) -> None:  # 토큰이 든 URL을 로그에 남기지 않는다
            logging.getLogger("webapp.http").debug("%s %s", self.command, urlparse(self.path).path)

        # ── 공통 ──────────────────────────────────────────
        def _authorized(self) -> bool:
            if not app.token:
                return True
            given = self.headers.get("X-App-Token") or parse_qs(urlparse(self.path).query).get("t", [""])[0] or self._cookie()
            return hmac.compare_digest(given.encode(), app.token.encode())

        def _cookie(self) -> str:
            # 팀원 화면은 요청마다 토큰을 붙이지 않는다 — 처음 ?t=로 들어올 때 심은 쿠키로 확인한다(ui.ts와 같은 방식)
            for part in (self.headers.get("Cookie") or "").split(";"):
                name, _, value = part.strip().partition("=")
                if name == "app_token":
                    return value
            return ""

        def _send(self, status: int, body: bytes, content_type: str, cookie: str | None = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            if cookie:
                self.send_header("Set-Cookie", cookie)
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
                return self._json({"error": "접속 토큰이 필요합니다 — 담당자에게 받은 주소(?t=… 포함)로 다시 여세요.",
                                   "unauthorized": True}, HTTPStatus.UNAUTHORIZED)
            q = parse_qs(url.query)
            if url.path == "/api/meta":
                years: dict[str, int] = {}
                for p in app.past.projects:
                    years[str(p.get("year"))] = years.get(str(p.get("year")), 0) + 1
                return self._json({
                    "past_count": len(app.past.projects), "past_years": years,
                    "past_error": app.past.error, "periods": PERIODS, "max_custom_days": MAX_CUSTOM_DAYS, "statuses": STATUSES,
                    "decision_reasons": DECISION_REASONS, "decision_summary_min": DECISION_SUMMARY_MIN,
                    "result_format": RESULT_FORMAT, "static_version": app.static_version,
                    "categories": scope.CATEGORIES, "sync_thresholds": {"high": HIGH, "border": BORDER},
                    "has_service_key": bool(os.environ.get("NARA_SERVICE_KEY", "").strip()),
                    "has_ai_key": bool(os.environ.get("ANTHROPIC_API_KEY", "").strip()),
                })
            if url.path == "/api/bidcalc":
                # 투찰 계산기 — 공고번호로 기초금액·하한율·A값 불러오기 (서비스키는 서버에만)
                no = q.get("no", [""])[0].strip()
                if not no:
                    return self._error(HTTPStatus.BAD_REQUEST, "공고번호를 입력하세요")
                try:
                    config = load_config()
                    if not config.api.service_key:
                        return self._error(HTTPStatus.CONFLICT, "서버에 나라장터 인증키(NARA_SERVICE_KEY)가 없습니다")
                    return self._json(bidcalc.lookup(bidcalc.make_client(config.api), no))
                except (ConfigError, ApiError) as err:
                    key = os.environ.get("NARA_SERVICE_KEY", "")
                    return self._error(HTTPStatus.BAD_GATEWAY, redact(str(err), [key] if key else []))
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
                            "static_version": app.static_version,
                            "comment_counts": app.store.comment_counts(), "job": app.collector.job.snapshot(),
                        })
                run = app.store.run(int(wanted)) if wanted.isdigit() else None
                return self._json({
                    "run": run or app.store.latest_run(), "missing": bool(wanted) and run is None,
                    "static_version": app.static_version,
                    "states": app.store.states(),
                    "comment_counts": app.store.comment_counts(), "job": app.collector.job.snapshot(),
                })
            if url.path == "/api/job":
                return self._json(app.collector.job.snapshot())
            # ── 팀원 화면(ui.html)이 부르는 주소 — 2026-10-19 화면 이식 ──
            if url.path == "/api/notices":
                job = app.collector.job.snapshot()
                if q.get("refresh", [""])[0] == "1":
                    # 나라장터 조회 + 첨부 읽기는 몇 분 걸려서 응답을 붙잡지 않는다 — 수집을 시작하고 화면이 /api/job을 본다
                    days = int(q.get("days", ["7"])[0]) if q.get("days", ["7"])[0].isdigit() else 7
                    try:
                        app.collector.start(days=days if days in PERIODS else 7, attachments=True, ai=False,
                                            categories=None, prespec=True)
                    except RuntimeError:
                        pass  # 이미 누가 수집 중 — 그 진행을 같이 본다
                    return self._json({"running": True, "job": app.collector.job.snapshot()})
                data = mview.notices(app.latest_run(), app.team.promoted, app.held_raw(),
                                     app.docscan.codes() if app.docscan else None)
                return self._json({**data, "job": job, "static_version": app.static_version, "resultFormat": RESULT_FORMAT,
                                   "docScan": app.docscan.state if app.docscan else None})
            if url.path == "/api/excluded":
                return self._json(mview.excluded(app.latest_run(), app.team.promoted, app.held_raw()))
            if url.path == "/api/docscan":
                # '세부품명 미달' 용역 공고 공고문 확인 진행 — 화면이 찾은 건수가 늘면 세부품명 공고 탭을 다시 받는다
                return self._json(app.docscan.state if app.docscan else {"running": False, "done": 0, "total": 0, "found": 0})
            if url.path == "/api/enrich":
                # 첨부 읽기는 수집할 때 끝나 있다 — 화면이 기다릴 것이 없다
                return self._json({"running": False, "done": 0, "total": 0, "byNo": {}, "keepQualifications": [],
                                   "headquartersRegion": app.held_raw().get("companyRegion")})
            if url.path == "/api/participation":
                return self._json({"participation": app.team.participation, "statuses": STATUSES})
            if url.path == "/api/search":
                return self._json(mview.search(q.get("q", [""])[0], app.latest_run(), app.past, app.held_raw()))
            if url.path == "/api/corpus":
                return self._json(mview.corpus(app.past))
            if url.path == "/api/project":
                pid = q.get("id", [""])[0]
                p = app.past.projects[int(pid)] if pid.isdigit() and int(pid) < len(app.past.projects) else {}
                return self._json({"excerpt": mview.project_excerpt(p) if p else None})
            if url.path == "/api/comments":
                key = q.get("key", [""])[0]
                return self._json({"comments": app.store.comments(key)})
            if url.path == "/api/past":
                return self._json({"projects": app.past.public_list(), "error": app.past.error})
            if url.path == "/api/decisions":
                return self._json({"decisions": app.store.decisions()})
            if url.path == "/api/decisions.csv":
                # 판단 기록 내려받기(엑셀에서 바로 열리게 BOM 붙인 UTF-8) — 키워드 조정 검토 때 Claude에게 올려 주는 파일
                return self._send(200, decisions_csv(app.store.decisions(latest_only=False)).encode("utf-8-sig"),
                                  "text/csv; charset=utf-8")
            return self._error(HTTPStatus.NOT_FOUND, "없는 주소")

        def _static(self, path: str) -> None:
            name = "index.html" if path in ("", "/") else path.lstrip("/")
            target = (STATIC / name).resolve()
            if STATIC not in target.parents or not target.is_file():
                return self._error(HTTPStatus.NOT_FOUND, "없는 파일")
            ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype.endswith("javascript"):
                ctype += "; charset=utf-8"
            cookie = None
            if name == "index.html" and app.token:
                given = parse_qs(urlparse(self.path).query).get("t", [""])[0]
                if given and hmac.compare_digest(given.encode(), app.token.encode()):
                    # HttpOnly·SameSite=Lax, 1년 — 한 번 토큰 주소로 들어오면 그 뒤로는 주소에 토큰이 없어도 된다
                    cookie = f"app_token={app.token}; HttpOnly; SameSite=Lax; Path=/; Max-Age=31536000"
            self._send(200, target.read_bytes(), ctype, cookie)

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
                        promote=(bool(data["promote"]) if "promote" in data else None),
                    )
                    return self._json({"key": key, "state": state})
                if url.path == "/api/decision":
                    # 참가·보류·불참 + 사유(2026-10-13 요청) — 참가여부도 같이 바꾼다. status 없음 = 참가여부 해제
                    name = self._name(data)
                    key = str(data.get("key") or "")
                    if not key:
                        raise ValueError("key가 필요합니다")
                    status = data.get("status") or None
                    snapshot = data.get("snapshot") if isinstance(data.get("snapshot"), dict) else {}
                    app.store.add_decision(key, name, status, [str(r) for r in (data.get("reasons") or [])][:12],
                                           str(data.get("memo") or ""), snapshot)
                    state = app.store.set_state(key, by=name, status=status, clear_status=status is None)
                    return self._json({"key": key, "state": state, "decisions": app.store.decisions()})
                if url.path == "/api/comments":
                    name = self._name(data)
                    key = str(data.get("key") or "")
                    body = str(data.get("body") or "").strip()[:2000]
                    if not key or not body:
                        raise ValueError("key와 내용이 필요합니다")
                    app.store.add_comment(key, name, body)
                    return self._json({"comments": app.store.comments(key)})
                if url.path == "/api/participation":
                    return self._json({"ok": True, "participation": app.team.update(data)})
                if url.path == "/api/promote":
                    return self._json({"ok": True, "promoted": app.team.promote(data)})
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


def decisions_csv(rows: list[dict[str, Any]]) -> str:
    """판단 기록 → CSV(한 판단 한 줄, 바꾼 이력 포함). 집계는 화면/Claude가 한다."""
    import csv
    import io

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["판단일시", "판단자", "참가여부", "사유", "메모", "공고번호", "차수", "공고명", "공고종류", "낙찰방법", "업무",
                "금액", "수요기관", "관심키워드", "검토필요_제외키워드", "원래_제외사유", "싱크로율", "자격"])
    for d in rows:
        sn = d.get("snapshot") or {}
        w.writerow([d["decided_at"], d["decided_by"], d["status"] or "(해제)", " / ".join(d["reasons"]), d.get("memo") or "",
                    sn.get("notice_no"), sn.get("notice_ord"), sn.get("title"), sn.get("kind"), sn.get("category"),
                    sn.get("work_type"), sn.get("budget"), sn.get("demand_institution"),
                    ", ".join(sn.get("matched_keywords") or []), sn.get("review_exclude") or "",
                    sn.get("excluded_reason") or "", sn.get("sync_level") or "", sn.get("qual_summary") or ""])
    return buf.getvalue()


def build_app(db_path: Path, token: str | None, past: PastIndex | None = None, collector: Collector | None = None,
              backup_dir: Path | None = None) -> App:
    store = Store(db_path)
    past = past or PastIndex.load()
    docscan = DocScan(store.path.parent / "doc_products.json")
    return App(store, past, collector or Collector(store, past, docscan=docscan), token, backup_dir=backup_dir, docscan=docscan)


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

    # 판단 기록 백업 폴더 — 구글 드라이브 데스크톱이 동기화하는 폴더 (webapp/backup.py). 비어 있으면 백업하지 않는다
    backup_dir = Path(os.environ["BACKUP_DIR"].strip()) if os.environ.get("BACKUP_DIR", "").strip() else None
    app = build_app(Path(args.db), token, backup_dir=backup_dir)
    if backup_dir is not None:
        app.team.backup_now()
        # 로그인하자마자 서버가 뜨면 구글 드라이브가 아직 연결되기 전일 수 있어 2분 뒤 한 번 더
        timer = threading.Timer(120, app.team.backup_now)
        timer.daemon = True
        timer.start()
    # 텔레그램 보고에 나간 공고 → 판단 기록 '미판단' (bot-state 브랜치의 보고 기록을 몇 분마다 받아 합친다)
    ReportSync(app.team.merge_report).start()
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
    if backup_dir is not None:
        print(f"  판단 기록 백업: {backup_dir}" + ("" if backup_dir.exists() else "  ⚠ 폴더가 없습니다 — 구글 드라이브가 켜져 있는지 확인하세요"))
    else:
        print("  판단 기록 백업: 꺼져 있음 (환경변수 BACKUP_DIR에 구글 드라이브 폴더를 적으면 켜집니다)")
    print("  종료: Ctrl+C\n")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0
