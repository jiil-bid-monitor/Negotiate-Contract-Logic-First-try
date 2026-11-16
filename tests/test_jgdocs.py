"""적격심사 서류: 공고·개찰 결과 → 기본사항 칸 값."""

import datetime as dt

from jgdocs import fill


def test_values_works_fill_from_notice_and_result():
    entry = {"noticeNo": "R26BK01815406", "noticeOrd": "000", "bid": 104106058,
             "result": {"plannedPrice": 115000000, "own": {"amount": 104106000}, "aValue": 8522747}}
    info = {"work_type": "공사", "title": "낙석방지책 정비공사", "est_price": 105328182, "notice_date": "2026-11-09 09:08:50",
            "opening_at": "2026-11-13 11:00:00", "licenses": ["금속창호ㆍ지붕건축물조립공사업"], "demand_institution": "도로관리사업소"}
    v = fill.values("공사", entry, info)
    assert v["입찰공고번호"] == "R26BK01815406-000"
    assert v["입찰금액"] == 104106000  # 개찰 결과의 실제 투찰금액이 기록 금액보다 먼저
    assert v["예정가격"] == 115000000 and v["A값"] == 8522747
    assert v["입찰공고일"] == dt.datetime(2026, 11, 9, 9, 8, 50) and v["입찰일시"] == "2026-11-13 11:00"
    assert v["업종"] == "금속창호ㆍ지붕건축물조립공사업" and v["업종평가비율(%)"] == 100


def test_values_goods_and_multi_license_leaves_ratio_blank():
    info = {"title": "전시물 구매", "product_name": "실물모형및전시물", "product_no": "6010989901", "est_price": 7e8}
    v = fill.values("물품", {"noticeNo": "R1", "bid": 1}, info)
    assert v["입찰 대상 물품명"] == "실물모형및전시물" and v["정부물품 분류번호"] == "6010989901" and "예정가격" not in v
    w = fill.values("공사", {"noticeNo": "R1"}, {"licenses": ["실내건축공사업", "정보통신공사업"]})
    assert "업종" not in w and "업종평가비율(%)" not in w  # 복합 업종은 공고의 비율을 담당자가


def test_choose_group_by_estimated_price():
    assert fill.choose("물품", 1e9) == ("물품", "10억이상")
    assert fill.choose("공사", 9.9e8) == ("공사", "10억미만")
    assert fill.choose("공사", None) == ("공사", "10억미만")
    assert fill.choose("용역", 1e8) is None
