"""정량평가서 서식 읽기 건강 검사(quantdocs.health) — 11/10. (새 공고 미리 검사는 11/11 사용자 결정으로 삭제)"""

from __future__ import annotations

import zipfile
from pathlib import Path

from quantdocs import health


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

