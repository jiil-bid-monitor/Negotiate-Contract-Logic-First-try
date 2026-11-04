import re
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
    # 11/04 사용자 결정: 제출 파일 둘 — 통합본.pdf(원본 그대로) + 통합서식.hwpx, 서류 원본은 PDF에 못 넣은 것만
    assert not any(n.startswith("첨부/통합본_순서/") for n in names)
    from pypdf import PdfReader
    assert len(PdfReader(io.BytesIO(z.read("통합본.pdf"))).outline) >= 1
    # 11/04 사용자 결정: 한글은 통합서식.hwpx 하나만 — 서식별 파일은 통합서식을 못 만들 때만
    assert not any(n.startswith(("서식/", "서식_직접기입/")) for n in names)
    # 통합서식.hwpx: 서식 31개가 한 한글 파일에 순서대로(채운 값 포함) — 다시 잘라도 31개
    merged = tmp_path / "m.hwpx"
    merged.write_bytes(z.read("통합서식.hwpx"))
    from quantdocs.forms_cut import cut as recut
    parts = recut(merged, tmp_path / "recut")
    assert len(parts) == 31
    sec = "".join(zipfile.ZipFile(merged).read(n).decode() for n in zipfile.ZipFile(merged).namelist()
                  if n.startswith("Contents/section"))
    assert "(가상)시험회사" in sec and "(가상) 법인 설립" in sec  # 서식14 일반현황·연혁
    assert "(가상) 전시관" in sec and "250,000,000" in sec  # 서식16 실적집계표
    readme = z.read("읽어보기.txt").decode()
    assert "통합서식.hwpx" in readme and "서식_직접기입/" not in readme
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


def _hp_cell(col, row, text, colspan=1):
    return (f'<hp:tc><hp:subList><hp:p><hp:run charPrIDRef="1"><hp:t>{text}</hp:t></hp:run></hp:p></hp:subList>'
            f'<hp:cellAddr colAddr="{col}" rowAddr="{row}"/><hp:cellSpan colSpan="{colspan}" rowSpan="1"/></hp:tc>')


def test_track_form_dangjin_shape(tmp_path: Path):
    """11/04 당진 '전시분야 최근 3년간 사업수행실적' — 연번 '1'만 적힌 첫 줄도 채움, 백만원 단위, 비고(단독/공동),
    표 밖 '업 체 명 :' 문단, '계 | 총 실적 금액 : 백만원' 줄."""
    from quantdocs.fill import FillReport, _cells, _fill_label_paragraphs, _fill_tracks, _set_cell, _spans

    head = ["연번", "① 사업명", "사업기간", "② 계약금액 (단위 : 백만원)", "발주처", "③ 구체적 업무수행 내용", "비고 (단독/공동)"]
    rows = [[_hp_cell(i, 0, h) for i, h in enumerate(head)]]
    rows.append([_hp_cell(i, 1, "1" if i == 0 else "") for i in range(7)])
    rows.append([_hp_cell(i, 2, "") for i in range(7)])
    rows.append([_hp_cell(0, 3, "계"), _hp_cell(1, 3, "총 실적 금액 :          백만원", 6)])
    tbl = "<hp:tbl>" + "".join("<hp:tr>" + "".join(r) + "</hp:tr>" for r in rows) + "</hp:tbl>"
    xml = ('<hs:sec><hp:p><hp:run charPrIDRef="1"><hp:t>업 체 명 :</hp:t></hp:run><hp:linesegarray/></hp:p>'
           f'<hp:p><hp:run>{tbl}</hp:run></hp:p></hs:sec>')
    tracks = [{"사업명": "가상 전시물 제작 설치", "계약일": dt.date(2024, 1, 1), "준공일": dt.date(2024, 6, 30),
               "계약금액(원)": 123_400_000, "분야": "전시·체험"},
              {"사업명": "가상 체험관 조성", "준공일": dt.date(2025, 3, 1), "계약금액(원)": 50_000_000, "지분율(%)": 60}]
    rep = FillReport("x")
    edits = _fill_tracks(xml, _cells(xml, _spans(xml, "tbl")[0]), tracks, rep)
    for cell, lines, keep in sorted(edits, key=lambda e: e[0].start, reverse=True):
        xml = xml[:cell.start] + _set_cell(xml[cell.start:cell.end], lines, keep) + xml[cell.end:]

    class V:
        info = {"회사명": "(주)가상회사"}
    from quantdocs import fill as fill_mod
    orig = fill_mod._value
    fill_mod._value = lambda key, vault: vault.info.get(key, "")
    try:
        xml = _fill_label_paragraphs(xml, V(), rep)
    finally:
        fill_mod._value = orig
    assert rep.rows == 2
    for want in ("가상 전시물 제작 설치", "123.4", "2024.01.01~2024.06.30", "단독", "공동(60%)", ">2<",
                 "총 실적 금액 : 173.4 백만원", "업 체 명 : (주)가상회사"):
        assert want in xml, want
    assert "123,400,000" not in xml


