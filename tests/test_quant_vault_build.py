import datetime as dt
import io
import json
import os
import threading
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

import pytest
from openpyxl import load_workbook

from quantdocs import vault
from quantdocs.fill import _label_key
from quantdocs.templates import build as make_template

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "attachments"
PW = "test-pass-123456"
needs_sample = pytest.mark.skipif(not (FIXTURES / "jeongseon_culture_center_rfp.hwpx").exists(), reason="프로젝트 1 공고 표본 없음")


def _blank_pdf(n: int = 1) -> bytes:
    from pypdf import PdfWriter

    w = PdfWriter()
    for _ in range(n):
        w.add_blank_page(100, 100)
    b = io.BytesIO()
    w.write(b)
    return b.getvalue()


def _company(tmp: Path) -> Path:
    """가짜 회사자료 폴더(시험용 — 실제 자료 아님)."""
    src = tmp / "회사자료"
    (src / "files").mkdir(parents=True)
    p = make_template(src)
    wb = load_workbook(p)
    ws = wb["회사정보"]
    vals = {"회사명": "(가상)시험회사", "대표자": "(가상)대표", "주소": "가상시 가상로 1", "설립일": "2009-09-23"}
    for r in range(2, ws.max_row + 1):
        if ws.cell(r, 1).value in vals:
            ws.cell(r, 2, vals[ws.cell(r, 1).value])
    wb["연혁"].append([dt.date(2009, 9, 23), "(가상) 법인 설립", "연혁.pdf"])
    wb["서류상태"].append([1, "공고가 요구하는 자격 서류", "(가상) 등록증", "4990, 4442", None, None, None, "사용", "실내건축공사업등록증.pdf"])
    wb["실적목록"].append([1, "공사", "(가상) 전시관", "(가상) 시", dt.date(2024, 1, 1), dt.date(2024, 6, 30),
                       250_000_000, "포함", 100, "전시·체험", "실적1.pdf"])
    wb.save(p)
    for f in ("연혁.pdf", "실내건축공사업등록증.pdf", "실적1.pdf"):
        (src / "files" / f).write_bytes(_blank_pdf())
    return src


def test_vault_roundtrip_and_wrong_password(tmp_path: Path):
    out = vault.seal_folder(_company(tmp_path), PW, tmp_path / "c.enc")
    raw = out.read_bytes()
    assert raw.startswith(vault.MAGIC) and "(가상)시험회사".encode() not in raw
    v = vault.load(PW, out)
    assert v.info["회사명"] == "(가상)시험회사" and v.held_codes() == {"4990", "4442"}
    assert v.file("실내건축공사업등록증.pdf").startswith(b"%PDF") and v.tracks[0]["사업명"] == "(가상) 전시관"
    with pytest.raises(vault.VaultError):
        vault.load("wrong-password!!", out)
    with pytest.raises(vault.VaultError):
        vault.seal_bytes(b"x", "short")


def test_label_keys():
    assert _label_key("홈페이지주소") == "홈페이지" and _label_key("주        소") == "주소"
    assert _label_key("상호 또는 법인명칭") == "회사명" and _label_key("주민(법인)등록번호") is None
    assert _label_key("대리인 성명") is None


