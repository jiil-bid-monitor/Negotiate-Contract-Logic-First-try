"""정량평가서 — 규칙이 못 읽은 곳만 Claude에게 묻기(시험 단계, 2026-11-11 사용자 결정).

방식(사용자 결정 'MAX로 하자'): 서버 PC에 설치·로그인된 Claude Code(Max 구독)를 `claude -p`로 부름 — API 키·토큰 요금 없음,
대신 Max 사용량 한도를 같이 씀. 회사 자료는 보내지 않음 — 보내는 것은 공개 공고 문서(제안요청서·공고문)의 글과
서식 이름표 낱말뿐. 받은 답은 정해진 모양(JSON 스키마)으로만 받고, 값 채우기는 지금처럼 이 PC 안의 코드가 함.

    ask(prompt, schema)          — claude -p 한 번(도구 없음, 세션 저장 없음). 실패하면 AiError
    criteria_from_text(text)     — 제안요청서 글 → 정량평가 배점기준(selfscore.Criterion 목록)
    map_labels(labels)           — 서식 이름표 낱말 → 회사 정보 항목(FIELDS) 또는 'none'

명령 경로는 `.env` CLAUDE_CMD(없으면 PATH의 claude), 모델은 CLAUDE_MODEL(기본 sonnet).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from .selfscore import GRADES, Band, Criterion

FIELDS = ["회사명", "대표자", "주소", "전화번호", "팩스번호", "사업자등록번호", "법인등록번호", "이메일", "홈페이지",
          "설립일", "자본금", "종업원 수", "업태", "종목", "대표자 생년월일", "공고번호", "공고명", "none"]
KINDS = ["track", "staff", "patent", "credit", "sanction", "other"]
MAX_CHARS = 60_000  # 제안요청서 글이 길면 정량평가 부분 앞뒤로만(사용량 아끼기)


class AiError(RuntimeError):
    pass


def command() -> list[str]:
    """claude 실행 명령. Windows npm 설치(claude.cmd)는 인자 따옴표가 깨질 수 있어 node + cli.js로 부름."""
    cmd = os.environ.get("CLAUDE_CMD", "").strip() or shutil.which("claude") or ""
    if not cmd:
        raise AiError("claude 명령을 찾지 못함 — 서버 PC에 Claude Code를 설치하고 로그인(.env CLAUDE_CMD로 경로 지정 가능)")
    if cmd.lower().endswith((".cmd", ".bat")):
        cli = Path(cmd).parent / "node_modules" / "@anthropic-ai" / "claude-code" / "cli.js"
        node = shutil.which("node")
        if cli.exists() and node:
            return [node, str(cli)]
    return [cmd]


def ask(prompt: str, schema: dict, model: str | None = None, timeout: int = 600, run=subprocess.run) -> dict:
    """claude -p 한 번 — 글은 표준 입력으로(명령줄 길이 제한), 답은 스키마대로 검사된 JSON. 도구는 모두 끔."""
    args = command() + ["-p", "--output-format", "json", "--json-schema", json.dumps(schema, ensure_ascii=False),
                        "--tools", "", "--no-session-persistence", "--model", model or os.environ.get("CLAUDE_MODEL", "sonnet")]
    with tempfile.TemporaryDirectory() as cwd:  # 빈 폴더에서 — 이 저장소의 CLAUDE.md 등을 읽지 않게
        try:
            done = run(args, input=prompt, capture_output=True, text=True, encoding="utf-8", timeout=timeout, cwd=cwd)
        except (OSError, subprocess.TimeoutExpired) as err:
            raise AiError(f"claude 실행 실패: {err}") from err
    if done.returncode != 0:
        raise AiError(f"claude 종료 코드 {done.returncode}: {(done.stderr or done.stdout or '')[:300]}")
    try:
        env = json.loads(done.stdout)
    except json.JSONDecodeError as err:
        raise AiError(f"claude 답을 읽지 못함: {done.stdout[:200]}") from err
    if isinstance(env, dict) and env.get("is_error"):
        raise AiError(f"claude 오류: {str(env.get('result'))[:300]}")
    out = env.get("structured_output") if isinstance(env, dict) else None
    if out is None and isinstance(env, dict):  # 스키마 답이 결과 글에 JSON으로 온 경우
        text = str(env.get("result") or "")
        m = re.search(r"\{.*\}", text, re.S)
        try:
            out = json.loads(m.group(0)) if m else None
        except json.JSONDecodeError:
            out = None
    if not isinstance(out, dict):
        raise AiError("claude 답에 정해진 모양의 결과가 없음")
    return out


# ── 배점기준 ───────────────────────────────────────────────────────────
CRITERIA_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["items"],
    "properties": {"items": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["name", "kind", "points", "per_grade", "bands", "credit_rows", "note"],
        "properties": {
            "name": {"type": "string"},
            "kind": {"type": "string", "enum": KINDS},
            "points": {"type": "number"},
            "per_grade": {"type": "object", "additionalProperties": False,
                          "properties": {g: {"type": "number"} for g in GRADES}},
            "bands": {"type": "array", "items": {
                "type": "object", "additionalProperties": False, "required": ["text", "lo", "hi", "unit", "score"],
                "properties": {"text": {"type": "string"}, "lo": {"type": ["number", "null"]}, "hi": {"type": ["number", "null"]},
                               "unit": {"type": "string", "enum": ["원", "건", "점", ""]}, "score": {"type": "number"}}}},
            "credit_rows": {"type": "array", "items": {
                "type": "object", "additionalProperties": False, "required": ["회사채", "기업어음", "기업신용평가등급", "score"],
                "properties": {"회사채": {"type": "string"}, "기업어음": {"type": "string"}, "기업신용평가등급": {"type": "string"},
                               "score": {"type": "number"}}}},
            "note": {"type": "string"}}}}}}

CRITERIA_PROMPT = """아래는 한국 공공 입찰 제안요청서(공개 문서)의 글이다. '정량적 평가'(정량평가, 사업수행능력 평가 등) 배점기준만 뽑아라.
정성평가·기술제안서 평가·가격평가는 빼라. 항목마다:
- name: 문서에 적힌 항목 이름 그대로, points: 그 항목 배점(상한), kind: 실적=track, 기술인력·참여기술자·기술능력=staff,
  특허·보유기술=patent, 경영상태·신용평가=credit, 신인도·제재=sanction, 그 밖=other
