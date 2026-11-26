"""텔레그램으로 회사 서류 넣기 (2026-11-09 사용자 요청 — 이름 규칙을 몰라도 되게).

담당자 방(TELEGRAM_CHAT_ID)에서:
  /upload → 봇 "이 메시지에 답장으로 PDF" → PDF 답장 → 서류 종류 버튼 → 유효기간 답장(날짜 또는 '없음')
  → `회사서류_최신/(지일|그리다)/[최신 서류]/종류_오늘[_유효날짜].pdf`로 저장하고 바로 정리(under100m/docs.py — 옛 파일은 [이전 서류]).
단체방의 봇은 일반 메시지를 못 받아서(privacy mode) 봇 메시지에 '답장'으로 주고받는다. 진행 상태는 메모리에만(재시작하면 처음부터).
발급일은 받은 날(오늘)로 적는다 — 같은 종류 중 새 파일을 고르는 데만 쓴다.
"""

from __future__ import annotations

import html
import json
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable

from . import docexpiry

_DATE = re.compile(r"(\d{4})\D?(\d{1,2})\D?(\d{1,2})")
FORCE = {"force_reply": True, "selective": True}
REPLY = {"force_reply": True}  # 버튼에서 시작한 질문 — 누른 사람 메시지가 없어 selective를 못 씀


