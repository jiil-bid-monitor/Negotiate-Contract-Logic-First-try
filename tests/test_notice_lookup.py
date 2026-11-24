"""공고번호 조회 도구(`scripts/notice_lookup.py`) — 가짜 나라장터로(공고 행·낙찰 행은 형식만 따른 가짜)."""

from __future__ import annotations

import csv
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("notice_lookup", ROOT / "scripts" / "notice_lookup.py")
nl = importlib.util.module_from_spec(spec)
sys.modules["notice_lookup"] = nl
spec.loader.exec_module(nl)

from nego import fields as F  # noqa: E402

NOTICES = {
    ("공사", "P2026310"): [{"bidNtceNo": "P2026310", "bidNtceOrd": "01", "bidNtceNm": "부안 수소도시 홍보관 인테리어 공사",
                           "presmptPrce": "18000000", "asignBdgtAmt": "", "cntrctCnclsMthdNm": "수의계약",
                           "sucsfbidMthdNm": "최저가", "bidNtceDt": "2026-06-15 16:30:00", "bidClseDt": "2026-06-16 18:00:00",
                           "dminsttNm": "한전KDN(주)"}],
    ("용역", "20260415"): [{"bidNtceNo": "20260415", "bidNtceOrd": "00", "bidNtceNm": "옛 차수"},
                          {"bidNtceNo": "20260415", "bidNtceOrd": "01", "bidNtceNm": "2026년 한전KDN 기업홍보영상 제작 용역",
                           "asignBdgtAmt": "266227500", "cntrctCnclsMthdNm": "제한경쟁"}],
}
AWARDS = {("용역", "20260415"): [{"bidNtceNo": "20260415", "bidNtceOrd": "01", "bidwinnrNm": "가짜영상(주)",
                                  "sucsfbidAmt": "240000000", "sucsfbidRate": "90.15", "prtcptCnum": "7"}]}


class FakeClient:
    def __init__(self):
        self.calls = []

    def fetch_all_pages(self, base, op, params, label):
        self.calls.append(op)
        no = params["bidNtceNo"]
        for w, o in F.BID_NOTICE_OPERATIONS.items():
            if o == op:
                return NOTICES.get((w, no), [])
        for key, o in F.SCSBID_OPERATIONS.items():
            if o == op:
                return AWARDS.get((key.split("/")[1], no), [])
        return []


def test_lookup_amounts_award_and_missing(tmp_path, capsys):
    src = tmp_path / "t.csv"
    src.write_text("공고번호,업무,묶음,공고명\nP2026310-01,공사,홍보관·공간,부안\n20260415-01,,영상·콘텐츠·홍보물,영상\n"
                   "R26BK0000000-000,용역,통신공사,없는 공고\n", encoding="utf-8-sig")
    client = FakeClient()
    assert nl.main([str(src), "--out", str(tmp_path)], client=client) == 0
    text = capsys.readouterr().out
    assert "찾음 2건 · 못 찾음 1건" in text and "영상·콘텐츠·홍보물: 1건" in text
    with next(tmp_path.glob("공고조회_*.csv")).open(encoding="utf-8-sig") as f:
        rows = {r["공고번호"]: r for r in csv.DictReader(f)}
    a = rows["P2026310-01"]
    assert a["사업금액(원)"] == "19800000" and a["계약방법"] == "수의계약" and a["게시"] == "2026-06-15 16:30"
    b = rows["20260415-01"]  # 업무를 몰라 공사 → 용역 순으로 찾음, 마지막 차수
    assert b["업무"] == "용역" and b["공고명"].startswith("2026년") and b["사업금액(원)"] == "266227500"
    assert b["낙찰 업체"] == "가짜영상(주)" and b["낙찰 금액(원)"] == "240000000"
    assert rows["R26BK0000000-000"]["조회 오류"]  # 못 찾은 공고도 줄은 남김


def test_numbers_as_args(tmp_path):
    client = FakeClient()
    assert nl.main(["P2026310-01", "--no-award", "--out", str(tmp_path)], client=client) == 0
    assert not any("Scsbid" in c for c in client.calls)


def test_targets_file_has_17():
    assert len(nl.read_targets([str(ROOT / "docs" / "kdn_targets_2026-05_11.csv")])) == 17
