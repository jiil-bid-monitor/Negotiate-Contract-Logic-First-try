"""텔레그램 명령어 (2026-11-09 사용자 결정 — NAS 웹앱 안에서, 늘 켜져 있게).

/notice 공고번호 — 최신 수집 결과에서 그 공고의 금액·마감·자격·싱크로율
/deadline [N]   — N일(기본 3일) 안에 마감하는 후보 공고
/help           — 명령어 목록

`TELEGRAM_COMMANDS=1`인 곳에서만 켠다 — 봇 하나를 두 곳에서 받으면(getUpdates) 텔레그램이 409로 막으므로
서버 PC와 NAS 중 NAS만. 답은 TELEGRAM_CHAT_ID·TELEGRAM_DEV_CHAT_ID 대화방에만(다른 방·개인 대화는 무시).
아침 보고는 그대로 GitHub Actions가 보낸다(보내기만 하므로 충돌 없음).
"""

from __future__ import annotations

import html
import logging
import os
import re
import threading
import time
from datetime import datetime
from typing import Any, Callable

import requests

from nego.telegram import API_BASE, _short_money

log = logging.getLogger(__name__)

COMMANDS = [("notice", "공고번호로 공고 요약 — /notice R26BK01234567"),
            ("deadline", "N일 안에 마감하는 공고 — /deadline 3"),
            ("help", "명령어 목록")]
_NO = re.compile(r"[A-Z0-9]{10,14}", re.I)


def _dday(iso: str | None, now: datetime) -> int | None:
    if not iso:
        return None
    try:
        return (datetime.fromisoformat(iso).date() - now.date()).days
    except ValueError:
        return None


def _title(c: dict) -> str:
    title = html.escape(c.get("title") or "(공고명 없음)")
    url = c.get("detail_url")
    return f'<a href="{html.escape(url, quote=True)}">{title}</a>' if url else title


def _qual(c: dict) -> str:
    q = c.get("qualification") or {}
    if not q.get("checked"):
        return "자격 미확인"
    if not q.get("missing"):
        return "자격 충족"
    return "자격 미달: " + ", ".join(q["missing"][:5])


def _days_text(d: int | None) -> str:
    return "마감 미상" if d is None else ("마감됨" if d < 0 else f"D-{d}")


def notice_reply(run: dict | None, arg: str, now: datetime) -> str:
    m = _NO.search(arg or "")
    if not m:
        return "공고번호를 함께 적어 주세요. 예) /notice R26BK01234567"
    no = m.group(0).upper()
    if not run:
        return "아직 받은 수집 결과가 없습니다."
    for c in run.get("candidates") or []:
        if (c.get("notice_no") or "").upper() == no:
            sync = c.get("sync") or {}
            score = sync.get("score")
            lines = [_title(c),
                     f"{html.escape(c.get('demand_institution') or '')} · {html.escape(c.get('category') or c.get('kind') or '')}",
                     f"{_short_money(c.get('budget'), c.get('budget_kind'))} · {_days_text(_dday(c.get('deadline'), now))}"
                     + (f" ({html.escape(c['deadline'].replace('T', ' '))} {html.escape(c.get('deadline_label') or '')})"
                        if c.get("deadline") else ""),
                     html.escape(_qual(c)),
                     "싱크로율 " + (f"{score * 100:.0f}% ({html.escape(sync.get('level') or '')})" if score is not None
                                  else html.escape(sync.get("level") or "판정 불가"))]
            return "\n".join(lines)
    for c in run.get("rejected") or []:
        if (c.get("notice_no") or "").upper() == no:
            return f"{_title(c)}\n제외된 공고 — {html.escape(c.get('excluded_reason') or '사유 없음')}"
    return f"{no}: 최신 수집 결과에 없는 공고입니다 (수집 기간 밖이거나 마감 지난 공고)."


