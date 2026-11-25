"""낙찰 서류 엑셀 → 한글(hwpx). 한글이 못 여는 모양(빈 줄·빈 격자)이 안 생기는지."""
import io
import re
import zipfile
from datetime import datetime
from xml.etree import ElementTree as ET

import pytest
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, PatternFill, Side

from webapp import hwpxout
from webapp.postaward import PostAward

HP = "{http://www.hancom.co.kr/hwpml/2011/paragraph}"


def _book(path, formula=False):
    wb = Workbook()
    wb.active.title = "기본사항"
    ws = wb.create_sheet("착공계")
    ws.print_area = "A1:D8"
    ws["A1"] = "착 공 계"
    ws.merge_cells("A1:D1")
    ws["A2"] = "계약금액"
    ws["A2"].alignment = Alignment(horizontal="distributed")
    ws["B2"] = 390144500
    ws["B2"].number_format = '_-"₩"* #,##0_-'
    ws["A3"] = "계약년월일"
    ws["B3"] = datetime(2025, 11, 26)
    ws["B3"].number_format = 'yyyy"년" m"월" d"일"'
    ws["B3"].alignment = Alignment(horizontal="distributed")
    ws.merge_cells("A4:D8")  # 안쪽 줄에는 칸이 하나도 없음 — 그대로 옮기면 한글이 파일을 못 엶
    ws["A4"].border = Border(left=Side("medium"))
    ws["D2"].fill = PatternFill("solid", fgColor="FFFF00")
    ph = wb.create_sheet("사진대지")
    ph.print_area = "A1:B20"
    ph.sheet_properties.pageSetUpPr.fitToPage = True
    ph.page_setup.fitToHeight = 0
    ph.page_setup.orientation = "landscape"
    for r in range(1, 21):
        ph.row_dimensions[r].height = 60
        ph.cell(r, 1, f"사진 {r}")
    wb.create_sheet("증빙").print_area = "A1:A100"  # 아직 안 붙인 탭
    if formula:
        ws["C2"] = "=기본사항!B1"
    wb.save(path)


def _tables(data):
    z = zipfile.ZipFile(io.BytesIO(data))
    assert z.testzip() is None
    out = []
    for i in range(sum(n.startswith("Contents/section") for n in z.namelist())):
        out.append((z.read(f"Contents/section{i}.xml").decode(), [t for t in ET.fromstring(z.read(f"Contents/section{i}.xml")).iter(HP + "tbl")]))
    return z, out


def test_convert_makes_whole_grid_and_sections(tmp_path):
    f = tmp_path / "a.xlsx"
    _book(f)
    z, secs = _tables(hwpxout.convert(f))
    assert len(secs) == 2  # 기본사항·빈 증빙 탭은 뺌
    assert 'secCnt="2"' in z.read("Contents/header.xml").decode()
    assert "section1.xml" in z.read("META-INF/container.rdf").decode()
    text = secs[0][0]
    assert "₩ 390,144,500" in text and "2025 년 11 월 26 일" in text  # 숫자는 붙고 낱말만 벌어짐
    assert 'landscape="NARROWLY"' in secs[1][0] and len(secs[1][1]) > 1  # 가로 쪽, 여러 쪽
    for _, tables in secs:
        for t in tables:
            rows, cols = int(t.get("rowCnt")), int(t.get("colCnt"))
            seen = [[0] * cols for _ in range(rows)]
            for tr in t.findall(HP + "tr"):
                assert tr.findall(HP + "tc"), "칸 없는 줄"
            for tc in t.iter(HP + "tc"):
                a, s = tc.find(HP + "cellAddr"), tc.find(HP + "cellSpan")
                for i in range(int(s.get("rowSpan"))):
                    for j in range(int(s.get("colSpan"))):
                        seen[int(a.get("rowAddr")) + i][int(a.get("colAddr")) + j] += 1
            assert all(v == 1 for r in seen for v in r)


