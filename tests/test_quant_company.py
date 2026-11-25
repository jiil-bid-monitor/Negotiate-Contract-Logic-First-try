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
    """(11/25 사용자 결정) 그리다 대표자 칸 = 등기상 대표(서류 값 그대로), .env 지정 임원(QUANT_GRIDA_REP… / AGENT…)은 vault.agent —
    위임장 대리인 칸에만. 등기 대표 주민번호는 QUANT_GRIDA_CEO_RRN. 자본금·직원 수는 그대로 .env. 지일은 예전대로."""
    from quantdocs import fill, importer

    env = tmp_path / ".env"
    env.write_text("QUANT_GRIDA_REP=홍길동\nQUANT_GRIDA_REP_RRN=670222 -1*\nQUANT_GRIDA_REP_MOBILE=010-0000-0000\n"
                   "QUANT_GRIDA_REP_TITLE=이사\nQUANT_GRIDA_AGENT_ADDR=가상시 집로 2\nQUANT_GRIDA_CEO_RRN=900101-2\n"
                   "QUANT_GRIDA_CAPITAL=200000000\nQUANT_GRIDA_STAFF=6\nQUANT_REP_MOBILE=010-9999-9999\n", encoding="utf-8")
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
        assert v.info["대표자"] == "등기 대표"
        assert v.info["대표자 주민등록번호"] == "900101-2******"
        assert "대표자 직위" not in v.info
        assert v.agent == {"성명": "홍길동", "주민등록번호": "670222-1******", "직위": "이사",
                           "휴대폰": "010-0000-0000", "주소": "가상시 집로 2"}
        assert fill._value("대표자 휴대폰", v) == "031-000-0000"  # 지일과 같은 규칙 — 대표자 칸에 개인 번호는 안 넣음
        assert fill._value("자본금", v) == "200,000,000원"
        assert fill._value("종업원 수", v) == "6명"
    finally:
        company.use("jiil")
    v = V()
    v.info = dict(V.info)
    importer.enrich_vault(v)
    assert v.info["대표자"] == "등기 대표"
    assert not getattr(v, "agent", None)
    assert fill._value("대표자 휴대폰", v) == "031-000-0000"


def test_grida_overview_and_history_from_env(monkeypatch, tmp_path):
    """(11/25 사용자 요청) 그리다 설립일·사업분야·신용평가등급·연혁도 .env로 — 연혁은 '|'로 나눈 줄."""
    from quantdocs import fill, importer

    env = tmp_path / ".env"
    env.write_text("QUANT_GRIDA_NAME=㈜가상\nQUANT_GRIDA_ADDR=가상시 길 1\nQUANT_GRIDA_FOUNDED=2000-01-02\nQUANT_GRIDA_FIELD=제조,건설\nQUANT_GRIDA_CREDIT=BB0\n"
                   "QUANT_GRIDA_CREDIT_KIND=기업신용평가등급\n"
                   "QUANT_GRIDA_HISTORY=2000.01.02 법인설립|2001.03.04 공장등록\n", encoding="utf-8")
    monkeypatch.setattr(vault, "ENV_FILE", env)

    class V:
        info: dict = {}
        files: dict = {}
        history = [{"연월일": "1999.01", "내용": "예전 줄"}]

    company.use("grida")
    try:
        v = V()
        v.info = {"회사명": "주식회사 가상"}
        importer.enrich_vault(v)
        assert v.info["회사명"] == "(주)가상" and v.info["주소"] == "가상시 길 1"
        assert v.info["신용평가등급"] == "BB0" and v.info["신용평가 종류"] == "기업신용평가등급"
        assert fill._value("사업분야", v) == "제조,건설"
        assert fill._value("설립일", v)
        assert [h["내용"] for h in v.history] == ["법인설립", "공장등록"]
    finally:
        company.use("jiil")


