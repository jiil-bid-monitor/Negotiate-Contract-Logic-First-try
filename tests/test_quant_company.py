"""정량평가서 회사 고르기(2026-11-24) — 그리다는 묶음 없이 옆 폴더 grida-quant-docs의 서류를 읽고, 보유 자격도 그리다 파일."""

from __future__ import annotations

import io
import json
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402
from openpyxl import Workbook  # noqa: E402

from quantdocs import company, g2b, vault, web  # noqa: E402


def _index() -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "회사정보"
    ws.append(["항목", "값"])
    ws.append(["회사명", "(주)가나다"])
    ws.append(["대표자", "홍길동"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_grida_folder_vault(monkeypatch):
    folder = Path(tempfile.mkdtemp()) / "grida-quant-docs"
    (folder / "그리다 자료" / "법인일괄서류").mkdir(parents=True)
    (folder / "그리다 자료" / vault.INDEX).write_bytes(_index())
    (folder / "그리다 자료" / "법인일괄서류" / "사업자등록증.pdf").write_bytes(b"%PDF-1.4 fake")
    monkeypatch.setenv("QUANT_GRIDA_DIR", str(folder))
    company.use("grida")
    try:
        v = vault.load_company()
        assert v.info["회사명"] == "(주)가나다"
        assert any(k.endswith("사업자등록증.pdf") for k in v.files), "하위 폴더 서류도"
        assert company.held_file() == "companies/grida/held_qualifications.json"
        held_names, _ = g2b._names()
        assert "1469" in held_names and "0036" not in held_names, "그리다 보유 자격으로 판정"
        assert json.loads(web.api("GET", "/meta", company="grida")[2])["vault"]["ok"]
    finally:
        company.use("jiil")


def test_missing_grida_folder_message(monkeypatch):
    monkeypatch.setenv("QUANT_GRIDA_DIR", str(Path(tempfile.mkdtemp()) / "없음"))
    company.use("grida")
    try:
        with pytest.raises(vault.VaultError, match="그리다 자료 폴더가 없습니다"):
            vault.load_company()
    finally:
        company.use("jiil")


def test_company_is_per_thread():
    """작업 스레드마다 회사가 따로 — 그리다 작업이 지일 작업을 바꾸지 않는다."""
    seen = {}

    def run(co):
        company.use(co)
        seen[co] = company.held_file()
    threads = [threading.Thread(target=run, args=(c,)) for c in ("grida", "jiil")]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert seen == {"grida": "companies/grida/held_qualifications.json", "jiil": "held_qualifications.json"}
    assert company.current() == "jiil"


def test_quant_iframe_carries_company():
    root = Path(__file__).resolve().parent.parent
    assert "co=" in (root / "quantdocs" / "static" / "index.html").read_text(encoding="utf-8")
    assert "/quant/?co=" in (root / "webapp" / "static" / "index.html").read_text(encoding="utf-8")


def test_bizreg_company_name_with_space():
    """사업자등록증 '법인명(단체명) : 주식회사 그리다' — '주식회사'만 읽던 것(11/24)."""
    from quantdocs.importer import read_bizreg

    assert read_bizreg("법 인 명 ( 단 체 명 ) : 주식회사 그리다\n대표자")["회사명"] == "주식회사 그리다"
    assert read_bizreg("법인명(단체명) : (주)지일   대표자(대표유형) : 홍")["회사명"] == "(주)지일"


def test_grida_rep_from_env(monkeypatch, tmp_path):
    """(11/24 사용자 요청) 그리다 대표자 칸 전체 = .env QUANT_GRIDA_REP·REP_RRN·REP_MOBILE·REP_TITLE, 자본금·직원 수도.
    지일은 대표자 휴대폰 칸에 여전히 회사 전화(11/20 규칙)."""
    from quantdocs import fill, importer

    env = tmp_path / ".env"
    env.write_text("QUANT_GRIDA_REP=홍길동\nQUANT_GRIDA_REP_RRN=670222 -1*\nQUANT_GRIDA_REP_MOBILE=010-0000-0000\n"
                   "QUANT_GRIDA_REP_TITLE=이사\nQUANT_GRIDA_CAPITAL=200000000\nQUANT_GRIDA_STAFF=6\n"
                   "QUANT_REP_MOBILE=010-9999-9999\n", encoding="utf-8")
    monkeypatch.setattr(vault, "ENV_FILE", env)

    class V:
        info = {"대표자": "등기 대표", "전화번호": "031-000-0000"}
        files: dict = {}
        history: list = []

    company.use("grida")
    try:
        v = V()
        v.info = dict(V.info)
        importer.enrich_vault(v)
        assert v.info["대표자"] == "홍길동"
        assert v.info["대표자 주민등록번호"] == "670222-1******"
        assert v.info["대표자 직위"] == "이사"
        assert fill._value("대표자 휴대폰", v) == "010-0000-0000"
        assert fill._value("자본금", v) == "200,000,000원"
        assert fill._value("종업원 수", v) == "6명"
        assert fill._birth(v.info["대표자 주민등록번호"]) == "1967. 02. 22."
    finally:
        company.use("jiil")
    v = V()
    v.info = dict(V.info)
    importer.enrich_vault(v)
    assert v.info["대표자"] == "등기 대표"
    assert fill._value("대표자 휴대폰", v) == "031-000-0000"
