"""기관별 따로 수집(`nego/org_watch.py`, `scripts/org_watch.py`) — 나라장터를 부르지 않는 부분.
공고명은 한전KDN 6개월 공고(2026-05~11)와 보고서 사업 이름을 본떠 만든 것, 공고 칸은 형식만 따른 가짜(tests.fixtures)."""

from __future__ import annotations

import csv
import importlib.util
import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("org_watch_script", ROOT / "scripts" / "org_watch.py")
ow = importlib.util.module_from_spec(spec)
sys.modules["org_watch_script"] = ow
spec.loader.exec_module(ow)

from nego.models import notice_from_raw  # noqa: E402
from nego.org_watch import load_rules  # noqa: E402
from tests import fixtures  # noqa: E402

NOW = datetime(2026, 9, 14, 9, 0)

# (업무, 공고명, 대상이어야 하나)
TITLES = [
    ("용역", "2025년 비상설전시회 한전KDN 전시부스 설치·운영", True),
    ("용역", "2026년 국제전기전력전시회 한전KDN 전시부스 설치·운영", True),
    ("용역", "2025년도 한전KDN 회사 홍보물 제작 용역", True),
    ("용역", "2026년 한전KDN 기업홍보영상 제작 용역 발주 의뢰", True),
    ("용역", "[수의시담] 2026년 블록체인 확산사업 홍보영상 제작", True),
    ("용역", "2026년 기업문화 인식개선 캠페인 콘텐츠 제작", True),
    ("공사", "부안 수소도시 홍보관 인테리어 공사", True),
    ("물품", "부안 수소도시 홍보관 설비 구매", True),
    ("공사", "서울본부 신사옥 3층 회의실 등 업무공간 재배치공사", True),
    ("공사", "진주지사 신규사옥 칸막이 공사", True),
    ("공사", "미터링서비스부 신규 사업수행 사무실 통신공사", True),
    ("용역", "[경쟁입찰] 27년도 산업부 사이버안전센터 보안관제 및 사이버안전시스템 통합 유지보수 용역", False),
    ("용역", "[재공고]멀티미디어센터 운영S/W 유지관리", False),
    ("물품", "[경쟁입찰] 26년 RedHat 리눅스 서브스크립션 구매", False),
    ("공사", "서울본부 공용공간(3층,10층,11층,12층) 재배치관련 전기공사", False),
    ("용역", "[수의시담]나주 본사 비품 재배치 및 서울본부 비품 이동", False),
    ("공사", "영상회의 설비 서울 가락동 철거 및 고덕동 이전 설치 공사", False),
    ("용역", "통신공사 감리 용역", False),  # 통신공사 묶음은 공사만
]


def _notices():
    out = []
    for i, (work, title, _) in enumerate(TITLES):
        raw = fixtures.notice(f"P2026{i:03d}", ord_="01", title=title, award=fixtures.QUALIFY, ntceInsttNm="한전KDN(주)",
                              dminsttNm="한전KDN(주)")
        out.append(notice_from_raw(raw, work))
    other = fixtures.notice("R26BK0999", title="○○박물관 전시물 제작 설치", ntceInsttNm="다른기관", dminsttNm="다른수요기관")
    cancel = fixtures.notice("R26BK0998", title="홍보관 인테리어 공사", kind="취소공고", ntceInsttNm="한전KDN(주)")
    return out + [notice_from_raw(other, "용역"), notice_from_raw(cancel, "공사")]


def test_rule_targets_report_projects_not_it_work():
    rule = load_rules()["한전KDN"]
    rows = {r["공고명"]: r for r in ow.rows_for(rule, _notices(), NOW, {})}
    for _, title, want in TITLES:
        assert (rows[title]["판정"] == "대상") == want, title
    assert "○○박물관 전시물 제작 설치" not in rows  # 다른 기관
    assert all("취소" not in r["구분"] for r in rows.values()) and len(rows) == len(TITLES)  # 취소공고 뺌
    assert rows["부안 수소도시 홍보관 인테리어 공사"]["묶음"] == "홍보관·공간"


def test_new_then_seen(tmp_path, capsys):
    state = tmp_path / "seen.json"
    assert ow.main(["--out", str(tmp_path), "--state", str(state)], notices=_notices()) == 0
    first = capsys.readouterr().out
    target = sum(want for *_, want in TITLES)
    assert f"대상 {target}건(새 공고 {target})" in first and "새로 나온 공고:" in first
    assert len(json.loads(state.read_text(encoding="utf-8"))["한전KDN"]) == target
    assert ow.main(["--out", str(tmp_path), "--state", str(state)], notices=_notices()) == 0
    assert f"(새 공고 0)" in capsys.readouterr().out
    out = next(tmp_path.glob("기관수집_한전KDN_*.csv"))
    with out.open(encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    assert {r["판정"] for r in rows} == {"대상", "제외 낱말"}  # --all 없으면 묶음 없는 공고는 CSV에서 뺌


def test_unknown_org(tmp_path):
    assert ow.main(["--org", "없는기관", "--state", str(tmp_path / "s.json")], notices=[]) == 2


@pytest.mark.parametrize("name", ["한전KDN(주)", "한전 KDN 주식회사", "한전케이디엔", "KEPCO KDN"])
def test_org_names(name):
    raw = fixtures.notice("R1", ntceInsttNm="조달청", dminsttNm=name)
    assert load_rules()["한전KDN"].owns(notice_from_raw(raw, "용역")) == name