- per_grade: 기술인력이 '1인당 등급별 점수'(특급 각 0.5점 등)면 등급별 점수(기술사·특급·고급·중급·초급 중 문서에 있는 것만). 아니면 {}
- bands: 금액·건수·점수 구간표면 구간마다 {text: 문서 글 그대로, lo: 이상(포함) 값 또는 null, hi: 미만(제외) 값 또는 null,
  unit: 원/건/점, score}. 금액은 원 단위 숫자(5억 = 500000000). '이하'면 hi = 그 값 + 1(건) 또는 + 0.000001.
- credit_rows: 경영상태(신용평가등급) 표면 줄마다 {회사채, 기업어음, 기업신용평가등급: 그 줄의 등급 글(예: "BBB- 이상", "BB+, BB0, BB-",
  "CCC+ 이하"), score}. 종류 구분 없이 '신용등급'만 적혔으면 회사채·기업신용평가등급 칸에 같은 글, 기업어음은 "". 신용등급 표가 아니면 [].
  신용등급 표는 bands에 넣지 마라.
- note: 인정 기준·기간·주석 등 채점에 필요한 문장을 짧게.
문서에 없는 값은 만들지 말고 비워라. 글:
"""


def _trim(text: str) -> str:
    if len(text) <= MAX_CHARS:
        return text
    i = max(0, (re.search(r"정\s*량\S{0,4}\s*평\s*가|사업\s*수행\s*능력\s*평가", text) or re.match("", text)).start() - 2000)
    return text[i:i + MAX_CHARS]


def to_criteria(data: dict) -> list[Criterion]:
    out = []
    for it in data.get("items") or []:
        kind = it.get("kind") if it.get("kind") in KINDS else "other"
        c = Criterion(str(it.get("name") or "").strip(), float(it.get("points") or 0), "" if kind == "other" else kind,
                      note=str(it.get("note") or ""))
        c.per_grade = {g: float(v) for g, v in (it.get("per_grade") or {}).items() if g in GRADES and v is not None}
        for b in it.get("bands") or []:
            c.bands.append(Band(b.get("lo"), b.get("hi"), float(b.get("score") or 0), str(b.get("text") or "")))
            c.unit = c.unit or ("" if b.get("unit") == "점" else str(b.get("unit") or ""))
        if any(b.get("unit") == "점" for b in it.get("bands") or []):
            c.unit = "점"
        c.credit_rows = _credit_rows(it)
        if kind == "credit" and c.credit_rows:
            c.bands, c.unit = [], ""  # 신용등급은 credit_rows로 채점(selfscore._credit)
        if c.name and c.points > 0:
            out.append(c)
    return out


_GRADE = re.compile(r"\b[A-D][A-D0-9+\-]*")


def _credit_rows(it: dict) -> list[tuple[dict[str, str], float]]:
    """신용등급 표 → selfscore.Criterion.credit_rows [({종류: 등급 글}, 점수)]. 예전 답처럼 bands 글에 등급이 섞여 오면(종류 없는 '신용등급 BBB- 이상')
    회사채·기업신용평가등급 같은 글로."""
    rows = []
    for r in it.get("credit_rows") or []:
        rules = {k: str(r.get(k) or "").strip() for k in ("회사채", "기업어음", "기업신용평가등급") if str(r.get(k) or "").strip()}
        if rules:
            rows.append((rules, float(r.get("score") or 0)))
    if rows or it.get("kind") != "credit":
        return rows
    for b in it.get("bands") or []:
        text = str(b.get("text") or "")
        if _GRADE.search(text.upper()):
            rule = re.sub(r"^\s*(?:신용\s*등급|신용\s*평가\s*등급)\s*", "", text).strip()
            rows.append(({"회사채": rule, "기업신용평가등급": rule}, float(b.get("score") or 0)))
    return rows


def criteria_from_text(text: str, **kw) -> list[Criterion]:
    return to_criteria(ask(CRITERIA_PROMPT + _trim(text), CRITERIA_SCHEMA, **kw))


# ── 이름표 ─────────────────────────────────────────────────────────────
LABEL_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["labels"],
    "properties": {"labels": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["label", "field"],
        "properties": {"label": {"type": "string"}, "field": {"type": "string", "enum": FIELDS}}}}}}

LABEL_PROMPT = """한국 공공 입찰 서식의 표 이름표(칸 이름)들이다. 각 이름표 옆 칸에 입찰 참가 회사(제출자 본인)의 어떤 정보를 적는지 골라라.
고를 수 있는 항목: {fields}. 담당자·대리인·발주처·발급기관·날짜·서명처럼 회사 기본 정보가 아니거나 제출자가 아닌 쪽의 칸은 none.
이름표마다 하나씩, 이름표 글은 받은 그대로 돌려줘라. 이름표:
"""


def map_labels(labels: list[str], **kw) -> dict[str, str]:
    labels = [x for x in dict.fromkeys(labels) if x][:200]
    if not labels:
        return {}
    data = ask(LABEL_PROMPT.format(fields=", ".join(FIELDS)) + "\n".join(labels), LABEL_SCHEMA, **kw)
    return {str(x.get("label")): str(x.get("field")) for x in data.get("labels") or [] if x.get("field") in FIELDS}
