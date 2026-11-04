import datetime as dt
import os
from pathlib import Path

import pytest
from openpyxl import load_workbook

from quantdocs.catalog import load_catalog
from quantdocs.notice import TrackReq, classify, parse_track, read_forms, read_submit_items, track_candidates


def test_parse_track():
    assert parse_track("공고일 기준 3년 이내 건당 사업비 1억 원 이상의 수행실적") == (3, 100_000_000, True)
    assert parse_track("최근 3년간 5억 원 이상 사업수행실적(금액 기준)을 합산 적용") == (3, 500_000_000, False)
    assert parse_track("단일 계약 5,000만원 이상 실적") == (None, 50_000_000, True)


def test_classify():
    cat = load_catalog()
    assert classify("회사경영상태 (신용평가 등급확인서)", cat).id == "credit_rating"
    assert classify("최근 3년간 사업수행실적 집계표", cat).id == "track_record"
    assert classify("기술인력 개인별 이력사항", cat).id == "staff"
    assert classify("법인 등기부 등본 1부", cat) is None  # 회사 공통 발급 서류
    assert classify("보안서약서", cat) is None  # 공고 서식


def test_read_submit_items_takes_circled_list_and_dash_lines():
    text = "  2) 제출서류\n   ① 입찰참가 신청서\n   ② 실적증명서 원본\n    - 3년 이내 1억 원 이상\n 나. 다음 절\n   ① 다른 목록"
    items = read_submit_items(text, "x.hwpx", load_catalog())
    assert [i.text for i in items] == ["입찰참가 신청서", "실적증명서 원본 — 3년 이내 1억 원 이상"]


def test_read_forms_three_layouts():
    cat = load_catalog()
    table = "정량적 평가서 | 2부 | 사업 수행 능력 | - 사업수행능력평가자료(표지) | 서식13\n - 일반현황 및 연혁 | 서식14\n정성적 평가서 | 9부"
    unrolled = ("정량적\n평가서\n2부\n - 제안사 연혁 및 일반현황\n서식12\n - 회사경영상태 (신용평가 등급확인서)\n서식19\n"
                "정성적\n평가서\n - 가격제안서 봉투서식 \n - 가격제안 입찰서\n - 가격제안서\n - 가격산출 내역서\n서식28\n~31")
    listed = "□ 협상에 의한 계약 필요서류\n   1) 가격제안 입찰서(서식 7)\n   3) 참여전문기술자 보유현황(서식 9)\n" \
             "  - 질의는 서면 질의서 <서식 32>를 작성, 이메일로 제출"
    a = {f.no: (f.name, f.in_quant) for f in read_forms(table, "a", cat)}
    assert a == {13: ("사업수행능력평가자료(표지)", True), 14: ("일반현황 및 연혁", True)}
    b = {f.no: (f.name, f.in_quant) for f in read_forms(unrolled, "b", cat)}
    assert b[12] == ("제안사 연혁 및 일반현황", True) and b[19][1] is True
    assert [b[n][0] for n in (28, 29, 30, 31)] == ["가격제안서 봉투서식", "가격제안 입찰서", "가격제안서", "가격산출 내역서"]
    assert b[28][1] is False
    c = {f.no: f.name for f in read_forms(listed, "c", cat)}
    assert c == {7: "가격제안 입찰서", 9: "참여전문기술자 보유현황"}  # 문장 속 <서식 32>는 목록이 아님


def test_read_submit_items_paren_list_and_schedule_line():
    text = ("가. 입찰참가신청 및 제출서류\n  1) 입찰참가신청 등록 : 2026. 09. 03.(목) 10:00\n    -제출방법 : 방문접수\n"
            "  2) 제출서류\n   ① 입찰참가 신청서\n   ② 사업자등록증 사본\n 나. 다음\n"
            "□ 협상에 의한 계약 필요서류\n   1) 가격제안 입찰서(서식 7)\n   2) 기업신용평가등급 확인서\n")
    items = [i.text for i in read_submit_items(text, "x", load_catalog())]
    assert items == ["입찰참가 신청서", "사업자등록증 사본", "가격제안 입찰서(서식 7)", "기업신용평가등급 확인서"]


def _t(no, done, amount, share=100, vat="포함"):
    return {"번호": no, "사업명": f"가상{no}", "준공일": done, "계약금액(원)": amount, "지분율(%)": share, "부가세": vat}


def test_track_candidates_period_amount_share():
    ref = dt.date(2026, 8, 20)
    tracks = [_t(1, dt.date(2024, 6, 30), 250e6), _t(2, dt.date(2022, 12, 31), 180e6),
              _t(3, dt.date(2025, 9, 30), 150e6, 50), _t(4, dt.date(2025, 11, 20), 120e6, None, None)]
    single = track_candidates(TrackReq("", "", 3, 100_000_000, True), tracks, ref)
    assert [t["번호"] for t in single] == [1, 4]  # 2는 3년 지남, 3은 지분 50% → 7,500만
    assert single[1]["확인할 것"] == "부가세 포함 여부 확인"
    cumulative = track_candidates(TrackReq("", "", 3, 500_000_000, False), tracks, ref)
    assert [t["번호"] for t in cumulative] == [1, 3, 4]  # 누적은 건별 금액으로 거르지 않음


