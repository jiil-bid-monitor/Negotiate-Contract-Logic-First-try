"""매일 밤 투찰 이력 자동 수집 (2026-11-06 사용자 요청 — 11/05 대표님 미팅 ② "개찰 결과로 투찰 이력을 계속 모아 경향 보정").

웹앱이 켜져 있으면 매일 22시(KST)에 한 번 아래를 차례로 한다. 22시에 꺼져 있었으면 다음 날 켤 때 한 번 따라잡는다.

1. **투찰내역 결과** — 지일이 기록한 공고의 개찰 결과(순위·1순위·실제 사정률)를 채운다(`webapp/bidresult.fill_results`).
2. **입찰 공고 개찰 결과 모으기**(사용자 결정 (나) — 웹앱이 수집한 입찰 공고 전부) — 수집 결과의 입찰 유형 후보와 투찰내역 공고를
   '지켜볼 공고'(`data/bid_nightly.json`)에 쌓아 두고, 개찰일이 지난 공고의 참가업체 전체·예비가격을 받아
   과거 개찰 폴더(`BID_HISTORY_DIR`, 없으면 `OUTPUT_DIR`·output)의 `bid_results_live.jsonl`에 한 줄씩 더한다.
   추천 사정률·투찰 경향(`nego/bidrecommend.py`)이 이 폴더의 `bid_results*.jsonl`을 다 읽으므로 **다음 추천 계산부터 저절로 반영** —
   방식·기준값(통과 60%·1순위 10% 등)은 바꾸지 않는다(10/29 결정: 실전 기록이 쌓이기 전엔 규칙을 손대지 않음).
3. **투찰 이력 엑셀** — `BID_ARCHIVE_DIR`(없으면 output/투찰이력) 폴더의 `투찰이력.xlsx`를 새로 쓴다(투찰내역·개찰결과·대기 중 시트).
   엑셀에서 열어 둔 채면 덮어쓸 수 없어 날짜 붙인 이름으로 따로 저장한다.
4. **주간 성적표** — 월요일(또는 지난 성적표가 7일 넘었을 때) 같은 폴더 `성적표/성적표_날짜.txt`.

나라장터 호출은 하룻밤 최대 `MAX_FETCH`건, 하루 한도(resultCode=22)에 걸리면 멈추고 다음 날 이어서. 개찰일이 지나고
`GIVE_UP_DAYS`일이 넘도록 결과가 없으면(유찰·취소 등) '결과 없음'으로 그만 본다. 이 파일들은 모두 서버 PC에만 — 공개 저장소 X.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import os
import re
import statistics
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))
EVERY = timedelta(hours=1)        # 22시 한 번 → 1시간마다(11/12 사용자 결정 — 서버 PC는 22시에 꺼져 있음)
RETRY_AFTER = timedelta(hours=20)  # 결과가 아직 없는 공고는 하루에 한 번만 다시 물음(1시간마다 물으면 나라장터 하루 한도)
MAX_FETCH = 150          # 하룻밤 개찰 결과를 받는 공고 수(공고마다 나라장터를 4~5번 부름)
GIVE_UP_DAYS = 21        # 개찰일 뒤 이만큼 지나도 결과가 없으면 그만 봄
WATCH_KEEP_DAYS = 120    # 끝난(완료·결과 없음) 공고를 지켜볼 목록에 남겨 두는 기간
LIVE_FILE = "bid_results_live.jsonl"
STATE_FILE = "bid_nightly.json"
EXCEL_NAME = "투찰이력.xlsx"
ROOT = Path(__file__).resolve().parents[1]


def _now() -> datetime:
    return datetime.now(KST)


def _digits(text: Any) -> str:
    return re.sub(r"\D", "", str(text or ""))


def _parse_dt(text: Any) -> datetime | None:
    if not text:
        return None
    s = str(text).strip().replace("/", "-")
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(s[:25] if "%z" in fmt else s[:19], fmt)
            return dt if dt.tzinfo else dt.replace(tzinfo=KST)
        except ValueError:
            continue
    try:
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=KST)
    except ValueError:
        return None


def default_dirs() -> tuple[Path, Path]:
    """(과거 개찰 폴더, 투찰 이력 엑셀·성적표 폴더)."""
    history = Path(os.environ.get("BID_HISTORY_DIR") or os.environ.get("OUTPUT_DIR") or "output")
    archive = Path(os.environ["BID_ARCHIVE_DIR"].strip()) if os.environ.get("BID_ARCHIVE_DIR", "").strip() else history / "투찰이력"
    return history, archive


def _load_fetch_one() -> Callable[..., dict]:
    """scripts/fetch_bid_results.py의 fetch_one — 손으로 받던 개찰 결과와 같은 모양(추천이 그대로 읽음)."""
    path = ROOT / "scripts" / "fetch_bid_results.py"
    spec = importlib.util.spec_from_file_location("fetch_bid_results", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod.fetch_one


# ── 지켜볼 공고 ─────────────────────────────────────────────

def watch_items_from_run(run: dict[str, Any] | None) -> list[dict[str, Any]]:
    """수집 결과 → 입찰 유형 본공고 후보(개찰일은 일정의 '개찰', 없으면 가장 늦은 마감)."""
    out = []
    for c in (run or {}).get("candidates") or []:
        if c.get("category") != "입찰" or (c.get("kind") or "본공고") != "본공고" or not c.get("notice_no"):
            continue
        dl = c.get("deadlines") or []
        opening = next((d for label, d in dl if "개찰" in str(label) and d), None)
        if not opening:
            dates = sorted(d for _, d in dl if d)
            opening = dates[-1] if dates else c.get("deadline")
        out.append({"no": c["notice_no"], "ord": c.get("notice_ord") or "000", "work": c.get("work_type") or "",
                    "title": c.get("title") or "", "institution": c.get("demand_institution") or "",
                    "opening": opening, "source": "후보"})
    return out


def watch_items_from_bidlog(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """투찰내역 공고 — 개찰일을 따로 모르므로 기록한 날 다음 날부터 받아 봄."""
    out = []
    for e in entries:
        if not e.get("noticeNo"):
            continue
        rec = _parse_dt(e.get("recordedAt")) or _now()
        out.append({"no": e["noticeNo"], "ord": e.get("noticeOrd") or "000", "work": e.get("workType") or "",
                    "title": e.get("title") or "", "institution": e.get("institution") or "",
                    "opening": (rec + timedelta(days=1)).isoformat(timespec="seconds"), "source": "투찰"})
    return out


def archived_numbers(history_dir: Path) -> set[str]:
    """과거 개찰 폴더의 jsonl에 이미 있는(참가업체까지 받은) 공고번호 — 같은 공고를 두 번 받지 않게."""
    done: set[str] = set()
    if not history_dir.exists():
        return done
    for path in sorted(history_dir.glob("bid_results*.jsonl")):
        try:
            with path.open(encoding="utf-8") as f:
                for line in f:
                    try:
                        rec = json.loads(line)
                    except ValueError:
                        continue
                    if not rec.get("error") and rec.get("bidders"):
                        done.add(str(rec.get("no") or rec.get("input", "").split("-")[0]))
        except OSError:
            continue
    return done


def read_live(history_dir: Path) -> list[dict[str, Any]]:
    path = history_dir / LIVE_FILE
    out = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


def summarize_record(rec: dict[str, Any], own_bizno: str = "") -> dict[str, Any]:
    """개찰 결과 1줄 → 엑셀 '개찰결과' 시트 한 줄."""
    from nego.bidprice import parse_bidders, parse_prepare_prices

    bidders = parse_bidders(rec.get("bidders") or [])
    planned, basis, _ = parse_prepare_prices(rec.get("prices") or [])
    info = rec.get("notice") or {}
    winner = next((b for b in bidders if b.rank == 1), None)
    own_digits = _digits(own_bizno)
    own = next((b for b in bidders if own_digits and _digits(b.bizno) == own_digits), None)
    rate = round(planned / basis * 100, 4) if planned and basis else None
    lower = info.get("lower_rate")
    try:
        lower = float(lower) if lower not in (None, "") else None
    except ValueError:
        lower = None
    return {
        "opened": str(info.get("opened_at") or rec.get("opened_at") or "")[:16],
        "no": f"{rec.get('no', '')}-{rec.get('ord') or '000'}", "title": info.get("title") or rec.get("title") or "",
        "institution": info.get("institution") or rec.get("institution") or "", "work": rec.get("work") or "", "source": rec.get("source") or "",
        "bidders": len(bidders), "basis": basis, "planned": planned, "rate": rate, "lower": lower,
        "winner": winner.name if winner else "", "winnerAmount": winner.amount if winner else None,
        "winnerRate": winner.rate if winner else None,
        "winnerGap": round(winner.rate - lower, 4) if winner and winner.rate is not None and lower is not None else None,
        "ownRank": (own.rank if own.rank is not None else "순위 없음") if own else "",
        "fetched": str(rec.get("fetched_at") or "")[:16].replace("T", " "),
    }


# ── 밤 작업 ─────────────────────────────────────────────────

class Nightly:
    def __init__(self, data_dir: Path, bidlog, latest_run: Callable[[], dict | None],
                 make_client: Callable[[], tuple[Any, str] | None], own_bizno: str,
                 history_dir: Path | None = None, archive_dir: Path | None = None,
                 fetch_one: Callable[..., dict] | None = None, fill_results: Callable[..., dict] | None = None, postaward=None,
                 before: Callable[[], Any] | None = None,
                 now: Callable[[], datetime] = _now):
        h, a = default_dirs()
        self.state_path = Path(data_dir) / STATE_FILE
        self.bidlog = bidlog
        self.latest_run = latest_run
        self.make_client = make_client
        self.own_bizno = own_bizno
        self.history_dir = Path(history_dir or h)
        self.archive_dir = Path(archive_dir or a)
        self._fetch_one = fetch_one
        self._fill_results = fill_results
        self.before = before  # 밤 작업 직전에 부름 — 서버 PC 투찰 기록 받아 오기(webapp/bidlogpull.py)
        self.postaward = postaward  # 낙찰 후 서류 목록(webapp/postaward.py) — 투찰 기록을 올리고 낙찰자 확인
        self.now = now
        self._lock = threading.Lock()

    # 상태 파일
    def state(self) -> dict[str, Any]:
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self, state: dict[str, Any]) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.state_path)

    def due(self, now: datetime | None = None) -> bool:
        """마지막으로 돈 지 1시간이 지났으면(처음이면 바로)."""
        now = now or self.now()
        last = _parse_dt(self.state().get("lastRunAt"))
        return last is None or now - last >= EVERY

    def update_watch(self, state: dict[str, Any], now: datetime) -> int:
        watch: dict[str, dict] = state.setdefault("watch", {})
        added = 0
        items = watch_items_from_run(self.latest_run()) + watch_items_from_bidlog(self.bidlog.entries())
        for it in items:
            key = f"{it['no']}-{it['ord']}"
            cur = watch.get(key)
            if cur is None:
                watch[key] = {**it, "status": "대기", "tries": 0, "added": now.isoformat(timespec="seconds")}
                added += 1
            else:
                if it["source"] == "투찰":
                    cur["source"] = "투찰"
                if it.get("opening") and cur.get("status") == "대기" and it["source"] == "후보":
                    cur["opening"] = it["opening"]  # 정정공고로 개찰일이 바뀐 것
        # 오래 전에 끝난 공고는 목록에서 뺌(파일이 끝없이 커지지 않게)
        cutoff = now - timedelta(days=WATCH_KEEP_DAYS)
        for key in [k for k, v in watch.items() if v.get("status") != "대기"
                    and (_parse_dt(v.get("doneAt")) or now) < cutoff]:
            del watch[key]
        return added

    def collect(self, state: dict[str, Any], client: Any, base_url: str, now: datetime) -> dict[str, int]:
        """개찰일이 지난 대기 공고의 개찰 결과를 받아 bid_results_live.jsonl에 더한다."""
        from nego.http_client import ApiError, ApiResultError

        fetch_one = self._fetch_one or _load_fetch_one()
        done = archived_numbers(self.history_dir)
        watch = state.setdefault("watch", {})
        ready = sorted((v for v in watch.values() if v.get("status") == "대기"
                        and (_parse_dt(v.get("opening")) or now) <= now),
                       key=lambda v: (v.get("source") != "투찰", v.get("opening") or ""))
        got = none = waiting = 0
        quota = False
        self.history_dir.mkdir(parents=True, exist_ok=True)
        with (self.history_dir / LIVE_FILE).open("a", encoding="utf-8") as out:
            for v in ready:
                if v["no"] in done:
                    v.update(status="완료", doneAt=now.isoformat(timespec="seconds"))
                    continue
                if got + waiting + none >= MAX_FETCH:
                    break
                tried = _parse_dt(v.get("lastTry"))
                if tried and now - tried < RETRY_AFTER:
                    continue
                try:
                    rec = fetch_one(client, base_url, f"{v['no']}-{v['ord']}", v.get("work") or "")
                except ApiResultError as err:
                    if getattr(err, "result_code", "") == "22":
                        quota = True
                        break
                    rec = {"error": str(err)[:200]}
                except ApiError as err:
                    rec = {"error": str(err)[:200]}
                v["tries"] = int(v.get("tries") or 0) + 1
                v["lastTry"] = now.isoformat(timespec="seconds")
                if not rec.get("error") and rec.get("bidders"):
                    rec["source"] = v.get("source") or ""
                    rec["title"] = v.get("title") or ""
                    rec["institution"] = v.get("institution") or ""
                    out.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    out.flush()
                    done.add(v["no"])
                    v.update(status="완료", doneAt=now.isoformat(timespec="seconds"))
                    got += 1
                    continue
                opening = _parse_dt(v.get("opening")) or now
                if now - opening > timedelta(days=GIVE_UP_DAYS):
                    v.update(status="결과 없음", doneAt=now.isoformat(timespec="seconds"),
                             why=str(rec.get("error") or "참가업체 없음")[:120])
                    none += 1
                else:
                    waiting += 1
        return {"fetched": got, "gaveUp": none, "notYet": waiting, "quota": int(quota)}

    def fill_bidlog(self, client: Any, base_url: str) -> int:
        fill = self._fill_results
        if fill is None:
            from .bidresult import fill_results as fill
        total = 0
        for _ in range(10):  # 한 번에 8건씩 — 남으면 이어서(최대 80건)
            done = fill(self.bidlog, client, base_url, self.own_bizno)
            total += done.get("fetched", 0)
            if not done.get("left") or not done.get("fetched"):
                break
        return total

    # 엑셀
    def write_excel(self, state: dict[str, Any], now: datetime) -> Path:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font, PatternFill
        from openpyxl.utils import get_column_letter

        wb = Workbook()
        head_font, head_fill = Font(bold=True, color="FFFFFF"), PatternFill("solid", fgColor="1B6E43")

        def sheet(ws, head: list[str], rows: list[list[Any]], widths: dict[int, int] | None = None) -> None:
            ws.append(head)
            for cell in ws[1]:
                cell.font, cell.fill = head_font, head_fill
                cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            for row in rows:
                ws.append(["" if v is None else v for v in row])
            ws.freeze_panes = "A2"
            for i, h in enumerate(head, 1):
                ws.column_dimensions[get_column_letter(i)].width = (widths or {}).get(i, max(10, min(40, len(h) * 2 + 2)))
            for row in ws.iter_rows(min_row=2):
                for cell in row:
                    if isinstance(cell.value, (int, float)) and not isinstance(cell.value, bool):
                        cell.number_format = "#,##0" if abs(cell.value) >= 1000 or float(cell.value).is_integer() else "0.0###"

        head, rows = self.bidlog.table()
        rows = [[str(r[0] or "")[:16].replace("T", " "), *r[1:]] for r in rows]  # 기록일시 '2026-11-06 10:59'
        ws = wb.active
        ws.title = "투찰내역"
        sheet(ws, head, rows, {1: 20, 4: 40, 5: 20})

        live = [summarize_record(r, self.own_bizno) for r in read_live(self.history_dir)]
        live.sort(key=lambda x: x["opened"], reverse=True)
        cols = [("opened", "개찰일시"), ("no", "공고번호"), ("title", "공고명"), ("institution", "발주처"), ("work", "업무"),
                ("source", "출처(후보/투찰)"), ("bidders", "참가 업체 수"), ("basis", "기초금액"), ("planned", "예정가격"),
                ("rate", "실제 사정률(%)"), ("lower", "하한율(%)"), ("winner", "1순위 업체"), ("winnerAmount", "1순위 투찰금액"),
                ("winnerRate", "1순위 투찰률(%)"), ("winnerGap", "1순위 투찰률 − 하한율(%p)"), ("ownRank", "지일 순위"), ("fetched", "받은 날")]
        sheet(wb.create_sheet("개찰결과"), [c[1] for c in cols], [[x[k] for k, _ in cols] for x in live], {1: 17, 2: 18, 3: 40, 4: 20, 12: 20})

        wait = sorted((v for v in (state.get("watch") or {}).values() if v.get("status") != "완료"),
                      key=lambda v: (v.get("status") != "대기", v.get("opening") or ""))
        sheet(wb.create_sheet("대기 중"), ["상태", "개찰 예정", "공고번호", "공고명", "발주처", "업무", "출처", "시도 횟수", "비고"],
              [[v.get("status"), str(v.get("opening") or "")[:16].replace("T", " "), f"{v['no']}-{v.get('ord') or '000'}",
                v.get("title"), v.get("institution"), v.get("work"), v.get("source"), v.get("tries"), v.get("why", "")] for v in wait],
              {2: 17, 3: 18, 4: 40, 5: 20, 9: 30})

        self.archive_dir.mkdir(parents=True, exist_ok=True)
        path = self.archive_dir / EXCEL_NAME
        try:
            wb.save(path)
        except PermissionError:  # 엑셀에서 열어 둔 채면 덮어쓸 수 없음 — 따로 저장
            path = self.archive_dir / f"투찰이력_{now:%Y%m%d-%H%M}.xlsx"
            wb.save(path)
            log.warning("투찰이력.xlsx가 열려 있어 %s로 저장했습니다", path.name)
        return path

    # 주간 성적표
    def report_due(self, state: dict[str, Any], now: datetime) -> bool:
        last = state.get("lastReport")
        if not last:
            return now.weekday() == 0
        if last == now.date().isoformat():
            return False
        return now.weekday() == 0 or now.date() - date.fromisoformat(last) >= timedelta(days=7)

    def weekly_report(self, state: dict[str, Any], now: datetime) -> Path:
        text = build_report(self.bidlog.entries(), read_live(self.history_dir), state, now, self.own_bizno)
        folder = self.archive_dir / "성적표"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"성적표_{now:%Y-%m-%d}.txt"
        path.write_text(text, encoding="utf-8-sig")
        return path

    def run_once(self, now: datetime | None = None) -> dict[str, Any]:
        now = now or self.now()
        if self.before is not None:
            try:
                self.before()
            except Exception:  # noqa: BLE001 — 받아 오기가 실패해도 밤 작업은 함
                log.exception("밤 작업 전 준비 실패")
        with self._lock:
            state = self.state()
            out: dict[str, Any] = {"at": now.isoformat(timespec="seconds")}
            out["watchAdded"] = self.update_watch(state, now)
            made = self.make_client()
            if made is None:
                out["error"] = "나라장터 인증키 없음 — 결과 받기는 건너뜀"
            else:
                client, base = made
                try:
                    out["bidlog"] = self.fill_bidlog(client, base)
                    if self.postaward is not None:
                        self.postaward.sync_bidlog()
                        out["postaward"] = self.postaward.check(client, base)
                    out.update(self.collect(state, client, base, now))
                except Exception as err:  # noqa: BLE001 — 받기가 실패해도 엑셀·성적표는 씀
                    log.exception("개찰 결과 받기 실패")
                    out["error"] = str(err)[:200]
            try:
                out["excel"] = str(self.write_excel(state, now))
                if self.report_due(state, now):
                    out["report"] = str(self.weekly_report(state, now))
                    state["lastReport"] = now.date().isoformat()
            except Exception as err:  # noqa: BLE001
                log.exception("투찰 이력 엑셀·성적표 쓰기 실패")
                out["error"] = (out.get("error", "") + " / " + str(err)[:200]).strip(" /")
            state["lastRun"] = now.date().isoformat()
            state["lastRunAt"] = now.isoformat(timespec="seconds")
            state["lastResult"] = out
            self._save(state)
        log.info("투찰 이력 자동 수집: %s", out)
        return out

    def start(self, poll_seconds: int = 60) -> None:
        stop = threading.Event()

        def loop() -> None:
            if stop.wait(30):
                return
            while True:
                try:
                    if self.due():
                        self.run_once()
                except Exception:  # noqa: BLE001 — 스레드가 죽지 않게
                    log.exception("투찰 이력 자동 수집 오류")
                if stop.wait(poll_seconds):
                    return

        threading.Thread(target=loop, name="bid-nightly", daemon=True).start()


# ── 성적표 글 (11/06 사용자 요청: 총 투찰 건수·투찰건별 등수·보정 방향만) ─────────────

def _rank_text(e: dict[str, Any]) -> str:
    r = e.get("result") or {}
    if r.get("status") != "완료":
        return r.get("status") or "개찰 전"
    own = r.get("own")
    if not own:
        return f"참가 기록 없음 ({r.get('bidderCount', '-')}곳)"
    if own.get("rank") is None:
        return f"하한 미달 ({r.get('bidderCount', '-')}곳)"
    return f"{own['rank']}위 / {r.get('bidderCount', '-')}곳"


def direction(entries: list[dict[str, Any]]) -> list[str]:
    """개찰된 지일 투찰로 본 보정 방향 — 미달이 많으면 높이는 쪽, 통과했는데 1위보다 비쌌으면 낮추는 쪽."""
    done = [e["result"] for e in entries if (e.get("result") or {}).get("status") == "완료" and e["result"].get("own")]
    if not done:
        return ["아직 개찰된 투찰이 없어 판단할 수 없음"]
    under = [r for r in done if r["own"].get("rank") is None]
    passed = [r for r in done if r["own"].get("rank") is not None]
    dear = [r for r in passed if r["own"]["rank"] > 1 and (r.get("gapToWinner") or 0) > 0]
    diffs = [r["rateDiff"] for r in done if r.get("rateDiff") is not None]
    mid = f" (기준 사정률이 실제보다 가운데값 {statistics.median(diffs):+.3f}%p)" if diffs else ""
    lines = [f"개찰 {len(done)}건 중 하한선 통과 {len(passed)}건 · 미달 {len(under)}건 · 1순위 {sum(1 for r in passed if r['own']['rank'] == 1)}건{mid}"]
    if len(under) > len(passed):
        lines.append("→ 기준 사정률을 높이는 쪽: 실제 사정률보다 낮게 잡아 하한선에 못 미친 투찰이 더 많음")
    elif passed and len(dear) * 2 > len(passed):
        gaps = [r["gapToWinner"] for r in dear]
        lines.append(f"→ 기준 사정률을 조금 낮추는 쪽: 통과한 투찰 대부분이 1위보다 비쌈(1위와 차이 가운데값 {statistics.median(gaps):,.0f}원)")
    else:
        lines.append("→ 지금 수준 유지: 미달과 비싼 투찰이 한쪽으로 몰리지 않음")
    return lines


def build_report(entries: list[dict[str, Any]], live: list[dict[str, Any]], state: dict[str, Any], now: datetime,
                 own_bizno: str = "") -> str:
    week = [e for e in entries if (_parse_dt(e.get("recordedAt")) or now) >= now - timedelta(days=7)]
    lines = [f"투찰 성적표 — {now:%Y-%m-%d}", "",
             f"■ 총 투찰 {len(entries)}건 (이번 주 {len(week)}건)", "", "■ 투찰건별 등수"]
    for e in entries:
        lines.append(f"  · {str(e.get('recordedAt') or '')[:10]}  {(e.get('title') or e.get('noticeNo') or '')[:30]}  —  {_rank_text(e)}")
    if not entries:
        lines.append("  (없음)")
    lines += ["", "■ 투찰 경향 보정 방향", *[f"  {x}" for x in direction(entries)],
              f"  ※ 추천 칸은 모은 개찰 결과 {len(live)}건을 다음 계산부터 자동으로 씀(규칙은 그대로)"]
    return "\n".join(lines) + "\n"
