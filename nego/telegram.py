"""수집 결과를 텔레그램 봇으로 발송한다.

"수집 완료" 알림이 아니라 **보고서 형태**(후보 목록·발주기관·금액·마감·자격)로 보낸다
(2026-09 결정 — 카카오 알림톡은 채널 등록·템플릿 심사·건당 비용이 들어 보류).

  - 본문: 후보 공고 목록. 텔레그램 메시지 한 건은 4096자까지라 넘치면 공고 단위로 나눠 보낸다.
  - 첨부(CLI만): 전체 HTML 리포트 파일 (PC·핸드폰에서 눌러 바로 열림).
  - 후보가 0건이어도 "새 공고 없음"을 보낸다 — 받는 사람이 "시스템이 돌았는지"를 알 수 있게.

두 곳에서 부른다(2026-10-12 — 9/28 브랜치 구현본을 main에 옮기면서 웹앱 버튼을 더함):
  - CLI `python -m nego --telegram` — `Candidate` 객체를 `candidate_row`로 바꿔 보냄
  - 웹앱 "텔레그램 보내기" — 저장된 수집 결과(카드 JSON, `webapp/collect.serialize`)를 그대로 보냄.
    카드 JSON과 `candidate_row`는 같은 키를 쓴다 — 메시지 모양이 한 곳(`_candidate_block`)에서 정해진다.

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
# 웹앱 "검토 필요"(관심·제외 키워드 공존)는 목록 끝에 짧게 — 길어지면 핵심 후보가 묻힌다
MAX_REVIEW_LINES = 15


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


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat(timespec="minutes") if dt else None


def candidate_row(c: Any) -> dict[str, Any]:
    """CLI의 `Candidate` → 웹앱 카드 JSON과 같은 키의 dict (메시지에 쓰는 칸만)."""
    n = c.notice
    q = c.qualification
    earliest = c.schedule.earliest
    return {
        "title": n.title,
        "detail_url": n.detail_url,
        "kind": n.kind,
        "category": c.category,
        "work_type": n.work_type,
        "confidence": c.screen_result.confidence,
        "review_exclude": c.screen_result.review_exclude,
        "matched_keywords": c.screen_result.matched_keywords,
        "is_re_notice": c.is_re_notice,
        "demand_institution": n.demand_institution or n.notice_institution,
        "budget": n.budget,
        "deadline_label": earliest[0] if earliest else None,
        "deadline": _iso(earliest[1]) if earliest else None,
        "days_left": c.days_left,
        "region_check": c.region_check.to_dict(),
        "text_flags": list(getattr(c, "text_flags", []) or []),
        "joint": {"label": c.joint.label, "allowed": c.joint.allowed},
        "qualification": {"checked": q.checked, "passes": q.passes, "summary": q.summary},
    }


def _short_money(value: float | None) -> str:
    """핸드폰 한 줄에 들어가게 줄인다: 320,000,000 → 3.2억, 45,000,000 → 4,500만원."""
    if not value:
        return "금액 미상"
    if value >= 100_000_000:
        return f"{value / 100_000_000:.1f}억"
    return f"{value / 10_000:,.0f}만원"


def _deadline_text(row: dict[str, Any]) -> str:
    days = row.get("days_left")
    if days is None:
        return "마감 미상"
    if days < 0:
        return "마감됨"
    text = f"D-{days}"
    if row.get("deadline_label"):
        text += f" {row['deadline_label']}"
    deadline = row.get("deadline")
    if deadline:
        try:
            dt = datetime.fromisoformat(deadline)
            text += f"({dt.month}/{dt.day})"
        except ValueError:
            pass
    return text


def _qual_text(row: dict[str, Any]) -> str:
    q = row.get("qualification") or {}
    if not q.get("checked"):
        return "자격 미확인"
    if q.get("passes"):
        return "자격 충족"
    summary = str(q.get("summary") or "자격 미달")
    return summary if len(summary) <= 60 else summary[:58] + "…)"


def _region_text(row: dict[str, Any]) -> str | None:
    r = row.get("region_check") or {}
    req = "·".join(r.get("required") or [])
    if r.get("status") == "미달":
        return f"지역 미달({req}만)"
    if r.get("status") == "충족":
        return "지역 충족"
    return None


def _flags_text(row: dict[str, Any]) -> str | None:
    kinds = list(dict.fromkeys(f.get("kind") for f in row.get("text_flags") or [] if f.get("kind")))
    names = {"실적": "실적", "현장설명회": "현장설명회", "인력": "기술인력", "건축사사무소": "건축사사무소"}
    return " · ".join(names.get(k, k) for k in kinds) or None


def _kind_tag(row: dict[str, Any]) -> str:
    if row.get("kind") == "사전규격":
        return "사전규격"
    return "·".join(x for x in (row.get("category"), row.get("work_type")) if x) or "유형 미상"


def _candidate_block(index: int, row: dict[str, Any]) -> str:
    esc = html.escape
    title = esc(row.get("title") or "")
    if row.get("detail_url"):
        title = f'<a href="{esc(row["detail_url"], quote=True)}">{title}</a>'
    tag = esc(_kind_tag(row)) + (" · 재공고" if row.get("is_re_notice") else "")
    info = [
        esc(row.get("demand_institution") or "기관 미상"),
        esc(_short_money(row.get("budget"))),
        esc(_deadline_text(row)),
    ]
    cond = [_qual_text(row)]
    region = _region_text(row)
    if region:
        cond.append(region)
    joint = (row.get("joint") or {}).get("label")
    if joint and row.get("kind") != "사전규격":
        cond.append(f"공동수급 {joint}")
    lines = [
        f"<b>{index}. [{tag}]</b> {title}",
        "   " + " · ".join(info),
        "   " + esc(" · ".join(cond)),
    ]
    flags = _flags_text(row)
    if flags:
        lines.append(f"   ⚠️ 확인 필요: {esc(flags)}")
    sync = row.get("sync") or {}
    if sync.get("score") is not None:
        lines.append(f"   싱크로율 {round(sync['score'] * 100)}%")
    return "\n".join(lines)


def _review_line(row: dict[str, Any]) -> str:
    esc = html.escape
    title = esc(row.get("title") or "")
    if row.get("detail_url"):
        title = f'<a href="{esc(row["detail_url"], quote=True)}">{title}</a>'
    why = f' (제외어 "{esc(row.get("review_exclude") or "")}")' if row.get("review_exclude") else ""
    return f"· {title} — {esc(_short_money(row.get('budget')))} · {esc(_deadline_text(row))}{why}"


def _fmt_period(begin: str | datetime | None, end: str | datetime | None) -> str | None:
    def day(v):
        if isinstance(v, str):
            try:
                v = datetime.fromisoformat(v)
            except ValueError:
                return None
        return f"{v.year}.{v.month}.{v.day}" if v else None

    b, e = day(begin), day(end)
    return f"{b} ~ {e}" if b and e else None


def build_messages(
    rows: list[dict[str, Any]],
    title: str,
    period_begin: str | datetime | None = None,
    period_end: str | datetime | None = None,
    warnings: list[str] | None = None,
    footer: str | None = None,
) -> list[str]:
    """후보 목록을 메시지 한도에 맞춰 여러 개로 나눈다. 공고 하나가 두 메시지로 갈라지지 않게
    공고 단위로 자른다.

    - 마감 지난 공고는 목록에서 빼고 건수만 적는다.
    - `confidence == "검토필요"`(웹앱의 관심·제외 키워드 공존)는 맨 뒤에 한 줄씩.
    """
    esc = html.escape
    open_rows = [r for r in rows if r.get("days_left") is None or r["days_left"] >= 0]
    closed = len(rows) - len(open_rows)
    main = [r for r in open_rows if r.get("confidence") != "검토필요"]
    review = [r for r in open_rows if r.get("confidence") == "검토필요"]

    header = [f"📋 <b>{esc(title)}</b>"]
    period = _fmt_period(period_begin, period_end)
    if period:
        header.append(f"조회 기간: {period}")
    for w in warnings or []:
        header.append(f"⚠️ {esc(w)}")

    if not main and not review:
        header.append("")
        header.append("조건에 맞는 새 공고가 없습니다." + (f" (마감 지난 {closed}건 제외)" if closed else ""))
        if footer:
            header.append(esc(footer))
        return ["\n".join(header)]

    summary = f"후보 <b>{len(main)}건</b>"
    if review:
        summary += f" · 검토 필요 {len(review)}건"
    if closed:
        summary += f" (마감 지난 {closed}건 제외)"
    header.append(summary)

    blocks = [_candidate_block(i, r) for i, r in enumerate(main, start=1)]
    if review:
        lines = [f"<b>🔎 검토 필요 {len(review)}건</b> — 관심 키워드와 제외 키워드가 함께 있는 공고"]
        lines += [_review_line(r) for r in review[:MAX_REVIEW_LINES]]
        if len(review) > MAX_REVIEW_LINES:
            lines.append(f"· 외 {len(review) - MAX_REVIEW_LINES}건은 웹앱에서 확인")
        # 검토 필요 줄도 한도를 넘지 않게 줄 단위로 나눠 블록에 넣는다
        chunk: list[str] = []
        for line in lines:
            if chunk and len("\n".join(chunk)) + 1 + len(line) > MESSAGE_LIMIT:
                blocks.append("\n".join(chunk))
                chunk = []
            chunk.append(line)
        blocks.append("\n".join(chunk))
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
    """CLI `RunStats` → 메시지 머리에 붙일 경고."""
    out = []
    if getattr(stats, "failed_operations", None):
        out.append(f"조회 실패: {', '.join(stats.failed_operations)}")
    if getattr(stats, "license_error", None):
        out.append("면허제한정보 조회 실패 — 자격 판정 미적용")
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
    """CLI용 — 후보 목록 + (후보가 있으면) HTML 리포트 파일."""
    from .report import report_title

    rows = [candidate_row(c) for c in candidates]
    messages = build_messages(
        rows, report_title(stats), stats.period_begin, stats.period_end, stats_warnings(stats),
        footer="전체 표는 첨부 HTML 리포트" if rows else None,
    )
    return send_messages(config, messages, html_path if rows else None, session)