@needs_sample
def test_build_package(tmp_path: Path):
    from quantdocs.build import build

    v = vault.load(PW, vault.seal_folder(_company(tmp_path), PW, tmp_path / "c.enc"))
    out = build([FIXTURES / "jeongseon_culture_center_rfp.hwpx"], v, tmp_path / "o.zip", "정선", dt.date(2026, 8, 20))
    z = zipfile.ZipFile(out)
    names = z.namelist()
    assert "공고별_준비표.xlsx" in names and "통합본.pdf" in names and "첨부/실적/실적1.pdf" in names
    assert any(n.startswith("첨부/통합본_순서/") and n.endswith("실내건축공사업등록증.pdf") for n in names)
    assert sum(n.startswith(("서식/", "서식_직접기입/")) for n in names) == 31
    sec = zipfile.ZipFile(io.BytesIO(z.read("서식/서식14_일반현황_및_연혁.hwpx"))).read("Contents/section0.xml").decode()
    assert "(가상)시험회사" in sec and "(가상) 법인 설립" in sec
    manual = [n for n in names if n.startswith("서식_직접기입/")]
    filled = {n.split("/", 1)[1] for n in names if n.startswith("서식/")}
    assert manual and not filled & {n.split("/", 1)[1] for n in manual}  # 직접 기입 서식은 채운 서식 폴더에 없음
    assert any("인력" in n or "이력" in n or "조직" in n for n in manual)
    # 통합서식.hwpx: 서식 31개가 한 한글 파일에 순서대로(채운 값 포함) — 다시 잘라도 31개
    merged = tmp_path / "m.hwpx"
    merged.write_bytes(z.read("통합서식.hwpx"))
    from quantdocs.forms_cut import cut as recut
    assert len(recut(merged, tmp_path / "recut")) == 31
    assert "(가상)시험회사" in zipfile.ZipFile(merged).read("Contents/section0.xml").decode()
    sec16 = zipfile.ZipFile(io.BytesIO(z.read("서식/서식16_사업수행_실적집계표.hwpx"))).read("Contents/section0.xml").decode()
    assert "(가상) 전시관" in sec16 and "250,000,000" in sec16
    wb = load_workbook(io.BytesIO(z.read("공고별_준비표.xlsx")))
    assert {"자격요건", "통합본 순서", "자동입력", "준비 필요"} <= set(wb.sheetnames)


@needs_sample
def test_web_flow(tmp_path: Path, monkeypatch):
    from http.server import ThreadingHTTPServer

    from quantdocs import web

    enc = vault.seal_folder(_company(tmp_path), PW, tmp_path / "c.enc")
    off = tmp_path / "off"
    off.mkdir()
    (off / "rfp.hwpx").write_bytes((FIXTURES / "jeongseon_culture_center_rfp.hwpx").read_bytes())
    monkeypatch.setattr(vault, "SEALED", enc)
    monkeypatch.setattr(vault, "ENV_FILE", tmp_path / "없는.env")
    monkeypatch.setattr(web, "JOBS", tmp_path / "jobs")
    monkeypatch.setattr(web, "TOKEN", "tok")
    monkeypatch.setenv(vault.ENV, PW)
    monkeypatch.setenv("QUANT_OFFLINE_NOTICE_DIR", str(off))
    srv = ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"

    def call(path, body=None, token=True):
        url = base + path + ("?t=tok" if token else "")
        req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.read(), r.headers

    def wait(job, state):
        for _ in range(240):
            j = json.loads(call(f"/api/job/{job}")[0])
            if j["state"] != state:
                return j
            time.sleep(0.5)
        raise AssertionError("시간 초과")

    try:
        # 회사 자료가 없어도 서식·자격 목록은 보이고, 만들기만 막힘
        monkeypatch.delenv(vault.ENV)
        job0 = json.loads(call("/api/analyze", {"no": "R26BK01790000"})[0])["id"]
        j0 = wait(job0, "running")
        assert j0["state"] == "ready" and j0["vault_ok"] is False and j0["summary"]["forms"], j0
        with pytest.raises(urllib.error.HTTPError):
            call(f"/api/build/{job0}", {})
        monkeypatch.setenv(vault.ENV, PW)
        with pytest.raises(urllib.error.HTTPError):
            call("/api/meta", token=False)
        job = json.loads(call("/api/analyze", {"no": "R26BK01790000"})[0])["id"]
        j = wait(job, "running")
        assert j["state"] == "ready", j
        assert any(f["no"] == 16 for f in j["summary"]["forms"])
        assert j["summary"]["kind"]["id"] == "negotiation" and j["summary"]["kind"]["docs"]
        assert j["notice"]["ref_date"] and j["notice"]["ref_from"].startswith("오늘")  # 오프라인 시험은 게시일 없음
        assert any(it["code"] == "4990" and it["held"] for q in j["summary"]["quals"] for it in q["items"])
        call(f"/api/build/{job}", {})
        j = wait(job, "building")
        assert j["state"] == "built" and j["download"], j
        data, headers = call(j["download"])
        assert "통합본.pdf" in zipfile.ZipFile(io.BytesIO(data)).namelist()
        assert any(b["file"] == "실내건축공사업등록증.pdf" for b in j["summary"]["bundle"])
        assert "filename*=UTF-8''" in headers["Content-Disposition"]
    finally:
        srv.shutdown()