def _staff_xlsx(name: str) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.append(["이름", "입사일", "등급"])
    ws.append([name, "2015.01.01", "특급"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_grida_staff_is_jiil_staff(monkeypatch):
    """(11/25 사용자 결정) 그리다 인력 자료 = 지일 인력 — 그리다 폴더에 인력 엑셀이 있어도 지일 것."""
    root = Path(tempfile.mkdtemp())
    jiil = root / "jiil-quant-docs" / "기술인력"
    jiil.mkdir(parents=True)
    (jiil / "인력목록.xlsx").write_bytes(_staff_xlsx("가지일"))
    grida = root / "grida-quant-docs"
    grida.mkdir()
    (grida / vault.INDEX).write_bytes(_index())
    monkeypatch.setenv("QUANT_LOOSE_DIR", str(root / "jiil-quant-docs"))
    monkeypatch.setenv("QUANT_GRIDA_DIR", str(grida))
    company.use("grida")
    try:
        v = vault.load_company()
        assert [s["이름"] for s in v.staff] == ["가지일"]
        assert v.staff_src.startswith("지일 인력")
        (grida / "그리다인력.xlsx").write_bytes(_staff_xlsx("나그리"))
        v = vault.load_company()
        assert [s["이름"] for s in v.staff] == ["가지일"], "그리다 폴더 인력 엑셀이 있어도 지일 인력"
    finally:
        company.use("jiil")


def test_grida_jiil_staff_no_note():
    """(11/25 사용자) 그리다가 지일 인력을 쓸 때 안내 문구 없음."""
    from quantdocs import importer

    v = vault.Vault({}, staff=[{"이름": "가", "입사일": None, "재직": "재직", "등급": "특급"}], staff_src="지일 인력(x.xlsx)")
    notes = importer.enrich_vault(v) or []
    assert not any("기술인력" in n for n in notes)


def test_grida_seal_form_uses_jiil_vault(monkeypatch, tmp_path):
    """(11/25 사용자 결정) 그리다 화면에서 만들어도 사용인감계만 지일 회사 정보 — 다른 서식은 그리다 것."""
    from quantdocs import build, importer

    jiil = vault.Vault({}, info={"회사명": "(주)지일", "대표자": "지일대표"})
    monkeypatch.setattr(vault, "load", lambda *a, **k: jiil)
    monkeypatch.setattr(importer, "enrich_vault", lambda v, report=None: [])
    grida = vault.Vault({}, info={"회사명": "주식회사 그리다"})
    build._JIIL_VAULT.clear()
    company.use("grida")
    try:
        assert build._seal_vault("사 용 인 감 계", grida) is jiil
        assert build._seal_vault("위임장", grida) is grida
        # 서식 이름에 '사용인감'이 없어도 본문이 '사 용 인 감 | 인 적 사 항' 표면 사용인감계(11/25 해남)
        import zipfile
        for name, body in (("seal.hwpx", "사 용 인 감</hp:t><hp:t>인 적 사 항"), ("pledge.hwpx", "서 약 자 인(사용인감)")):
            path = tmp_path / name
            with zipfile.ZipFile(path, "w") as z:
                z.writestr("Contents/section0.xml", f"<hp:p><hp:run><hp:t>{body}</hp:t></hp:run></hp:p>")
            assert (build._seal_vault("서식 7", grida, path) is jiil) == (name == "seal.hwpx")
    finally:
        company.use("jiil")
    assert build._seal_vault("사용인감계", grida) is grida, "지일 화면은 그대로"
    build._JIIL_VAULT.clear()


def test_company_name_short_form(monkeypatch, tmp_path):
    """(11/25 사용자 요청) 업체명·회사명은 '주식회사 그리다' 대신 '(주)그리다'."""
    from quantdocs import importer

    env = tmp_path / ".env"
    env.write_text("", encoding="utf-8")
    monkeypatch.setattr(vault, "ENV_FILE", env)

    class V:
        files: dict = {}
        history: list = []
    for raw in ("주식회사 가상", "가상 주식회사", "(주)가상", "㈜가상"):
        v = V()
        v.info = {"회사명": raw}
        importer.enrich_vault(v)
        assert v.info["회사명"] == "(주)가상", raw


def test_grida_licenses_from_held_file():
    """(11/25 사용자) 서류상태 시트가 없는 그리다도 '면허/허가/등록증 보유현황' = 보유 자격 파일의 등록 업종."""
    from quantdocs import fill

    company.use("grida")
    try:
        text = fill._value("_licenses", vault.Vault({}))
    finally:
        company.use("jiil")
    assert "실내건축공사업" in text and "비디오물제작업" in text
    assert text.count("실내건축공사업") == 1


def test_read_env_tolerant_lines(monkeypatch, tmp_path):
    """(11/25) 'KEY = 값'·'export KEY=값'·메모장 한글 인코딩(ANSI) .env도 읽음 — 'KEY=값'은 예전처럼 글자 그대로."""
    env = tmp_path / ".env"
    monkeypatch.setattr(vault, "ENV_FILE", env)
    env.write_bytes("QUANT_GRIDA_AGENT = 홍길동\n".encode("cp949"))
    assert vault.read_env("QUANT_GRIDA_AGENT") == "홍길동"
    env.write_text("export QUANT_GRIDA_AGENT=홍길동\r\nQUANT_VAULT_PASSPHRASE= a b \n", encoding="utf-8")
    assert vault.read_env("QUANT_GRIDA_AGENT") == "홍길동"
    assert vault.read_env("QUANT_VAULT_PASSPHRASE") == " a b "  # 정확한 줄은 글자 그대로