def test_form_tracks_by_title(monkeypatch):
    from quantdocs import notice
    monkeypatch.setattr(notice, "track_field", lambda n: "전시·체험" if "전시" in n else "기타")
    ref = dt.date(2026, 11, 4)
    tracks = [{"사업명": "전시 A", "준공일": dt.date(2025, 1, 1)}, {"사업명": "전시 B", "준공일": dt.date(2022, 1, 1)},
              {"사업명": "창호 C", "준공일": dt.date(2025, 5, 1)}, {"사업명": "전시 D", "준공일": None},
              {"사업명": "전시 E", "준공일": dt.date(2024, 2, 1)}]
    got, notes = notice.form_tracks("전시분야 최근 3년간 사업수행실적", tracks, ref)
    assert [t["사업명"] for t in got] == ["전시 E", "전시 A"]  # 3년 밖·다른 분야 뺌, 오래된 것부터
    assert notes and "전시 D" in notes[0]


def test_track_rows_keep_largest_when_short():
    from quantdocs.fill import FillReport, _cells, _fill_tracks, _spans

    head = ["연번", "사업명", "계약금액"]
    rows = [[_hp_cell(i, 0, h) for i, h in enumerate(head)]] + [[_hp_cell(i, r, "") for i in range(3)] for r in (1, 2)]
    xml = "<hp:tbl>" + "".join("<hp:tr>" + "".join(r) + "</hp:tr>" for r in rows) + "</hp:tbl>"
    tracks = [{"사업명": n, "계약금액(원)": a} for n, a in (("옛 큰 것", 900), ("작은 것", 100), ("새 큰 것", 800))]
    rep = FillReport("x")
    names = [l[0] for c, l, _ in _fill_tracks(xml, _cells(xml, _spans(xml, "tbl")[0]), tracks, rep) if c.col == 1]
    assert names == ["옛 큰 것", "새 큰 것"] and rep.overflow == 1 and rep.overflow_names == ["작은 것"]


@needs_sample
def test_page_titles_by_font_size():
    """서식 이름 = 페이지에서 가장 큰 글씨(11/04 사용자 결정). 절취선·'정선군수 귀하'·날짜·공고명은 빼고, 줄이 넘어간 제목은 이음."""
    from quantdocs.forms_cut import page_titles

    t = page_titles(FIXTURES / "jeongseon_culture_center_rfp.hwpx", "공고문(정선군 복합문화센터 공간디자인 및 전시물 제작설치).hwpx")
    assert t[1] == "입찰참가 신청서" and t[3] == "공동수급체 합의각서" and t[6] == "청렴계약이행 서약서"
    assert t[13] == "사업수행능력평가자료(표지)" and t[14] == "일반현황 및 연혁"
    assert t[23] == "보안서약서" and t[25] == "가격제안입찰서" and t[31] == "제안서"
    assert 4 not in t  # 하위 서식(4-1·4-2)만 있는 번호는 뺌


