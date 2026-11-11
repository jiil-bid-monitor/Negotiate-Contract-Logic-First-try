"""정량평가서 Claude 시험(quantdocs.aiassist) — 실제 claude는 부르지 않고 가짜 실행으로(11/11)."""

from __future__ import annotations

import json
import subprocess

import pytest

from quantdocs import aiassist
from quantdocs.health import doc_text


def fake_run(payload, code=0, holder=None):
    def run(args, **kw):
        if holder is not None:
            holder.update(args=args, input=kw.get("input"), cwd=kw.get("cwd"))
        return subprocess.CompletedProcess(args, code, stdout=json.dumps(payload, ensure_ascii=False), stderr="")
    return run


def test_ask_uses_no_tools_and_reads_structured_output(monkeypatch):
    monkeypatch.setenv("CLAUDE_CMD", "claude")
    seen = {}
    out = aiassist.ask("글", {"type": "object"}, run=fake_run({"structured_output": {"a": 1}, "result": ""}, holder=seen))
    assert out == {"a": 1}
    args = seen["args"]
    assert args[:2] == ["claude", "-p"] and args[args.index("--tools") + 1] == "" and "--json-schema" in args
    assert "--no-session-persistence" in args and seen["input"] == "글"  # 글은 표준 입력으로


def test_ask_falls_back_to_json_in_result_and_raises_on_error(monkeypatch):
    monkeypatch.setenv("CLAUDE_CMD", "claude")
    assert aiassist.ask("x", {}, run=fake_run({"result": '답: {"b": 2}'})) == {"b": 2}
    with pytest.raises(aiassist.AiError):
        aiassist.ask("x", {}, run=fake_run({"is_error": True, "result": "usage limit"}))
    with pytest.raises(aiassist.AiError):
        aiassist.ask("x", {}, run=fake_run({}, code=1))


def test_criteria_and_labels_conversion(monkeypatch):
    monkeypatch.setenv("CLAUDE_CMD", "claude")
    data = {"items": [
        {"name": "전문인력", "kind": "staff", "points": 3, "per_grade": {"특급": 0.5, "고급": 0.4}, "bands": [], "note": "최고 3점"},
        {"name": "수행실적", "kind": "track", "points": 5, "per_grade": {},
         "bands": [{"text": "5억 이상", "lo": 500000000, "hi": None, "unit": "원", "score": 5}], "note": ""},
        {"name": "사업이해도", "kind": "other", "points": 0, "per_grade": {}, "bands": [], "note": ""}]}
    monkeypatch.setattr(aiassist, "ask", lambda prompt, schema, **kw: data)
    crit = aiassist.criteria_from_text("글")
    assert [(c.name, c.kind, c.points) for c in crit] == [("전문인력", "staff", 3.0), ("수행실적", "track", 5.0)]  # 배점 0은 뺌
    assert crit[0].per_grade == {"특급": 0.5, "고급": 0.4} and crit[1].bands[0].lo == 500000000 and crit[1].unit == "원"
    monkeypatch.setattr(aiassist, "ask", lambda prompt, schema, **kw: {"labels": [
        {"label": "상호또는법인명", "field": "회사명"}, {"label": "담당자", "field": "none"}]})
    assert aiassist.map_labels(["상호또는법인명", "담당자", "상호또는법인명"]) == {"상호또는법인명": "회사명", "담당자": "none"}


def test_command_windows_cmd_uses_node(tmp_path, monkeypatch):
    cli = tmp_path / "node_modules" / "@anthropic-ai" / "claude-code" / "cli.js"
    cli.parent.mkdir(parents=True)
    cli.write_text("")
    monkeypatch.setenv("CLAUDE_CMD", str(tmp_path / "claude.cmd"))
    monkeypatch.setattr(aiassist.shutil, "which", lambda name: "/usr/bin/node" if name == "node" else None)
    assert aiassist.command() == ["/usr/bin/node", str(cli)]


def test_doc_text_keeps_table_rows():
    c = lambda t: f"<hp:tc><hp:subList><hp:p><hp:run><hp:t>{t}</hp:t></hp:run></hp:p></hp:subList></hp:tc>"
    xml = ("<hp:p><hp:run><hp:t>나. 기술능력 평가</hp:t></hp:run></hp:p><hp:p><hp:run><hp:tbl><hp:tr>" + c("항목평가") + c("배점")
           + "</hp:tr><hp:tr>" + c("전문인력") + c("3.0") + "</hp:tr></hp:tbl></hp:run></hp:p>")
    assert doc_text(xml) == "나. 기술능력 평가\n항목평가 | 배점\n전문인력 | 3.0"
