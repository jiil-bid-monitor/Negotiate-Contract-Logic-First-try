"""웹앱 "텔레그램 보내기" — 지금 화면에 띄운 수집 결과를 텔레그램 대화방으로 보낸다.

메시지 모양은 CLI 발송과 같다(`nego.telegram`). 저장된 카드 JSON(`collect.serialize`)을 그대로 넘긴다.
서버 PC `.env`에 TELEGRAM_BOT_TOKEN·TELEGRAM_CHAT_ID가 있어야 한다. 선택: WEBAPP_URL(메시지 끝에 웹앱 주소).
"""

from __future__ import annotations

import os
from typing import Any

from nego import telegram

TITLE = "지일 입찰 공고 보고"


def _span(run: dict[str, Any]) -> str:
    params = run.get("params") or {}
    if params.get("begin"):
        return "직접 지정"
    if params.get("days"):
        return f"최근 {params['days']}일"
    return ""


def build(run: dict[str, Any], sender: str) -> list[str]:
    stats = run.get("stats") or {}
    warnings = []
    if stats.get("license_error"):
        warnings.append("면허제한정보 조회 실패 — 자격 판정 일부 미적용")
    if stats.get("prespec_error"):
        warnings.append("사전규격 조회 실패 — 본공고만")
    if not (run.get("params") or {}).get("attachments", True):
        warnings.append("첨부 자격판정 안 함 — 실적·현장설명회 등 확인 필요 항목 없음")
    span = _span(run)
    footer = f"보낸 사람: {sender}"
    url = os.environ.get("WEBAPP_URL", "").strip()
    if url:
        footer += f"\n자세히(사내망): {url}"
    return telegram.build_messages(
        run.get("candidates") or [],
        f"{TITLE}" + (f" ({span})" if span else ""),
        stats.get("period_begin"), stats.get("period_end"), warnings, footer=footer,
    )


def send(run: dict[str, Any], sender: str, session=None) -> tuple[int, list[str]]:
    """(대화방 수, 실패 목록). 설정이 없으면 ValueError."""
    config = telegram.TelegramConfig.from_env()
    if config is None:
        raise ValueError("서버 .env에 TELEGRAM_BOT_TOKEN·TELEGRAM_CHAT_ID가 없습니다 (SETUP.md '텔레그램 봇' 참고)")
    errors = telegram.send_messages(config, build(run, sender), session=session)
    return len(config.chat_ids), errors
