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
        rows = []
    out = {r["종류"] for r in rows if r.get("종류")}
    # 인증서갱신.xlsx의 그리다 서류도 '(그리다) 이름'으로 고를 수 있게 — 지일 자격과 헷갈리지 않게(11/11 사용자)
    cert = folder / docexpiry.CERT_FILE
    if cert.exists():
        out |= {k for k, _ in docexpiry.read_cert(cert) if k.startswith(docexpiry.GRIDA_TAG)}
    return sorted(out)


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


def _norm(text: str) -> str:
    return re.sub(r"[\W_]+", "", text or "").lower()


def guess_kind(options: list[str], name: str, text: str = "") -> str | None:
    """파일 이름·PDF 글자로 서류 종류 짐작 — '직접생산확인증명서(조형물)'은 품목까지 맞아야. 여럿이면 가장 긴 이름."""
    hay = _norm(name) + "|" + _norm(text)
    hits = []
    for kind in options:
        base, _, item = kind.partition("(")
        if _norm(kind) in hay or (_norm(base) in hay and (not item or _norm(item.rstrip(")")) in hay)):
            hits.append(kind)
    return max(hits, key=len) if hits else None


def pdf_text(data: bytes) -> str:
    """앞 3쪽 글자(스캔본이면 빈 글) — 종류 짐작용."""
    try:
        import io

        from pypdf import PdfReader

        return "\n".join((page.extract_text() or "") for page in PdfReader(io.BytesIO(data)).pages[:3])
    except Exception:  # noqa: BLE001 — 못 읽으면 이름으로만 짐작
        return ""


def _is_pdf(doc: dict) -> bool:
    return bool(doc) and (doc.get("file_name", "").lower().endswith(".pdf") or doc.get("mime_type") == "application/pdf")


