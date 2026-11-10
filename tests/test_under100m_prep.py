"""회사준비서류 — 표본 규칙: 항목 찾기, 재발급 판단, 쓸 수 있는 서류만 복사."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from under100m.docs import LATEST, LIST_JSON
from under100m.prep import AFTER_NOTICE, CHECK, RENEW, USE, find_items, hints, issue_after_all, judge, listed_items, prepare

REF = "2026-11-20"


def rec(kind, issued="2026-11-01", end="", **kw):
    return {"종류": kind, "파일": f"{kind}_{issued}.pdf", "발급일": issued, "유효기간": end, **kw}


class FindTest(unittest.TestCase):
    def test_requirement_only(self):
        items = find_items(["사업자등록증 사본 1부 제출\n계약 체결 후 공정표 제출\n인감증명서는 제출하지 않음"])
        self.assertIn("사업자등록증", items)
        self.assertNotIn("공정표·수행계획", items)
        self.assertNotIn("인감증명서", items)

    def test_hints_and_notice_wide_issue_rule(self):
        self.assertEqual(hints("정량평가서 2부 원본, USB"), ["2부", "원본", "USB"])
        self.assertTrue(issue_after_all(["※ 상기 입찰관련서류는 입찰공고 이후에 발급한 자료를 제출"]))
        self.assertFalse(issue_after_all(["공고일 현재 휴업 중이 아닐 것"]))


class JudgeTest(unittest.TestCase):
    def test_expiry_and_fresh(self):
        self.assertEqual(judge(rec("신용평가등급확인서", end="2026-11-01"), "", REF, None, {})[0], RENEW)
        self.assertEqual(judge(rec("신용평가등급확인서", end="2027-01-01"), "", REF, None, {})[0], USE)
        self.assertEqual(judge(rec("신용평가등급확인서"), "", REF, None, {})[0], CHECK)
        self.assertEqual(judge(rec("법인등기사항전부증명서", "2026-03-12"), "", REF, None, {})[0], RENEW)
        self.assertEqual(judge(rec("법인등기사항전부증명서", "2026-10-12"), "", REF, None, {})[0], USE)
        self.assertEqual(judge(rec("재무제표", "2024-03-31"), "", REF, None, {})[0], RENEW)

    def test_ceo_rule_comes_from_file_not_code(self):
        rule = {"현재대표자": "갑", "대표변경일": "2024-09-08"}
        self.assertEqual(judge(rec("사업자등록증", "2025-01-01", 대표자="을"), "", REF, None, rule)[0], RENEW)
        self.assertEqual(judge(rec("사업자등록증", "2019-01-01"), "", REF, None, rule)[0], RENEW)
        self.assertEqual(judge(rec("사업자등록증", "2025-01-01"), "", REF, None, rule)[0], USE)
        self.assertEqual(judge(rec("사업자등록증", "2019-01-01"), "", REF, None, {})[0], USE)

    def test_after_notice_rule(self):
        line = f"[ ] 사업자등록증 ({AFTER_NOTICE})"
        self.assertEqual(judge(rec("사업자등록증", "2025-01-01"), line, REF, "2026-11-01", {})[0], RENEW)
        self.assertEqual(judge(rec("사업자등록증", "2026-11-02"), line, REF, "2026-11-01", {})[0], USE)
        self.assertEqual(judge(rec("사업자등록증", "2026-11-02"), line, REF, None, {})[0], CHECK)


class PrepareTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.docs = self.dir / "docs"
        (self.docs / LATEST).mkdir(parents=True)
        self.records = [rec("사업자등록증", "2025-08-18"), rec("법인등기사항전부증명서", "2026-03-12")]
        for record in self.records:
            (self.docs / LATEST / record["파일"]).write_bytes(b"%PDF")
        (self.docs / LIST_JSON).write_text(json.dumps(self.records, ensure_ascii=False), encoding="utf-8")
        self.text = ["사업자등록증 1부 제출", "법인 등기사항전부증명서 1부 제출", "인감증명서 1부 제출"]

    def run_prepare(self, prep, forms=()):
        return prepare(prep, self.text, list(forms), self.docs, "2026-11-20", "2026-11-01", date(2026, 11, 5))

    def test_copies_usable_and_lists_needed(self):
        prep = self.dir / "회사준비서류"
        need, ref = self.run_prepare(prep)
        self.assertEqual(ref, "2026-11-20")
        self.assertTrue(list(prep.glob("*_사업자등록증/사업자등록증_2025-08-18.pdf")))
        self.assertEqual(list(prep.glob("*_법인등기부등본/*")), [])  # 3개월 넘음 — 폴더에 안 넣음
        self.assertTrue(any(i == "법인등기부등본" and "재발급 필요" in why for i, _, why in need))
        self.assertTrue(any(i == "인감증명서" and "직접 준비" in why for i, _, why in need))
        self.assertEqual(list(prep.glob("*_견적서")), [])  # 공고문에 없는 서류는 안 만듦(11/10)
        text = (prep / "준비서류_체크리스트.txt").read_text(encoding="utf-8")
        self.assertIn("※ 서류 판단 기준일 2026-11-20 (제출 마감 2026-11-20)", text)

    def test_notice_list_order(self):
        prep = self.dir / "회사준비서류"
        lines = ["□ 아래의 서류를 모두 제출", "1) 사업자등록증 사본(원본대조필), 법인등기부등본(해당 시) 각 1부",
                 "2) 박물관·미술관 학예사 자격증 사본 1부", "3) 영인 및 족자 장황 제작사양서", "(공정·재료 명시) 1부",
                 "※ 사본은 원본대조필"]
        items = listed_items(lines)
        self.assertEqual(items[2], "영인 및 족자 장황 제작사양서 (공정·재료 명시) 1부")  # 다음 줄로 넘어간 글
        need, _ = prepare(prep, self.text, [], self.docs, "2026-11-20", "2026-11-01", date(2026, 11, 5), listed=items)
        names = sorted(p.name for p in prep.iterdir() if p.is_dir())
        self.assertEqual(names, ["01_사업자등록증 사본, 법인등기부등본", "02_박물관·미술관 학예사 자격증 사본", "03_영인 및 족자 장황 제작사양서"])
        self.assertTrue(list(prep.glob("01_*/사업자등록증_2025-08-18.pdf")))  # 한 항목에 든 회사 서류 둘 다 봄
        self.assertTrue(any("재발급 필요" in why for _, _, why in need))  # 법인등기 3개월 넘음
        self.assertIn("직접 준비", (prep / "준비서류_체크리스트.txt").read_text(encoding="utf-8"))

    def test_form_item_is_not_repeated(self):
        prep = self.dir / "회사준비서류"
        self.run_prepare(prep, ["서식03_인감증명서"])
        self.assertEqual(list(prep.glob("*_인감증명서")), [])
        self.assertIn("서식 파일로 이미 있는 것(서식자동분리): 인감증명서", (prep / "준비서류_체크리스트.txt").read_text(encoding="utf-8"))

    def test_none_only_judges(self):
        need, _ = self.run_prepare(None)
        self.assertTrue(need)
        self.assertFalse((self.dir / "회사준비서류").exists())


if __name__ == "__main__":
    unittest.main()
