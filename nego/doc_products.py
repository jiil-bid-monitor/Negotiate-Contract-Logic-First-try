"""공고문으로 찾는 세부품명 공고 (2026-10-20, 팀원 레포 10/19 이식).

용역 공고는 나라장터 분류에 세부품명이 없고 용역분류만 있어, 공고문 참가자격에 지일 등록 세부품명을 요구해도
'세부품명 미달'(미매칭)로 빠진다 — 팀원 제보: 휘문고 역사관 조성 2차(나라장터 분류엔 세부품명이 없고 공고문에만
디자인서비스(8214150201)·동영상제작서비스). 그런 공고의 첨부를 **참가자격 절이 나오는 문서까지만** 읽어
요구 세부품명 중 지일 등록 품목을 찾는다. 찾으면 웹앱 '세부품명 공고' 탭에 싣는다(`webapp/docscan.py`).
매일 보고에는 넣지 않는다.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import requests

from .attachment_sources import attachments_for
from .attachments import fetch_attachment_text
from .qualification_text import find_qualification_section
from .qualify import _extract_code_requirements

if TYPE_CHECKING:
    from .models import Notice

log = logging.getLogger(__name__)

# 참가자격 절을 찾을 때까지 읽을 첨부 수 — 공고문은 대개 첫째·둘째라 이 안에서 끝난다(수백 건을 읽어 몇 분 안에 끝나게)
MAX_FILES = 3


def _notice_doc_first(attachments: list[dict[str, str]]) -> list[dict[str, str]]:
    """공고문(이름에 '공고')을 앞으로 — 참가자격은 공고문에 적힌다. 나머지는 원래 순서."""
    return sorted(attachments, key=lambda a: 0 if "공고" in (a.get("file_name") or "") else 1)


def required_held_products(notice: "Notice", held_products: set[str], session: requests.Session,
                           timeout: float = 30.0) -> tuple[list[str], str | None]:
    """공고 첨부의 참가자격 절이 요구한 세부품명번호 중 지일 등록 품목 (같은 코드는 한 번)과 그 문서 이름.
    참가자격 절이 나온 첫 문서만 본다. 못 찾으면 ([], None). 연결 사전규격·국방전자조달은 부르지 않는다(가볍게)."""
    tried = 0
    for att in _notice_doc_first(attachments_for(notice, session, None)):
        result = fetch_attachment_text(session, att, timeout=timeout)
        if result.skipped:
            continue
        tried += 1
        if result.ok:
            section = find_qualification_section(result.text)
            if section is not None:
                codes: list[str] = []
                for item in section.items:
                    for code, _ in _extract_code_requirements(item):
                        if len(code) == 10 and code in held_products and code not in codes:
                            codes.append(code)
                return codes, result.file_name
        if tried >= MAX_FILES:
            break
    return [], None
