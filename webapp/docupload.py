"""텔레그램으로 회사 서류 넣기 (2026-11-09 사용자 요청 — 이름 규칙을 몰라도 되게).

담당자 방(TELEGRAM_CHAT_ID)에서:
  /upload → 봇 "이 메시지에 답장으로 PDF" → PDF 답장 → 서류 종류 버튼 → 유효기간 답장(날짜 또는 '없음')
  → `회사서류_최신/[최신 서류]/종류_오늘[_유효날짜].pdf`로 저장하고 바로 정리(under100m/docs.py — 옛 파일은 [이전 서류]).
단체방의 봇은 일반 메시지를 못 받아서(privacy mode) 봇 메시지에 '답장'으로 주고받는다. 진행 상태는 메모리에만(재시작하면 처음부터).
발급일은 받은 날(오늘)로 적는다 — 같은 종류 중 새 파일을 고르는 데만 쓴다.
"""

from __future__ import annotations

import html
import json
import re
from datetime import date
from pathlib import Path
from typing import Any, Callable

from . import docexpiry

_DATE = re.compile(r"(\d{4})\D?(\d{1,2})\D?(\d{1,2})")
FORCE = {"force_reply": True, "selective": True}


def kinds(folder: Path) -> list[str]:
    try:
        rows = json.loads((folder / "_목록.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return sorted({r["종류"] for r in rows if r.get("종류")})


def parse_expiry(text: str) -> date | None | str:
    """'없음' → None, 날짜 → date, 못 읽으면 "" (다시 묻는다)."""
    t = (text or "").strip()
    if t in ("없음", "없어", "x", "X", "-"):
        return None
    m = _DATE.search(t)
    if not m:
        return ""
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return ""


class Uploader:
    def __init__(self, api: Callable[..., Any], download: Callable[[str], bytes], chats: set[str],
                 folder: Callable[[], Path] = docexpiry.docs_dir, today: Callable[[], date] = date.today):
        self.api, self.download, self.chats, self.folder, self.today = api, download, chats, folder, today
        self.pending: dict[str, dict] = {}

    def _say(self, chat: str, text: str, reply_to: int | None = None, markup: dict | None = None) -> int | None:
        body = {"chat_id": chat, "text": text, "parse_mode": "HTML"}
        if reply_to:
            body["reply_to_message_id"] = reply_to
        if markup:
            body["reply_markup"] = markup
        res = self.api("sendMessage", **body) or {}
        return res.get("message_id")

    def handle_message(self, msg: dict) -> bool:
        """처리했으면 True. 명령어·답장이 서류 넣기 흐름이 아니면 False(다른 명령어 처리로)."""
        chat = str((msg.get("chat") or {}).get("id", ""))
        if chat not in self.chats:
            return False
        text = (msg.get("text") or "").strip()
        state = self.pending.get(chat)
        replied = (msg.get("reply_to_message") or {}).get("message_id")
        if re.match(r"^/upload(@\w+)?$", text):
            if not (self.folder() / "[최신 서류]").is_dir():
                self._say(chat, "회사서류_최신/[최신 서류] 폴더가 없어 받을 수 없습니다.", msg.get("message_id"))
                return True
            ask = self._say(chat, "회사 서류 PDF를 <b>이 메시지에 답장</b>으로 보내 주세요. (그만두려면 답장으로 '취소')",
                            msg.get("message_id"), FORCE)
            self.pending[chat] = {"step": "file", "ask": ask}
            return True
        if not state or replied != state.get("ask"):
            return False
        if text == "취소":
            self.pending.pop(chat, None)
            self._say(chat, "서류 넣기를 그만뒀습니다.", msg.get("message_id"))
            return True
        if state["step"] == "file":
            doc = msg.get("document") or {}
            if not doc or not (doc.get("file_name", "").lower().endswith(".pdf") or doc.get("mime_type") == "application/pdf"):
                state["ask"] = self._say(chat, "PDF 파일을 답장으로 보내 주세요.", msg.get("message_id"), FORCE)
                return True
            options = kinds(self.folder())
            state.update(step="kind", file_id=doc["file_id"], kinds=options)
            buttons = [[{"text": k, "callback_data": f"k:{i}"}] for i, k in enumerate(options)]
            buttons.append([{"text": "취소", "callback_data": "k:x"}])
            state["ask"] = self._say(chat, f"어떤 서류인가요? ({html.escape(doc.get('file_name') or 'PDF')})",
                                     msg.get("message_id"), {"inline_keyboard": buttons})
            return True
        if state["step"] == "expiry":
            end = parse_expiry(text)
            if end == "":
                state["ask"] = self._say(chat, "날짜를 못 읽었습니다. 예) 2028-11-09 또는 '없음'", msg.get("message_id"), FORCE)
                return True
            self._save(chat, state, end, msg.get("message_id"))
            return True
        return False

    def handle_callback(self, cb: dict) -> bool:
        msg = cb.get("message") or {}
        chat = str((msg.get("chat") or {}).get("id", ""))
        state = self.pending.get(chat)
        data = cb.get("data") or ""
        if not data.startswith("k:") or not state or state.get("step") != "kind" or msg.get("message_id") != state.get("ask"):
            return False
        self.api("answerCallbackQuery", callback_query_id=cb["id"])
        if data == "k:x":
            self.pending.pop(chat, None)
            self._say(chat, "서류 넣기를 그만뒀습니다.")
            return True
        state.update(step="expiry", kind=state["kinds"][int(data[2:])])
        state["ask"] = self._say(chat, f"<b>{html.escape(state['kind'])}</b> — 유효기간 끝나는 날을 답장으로 보내 주세요.\n"
                                       "예) 2028-11-09 · 유효기간이 없으면 '없음'", None, FORCE)
        return True

    def _save(self, chat: str, state: dict, end: date | None, reply_to: int | None) -> None:
        from under100m import docs

        folder = self.folder()
        name = f"{state['kind']}_{self.today()}" + (f"_유효{end}" if end else "") + ".pdf"
        target = docs._unique(folder / "[최신 서류]" / name)
        target.write_bytes(self.download(state["file_id"]))
        self.pending.pop(chat, None)
        notes: list[str] = []
        docs.run(folder, self.today(), notes.append, settle=0, alert_due=False)
        self._say(chat, f"저장했습니다: <code>{html.escape(target.name)}</code>"
                        + ("\n\n" + html.escape("\n".join(notes)) if notes else "")
                        + "\n/docs 로 유효기간을 확인할 수 있습니다.", reply_to)
