from pathlib import Path

import pytest
from openpyxl import load_workbook

from quantdocs.__main__ import main
from quantdocs.templates import FILE_NAME, build


def test_build_makes_four_sheets_with_headers(tmp_path: Path):
    out = build(tmp_path)
    wb = load_workbook(out)
    assert wb.sheetnames == ["안내", "회사정보", "실적목록", "인력목록", "연혁", "서류상태"]
    heads = [c.value for c in wb["실적목록"][1]]
    assert heads[:3] == ["번호", "업무", "사업명"] and "준공일" in heads and "부가세" in heads
    body = wb["실적목록"].iter_rows(min_row=2, values_only=True)
    assert all(v is None for row in body for v in row)  # 내용은 비어 있음(머리글만, 형식만 걸어 둠)
    choices = [dv.formula1 for dv in wb["실적목록"].data_validations.dataValidation if dv.type == "list"]
    assert '"물품,용역,공사"' in choices
    status = [dv.formula1 for dv in wb["서류상태"].data_validations.dataValidation if dv.type == "list"]
    assert any("회사 연혁 + 증빙 서류" in f and "경영상태" in f for f in status)


def test_build_does_not_overwrite(tmp_path: Path):
    build(tmp_path)
    with pytest.raises(FileExistsError):
        build(tmp_path)


def test_cli_templates(tmp_path: Path):
    assert main(["templates", "--out", str(tmp_path)]) == 0
    assert (tmp_path / FILE_NAME).exists()
    assert main(["templates", "--out", str(tmp_path)]) == 1