def _sz_cell(col, row, text, w, h, run='<hp:run charPrIDRef="1">'):
    body = f"{run}<hp:t>{text}</hp:t></hp:run>" if text else run + "</hp:run>"
    return (f'<hp:tc><hp:subList><hp:p paraPrIDRef="0">{body}</hp:p></hp:subList>'
            f'<hp:cellAddr colAddr="{col}" rowAddr="{row}"/><hp:cellSpan colSpan="1" rowSpan="1"/>'
            f'<hp:cellSz width="{w}" height="{h}"/><hp:cellMargin left="141" right="141" top="141" bottom="141"/></hp:tc>')


def test_tall_track_row_is_split_and_text_fits():
    """11/04 필암서원 서식16 — 머리줄 아래 '계' 줄 + 세로로 긴 빈 칸 한 줄: 그 줄을 실적 수만큼 나눠 채우고(표 높이 그대로),
    '계' 줄은 넣은 뒤 합계. 칸보다 긴 글은 같은 글꼴에서 크기만 줄인 글자 모양을 머리 정보에 더해 씀."""
    from quantdocs.fill import FillReport, _Chars, _fit_char, _split_tall_rows, _cells, _fill_tracks, _spans

    rows = [[_sz_cell(0, 0, "연번", 2000, 3000), _sz_cell(1, 0, "사업명", 9000, 3000), _sz_cell(2, 0, "계약금액(천원)", 6000, 3000)],
            [_sz_cell(0, 1, "계", 2000, 3000), _sz_cell(1, 1, "", 9000, 3000), _sz_cell(2, 1, "", 6000, 3000)],
            [_sz_cell(0, 2, "", 2000, 30000), _sz_cell(1, 2, "", 9000, 30000), _sz_cell(2, 2, "", 6000, 30000)]]
    xml = '<hp:tbl rowCnt="3" colCnt="3">' + "".join("<hp:tr>" + "".join(r) + "</hp:tr>" for r in rows) + "</hp:tbl>"
    tracks = [{"사업명": f"가상 사업 {i}", "계약금액(원)": 10_000_000 * (i + 1)} for i in range(3)]
    xml = _split_tall_rows(xml, _spans(xml, "tbl")[0], len(tracks))
    assert 'rowCnt="5"' in xml
    cells = _cells(xml, _spans(xml, "tbl")[0])
    assert sum(int(re.search(r'height="(\d+)"', xml[c.start:c.end][xml[c.start:c.end].find("cellSz"):]).group(1))
               for c in cells if c.col == 0) == 3000 + 3000 + 30000  # 표 높이 그대로
    rep = FillReport("x")
    vals = {(c.row, c.col): l[0] for c, l, _ in _fill_tracks(xml, cells, tracks, rep)}
    assert rep.rows == 3 and vals[(2, 1)] == "가상 사업 0" and vals[(4, 2)] == "30,000" and vals[(1, 2)] == "60,000"

    header = ('<hh:charProperties itemCnt="2"><hh:charPr id="0" height="1000"><hh:ratio hangul="100"/></hh:charPr>'
              '<hh:charPr id="1" height="1300"><hh:ratio hangul="100"/></hh:charPr></hh:charProperties>')
    chars = _Chars(header)
    cid, note = _fit_char(_sz_cell(1, 0, "", 6000, 1600), "강원특별자치도 정선군 남면 무릉2로 64(공장1층)", chars)
    assert cid == "2" and "줄여" in note
    out = chars.finish()
    assert 'itemCnt="3"' in out and re.search(r'<hh:charPr id="2" height="(\d+)"', out)
    assert int(re.search(r'<hh:charPr id="2" height="(\d+)"', out).group(1)) < 1300


