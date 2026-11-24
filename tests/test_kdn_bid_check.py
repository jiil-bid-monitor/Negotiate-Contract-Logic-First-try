"""기관 공고 점검 도구(`scripts/kdn_bid_check.py`) — 나라장터를 부르지 않는 부분(기관 고르기·판정 줄·요약·출력).
공고는 형식만 따른 가짜(tests.fixtures)."""

from __future__ import annotations

import csv
import importlib.util
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("kdn_bid_check", ROOT / "scripts" / "kdn_bid_check.py")
kbc = importlib.util.module_from_spec(spec)
sys.modules["kdn_bid_check"] = kbc
spec.loader.exec_module(kbc)

from nego.config import load_config  # noqa: E402
from nego.models import notice_from_raw  # noqa: E402
from nego.pipeline import RunStats, build_candidates  # noqa: E402
from tests import fixtures  # noqa: E402

NOW = datetime(2026, 9, 14, 9, 0)
BID = fixtures.QUALIFY


def _notices():
    raw = [
        ("공사", fixtures.notice("R26BK0001", title="본사 홍보관 전시물 개선공사", award=BID, ntceInsttNm="한전KDN(주)")),
        ("공사", fixtures.notice("R26BK0002", title="사옥 정보통신 설비 개량공사", award=BID, ntceInsttNm="조달청",
                               dminsttNm="한전 KDN 주식회사")),
        ("용역", fixtures.notice("R26BK0003", title="통합모니터링시스템 SW 유지관리", ntceInsttNm="한전KDN(주)")),
        ("용역", fixtures.notice("R26BK0004", title="○○박물관 전시물 제작 설치", ntceInsttNm="다른기관")),
    ]
    return [notice_from_raw(r, w) for w, r in raw]


def test_org_words_and_hit():
    words = kbc.org_words(["한전KDN"])
    assert "한전케이디엔" in words and "kepcokdn" in words
    n = _notices()
    assert kbc.org_hit(n[0], words) == "한전KDN(주)"
    assert kbc.org_hit(n[1], words) == "한전 KDN 주식회사"  # 수요기관·띄어쓰기
    assert kbc.org_hit(n[3], words) is None
    assert kbc.org_words(["한국전력공사"]) == ["한국전력공사"]


def test_pick_keeps_only_org_and_sorts(tmp_path):
    stats = RunStats()
    cands = build_candidates(_notices(), load_config(), {}, {}, NOW, stats)
    rows = kbc.pick(cands, stats.rejected, kbc.org_words(["한전KDN"]), NOW)
    by_no = {r["공고번호"]: r for r in rows}
    assert set(by_no) == {"R26BK0001-000", "R26BK0002-000", "R26BK0003-000"}  # 다른 기관 공고는 빠짐
    assert by_no["R26BK0001-000"]["판정"] == "후보" and "전시물" in by_no["R26BK0001-000"]["이유"]
    assert by_no["R26BK0002-000"]["판정"] == "후보"  # '통신' — 공사
    assert by_no["R26BK0003-000"]["판정"] != "후보"  # 유지관리
    assert rows[-1]["공고번호"] == "R26BK0003-000"  # 후보가 앞
    text = kbc.summary(rows, ["한전KDN"], NOW.date(), NOW.date())
    assert "그 기관 공고 3건" in text and "본사 홍보관 전시물 개선공사" in text
    out = tmp_path / "a.csv"
    kbc.write_csv(rows, out)
    with out.open(encoding="utf-8-sig") as f:
        assert len(list(csv.DictReader(f))) == 3


def test_no_key(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("NARA_SERVICE_KEY", raising=False)
    monkeypatch.setattr(kbc, "ROOT", tmp_path)
    assert kbc.main(["--out", str(tmp_path)]) == 2
    assert "NARA_SERVICE_KEY" in capsys.readouterr().err