FIXTURES = Path(__file__).resolve().parent / "fixtures" / "attachments"


@pytest.mark.skipif(not (FIXTURES / "jeongseon_culture_center_rfp.hwpx").exists(), reason="프로젝트 1 공고 표본 없음")
def test_jeongseon_sample(tmp_path: Path):
    from quantdocs.notice import read_notice, write_sheet

    sheet = read_notice([FIXTURES / "jeongseon_culture_center_rfp.hwpx"])
    forms = {f.no: f for f in sheet.forms}
    assert len(sheet.forms) == 31 and forms[21].doc.id == "credit_rating" and forms[16].doc.id == "track_record"
    assert forms[21].in_quant and not forms[1].in_quant and forms[4].name == "공동수급표준협정서"
    assert any("실적증명서" in it.text and "1억" in it.text for it in sheet.submit_items)
    kinds = {(t.kind, t.per_project, t.min_amount) for t in sheet.track_reqs}
    assert ("제출 요건", True, 100_000_000) in kinds and ("평가 실적 인정 기준", False, 500_000_000) in kinds
    out = write_sheet(sheet, tmp_path, ref=dt.date(2026, 8, 20))
    assert load_workbook(out).sheetnames == ["공고정보", "서식목록", "제출서류", "실적요건", "실적후보"]


@pytest.mark.skipif(not (FIXTURES / "jeongseon_culture_center_rfp.hwpx").exists(), reason="프로젝트 1 공고 표본 없음")
def test_cut_forms_sample(tmp_path: Path):
    import xml.etree.ElementTree as ET
    import zipfile

    from quantdocs.forms_cut import cut
    from quantdocs.notice import _import_nego

    attachments, _, _ = _import_nego()
    forms = cut(FIXTURES / "jeongseon_culture_center_rfp.hwpx", tmp_path)
    nos = [f.no for f in forms]
    assert len(forms) == 31 and nos[:5] == ["1", "2", "3", "4-1", "4-2"] and "12" not in nos  # 서식12(질의서)는 본문 없음
    for f in forms:
        z = zipfile.ZipFile(tmp_path / f.file)
        assert z.namelist()[0] == "mimetype"
        for n in z.namelist():
            if n.endswith((".xml", ".hpf")):
                ET.fromstring(z.read(n))  # 깨지지 않은 XML
        text = attachments.extract_text((tmp_path / f.file).read_bytes(), "hwpx")
        assert text.lstrip().startswith(("〔서식", "[서식")) and f"서식 {f.no}" in text.replace("서식", "서식 ").replace("  ", " ")
    assert any("서약서" in f.file for f in forms if f.no == "5")


def test_form_table_with_ho_and_submit_table():
    """'서식 1호 | 이름 |' 목차 표와 '구비서류 | 서식번호' 제출서류 표(11/03 영동군 모양 — 가짜 글)."""
    from quantdocs.catalog import load_catalog
    from quantdocs.notice import read_forms, read_submit_items, submit_quant_forms

    text = ("3) 제출서류\n\n구비서류 | 서식번호\n• 기본서류 | \n - 입찰참가신청서  | 서식  2호\n"
            " - 사업자등록증   ※ 모두 제출 | \n - 위임장 및 재직증명서 | 서식  3,4호\n"
            "• 사업수행능력 제안서(정량적 평가서류) | 서식 17호\n - 일반현황 및 연혁 | 서식  19호\n"
            " - 관련분야 사업 수행실적 및 실적증명서 | 서식  20, 21호\n• 기술제안서(정성적 평가서류) | 서식 27호\n"
            " - 사업제안서 | \n라. 제안발표\n\n구분 | 내용 | 비고\n서식 1호 |  입찰참가신청서류 표지 | \n"
            "서식 16호 |  안전보건 확보 의무 이행 서약서 ※ 서식 16호 이행 서약서는 계약 시 제출 | \n"
            "서식 19호 |  일반현황 및 연혁 | \n")
    cat = load_catalog()
    forms = {f.no: f.name for f in read_forms(text, "x", cat)}
    assert forms == {1: "입찰참가신청서류 표지", 16: "안전보건 확보 의무 이행 서약서", 19: "일반현황 및 연혁"}
    items = read_submit_items(text, "x", cat)
    assert [(i.text.split()[0], i.form_no) for i in items] == [
        ("입찰참가신청서", "2"), ("사업자등록증", ""), ("위임장", "3,4"), ("일반현황", "19"), ("관련분야", "20,21"), ("사업제안서", "")]
    assert submit_quant_forms(text) == {17, 19, 20, 21}
