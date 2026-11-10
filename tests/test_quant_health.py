"""정량평가서 서식 읽기 건강 검사(quantdocs.health)·새 협상 공고 미리 검사(webapp.quantprecheck) — 11/10."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

from quantdocs import health
from webapp import quantprecheck as qp


def _c(col, row, text, span=1):
    return (f'<hp:tc><hp:subList><hp:p paraPrIDRef="0"><hp:run charPrIDRef="0"><hp:t>{text}</hp:t></hp:run></hp:p></hp:subList>'
            f'<hp:cellAddr colAddr="{col}" rowAddr="{row}"/><hp:cellSpan colSpan="{span}" rowSpan="1"/></hp:tc>')


def _tbl(rows):
    return "<hp:p><hp:run><hp:tbl>" + "".join("<hp:tr>" + "".join(r) + "</hp:tr>" for r in rows) + "</hp:tbl></hp:run></hp:p>"


def test_unknown_labels_only_company_like_unknown_blank():
    xml = _tbl([
        [_c(0, 0, "업 체 명"), _c(1, 0, "")],  # 규칙이 아는 이름표 — 모르는 이름표 아님
        [_c(0, 1, "회사 규모"), _c(1, 1, "")],  # 회사 정보 낱말 + 모르는 이름표 + 빈 칸
        [_c(0, 2, "담당자 성명"), _c(1, 2, "")],  # 비워 두는 칸
        [_c(0, 3, "사업 내용"), _c(1, 3, "")],  # 회사 정보 낱말 없음
        [_c(0, 4, "회사 형태"), _c(1, 4, "주식회사")],  # 옆 칸이 차 있음
    ])
    got = health.unknown_labels(f"<hs:sec>{xml}</hs:sec>")
    assert got == ["회사규모"]


def test_check_missing_form_and_no_criteria(tmp_path: Path, monkeypatch):
    """목록 서식 1~3 중 2만 잘림 → 서식 빠짐, 자기평가표가 있는데 배점기준 없음 → 배점기준 못 읽음."""
    from quantdocs import build, forms_cut, notice, selfscore  # noqa: F401 — build을 먼저 불러야 가짜 read_notice가 build에 남지 않음
    from quantdocs.forms_cut import CutForm

    doc = tmp_path / "제안요청서.hwpx"
    with zipfile.ZipFile(doc, "w") as z:
        z.writestr("mimetype", "application/hwp+zip")
        z.writestr("Contents/section0.xml", "<hs:sec></hs:sec>")
    form_xml = "<hs:sec>" + _tbl([[_c(0, 0, "항목"), _c(1, 0, "배점"), _c(2, 0, "자기평점")]]) + "</hs:sec>"

    def fake_cut(path, out_dir, names=None):
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(out_dir / "f2.hwpx", "w") as z:
            z.writestr("Contents/section0.xml", form_xml)
        return [CutForm("2", "자기평가표", "", [], "f2.hwpx")]

    class Sheet:
        forms = [notice.Form(n, f"서식{n}", "x") for n in (1, 2, 3)]

    monkeypatch.setattr(forms_cut, "cut", fake_cut)
    monkeypatch.setattr(notice, "read_notice", lambda paths, catalog=None: Sheet())
    monkeypatch.setattr(selfscore, "read_criteria", lambda paths: [])
    h = health.check([doc], tmp_path / "w")
    kinds = h.counts()
    assert h.listed == [1, 2, 3] and h.cut == ["2"]
    assert kinds.get("서식 빠짐") == 1 and "1, 3" in next(i.detail for i in h.issues if i.kind == "서식 빠짐")
    assert kinds.get("배점기준 못 읽음") == 1
    assert "서식 빠짐" in health.summary(h)


def test_targets_only_negotiation_main_notices():
    run = {"candidates": [
        {"category": "협상", "kind": "본공고", "notice_no": "R26BK1", "notice_ord": "000", "title": "가"},
        {"category": "규격가격동시입찰", "notice_no": "R26BK2", "title": "나"},
        {"category": "입찰", "kind": "본공고", "notice_no": "R26BK3"},
        {"category": "협상", "kind": "사전규격", "notice_no": "R26BD4"},
    ]}
    assert [t["no"] for t in qp.targets(run)] == ["R26BK1", "R26BK2"]


def _health(issues):
    h = health.Health(listed=[1], cut=["1"])
    h.issues = [health.Issue(k, d) for k, d in issues]
    return h


def test_precheck_alerts_once_and_records(tmp_path: Path, monkeypatch):
    run = {"candidates": [{"category": "협상", "notice_no": f"R26BK{i}", "notice_ord": "000", "title": f"공고{i}"} for i in range(3)]}
    results = {"R26BK0": _health([("빈 칸", "표: 주소")]), "R26BK1": _health([]), "R26BK2": None}
    sent = []

    def fake_check(item, work, fetch=None):
        h = results[item["no"]]
        if h is None:
            raise RuntimeError("나라장터 접속 실패")
        return h, ["제안요청서.hwp"]

    monkeypatch.setattr(qp, "check_notice", fake_check)
    pc = qp.Precheck(tmp_path, lambda: run, lambda: False, send=lambda text: sent.append(text) or [])
    out = pc.run_once()
    assert len(sent) == 1 and "R26BK0-000" in sent[0] and "빈 칸" in sent[0]
    state = json.loads((tmp_path / "quant_precheck.json").read_text(encoding="utf-8"))
    assert set(state["done"]) == {"R26BK0-000", "R26BK1-000"} and state["done"]["R26BK0-000"]["alert"] == "보냄"
    assert state["failed"] == {"R26BK2-000": 1} and any("error" in o for o in out)
    # 다시 돌리면 검사한 공고는 건너뛰고, 실패한 공고는 MAX_TRIES번째에 그만
    for _ in range(qp.MAX_TRIES - 1):
        pc.run_once()
    state = json.loads((tmp_path / "quant_precheck.json").read_text(encoding="utf-8"))
    assert len(sent) == 1 and "R26BK2-000" in state["done"] and not state["failed"]


def test_precheck_without_alert_config_and_busy(tmp_path: Path, monkeypatch):
    run = {"candidates": [{"category": "협상", "notice_no": "R26BK9", "title": "공고"}]}
    monkeypatch.setattr(qp, "check_notice", lambda item, work, fetch=None: (_health([("서식 빠짐", "2")]), []))
    busy = [True]
    pc = qp.Precheck(tmp_path, lambda: run, lambda: busy[0], send=lambda text: None)
    assert pc.run_once() == []  # 작업 중엔 쉼
    busy[0] = False
    out = pc.run_once()
    assert out[0]["alert"] == "알림 설정 없음"