def kinds(folder: Path) -> list[str]:
    from under100m import docs

    rows = []
    for sub in docs.company_dirs(folder):  # 회사별 폴더(지일·그리다)면 두 목록을 합침(2026-11-26)
        try:
            rows += json.loads((sub / "_목록.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    out = {r["종류"] for r in rows if r.get("종류")}
    # 인증서갱신.xlsx의 그리다 서류도 '(그리다) 이름'으로 고를 수 있게 — 지일 자격과 헷갈리지 않게(11/11 사용자)
    cert = docexpiry.cert_path(folder)
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


def plus_years(d: date, n: int) -> date:
    try:
        return d.replace(year=d.year + n)
    except ValueError:  # 2월 29일
        return d.replace(year=d.year + n, day=28)


def date_base(folder: Path, kind: str, today: date) -> date:
    """1년·2년 뒤 버튼의 기준 — 그 서류의 지금 만료일(표 이름·파일 종류 어느 쪽이든), 없으면 오늘 하루 전."""
    old = next((d for k, d in docexpiry.read_docs(folder) if k == kind or docexpiry.same_doc(k, kind)), None)
    return old or today - timedelta(days=1)


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
            if not docexpiry.ready(self.folder()):
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
            if state.get("kind"):  # '갱신 완료'에서 시작 — 종류는 이미 앎
                state["data"] = self.download(doc["file_id"])
                self._ask_expiry(chat, state)
            else:
                self._ask_kind(chat, state)
            return True
        if state["step"] == "newname":
            name = new_kind_name(text)
            if not name:
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

    def start_renew(self, chat: str, kinds: list[str]) -> bool:
        """'갱신 완료' 버튼 — 그 서류의 새 PDF를 받는다(종류는 이미 앎). 여러 건이면 하나씩 차례로(2026-11-23 사용자 요청).
        담당자 방이 아니거나 폴더가 없으면 False."""
        if chat not in self.chats or not kinds or not docexpiry.ready(self.folder()):
            return False
        self.pending[chat] = {"step": "file", "ask": None, "asks": set(), "kind": kinds[0], "queue": kinds[1:]}
        more = f"\n(이 서류 다음에 {len(kinds) - 1}건 더 차례로 받습니다)" if len(kinds) > 1 else ""
        self.pending[chat]["ask"] = self._say(
            chat, f"<b>{html.escape(kinds[0])}</b> — 새로 받은 서류 PDF를 <b>이 메시지에 답장</b>으로 보내 주세요.{more}\n"
                  "(그만두려면 답장으로 '취소')", None, REPLY)
        return True

    def handle_callback(self, cb: dict) -> bool:
        msg = cb.get("message") or {}
        chat = str((msg.get("chat") or {}).get("id", ""))
        state = self.pending.get(chat)
        data = cb.get("data") or ""
        step = {"k": "kind", "r": "remove", "x": "expiry"}.get(data[:1])
        if not state or not step or state.get("step") != step or msg.get("message_id") not in state["asks"]:
            return False
        self.api("answerCallbackQuery", callback_query_id=cb["id"])
        if step == "expiry":  # 만료일 버튼 — 1년·2년 뒤·직접 입력·유효기간 없음
            if data == "x:type":
                self._ask_date(chat, state)
            elif data in ("x:y1", "x:y2") and state.get("cands"):
                self._save(chat, state, state["cands"][int(data[-1]) - 1], state.get("user_msg"))
            elif data == "x:none":
                self._save(chat, state, None, state.get("user_msg"))
            return True
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
        """만료일은 PDF에서 읽지 않고 늘 사람이 고른다(2026-11-23 사용자 결정 — 잘못 읽으면 엑셀을 다시 고쳐야 해서).
        버튼: 지금 만료일(없으면 오늘 하루 전)에서 1년·2년 뒤 · 직접 입력 · 유효기간 없음."""
        state["step"] = "expiry"
        state["cands"] = [plus_years(date_base(self.folder(), state["kind"], self.today()), n) for n in (1, 2)]
        state["ask"] = self._say(chat, f"<b>{html.escape(state['kind'])}</b> — 서류에 적힌 유효기간 끝나는 날과 같은 버튼을 눌러 주세요.",
                                 state.get("user_msg"), {"inline_keyboard": [
                                     [{"text": f"{n}년 → {d}", "callback_data": f"x:y{n}"} for n, d in zip((1, 2), state["cands"])],
                                     [{"text": "다른 날짜 직접 입력", "callback_data": "x:type"},
                                      {"text": "유효기간 없는 서류", "callback_data": "x:none"}]]})

    def _ask_date(self, chat: str, state: dict, head: str = "") -> None:
        # 텔레그램은 버튼과 답장 칸을 한 메시지에 같이 못 붙여서, 날짜를 칠 때는 답장 칸이 바로 열리는 질문으로
        state["ask"] = self._say(chat, head + f"<b>{html.escape(state['kind'])}</b> — 서류에 적힌 유효기간 끝나는 날을 답장으로 "
                                 "보내 주세요.\n예) 2028-11-09 · 유효기간이 없으면 '없음'",
                                 state.get("user_msg"), FORCE if state.get("user_msg") else REPLY)

    def _ask_kind(self, chat: str, state: dict) -> None:
        """PDF를 받아 종류를 짐작 — 맞으면 버튼 하나로 끝, 아니면 전체 목록(두 줄씩)."""
        doc = state["doc"]
        state["data"] = self.download(doc["file_id"])
        state.update(step="kind", kinds=kinds(self.folder()), text=pdf_text(state["data"]))
        guess = guess_kind(state["kinds"], doc.get("file_name") or "", state["text"])
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
        self.pending.pop(chat, None)
        moved, cert = remove_doc(self.folder(), kind, self.today())
        self._say(chat, f"<b>{html.escape(kind)}</b> 뺐습니다 — 파일 {moved}개를 [이전 서류]로 옮김." + ("\n" + cert if cert else "")
                        + "\n다시 필요하면 /upload 로 넣으면 됩니다.")

    def _save(self, chat: str, state: dict, end: date | None, reply_to: int | None) -> None:
        queue = (self.pending.pop(chat, None) or {}).get("queue")
        saved, cert, notes = save_doc(self.folder(), state["kind"], state["data"], end, self.today())
        self._say(chat, f"저장했습니다: <code>{html.escape(saved)}</code>" + ("\n" + cert if cert else "")
                        + ("\n\n" + html.escape("\n".join(notes)) if notes else "")
                        + "\n\n/docs 로 유효기간을 확인할 수 있습니다.", reply_to)
        if queue:
            self.start_renew(chat, queue)

    def _cert(self, kind: str, value: date | str) -> str:
        return ("\n" + cert) if (cert := cert_note(self.folder(), kind, value)) else ""


def cert_note(folder: Path, kind: str, value: date | str) -> str:
    """인증서갱신.xlsx(차장님 표)의 그 서류 만료일 칸도 고친다 — 알림·/docs가 이 표를 먼저 보므로(11/11 사용자 요청)."""
    try:
        hit = docexpiry.set_cert(docexpiry.cert_path(folder), kind, value)
    except OSError:
        return "⚠ 인증서갱신.xlsx가 열려 있어 못 고쳤습니다 — 엑셀을 닫고 직접 고쳐 주세요."
    if not hit:
        return ""
    return "인증서갱신.xlsx 만료일도 " + (f"{value}로 고쳤습니다." if isinstance(value, date) else f"'{value}'로 바꿨습니다.")


# 정리 때 "막 넣은 파일은 건너뜀"(담당자가 복사 중인 파일 보호) — 우리가 다 쓴 파일이라 바로 셈.
# 0이면 파일 시각이 지금보다 조금 앞서(서버 PC↔NAS 시계 차이) 새 파일을 건너뛰고 옛 파일이 최신으로 남았음(2026-11-23)
OURS = -600


def new_kind_name(text: str) -> str | None:
    """새 서류 종류 이름 — 파일 이름이 되므로 40자 안, 못 쓰는 기호 없이. 안 되면 None (텔레그램·웹앱 같이)."""
    name = re.sub(r"\s+", " ", text or "").strip()
    # 그리다 서류는 앞머리를 '(그리다) '로 통일(11/24 사용자) — '그리다 - 여성기업'·'[그리다]여성기업'·'그리다_여성기업' 등
    if m := re.match(r"^[(\[{（【]?\s*그리다\s*[)\]}）】]?\s*[-_:·.,]?\s*(.+)$", name):
        name = f"(그리다) {m.group(1)}"
    return name if name and len(name) <= 40 and not re.search(r'[\\/:*?"<>|]', name) and name not in (".", "..") else None


def file_kind(folder: Path, kind: str) -> str:
    """인증서갱신.xlsx 이름('신용평가') → [최신 서류] 파일 종류('신용평가등급확인서') — 저장·빼기·고치기가 옛 파일을 찾게(2026-11-23).
    표 이름이 파일 종류와 다른 지일 서류가 많음(직접생산확인증명서(…) 등). 여럿이면 표 날짜와 같은 유효기간의 파일, 없으면 그대로."""
    from under100m import docs

    folder = docs.company_dir(folder, kind)  # 그 서류의 회사 폴더(2026-11-26)
    try:
        rows = json.loads((folder / "_목록.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        rows = []
    files = {r["종류"]: r.get("유효기간") or "" for r in rows if r.get("종류")}
    for p in (folder / "[최신 서류]").glob("*.pdf"):  # 목록이 아직 없거나 낡았어도 실제 파일로
        if (d := docs.parse_name(p.name)) and d.kind not in files:
            files[d.kind] = d.expires.isoformat() if d.expires else ""
    if kind in files:
        return kind
    hits = [k for k in files if docexpiry.same_doc(kind, k)]
    if len(hits) > 1:
        cert = dict(docexpiry.read_cert(docexpiry.cert_path(folder))) if docexpiry.cert_path(folder).exists() else {}
        hits = [k for k in hits if cert.get(kind) and files[k] == cert[kind].isoformat()] or hits[:1]
    return hits[0] if hits else kind


def save_doc(folder: Path, kind: str, data: bytes, end: date | None, today: date) -> tuple[str, str, list[str]]:
    """새 서류 PDF를 [최신 서류]/종류_오늘[_유효날짜].pdf로 저장하고 정리(옛 파일 → [이전 서류]) + 표 만료일 —
    텔레그램 /upload·갱신 완료와 웹앱이 같이 쓴다(2026-11-23). → (저장한 파일 이름, 표 안내, 정리 안내들)"""
    from under100m import docs

    folder = docs.company_dir(folder, kind)  # '(그리다) …'는 그리다 폴더, 그 밖은 지일 폴더(2026-11-26)
    fkind = file_kind(folder, kind)
    notes: list[str] = []
    # 같은 날 같은 서류를 또 올리면(잘못 올려 다시 올림) 앞 파일을 먼저 [이전 서류]로 — 발급일이 같으면 정리가 둘 다 남겨 중복이 됐음(2026-11-23)
    for p in _latest(folder, fkind):
        if docs.parse_name(p.name).issued == today:
            notes.append(f"- {_set_aside(folder, p)} → [이전 서류] (같은 날 다시 올림)")
    name = f"{fkind}_{today}" + (f"_유효{end}" if end else "") + ".pdf"
    target = docs._unique(folder / "[최신 서류]" / name)
    target.write_bytes(data)
    docs.run(folder, today, notes.append, settle=OURS, alert_due=False)
    return target.name, cert_note(folder, kind, end) if end else "", notes


def _set_aside(folder: Path, path: Path) -> str:
    """파일을 지우지 않고 [이전 서류]로 — 옮긴 파일 이름."""
    from under100m import docs

    (folder / "[이전 서류]").mkdir(exist_ok=True)
    path.replace(docs._unique(folder / "[이전 서류]" / path.name))
    return path.name


def replace_doc(folder: Path, kind: str, data: bytes, today: date) -> str:
    """잘못 올린 파일 바꾸기(2026-11-23 사용자 요청) — 지금 파일을 [이전 서류]로 빼고 같은 이름(발급일·유효기간 그대로)으로 새 PDF.
    날짜가 틀렸으면 '날짜 수정'으로 따로. 바꿀 파일이 없으면 ValueError."""
    from under100m import docs

    folder = docs.company_dir(folder, kind)
    paths = _latest(folder, file_kind(folder, kind))
    if not paths:
        raise ValueError("바꿀 파일이 없습니다 — '새 서류 올리기'로 넣어 주세요")
    target = paths[-1]
    _set_aside(folder, target)
    target.write_bytes(data)
    docs.run(folder, today, lambda text: None, settle=OURS, alert_due=False)
    return f"파일을 바꿨습니다: {target.name}\n잘못 올린 파일은 지우지 않고 [이전 서류]로 옮겼습니다."


def same_kind(folder: Path, name: str) -> str | None:
    """새 서류 이름이 이미 있는 서류와 같은지(띄어쓰기·기호·대소문자 무시, 표 이름 포함) — 있으면 그 이름. 같은 서류가 두 줄로 생기지 않게.
    글자 포함으로는 안 봄 — '사업자등록증'·'사업자등록증명'처럼 다른 서류가 실제로 있음."""
    norm = _norm(name)
    return next((k for k in kinds(folder) + [k for k, _ in docexpiry.read_docs(folder)] if _norm(k) == norm), None)


def _latest(folder: Path, kind: str):
    from under100m import docs

    folder = docs.company_dir(folder, kind)
    files = [(d, p) for p in (folder / "[최신 서류]").glob("*.pdf") if (d := docs.parse_name(p.name)) and d.kind == kind]
    return [p for _, p in sorted(files, key=lambda x: x[0].issued)]


def remove_doc(folder: Path, kind: str, today: date) -> tuple[int, str]:
    """그 종류 파일을 모두 [이전 서류]로(지우지 않음) + 표 '사용 안 함' → (옮긴 수, 표 안내)."""
    from under100m import docs

    folder = docs.company_dir(folder, kind)
    old = folder / "[이전 서류]"
    old.mkdir(exist_ok=True)
    paths = _latest(folder, file_kind(folder, kind))
    for path in paths:
        path.replace(docs._unique(old / path.name))
    docs.run(folder, today, lambda text: None, settle=OURS, alert_due=False)
    return len(paths), cert_note(folder, kind, "사용 안 함")


def edit_doc(folder: Path, kind: str, end: date | None, today: date) -> str:
    """새 파일 없이 유효기간만 고치기(잘못 적었을 때, 2026-11-23 사용자 요청) — 표 만료일 + 최신 파일 이름의 _유효 날짜.
    고친 곳이 하나도 없으면 ValueError."""
    from under100m import docs

    folder = docs.company_dir(folder, kind)
    done = []
    paths = _latest(folder, file_kind(folder, kind))
    if paths:
        doc = docs.parse_name(paths[-1].name)
        new = docs._unique(paths[-1].with_name(f"{doc.kind}_{doc.issued}" + (f"_유효{end}" if end else "") + ".pdf"))
        if new.name != paths[-1].name:
            paths[-1].replace(new)
        done.append(f"파일 이름 → {new.name}")
        docs.run(folder, today, lambda text: None, settle=OURS, alert_due=False)
    cert = cert_note(folder, kind, end or "없음")
    if cert:
        done.append(cert)
    if not done:
        raise ValueError("고칠 파일도 인증서갱신.xlsx 줄도 없습니다 — 새 서류 올리기로 넣어 주세요")
    return "\n".join(done)


def all_docs(folder: Path, today: date) -> list[dict]:
    """서류 관리 목록 — 만료일이 있는 서류(표·서류목록) + 날짜 없는 파일 종류. [{kind, end, left, state, file}]
    file = [최신 서류]의 지금 파일 이름(어떤 파일이 올라가 있는지 보이게, 없으면 '')."""
    states = docexpiry.load_states()
    dated = docexpiry.read_docs(folder)
    out = [{"kind": k, "end": d.isoformat(), "left": (d - today).days,
            "state": docexpiry.STATES[states[docexpiry.apply_key(k, d)]][0] if docexpiry.apply_key(k, d) in states else ""}
           for k, d in dated]
    names = [k for k, _ in dated]
    out += [{"kind": k, "end": None, "left": None, "state": ""} for k in kinds(folder)
            if k not in names and not any(docexpiry.same_doc(c, k) for c in names)]
    from under100m import docs

    for d in out:
        paths = _latest(folder, file_kind(folder, d["kind"]))
        d["file"] = paths[-1].name if paths else ""
        if paths and not d["end"] and (note := docs.missing_expiry_note(d["kind"])):
            d["note"] = note  # 웹앱 서류 관리 창에 빨간 글씨
    return out