def test_qualification_rows_merge_docs_and_name_only(tmp_path: Path, monkeypatch):
    """11/04 필암서원: 공고문·제안요청서의 '또는' 묶음을 하나로, 이름만 적힌 중소기업확인서도 한 줄, 통합본에 코드 서류 그대로."""
    from nego import attachments
    from quantdocs import g2b
    from quantdocs.bundle import pick

    texts = {
        "공고문.hwpx": "3. 입찰참가자격\n가. 아래의 자격을 모두 갖춘 자\n"
                      "1) 정보통신공사업(업종코드 0036) 입찰참가자격으로 등록한 업체\n"
                      "2) 산업디자인전문회사(환경디자인 : 업종코드 4442 또는 종합디자인 : 업종코드 4444) 입찰참가자격으로 등록한 업체\n"
                      "3) 실물모형및전시물(6010989901)을 등록하고 직접생산확인증명서(세부품명번호 6010989901)를 소지한 업체\n"
                      "4) 「중소기업기본법」에 따른 중소기업자로서 중소기업·소상공인확인서를 소지한 업체\n"
                      "4. 입찰방법\n가. 전자입찰",
        "제안요청서.hwpx": "1. 입찰참가자격\n가. 아래의 자격을 모두 갖춘 자\n"
                         "1) 산업디자인전문회사(환경디자인 : 업종코드 4442 또는 종합디자인 : 업종코드 4444) 또는 "
                         "공공디자인전문회사 (업종코드 6484) 입찰참가자격으로 등록한 업체\n2. 과업 내용\n가. 기획",
    }
    paths = []
    for name in texts:
        (tmp_path / name).write_bytes(name.encode())
        paths.append(tmp_path / name)
    monkeypatch.setattr(attachments, "extract_text", lambda data, ext: texts[data.decode()])
    v = vault.Vault({"files/정보통신공사업등록증.pdf": b"", "files/중소기업확인서(~27.3.31).pdf": b"", "files/직접생산_실물.pdf": b""},
                    docs=[{"서류 이름": "직접생산확인증명서", "파일 이름": "직접생산_실물.pdf", "업종코드·세부품명번호": "6010989901"},
                          {"서류 이름": "정보통신공사업 등록증", "파일 이름": "정보통신공사업등록증.pdf", "업종코드·세부품명번호": "0036"}])
    rows = g2b.qualification_rows(paths, v.held_codes(), v, ["정보통신공사업", "엔지니어링사업"])
    kinds = [(r["kind"], [i["code"] or i["name"] for i in r["items"]]) for r in rows]
    assert ("이 중 하나", ["4442", "4444", "6484"]) in kinds
    assert sum(1 for k in kinds if "4442" in k[1]) == 1  # 문서별로 두 줄 나오지 않음
    assert ("필수", ["0036"]) in kinds and ("필수", ["6010989901"]) in kinds
    assert ("필수 서류", ["중소기업·소상공인 확인서"]) in kinds
    assert ("나라장터 면허제한", ["엔지니어링사업"]) in [(k, [n.split("(")[0] for n in c]) for k, c in kinds]
    assert not any(k == "나라장터 면허제한" and "0036" in c for k, c in kinds)  # 첨부에 있는 업종은 다시 안 넣음
    prod = next(i for r in rows for i in r["items"] if i["code"] == "6010989901")
    assert prod["need"] == "직접생산확인증명서" and prod["paths"] == ["files/직접생산_실물.pdf"]
    sme = next(i for r in rows for i in r["items"] if i["name"].startswith("중소기업"))
    assert sme["held"] and sme["paths"] == ["files/중소기업확인서(~27.3.31).pdf"]
    picks, _ = pick(list(v.files), [], rows, dt.date(2026, 11, 4))
    got = [p.path for p in picks]
    assert "files/직접생산_실물.pdf" in got and "files/중소기업확인서(~27.3.31).pdf" in got and "files/정보통신공사업등록증.pdf" in got


