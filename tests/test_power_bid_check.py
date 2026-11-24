"""전력그룹사 공고 점검 도구(`scripts/power_bid_check.py`) — API를 부르지 않는 부분(기간 나누기·응답 읽기·공고 바꾸기·수집 규칙 판정·출력).
응답 줄은 사양 모양으로 만든 가짜(실제 공고·담당자 정보 아님)."""

from __future__ import annotations

import csv
import importlib.util
import json
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("power_bid_check", ROOT / "scripts" / "power_bid_check.py")
pbc = importlib.util.module_from_spec(spec)
sys.modules["power_bid_check"] = pbc
spec.loader.exec_module(pbc)

from nego.config import load_config  # noqa: E402

ROWS = [
    {"no": "E01", "name": "OO발전본부 홍보관 전시물 개선 공사", "companyId": "COM08", "placeName": "가짜발전본부",
     "itemType": "Construction", "purchaseType": "ConstructionService", "competitionType": "Limited",
     "bidType": "QualifiedEval", "progressState": "AttendProgress", "noticeDate": "20261120100000",
     "endDatetime": "20261201110000", "presumedAmount": "180,000,000", "bidAttendRestrict": "실내건축공사업   등록 업체"},
    {"no": "E02", "name": "변전소 통신설비 개량공사", "companyId": "COM01", "itemType": "Construction",
     "competitionType": "Open", "bidType": "LowestPrice", "progressState": "PreAttendProgress",
     "noticeDate": "2026-11-21 09:00:00", "endDatetime": "2026-12-02 10:00:00", "presumedAmount": "95000000"},
    {"no": "E03", "name": "통신장비 유지보수 용역", "companyId": "COM01", "itemType": "Service",
     "competitionType": "Open", "bidType": "Nego", "progressState": "AttendProgress", "noticeDate": "20261122"},
    {"no": "E04", "name": "보일러 튜브 구매", "companyId": "COM05", "itemType": "Product", "purchaseType": "Product",
     "competitionType": "Private", "progressState": "Close", "presumedPrice": "12000000"},
    {"no": "E04", "name": "보일러 튜브 구매", "companyId": "COM05", "itemType": "Product"},  # 같은 공고 두 번
]


def test_windows_split_at_90_days():
    w = pbc.windows(date(2026, 1, 1), date(2026, 6, 30))
    assert w[0] == (date(2026, 1, 1), date(2026, 3, 31))
    assert all((e - b).days + 1 <= 90 for b, e in w)
    assert w[-1][1] == date(2026, 6, 30) and len(w) == 3


def test_rows_need_data_list():
    assert len(pbc.rows_of({"data": ROWS})) == 5
    with pytest.raises(pbc.FetchError):
        pbc.rows_of({"error": "invalid key"})


def test_to_notice_maps_fields():
    n = pbc.to_notice(ROWS[0])
    assert (n.work_type, n.notice_institution, n.demand_institution) == ("공사", "한국동서발전", "가짜발전본부")
    assert (n.contract_method, n.award_method) == ("제한경쟁", "적격심사")
    assert n.estimated_price == 180_000_000 and n.bid_deadline == "2026-12-01 11:00"
    assert pbc.to_notice(ROWS[3]).estimated_price == 12_000_000  # 자재구매는 추정가격
    assert pbc.to_notice(ROWS[1]).posted_at == "2026-11-21 09:00"


def test_judge_applies_current_rules():
    judged = {j["공고번호"]: j for j in pbc.judge(ROWS, load_config().screen)}
    assert len(judged) == 4  # 같은 공고번호는 한 번
    assert judged["E01"]["판정"] != "빠짐"  # '전시물'
    assert judged["E02"]["판정"] == "후보" and "통신" in judged["E02"]["이유"]  # 통신 = 공사만
    assert judged["E03"]["판정"] == "빠짐"  # 용역 통신 + 유지보수
    assert judged["E04"]["판정"] == "빠짐" and judged["E04"]["업무"] == "물품"
    assert judged["E01"]["참가자격(앞부분)"] == "실내건축공사업 등록 업체"


def test_main_from_saved_input(tmp_path, capsys):
    src = tmp_path / "resp.json"
    src.write_text(json.dumps({"data": ROWS}, ensure_ascii=False), encoding="utf-8")
    assert pbc.main(["--input", str(src), "--out", str(tmp_path)]) == 0
    text = capsys.readouterr().out
    assert "받은 공고 4건" in text and "걸린 공고" in text
    out = next(tmp_path.glob("전력그룹사_공고점검_*.csv"))
    with out.open(encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 4 and "담당" not in "".join(rows[0])  # 담당자 칸은 안 남김


def test_no_key_message(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv(pbc.KEY_NAME, raising=False)
    monkeypatch.setattr(pbc, "ROOT", tmp_path)
    assert pbc.main(["--out", str(tmp_path)]) == 2
    assert pbc.KEY_NAME in capsys.readouterr().err
