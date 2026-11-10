"""공고 문서 글을 Claude에 보내 업체 조건·제출 서류를 원문 그대로 뽑는다(규칙 목록이 모르는 서류까지).

`ANTHROPIC_API_KEY`가 없거나 호출이 실패하면 None — 규칙으로 만든 요약·체크리스트만 남는다.
AI가 지어낸 항목을 막으려고, 근거 문장이 실제 문서에 없는 항목은 버린다.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

log = logging.getLogger("under100m")

MODEL = "claude-opus-5-5"
MAX_CHARS = 300_000  # 공고 문서 몇 개면 보통 이 안 — 넘으면 앞부분만 보내고 요약에 적는다
SYSTEM = ("너는 한국 공공입찰 공고 문서를 읽는 도우미다. 주어진 문서에 실제로 적힌 내용만 뽑는다. "
          "업체 조건(참가 자격·제작 업체 조건)과 입찰 때 내는 제출 서류를 문서 표현 그대로 항목마다 적고, "
          "quote에는 그 항목이 나온 문서 문장을 글자 그대로 옮긴다. 문서에 없는 것은 쓰지 않는다. "
          "낙찰·계약 뒤에 내는 서류(착수계·준공 서류 등)는 제출 서류에 넣지 않는다.")
_ITEM = {"type": "object", "properties": {"text": {"type": "string"}, "quote": {"type": "string"}},
         "required": ["text", "quote"], "additionalProperties": False}
SCHEMA = {
    "type": "object",
    "properties": {
        "overview": {"type": "string", "description": "사업 내용 두세 문장"},
        "conditions": {"type": "array", "items": _ITEM},
        "documents": {"type": "array", "items": _ITEM},
        "notes": {"type": "array", "items": {"type": "string"}, "description": "담당자가 놓치기 쉬운 특이사항"},
    },
    "required": ["overview", "conditions", "documents", "notes"],
    "additionalProperties": False,
}


def _flat(text: str) -> str:
    return re.sub(r"\s+", "", text)


def keep_quoted(data: dict, source: str) -> dict:
    """근거 문장이 원문에 없는 조건·서류는 뺀다(띄어쓰기는 무시)."""
    flat = _flat(source)
    out = dict(data)
    for key in ("conditions", "documents"):
        items = data.get(key) or []
        out[key] = [i for i in items if _flat(i.get("quote", "")) and _flat(i["quote"]) in flat]
        if len(out[key]) < len(items):
            log.info("AI 요약: %s %d건은 근거 문장이 문서에 없어 뺌", key, len(items) - len(out[key]))
    return out


def summarize(docs: list[tuple[str, str]], client: Any = None) -> dict | None:
    """docs = [(파일 이름, 글)]. 돌려주는 값에 `truncated`(앞부분만 보냈는지)가 붙는다."""
    if client is None:
        if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
            return None
        import anthropic
        client = anthropic.Anthropic()
    import anthropic

    source = "\n\n".join(f"=== {name} ===\n{text}" for name, text in docs if text.strip())
    if not source:
        return None
    truncated = len(source) > MAX_CHARS
    try:
        response = client.beta.messages.create(
            model=os.environ.get("UNDER100M_LLM_MODEL", MODEL), max_tokens=16000, system=SYSTEM,
            messages=[{"role": "user", "content": source[:MAX_CHARS]}],
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
            betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        )
    except anthropic.APIError as err:
        log.warning("AI 요약 실패: %s", err)
        return None
    if response.stop_reason != "end_turn":
        log.warning("AI 요약 실패: stop_reason=%s", response.stop_reason)
        return None
    text = next((b.text for b in response.content if getattr(b, "type", None) == "text"), "")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as err:
        log.warning("AI 요약 실패: JSON 아님 %s", err)
        return None
    return {**keep_quoted(data, source), "truncated": truncated}


def lines(ai: dict) -> list[str]:
    """체크리스트 파일에 붙일 줄."""
    out = ["", "AI가 읽은 업체 조건·제출 서류 (공고문과 대조하세요)"]
    if ai.get("truncated"):
        out.append(f"※ 문서가 길어 앞 {MAX_CHARS:,}자만 읽음")
    out += ["[업체 조건]"] + [f"- {i['text']}" for i in ai.get("conditions") or []]
    out += ["[제출 서류]"] + [f"- {i['text']}" for i in ai.get("documents") or []]
    if ai.get("notes"):
        out += ["[특이사항]"] + [f"- {n}" for n in ai["notes"]]
    return out
