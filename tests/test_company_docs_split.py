"""회사서류_최신을 회사별 폴더(지일·그리다)로 나눈 모양 (2026-11-26 사용자 — 섞여 있어 확인하기 어려움).
인증서갱신.xlsx는 위 폴더에 하나, '(그리다) …' 서류는 그리다 폴더, 그 밖은 지일 폴더. 나누기 전 한 폴더도 그대로 돌아야 함."""
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from under100m import docs
from webapp import docexpiry, docupload


def _folder(root: Path, name: str, rows: list[tuple[str, str, str]]) -> None:
    (root / name / docs.LATEST).mkdir(parents=True)
    out = []
    for kind, issued, end in rows:
        file = f"{kind}_{issued}" + (f"_유효{end}" if end else "") + ".pdf"
        (root / name / docs.LATEST / file).write_bytes(b"%PDF")
        out.append({"종류": kind, "파일": file, "발급일": issued, "유효기간": end, "대표자": "", "원본 위치": docs.LATEST})
    (root / name / docs.LIST_JSON).write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")


class SplitFolderTest(unittest.TestCase):
    def setUp(self):
        import openpyxl

        self.root = Path(tempfile.mkdtemp())
        _folder(self.root, "지일", [("신용평가등급확인서", "2026-06-16", "2027-06-15"), ("국세 납세증명서", "2026-03-01", "")])
        _folder(self.root, "그리다", [("(그리다) 중소기업확인서", "2026-04-06", "2027-03-31")])
        wb = openpyxl.Workbook()
        wb.active.append(("여성기업", "29.11.5", "", "그리다"))
        wb.save(self.root / docexpiry.CERT_FILE)
        today = date(2026, 11, 26)
        for sub in docs.company_dirs(self.root):
            docs.run(sub, today, settle=0, now=9e12, alert_due=False)

    def test_folders_and_routing(self):
        self.assertEqual([p.name for p in docs.company_dirs(self.root)], ["지일", "그리다"])
        self.assertEqual(docs.company_dir(self.root, "(그리다) 여성기업").name, "그리다")
        self.assertEqual(docs.company_dir(self.root, "사용인감계").name, "지일")
        self.assertEqual(docs.company_dir(self.root / "그리다", "(그리다) 여성기업"), self.root / "그리다", "회사 폴더는 그대로")
        self.assertTrue(docexpiry.ready(self.root))
        self.assertEqual(docexpiry.cert_path(self.root / "그리다"), self.root / docexpiry.CERT_FILE, "표는 위 폴더에 하나")

    def test_lists_merge_both_companies(self):
        got = dict(docexpiry.read_docs(self.root))
        self.assertEqual(got["신용평가등급확인서"], date(2027, 6, 15))
        self.assertEqual(got["(그리다) 중소기업확인서"], date(2027, 3, 31))
        self.assertEqual(got["(그리다) 여성기업"], date(2029, 11, 5), "인증서갱신.xlsx 그리다 줄")
        self.assertIn("(그리다) 중소기업확인서", docupload.kinds(self.root))
        self.assertIn("국세 납세증명서", docupload.kinds(self.root))
        self.assertTrue(any("국세 납세증명서 (보유본 2026-03-01)" in line for line in docs.issue_lines(self.root)), "지일 목록")

    def test_save_goes_to_company_folder(self):
        today = date(2026, 11, 26)
        name, _, _ = docupload.save_doc(self.root, "(그리다) 법인등기사항전부증명서", b"%PDF g", None, today)
        self.assertTrue((self.root / "그리다" / docs.LATEST / name).exists())
        name, _, _ = docupload.save_doc(self.root, "사용인감계", b"%PDF j", None, today)
        self.assertTrue((self.root / "지일" / docs.LATEST / name).exists())
        self.assertFalse((self.root / docs.LATEST).exists(), "위 폴더엔 [최신 서류]를 만들지 않음")
        kinds_g = [r["종류"] for r in json.loads((self.root / "그리다" / docs.LIST_JSON).read_text(encoding="utf-8"))]
        self.assertIn("(그리다) 법인등기사항전부증명서", kinds_g)
        moved, _ = docupload.remove_doc(self.root, "(그리다) 법인등기사항전부증명서", today)
        self.assertEqual(moved, 1)
        self.assertTrue(any((self.root / "그리다" / docs.OLD).glob("(그리다) 법인등기*")))
        files = {d["kind"]: d["file"] for d in docupload.all_docs(self.root, today)}
        self.assertTrue(files["(그리다) 중소기업확인서"].startswith("(그리다) 중소기업확인서_"))

    def test_one_folder_still_works(self):
        one = Path(tempfile.mkdtemp())
        _folder(one, ".", [("사업자등록증", "2026-08-25", "")])
        self.assertEqual(docs.company_dirs(one), [one])
        self.assertEqual(docs.company_dir(one, "(그리다) 여성기업"), one)
        self.assertTrue(docexpiry.ready(one))
        name, _, _ = docupload.save_doc(one, "(그리다) 여성기업", b"%PDF", None, date(2026, 11, 26))
        self.assertTrue((one / docs.LATEST / name).exists())


if __name__ == "__main__":
    unittest.main()
