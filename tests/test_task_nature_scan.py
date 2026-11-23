"""과업 성격 정리 도구(`scripts/task_nature_scan.py`) — 나라장터를 부르지 않는 부분(판단 기록 고르기·낱말 수·절 발췌·출력)."""

from __future__ import annotations

import csv
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("task_nature_scan", ROOT / "scripts" / "task_nature_scan.py")
tns = importlib.util.module_from_spec(spec)
sys.modules["task_nature_scan"] = tns
spec.loader.exec_module(tns)

HEAD = ["판단일시", "판단", "주된 사유", "사유", "판단자", "담당", "공고번호", "공고명", "구분", "업무", "수요기관", "수집 경로"]


def _csv(tmp_path: Path, rows: list[list[str]]) -> Path:
    p = tmp_path / "log.csv"
    with open(p, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(HEAD)
        w.writerows(rows)
    return p


def test_load_rows_picks_reason_and_join(tmp_path: Path):
    p = _csv(tmp_path, [
        ["", "불참", "과업 성격 다름", "", "", "", "R1", "네트워크스위치 구매", "본공고", "물품", "가", "보유 면허"],
        ["", "불참", "금액 적음", "", "", "", "R2", "인테리어 용역", "본공고", "용역", "나", ""],
        ["", "참가", "본업과 맞음", "", "", "", "R3", "체육센터 건립공사(통신)", "본공고", "공사", "다", "관심 키워드"],
        ["", "불참", "과업 성격 다름, 금액 적음", "", "", "", "R1", "중복", "본공고", "물품", "가", ""],
    ])
    assert [r["공고번호"] for r in tns.load_rows(p, "과업 성격 다름", False)] == ["R1"]
    assert [r["공고번호"] for r in tns.load_rows(p, "과업 성격 다름", True)] == ["R1", "R3"]


def test_nature_counts_and_sections():
    text = ("1. 사업 개요\n사업명 : 대강당 LED 스크린 구매설치\n"
            "2. 과업 내용\n가. LED 스크린 물품 구매 및 납품 — 제조사·모델명은 규격서 참조, 설치 후 1년 무상 유지보수\n"
            "나. 기존 장비 철거 및 설치, 시운전과 운영 교육을 포함하여 수행한다.\n")
    counts = tns.nature_counts(text)
    assert counts["납품·구매"] >= 4 and counts["제작·설치·시공"] >= 2 and counts["유지보수·운영"] >= 2
    assert tns.main_nature(counts).startswith("납품·구매")
    got = tns.sections(text)
    assert got and got[0].startswith("1. 사업 개요") and any("과업 내용" in g for g in got)
    assert tns.main_nature({k: 0 for k in counts}) == ""


def test_write_outputs(tmp_path: Path):
    row = {"판단": "불참", "주된 사유": "과업 성격 다름", "공고번호": "R1", "공고명": "스위치 구매", "업무": "물품", "수요기관": "가",
           "수집 경로": "보유 면허", "읽은 문서": "규격서.hwp", "과업 성격": "납품·구매", "낱말 수": "납품·구매 5", "과업 태그": "",
           "닮은 과거 실적": "", "과업 내용 발췌": "2. 과업 내용 — 스위치 납품", "메모": ""}
    c, m = tns.write([row], tmp_path / "out" / "task_nature_x")
    assert c.exists() and "납품·구매: 1건 (참가 0)" in m.read_text(encoding="utf-8") and "> 2. 과업 내용" in m.read_text(encoding="utf-8")