def _grid(options: list[str], prefix: str) -> list[list[dict]]:
    """버튼 두 개씩 한 줄 — 25종도 한눈에."""
    return [[{"text": k, "callback_data": f"{prefix}:{j}"} for j, k in list(enumerate(options))[i:i + 2]]
            for i in range(0, len(options), 2)]


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
        if chat in self.pending:  # 진행 중 봇이 보낸 메시지는 어느 것에 답장해도 받는다(11/09 — 앞 메시지에 답장해 넘어감)
            self.pending[chat]["asks"].add(res.get("message_id"))
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
            self.pending[chat] = {"step": "file", "ask": ask, "asks": {ask}}
            return True
        if re.match(r"^/remove(@\w+)?$", text):
            opts = kinds(self.folder())
            if not opts:
                self._say(chat, "뺄 서류가 없습니다.", msg.get("message_id"))
                return True
            self.pending[chat] = {"step": "remove", "ask": None, "asks": set(), "kinds": opts, "user_msg": msg.get("message_id")}
            self.pending[chat]["ask"] = self._say(
                chat, "목록에서 뺄 서류를 고르세요. (파일은 지우지 않고 [이전 서류]로 옮깁니다)", msg.get("message_id"),
                {"inline_keyboard": _grid(opts, "r") + [[{"text": "취소", "callback_data": "r:x"}]]})
            return True
        doc = msg.get("document") or {}
        if not state or replied not in state["asks"]:
            return False
        if text == "취소":
            self.pending.pop(chat, None)
            self._say(chat, "서류 넣기를 그만뒀습니다.", msg.get("message_id"))
            return True
        if state["step"] == "file":
            if not _is_pdf(doc):
                state["ask"] = self._say(chat, "PDF 파일을 답장으로 보내 주세요.", msg.get("message_id"), FORCE)
                return True
            state.update(doc=doc, user_msg=msg.get("message_id"))
            self._ask_kind(chat, state)
            return True
        if state["step"] == "newname":
            name = re.sub(r"\s+", " ", text).strip()
            if not name or len(name) > 40 or re.search(r'[\\/:*?"<>|]', name):
                state["ask"] = self._say(chat, "서류 이름을 다시 적어 주세요. (40자 안, 파일 이름에 못 쓰는 기호 빼고)",
                                         msg.get("message_id"), FORCE)
                return True
            state["kind"] = name
            self._ask_expiry(chat, state)
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
        step = {"k": "kind", "r": "remove"}.get(data[:1])
        if not state or not step or state.get("step") != step or msg.get("message_id") not in state["asks"]:
            return False
        self.api("answerCallbackQuery", callback_query_id=cb["id"])
        if data in ("k:x", "r:x"):
            self.pending.pop(chat, None)
            self._say(chat, "그만뒀습니다.")
            return True
        if data == "k:more":
            self._kind_list(chat, state)
            return True
        if data == "k:new":
            state["step"] = "newname"
            state["ask"] = self._say(chat, "새 서류 이름을 답장으로 적어 주세요. 예) 조경식재공사업 등록증",
                                     state.get("user_msg"), FORCE)
            return True
        if data.startswith("r:y:"):
            self._remove(chat, state["kinds"][int(data[4:])])
            return True
        if step == "remove":
            kind = state["kinds"][int(data[2:])]
            state["ask"] = self._say(chat, f"<b>{html.escape(kind)}</b> — 목록과 만료 알림에서 뺄까요?", state.get("user_msg"),
                                     {"inline_keyboard": [[{"text": "빼기", "callback_data": f"r:y:{data[2:]}"},
                                                           {"text": "아니요", "callback_data": "r:x"}]]})
            return True
        state["kind"] = state["kinds"][int(data[2:])]
        self._ask_expiry(chat, state)
        return True

    def _ask_expiry(self, chat: str, state: dict) -> None:
        state["step"] = "expiry"
        state["ask"] = self._say(chat, f"<b>{html.escape(state['kind'])}</b> — 유효기간 끝나는 날을 답장으로 보내 주세요.\n"
                                       "예) 2028-11-09 · 유효기간이 없으면 '없음'", state.get("user_msg"), FORCE)
        # ↑ PDF 보낸 사람 메시지에 답장으로 + force_reply → 그 사람 입력창이 이 질문에 대한 답장으로 바로 열린다(selective)
        return True

    def _ask_kind(self, chat: str, state: dict) -> None:
        """PDF를 받아 종류를 짐작 — 맞으면 버튼 하나로 끝, 아니면 전체 목록(두 줄씩)."""
        doc = state["doc"]
        state["data"] = self.download(doc["file_id"])
        state.update(step="kind", kinds=kinds(self.folder()))
        guess = guess_kind(state["kinds"], doc.get("file_name") or "", pdf_text(state["data"]))
        if guess is None:
            self._kind_list(chat, state)
            return
        i = state["kinds"].index(guess)
        state["ask"] = self._say(chat, f"<b>{html.escape(guess)}</b> 맞나요?", state.get("user_msg"),
                                 {"inline_keyboard": [[{"text": "맞음", "callback_data": f"k:{i}"},
                                                       {"text": "다른 종류", "callback_data": "k:more"}],
                                                      [{"text": "취소", "callback_data": "k:x"}]]})

    def _kind_list(self, chat: str, state: dict) -> None:
        rows = _grid(state["kinds"], "k")
        rows.append([{"text": "➕ 새 종류", "callback_data": "k:new"}, {"text": "취소", "callback_data": "k:x"}])
        state["ask"] = self._say(chat, "어떤 서류인가요?", state.get("user_msg"), {"inline_keyboard": rows})

    def _remove(self, chat: str, kind: str) -> None:
        """그 종류 파일을 [이전 서류]로 옮기고(지우지 않음) 목록을 다시 쓴다 — 목록·/docs·만료 알림에서 빠짐."""
        from under100m import docs

        folder = self.folder()
        old = folder / "[이전 서류]"
        old.mkdir(exist_ok=True)
        moved = 0
        for path in (folder / "[최신 서류]").glob("*.pdf"):
            doc = docs.parse_name(path.name)
            if doc and doc.kind == kind:
                path.replace(docs._unique(old / path.name))
                moved += 1
        self.pending.pop(chat, None)
        docs.run(folder, self.today(), lambda text: None, settle=0, alert_due=False)
        self._say(chat, f"<b>{html.escape(kind)}</b> 뺐습니다 — 파일 {moved}개를 [이전 서류]로 옮김. "
                        "다시 필요하면 /upload 로 넣으면 됩니다.")

    def _save(self, chat: str, state: dict, end: date | None, reply_to: int | None) -> None:
        from under100m import docs

        folder = self.folder()
        name = f"{state['kind']}_{self.today()}" + (f"_유효{end}" if end else "") + ".pdf"
        target = docs._unique(folder / "[최신 서류]" / name)
        target.write_bytes(state["data"])
        self.pending.pop(chat, None)
        notes: list[str] = []
        docs.run(folder, self.today(), notes.append, settle=0, alert_due=False)
        self._say(chat, f"저장했습니다: <code>{html.escape(target.name)}</code>"
                        + ("\n\n" + html.escape("\n".join(notes)) if notes else "")
                        + "\n/docs 로 유효기간을 확인할 수 있습니다.", reply_to)
