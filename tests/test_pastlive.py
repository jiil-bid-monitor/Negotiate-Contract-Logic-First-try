"""과거사업 목록의 2026년~ 사업 폴더(webapp/pastlive.py) — 진행 중(YYYY 프로젝트 바로 아래)/끝남(실행완료), 사업 문서 없는 폴더·기존 목록 폴더는 뺌."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from webapp import pastlive


class _TP:
    calls = 0

    @classmethod
    def build_profile(cls, texts, title=""):
        cls.calls += 1
        return SimpleNamespace(tags={"과업유형": ["제작·설치"]}, counts={}, evidence={})


class PastLiveTest(unittest.TestCase):
    def test_build_from_project_folders(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "002"
            def mk(rel, doc=None):
                f = root / rel; f.mkdir(parents=True, exist_ok=True)
                if doc: (f / doc).write_text("과업", encoding="utf-8")
            mk("2025 프로젝트/2025_옛사업", "제안요청서.hwp")          # 2026년 앞 — 안 봄
            mk("2026 프로젝트/260514_동해전천스테이션", "과업지시서.hwp")  # 진행 중
            mk("2026 프로젝트/동해전천스테이션", "과업지시서.hwp")        # 같은 이름(날짜 앞말만 다름) — 하나만
            mk("2026 프로젝트/설계 파일 서식", "양식.xlsx")             # 사업 문서 없음 — 뺌
            mk("2026 프로젝트/실행완료/광양시교통안전체험관", "20251126_공고서(물품).hwp")  # 끝남
            mk("2026 프로젝트/실행완료/2026_이청준문학관", "제안요청서.hwp")  # 기존 155건에 있음 — 뺌
            base = [{"folders": ["2026/2026_이청준문학관"]}]
            with mock.patch("quantdocs.notice.doc_text", lambda p: "과업 내용"):
                rows = pastlive.build(Path(d), _TP, base, root=root)
                by = {r["title"]: r for r in rows}
                self.assertEqual(set(by), {"동해전천스테이션", "광양시교통안전체험관"})
                self.assertEqual(by["동해전천스테이션"]["status"], "진행 중")
                self.assertEqual(by["광양시교통안전체험관"]["status"], "끝남")
                self.assertEqual(by["광양시교통안전체험관"]["year"], 2026)
                n = _TP.calls
                pastlive.build(Path(d), _TP, base, root=root)
                self.assertEqual(_TP.calls, n)  # 문서가 같으면 다시 안 읽음
            self.assertTrue(json.loads((Path(d) / pastlive.LIVE_FILE).read_text(encoding="utf-8")))


if __name__ == "__main__":
    unittest.main()
