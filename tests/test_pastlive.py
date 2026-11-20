"""과거사업 목록의 지금 사업(webapp/pastlive.py) — 계약 올라온 낙찰 공고만, 준공예정일로 진행 중/끝남, 같은 문서면 다시 안 읽음."""
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from webapp import pastlive


class _PA:
    def __init__(self, items):
        self.items_ = items

    def _read(self):
        return self.items_


class _TP:
    calls = 0

    @classmethod
    def build_profile(cls, texts, title=""):
        cls.calls += 1
        return SimpleNamespace(tags={"시설·주제": ["교통·안전"], "과업유형": ["제작·설치"]}, counts={"시설·주제/교통·안전": 3},
                               evidence={"시설·주제/교통·안전": "교통안전체험관"})


class PastLiveTest(unittest.TestCase):
    def test_build_rows(self):
        with tempfile.TemporaryDirectory() as d:
            items = {
                "A-000": {"no": "A", "status": "낙찰", "title": "광양 체험관", "work": "물품",
                          "contract": {"date": "2025-11-26", "due": "2026-03-16"}},
                "B-000": {"no": "B", "status": "낙찰", "title": "진행 사업", "work": "용역", "contract": {"date": "2026-10-01"}},
                "C-000": {"no": "C", "status": "낙찰", "title": "계약 전", "contract": {}},
                "D-000": {"no": "D", "status": "다른 업체 낙찰", "title": "남의 것", "contract": {"date": "2026-10-01"}},
            }
            rows = pastlive.build(_PA(items), Path(d), _TP, today=datetime(2026, 11, 20))
            by = {r["key"]: r for r in rows}
            self.assertEqual(set(by), {"A-000", "B-000"})  # 계약이 올라온 낙찰 공고만
            self.assertEqual(by["A-000"]["status"], "끝남")  # 준공예정일이 지남
            self.assertEqual(by["B-000"]["status"], "진행 중")  # 준공예정일을 모르면 진행 중
            self.assertEqual(by["A-000"]["year"], 2025)
            self.assertEqual(by["A-000"]["tags"]["과업유형"], ["제작·설치"])
            n = _TP.calls
            pastlive.build(_PA(items), Path(d), _TP, today=datetime(2026, 11, 20))
            self.assertEqual(_TP.calls, n)  # 문서가 같으면 다시 안 읽음
            self.assertTrue(json.loads((Path(d) / pastlive.LIVE_FILE).read_text(encoding="utf-8")))


if __name__ == "__main__":
    unittest.main()
