"""공고 유형 가리기 — 협상·적격심사·사업수행능력평가(PQ)·종합심사·규격가격동시입찰·그 밖.

낙찰방법(나라장터 sucsfbidMthdNm)을 먼저 보고, 모르면 첨부 글의 표현으로 가린다.
유형마다 제출 시점·서식이 있는 곳·기본 준비 서류를 config/notice_kinds.json에서 읽는다.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config" / "notice_kinds.json"


def _sq(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def detect(award_method: str = "", contract_method: str = "", texts: dict[str, str] | None = None) -> str:
    a = _sq(award_method) + _sq(contract_method)
    if "협상" in a:
        return "negotiation"
    if "규격" in a and "가격" in a:
        return "spec_price"
    if "종합심사" in a:
        return "comprehensive"
    if "적격" in a:
        return "qualified"
    body = _sq(" ".join((texts or {}).values()))[:400_000]
    if re.search(r"협상에의한계약|우선협상대상자", body):
        return "negotiation"
    if re.search(r"종합심사낙찰제|종합심사대상", body):
        return "comprehensive"
    if re.search(r"사업수행능력평가(?:서|기준|대상|결과)|사전심사\(PQ\)|PQ심사", body) and not re.search(r"정량적(?:지표|평가)", body):
        return "pq"
    if re.search(r"적격심사", body):
        return "qualified"
    return "other"


def guide(kind: str) -> dict:
    kinds = {k["id"]: k for k in json.loads(CONFIG.read_text(encoding="utf-8"))["kinds"]}
    return kinds.get(kind, kinds["other"])
