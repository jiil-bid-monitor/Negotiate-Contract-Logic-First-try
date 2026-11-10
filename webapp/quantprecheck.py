"""새 협상 공고 정량평가서 미리 검사 — 수집된 공고를 담당자가 불러오기 전에 서버 PC가 먼저 검사하고, 규칙이 모르는 모양이면 개발자 방에 알림
(11/10 사용자 요청 — 서식 읽기 오류가 담당자 손에서야 드러나 반복됨).

- 대상: 최신 수집 결과의 본공고 후보 중 협상·규격가격동시입찰(정량평가 서식이 있는 유형). 사전규격·입찰은 뺌.
- 방법: 공고 첨부를 받아(`quantdocs.g2b.fetch_notice`) 공고문·제안요청서·서식 첨부만(`build.main_docs` — 화면과 같게, 서식이 없으면 나머지까지)
  `quantdocs.health.check`(서식 목록 ↔ 잘린 서식, 자기평가표·배점기준, 가짜 회사 정보로 채운 뒤 빈 칸·모르는 이름표).
- 알림: 문제가 있으면 개발자 방(TELEGRAM_DEV_CHAT_ID)에 공고마다 한 통. 알림 설정이 없으면 서버 기록(log)에만 남기고 다음에 다시 보내지 않음.
- 기록: data/quant_precheck.json(공고번호-차수 → 검사 시각·문제 건수). 같은 공고는 한 번만(차수가 바뀌면 다시). 받은 첨부·작업 폴더는 바로 지움.
- 서버 PC만(NAS(viewer)·QUANT_PRECHECK=0이면 끔), 수집·정량평가서 작업 중엔 쉬고, 한 번에 MAX_PER_ROUND건.
"""

from __future__ import annotations

import json
import logging
import shutil
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))
CATEGORIES = ("협상", "규격가격동시입찰")
MAX_PER_ROUND = 8
POLL_SECONDS = 600
MAX_TRIES = 3  # 검사 자체가 이만큼 실패하면 그 공고는 그만
KEEP_DAYS = 120  # 이보다 오래된 기록은 지움


def _now() -> datetime:
    return datetime.now(KST)


def targets(run: dict[str, Any] | None) -> list[dict[str, str]]:
    """수집 결과 → 검사할 공고(협상·규격가격 본공고 후보)."""
    out = []
    for c in (run or {}).get("candidates") or []:
        if c.get("category") not in CATEGORIES or (c.get("kind") or "본공고") != "본공고" or not c.get("notice_no"):
            continue
        out.append({"no": str(c["notice_no"]), "ord": str(c.get("notice_ord") or "000"), "title": c.get("title") or "",
                    "institution": c.get("demand_institution") or "", "category": c.get("category") or ""})
    return out


def alert_text(item: dict[str, str], h) -> str:
    from quantdocs.health import summary

    head = f"[정량평가서 미리 검사] {item['category']} {item['no']}-{item['ord']}\n{item['title']}"
    if item.get("institution"):
        head += f" ({item['institution']})"
    body = summary(h)
    tail = f"\n서식 목록 {len(h.listed)}개 · 잘린 서식 {len(h.cut)}개"
    if h.notes:
        tail += "\n참고: " + " / ".join(h.notes[:3])
    return f"{head}\n{body}{tail}\n→ 규칙 추가 필요(담당자가 불러오기 전에 확인)"


