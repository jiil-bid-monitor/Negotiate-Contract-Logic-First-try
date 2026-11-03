"""회사 원본 → 회사자료 양식 변환(가짜 자료 — 실제 회사 자료 아님)."""

import zipfile
from datetime import date
from pathlib import Path

from openpyxl import Workbook, load_workbook

from quantdocs import importer


def test_validity_and_bizreg():
    assert importer.validity_from_name("조형물(~27.6.17).pdf") == (date(2027, 6, 17), "")
    assert importer.validity_from_name("벤처기업확인서(-29.8.22).pdf")[0] == date(2029, 8, 22)
    assert importer.validity_from_name("정보인프라(~26년12.25).pdf")[0] == date(2026, 12, 25)
    assert importer.validity_from_name("카운터(~27).pdf")[0] is None and "확인" in importer.validity_from_name("카운터(~27).pdf")[1]
    assert importer.validity_from_text("유효기간 : 2026-04-01 ~ 2027-03-31") == (date(2026, 4, 1), date(2027, 3, 31))
    assert importer.validity_from_text("•유효기간 2026년08월23일~ 2029년 08월22일")[1] == date(2029, 8, 22)
    info = importer.read_bizreg("등록번호 : 123-45-67890\n법 인 명 ( 단 체 명 ) : (주)가상\n대표자(대표유형) :\n"
                                "개 업 연 월 일 : 2010 년 01 월 02 일 법 인 등 록 번 호 : 110111-1234567\n"
                                "본 점 소 재 지 : 가상시 가상로 1\n홍길동\n")
    assert info == {"사업자등록번호": "123-45-67890", "회사명": "(주)가상", "법인등록번호": "110111-1234567",
                    "주소": "가상시 가상로 1", "설립일": "2010-01-02", "대표자": "홍길동"}


def test_read_direct_keeps_latest(monkeypatch):
    text = ("2025년 06월 24일\n제 2025-0001-00001 호\n물품분류번호 세부품명 유효기간\n"
            "8111229901 소프트웨어유지및지원서비\n스\n   2025.09.12. ~\n2027.09.11. 2\n"
            "8111229901 소프트웨어유지및지원서비\n스\n   2023.09.20. ~\n2025.09.19. 1\n"
            "6012100201 조형물    2025.06.18. ~\n2027.06.17. 1\n")
    monkeypatch.setattr(importer, "_pdf_text", lambda p: text)
    items = importer.read_direct(Path("x.pdf"))
    assert [(i["code"], i["name"], i["end"]) for i in items] == [
        ("8111229901", "소프트웨어유지및지원서비스", date(2027, 9, 11)), ("6012100201", "조형물", date(2027, 6, 17))]


def test_run(tmp_path: Path, monkeypatch):
    wb = Workbook()
    ws = wb.active
    ws.title = "25년"
    ws.append([None, "25년 준공"])
    ws.append(["No.", "사업명", "계약종류", "사업기간", "계약금"])
    ws.append([1, "(가상) 전시관 조성 공사", "입찰", "2025.01.02-2025.03.04", 100_000_000])
    ws.append([2, "(가상) 홍보 영상", "수의", None, 5_000_000])
    ws.append([None, None, None, None, 105_000_000])  # 합계 줄
    orders = tmp_path / "수주.xlsx"
    wb.save(orders)
    corp = tmp_path / "corp.zip"
    with zipfile.ZipFile(corp, "w") as z:
        z.writestr("실내건축공사업-가상.pdf", b"%PDF fake")
        z.writestr("알수없는서류(~30.1.1).pdf", b"%PDF fake")
    monkeypatch.setattr(importer, "_pdf_text", lambda p: "")
    rep = importer.run(tmp_path / "out", orders, corp, None, today=date(2026, 11, 3))
    assert rep.tracks == 2 and rep.docs == 2
    out = load_workbook(tmp_path / "out" / "회사자료_양식.xlsx")
    t = out["실적목록"]
    assert (t["B2"].value, t["C2"].value, t["F2"].value.date(), t["G2"].value) == ("공사", "(가상) 전시관 조성 공사", date(2025, 3, 4), 100_000_000)
    assert t["F3"].value is None and "준공일 확인" in t["M3"].value
    d = out["서류상태"]
    assert d["D2"].value == "0006, 4990" and d["I2"].value == "법인/실내건축공사업-가상.pdf"
    assert d["F3"].value.date() == date(2030, 1, 1) and "미분류" in d["K3"].value
    assert (tmp_path / "out" / "files" / "법인" / "실내건축공사업-가상.pdf").exists()


def test_read_registration():
    text = ("전 화 번 호 02-000-0000 팩 스 번 호 02-000-0001\n입찰참가 등록물품\n등록일 세부\n"
            "2025/08/04 5512190301 안내전광판 Y 직접생산증명\n서\n2025/08/\n04\n2027/08/\n03\n2025/08/\n04\n2027/08/\n03 정선공장\n"
            "2021/06/14 4511161601 비디오프로젝터 N\n"
            "2015/08/28 7215409902 전시홍보관설치서\n비스 Y 직접생산증명\n서\n2024/11/\n12\n2026/11/\n11\n2024/11/\n12\n2026/11/\n11 다산공장\n"
            "등록업종 및 시공능력\n업종명 등록번호\n0006(실내건축공사업 - 2021.12.31 이\n전)\n가상제1호 2014/04/25 1,000 정상\n"
            "1426(소프트웨어사업자(패키지소프트웨\n어개발.공급사업)) B24 2024/10/17 2099/12/31 정상\n주력분야\n")
    r = importer.read_registration(text)
    assert r["info"] == {"전화번호": "02-000-0000", "팩스번호": "02-000-0001"}
    assert [(p["code"], p["name"], p["made"], p["reg_end"]) for p in r["products"]] == [
        ("5512190301", "안내전광판", True, date(2027, 8, 3)), ("4511161601", "비디오프로젝터", False, None),
        ("7215409902", "전시홍보관설치서비스", True, date(2026, 11, 11))]
    assert [(i["code"], i["status"]) for i in r["industries"]] == [("0006", "정상"), ("1426", "정상")]
    assert r["industries"][1]["name"] == "소프트웨어사업자(패키지소프트웨어개발.공급사업)"
