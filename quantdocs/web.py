"""정량평가서 준비 웹앱 — 서버 PC에서 띄운다(11/03 사용자 결정).

    python -m quantdocs.web            # http://<서버 PC>:8766/?t=<토큰>

대상: 참가하기로 정한 공고 — 협상·적격심사·사업수행능력평가 등 정량 서류를 내는 공고(11/03 넓힘).
1. 진행하기로 한 공고번호 입력 → 2. 나라장터에서 공고·첨부를 받아 필요 서식·자격·제출서류·실적 요건을 화면에
3. '정량평가서 만들기' → 잠금 묶음(vault/company.enc)을 메모리에서 풀어 서식을 채우고 회사 서류를 골라 zip
4. 내려받기.

환경변수:
  QUANT_VAULT_PASSPHRASE  잠금 묶음 비밀번호(필수 — 서버 PC에만)
  APP_TOKEN               접속 토큰(없으면 실행할 때 만들어 화면에 출력) — 사내망이라도 회사 서류가 나가므로 늘 확인
  HOST / PORT             기본 0.0.0.0 / 8766
  HWP2HWPX_CMD            옛 한글 변환 명령(없으면 .hwp 서식은 못 자름)
  QUANT_GIT_PULL=1        분석할 때마다 이 저장소를 git pull(새 잠금 묶음 받기)
  (11/03) 프로젝트 1 레포로 합침 — 보통은 프로젝트 1 웹앱(webapp, 8765)의 '정량평가서' 탭으로 씀(/api/quant/…).
  이 단독 웹앱(8766)도 그대로 켤 수 있음. 설정값은 저장소 .env에서 읽음(vault.read_env).
작업 폴더(data/jobs/…)는 2시간 뒤 지운다. 내려받은 묶음엔 회사 서류가 들어 있으니 사내에서만 보관.
"""

from __future__ import annotations

import json
import re
import logging
import os
import secrets
import shutil
import subprocess
import threading
import time
import traceback
import uuid
from datetime import date, datetime
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from . import vault as vault_mod
from .build import analyze, build, main_docs, summary
from .g2b import NoticeFiles, fetch_notice

ROOT = Path(__file__).resolve().parent.parent
STATIC = Path(__file__).resolve().parent / "static"
JOBS = ROOT / "data" / "quant_jobs"
KEEP_SEC = 2 * 3600
log = logging.getLogger("quantdocs.web")

TOKEN = os.environ.get("APP_TOKEN", "").strip() or secrets.token_urlsafe(9)
_jobs: dict[str, dict] = {}
_lock = threading.Lock()
_build_lock = threading.Lock()


def _cleanup() -> None:
    now = time.time()
    if JOBS.exists():
        for d in JOBS.iterdir():
            if d.is_dir() and now - d.stat().st_mtime > KEEP_SEC:
                shutil.rmtree(d, ignore_errors=True)
                _jobs.pop(d.name, None)


def _code_stamp() -> str:
    """quantdocs 폴더 .py 파일들의 마지막 고친 시각 — 켤 때와 지금이 다르면 예전 코드로 실행 중(11/05: 서버에서 고친 내용이 안 보임)."""
    try:
        return max(f"{p.stat().st_mtime:.0f}" for p in (ROOT / "quantdocs").glob("*.py"))
    except (OSError, ValueError):
        return ""


_STARTED_STAMP = _code_stamp()


def _stale_note() -> str:
    now = _code_stamp()
    if _STARTED_STAMP and now and now != _STARTED_STAMP:
        return ("⚠ 프로그램 코드가 바뀌었는데(git pull) 웹앱을 다시 켜지 않아 예전 코드로 실행 중 — 웹앱을 껐다 켠 뒤 F5 → 다시 불러오기. "
                "켠 이후 고친 내용(서식 찾기·채우기 등)이 이번 결과엔 없음")
    return ""


def _git_pull() -> str:
    if os.environ.get("QUANT_GIT_PULL") != "1":
        return ""
    try:
        r = subprocess.run(["git", "-C", str(ROOT), "pull", "--ff-only"], capture_output=True, text=True, timeout=60)
        return "" if r.returncode == 0 else f"git pull 실패 — 이전 묶음으로 진행: {r.stderr.strip()[:200]}"
    except (OSError, subprocess.SubprocessError) as err:
        return f"git pull 실패 — 이전 묶음으로 진행: {err}"


def _vault_info() -> dict:
    p = vault_mod.sealed_path()
    if not p.exists():
        return {"ok": False, "msg": "회사 자료 묶음이 없습니다 — 옆 폴더 jiil-quant-docs(비공개 레포)를 받거나 .env에 QUANT_VAULT_PATH"}
    if not vault_mod.read_passphrase():
        return {"ok": False, "msg": f"서버 PC에 {vault_mod.ENV}가 설정되지 않았습니다"}
    return {"ok": True, "msg": f"회사 자료 최근 갱신 {_sealed_date(p)}", "updated": _sealed_date(p)}


