"""수집 결과를 텔레그램 봇으로 발송한다.

"수집 완료" 알림이 아니라 **보고서 형태**로 보낸다 (2026-09 결정 — 카카오 알림톡은 채널 등록·
템플릿 심사·건당 비용이 들어 보류).

GitHub Actions 스케줄(`daily.yml`)이 `python -m nego --telegram`으로 부른다 (2026-10-12 사용자 결정):
매일 08:00 입찰(어제 하루), 매주 월 08:10 협상·규격가격동시입찰·사전규격(지난 7일).

메시지 모양 (2026-10-12 사용자 결정 — 간결형·마감 임박순·HTML 첨부·0건도 발송·이모지 없음, 경고는 "※"):

    입찰 공고 보고 (10/11 게시분)
    후보 3건

    1. ○○체험관 전시물 제작설치          ← 누르면 나라장터 공고
       3.2억 · D-5 · 자격 충족

  - 지역·공동수급·확인 필요 요건 등 자세한 건 첨부 HTML 리포트(전체 표)에서 본다.
  - 유형이 섞이는 주간 보고는 공고명 앞에 [협상]·[규격가격]·[사전규격]을 붙인다.
  - 마감 지난 공고는 목록에서 빼고 건수만 적는다.
  - 메시지 한 건은 4096자까지라 넘치면 공고 단위로 나눠 보낸다.
  - 후보가 0건이어도 "새 공고 없음"을 보낸다 — 받는 사람이 "시스템이 돌았는지"를 알 수 있게.

설정 (환경변수):
  TELEGRAM_BOT_TOKEN  @BotFather가 발급한 봇 토큰
  TELEGRAM_CHAT_ID    받을 대화방 ID. 여러 곳이면 쉼표로 구분 (개인 대화, 단체방 모두 가능)

발송 실패는 수집 결과(리포트/Supabase 저장)에 영향을 주지 않는다. 호출 쪽에서 오류를
로그로 남긴다 — 실패로 Run을 끝내면 워크플로의 자동 재시도가 수집부터 다시 돌려,
이미 받은 대화방에 같은 보고서가 한 번 더 가기 때문이다.
"""

from __future__ import annotations

import html
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

log = logging.getLogger(__name__)

API_BASE = "https://api.telegram.org"
# 텔레그램 한도는 4096자. 태그·이모지 여유를 두고 자른다.
MESSAGE_LIMIT = 3800
MAX_ATTEMPTS = 3

# 주간 보고에서 공고명 앞에 붙이는 유형 표시
_KIND_TAG = {"협상": "협상", "규격가격동시입찰": "규격가격", "입찰": "입찰", "사전규격": "사전규격"}


@dataclass
class TelegramConfig:
    token: str
    chat_ids: list[str] = field(default_factory=list)

    @classmethod
    def from_env(cls) -> "TelegramConfig | None":
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        chats = [c.strip() for c in os.environ.get("TELEGRAM_CHAT_ID", "").split(",") if c.strip()]
        if not token or not chats:
            return None
        return cls(token=token, chat_ids=chats)


@dataclass
class Row:
    """메시지 한 줄에 쓰는 칸만 뽑은 공고."""

    title: str
    detail_url: str | None
    kind: str | None  # 협상 / 규격가격동시입찰 / 입찰 / 사전규격
    budget: float | None
    days_left: int | None
    qualification: str  # 자격 충족 / 자격 미달 / 자격 미확인

    @classmethod
    def from_candidate(cls, c: Any) -> "Row":
        q = c.qualification
        qual = "자격 미확인" if not q.checked else ("자격 충족" if q.passes else "자격 미달")
        kind = "사전규격" if c.notice.kind == "사전규격" else c.category
        return cls(c.notice.title or "", c.notice.detail_url, kind, c.notice.budget, c.days_left, qual)


def _short_money(value: float | None) -> str:
    """핸드폰 한 줄에 들어가게 줄인다: 320,000,000 → 3.2억, 45,000,000 → 4,500만원."""
    if not value:
        return "금액 미상"
    if value >= 100_000_000:
        return f"{value / 100_000_000:.1f}억"
    return f"{value / 10_000:,.0f}만원"


def _md(dt: datetime) -> str:
    return f"{dt.month}/{dt.day}"


def report_title(categories: list[str] | None, prespec: bool, begin: datetime | None, end: datetime | None) -> str:
    """"입찰 공고 보고 (10/11 게시분)" / "협상·규격가격동시입찰·사전규격 공고 보고 (10/5~10/11 게시분)"."""
    kinds = list(categories or ["전체 유형"])
    if prespec:
        kinds.append("사전규격")
    title = f"{'·'.join(kinds)} 공고 보고"
    if begin and end:
        span = _md(begin) if begin.date() == end.date() else f"{_md(begin)}~{_md(end)}"
        title += f" ({span} 게시분)"
    return title


def _line(index: int, row: Row, tagged: bool) -> str:
    esc = html.escape
    title = esc(row.title)
    if row.detail_url:
        title = f'<a href="{esc(row.detail_url, quote=True)}">{title}</a>'
    if tagged and row.kind:
        title = f"[{esc(_KIND_TAG.get(row.kind, row.kind))}] {title}"
    days = "마감 미상" if row.days_left is None else f"D-{row.days_left}"
    return f"{index}. {title}\n   {esc(_short_money(row.budget))} · {days} · {row.qualification}"