def check_notice(item: dict[str, str], work: Path, fetch=None) -> tuple[Any, list[str]]:
    """공고 하나 검사 → (Health, 받은 첨부 이름). fetch: 테스트용(공고번호, 폴더) → NoticeFiles."""
    from quantdocs import health
    from quantdocs.build import main_docs

    if fetch is None:
        from quantdocs.g2b import fetch_notice as fetch
    nf = fetch(f"{item['no']}-{item['ord']}", work / "files")
    files = [Path(f) for f in nf.files]
    main, rest = main_docs(files)
    h = health.check(main, work / "check", title=nf.title or item["title"], g2b_no=f"{item['no']}-{item['ord']}")
    if rest and not h.listed and not h.cut:  # 서식이 다른 첨부에 있는 공고 — 화면처럼 나머지까지
        h = health.check(files, work / "check2", title=nf.title or item["title"], g2b_no=f"{item['no']}-{item['ord']}")
    if not files:
        h.notes.append("첨부를 받지 못함" + (f": {nf.skipped[0]}" if nf.skipped else ""))
    return h, [f.name for f in files]


class Precheck:
    def __init__(self, data_dir: Path, latest_run: Callable[[], dict | None], busy: Callable[[], bool],
                 send: Callable[[str], list[str] | None] | None = None, fetch=None):
        self.path = Path(data_dir) / "quant_precheck.json"
        self.latest_run = latest_run
        self.busy = busy
        self.fetch = fetch
        if send is None:
            from nego.telegram import send_dev_alert as send
        self.send = send
        self._lock = threading.Lock()

    def _load(self) -> dict[str, Any]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"done": {}}

    def _save(self, state: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.path)

    def run_once(self, limit: int = MAX_PER_ROUND) -> list[dict[str, Any]]:
        """새 공고를 최대 limit건 검사. 돌려줌: 이번에 검사한 결과 요약."""
        with self._lock:
            state = self._load()
            done: dict[str, Any] = state.setdefault("done", {})
            cutoff = (_now() - timedelta(days=KEEP_DAYS)).isoformat()
            for k in [k for k, v in done.items() if (v.get("at") or "") < cutoff]:
                del done[k]
            out = []
            for item in targets(self.latest_run()):
                key = f"{item['no']}-{item['ord']}"
                if key in done:
                    continue
                if len(out) >= limit or self.busy():
                    break
                work = Path(tempfile.mkdtemp(prefix="quantpre_"))
                try:
                    h, names = check_notice(item, work, self.fetch)
                    rec = {"at": _now().isoformat(timespec="seconds"), "title": item["title"], "issues": h.counts(),
                           "files": names[:10], "notes": h.notes[:5]}
                    if h.issues:
                        errors = self.send(alert_text(item, h))
                        rec["alert"] = "보냄" if errors == [] else ("알림 설정 없음" if errors is None else "실패: " + "; ".join(errors)[:200])
                        log.warning("정량평가서 미리 검사 %s — 문제 %s (%s)", key, h.counts(), rec["alert"])
                except Exception as err:  # noqa: BLE001 — 한 공고 실패로 멈추지 않게(나라장터 접속 오류 등은 다음에 다시, MAX_TRIES번까지)
                    log.warning("정량평가서 미리 검사 %s 실패: %s", key, err)
                    failed = state.setdefault("failed", {})
                    failed[key] = failed.get(key, 0) + 1
                    if failed[key] >= MAX_TRIES:
                        done[key] = {"at": _now().isoformat(timespec="seconds"), "title": item["title"], "error": str(err)[:200]}
                        del failed[key]
                    self._save(state)
                    out.append({"key": key, "error": str(err)[:200]})
                    continue
                finally:
                    shutil.rmtree(work, ignore_errors=True)
                done[key] = rec
                out.append({"key": key, **rec})
                self._save(state)
            state["lastRun"] = _now().isoformat(timespec="seconds")
            self._save(state)
            return out

    def start(self, poll_seconds: int = POLL_SECONDS) -> None:
        stop = threading.Event()

        def loop() -> None:
            if stop.wait(90):
                return
            while True:
                try:
                    if not self.busy():
                        self.run_once()
                except Exception:  # noqa: BLE001 — 스레드가 죽지 않게
                    log.exception("정량평가서 미리 검사 오류")
                if stop.wait(poll_seconds):
                    return

        threading.Thread(target=loop, name="quant-precheck", daemon=True).start()