def _sealed_date(p) -> str:
    """묶음을 마지막으로 잠근(갱신한) 날짜. git에 커밋된 그대로면 마지막 커밋 날짜 —
    pull·clone은 파일 시각을 받은 시각으로 바꿔서 시각만 보면 '갱신일'이 아니라 '받은 날'이 됨.
    아직 커밋 안 한 새 묶음(seal 직후)이거나 git 폴더가 아니면 파일 시각."""
    try:
        run = lambda *a: subprocess.run(["git", "-C", str(p.parent), *a, "--", p.name],
                                        capture_output=True, text=True, timeout=10)
        dirty = run("status", "--porcelain")
        last = run("log", "-1", "--format=%cs")
        if dirty.returncode == 0 and not dirty.stdout.strip() and last.returncode == 0 and last.stdout.strip():
            return last.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return f"{datetime.fromtimestamp(p.stat().st_mtime):%Y-%m-%d}"


def _offline(no: str, out: Path) -> NoticeFiles | None:
    """시험용: QUANT_OFFLINE_NOTICE_DIR의 파일을 그 공고 첨부로 씀(나라장터 안 부름)."""
    src = os.environ.get("QUANT_OFFLINE_NOTICE_DIR")
    if not src:
        return None
    out.mkdir(parents=True, exist_ok=True)
    files = []
    for p in sorted(Path(src).iterdir()):
        if p.is_file():
            shutil.copy(p, out / p.name)
            files.append(out / p.name)
    return NoticeFiles(no.upper(), "000", f"(오프라인 시험) {no}", "시험 기관", "용역", "협상에 의한 계약", "", "", [], files)


def posted_date(text: str) -> date | None:
    """나라장터 게시일시('2026-08-20 10:00:00'·'202608201000' 등) → 날짜."""
    digits = re.sub(r"\D", "", text or "")[:8]
    try:
        return datetime.strptime(digits, "%Y%m%d").date() if len(digits) == 8 else None
    except ValueError:
        return None


def _run_analyze(job: dict) -> None:
    try:
        job["step"] = "회사 자료 묶음 확인"
        note = _git_pull()
        # 회사 자료가 없어도 서식·자격 목록은 보여 줌(11/03 사용자 요청) — 보유 여부·만들기만 막음
        try:
            v, vault_err = vault_mod.load(), ""
        except vault_mod.VaultError as err:
            v, vault_err = vault_mod.Vault({}), str(err)
        job["vault_ok"] = not vault_err
        job["step"] = "나라장터에서 공고·첨부 받는 중"
        d = JOBS / job["id"] / "notice"
        nf = _offline(job["no"], d) or fetch_notice(job["no"], d)
        if not nf.title:
            raise RuntimeError("공고를 찾지 못했습니다 — 공고번호를 확인하세요")
        job["notice"] = {"no": nf.no, "ord": nf.ord, "title": nf.title, "institution": nf.institution,
                         "work": nf.work_type, "award": nf.award_method, "contract": nf.contract_method,
                         "posted": nf.posted, "licenses": nf.licenses, "license_groups": nf.license_groups, "files": [p.name for p in nf.files],
                         "skipped": nf.skipped, "negotiation": nf.negotiation}
        if not nf.files:
            raise RuntimeError("첨부 파일을 받지 못했습니다" + (f" — {nf.skipped[0]}" if nf.skipped else ""))
        nf.files, dropped = main_docs(nf.files)  # 공고문·제안요청서만 읽음(11/05 사용자 요청)
        job["notice"]["files"] = [p.name for p in nf.files]
        job["files"] = [str(p) for p in nf.files]
        # 실적 기간 기준일 = 공고 게시일(11/03 사용자 요청 — 입력칸 대신 자동). 못 읽으면 오늘
        job["date"] = posted_date(nf.posted)
        job["notice"]["ref_date"] = f"{job['date'] or date.today():%Y-%m-%d}"
        job["notice"]["ref_from"] = "공고 게시일" if job["date"] else "오늘(게시일을 못 읽음)"
        job["step"] = "서식·자격·실적 요건 읽는 중"
        a = analyze(nf.files, v, job.get("date"), JOBS / job["id"] / "work", nf.award_method, nf.contract_method, nf.licenses,
                     nf.license_groups)
        s = summary(a)
        if not s["forms"] and dropped:
            # 공고문·제안요청서에 서식이 없으면 나머지 첨부까지 읽어 서식을 찾음(11/05 사용자: 공고마다 서식이 다른 곳에 있음)
            job["step"] = "공고문·제안요청서에 서식이 없어 나머지 첨부에서 찾는 중"
            everything = nf.files + dropped
            a2 = analyze(everything, v, job.get("date"), JOBS / job["id"] / "work2", nf.award_method, nf.contract_method, nf.licenses,
                         nf.license_groups)
            s2 = summary(a2)
            if s2["forms"]:
                a, s = a2, s2
                found_in = sorted({f.source for f in a2.sheet.forms if f.source})
                s["notes"].append("공고문·제안요청서에 서식이 없어 나머지 첨부에서 서식을 찾음: " + ", ".join(found_in[:5]))
                nf.files, dropped = everything, []
                job["notice"]["files"] = [p.name for p in nf.files]
                job["files"] = [str(p) for p in nf.files]
        if note:
            s["notes"].insert(0, note)
        stale = _stale_note()
        if stale:
            s["notes"].insert(0, stale)
        if not s["forms"]:
            s["notes"].append("⚠ 첨부 어디에서도 서식을 찾지 못함 — 서식이 PDF 그림(스캔)이거나 '[서식 N]' 같은 표시가 없을 수 있음, 공고번호를 알려 주면 확인")
        if dropped:
            s["notes"].append("첨부 중 공고문·제안요청서·서식만 읽음 — 안 읽은 파일: " + ", ".join(p.name for p in dropped[:8])
                              + (" 외" if len(dropped) > 8 else ""))
        if vault_err:
            s["notes"].insert(0, f"회사 자료 없음 — 지일 보유 여부·정량평가서 만들기는 안 됨 ({vault_err})")
        job["summary"] = s
        job["state"] = "ready"
    except vault_mod.VaultError as err:
        job.update(state="error", error=str(err))
    except Exception as err:  # 화면에 이유를 보여 줌
        log.error("분석 실패 %s: %s", job.get("no"), traceback.format_exc())
        job.update(state="error", error=str(err))
    finally:
        job["step"] = ""