def test_unsaved_workbook_is_refused(tmp_path):
    f = tmp_path / "a.xlsx"
    _book(f, formula=True)
    with pytest.raises(hwpxout.NotSaved):
        hwpxout.convert(f)


def test_number_and_date_text():
    assert hwpxout.show(1234567, "#,##0") == "1,234,567"
    assert hwpxout.show(29.0, "0.00") == "29.00"
    assert hwpxout.show(0.8128, "0.00%") == "81.28%"
    assert hwpxout.show(datetime(2026, 1, 5), "yyyy\\. m\\. d\\.") == "2026. 1. 5."
    assert hwpxout.show(datetime(2026, 1, 5), "mm-dd-yy") == "2026-01-05"
    assert hwpxout.show(-5, '_-"₩"* #,##0_-;\\-"₩"* #,##0_-') == "-₩ 5"


def test_postaward_hwpx_stays_in_folder(tmp_path, monkeypatch):
    folder = tmp_path / "공고" / "2_착공"
    folder.mkdir(parents=True)
    _book(folder / "착공서류.xlsx")
    pa = PostAward.__new__(PostAward)
    monkeypatch.setattr(pa, "_read", lambda: {"k": {"folder": str(tmp_path / "공고")}}, raising=False)
    name, body = pa.hwpx("k", "2_착공/착공서류.xlsx")
    assert name == "착공서류.hwpx" and body[:2] == b"PK"
    (tmp_path / "밖.xlsx").write_bytes(b"x")
    with pytest.raises(ValueError):
        pa.hwpx("k", "../밖.xlsx")


def _hcell(path):
    """한셀이 저장한 모양으로 — 글꼴·칸 모양이 AlternateContent(한셀용 + 엑셀용) 안, 색은 소문자."""
    with zipfile.ZipFile(path) as z:
        items = {n: z.read(n) for n in z.namelist()}
    x = items["xl/styles.xml"].decode().replace("FFFF00", "ffff00")
    head = x[:x.index("<cellXfs")]
    body = x[len(head):]
    body = re.sub(r"(<xf [^>]*?/>|<xf [^>]*>.*?</xf>)", lambda m: '<mc:AlternateContent xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006">'
                  f'<mc:Choice xmlns:hs="h" Requires="hs">{m[0]}</mc:Choice><mc:Fallback>{m[0]}</mc:Fallback></mc:AlternateContent>', body, flags=re.S)
    items["xl/styles.xml"] = (head + body).encode()
    with zipfile.ZipFile(path, "w") as w:
        for n, d in items.items():
            w.writestr(n, d)


def test_hcell_saved_workbook(tmp_path):
    from webapp.xlsxread import load_workbook

    f = tmp_path / "a.xlsx"
    _book(f)
    _hcell(f)
    ws = load_workbook(f)["착공계"]
    assert ws["D2"].fill.fgColor.rgb == "00FFFF00" and ws["B3"].number_format == 'yyyy"년" m"월" d"일"'
    assert hwpxout.convert(f)[:2] == b"PK"
    assert hwpxout.show(5, r'_("$"* #,##0_);_("$"* \(#,##0\);_("$"* "-"_);_(@_)') == "₩ 5"


def test_saved_pictures_keep_size_for_hcell(tmp_path):
    from openpyxl.drawing.image import Image as XlImage
    from PIL import Image

    from webapp import xlsxfit

    Image.new("RGB", (60, 20)).save(tmp_path / "logo.png")
    wb = Workbook()
    wb.active.add_image(XlImage(str(tmp_path / "logo.png")), "A1")
    f = tmp_path / "a.xlsx"
    wb.save(f)
    xlsxfit.keep_pic_size(f)
    with zipfile.ZipFile(f) as z:
        assert '<a:ext cx="571500" cy="190500"/></a:xfrm>' in z.read("xl/drawings/drawing1.xml").decode()