def test_bundle_pick_one_of_group():
    """'이 중 하나' 묶음은 서류가 있는 첫 자격 하나만 통합본에(11/04)."""
    from quantdocs.bundle import pick

    files = ["files/환경디자인.pdf", "files/종합디자인.pdf", "files/공공디자인.pdf"]
    quals = [{"kind": "이 중 하나", "items": [
        {"name": "산업디자인전문회사(환경디자인분야)", "code": "4442", "held": True, "paths": []},
        {"name": "산업디자인전문회사(종합디자인분야)", "code": "4444", "held": True, "paths": ["files/종합디자인.pdf"]},
        {"name": "공공디자인 전문회사", "code": "6484", "held": True, "paths": ["files/공공디자인.pdf"]}]}]
    picks, missing = pick(files, [], quals, dt.date(2026, 11, 4))
    assert [p.path for p in picks] == ["files/종합디자인.pdf"] and not missing
    picks, missing = pick([], [], quals, dt.date(2026, 11, 4))
    assert not picks and len(missing) == 1 and " 또는 " in missing[0]


def test_fit_one_line_when_line_spacing_below_100():
    """11/04 필암서원 주소 칸 — 줄 간격 50% 문단에 12pt 주소가 두 줄로 넘어가 글이 겹쳤음 → 한 줄에 들어가게 글자를 줄임."""
    from quantdocs.fill import _Chars, _fit_char, _text_width

    addr = "강원특별자치도 정선군 남면 무릉2로 64(공장1층)"
    header = ('<hh:charProperties itemCnt="2"><hh:charPr id="0" height="1000"><hh:ratio hangul="100"/></hh:charPr>'
              '<hh:charPr id="1" height="1200"><hh:ratio hangul="100"/></hh:charPr></hh:charProperties>'
              '<hh:paraProperties itemCnt="2"><hh:paraPr id="0"><hh:lineSpacing type="PERCENT" value="50" unit="HWPUNIT"/></hh:paraPr>'
              '<hh:paraPr id="1"><hh:lineSpacing type="PERCENT" value="160" unit="HWPUNIT"/></hh:paraPr></hh:paraProperties>')
    chars = _Chars(header)
    cid, note = _fit_char(_sz_cell(1, 0, "", 19477, 2397), addr, chars)  # 실제 칸 크기
    assert cid and "줄여" in note
    h = int(re.search(rf'<hh:charPr id="{cid}" height="(\d+)"', chars.finish()).group(1))
    assert _text_width(addr, h, 1.0) <= (19477 - 282) * 0.95  # 한 줄에 들어감
    # 줄 간격 160%인 칸은 예전처럼 두 줄로 넘어가도 됨(높이가 되면 글자 그대로)
    tall = _sz_cell(1, 0, "", 19477, 4500).replace('paraPrIDRef="0"', 'paraPrIDRef="1"')
    assert _fit_char(tall, addr, chars) == (None, "")


def test_unfinished_tracks_are_never_counted(monkeypatch):
    """11/04 사용자 확인: 수행실적은 준공된 것만 — 기간 요건이 없는 서식·공고여도 진행 중(준공일이 기준일 뒤)·준공일 없는 실적은 뺌."""
    from quantdocs import notice
    from quantdocs.notice import TrackReq, track_candidates, unfinished

    monkeypatch.setattr(notice, "track_field", lambda n: "전시·체험")
    ref = dt.date(2026, 11, 4)
    tracks = [{"사업명": "끝난 것", "준공일": dt.date(2026, 3, 1), "계약금액(원)": 3e8},
              {"사업명": "진행 중", "준공일": dt.date(2027, 2, 28), "계약금액(원)": 5e8},
              {"사업명": "날짜 없음", "준공일": None, "계약금액(원)": 2e8}]
    got, notes = notice.form_tracks("전시분야 사업수행실적", tracks, ref)  # 제목에 기간 없음
    assert [t["사업명"] for t in got] == ["끝난 것"]
    assert any("진행 중" in n and "준공 전" in n for n in notes) and any("날짜 없음" in n for n in notes)
    assert [t["사업명"] for t in track_candidates(TrackReq("", "", None, None, False), tracks, ref)] == ["끝난 것"]
    assert unfinished(tracks, ref) == ["진행 중", "날짜 없음"]


