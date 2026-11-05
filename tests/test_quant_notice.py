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
    # 11/05 사용자 요청: 기간 조건만 — 단일 하한(1억)으로 거르지 않음(3은 지분 50% → 7,500만이어도 넣음), 2는 3년 지남
    assert [t["번호"] for t in single] == [1, 3, 4]
    assert single[2]["확인할 것"] == "부가세 포함 여부 확인" and single[1]["인정 금액(원)"] == 75e6
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


def test_forms_with_ho_ranges_unrolled_toc_and_body_heads():
    """11/04 당진교육종합센터(옛 hwp) 모양 — 제출서류 목록 '[서식2~10호]', 칸이 풀린 평가서 목차 '4. 사업수행실적' / '[서식5호, 6호]',
    본문 머리 '〔서식 5호〕' 다음 줄 제목(맨 앞 공고명 줄·'<편집용지>'는 건너뜀). 진단 줄을 본뜬 가짜 글."""
    from quantdocs.catalog import load_catalog
    from quantdocs.notice import read_forms

    title = "당진교육종합센터 전시체험 콘텐츠 제작·설치"
    text = "\n".join([
        " 관련서식  40", "Ⅰ",
        "▪기술제안서 10부(A3) [서식 1호]", " ※ 기술제안서 10부 중 1부만 업체명 기재 ",
        "▪사업수행능력 평가서 3부(A4) [서식2~10호]", "기타서류",
        "▪청렴서약서, 서약서 [서식11~12호], 발표 자료", "※ 세부사항은 반드시 공고문을 참고하시기 바랍니다. ",
        "▪ 제공된 표지(서식)와 용지서식을 사용",
        "1. 표지", "[서식2호]", "2. 일반현황 및 연혁", "[서식3호]", "3. 자기평가표", "[서식4호]",
        "4. 사업수행실적", "[서식5호, 6호]", "5. 기술인력", "[서식7, 8호]", "6. 기업신용평가", "[서식9호]",
        "7. 신인도", "[서식10호]", "4. 제안(요청)서 효력 및 유의사항",
        " 5. 입찰자는 수행실적 평가를 위하여 별지 제15호서식의 수행실적 총괄표와 …",
        "〔서식 1호〕", " <편집용지>", "용지 A3",
        "〔서식 2호〕", title, "사업수행능력 평가서",
        "〔서식 3호〕", "제안사 일반현황 및 연혁 ",
        "〔서식 4호〕", "사업수행능력 자기 평가표",
        "〔서식 5호〕", "전시분야 최근 3년간 사업수행실적",
        "〔서식 6호〕", "최근 3년간 사업이행실적 증명서",
        "〔서식 7호〕", "기술인력 보유 현황(공고일 기준)",
        "〔서식 8호〕", "투입 인력 계획",
        "〔서식 9호〕", "경영실태 확인서",
        "〔서식 10호〕", "행 정 처 분 확 인 서",
        "〔서식 11호〕", "청  렴  서  약  서",
        "〔서식 12호〕", "서     약     서",
    ])
    got = {f.no: (f.name, f.in_quant) for f in read_forms(text, f"★[제안요청서]{title.replace('·', ' ')}.hwp", load_catalog())}
    assert sorted(got) == list(range(1, 13))
    assert got[1] == ("기술제안서", False)
    assert got[2][0] == "사업수행능력 평가서"  # 공고명 줄은 건너뜀
    assert got[5][0] == "전시분야 최근 3년간 사업수행실적" and got[6][0] == "최근 3년간 사업이행실적 증명서"
    assert got[7][0] == "기술인력 보유 현황(공고일 기준)" and got[8][0] == "투입 인력 계획"
    assert got[10][0] == "행정처분확인서" and got[11][0] == "청렴서약서" and got[12][0] == "서약서"
    assert all(got[n][1] for n in range(2, 11))  # '사업수행능력 평가서 [서식2~10호]' = 정량 묶음
    assert not got[11][1] and not got[12][1]


def test_score_cell_is_not_form_name():
    """평가표 배점 칸('- 0.3점')이 서식 칸 바로 앞에 풀려 나와도 이름으로 안 씀 — 본문 머리 제목으로(11/04)."""
    from quantdocs.catalog import load_catalog
    from quantdocs.notice import read_forms

    text = "- 사업수행실적\n- 0.3점\n[서식5호]\n〔서식 5호〕\n전시분야 최근 3년간 사업수행실적\n"
    assert [(f.no, f.name) for f in read_forms(text, "x.hwp", load_catalog())] == [(5, "전시분야 최근 3년간 사업수행실적")]