def deadline_reply(run: dict | None, arg: str, now: datetime) -> str:
    days = int(arg) if (arg or "").strip().isdigit() else 3
    if not run:
        return "아직 받은 수집 결과가 없습니다."
    rows = []
    for c in run.get("candidates") or []:
        d = _dday(c.get("deadline"), now)
        if d is not None and 0 <= d <= days:
            rows.append((d, c))
    if not rows:
        return f"{days}일 안에 마감하는 후보 공고가 없습니다."
    rows.sort(key=lambda r: (r[0], r[1].get("deadline") or ""))
    out = [f"<b>{days}일 안에 마감 {len(rows)}건</b>"]
    for i, (d, c) in enumerate(rows[:30], 1):
        out.append(f"{i}. {_title(c)}\n   {_short_money(c.get('budget'), c.get('budget_kind'))} · {_days_text(d)} · "
                   f"{html.escape(_qual(c))}")
    if len(rows) > 30:
        out.append(f"… 외 {len(rows) - 30}건은 웹앱에서")
    return "\n".join(out)


def reply(text: str, run: dict | None, now: datetime) -> str | None:
    """명령어 한 줄 → 답(명령어가 아니면 None). 단체방에선 '/notice@봇이름'으로 온다."""
    m = re.match(r"^/(\w+)(?:@\w+)?\s*(.*)$", (text or "").strip(), re.S)
    if not m:
        return None
    cmd, arg = m.group(1).lower(), m.group(2)
    if cmd == "notice":
        return notice_reply(run, arg, now)
    if cmd == "deadline":
        return deadline_reply(run, arg, now)
    if cmd in ("help", "start"):
        return "\n".join(f"/{name} — {desc}" for name, desc in COMMANDS)
    return None


class TelegramBot:
    def __init__(self, token: str, chats: set[str], latest_run: Callable[[], dict | None],
                 session: requests.Session | None = None):
        self.token = token
        self.chats = chats
        self.latest_run = latest_run
        self.session = session or requests.Session()
        self.offset = 0

    @classmethod
    def from_env(cls, latest_run: Callable[[], dict | None]) -> "TelegramBot | None":
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        chats = {c.strip() for key in ("TELEGRAM_CHAT_ID", "TELEGRAM_DEV_CHAT_ID")
                 for c in os.environ.get(key, "").split(",") if c.strip()}
        if os.environ.get("TELEGRAM_COMMANDS", "").strip() != "1" or not token or not chats:
            return None
        return cls(token, chats, latest_run)

    def _api(self, method: str, http_timeout: float = 30, **body: Any) -> Any:
        res = self.session.post(f"{API_BASE}/bot{self.token}/{method}", json=body, timeout=http_timeout)
        data = res.json()
        if not data.get("ok"):
            raise RuntimeError(f"{method}: {data.get('description')}")
        return data.get("result")

    def poll_once(self, wait: int = 50) -> int:
        """한 번 받아 답한다. 답한 수를 돌려준다."""
        updates = self._api("getUpdates", http_timeout=wait + 15, offset=self.offset, timeout=wait,
                            allowed_updates=["message"])
        answered = 0
        for u in updates or []:
            self.offset = max(self.offset, u["update_id"] + 1)
            msg = u.get("message") or {}
            chat = str((msg.get("chat") or {}).get("id", ""))
            if chat not in self.chats:
                continue
            text = reply(msg.get("text") or "", self.latest_run(), datetime.now())
            if text:
                self._api("sendMessage", chat_id=chat, text=text, parse_mode="HTML",
                          disable_web_page_preview=True, reply_to_message_id=msg.get("message_id"))
                answered += 1
        return answered

    def start(self) -> None:
        def loop() -> None:
            try:
                self._api("setMyCommands", commands=[{"command": n, "description": d} for n, d in COMMANDS])
            except Exception as err:  # noqa: BLE001 — 메뉴는 없어도 명령은 된다
                log.warning("텔레그램 명령어 메뉴 등록 실패: %s", str(err).replace(self.token, "***"))
            while True:
                try:
                    self.poll_once()
                except Exception as err:  # noqa: BLE001 — 끊겨도 서버는 계속
                    log.warning("텔레그램 명령어 받기 실패: %s", str(err).replace(self.token, "***"))
                    time.sleep(30)

        threading.Thread(target=loop, name="telegram-commands", daemon=True).start()