def _g2b_no(n: dict, fallback: str) -> str:
    """서식 공고번호 칸에 넣을 나라장터 입찰공고번호 — 차수까지('R26BK01806620-000', 나라장터 화면 표기)."""
    no = str(n.get("no") or fallback or "").strip()
    ord_ = str(n.get("ord") or "").strip()
    if no and "-" not in no and ord_:
        no = f"{no}-{ord_.zfill(3)}"
    return no


def _run_build(job: dict) -> None:
    with _build_lock:
        try:
            job["step"] = "서식 채우고 회사 서류 고르는 중"
            v = vault_mod.load()
            out = JOBS / job["id"] / "out" / f"정량평가서_{job['no']}.zip"
            n = job.get("notice", {})
            build([Path(f) for f in job["files"]], v, out, n.get("title", ""), job.get("date"),
                  n.get("award", ""), n.get("contract", ""), n.get("licenses") or [], _g2b_no(n, job["no"]),
                  job.get("staff_pick"), n.get("license_groups") or [])
            job["zip"] = str(out)
            job["state"] = "built"
        except Exception as err:
            log.error("만들기 실패 %s: %s", job.get("no"), traceback.format_exc())
            job.update(state="error", error=str(err))
        finally:
            job["step"] = ""


def _public(job: dict, base: str = "/api") -> dict:
    return {k: job.get(k) for k in ("id", "no", "state", "step", "error", "notice", "summary", "vault_ok")} | {
        "download": f"{base}/download/{job['id']}" if job.get("zip") else None}


def _j(obj, code: int = 200) -> tuple[int, str, bytes, dict]:
    return code, "application/json; charset=utf-8", json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8"), {}


