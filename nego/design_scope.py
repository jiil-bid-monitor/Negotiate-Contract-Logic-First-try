"""설계 용역 공고가 제작·설치까지 하는지 (2026-10-19 사용자 요청 — 표시만, 수집·제외 규칙은 그대로).

관심 키워드로 걸린 공고 중 공고명에 "설계"가 든 용역은 **설계만** 하는 일(지일 본업 아님)과 **설계 + 제작·설치**(본업)가 섞여 있다.
공고명에 제작·설치·시공·조성이 있으면 그것으로, 없으면 첨부 과업 문서(과업지시서·제안요청서 등) 글에서 제작·설치 문장을 찾아
담당자가 바로 가늠하게 한다. 판정이 아니라 근거와 함께 보여 주는 칩이다.
"""

from __future__ import annotations

import re
from typing import Any

_SQUASH = re.compile(r"[\s·ㆍ.,()\[\]]")
# 공고명: 제작·설치까지 하는 사업임을 알 수 있는 말
_TITLE_MAKE = re.compile(r"제작|설치|시공|조성|구축|납품")
# 첨부: 제작·설치 문장 — "제작·설치", "제작 및 설치", "제작설치", "설치 공사", "시공"
_MAKE_LINE = re.compile(r"제작\s*[·ㆍ,및/]?\s*(?:및\s*)?설치|제작설치|설치\s*공사|시공")
# 제작·설치가 이번 과업이 아니라는 문장 — "제작·설치는 별도 발주", "향후 제작설치 시"
_NOT_THIS_JOB = re.compile(r"별도|제외|포함하지|불포함|추후|향후|차기|후속|다음\s*단계|발주\s*예정")
_EVIDENCE_MIN = 2  # 이만큼 나와야 "제작·설치 포함"으로 본다(목차·한 줄 언급은 넘긴다)


def is_design_service(title: str, work_type: str | None) -> bool:
    squashed = _SQUASH.sub("", title or "")
    return "설계" in squashed and "설계공모" not in squashed and (work_type == "용역" or "용역" in squashed)


def design_scope(title: str, work_type: str | None, text: str | None) -> dict[str, Any] | None:
    """설계 용역 공고면 {kind, source, lines}, 아니면 None.

    kind: "설계+제작설치"(공고명이나 첨부에 제작·설치가 있음) / "설계만"(첨부를 읽었는데 없음, 또는 별도 발주라고 적힘) /
    "확인 필요"(첨부를 못 읽음).
    """
    if not is_design_service(title, work_type):
        return None
    if _TITLE_MAKE.search(_SQUASH.sub("", title or "")):
        return {"kind": "설계+제작설치", "source": "공고명", "lines": []}
    if not (text or "").strip():
        return {"kind": "확인 필요", "source": None, "lines": []}
    positive, negative = [], []
    for raw in text.splitlines():
        line = re.sub(r"\s+", " ", raw).strip()
        if len(line) < 6 or line.startswith("===") or not _MAKE_LINE.search(line):
            continue
        (negative if _NOT_THIS_JOB.search(line) else positive).append(line[:160])
    positive = list(dict.fromkeys(positive))
    negative = list(dict.fromkeys(negative))
    if len(positive) >= _EVIDENCE_MIN:
        return {"kind": "설계+제작설치", "source": "첨부", "lines": positive[:3]}
    return {"kind": "설계만", "source": "첨부", "lines": (negative or positive)[:3]}
