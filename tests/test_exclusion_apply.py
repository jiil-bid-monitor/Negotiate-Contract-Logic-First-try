"""예외 낱말 판단 파일 자동 반영(nego/exclusion_apply.py)·지켜보기(webapp/exclusionsync.py) 테스트 — git·텔레그램 없이 도는 부분."""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path

from nego import exclusion_apply as ea
from webapp import exclusionsync as es


def decision(approved=(), rejected=()):
    def items(rows):
        return [{"n": i, "word": w, "keyword": k, "tag": "시설·주제/x"} for i, (w, k) in enumerate(rows, 1)]
    return {"approved": items(approved), "rejected": items(rejected)}


class TestMerge(unittest.TestCase):
    def test_adds_new_and_skips_known(self):
        fb = {"approved": [{"word": "백도어", "keyword": "도어"}], "rejected": []}
        words: list[str] = []
        st = ea.merge(fb, words, decision([("지상권", "상권"), ("백도어", "도어"), ("FLANGE", "LAN"), ("도시공원", "시공")],
                                          [("교통량", "교통")]), "user-x", known_words={"도시공원"})
        self.assertEqual((st["approved"], st["rejected"], st["duplicate"]), (3, 1, 1))
        self.assertEqual(words, ["지상권", "FLANGE"])  # 도시공원은 이미 코드 목록에 있음
        self.assertEqual(st["latin"], ["FLANGE"])
        self.assertEqual(fb["rejected"][0], {"word": "교통량", "keyword": "교통", "tag": "시설·주제/x", "by": "user-x"})

    def test_same_file_twice_changes_nothing(self):
        fb = {"approved": [], "rejected": []}
        words: list[str] = []
        d = decision([("지상권", "상권")], [("교통량", "교통")])
        ea.merge(fb, words, d, "u", set())
        st = ea.merge(fb, words, d, "u", set())
        self.assertEqual((st["approved"], st["rejected"], st["duplicate"]), (0, 0, 2))
        self.assertEqual(words, ["지상권"])

    def test_messages(self):
        path = Path("예외낱말_판단_20261103.json")
        ok = ea.message(path, {"approved": 2, "rejected": 1, "duplicate": 0, "new_words": ["지상권", "FLANGE"],
                               "latin": ["FLANGE"], "jiil_pr": "https://x/8", "bundle_pr": "https://y/9"}, None)
        self.assertIn("승인 2 · 기각 1", ok)
        self.assertIn("영문 낱말 1개(FLANGE)", ok)
        fail = ea.message(path, None, ea.ApplyError("jiil 테스트", "AssertionError <x>"))
        self.assertIn("실패</b> (jiil 테스트) — 병합하지 않았습니다", fail)
        self.assertIn("&lt;x&gt;", fail)

    def test_bad_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "a.json"
            p.write_text('{"approved": 1}', encoding="utf-8")
            with self.assertRaises(ea.ApplyError):
                ea.read_decision(p)


class TestPending(unittest.TestCase):
    def test_new_settled_files_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            old, done, fresh = d / "예외낱말_판단_1.json", d / "예외낱말_판단_2.json", d / "예외낱말_판단_3.json"
            for i, p in enumerate((old, done, fresh)):
                p.write_text(str(i), encoding="utf-8")
            (d / "다른파일.json").write_text("x", encoding="utf-8")
            now = time.time()
            os.utime(old, (now - 100, now - 100))
            os.utime(done, (now - 50, now - 50))
            got = es.pending(d, {ea.file_hash(done): {}}, now=now)
        self.assertEqual(got, [old])  # 처리한 것·내려받는 중(방금 생긴 것)·다른 이름은 뺌


if __name__ == "__main__":
    unittest.main()