def api(method: str, sub: str, body: dict | None = None, base: str = "/api") -> tuple[int, str, bytes, dict]:
    """API 한 번 — 이 웹앱(8766)과 프로젝트 1 웹앱(webapp/server.py의 /api/quant/…)이 같이 씀. 인증은 부르는 쪽이 함.
    sub: '/meta'·'/job/<id>'·'/download/<id>'·'/analyze'·'/build/<id>'. base: 내려받기 주소 앞부분. → (상태, 형식, 내용, 머리글)"""
    _cleanup()
    if method == "GET":
        if sub == "/meta":
            return _j({"vault": _vault_info(), "hwp": bool(vault_mod.read_env("HWP2HWPX_CMD"))})
        if sub.startswith("/job/"):
            job = _jobs.get(sub.rsplit("/", 1)[-1])
            return _j(_public(job, base)) if job else _j({"error": "작업이 없습니다(시간이 지나 지워졌을 수 있음)"}, 404)
        if sub.startswith("/download/"):
            job = _jobs.get(sub.rsplit("/", 1)[-1])
            if not job or not job.get("zip") or not Path(job["zip"]).exists():
                return _j({"error": "내려받을 파일이 없습니다"}, 404)
            name = Path(job["zip"]).name
            return 200, "application/zip", Path(job["zip"]).read_bytes(), {
                "Content-Disposition": f"attachment; filename=\"quantdocs.zip\"; filename*=UTF-8''{quote(name)}"}
        return _j({"error": "없는 주소"}, 404)
    body = body or {}
    if sub == "/analyze":
        no = str(body.get("no") or "").strip().upper()
        if not no or not all(ch.isalnum() or ch == "-" for ch in no) or len(no) > 20:
            return _j({"error": "공고번호 형식이 아닙니다(예: R26BK01790000)"}, 400)
        job = {"id": uuid.uuid4().hex[:12], "no": no, "date": None, "state": "running", "step": "시작"}
        with _lock:
            _jobs[job["id"]] = job
        threading.Thread(target=_run_analyze, args=(job,), daemon=True).start()
        return _j({"id": job["id"]})
    if sub.startswith("/build/"):
        job = _jobs.get(sub.rsplit("/", 1)[-1])
        if not job or job.get("state") not in ("ready", "built", "error") or not job.get("files"):
            return _j({"error": "먼저 공고를 불러오세요"}, 400)
        if not job.get("vault_ok"):
            return _j({"error": "회사 자료 묶음이 없어 만들 수 없습니다 — 회사 자료를 먼저 넣으세요"}, 400)
        pick = body.get("staff")  # 화면에서 고른 기술인력 이름(없으면 인력목록 전체)
        job["staff_pick"] = [str(x) for x in pick][:500] if isinstance(pick, list) else None
        job.update(state="building", error=None, zip=None)
        threading.Thread(target=_run_build, args=(job,), daemon=True).start()
        return _j({"id": job["id"]})
    return _j({"error": "없는 주소"}, 404)


class Server(ThreadingHTTPServer):
    """윈도우에선 SO_REUSEADDR가 같은 포트에 서버 두 개를 띄우게 해, 예전 서버가 계속 요청을 받는다
    (11/03 실측 — 새로 켰는데 화면이 옛 설정). 윈도우는 그 포트를 혼자 쓰게(이미 쓰는 중이면 바로 오류)."""

    allow_reuse_address = os.name != "nt"

    def server_bind(self):
        import socket
        if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


class Handler(BaseHTTPRequestHandler):
    server_version = "quantdocs"

    def log_message(self, fmt, *args):  # 접속 로그에 토큰이 남지 않게 경로만
        log.info("%s %s", self.command, urlparse(self.path).path)

    # ── 공통 ──
    def _authed(self) -> bool:
        q = parse_qs(urlparse(self.path).query)
        if q.get("t", [""])[0] == TOKEN:
            return True
        c = cookies.SimpleCookie(self.headers.get("Cookie", ""))
        return "qd_token" in c and secrets.compare_digest(c["qd_token"].value, TOKEN)

    def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8"), "application/json; charset=utf-8")

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        try:
            return json.loads(self.rfile.read(n) or b"{}") if 0 < n < 10_000 else {}
        except ValueError:
            return {}

    # ── GET ──
    def do_GET(self):  # noqa: N802
        _cleanup()
        path = urlparse(self.path).path
        if not self._authed():
            return self._send(403, "접속 토큰이 필요합니다 — 주소 끝에 ?t=토큰".encode(), "text/plain; charset=utf-8")
        q = parse_qs(urlparse(self.path).query)
        if path == "/":
            extra = {}
            if q.get("t"):
                extra["Set-Cookie"] = f"qd_token={TOKEN}; Path=/; HttpOnly; SameSite=Strict"
            return self._send(200, (STATIC / "index.html").read_bytes(), "text/html; charset=utf-8", extra)
        if path.startswith("/api/"):
            code, ctype, data, extra = api("GET", path[4:])
            return self._send(code, data, ctype, extra)
        return self._send(404, b"not found", "text/plain")

    # ── POST ──
    def do_POST(self):  # noqa: N802
        if not self._authed():
            return self._json({"error": "토큰"}, 403)
        path = urlparse(self.path).path
        if not path.startswith("/api/"):
            return self._json({"error": "없는 주소"}, 404)
        code, ctype, data, extra = api("POST", path[4:], self._body())
        return self._send(code, data, ctype, extra)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    host, port = os.environ.get("HOST", "0.0.0.0"), int(os.environ.get("PORT", "8766"))
    JOBS.mkdir(parents=True, exist_ok=True)
    _cleanup()
    print(f"정량평가서 준비 웹앱: http://<서버 PC 주소>:{port}/?t={TOKEN}")
    info = _vault_info()
    print(("✓ " if info["ok"] else "! ") + info["msg"])
    Server((host, port), Handler).serve_forever()


if __name__ == "__main__":
    main()
