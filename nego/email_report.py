"""주간 리포트 이메일 (2026-10-19 통합 결정 15번 — 팀원 수집기의 메일 발송을 옮김).

매주 월요일 주간 실행(`--email`)에서 HTML 리포트(`report.render_html` — 관심 공고 / 세부품명 공고 두 구역,
마감 일시 툴팁, 싱크로율)를 본문으로 보내고 같은 파일을 첨부한다.

설정:
  config/recipients.json   받는 사람 목록(나중에 이 파일만 고치면 됨)
  SMTP_HOST, SMTP_PORT(기본 587), SMTP_SECURE("true"면 SSL, 보통 465), SMTP_USER, SMTP_PASS, SMTP_FROM(없으면 SMTP_USER)

발송 실패는 예외로 올리지 않고 오류 문구를 돌려준다 — 텔레그램처럼 보고 하나가 실패했다고 수집 결과가 바뀌지 않게.
"""

from __future__ import annotations

import json
import logging
import os
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path

log = logging.getLogger(__name__)

RECIPIENTS_PATH = Path(__file__).resolve().parent.parent / "config" / "recipients.json"


def load_recipients(path: Path = RECIPIENTS_PATH) -> list[str]:
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return [r.strip() for r in data.get("recipients", []) if str(r).strip()]


def subject_for(date_text: str, total: int, partial: bool = False, part: str | None = None) -> str:
    """part("관심 공고"·"세부품명 공고")가 있으면 나눠 보낸 메일 — 제목에 구역과 그 구역 건수."""
    head = f"[나라장터 입찰 모니터링] {date_text} 주간 리포트"
    body = f"{part} {total}건" if part else f"매칭 {total}건"
    return f"{head} - {body}" + (" (일부 조회 실패)" if partial else "")


def build_message(subject: str, html_body: str, sender: str, recipients: list[str], attachment: Path | None = None) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    msg.set_content("HTML 메일을 볼 수 없는 환경입니다. 첨부한 리포트 파일을 열어 주세요.")
    msg.add_alternative(html_body, subtype="html")
    if attachment is not None and attachment.exists():
        msg.add_attachment(attachment.read_bytes(), maintype="text", subtype="html", filename=attachment.name)
    return msg


def send(subject: str, html_body: str, attachment: Path | None = None, recipients: list[str] | None = None) -> str | None:
    """보내면 None, 못 보내면 이유. SMTP 설정이나 받는 사람이 없으면 보내지 않고 이유를 돌려준다."""
    recipients = recipients if recipients is not None else load_recipients()
    host = os.environ.get("SMTP_HOST", "").strip()
    user = os.environ.get("SMTP_USER", "").strip()
    password = os.environ.get("SMTP_PASS", "")
    sender = os.environ.get("SMTP_FROM", "").strip() or user
    if not host or not recipients or not sender:
        return "SMTP_HOST·보내는 주소·받는 사람(config/recipients.json) 중 빠진 것이 있어 메일을 보내지 않았습니다"
    port = int(os.environ.get("SMTP_PORT", "587") or 587)
    secure = os.environ.get("SMTP_SECURE", "").strip().lower() in ("1", "true", "yes")
    msg = build_message(subject, html_body, sender, recipients, attachment)
    try:
        if secure:
            with smtplib.SMTP_SSL(host, port, context=ssl.create_default_context(), timeout=60) as smtp:
                if user:
                    smtp.login(user, password)
                smtp.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=60) as smtp:
                smtp.starttls(context=ssl.create_default_context())
                if user:
                    smtp.login(user, password)
                smtp.send_message(msg)
    except (smtplib.SMTPException, OSError) as err:
        return f"메일 발송 실패: {str(err).replace(password, '***') if password else err}"
    log.info("주간 리포트 메일 발송: %s명", len(recipients))
    return None