def test_forms_angle_bracket_heads_and_broken_names():
    """11/04 필암서원(장성군) 모양 — 본문 머리 '〈서식 12〉', 목록 범위 '사업수행능력평가(정량적 지표) 자료 [서식11~19]',
    목록 이름 꼬리 '10부 (A4 규격 30매 이내) ※ …', 표 칸이 섞인 이름 '8 위임장 … (대리인이 등록할 경우'."""
    from quantdocs.catalog import load_catalog
    from quantdocs.notice import read_forms

    title = "필암서원 집성관 디지털 콘텐츠 보강 기획 및 제작설치"
    text = "\n".join([
        "▪사업수행능력평가(정량적 지표) 자료 [서식11~19]",
        "▪기술제안서(정성적 지표) 10부 (A4 규격 30매 이내) ※ 응모자 표기 1부, 응모자 미표기 [서식 20]",
        "- 8 위임장 및 재직증명서(대리인이 등록할 경우", "서식26",
        "〈서식 11〉", "사업수행능력평가자료 (표지)",
        "〈서식 12〉", "제안회사 일반현황 및 연혁",
        "〈서식 16〉", "최근 5년간 사업수행실적",
        "〈서식 19〉", "행정처분내용",
        "〈서식 20〉", f"「{title}」", "제 안 서",
        "〈서식 26〉", "위 임 장",
    ])
    got = {f.no: f.name for f in read_forms(text, f"[제안요청서]{title}.hwp", load_catalog())}
    assert got[11] == "사업수행능력평가자료 (표지)" and got[12] == "제안회사 일반현황 및 연혁"
    assert got[16] == "최근 5년간 사업수행실적" and got[19] == "행정처분내용"
    assert got[13] == "사업수행능력평가(정량적 지표) 자료 (11~19)"  # 본문 머리가 없는 번호는 범위 이름 그대로
    assert got[20] == "기술제안서(정성적 지표)"
    assert got[26] == "위임장"


def test_form_heads_any_symbol():
    """서식 머리는 괄호 모양을 가리지 않음 — 특수기호 + 서식 N + 특수기호(11/04 사용자 요청). 문장 속 '※서식 7호는'은 아님."""
    from quantdocs.catalog import load_catalog
    from quantdocs.notice import read_forms

    text = "\n".join(["◈서식 3◈", "제안회사 일반현황", "｢서식 4｣", "수행실적", "◆서식5호◆ 투입인력",
                      "〔서식 6-1〕", "하위서식", "〔서식 6〕", "공동수급협정서", "※서식 7호는 계약 시 제출"])
    got = [(f.no, f.name) for f in read_forms(text, "x.hwp", load_catalog())]
    assert got == [(3, "제안회사 일반현황"), (4, "수행실적"), (5, "투입인력"), (6, "공동수급협정서")]


def test_read_notice_prefers_body_titles_from_later_document(tmp_path, monkeypatch):
    """11/04 필암서원 실제 진단 줄 — 공고문 표 '사업수행능력평가(정량적 지표) 자료 2부 | 【서식 11~19】'(범위 이름)가 먼저 읽혀도
    제안요청서 본문 머리 '〈서식 12〉 제안회사 일반현황 및 연혁'이 이름이 됨. 표 칸 '서류 ① 사업자등록증 …'은 ① 앞만."""
    from nego import attachments
    from quantdocs.notice import read_notice

    notice = "\n".join([
        "입찰참가 신청 | 1 | 제안공모 참가신청서 | 【서식 1】",
        "7 | 제안응모자의 참가자격을 입증할 수 있는 서류 ① 사업자등록증 사본, 법인등기부등본, 입찰참가자격 등록증 | 【서식 7, 26, 27】",
        "8 | 위임장 및 재직증명서(대리인이 등록할 경우에 한함) | 【서식 8~9】",
        "10 | 사업수행능력평가(정량적 지표) 자료 2부 | 【서식 11~19】",
        "13 | 가격제안서 1부(산출내역서 첨부, 밀봉) | 【서식 21~23】",
    ])
    rfp = "\n".join([
        "〈서식 7〉", "사 용 인 감 계", "〈서식 8〉", "위    임    장", "〈서식 11〉", "", "사업수행능력평가자료 (표지)",
        "접수 번호 |  주) 1. 사업수행능력평가자료(객관적 지표)의 규격은 A4용지", "〈서식 12〉", "제안회사 일반현황 및 연혁",
        "〈서식 16〉", "최근 5년간 사업수행실적", "〈서식 21〉", "가 격 입 찰 제 안 서", "〔서  식 26〕", "",
    ])
    texts = {"공고문.hwpx": notice, "제안요청서.hwpx": rfp}
    paths = []
    for name in texts:
        (tmp_path / name).write_bytes(b"x")
        paths.append(tmp_path / name)
    monkeypatch.setattr(attachments, "extract_text", lambda data, ext, _n=iter(texts.values()): next(_n))
    got = {f.no: f.name for f in read_notice(paths).forms}
    assert got[1] == "제안공모 참가신청서"
    assert got[7] == "사용인감계" and got[8] == "위임장"
    assert got[11] == "사업수행능력평가자료 (표지)" and got[12] == "제안회사 일반현황 및 연혁"
    assert got[16] == "최근 5년간 사업수행실적" and got[21] == "가격입찰제안서"
    assert got[26] == "제안응모자의 참가자격을 입증할 수 있는 서류 (7·26·27)"  # 본문 제목이 없으면 목록 이름
    assert got[13] == "사업수행능력평가(정량적 지표) 자료 (11~19)"


