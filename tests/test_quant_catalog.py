from pathlib import Path

from quantdocs.__main__ import main
from quantdocs.catalog import load_catalog, scan


def test_catalog_has_eight_documents():
    docs = load_catalog()
    assert len(docs) == 8
    assert {d.per_notice for d in docs} <= {"fixed", "varies", "when_required", "validity"}


def test_scan_matches_by_filename_ignoring_spaces(tmp_path: Path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "회사 연혁_2026.pdf").write_bytes(b"x")
    (tmp_path / "sub" / "직접 생산 확인증명서.pdf").write_bytes(b"x")
    (tmp_path / "메모.txt").write_text("연혁")  # 문서 형식 아님 → 안 셈
    found = scan(tmp_path, load_catalog())
    assert [p.name for p in found["history"]] == ["회사 연혁_2026.pdf"]
    assert [p.name for p in found["direct_production"]] == ["직접 생산 확인증명서.pdf"]
    assert found["credit_rating"] == []
    assert found["reissue"] == []


def test_cli_check(tmp_path: Path, capsys):
    (tmp_path / "신용평가등급확인서.pdf").write_bytes(b"x")
    assert main(["check", "--dir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "✓ 경영상태" in out and "✗ 회사 연혁" in out