def test_detect_kind_and_guide():
    from quantdocs.kinds import detect, guide

    assert detect("협상에의한계약") == "negotiation"
    assert detect("적격심사(지방자치단체)") == "qualified"
    assert detect("", "", {"a": "본 용역은 건설기술용역 사업수행능력평가 기준에 따라"}) == "pq"
    assert detect("", "", {"a": "사업수행능력 평가자료(정량적 지표) 2부 … 협상에 의한 계약"}) == "negotiation"
    assert detect("최저가낙찰") == "other"
    assert "개찰" in guide("qualified")["when"] and "track_record" in guide("qualified")["docs"]


def test_passphrase_from_env_file(tmp_path: Path, monkeypatch):
    """.env 값을 글자 그대로 — '$'·따옴표가 있어도, 윈도우 줄 끝(CRLF)이어도."""
    f = tmp_path / ".env"
    f.write_bytes(b"APP_TOKEN=x\r\nQUANT_VAULT_PASSPHRASE=ab$cd \"ef\"gh12\r\n")
    monkeypatch.setattr(vault, "ENV_FILE", f)
    monkeypatch.setenv(vault.ENV, "bash-mangled")
    assert vault.read_passphrase() == 'ab$cd "ef"gh12'
    monkeypatch.setattr(vault, "ENV_FILE", tmp_path / "none")
    assert vault.read_passphrase() == "bash-mangled"


def test_bundle_pick_by_file_name_and_merge(tmp_path: Path):
    """회사 서류 내용은 안 읽고 파일 이름 키워드로만 골라 원본을 이어 붙임."""
    from pypdf import PdfReader

    from quantdocs.bundle import merge, pick

    pdf = _blank_pdf

    files = {"회사자료_양식.xlsx": b"x", "files/법인/사업자등록증.pdf": pdf(1), "files/법인/인감증명서.pdf": pdf(2),
             "files/법인/실내건축공사업-가상.pdf": pdf(1), "files/직접생산/조형물(~27.6.17).pdf": pdf(3),
             "files/직접생산/책상(~20.1.1).pdf": pdf(1)}
    quals = [{"kind": "필수", "items": [{"name": "실내건축공사업", "code": "4990", "held": True}]},
             {"kind": "필수", "items": [{"name": "조형물", "code": "6012100201", "held": True}]},
             {"kind": "필수", "items": [{"name": "책상", "code": "5610170301", "held": True}]},
             {"kind": "필수", "items": [{"name": "라운지용의자", "code": "5611210501", "held": True}]}]
    submit = ["사업자등록증 사본 1부", "법인 등기부 등본 1부", "직접생산확인증명서", "인감증명서 1부"]
    picks, missing = pick(files, submit, quals, today=dt.date(2026, 11, 3))
    assert [p.path.rsplit("/", 1)[-1] for p in picks] == [
        "사업자등록증.pdf", "조형물(~27.6.17).pdf", "책상(~20.1.1).pdf", "인감증명서.pdf", "실내건축공사업-가상.pdf"]
    assert "지남" in picks[2].note and not picks[1].note
    assert any("등기" in m for m in missing) and any("라운지용의자" in m for m in missing)
    out, skipped = merge(picks, vault.Vault(files), tmp_path / "통합본.pdf")
    toc_pages = 0 if any("목차" in x for x in skipped) else 1  # 목차 장은 한글 글꼴이 있을 때만
    assert len(PdfReader(str(out)).pages) == 8 + toc_pages  # 원본 8쪽
    assert len(PdfReader(str(out)).outline) == 5


def test_manual_forms():
    from quantdocs.build import is_manual

    for n in ("참여인력 자격사항(총괄)", "참여인력 개인별 이력사항", "수행조직 및 인원현황", "사업책임자(PM) 경력",
              "재직증명서", "사업수행능력평가 자기평가표", "실적증명서"):
        assert is_manual(n), n
    for n in ("입찰참가신청서", "일반현황 및 연혁", "사업수행실적 집계표", "위임장", "청렴계약이행서약서"):
        assert not is_manual(n), n