def test_submit_items_skip_sentences_and_section_titles():
    """'제출' 제목 아래라도 실격 사유('…경우')·작성 요령('…한다.')·절 제목('저작권', '공통 사항')은 서류가 아님(11/04 제보)."""
    from quantdocs.catalog import load_catalog
    from quantdocs.notice import read_submit_items

    text = ("다. 제출도서\n - 사업의 목적 및 제안공모 설계지침을 현저히 충족시키지 못하는 경우\n - 저작권\n - 공통 사항\n"
            " - 주요 시설물 명칭 및 규모, 면적은 해당 시설물에 직접 기재한다.\n - 조감도 및 패널\n - USB 1점\n\n"
            "라. 제출서류 무효 및 실격\n ① 기타 평가위원회에서 실격으로 판정한 경우\n")
    assert [i.text for i in read_submit_items(text, "x", load_catalog())] == ["조감도 및 패널", "USB 1점"]


def test_as_date_accepts_dotted_text():
    """11/05 받은 기술인력표 입사일이 '2011.09.01' 글자 — 재직 개월 계산에서 '입사일 없음'으로 빠지지 않게."""
    from quantdocs.notice import _as_date
    assert _as_date("2011.09.01") == dt.date(2011, 9, 1) and _as_date("2020/2/17") == dt.date(2020, 2, 17)
    assert _as_date("2023년 6월 5일") == dt.date(2023, 6, 5) and _as_date("2026-04-27") == dt.date(2026, 4, 27)
    assert _as_date("모름") is None and _as_date(None) is None


def test_pdf_form_heads_name_before_number():
    """11/05 창업진흥원 제안요청서(PDF) — '서식 일반현황서식 일반현황[ 2]'·'서식 서식 [ 1]  제안서 표지제안서 표지'(굵은 글씨가 두 번 찍힘),
    하위 서식 '[ 4-1]~[ 4-3]'은 4번 하나로."""
    from quantdocs.notice import read_forms

    text = "\n".join(["서식 서식 [ 1]  제안서 표지제안서 표지", "제  안  서", "서식 일반현황서식 일반현황[ 2] ",
                      "서식 투입인력 현황서식 투입인력 현황[ 4-1] ", "서식 투입인력 이력사항서식 투입인력 이력사항[ 4-2] ",
                      "서식 청렴계약이행각서서식 청렴계약이행각서[ 5] "])
    got = [(f.no, f.name) for f in read_forms(text, "제안요청서.pdf", [])]
    assert got == [(1, "제안서 표지"), (2, "일반현황"), (4, "투입인력 현황 · 투입인력 이력사항 (4-1~4-2)"), (5, "청렴계약이행각서")]


def test_broken_pdf_pages_read_by_ocr(tmp_path, monkeypatch):
    """11/05 사용자 요청: 글꼴 문제로 본문 글이 빠진 PDF(제목만 남고 '「 」'·'◦'만 남은 쪽)는 그 쪽들만 OCR로 다시 읽음 —
    제목 줄은 살리고('2 입찰 참가자격' 겹친 글은 한 번, 번호 뒤 점), OCR의 'ㅇ'은 '○', ○ 아래 '가.·나.'는 하위 목으로.
    (11/05 사용자 결정으로 공고문 OCR은 기본 꺼짐 — 켜면 이렇게 동작)"""
    from quantdocs import notice as _n
    monkeypatch.setattr(_n, "NOTICE_OCR", True)
    from reportlab.pdfgen import canvas

    from quantdocs import importer, notice

    p = tmp_path / "rfp.pdf"
    c = canvas.Canvas(str(p))
    for page in range(4):
        y = 800
        for ln in ["- %d -" % page, "( )", "「 」", "◦", ", ,", "( 4817)", "( 4990)"]:
            c.drawString(50, y, ln)
            y -= 20
        c.showPage()
    c.save()
    ocr = "ㅇ 입찰참가등록을 완료하고, 다음 각 목의 업종 중 어느 하나 이상을 등록한 자\n가. 건축사사무소 (업종코드 4817)\n나. 실내건축공사업 (업종코드 4990)"
    calls = []
    monkeypatch.setattr(importer, "ocr_pages", lambda data, pages: calls.append(pages) or {i: ocr for i in pages})
    notice._TEXT_CACHE.clear()
    notice.OCR_USED.clear()
    text = notice.doc_text(p)
    assert calls == [[0, 1, 2, 3]] and "○ 입찰참가등록을 완료하고" in text and "  - 가. 건축사사무소" in text
    assert any("OCR" in n for n in notice.OCR_USED)
    notice.doc_text(p)
    assert len(calls) == 1  # 같은 파일은 한 번만
    assert notice._undouble("2 입찰 참가자격입찰 참가자격") == "2 입찰 참가자격"