def build_messages(rows: list[Row], title: str, warnings: list[str] | None = None,
                   footer: str | None = None) -> list[str]:
    """마감 임박순으로 나열하고 메시지 한도에 맞춰 나눈다(공고 하나가 두 메시지로 갈라지지 않게)."""
    esc = html.escape
    open_rows = [r for r in rows if r.days_left is None or r.days_left >= 0]
    closed = len(rows) - len(open_rows)
    open_rows.sort(key=lambda r: 9999 if r.days_left is None else r.days_left)  # 같은 날이면 들어온 순서 유지

    header = [f"<b>{esc(title)}</b>"]
    header += [f"※ {esc(w)}" for w in warnings or []]
    skipped = f" (마감 지난 {closed}건 제외)" if closed else ""
    if not open_rows:
        header.append("조건에 맞는 새 공고가 없습니다." + skipped)
        return ["\n".join(header)]
    header.append(f"후보 <b>{len(open_rows)}건</b>{skipped}")

    tagged = len({r.kind for r in open_rows}) > 1
    blocks = [_line(i, r, tagged) for i, r in enumerate(open_rows, start=1)]
    if footer:
        blocks.append(esc(footer))

    messages: list[str] = []
    current = "\n".join(header)
    for block in blocks:
        if len(current) + 2 + len(block) > MESSAGE_LIMIT:
            messages.append(current)
            current = block
        else:
            current = f"{current}\n\n{block}"
    messages.append(current)
    return messages


def stats_warnings(stats: Any) -> list[str]:
    """`RunStats` → 메시지 머리에 붙일 경고."""
    out = []
    if getattr(stats, "failed_operations", None):
        out.append(f"조회 실패: {', '.join(stats.failed_operations)}")
    if getattr(stats, "license_error", None):
        out.append("면허제한정보 조회 실패 — 자격 판정 미적용")
    if getattr(stats, "prespec_error", None):
        out.append("사전규격 조회 실패 — 본공고만")
    return out


class TelegramError(Exception):
    pass


def _call(session: requests.Session, config: TelegramConfig, method: str, **kwargs: Any) -> None:
    """429(retry_after)·5xx·연결 오류는 몇 번 다시 시도한다. 오류 메시지에서 토큰은 가린다."""
    url = f"{API_BASE}/bot{config.token}/{method}"
    last_error = ""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            res = session.post(url, timeout=60, **kwargs)
        except requests.RequestException as err:
            last_error = f"연결 실패: {err}"
        else:
            if res.status_code == 200:
                return
            try:
                body = res.json()
            except ValueError:
                body = {}
            last_error = f"HTTP {res.status_code}: {body.get('description') or res.text[:200]}"
            if res.status_code == 429:
                wait = (body.get("parameters") or {}).get("retry_after", 5)
                time.sleep(min(float(wait), 60))
                continue
            if res.status_code < 500:
                break  # 토큰/대화방 ID 오류 같은 건 다시 해도 같다
        if attempt < MAX_ATTEMPTS:
            time.sleep(2 * attempt)
    raise TelegramError(f"{method} 실패: {last_error.replace(config.token, '***')}")


def send_messages(
    config: TelegramConfig,
    messages: list[str],
    document: Path | None = None,
    session: requests.Session | None = None,
) -> list[str]:
    """대화방마다 메시지(와 첨부 파일)를 보낸다. 실패한 대화방의 오류 목록을 돌려준다
    (빈 목록이면 전부 성공)."""
    session = session or requests.Session()
    errors: list[str] = []
    for chat_id in config.chat_ids:
        try:
            for text in messages:
                _call(
                    session,
                    config,
                    "sendMessage",
                    data={
                        "chat_id": chat_id,
                        "text": text,
                        "parse_mode": "HTML",
                        "disable_web_page_preview": "true",
                    },
                )
            if document is not None and document.exists():
                with document.open("rb") as handle:
                    _call(
                        session,
                        config,
                        "sendDocument",
                        data={"chat_id": chat_id},
                        files={"document": (document.name, handle, "text/html")},
                    )
        except TelegramError as err:
            errors.append(f"[{chat_id}] {err}")
    return errors


def send_report(
    config: TelegramConfig,
    candidates: list[Any],
    stats: Any,
    html_path: Path | None = None,
    session: requests.Session | None = None,
) -> list[str]:
    """후보 목록 + (후보가 있으면) HTML 리포트 파일."""
    rows = [Row.from_candidate(c) for c in candidates]
    title = report_title(stats.categories, bool(getattr(stats, "prespec_requested", False)),
                         stats.period_begin, stats.period_end)
    has_open = any(r.days_left is None or r.days_left >= 0 for r in rows)
    messages = build_messages(rows, title, stats_warnings(stats),
                              footer="지역·공동수급·확인 필요 요건 등 자세한 내용은 첨부 HTML 리포트" if has_open else None)
    return send_messages(config, messages, html_path if has_open else None, session)