def test_filled_digits_use_hangul_font():
    """11/04 제보: 서식 글자 모양이 한글 휴먼명조·영문 HCI Poppy라 채운 사업자번호·주소의 숫자 글꼴이 달라 보임
    → 채운 글은 숫자·영문도 한글과 같은 글꼴(문서 글꼴 목록에 있는 것만 — 새로 더하지 않음)."""
    from quantdocs.fill import _Chars

    header = ('<hh:fontfaces><hh:fontface lang="HANGUL" fontCnt="1"><hh:font id="0" face="휴먼명조" type="TTF"/></hh:fontface>'
              '<hh:fontface lang="LATIN" fontCnt="2"><hh:font id="0" face="HCI Poppy" type="TTF"/><hh:font id="1" face="휴먼명조" type="TTF"/></hh:fontface>'
              '<hh:fontface lang="SYMBOL" fontCnt="1"><hh:font id="0" face="HCI Poppy" type="TTF"/></hh:fontface></hh:fontfaces>'
              '<hh:charProperties itemCnt="2"><hh:charPr id="0" height="1300"><hh:fontRef hangul="0" latin="0" hanja="0" symbol="0"/>'
              '<hh:ratio hangul="90"/></hh:charPr>'
              '<hh:charPr id="1" height="1000"><hh:fontRef hangul="0" latin="1" symbol="0"/></hh:charPr></hh:charProperties>')
    chars = _Chars(header)
    new = chars.variant("0")
    assert new == "2" and chars.variant("1") == "1"  # 1번은 이미 같은 글꼴(기호 목록엔 휴먼명조가 없어 그대로)
    small = chars.variant("0", 900)
    out = chars.finish()
    assert re.search(r'<hh:charPr id="2" height="1300"><hh:fontRef hangul="0" latin="1" hanja="0" symbol="0"/>', out)
    assert re.search(rf'<hh:charPr id="{small}" height="900"><hh:fontRef hangul="0" latin="1"', out)
    assert 'itemCnt="4"' in out and out.count("<hh:font ") == 4  # 글꼴 목록은 그대로


def test_read_history_formats():
    from quantdocs.importer import read_history

    text = ("회사 연혁\n2005. 03. 15 (주)가상 법인 설립\n2010년 7월 실내건축공사업 등록\n- 2012.11 산업디자인전문회사 신고\n"
            "2018\n03. 직접생산확인 취득\n11월 공장 이전\n주요 실적은 별첨\n2099.13 잘못된 달")
    got = read_history(text)
    assert [h["내용"] for h in got] == ["(주)가상 법인 설립", "실내건축공사업 등록", "산업디자인전문회사 신고", "직접생산확인 취득", "공장 이전"]
    assert got[0]["연월일"] == dt.date(2005, 3, 15) and got[1]["연월일"] == "2010.07" and got[3]["연월일"] == "2018.03"


def test_enrich_vault_from_pdfs_and_fill_bracketed_history_label(tmp_path: Path, monkeypatch):
    """11/04 제보: 연혁 칸 '〈 주요연혁(요약) 〉'이 양식만 보임 — 이름표가 괄호 때문에 8자를 넘어 건너뜀 + 연혁·전화번호를
    회사 서류 PDF(연혁·경쟁입찰참가자격등록증)에서 읽어 채움. 회사정보 시트 값이 먼저, 스캔본은 알림."""
    from quantdocs import importer
    from quantdocs.fill import fill_hwpx

    texts = {b"hist": "2005.03.15 법인 설립\n2010.07 실내건축공사업 등록\n2019.05 본사 이전",
             b"reg": "경쟁입찰참가자격등록증\n상호 (주)가상\n전 화 번 호 033-123-4567\n팩 스 번 호 033-123-4568",
             b"scan": ""}
    monkeypatch.setattr(importer, "_bytes_pdf_text", lambda data: texts[data])
    v = vault.Vault({"files/회사연혁.pdf": b"hist", "files/경쟁입찰참가자격등록증.pdf": b"reg", "files/입찰참가 스캔.pdf": b"scan",
                     "files/책상.pdf": b"scan"},
                    info={"회사명": "(주)가상", "팩스번호": "02-000-0000"},
                    history=[{"연월일": dt.date(2005, 3, 15), "내용": "법인 설립"}])
    notes = importer.enrich_vault(v)
    assert v.info["전화번호"] == "033-123-4567" and v.info["팩스번호"] == "02-000-0000"  # 시트 값이 먼저
    assert len(v.history) == 3 and any("연혁 3줄" in n for n in notes)
    assert any("입찰참가 스캔.pdf" in n for n in notes) and not any("책상" in n for n in notes)

    cell = lambda col, row, text, span=1: (
        f'<hp:tc><hp:subList><hp:p paraPrIDRef="0"><hp:run charPrIDRef="0"><hp:t>{text}</hp:t></hp:run></hp:p></hp:subList>'
        f'<hp:cellAddr colAddr="{col}" rowAddr="{row}"/><hp:cellSpan colSpan="{span}" rowSpan="1"/></hp:tc>')
    tbl = ('<hp:tbl rowCnt="2" colCnt="2"><hp:tr>' + cell(0, 0, "전 화 번 호") + cell(1, 0, "") + "</hp:tr><hp:tr>"
           + cell(0, 1, "〈 주요연혁(요약) 〉", 2) + "</hp:tr></hp:tbl>")
    src = tmp_path / "f.hwpx"
    with zipfile.ZipFile(src, "w") as z:
        z.writestr("mimetype", "application/hwp+zip")
        z.writestr("Contents/section0.xml", f'<hs:sec><hp:p><hp:run>{tbl}</hp:run></hp:p></hs:sec>')
    rep = fill_hwpx(src, tmp_path / "o.hwpx", v)
    xml = zipfile.ZipFile(tmp_path / "o.hwpx").read("Contents/section0.xml").decode()
    assert "033-123-4567" in xml and "2010.07  실내건축공사업 등록" in xml and "〈 주요연혁(요약) 〉" in xml
    assert any(f.startswith("연혁 → 3줄") for f in rep.filled)


def _mini_hwpx(path: Path, body: str) -> Path:
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/hwp+zip")
        z.writestr("Contents/section0.xml", f"<hs:sec>{body}</hs:sec>")
    return path


def _c(col, row, text, span=1):
    return (f'<hp:tc><hp:subList><hp:p paraPrIDRef="0"><hp:run charPrIDRef="0"><hp:t>{text}</hp:t></hp:run></hp:p></hp:subList>'
            f'<hp:cellAddr colAddr="{col}" rowAddr="{row}"/><hp:cellSpan colSpan="{span}" rowSpan="1"/></hp:tc>')


def _tbl(rows):
    return "<hp:p><hp:run><hp:tbl>" + "".join("<hp:tr>" + "".join(r) + "</hp:tr>" for r in rows) + "</hp:tbl></hp:run></hp:p>"


def test_proxy_form_phone_fax_rrn_and_none_rule(tmp_path: Path):
    """11/04 제보(위임장·참가신청서·공동수급 대표자 선임계·행정처분내용): 전화·팩스(시트 항목 이름이 달라도), '사무실' 칸,
    대표자 줄 주민등록번호는 뒷자리 '*', 대리인 줄·휴대폰 칸은 비움, '해당 없음' 안내가 있는 빈 표엔 '해당 없음'."""
    from quantdocs.fill import fill_hwpx

    v = vault.Vault({}, info={"회사명": "(주)가상", "대표자": "홍길동", "대표전화": "033-111-2222", "FAX": "033-111-3333",
                              "대표자 주민등록번호": "800101-1234567"})
    proxy = _tbl([
        [_c(0, 0, "대 표 자"), _c(1, 0, "홍길동"), _c(2, 0, "주민등록번호"), _c(3, 0, "")],
        [_c(0, 1, "전 화 번 호"), _c(1, 1, "")],
        [_c(0, 2, "성 명"), _c(1, 2, ""), _c(2, 2, "주민등록번호"), _c(3, 2, "")],
        [_c(0, 3, "전 화 번 호(휴대폰번호)"), _c(1, 3, "")],
        [_c(0, 4, "사무실"), _c(1, 4, ""), _c(2, 4, "FAX"), _c(3, 4, "")]])
    rep = fill_hwpx(_mini_hwpx(tmp_path / "p.hwpx", proxy), tmp_path / "po.hwpx", v)
    xml = zipfile.ZipFile(tmp_path / "po.hwpx").read("Contents/section0.xml").decode()
    assert xml.count("800101-*******") == 1 and "1234567" not in xml  # 대표자 줄만, 뒷자리 없음
    assert xml.count("033-111-2222") == 2 and xml.count("033-111-3333") == 1  # 신청인 전화·사무실 / FAX, 휴대폰 칸은 비움
    assert any("주민등록번호" in f for f in rep.filled)

    sanction = ('<hp:p><hp:run charPrIDRef="0"><hp:t>※ 해당사항이 없을 경우에는 “해당 없음”을 기재</hp:t></hp:run></hp:p>'
                + _tbl([[_c(i, 0, h) for i, h in enumerate(["업 체 명", "용 역 명", "지정기관"])]]
                       + [[_c(i, r, "") for i in range(3)] for r in (1, 2)]))
    fill_hwpx(_mini_hwpx(tmp_path / "s.hwpx", sanction), tmp_path / "so.hwpx", v)
    xml = zipfile.ZipFile(tmp_path / "so.hwpx").read("Contents/section0.xml").decode()
    assert xml.count("해당 없음") == 2  # 안내 문구 + 첫 칸
    # 안내가 없는 서식의 빈 표는 그대로
    fill_hwpx(_mini_hwpx(tmp_path / "n.hwpx", sanction.split("</hp:p>", 1)[1]), tmp_path / "no.hwpx", v)
    assert "해당 없음" not in zipfile.ZipFile(tmp_path / "no.hwpx").read("Contents/section0.xml").decode()


def test_enrich_ocr_scanned_and_masked_rrn(monkeypatch):
    from quantdocs import importer

    monkeypatch.setattr(importer, "_bytes_pdf_text", lambda data: "")  # 모두 스캔본
    ocr = {b"seal": "법인인감증명서\n상 호 : 주식회사 가상\n대표이사 홍길동\n(800101-1234567)",
           b"hist": "사업연혁\n2005.03.15 법인 설립\n2012.11 산업디자인전문회사 신고"}
    monkeypatch.setattr(importer, "_ocr_pdf", lambda data: ocr[data])
    v = vault.Vault({"files/인감증명서.pdf": b"seal", "files/사업연혁.pdf": b"hist"})
    notes = importer.enrich_vault(v)
    assert v.info["대표자 주민등록번호"] == "800101-*******"  # 뒷자리는 남기지 않음
    assert [h["내용"] for h in v.history] == ["법인 설립", "산업디자인전문회사 신고"]
    assert any("OCR" in n for n in notes)
    monkeypatch.setattr(importer, "_ocr_pdf", lambda data: None)  # 도구 없음
    notes = importer.enrich_vault(vault.Vault({"files/사업연혁.pdf": b"hist"}))
    assert any("Tesseract" in n for n in notes)
