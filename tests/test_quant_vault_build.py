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
              "재직증명서", "실적증명서"):
        assert is_manual(n), n
    # 자기평가 심사표는 회사 정보 칸만 채움(점수 칸은 비움, 11/04 서식 24)
    for n in ("입찰참가신청서", "일반현황 및 연혁", "사업수행실적 집계표", "위임장", "청렴계약이행서약서", "사업수행능력평가 자기평가표"):
        assert not is_manual(n), n


def _hp_cell(col, row, text, colspan=1):
    return (f'<hp:tc><hp:subList><hp:p><hp:run charPrIDRef="1"><hp:t>{text}</hp:t></hp:run></hp:p></hp:subList>'
            f'<hp:cellAddr colAddr="{col}" rowAddr="{row}"/><hp:cellSpan colSpan="{colspan}" rowSpan="1"/></hp:tc>')


def test_track_form_dangjin_shape(tmp_path: Path):
    """11/04 당진 '전시분야 최근 3년간 사업수행실적' — 연번 '1'만 적힌 첫 줄도 채움, 백만원 단위, 비고(단독/공동),
    표 밖 '업 체 명 :' 문단, '계 | 총 실적 금액 : 백만원' 줄."""
    from quantdocs.fill import FillReport, _apply_marks, _cells, _fill_label_paragraphs, _fill_tracks, _set_cell, _spans

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
        xml = _apply_marks(_fill_label_paragraphs(xml, V(), rep), None)
    finally:
        fill_mod._value = orig
    assert rep.rows == 2
    # 11/05 사용자 요청: 금액은 머리글 단위(백만원)와 상관없이 원 단위 전체 — 단위는 담당자가 고침
    for want in ("가상 전시물 제작 설치", "123,400,000", "2024.01.01~2024.06.30", "단독", "공동(60%)", ">2<",
                 "총 실적 금액 : 173,400,000 원", "업 체 명 : (주)가상회사"):
        assert want in xml, want


def test_form_tracks_by_title(monkeypatch):
    from quantdocs import notice
    monkeypatch.setattr(notice, "track_field", lambda n: "전시·체험" if "전시" in n else "기타")
    ref = dt.date(2026, 11, 4)
    tracks = [{"사업명": "전시 A", "준공일": dt.date(2025, 1, 1)}, {"사업명": "전시 B", "준공일": dt.date(2022, 1, 1)},
              {"사업명": "창호 C", "준공일": dt.date(2025, 5, 1)}, {"사업명": "전시 D", "준공일": None},
              {"사업명": "전시 E", "준공일": dt.date(2024, 2, 1)}]
    got, notes = notice.form_tracks("전시분야 최근 3년간 사업수행실적", tracks, ref)
    # 11/05 사용자 요청: 기간 조건만 — 분야로 거르지 않음(창호 C도 넣음), 3년 밖(B)은 뺌, 오래된 것부터
    assert [t["사업명"] for t in got] == ["전시 E", "전시 A", "창호 C"]
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
    assert rep.rows == 3 and vals[(2, 1)] == "가상 사업 0" and vals[(4, 2)] == "30,000,000" and vals[(1, 2)] == "60,000,000원"

    header = ('<hh:charProperties itemCnt="2"><hh:charPr id="0" height="1000"><hh:ratio hangul="100"/></hh:charPr>'
              '<hh:charPr id="1" height="1300"><hh:ratio hangul="100"/></hh:charPr></hh:charProperties>')
    chars = _Chars(header)
    # 11/05 사용자 결정: 채운 글은 11pt — 줄 간격이 보통인 칸은 넘쳐도 줄이지 않고 두 줄로
    cid, note = _fit_char(_sz_cell(1, 0, "", 6000, 1600), "강원특별자치도 정선군 남면 무릉2로 64(공장1층)", chars)
    assert cid == "2" and note == ""
    out = chars.finish()
    assert 'itemCnt="3"' in out and re.search(r'<hh:charPr id="2" height="1100"', out)


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
    """11/04 필암서원 주소 칸 — 줄 간격 50% 문단에 12pt 주소가 두 줄로 넘어가 글이 겹쳤음 → 한 줄에 들어가게 글자를 줄임.
    (11/05 채운 글 11pt 결정 뒤에도 이런 칸만 예외로 줄임)"""
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
    assert h < 1100
    # 줄 간격 160%인 칸은 11pt 그대로 두 줄로(칸 높이가 모자라도 줄이지 않음 — 11/05 사용자 결정)
    tall = _sz_cell(1, 0, "", 19477, 1500).replace('paraPrIDRef="0"', 'paraPrIDRef="1"')
    cid2, note2 = _fit_char(tall, addr, chars)
    assert note2 == "" and re.search(rf'<hh:charPr id="{cid2}" height="1100"', chars.finish())


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
    assert new == "2" and chars.variant("1") == "3"  # 11/11: 기호 목록에도 휴먼명조를 더해 기호까지 휴먼명조
    small = chars.variant("0", 900)
    out = chars.finish()
    assert re.search(r'<hh:charPr id="2" height="1300"><hh:fontRef hangul="0" latin="1" hanja="0" symbol="1"/>', out)
    assert re.search(rf'<hh:charPr id="{small}" height="900"><hh:fontRef hangul="0" latin="1"', out)
    assert 'itemCnt="5"' in out and out.count("<hh:font ") == 5  # 기호 목록에만 휴먼명조 하나 더함(이미 있는 언어는 그대로)


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
    monkeypatch.setattr(importer, "_TEXT_CACHE", {})
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

    monkeypatch.setattr(importer, "_TEXT_CACHE", {})
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


def test_enrich_recognizes_registration_by_content(monkeypatch):
    """11/04 제보: 등록증 PDF에 전화·팩스·대표자 주민번호(뒷자리 가림)가 다 적혀 있는데 못 읽음 — 파일 이름에 '참가자격'이 없어서.
    글 내용('경쟁입찰참가자격등록증')으로 서류 종류를 알아봄. OCR 필요 없음(글이 있는 PDF)."""
    from quantdocs import importer

    text = ("1 / 3\n경쟁입찰참가자격등록증\n한글상호 주식회사 가상\n전 화 번 호 031-000-1111 팩 스 번 호 02-000-2222\n"
            "대표자 정보\n성명 주민등록번호 E - Mail 대표자휴대폰 대표\n대표자여부\n대표자구분\n공동 각자\n홍길동 800101-1******")
    monkeypatch.setattr(importer, "_TEXT_CACHE", {})
    monkeypatch.setattr(importer, "_bytes_pdf_text", lambda data: text if data == b"reg" else "견적서 내용")
    monkeypatch.setattr(importer, "_ocr_pdf", lambda data: (_ for _ in ()).throw(AssertionError("OCR 쓰면 안 됨")))
    v = vault.Vault({"files/나라장터 출력.pdf": b"reg", "files/견적.pdf": b"etc"})
    importer.enrich_vault(v)
    assert v.info["전화번호"] == "031-000-1111" and v.info["팩스번호"] == "02-000-2222"
    assert v.info["대표자 주민등록번호"] == "800101-*******"


def test_inline_tel_fax_and_seal_placeholders(tmp_path: Path):
    """11/04 제보(제안 제출서·참가신청서): 값 칸에 'TEL :      FAX :'·'(인)'이 미리 적혀 있어 '빈 칸 아님'으로 건너뜀
    → 빈자리에 전화·팩스를 넣고, '(인)' 앞에 대표자·회사명(대표성명)을 씀."""
    from quantdocs.fill import fill_hwpx

    v = vault.Vault({}, info={"회사명": "(주)가상", "대표자": "홍길동", "전화번호": "033-111-2222", "팩스번호": "033-111-3333"})
    body = _tbl([
        [_c(0, 0, "대 표 자"), _c(1, 0, "                 (인)")],
        [_c(0, 1, "전화번호"), _c(1, 1, "TEL :              FAX : ")],
        [_c(0, 2, "상호·법인명(대표성명)"), _c(1, 2, "(인)"), _c(2, 2, "(인)")],
        [_c(0, 3, "비 고"), _c(1, 3, "기타 : 없음")]])
    rep = fill_hwpx(_mini_hwpx(tmp_path / "f.hwpx", body), tmp_path / "o.hwpx", v)
    xml = zipfile.ZipFile(tmp_path / "o.hwpx").read("Contents/section0.xml").decode()
    assert re.search(r"TEL : 033-111-2222 +FAX : 033-111-3333", xml)
    assert "홍길동   (인)" in xml and "(주)가상 홍길동   (인)" in xml
    assert xml.count("(인)") == 3  # 공동참여자 칸 '(인)'은 그대로
    assert "기타 : 없음" in xml and any("TEL" in f for f in rep.filled)


def test_signature_lines_inside_body_cell_and_rrn_not_corporate(tmp_path: Path):
    """11/04 제보(서식 2·8): 서식 본문 칸 안 '대표자 :      (인)' 줄(글 안 탭 포함)도 채움, 한 줄짜리 이름표 칸엔 안 씀.
    대표자 주민번호에 법인등록번호(110111-…)를 잡지 않음."""
    from quantdocs.fill import fill_hwpx
    from quantdocs.importer import masked_rrn

    assert masked_rrn("대 표 자 : 홍길동\n법인등록번호 : 110111-4182971") == ""
    v = vault.Vault({}, info={"회사명": "(주)가상", "대표자": "홍길동"})
    body_cell = ('<hp:tc><hp:subList><hp:p><hp:run charPrIDRef="0"><hp:t>위와 같이 위임합니다.</hp:t></hp:run></hp:p>'
                 '<hp:p><hp:run charPrIDRef="0"><hp:t><hp:tab width="4000"/>대표자 :                (인)</hp:t></hp:run></hp:p></hp:subList>'
                 '<hp:cellAddr colAddr="0" rowAddr="1"/><hp:cellSpan colSpan="2" rowSpan="1"/></hp:tc>')
    xml = ("<hp:p><hp:run><hp:tbl><hp:tr>" + _c(0, 0, "업체명 :") + _c(1, 0, "") + "</hp:tr><hp:tr>" + body_cell
           + "</hp:tr></hp:tbl></hp:run></hp:p>")
    fill_hwpx(_mini_hwpx(tmp_path / "s.hwpx", xml), tmp_path / "so.hwpx", v)
    out = zipfile.ZipFile(tmp_path / "so.hwpx").read("Contents/section0.xml").decode()
    assert re.search(r"대표자 : 홍길동 +\(인\)", out)
    assert out.count("(주)가상") == 1  # 이름표 칸 '업체명 :'은 그대로, 옆 칸에만


def test_enrich_warns_when_phone_or_rrn_missing(monkeypatch):
    """11/04 제보: 서버에서 전화번호·주민번호 칸이 계속 비어도 이유가 안 보임 → 못 찾으면 이유·할 일을 알림, 점검 보고(report)도 줌."""
    from quantdocs import importer

    monkeypatch.setattr(importer, "_TEXT_CACHE", {})
    monkeypatch.setattr(importer, "_bytes_pdf_text", lambda data: "" if data == b"scan" else "견적서 — 전시물 제작 설치 비용 내역 합계 금액 확인")
    v = vault.Vault({"files/스캔1.pdf": b"scan", "files/견적.pdf": b"etc"}, info={"회사명": "(주)가상"})
    rows: list = []
    notes = importer.enrich_vault(v, rows)
    assert any("전화번호를 찾지 못해" in n and "등록증으로 알아본 PDF가 없음" in n and "스캔본 1개" in n for n in notes)
    assert any("주민등록번호" in n for n in notes)
    assert ("스캔1.pdf", "스캔본(이름으로 종류를 몰라 안 읽음)", "") in rows
    v2 = vault.Vault({}, info={"대표전화": "02-1", "대표자 주민등록번호": "800101-*******"})
    assert not any("⚠" in n for n in importer.enrich_vault(v2))


def test_loose_pdf_in_quant_docs_folder(tmp_path: Path, monkeypatch):
    """11/04 사용자 요청: 묶음에 안 넣은 jiil-quant-docs 폴더의 '경쟁입찰참가등록증.pdf'도 읽음 — 서식 채우기·통합본 둘 다.
    같은 이름이 묶음에 있으면 묶음 것, 하위 폴더는 안 봄."""
    from quantdocs import importer
    from quantdocs.bundle import pick

    loose = tmp_path / "jiil-quant-docs"
    (loose / "vault").mkdir(parents=True)
    (loose / "경쟁입찰참가등록증.pdf").write_bytes(b"reg")
    (loose / "사업자등록증.pdf").write_bytes(b"other")
    (loose / "vault" / "안쪽.pdf").write_bytes(b"x")
    v = vault.Vault({"files/사업자등록증.pdf": b"inner"})
    vault.add_loose(v, loose)
    assert v.loose == ["경쟁입찰참가등록증.pdf"] and v.files["files/사업자등록증.pdf"] == b"inner"
    monkeypatch.setattr(importer, "_TEXT_CACHE", {})
    monkeypatch.setattr(importer, "_bytes_pdf_text", lambda data: "경쟁입찰참가자격등록증\n전 화 번 호 031-000-1111 팩 스 번 호 02-000-2222"
                        if data == b"reg" else "사업자등록증 내용이 들어 있는 문서입니다 확인용")
    notes = importer.enrich_vault(v)
    assert v.info["전화번호"] == "031-000-1111" and any("묶음 밖 PDF" in n for n in notes)
    picks, _ = pick(list(v.files), ["입찰참가자격 등록증"], [], dt.date(2026, 11, 4))
    assert "files/경쟁입찰참가등록증.pdf" in [p.path for p in picks]


def test_fill_tagged_labels_bare_lines_title_and_biz_kind(tmp_path: Path):
    """11/04 필암서원 비교: '상호<fwSpace/>또는 법인명' 이름표, '대표사 업체명'·'생년월일(사업자등록번호)', 줄 전체 칸 '◦대표자 :',
    '대표이사 :  (인)'·'신청인(대표자) :  (인 또는 서명)', 쌍점 없는 '주  소 / 상  호 / 대표자' 줄, 입찰명, 업태·종목."""
    from quantdocs.fill import fill_hwpx
    from quantdocs.importer import read_bizreg

    biz = read_bizreg("사 업 의 종 류 :\n:\n업태 사업시설 관리, 사업지원 및 임대 서\n비스업\n제조\n"
                      "종목 전시, 컨벤션 및 행사 대행업\n실물모형제작\n(별지 출력)발 급 사 유 : 정정")
    assert biz["업태"] == "사업시설 관리, 사업지원 및 임대 서비스업" and biz["종목"] == "전시, 컨벤션 및 행사 대행업"
    v = vault.Vault({}, info={"회사명": "(주)가상", "대표자": "홍길동", "주소": "강원 정선", "사업자등록번호": "123-45-67890",
                              "업태": "서비스업", "종목": "행사대행"})

    def para(t):
        return f'<hp:p><hp:run charPrIDRef="0"><hp:t>{t}</hp:t></hp:run></hp:p>'
    tbl = ("<hp:p><hp:run><hp:tbl><hp:tr>" + _c(0, 0, "상호<hp:fwSpace/>또는<hp:fwSpace/>법인명") + _c(1, 0, "")
           + _c(2, 0, "대표사 업체명") + _c(3, 0, "") + "</hp:tr><hp:tr>" + _c(0, 1, "생년월일(사업자등록번호)") + _c(1, 1, "")
           + _c(2, 1, "사업분야") + _c(3, 1, " 업태 :          종목 : ") + "</hp:tr><hp:tr>" + _c(0, 2, "입 찰 건 명") + _c(1, 2, "")
           + "</hp:tr></hp:tbl></hp:run></hp:p>")
    cover = "<hp:p><hp:run><hp:tbl><hp:tr>" + _c(0, 0, "◦대표자 : ") + "</hp:tr></hp:tbl></hp:run></hp:p>"
    body = (tbl + cover + para("대표이사 :            (인)") + para("신청인(대표자) :          (인 또는 서명)")
            + para("   주  소   ") + para("   상  호   ") + para("   대표자 ") + para("귀하") + para("주소"))
    fill_hwpx(_mini_hwpx(tmp_path / "t.hwpx", body), tmp_path / "to.hwpx", v, title="가상 전시 제작")
    out = zipfile.ZipFile(tmp_path / "to.hwpx").read("Contents/section0.xml").decode()
    assert out.count("(주)가상") == 3  # 상호 칸 · 대표사 업체명 칸 · 상  호 줄
    assert "123-45-67890" in out and "가상 전시 제작" in out
    assert "업태 : 서비스업" in out and "종목 : 행사대행" in out
    assert "◦대표자 : 홍길동" in out
    assert re.search(r"대표이사 : 홍길동 +\(인\)", out) and re.search(r"신청인\(대표자\) : 홍길동 +\(인 또는 서명\)", out)
    assert "주  소   강원 정선" in out and "대표자   홍길동" in out
    assert "<hp:t>주소</hp:t>" in out  # 혼자 있는 '주소' 줄(제목일 수 있음)은 그대로


def test_number_cells_follow_centered_sibling(tmp_path: Path):
    """11/04 사용자 요청(서식 1): 같은 열 번호 칸 중 FAX만 가운데 정렬 → 법인등록번호·전화도 가운데로. 주소(글)는 그대로."""
    from quantdocs.fill import fill_hwpx

    header = ('<hh:head><hh:charProperties><hh:charPr id="0" height="1000"></hh:charPr></hh:charProperties>'
              '<hh:paraPr id="0"><hh:align horizontal="JUSTIFY"/></hh:paraPr>'
              '<hh:paraPr id="7"><hh:align horizontal="CENTER"/></hh:paraPr></hh:head>')
    fax = ('<hp:tc><hp:subList><hp:p paraPrIDRef="7"><hp:run charPrIDRef="0"><hp:t></hp:t></hp:run></hp:p></hp:subList>'
           '<hp:cellAddr colAddr="1" rowAddr="3"/><hp:cellSpan colSpan="1" rowSpan="1"/></hp:tc>')
    body = _tbl([[_c(0, 0, "법인등록번호"), _c(1, 0, "")], [_c(0, 1, "주소"), _c(1, 1, "")],
                 [_c(0, 2, "전 화"), _c(1, 2, "")], [_c(0, 3, "FAX"), fax]])
    src = _mini_hwpx(tmp_path / "a.hwpx", body)
    with zipfile.ZipFile(src, "a") as z:
        z.writestr("Contents/header.xml", header)
    v = vault.Vault({}, info={"법인등록번호": "110111-1234567", "주소": "강원 정선", "전화번호": "033-111-2222", "팩스번호": "033-111-3333"})
    fill_hwpx(src, tmp_path / "ao.hwpx", v)
    out = zipfile.ZipFile(tmp_path / "ao.hwpx").read("Contents/section0.xml").decode()
    para = {t: re.search(rf'paraPrIDRef="(\d+)"(?:(?!paraPrIDRef).)*?{re.escape(t)}', out, re.S).group(1)
            for t in ("110111-1234567", "강원 정선", "033-111-2222", "033-111-3333")}
    # 주소(글)는 가운데로 안 바꿈 — 원래 양쪽 정렬이라 왼쪽 정렬 사본(11/05: 넘친 줄 글자 사이 벌어짐 방지)
    assert para == {"110111-1234567": "7", "강원 정선": "8", "033-111-2222": "7", "033-111-3333": "7"}


def test_seal_cell_name_centered(tmp_path: Path):
    """11/04 사용자 요청(서식 1): '(인)'이 오른쪽 정렬이던 칸에 '(주)가상 홍길동   (인)'을 넣으면 가운데 정렬 사본 문단 모양으로."""
    from quantdocs.fill import fill_hwpx

    header = ('<hh:head><hh:paraProperties itemCnt="2"><hh:paraPr id="0"><hh:align horizontal="JUSTIFY"/></hh:paraPr>'
              '<hh:paraPr id="5"><hh:align horizontal="RIGHT"/><hh:margin left="1"/></hh:paraPr></hh:paraProperties>'
              '<hh:charProperties itemCnt="1"><hh:charPr id="0" height="1000"></hh:charPr></hh:charProperties></hh:head>')
    seal = ('<hp:tc><hp:subList><hp:p paraPrIDRef="5"><hp:run charPrIDRef="0"><hp:t>(인)</hp:t></hp:run></hp:p></hp:subList>'
            '<hp:cellAddr colAddr="1" rowAddr="0"/><hp:cellSpan colSpan="1" rowSpan="1"/></hp:tc>')
    src = _mini_hwpx(tmp_path / "s.hwpx", _tbl([[_c(0, 0, "상호·법인명(대표성명)"), seal]]))
    with zipfile.ZipFile(src, "a") as z:
        z.writestr("Contents/header.xml", header)
    fill_hwpx(src, tmp_path / "so.hwpx", vault.Vault({}, info={"회사명": "(주)가상", "대표자": "홍길동"}))
    zo = zipfile.ZipFile(tmp_path / "so.hwpx")
    out, head = zo.read("Contents/section0.xml").decode(), zo.read("Contents/header.xml").decode()
    pid = re.search(r'paraPrIDRef="(\d+)"(?:(?!paraPrIDRef).)*?\(주\)가상 홍길동', out, re.S).group(1)
    assert pid == "6" and 'paraProperties itemCnt="3"' in head
    block = re.search(r'<hh:paraPr id="6".*?</hh:paraPr>', head, re.S).group(0)
    assert 'horizontal="CENTER"' in block and '<hh:margin left="1"/>' in block  # 여백 등은 원래 모양 그대로


def test_other_party_zone_and_placeholder_signature(tmp_path: Path):
    """11/04 사용자 요청: 서식 28 발행자(제조사·공식 수입원) 칸은 비우고(서명 줄은 11/05부터 채움) '제안업체(공급받는 자)'에 회사명,
    서식 19 '○○○업체 대표자 : ○○○ (인)' 자리표시는 회사명·대표자로. 봉투 예시('대표이사 ○○○ (인)', 쌍점 없음)는 그대로."""
    from quantdocs.fill import fill_hwpx

    v = vault.Vault({}, info={"회사명": "(주)가상", "대표자": "홍길동", "주소": "강원 정선", "전화번호": "033-111-2222"})

    def para(t):
        return f'<hp:p><hp:run charPrIDRef="0"><hp:t>{t}</hp:t></hp:run></hp:p>'
    two = ('<hp:tc><hp:subList><hp:p><hp:run charPrIDRef="0"><hp:t>상호 :</hp:t></hp:run></hp:p>'
           '<hp:p><hp:run charPrIDRef="0"><hp:t>전화 :</hp:t></hp:run></hp:p></hp:subList>'
           '<hp:cellAddr colAddr="1" rowAddr="1"/><hp:cellSpan colSpan="1" rowSpan="1"/></hp:tc>')
    tbl = ("<hp:p><hp:run><hp:tbl><hp:tr>" + _c(0, 0, "제안업체(공급받는 자)") + _c(1, 0, "") + "</hp:tr><hp:tr>"
           + _c(0, 1, "발 행 자 (제조사 / 공식 수입원)") + two + "</hp:tr></hp:tbl></hp:run></hp:p>")
    body = (tbl + para("발행자   상호 :") + para("대표자 :            (인)") + para("본문")
            + para("   ○○○업체 대표자 :      ○○○ (인)") + para("대표이사 ○○○     (인)"))
    fill_hwpx(_mini_hwpx(tmp_path / "o.hwpx", body), tmp_path / "oo.hwpx", v)
    out = zipfile.ZipFile(tmp_path / "oo.hwpx").read("Contents/section0.xml").decode()
    assert "<hp:t>상호 :</hp:t>" in out and "<hp:t>전화 :</hp:t>" in out  # 발행자 칸은 그대로
    # 11/05 사용자 요청: 표 밖 서명 줄은 서식 28 '발행자 상호 :'·'대표자 : (인)'도 채움(표 안 발행자 칸은 그대로 비움)
    assert "<hp:t>발행자   상호 : (주)가상</hp:t>" in out and re.search(r"<hp:t>대표자 : 홍길동 +\(인\)</hp:t>", out)
    assert re.search(r"\(주\)가상 대표자 : 홍길동 +\(인\)", out)  # 이름은 쌍점 바로 옆(11/11 사용자 요청)
    assert "대표이사 ○○○     (인)" in out
    assert out.count("(주)가상") == 3  # 제안업체 칸 + 서식 28 서명 줄 + 서식 19 줄


def test_notice_number_autofill(tmp_path: Path):
    """11/04 사용자 요청: 공고번호는 공고마다 자동 — 나라장터 입찰공고번호('R26BK…-000')가 먼저(사용자 정정),
    모를 때만 공고문 기관 공고번호('제2026-1129호', '제 2026 - 호' 칸은 모양 유지). 공고명 칸 = 공고 이름."""
    from quantdocs.fill import agency_notice_no, fill_hwpx

    assert agency_notice_no({"제안요청서.hwpx": "…", "공고문.hwpx": "장성군 공고 제2026 - 1129 호\n…"}) == ("2026", "1129")
    assert agency_notice_no({"공고문.pdf": "공고 없음"}) is None
    v = vault.Vault({}, info={"회사명": "(주)가상"})
    body = _tbl([[_c(0, 0, "입  찰   공  고 (지 명)  번  호"), _c(1, 0, "제 2026 -     호")],
                 [_c(0, 1, "입찰공고번호"), _c(1, 1, "")], [_c(0, 2, "공 고 명"), _c(1, 2, "")]])
    fill_hwpx(_mini_hwpx(tmp_path / "n.hwpx", body), tmp_path / "no.hwpx", v, title="가상 전시", notice_no=("2026", "1129"),
              g2b_no="R26BK00000001")
    out = zipfile.ZipFile(tmp_path / "no.hwpx").read("Contents/section0.xml").decode()
    # 나라장터 번호가 있으면 그 번호(11/04 사용자 정정) — 자리표시 칸·빈 칸 모두
    assert out.count("R26BK00000001") == 2 and "1129" not in out and "가상 전시" in out
    fill_hwpx(_mini_hwpx(tmp_path / "m.hwpx", body), tmp_path / "mo.hwpx", v, notice_no=("2026", "1129"))
    out = zipfile.ZipFile(tmp_path / "mo.hwpx").read("Contents/section0.xml").decode()
    assert "제 2026 - 1129 호" in out and "제2026-1129호" in out  # 나라장터 번호를 모를 때만 기관 공고번호


def test_screen_hides_routine_notes():
    """11/04 사용자 요청: 화면 알림에서 'PDF에서 읽어 채움'·'Tesseract 없음'·'준공 전 실적 뺌' 문구를 뺌(읽어보기엔 남음)."""
    from quantdocs.build import _SCREEN_HIDDEN

    for n in ("회사정보 빈 칸을 서류 PDF에서 읽어 채움: 전화번호(등록증.pdf)",
              "글이 없는 스캔본인데 글자 인식 도구(Tesseract)가 없어 읽지 못한 서류: 인감증명서.pdf",
              "준공 전(준공일이 없거나 2026-11-03 뒤)이라 실적 후보에서 뺀 실적 12건: …",
              "스캔본이라 글자 인식(OCR)으로 읽은 서류: 인감증명서.pdf — 채운 값(특히 숫자·연혁) 원본과 대조"):
        assert _SCREEN_HIDDEN.match(n)
    assert not _SCREEN_HIDDEN.match("⚠ 회사 전화번호를 찾지 못해 서식의 전화번호 칸이 비어 있음")
    # 11/06 사용자 요청: 11/05에 더한 자료 읽기 안내도 숨김, '⚠' 경고는 그대로
    for n in ("회사 자료 묶음 밖 PDF도 읽음(jiil-quant-docs 폴더): 정량서류/연혁/연혁.pdf",
              "특허 건수는 특허증 파일 8개로 셈(특허목록 시트가 비어 있음) — 파일 하나 = 1건",
              "실적목록은 jiil-quant-docs 폴더의 '정량서류/공사수주현황/공사수주현황.xlsx'에서 읽음(114건) — 묶음 실적의 발주기관·지분율 등은 사업명으로 살림",
              "기술인력은 jiil-quant-docs 폴더의 '정량서류/기술인력/인력목록.xlsx'에서 읽음(10명) — 회사 자료 묶음의 인력목록보다 먼저",
              "신용평가등급 BB+(기업신용평가등급, 평가일 2026-06-16)을 '신용평가(27.6.15).pdf'에서 읽음 — 자기평점 경영상태에 씀",
              "연혁 21줄을 '주식회사 지일 일반현황 및 연혁.hwp'에서 읽어 넣음 — 줄 확인"):
        assert _SCREEN_HIDDEN.match(n), n
    assert not _SCREEN_HIDDEN.match("⚠ 신용평가등급 확인서 유효기간(2026-06-15)이 지났음 — 새 확인서로 바꾸세요")


def test_g2b_notice_number_with_order():
    from quantdocs.web import _g2b_no

    assert _g2b_no({"no": "R26BK01806620", "ord": "000"}, "x") == "R26BK01806620-000"
    assert _g2b_no({"no": "R26BK01806620", "ord": "1"}, "x") == "R26BK01806620-001"
    assert _g2b_no({}, "R26BK01806620") == "R26BK01806620"


def test_applicant_line_with_corp_and_name_blanks(tmp_path: Path):
    """11/04 사용자 요청(서식 2 하단): '신청인 : 대표자 (주)     대표자 성명 :     (인)' → (주)회사 · 성명 대표자."""
    from quantdocs.fill import fill_hwpx

    v = vault.Vault({}, info={"회사명": "(주)가상", "대표자": "홍길동"})
    body = '<hp:p><hp:run charPrIDRef="0"><hp:t>신청인 : 대표자 (주)             대표자<hp:fwSpace/> 성명 :           (인)</hp:t></hp:run></hp:p>'
    fill_hwpx(_mini_hwpx(tmp_path / "a.hwpx", body), tmp_path / "ao.hwpx", v)
    out = zipfile.ZipFile(tmp_path / "ao.hwpx").read("Contents/section0.xml").decode()
    assert re.search(r"대표자 \(주\)가상 +대표자<hp:fwSpace/> 성명 : 홍길동 +\(인\)", out) and out.count("(주)") == 1


def test_filled_text_is_11pt(tmp_path: Path):
    """11/05 사용자 요청: 채운 글은 전부 11pt. 칸 전체를 채운 글·이름표 뒤에 끼워 넣은 값('TEL :'·'대표자 :') 모두 —
    같은 줄의 이름표·서식 글자는 원래 크기 그대로, 이미 11pt인 글자 모양은 새로 만들지 않음."""
    from quantdocs.fill import fill_hwpx

    header = ('<hh:head><hh:charProperties itemCnt="2"><hh:charPr id="0" height="1000"><hh:ratio hangul="100"/></hh:charPr>'
              '<hh:charPr id="1" height="1100"><hh:ratio hangul="100"/></hh:charPr></hh:charProperties></hh:head>')
    inline = ('<hp:tc><hp:subList><hp:p paraPrIDRef="0"><hp:run charPrIDRef="0"><hp:t>TEL :          FAX :</hp:t></hp:run></hp:p>'
              '<hp:p paraPrIDRef="0"><hp:run charPrIDRef="0"><hp:t>둘째 줄</hp:t></hp:run></hp:p></hp:subList>'
              '<hp:cellAddr colAddr="0" rowAddr="1"/><hp:cellSpan colSpan="2" rowSpan="1"/></hp:tc>')
    addr11 = _c(1, 2, "").replace('charPrIDRef="0"', 'charPrIDRef="1"')
    body = (_tbl([[_c(0, 0, "주 소"), _c(1, 0, "")], [inline], [_c(0, 2, "상호"), addr11]])
            + '<hp:p><hp:run charPrIDRef="0"><hp:t>대표자 :          (인)</hp:t></hp:run></hp:p>')
    src = _mini_hwpx(tmp_path / "e.hwpx", body)
    with zipfile.ZipFile(src, "a") as z:
        z.writestr("Contents/header.xml", header)
    v = vault.Vault({}, info={"회사명": "(주)가상", "대표자": "홍길동", "주소": "강원특별자치도 정선군 남면 무릉2로 64(공장1층)",
                              "전화번호": "033-111-2222", "팩스번호": "033-111-3333"})
    fill_hwpx(src, tmp_path / "eo.hwpx", v)
    zo = zipfile.ZipFile(tmp_path / "eo.hwpx")
    out, head = zo.read("Contents/section0.xml").decode(), zo.read("Contents/header.xml").decode()
    assert "\ue000" not in out and "\ue001" not in out
    sizes = dict(re.findall(r'<hh:charPr id="(\d+)" height="(\d+)"', head))

    def size_of(text):
        return sizes[re.findall(r'charPrIDRef="(\d+)"><hp:t>' + re.escape(text), out)[-1]]
    assert size_of("강원특별자치도") == "1100"  # 칸 전체를 채운 주소
    assert size_of("033-111-2222") == "1100" and size_of("033-111-3333") == "1100"  # 'TEL :' 뒤에 끼워 넣은 값
    assert size_of("홍길동") == "1100"  # 서명 줄
    assert size_of("TEL :") == "1000" and size_of("둘째 줄") == "1000"  # 서식 글자는 그대로
    assert re.search(r'charPrIDRef="1"><hp:t>\(주\)가상', out)  # 이미 11pt인 칸은 새 글자 모양 없이
    assert 'charProperties itemCnt="3"' in head  # 10pt → 11pt 사본 하나만 더함


def test_label_paragraph_value_wraps_under_value(tmp_path: Path):
    """11/05 사용자 요청: '          주  소 : 강원특별자치도 … (공장1층)'이 둘째 줄로 넘어가면 줄 맨 앞이 아니라 값 시작 위치에 맞춰 이어지게
    — 그 문단에 내어쓰기(앞부분 너비)를 준 문단 모양 사본. 첫 줄 시작은 그대로, hp:default 묶음은 hp:case의 두 배."""
    from quantdocs.fill import fill_hwpx

    margin = lambda f: (f'<hh:margin><hc:intent value="0" unit="HWPUNIT"/><hc:left value="{1000 * f}" unit="HWPUNIT"/>'
                        f'<hc:right value="0" unit="HWPUNIT"/></hh:margin>')
    header = ('<hh:head><hh:charProperties itemCnt="1"><hh:charPr id="0" height="1000" useFontSpace="0"><hh:ratio hangul="100"/>'
              '<hh:spacing hangul="0"/></hh:charPr></hh:charProperties>'
              '<hh:paraProperties itemCnt="1"><hh:paraPr id="0"><hh:align horizontal="JUSTIFY"/><hp:switch><hp:case>'
              f'{margin(1)}</hp:case><hp:default>{margin(2)}</hp:default></hp:switch></hh:paraPr></hh:paraProperties></hh:head>')
    body = ('<hp:p paraPrIDRef="0"><hp:run charPrIDRef="0"><hp:t><hp:tab width="20000" leader="0" type="1"/>주  소 :</hp:t></hp:run></hp:p>'
            '<hp:p paraPrIDRef="0"><hp:run charPrIDRef="0"><hp:t><hp:tab width="20000" leader="0" type="1"/>상  호 :</hp:t></hp:run></hp:p>')
    src = _mini_hwpx(tmp_path / "h.hwpx", body)
    with zipfile.ZipFile(src, "a") as z:
        z.writestr("Contents/header.xml", header)
    fill_hwpx(src, tmp_path / "ho.hwpx", vault.Vault({}, info={"주소": "강원특별자치도 정선군 남면 무릉2로 64(공장1층)", "회사명": "(주)가상"}))
    zo = zipfile.ZipFile(tmp_path / "ho.hwpx")
    out, head = zo.read("Contents/section0.xml").decode(), zo.read("Contents/header.xml").decode()
    pid = re.search(r'<hp:p paraPrIDRef="(\d+)">(?:(?!</hp:p>).)*강원특별자치도', out, re.S).group(1)
    assert pid != "0"
    block = re.search(rf'<hh:paraPr id="{pid}".*?</hh:paraPr>', head, re.S).group(0)
    # 탭 20000 + '주' '소' 1000씩 + 빈칸 4개(500씩) + ':' 450
    assert re.findall(r'intent value="(-?\d+)"', block) == ["-24450", "-48900"]
    assert re.findall(r'left value="(\d+)"', block) == ["1000", "2000"]
    assert 'paraProperties itemCnt="2"' in head  # '주  소 :'·'상  호 :'는 앞부분 너비가 같아 사본 하나를 같이 씀
    assert re.search(rf'<hp:p paraPrIDRef="{pid}">(?:(?!</hp:p>).)*\(주\)가상', out, re.S)


def test_table_keeps_height_when_filled_text_wraps(tmp_path: Path):
    """11/05 필암서원 서식 12: 쪽 끝까지 0.2mm만 남는 '글자처럼 취급' 표에서 업태·종목이 두 줄이 되자 표가 통째로 다음 쪽으로 넘어가
    제목만 남음 → 늘어난 만큼 같은 표의 여유 많은 줄(주요연혁 빈 칸)을 줄여 표 전체 높이를 원래대로. 쪽에 여유가 넉넉한 표는 그대로."""
    from quantdocs.fill import fill_hwpx

    header = ('<hh:head><hh:charProperties itemCnt="1"><hh:charPr id="0" height="1300" useFontSpace="0"><hh:ratio hangul="100"/>'
              '</hh:charPr></hh:charProperties><hh:paraProperties itemCnt="1"><hh:paraPr id="0"><hh:align horizontal="JUSTIFY"/>'
              '<hh:lineSpacing type="PERCENT" value="170"/></hh:paraPr></hh:paraProperties></hh:head>')

    def form(lineseg_bottom):
        rows = [[_sz_cell(0, 0, "사 업 분 야", 12622, 2912).replace('charPrIDRef="1"', 'charPrIDRef="0"'),
                 _sz_cell(1, 0, "", 35341, 2912).replace('charPrIDRef="1"', 'charPrIDRef="0"')],
                [_sz_cell(0, 1, "〈주요연혁(요약)〉", 47963, 43823).replace('charPrIDRef="1"', 'charPrIDRef="0"')]]
        tbl = ('<hp:tbl rowCnt="2" colCnt="2"><hp:sz width="47963" height="46735"/>'
               + "".join("<hp:tr>" + "".join(r) + "</hp:tr>" for r in rows) + "</hp:tbl>")
        return ('<hp:p paraPrIDRef="0"><hp:run charPrIDRef="0"><hp:secPr><hp:pagePr height="84188"><hp:margin header="1417" footer="1417" '
                'top="4252" bottom="4252" left="5669" right="5669"/></hp:pagePr></hp:secPr></hp:run></hp:p>'
                f'<hp:p paraPrIDRef="0"><hp:run charPrIDRef="0">{tbl}<hp:t/></hp:run><hp:linesegarray>'
                f'<hp:lineseg textpos="0" vertpos="5880" vertsize="{lineseg_bottom - 5880}"/></hp:linesegarray></hp:p>')
    v = vault.Vault({}, info={"사업분야": "사업시설 관리, 사업지원 및 임대 서비스업 / 전시, 컨벤션 및 행사 대행업 / 가상 업종 하나 더"})
    heights = {}
    for name, bottom in (("tight", 72850 - 150), ("roomy", 72850 - 20000)):
        src = _mini_hwpx(tmp_path / f"{name}.hwpx", form(bottom))
        with zipfile.ZipFile(src, "a") as z:
            z.writestr("Contents/header.xml", header)
        rep = fill_hwpx(src, tmp_path / f"{name}_o.hwpx", v)
        out = zipfile.ZipFile(tmp_path / f"{name}_o.hwpx").read("Contents/section0.xml").decode()
        assert "가상 업종" in out
        heights[name] = [int(h) for h in re.findall(r'cellSz width="47963" height="(\d+)"', out)][0]
        if name == "tight":
            assert any("주요연혁" in x and "줄임" in x for x in rep.filled), rep.filled
    assert heights["roomy"] == 43823  # 쪽에 여유가 있으면 건드리지 않음
    assert 43823 - 6000 < heights["tight"] < 43823  # 늘어난 만큼(+1mm)만 줄임


def test_merge_drops_blank_paragraphs_before_page_break():
    """11/05 제보: 통합서식에 빈 쪽 — 서식 끝 빈 문단이 다음 쪽으로 넘어가 혼자 한 쪽을 차지. 서식 끝·'새 쪽' 앞 빈 문단은 지우고,
    글·표가 있는 문단과 서식 중간의 빈 문단은 그대로."""
    from quantdocs.forms_cut import _drop_blank_tail

    def p(t="", brk="0", extra=""):
        run = f'<hp:run charPrIDRef="0"><hp:t>{t}</hp:t>{extra}</hp:run>' if t or extra else '<hp:run charPrIDRef="0"/>'
        return f'<hp:p paraPrIDRef="0" pageBreak="{brk}">{run}<hp:linesegarray><hp:lineseg vertpos="0"/></hp:linesegarray></hp:p>'
    body = p("〈서식 5〉") + p() + p("장성군수 귀하") + p() + p() + p("둘째 쪽", "1") + p() + p("", extra="<hp:tbl/>") + p() + p()
    out = _drop_blank_tail(body)
    assert out.count("<hp:p ") == 6  # 서식 5·가운데 빈 문단·귀하·둘째 쪽·표 문단 + 맨 앞은 남김
    assert out.startswith(p("〈서식 5〉") + p() + p("장성군수 귀하") + p("둘째 쪽", "1"))
    assert out.endswith("<hp:tbl/></hp:run><hp:linesegarray><hp:lineseg vertpos=\"0\"/></hp:linesegarray></hp:p>")


def _score_doc(tmp_path: Path) -> Path:
    """필암서원 제안요청서 〈별표1〉 주1) 정량적평가 모양을 줄인 가짜 문서(표 구조·글은 원문과 같은 꼴)."""
    def para(t):
        return f'<hp:p paraPrIDRef="0"><hp:run charPrIDRef="0"><hp:t>{t}</hp:t></hp:run></hp:p>'
    track = _tbl([[_c(0, 0, "평 가 요 소", 7), _c(7, 0, "배점")],
                  [_c(0, 1, "사업 수행 실적"), _c(1, 1, "누 계 실 적"), _c(2, 1, "누계실적 (최근 5년간 기준/부가세 포함)", 5), _c(7, 1, "6")],
                  [_c(2, 2, "5.5억 이상"), _c(3, 2, "4억 이상 ~ 5.5억 미만"), _c(4, 2, "2.5억 이상 ~ 4억 미만"),
                   _c(5, 2, "1억 이상 ~ 2.5억 미만"), _c(6, 2, "1억 미만")],
                  [_c(2, 3, "6.0"), _c(3, 3, "5.0"), _c(4, 3, "4.0"), _c(5, 3, "3.0"), _c(6, 3, "2.0")]])
    staff = _tbl([[_c(0, 0, "기술 능력"), _c(1, 0, "특급기술자"), _c(2, 0, "고급기술자"), _c(3, 0, "중급기술자"), _c(4, 0, "초급기술자")],
                  [_c(1, 1, "1점"), _c(2, 1, "0.5점"), _c(3, 1, "0.3점"), _c(4, 1, "0.2점")]])
    patent = _tbl([[_c(0, 0, "배점 기준"), _c(1, 0, "2건 이상"), _c(2, 0, "2.0")], [_c(1, 1, "1건 이상 ~ 2건 미만"), _c(2, 1, "1.0")],
                   [_c(1, 2, "0건"), _c(2, 2, "0")]])
    credit = _tbl([[_c(0, 0, "평가 요소"), _c(1, 0, "신용평가등급", 2), _c(3, 0, "배점")],
                   [_c(1, 1, "회사채"), _c(2, 1, "기업신용평가등급")],
                   [_c(0, 2, "경영 상태"), _c(1, 2, "A- 이상"), _c(2, 2, "A- 이상"), _c(3, 2, "4")],
                   [_c(1, 3, "BBB+, BBB0, BBB-, BB+, BB0, BB-"), _c(2, 3, "BBB+, BBB0, BBB-, BB+, BB0, BB-"), _c(3, 3, "3.5")],
                   [_c(1, 4, "CCC+ 이하"), _c(2, 4, "CCC+ 이하"), _c(3, 4, "2")]])
    sanction = _tbl([[_c(0, 0, "신인도"), _c(1, 0, "부정당업자 제재처분"), _c(2, 0, "해당없음"), _c(3, 0, "해 당")],
                     [_c(2, 1, "2"), _c(3, 1, "0")]])
    body = (para("주1) 정량적평가 : 15점") + para("1) 사업수행실적 : 6점") + track
            + para("※ 1. 누계실적이 본 사업 사업비(400백만원) 이상인 경우 만점으로 함")
            + para("2. 미디어아트, 실감영상관·전시관 조성 등 단일계약 1건의 수주·준공 금액이 5천만원 이상(부가세포함)의 것을 유사 사업으로 인정함")
            + para("3. 기성실적은 인정하지 않고, 별건으로 진행된 설계용역 실적은 인정하지 않음")
            + para("2) 기술인력 보유상태 : 3점") + staff + para("※ 1. 공고일 기준 6개월 이상 해당업체에 근무하고 있는 기술자만 해당")
            + para("3) 보유 전문기술 현황 : 2점") + patent + para("4) 경영상태 평가 : 4점") + credit
            + para("5) 신인도 : 2점") + sanction + para("주2) 정성적평가 : 70점") + para("6) 사업의 이해 : 10점"))
    return _mini_hwpx(tmp_path / "rfp.hwpx", body)


def test_self_score_from_rfp_criteria(tmp_path: Path):
    """11/05 사용자 요청: 자기평점은 제안요청서 본문 배점기준으로 계산 — 실적(최근 5년·준공·유사사업·단일 5천만 이상·설계용역 제외·
    지분율)·기술인력(6개월 이상 재직, 한 사람은 가장 높은 등급 하나, 최고 배점)·특허 건수·신용등급·제재처분. 자료가 없으면 비움.
    주석 '사업비 이상이면 만점'과 배점표가 다르면 배점표대로 + 알림(사용자 결정)."""
    from quantdocs.selfscore import read_criteria, score

    crit = read_criteria([_score_doc(tmp_path)])
    assert [(c.kind, c.points) for c in crit] == [("track", 6), ("staff", 3), ("patent", 2), ("credit", 4), ("sanction", 2)]
    v = vault.Vault({}, info={"신용평가등급": "BB+", "부정당업자 제재처분": "없음"},
                    tracks=[{"사업명": "가상 실감영상관 조성", "준공일": dt.date(2024, 5, 1), "계약금액(원)": 300_000_000},
                            {"사업명": "가상 미디어아트 전시 제작설치", "준공일": dt.date(2023, 3, 1), "계약금액(원)": 200_000_000, "지분율(%)": 60},
                            {"사업명": "가상 홍보관 전시설계 용역", "준공일": dt.date(2025, 1, 1), "계약금액(원)": 90_000_000},
                            {"사업명": "가상 체험관 소품", "준공일": dt.date(2024, 1, 1), "계약금액(원)": 30_000_000},
                            {"사업명": "가상 전시관 조성", "준공일": dt.date(2020, 1, 1), "계약금액(원)": 500_000_000},
                            {"사업명": "가상 전시 진행 중", "준공일": dt.date(2027, 1, 1), "계약금액(원)": 500_000_000}],
                    staff=[{"이름": "갑", "입사일": dt.date(2020, 1, 1), "등급": "특급"}, {"이름": "갑", "입사일": dt.date(2020, 1, 1), "등급": "중급"},
                           {"이름": "을", "입사일": dt.date(2022, 1, 1), "등급": "고급기술자"}, {"이름": "병", "입사일": dt.date(2026, 9, 1), "등급": "고급"},
                           {"이름": "정", "입사일": dt.date(2019, 1, 1), "재직": "퇴사", "등급": "특급"}],
                    patents=[{"명칭": "가상 특허", "등록번호": "10-1"}])
    got = {s.kind: s for s in score(crit, v, dt.date(2026, 11, 3))}
    # 3억 + 2억×60% = 4.2억 → 배점표로는 '4억 이상 ~ 5.5억 미만' 5점, 주석 '사업비(4억) 이상이면 만점' — 계산식끼리 다르면 유리한 쪽(11/05)
    assert got["track"].score == 6.0
    assert any("주석" in n and "만점" in n for n in got["track"].notes)
    assert got["staff"].score == 1.5 and got["patent"].score == 1.0 and got["credit"].score == 3.5 and got["sanction"].score == 2
    empty = {s.kind: s for s in score(crit, vault.Vault({}), dt.date(2026, 11, 3))}
    assert all(s.score is None and any("자료 없음" in n for n in s.notes) for k, s in empty.items() if k != "sanction")
    assert empty["sanction"].score == 2  # 신인도는 늘 배점 만점(11/05 사용자 결정)


def test_self_score_fills_form_by_rfp(tmp_path: Path):
    """서식 24(자기평가 심사표) '자기평점' 칸 — 11/05 사용자 결정: 서식 배점이 본문과 다르면 서식 기준(본문 점수를 서식 배점에 맞춰 환산),
    본문에 없는 '단일최대' 줄은 본문 구간을 단일 최대 실적에 적용, 띄어 쓴 '신 인 도'도 맞춤(늘 만점), 계."""
    from quantdocs.fill import fill_hwpx
    from quantdocs.selfscore import SelfScore, read_criteria

    crit = read_criteria([_score_doc(tmp_path)])
    v = vault.Vault({}, info={"신용평가등급": "BB+", "부정당업자 제재처분": "없음"},
                    tracks=[{"사업명": "가상 실감영상관 조성", "준공일": dt.date(2024, 5, 1), "계약금액(원)": 600_000_000}],
                    staff=[{"이름": "갑", "입사일": dt.date(2020, 1, 1), "등급": "특급"}], patents=[{"명칭": "가상", "등록번호": "1"}])
    rows = [["구 분", "배 점", "자기평점", "심사평점"], ["계", "20", "", ""], ["최근 5년 수행실적(누계)", "4", "", ""],
            ["최근5년 단일최대 수행실적", "2", "", ""], ["관련 기술인력 보유현황", "6", "", ""], ["전문기술(특허) 보유", "2", "", ""],
            ["경영상태(신용평가등급)", "4", "", ""], ["신 인 도", "2", "", ""]]
    src = _mini_hwpx(tmp_path / "f24.hwpx", _tbl([[_c(col, r, t) for col, t in enumerate(row)] for r, row in enumerate(rows)]))
    rep = fill_hwpx(src, tmp_path / "o24.hwpx", vault.Vault({}), self_scores=SelfScore(crit, v, dt.date(2026, 11, 3)))
    out = zipfile.ZipFile(tmp_path / "o24.hwpx").read("Contents/section0.xml").decode()
    from quantdocs.fill import _cells, _spans
    got = {c.row: c.text for c in _cells(out, _spans(out, "tbl")[0]) if c.col == 2}
    # 11/05 사용자 결정: 기술인력·경영상태·신인도·특허만 자동 — 실적은 담당자가 직접(비움), 그래서 계도 비움
    # 인력 특급 1명 1점(본문 3점) × 6/3 = 2, 특허 1건 1점, 경영 3.5, 신인도 2
    assert got == {0: "자기평점", 1: "", 2: "", 3: "", 4: "2", 5: "1", 6: "3.5", 7: "2"}
    assert any("직접" in r[4] for r in rep.self_rows if r[0].startswith("최근"))


def test_self_score_form_with_own_criteria(tmp_path: Path):
    """국민안전체험관 서식 6 모양 — 서식 표 안에 구간·등급·신용등급이 적힌 자기평가표는 서식 기준으로 계산(11/05 사용자 결정),
    인력은 '미보유 시 4.2점'(서식)과 '4.2 + ∑'(본문 주석)처럼 식이 다르면 유리한 쪽, 신인도는 만점 고정."""
    from quantdocs.fill import _cells, _spans
    from quantdocs.selfscore import Criterion, read_form, score_form

    def r(row, *cols):
        return [_c(col, row, t) for col, t in cols]
    rows = [r(0, (0, "항 목"), (1, "세 부 항 목"), (2, "배 점"), (3, "평 점"), (4, "비 고")),
            r(1, (0, "사업수행실적(최근3년)"), (1, "70억 이상"), (2, "6.0"), (3, ""), (4, "부가세 포함10억이상 실적")),
            r(2, (1, "50억 이상"), (2, "4.8")), r(3, (1, "50억 미만"), (2, "4.2")), r(4, (1, "상한 점수"), (2, "6.0")),
            r(5, (0, "기술인력"), (1, "특급기술자"), (2, "0.8/인"), (3, ""), (4, "※ 최고 6점 - 미보유 시 4.2점")),
            r(6, (1, "고급기술자"), (2, "0.6/인")), r(7, (1, "상한 점수"), (2, "6.0")),
            r(8, (0, "신인도"), (1, "해당없음"), (2, "2.0"), (3, "")), r(9, (1, "해당"), (2, "1.4"))]
    xml = _tbl(rows)
    form = read_form(_cells(xml, _spans(xml, "tbl")[0]))
    assert [(f.points, bool(f.crit)) for f in form] == [(6.0, True), (6.0, True), (2.0, True)]
    body = [Criterion("기술인력", 6, "staff", note="※ 평점= 4.2+∑(점수×인원) / 최고 6점")]
    v = vault.Vault({}, tracks=[{"사업명": "가상 전시관", "준공일": dt.date(2025, 1, 1), "계약금액(원)": 5_500_000_000},
                                {"사업명": "가상 작은 것", "준공일": dt.date(2025, 2, 1), "계약금액(원)": 500_000_000}],
                    staff=[{"이름": "갑", "입사일": dt.date(2020, 1, 1), "등급": "특급"}])
    got = [it.score for _, it in score_form(form, body, v, dt.date(2026, 11, 3))]
    # 실적은 담당자가 직접(비움), 인력: 0.8 → 본문 식 4.2 + 0.8 = 5.0, 신인도 2
    assert got == [None, 5.0, 2.0]


def test_package_lists_match_kept_sections(tmp_path: Path):
    """11/05 제보: 구역이 둘인 제안요청서(국민안전체험관)로 만든 통합서식이 한글에서 '파일이 손상되었습니다' — 서식 없는 구역을
    content.hpf에서만 지우고 META-INF/container.rdf·머리 정보 secCnt는 그대로였음. 서식이 둘째 구역에만 있어도 section0으로 다시 이름 붙임."""
    from quantdocs import forms_cut

    def sec(body):
        return f'<hs:sec><hp:p paraPrIDRef="0"><hp:run charPrIDRef="0"><hp:secPr/></hp:run></hp:p>{body}</hs:sec>'
    para = lambda t, brk="0": f'<hp:p paraPrIDRef="0" pageBreak="{brk}"><hp:run charPrIDRef="0"><hp:t>{t}</hp:t></hp:run></hp:p>'
    rdf = ('<rdf:RDF xmlns:rdf="r"><rdf:Description rdf:about=""><ns0:hasPart xmlns:ns0="p" rdf:resource="Contents/header.xml"/></rdf:Description>'
           + "".join(f'<rdf:Description rdf:about=""><ns0:hasPart xmlns:ns0="p" rdf:resource="Contents/section{i}.xml"/></rdf:Description>'
                     f'<rdf:Description rdf:about="Contents/section{i}.xml"><rdf:type rdf:resource="p#SectionFile"/></rdf:Description>' for i in (0, 1))
           + "</rdf:RDF>")
    hpf = ('<opf:package xmlns:opf="o"><opf:manifest><opf:item id="header" href="Contents/header.xml"/>'
           '<opf:item id="section0" href="Contents/section0.xml"/><opf:item id="section1" href="Contents/section1.xml"/></opf:manifest>'
           '<opf:spine><opf:itemref idref="header"/><opf:itemref idref="section0"/><opf:itemref idref="section1"/></opf:spine></opf:package>')
    src = tmp_path / "rfp.hwpx"
    with zipfile.ZipFile(src, "w") as z:
        z.writestr("mimetype", "application/hwp+zip")
        z.writestr("Contents/header.xml", '<hh:head xmlns:hh="h" secCnt="2"><hh:charProperties itemCnt="1"><hh:charPr id="0" height="1000"></hh:charPr></hh:charProperties></hh:head>')
        z.writestr("Contents/section0.xml", sec(para("본문 — 서식 없음")))
        z.writestr("Contents/section1.xml", sec(para("〈서식 1〉 참가신청서", "1") + para("내용") + para("〈서식 2〉 서약서", "1") + para("내용")))
        z.writestr("Contents/content.hpf", hpf)
        z.writestr("META-INF/container.rdf", rdf)
    forms = forms_cut.cut(src, tmp_path / "cut")
    assert [f.no for f in forms] == ["1", "2"]
    for f in forms:
        part = zipfile.ZipFile(tmp_path / "cut" / f.file)
        assert forms_cut.section_names(part.namelist()) == ["Contents/section0.xml"]
        assert "section1" not in part.read("META-INF/container.rdf").decode() + part.read("Contents/content.hpf").decode()
        assert 'secCnt="1"' in part.read("Contents/header.xml").decode()
    out = forms_cut.merge(src, [(f, tmp_path / "cut" / f.file) for f in forms], tmp_path / "merged.hwpx")
    z = zipfile.ZipFile(out)
    assert forms_cut.section_names(z.namelist()) == ["Contents/section0.xml"]
    rdf_out, hpf_out = z.read("META-INF/container.rdf").decode(), z.read("Contents/content.hpf").decode()
    assert re.findall(r"Contents/section\d\.xml", rdf_out) == ["Contents/section0.xml"] * 2
    assert re.findall(r"Contents/section\d\.xml", hpf_out) == ["Contents/section0.xml"]
    assert re.findall(r'idref="([^"]+)"', hpf_out) == ["header", "section0"] and "section1" not in hpf_out
    assert 'secCnt="1"' in z.read("Contents/header.xml").decode()
    assert "서식 2" in z.read("Contents/section0.xml").decode()


def test_inline_phone_fax_paragraph_and_company_seal_cell(tmp_path: Path):
    """11/05 제보(국민안전체험관): ① 표 밖 '전화번호 :      (FAX :      )' 한 줄이 비어 있음 ② 질의서 '회사명 | 대표자 :     (인)' 칸이 빔
    ③ '전화번호(FAX번호)' 칸에 FAX가 빠짐. 증명서 발급기관 칸 '기관 명 : (인) (전화 : )'(표 칸)은 다른 쪽이 적는 곳이라 그대로.
    또 이름표 문단 고침이 문서 순서대로 모이지 않아 채운 줄이 엉뚱한 곳에 한 번 더 들어가던 것(줄 겹침)도 함께."""
    from quantdocs.fill import fill_hwpx

    para = lambda t: f'<hp:p paraPrIDRef="0"><hp:run charPrIDRef="0"><hp:t>{t}</hp:t></hp:run></hp:p>'
    body = (_tbl([[_c(0, 0, "회사명"), _c(1, 0, "대표자 :          (인)")], [_c(0, 1, "전화번호(FAX번호)"), _c(1, 1, "")],
                  [_c(0, 2, "기 관 명 :      (인)   (전화 :          )", 2)]])
            + para("        업 체 명 :           ") + para("        대 표 자 :                  (인)")
            + para("        주    소 :") + para("        전화번호 :                  (FAX :              )") + para("        작 성 자 :      "))
    v = vault.Vault({}, info={"회사명": "(주)가상", "대표자": "홍길동", "주소": "강원 정선", "전화번호": "033-111-2222", "팩스번호": "033-111-3333"})
    fill_hwpx(_mini_hwpx(tmp_path / "p.hwpx", body), tmp_path / "po.hwpx", v)
    out = zipfile.ZipFile(tmp_path / "po.hwpx").read("Contents/section0.xml").decode()
    from quantdocs.fill import _text
    t = re.sub(r"\s+", " ", _text(out))
    assert "(주)가상 대표자 : 홍길동 (인)" in t
    assert "033-111-2222 / 033-111-3333" in t
    assert "(전화 : )" in t  # 발급기관 칸은 그대로
    assert re.search(r"업 체 명 : \(주\)가상 대 표 자 : 홍길동 \(인\) 주 소 : 강원 정선 전화번호 : 033-111-2222 \(FAX : 033-111-3333 \) 작 성 자 :", t)
    assert t.count("전화번호 :") == 1  # 한 번만(겹치지 않음)


def test_bond_pledge_signature_agent_and_corp_numbers(tmp_path: Path):
    """11/05 사용자 요청 묶음 — ③ 입찰보증금 확약서: 입찰 금액 = 제안요청서 기초금액(숫자 + 괄호 안 한글), 보증금 = × 0.05(11/11 사용자 요청)
    ② 청렴계약 '서 약 자 : (인)' = 대표자 ④ 위임장 대리인 주소는 비움 ⑧ '생년월일(사업자등록번호)' = 법인등록번호 (사업자등록번호)."""
    from quantdocs.fill import _cells, _spans, basis_amount, fill_hwpx, kor_money

    assert kor_money(400_000_000) == "사억" and kor_money(20_000_000) == "이천만" and kor_money(350_000_000) == "삼억오천만"
    assert basis_amount({"공고문.hwpx": "기초금액 금 7,000,000,000원", "제안요청서.hwpx": "마. 사 업 비 : 금 400,000,000원(부가가치세 포함)"}) \
        == (400_000_000, "제안요청서.hwpx")
    assert basis_amount({"공고문.hwpx": "기초금액(A=B+C)추정가격(B)부가가치세(C)비고7,000,000,000원6,363,636,364원"})[0] == 7_000_000_000

    def para(t):
        return f'<hp:p paraPrIDRef="0"><hp:run charPrIDRef="0"><hp:t>{t}</hp:t></hp:run><hp:linesegarray><hp:lineseg/></hp:linesegarray></hp:p>'
    body = (para("입찰보증금 지급 확약서") + para(" 2. 입 찰 금 액 : 금              원(금            원)")
            + para(" 3. 입찰 보증금 : 금              원(금            원)") + para("입찰보증금을 납입할 것을 확약하고")
            + para("서 약 자 :           (인)")
            + _tbl([[_c(0, 0, "대리인", 1).replace('rowSpan="1"', 'rowSpan="2"'), _c(1, 0, "성 명"), _c(2, 0, "")],
                    [_c(1, 1, "주 소"), _c(2, 1, "")],
                    [_c(0, 2, "주 소"), _c(1, 2, "", 2)],
                    [_c(0, 3, "생년월일(사업자등록번호)"), _c(1, 3, "", 2)]]))
    src = _mini_hwpx(tmp_path / "b.hwpx", body)
    v = vault.Vault({}, info={"대표자": "홍길동", "주소": "가상시 가상로 1", "법인등록번호": "110111-0000000", "사업자등록번호": "123-45-67890"})
    rep = fill_hwpx(src, tmp_path / "o.hwpx", v, basis=400_000_000)
    xml = zipfile.ZipFile(tmp_path / "o.hwpx").read("Contents/section0.xml").decode()
    flat = re.sub(r"<[^>]+>", "", xml)
    assert "입 찰 금 액 : 금 400,000,000원(금 사억원)" in flat and "입찰 보증금 : 금 20,000,000원(금 이천만원)" in flat
    assert "서 약 자 : 홍길동" in flat
    got = {(c.row, c.col): c.text for c in _cells(xml, _spans(xml, "tbl")[0])}
    assert got[(1, 2)] == "" and got[(2, 1)] == "가상시 가상로 1"  # 대리인 묶음의 주소는 비움, 회사 주소 줄은 채움
    assert got[(3, 1)] == "110111-0000000 (123-45-67890)"
    assert any("보증금" in x for x in rep.filled)
    # 가격제안서처럼 보증금 글이 없는 서식의 '입찰금액'엔 넣지 않음(지일이 정할 금액)
    src2 = _mini_hwpx(tmp_path / "c.hwpx", para("가격 제안서") + para(" 입 찰 금 액 : 금              원(금            원)"))
    fill_hwpx(src2, tmp_path / "o2.hwpx", v, basis=400_000_000)
    assert "400,000,000" not in zipfile.ZipFile(tmp_path / "o2.hwpx").read("Contents/section0.xml").decode()


def test_main_docs_blank_forms_and_fax_override(tmp_path: Path, monkeypatch):
    """11/05 사용자 요청 — ① 첨부는 공고문·제안요청서만 ⑬⑭ 서면질의서·공동수급 서류는 공란 ⑤ FAX는 .env QUANT_FAX가 먼저."""
    from quantdocs import importer
    from quantdocs.build import is_manual, main_docs

    files = [Path(n) for n in ("공고문(가상).hwpx", "제안요청서(가상).hwp", "과업지시서(가상).hwpx", "도면.pdf")]
    keep, dropped = main_docs(files)
    assert [p.name for p in keep] == ["공고문(가상).hwpx", "제안요청서(가상).hwp"] and len(dropped) == 2
    assert main_docs([Path("과업지시서.hwpx")]) == ([Path("과업지시서.hwpx")], [])  # 둘 다 없으면 전부
    # 서식만 따로 담은 첨부도 읽음(11/05 — 공고마다 서식이 다른 곳에 있음)
    assert [p.name for p in main_docs([Path("과업지시서.hwp"), Path("붙임2_제안서 작성 서식.hwp"), Path("별지 양식.hwpx")])[0]] \
        == ["붙임2_제안서 작성 서식.hwp", "별지 양식.hwpx"]
    assert all(is_manual(n) for n in ("서면질의서", "질의서", "공동수급표준협정서(분담이행방식)", "공동수급 대표자 선임계", "공동수급체 합의각서"))
    assert not is_manual("입찰보증금 지급 확약서") and not is_manual("사업수행능력 자기평가 심사표")
    monkeypatch.setattr(vault, "ENV_FILE", tmp_path / "none.env")
    monkeypatch.setenv("QUANT_FAX", "000-000-0000")
    monkeypatch.setenv("QUANT_EMAIL", "mail@example.com")  # 11/11 이메일·홈페이지도 .env로
    monkeypatch.setenv("QUANT_HOMEPAGE", "https://example.com/")
    v = vault.Vault({}, info={"팩스번호": "111-111-1111"})
    importer.enrich_vault(v)
    assert v.info["팩스번호"] == "000-000-0000"
    assert v.info["이메일"] == "mail@example.com" and v.info["홈페이지"] == "https://example.com/"


def test_track_form_delivery_amount_and_total_cells(tmp_path: Path):
    """국민안전체험관 서식 7 '체험물 제작·설치 실적' — '납품(도급)액(천원)'·'남품(준공)년 월 일' 머리, '실적 누계 | 건 | 천원' 줄(11/05)."""
    from quantdocs.fill import FillReport, _cells, _fill_tracks, _spans

    head = ["남품(준공)년 월 일", "사업명", "발주기관", "납품(도급)액 (천원)", "비고"]
    rows = [[_hp_cell(i, 0, h) for i, h in enumerate(head)], [_hp_cell(0, 1, "실적 누계"), _hp_cell(1, 1, "건"), _hp_cell(2, 1, "  "),
                                                               _hp_cell(3, 1, "     천원"), _hp_cell(4, 1, "")]]
    rows += [[_hp_cell(i, r, "") for i in range(5)] for r in (2, 3)]
    xml = "<hp:tbl>" + "".join("<hp:tr>" + "".join(r) + "</hp:tr>" for r in rows) + "</hp:tbl>"
    tracks = [{"사업명": "가상 A", "준공일": dt.date(2024, 7, 1), "계약금액(원)": 345_678_912, "발주기관": "가상시"},
              {"사업명": "가상 B", "준공일": dt.date(2025, 3, 1), "계약금액(원)": 2_100_000_000}]
    rep = FillReport("x")
    vals = {(c.row, c.col): l[0] for c, l, _ in _fill_tracks(xml, _cells(xml, _spans(xml, "tbl")[0]), tracks, rep)}
    assert vals[(2, 0)] == "2024.07.01" and vals[(2, 1)] == "가상 A" and vals[(2, 3)] == "345,678,912"
    assert vals[(1, 1)] == "2건" and vals[(1, 3)] == "2,445,678,912원"


def test_hangul_history_table_date_line_then_content(tmp_path: Path, monkeypatch):
    """11/05 받은 연혁은 한글(.hwp) '일반현황 및 연혁' 표 — 날짜만 한 줄, 내용은 다음 줄(두 줄로 넘어가기도), 앞쪽 일반현황 표는 이름표/값 줄.
    jiil-quant-docs 폴더 맨 위의 .hwp도 읽음."""
    from quantdocs import importer
    from quantdocs.importer import read_history, read_overview

    text = "\n".join(["(가상)회사 일반현황 및 연혁", "1. 일반현황", "회 사 명", "(가상)회사", "대표자명", "(가상)대표",
                      "팩스번호", "000-000-0000", "총종업원수", "7명", "2. 회사연혁", "연 월 일", "내         용", "비  고",
                      "2009.09", "(가상) 법인설립", "2020.06", "(가상) 플랫폼", "(가상) 과제 선정", "2024.12", "(가상) 인증"])
    got = read_history(text)
    assert [(h["연월일"], h["내용"]) for h in got] == [("2009.09", "(가상) 법인설립"), ("2020.06", "(가상) 플랫폼 (가상) 과제 선정"),
                                                     ("2024.12", "(가상) 인증")]
    assert read_overview(text) == {"회사명": "(가상)회사", "대표자": "(가상)대표", "팩스번호": "000-000-0000", "종업원 수": "7명"}
    from quantdocs.fill import _label_key, _value
    assert _label_key("총직원수") == "종업원 수" and _label_key("총 종업원 수") == "종업원 수"
    assert _value("종업원 수", vault.Vault({}, info={"종업원 수": "7"})) == "7명"
    # 예전 모양('2005.03 법인 설립' 한 줄)도 그대로
    assert read_history("2005.03 (가상) 법인 설립")[0]["내용"] == "(가상) 법인 설립"

    (tmp_path / "연혁.hwp").write_bytes(b"x")
    v = vault.Vault({}, info={"회사명": "(가상)시트 값"})
    vault.add_loose(v, tmp_path)
    assert "files/연혁.hwp" in v.files
    monkeypatch.setattr(vault, "ENV_FILE", tmp_path / "none.env")
    monkeypatch.setattr(importer, "_cached_text", lambda path, data: text)
    importer.enrich_vault(v)
    assert len(v.history) == 3 and v.info["회사명"] == "(가상)시트 값" and v.info["대표자"] == "(가상)대표"


def test_justified_value_cell_gets_left_aligned_copy(tmp_path: Path):
    """11/05 제보: 양쪽 정렬 칸에 넣은 '법인등록번호 (사업자등록번호)'가 두 줄로 넘어가 첫 줄 숫자 사이가 벌어짐 → 그 칸은 왼쪽 정렬 사본."""
    from quantdocs.fill import fill_hwpx

    header = ('<hh:head xmlns:hh="h"><hh:refList><hh:charProperties itemCnt="1"><hh:charPr id="0" height="1000"><hh:ratio hangul="100"/>'
              '</hh:charPr></hh:charProperties><hh:paraProperties itemCnt="1"><hh:paraPr id="0"><hh:align horizontal="JUSTIFY"/>'
              '</hh:paraPr></hh:paraProperties></hh:refList></hh:head>')
    src = _mini_hwpx(tmp_path / "j.hwpx", _tbl([[_c(0, 0, "법인등록번호(사업자등록번호)"), _c(1, 0, "")]]))
    with zipfile.ZipFile(src) as z:
        items = {n: z.read(n) for n in z.namelist()}
    items["Contents/header.xml"] = header.encode()
    with zipfile.ZipFile(src, "w") as z:
        for n, d in items.items():
            z.writestr(n, d)
    fill_hwpx(src, tmp_path / "o.hwpx", vault.Vault({}, info={"법인등록번호": "000000-0000000", "사업자등록번호": "000-00-00000"}))
    out = zipfile.ZipFile(tmp_path / "o.hwpx")
    sec, head = out.read("Contents/section0.xml").decode(), out.read("Contents/header.xml").decode()
    cell = re.findall(r"<hp:tc.*?</hp:tc>", sec, re.S)[1]
    pid = re.search(r'paraPrIDRef="(\d+)"', cell).group(1)
    assert pid != "0" and re.search(rf'<hh:paraPr id="{pid}"><hh:align horizontal="LEFT"/>', head) and 'itemCnt="2"' in head


def test_long_history_table_splits_across_pages(tmp_path: Path):
    """11/05 사용자 요청: 연혁이 칸보다 길어 쪽 끝에서 잘림 → 그 표만 '글자처럼 취급'을 풀고 쪽 경계에서 '나눔'(pageBreak=TABLE),
    문단 위 간격(prev)도 높이 어림에 넣음. 짧은 연혁이면 그대로."""
    from quantdocs.fill import fill_hwpx

    header = ('<hh:head xmlns:hh="h"><hh:refList><hh:charProperties itemCnt="1"><hh:charPr id="0" height="1000"><hh:ratio hangul="100"/>'
              '</hh:charPr></hh:charProperties><hh:paraProperties itemCnt="1"><hh:paraPr id="0"><hh:align horizontal="LEFT"/>'
              '<hh:margin><hc:prev value="1000"/></hh:margin><hh:lineSpacing type="PERCENT" value="160"/></hh:paraPr></hh:paraProperties>'
              '</hh:refList></hh:head>')
    cell = ('<hp:tc><hp:subList><hp:p paraPrIDRef="0"><hp:run charPrIDRef="0"><hp:t>주요연혁</hp:t></hp:run></hp:p></hp:subList>'
            '<hp:cellAddr colAddr="0" rowAddr="0"/><hp:cellSpan colSpan="1" rowSpan="1"/><hp:cellSz width="40000" height="10000"/></hp:tc>')
    body = ('<hp:p><hp:run><hp:tbl pageBreak="NONE" rowCnt="1" colCnt="1"><hp:sz width="40000" height="10000"/>'
            '<hp:pos treatAsChar="1" affectLSpacing="1"/><hp:tr>' + cell + '</hp:tr></hp:tbl></hp:run>'
            '<hp:linesegarray><hp:lineseg/></hp:linesegarray></hp:p>')

    def run(n):
        src = _mini_hwpx(tmp_path / f"h{n}.hwpx", body)
        with zipfile.ZipFile(src) as z:
            items = {k: z.read(k) for k in z.namelist()}
        items["Contents/header.xml"] = header.encode()
        with zipfile.ZipFile(src, "w") as z:
            for k, d in items.items():
                z.writestr(k, d)
        v = vault.Vault({}, history=[{"연월일": f"20{10 + i}.01", "내용": f"(가상) 연혁 {i}"} for i in range(n)])
        rep = fill_hwpx(src, tmp_path / f"o{n}.hwpx", v)
        return zipfile.ZipFile(tmp_path / f"o{n}.hwpx").read("Contents/section0.xml").decode(), rep
    sec, rep = run(20)
    assert 'pageBreak="TABLE"' in sec and 'treatAsChar="0"' in sec and "<hp:linesegarray>" not in sec.split("</hp:tbl>")[-1]
    assert any("나뉘게" in x for x in rep.filled)
    sec, _ = run(2)
    assert 'pageBreak="NONE"' in sec and 'treatAsChar="1"' in sec


def test_staff_excel_in_folder_and_pick(tmp_path: Path):
    """11/05 사용자 요청: 기술인력 엑셀을 jiil-quant-docs 폴더에서 바로 읽음(묶음 다시 잠그지 않게, 등급이 '업무' 칸·입사일 '2011.09.01' 글자여도),
    화면에서 고른 사람만 자기평점 기술인력 점수에(staff_status — 조건에 안 맞는 사람은 이유와 함께 빠짐)."""
    from openpyxl import Workbook

    from quantdocs.selfscore import Criterion, score, staff_status

    wb = Workbook()
    ws = wb.active
    ws.append(["이름", "입사일", "업무"])
    for r in (["(가)갑", "2011.09.01", "특급"], ["(가)을", "2020.02.17", "고급"], ["(가)병", "2026.10.01", "초급"], ["(가)정", "2019.01.01", ""]):
        ws.append(r)
    wb.save(tmp_path / "기술인력.xlsx")
    (tmp_path / "~$기술인력.xlsx").write_bytes(b"lock")  # 엑셀이 열어 둔 임시 파일은 안 읽음
    v = vault.Vault({}, staff=[{"이름": "(가)옛 사람", "등급": "특급", "입사일": dt.date(2010, 1, 1)}])
    vault.add_loose(v, tmp_path)
    assert v.staff_src == "기술인력.xlsx" and [r["이름"] for r in v.staff] == ["(가)갑", "(가)을", "(가)병", "(가)정"]
    c = Criterion("기술인력", 6, "staff", note="※ 공고일 기준 6개월 이상 근무", per_grade={"특급": 1.0, "고급": 0.5, "초급": 0.2})
    st = {p["이름"]: (p["counted"], p["why"]) for p in staff_status(c, v, dt.date(2026, 11, 5))}
    assert st == {"(가)갑": (True, ""), "(가)을": (True, ""), "(가)병": (False, "재직 6개월 미만·입사일 없음"), "(가)정": (False, "등급 없음")}
    assert score([c], v, dt.date(2026, 11, 5))[0].score == 1.5
    v.staff = [r for r in v.staff if r["이름"] in {"(가)갑"}]  # 화면에서 갑만 고름 — build(staff_pick=)가 하는 일
    assert score([c], v, dt.date(2026, 11, 5))[0].score == 1.0


def test_staff_zero_picked_gets_lowest_band():
    """11/11 사용자 제보: 화면에서 0명을 고르면 '자료 없음'으로 점수가 비었음 — 0명 = 0점 → '점수 30점 미만' 구간 2.0점이어야 함.
    (화면에서 고르지 않았는데 인력 자료가 없으면 예전처럼 '자료 없음')"""
    from quantdocs.selfscore import Band, Criterion, score

    c = Criterion("참여기술자", 5, "staff", per_grade={"특급": 10.0, "고급": 8.0, "중급": 6.0, "초급": 4.0})
    c.unit = "점"
    c.bands = [Band(50, None, 5.0, "점수 50점 이상"), Band(40, 50, 4.0, "점수 50점 미만 40점 이상"),
               Band(30, 40, 3.0, "점수 40점 미만 30점 이상"), Band(None, 30, 2.0, "점수 30점 미만")]
    v = vault.Vault({}, staff=[])
    assert score([c], v, dt.date(2026, 11, 11))[0].score is None  # 자료 없음
    v.staff_pick_from_screen = True  # build(staff_pick=[])가 하는 일
    got = score([c], v, dt.date(2026, 11, 11))[0]
    assert got.score == 2.0 and "0명" in got.basis and "30점 미만" in got.basis


def test_signature_lines_without_colon_and_manual_form(tmp_path: Path):
    """11/05 사용자 요청: 대표자 서명이 있는 서식엔 자동 기재 — 쌍점 없는 '서 약 자      (인) (사용인감)'(보안서약서),
    직접 기입 서식(재직증명서)은 표는 그대로 두고 표 밖 서명 줄만(sign_only)."""
    from quantdocs.fill import fill_hwpx

    v = vault.Vault({}, info={"회사명": "(주)가상", "대표자": "홍길동", "주소": "강원 정선"})

    def para(t):
        return f'<hp:p><hp:run charPrIDRef="0"><hp:t>{t}</hp:t></hp:run></hp:p>'
    fill_hwpx(_mini_hwpx(tmp_path / "a.hwpx", para("서 약 자                 (인) (사용인감)")), tmp_path / "ao.hwpx", v)
    out = zipfile.ZipFile(tmp_path / "ao.hwpx").read("Contents/section0.xml").decode()
    assert re.search(r"서 약 자  홍길동 +\(인\) \(사용인감\)", out)
    body = (_tbl([[_c(0, 0, "주     소"), _c(1, 0, "")]]) + para("업 체 명 :") + para("주    소 :") + para("대 표 자 :                (인)"))
    fill_hwpx(_mini_hwpx(tmp_path / "b.hwpx", body), tmp_path / "bo.hwpx", v, sign_only=True)
    out = zipfile.ZipFile(tmp_path / "bo.hwpx").read("Contents/section0.xml").decode()
    assert "업 체 명 : (주)가상" in out and "주    소 : 강원 정선" in out and re.search(r"대 표 자 : 홍길동 +\(인\)", out)
    assert out.count("강원 정선") == 1  # 표 안 '주소' 칸(직원 주소)은 그대로


def test_staff_box_only_when_rfp_has_staff_criteria(tmp_path: Path):
    """11/05 사용자 요청: 기술인력 칸은 제안요청서에 기술인력 배점 기준이 있는 공고에서만 — summary.staff_on."""
    from quantdocs.build import analyze, summary

    v = vault.Vault({}, staff=[{"이름": "(가)갑", "등급": "특급", "입사일": dt.date(2020, 1, 1)}])
    rfp = _score_doc(tmp_path)  # 기술인력 배점표 있음
    on = summary(analyze([rfp], v, dt.date(2026, 11, 3), tmp_path / "w1"))
    assert on["staff_on"] and on["staff"] and "특급 1점" in on["staff_rule"]
    plain = _mini_hwpx(tmp_path / "plain.hwpx", '<hp:p><hp:run charPrIDRef="0"><hp:t>가상 공고문</hp:t></hp:run></hp:p>')
    assert summary(analyze([plain], v, dt.date(2026, 11, 3), tmp_path / "w2"))["staff_on"] is False


def test_patent_folder_counts_and_bundle(tmp_path: Path):
    """11/05 사용자 결정 (가): jiil-quant-docs 폴더의 '특허증' 하위 폴더 PDF — 특허목록 시트가 비면 파일 수로 건수를 세어 자기평점(보유기술)을
    자동으로, 보유기술을 평가하는 공고면 특허증 원본을 통합본 끝에."""
    from quantdocs.build import analyze
    from quantdocs.selfscore import AUTO_KINDS, read_criteria, score

    (tmp_path / "특허증").mkdir()
    for i in range(3):  # 내용이 다른 PDF(같으면 한 번만 읽음)
        (tmp_path / "특허증" / f"(가상)특허{i}.pdf").write_bytes(_blank_pdf(i + 1))
    (tmp_path / "기타폴더").mkdir()
    (tmp_path / "기타폴더" / "무관.pdf").write_bytes(_blank_pdf(5))
    v = vault.Vault({}, patents=[])
    vault.add_loose(v, tmp_path)
    # 하위 폴더도 모두 읽지만(11/05 '정량서류'·'회사자료' 폴더) 특허 건수는 경로에 '특허'가 든 파일만
    assert len(v.patents) == 3 and v.patent_src == "파일" and "files/기타폴더/무관.pdf" in v.files
    assert "patent" in AUTO_KINDS
    crit = [c for c in read_criteria([_score_doc(tmp_path)]) if c.kind == "patent"]
    got = score(crit, v, dt.date(2026, 11, 3))[0]
    assert got.score == 2.0 and "특허증 파일 수" in got.basis  # '2건 이상' 2점
    a = analyze([_score_doc(tmp_path)], v, dt.date(2026, 11, 3), tmp_path / "w")
    assert [p.path for p in a.picks if "특허" in p.section] == [f"files/특허증/(가상)특허{i}.pdf" for i in range(3)]


def test_bundle_includes_history_and_all_base_docs(tmp_path: Path):
    """11/05 사용자 요청: 통합본.pdf에 공고 관련 자격증·연혁·기타 회사 서류 모두 — 제출서류 목록에 없어도 기본 서류는 넣고,
    연혁 PDF가 없으면 연혁 표 한 장을 만들어 넣음. 공고 자격에 없는 직접생산 증명서는 안 넣음."""
    from pypdf import PdfReader

    from quantdocs.bundle import HISTORY, merge, pick

    files = {f"files/{n}": _blank_pdf() for n in ("사업자등록증.pdf", "법인등기부등본.pdf", "신용평가등급확인서.pdf", "책상(직접생산).pdf")}
    picks, _ = pick(list(files), ["사업자등록증 사본 1부"], [], with_history=True)
    got = [(p.section, p.path) for p in picks]
    assert got[0] == ("사업자등록증", "files/사업자등록증.pdf") and ("회사 연혁", HISTORY) in got
    assert ("법인등기부등본", "files/법인등기부등본.pdf") in got and ("신용평가등급확인서", "files/신용평가등급확인서.pdf") in got
    assert not any("책상" in p for _, p in got)
    v = vault.Vault(files, info={"회사명": "(가상)회사"}, history=[{"연월일": "2009.09", "내용": "(가상) 법인 설립"}])
    out, skipped = merge(picks, v, tmp_path / "b.pdf")
    if not any("글꼴" in s for s in skipped):  # 한글 글꼴이 있는 곳(서버 PC)에서만 연혁 표가 들어감
        assert "(가상) 법인 설립" in "".join(pg.extract_text() or "" for pg in PdfReader(out).pages)
    # 연혁 PDF가 있으면 표 대신 그 파일
    files["files/회사연혁.pdf"] = _blank_pdf()
    picks, _ = pick(list(files), [], [], with_history=True)
    assert ("회사 연혁", "files/회사연혁.pdf") in [(p.section, p.path) for p in picks]


def test_credit_rating_pdf_feeds_score_and_bundle(tmp_path: Path, monkeypatch):
    """11/05 사용자: 기업신용평가확인서 PDF를 폴더('정량서류' 등 하위 폴더)에 둠 — 등급·종류·평가일을 읽어 자기평점 경영상태에 쓰고, 통합본에도."""
    from quantdocs import importer
    from quantdocs.bundle import pick
    from quantdocs.importer import read_credit

    text = "기업신용평가등급 확인서\n업체명 (가상)회사\n신용등급 : BB+\n평가일자 : 2026. 06. 15\n유효기간 : 2026.06.15 ~ 2027.06.30"
    assert read_credit(text) == {"신용평가등급": "BB+", "신용평가 종류": "기업신용평가등급", "신용평가 평가일": "2026-06-15",
                                 "신용평가 유효기간": "2027-06-30"}
    assert read_credit("기업어음 신용평가\n평가등급\nA3+") ["신용평가 종류"] == "기업어음"
    assert read_credit("신용평가 안내문") == {}
    kcb = "신용평가등급확인서\n● 기업명 (가상)회사 기업신용평가등급\nBB+\n● 등급평가일 2026년 06월 16일\n● 유효기간 2027년 06월 15일"  # KCB 확인서 모양
    assert read_credit(kcb) == {"신용평가등급": "BB+", "신용평가 종류": "기업신용평가등급", "신용평가 평가일": "2026-06-16",
                                "신용평가 유효기간": "2027-06-15"}
    (tmp_path / "정량서류").mkdir()
    (tmp_path / "정량서류" / "기업신용평가확인서.pdf").write_bytes(_blank_pdf())
    v = vault.Vault({}, info={"신용평가등급": "B0"})
    vault.add_loose(v, tmp_path)
    assert "files/정량서류/기업신용평가확인서.pdf" in v.files
    monkeypatch.setattr(vault, "ENV_FILE", tmp_path / "none.env")
    monkeypatch.setattr(importer, "_cached_text", lambda path, data: text if "신용" in path else "")
    notes = importer.enrich_vault(v)
    assert v.info["신용평가등급"] == "BB+" and any("BB+" in n for n in notes)  # 확인서 값이 시트보다 먼저
    picks, _ = pick(list(v.files), [], [])
    assert [p.path for p in picks] == ["files/정량서류/기업신용평가확인서.pdf"]


def test_loose_folders_skip_duplicate_contents(tmp_path: Path):
    """11/05: '정량서류'·'회사자료' 폴더에 같은 서류가 겹침 — 내용이 같으면 이름이 달라도 한 번만(통합본에 두 번 안 들어가게)."""
    pdf = _blank_pdf()
    for d, n in (("정량서류", "사업자등록증.pdf"), ("회사자료/files", "사업자등록증 사본.pdf"), ("회사자료/files", "법인등기부등본.pdf")):
        (tmp_path / d).mkdir(parents=True, exist_ok=True)
        (tmp_path / d / n).write_bytes(pdf if "사업자" in n else _blank_pdf(2))
    v = vault.Vault({})
    vault.add_loose(v, tmp_path)
    assert sorted(v.files) == ["files/정량서류/사업자등록증.pdf", "files/회사자료/files/법인등기부등본.pdf"]


@needs_sample
def test_forms_found_in_other_attachment_when_rfp_has_none(tmp_path: Path, monkeypatch):
    """11/05 제보: '공고 자료에서 서식 목록을 찾지 못했습니다' — 공고문·제안요청서만 읽게 한 뒤 서식이 다른 첨부에 있는 공고.
    공고문·제안요청서에서 서식을 못 찾으면 나머지 첨부까지 읽어 찾음."""
    import shutil

    from quantdocs import web
    from quantdocs.g2b import NoticeFiles

    d = tmp_path / "n"
    d.mkdir()
    notice = _mini_hwpx(d / "공고문.hwpx", '<hp:p><hp:run charPrIDRef="0"><hp:t>(가상) 공고문 — 서식 없음</hp:t></hp:run></hp:p>')
    other = d / "과업지시서.hwpx"
    shutil.copyfile(FIXTURES / "jeongseon_culture_center_rfp.hwpx", other)
    monkeypatch.setattr(web, "_offline", lambda no, out: None)
    monkeypatch.setattr(web, "fetch_notice", lambda no, out: NoticeFiles(no, "000", "(가상) 공고", "가상", "용역", "협상에 의한 계약",
                                                                       "", "2026-08-20 10:00", [], [notice, other]))
    monkeypatch.setattr(web, "_git_pull", lambda: "")
    monkeypatch.setattr(web.vault_mod, "load", lambda: vault.Vault({}))
    monkeypatch.setattr(web, "JOBS", tmp_path / "jobs")
    job = {"id": "t1", "no": "R26BK0", "state": "running"}
    web._run_analyze(job)
    assert job["state"] == "ready", job.get("error")
    assert len(job["summary"]["forms"]) >= 20 and any("나머지 첨부에서 서식" in n for n in job["summary"]["notes"])
    assert "과업지시서.hwpx" in job["notice"]["files"]


def test_folders_by_kind_and_example_folder_skipped(tmp_path: Path):
    """11/05 사용자가 서류를 종류별 폴더로 나눔 — 파일 이름에 종류가 없어도 폴더 이름으로 통합본 규칙·연혁을 고르고, '예시_…' 폴더는 안 읽음."""
    from quantdocs.bundle import pick

    for d, n, pages in (("기업신용평가확인", "(27.6.15).pdf", 1), ("연혁", "회사소개.pdf", 2), ("경쟁입찰참가등록증", "스캔0001.pdf", 3),
                        ("예시_사업수행능력평가서", "남의평가서.pdf", 4)):
        (tmp_path / d).mkdir()
        (tmp_path / d / n).write_bytes(_blank_pdf(pages))
    v = vault.Vault({})
    vault.add_loose(v, tmp_path)
    assert not any("예시" in k for k in v.files)
    got = [(p.section, p.path) for p in pick(list(v.files), [], [], with_history=True)[0]]
    assert ("회사 연혁", "files/연혁/회사소개.pdf") in got
    assert ("신용평가등급확인서", "files/기업신용평가확인/(27.6.15).pdf") in got
    assert ("경쟁입찰참가자격등록증", "files/경쟁입찰참가등록증/스캔0001.pdf") in got


def test_orders_excel_in_folder_becomes_tracks(tmp_path: Path, monkeypatch):
    """11/05 사용자 요청: 공사수주현황 엑셀도 폴더에서 바로 실적목록으로 — 묶음 실적에 적어 둔 발주기관·지분율 등은 사업명으로 살림."""
    from openpyxl import Workbook

    from quantdocs import notice

    monkeypatch.setattr(notice, "track_field", lambda n: "전시·체험")
    (tmp_path / "공사수주현황").mkdir()
    wb = Workbook()
    ws = wb.active
    ws.append(["공사 수주 현황"])
    ws.append(["No.", "사업명", "계약종류", "사업기간", "계약금"])
    ws.append([1, "(가상) 전시관 조성", "용역", "2024.01.02~2024.06.30", 300_000_000])
    ws.append([2, "(가상) 새 체험관", "공사", "2025.03.01~2025.09.30", 120_000_000])
    ws.append(["", "합계", "", "", 420_000_000])
    wb.save(tmp_path / "공사수주현황" / "공사수주현황.xlsx")
    v = vault.Vault({}, tracks=[{"사업명": "(가상) 전시관  조성", "발주기관": "(가상)시", "지분율(%)": 60, "계약금액(원)": 1},
                                {"사업명": "(가상) 직접 더한 실적", "준공일": dt.date(2023, 1, 1)}])
    vault.add_loose(v, tmp_path)
    assert v.tracks_src == "공사수주현황/공사수주현황.xlsx"
    got = {t["사업명"]: t for t in v.tracks}
    assert got["(가상) 전시관 조성"]["발주기관"] == "(가상)시" and got["(가상) 전시관 조성"]["지분율(%)"] == 60
    assert got["(가상) 전시관 조성"]["계약금액(원)"] == 300_000_000 and got["(가상) 전시관 조성"]["준공일"] == dt.date(2024, 6, 30)
    assert "(가상) 새 체험관" in got and "(가상) 직접 더한 실적" in got and len(v.tracks) == 3


def test_pdf_form_table_grid_with_merged_cells():
    """11/05 사용자 결정 (다): PDF 서식 쪽 그림에서 표 칸 찾기 — 가로·세로 선으로 격자, 선이 없는 곳은 합친 칸, 회색 바탕 칸."""
    import numpy as np

    from quantdocs.pdfforms import find_tables

    g = np.full((900, 1200), 255, dtype=np.uint8)
    xs, ys = [100, 400, 700, 1000], [100, 200, 300, 600]
    for y in ys:
        g[y - 1:y + 2, 100:1001] = 0
    for x in xs:
        for r in range(3):
            if x == 400 and r == 2:  # 마지막 줄은 400 선이 없음 → 첫 두 칸 합침
                continue
            g[ys[r]:ys[r + 1] + 1, x - 1:x + 2] = 0
    g[110:190, 110:390] = 220  # 회색 이름표 칸
    tables, mask = find_tables(g)
    assert len(tables) == 1
    t = tables[0]
    assert len(t.xs) == 4 and len(t.ys) == 4
    assert (2, 0, 1, 2) in t.cells and (0, 0, 1, 1) in t.cells and len(t.cells) == 8
    assert (0, 0) in t.shade and (0, 1) not in t.shade and mask[ys[0], 500]


def test_pdf_form_rebuild_writes_cuttable_hwpx(tmp_path: Path, monkeypatch):
    """다시 만든 한글 서식은 '[서식 N] 제목' 머리 문단마다 잘리고(forms_cut.cut), 표 칸 글은 회사 정보 채우기가 그대로 씀."""
    import numpy as np
    from reportlab.pdfgen import canvas

    from quantdocs import pdfforms
    from quantdocs.fill import fill_hwpx
    from quantdocs.forms_cut import cut

    p = tmp_path / "rfp.pdf"
    c = canvas.Canvas(str(p))
    c.drawString(50, 800, "서식 2 일반현황")
    c.showPage()
    c.save()

    def fake_page(img, st, width, pb, uid, drop_head=False):  # 그림·OCR 대신 표 하나(회사명 | 빈칸)
        t = pdfforms.Table([100, 300, 600], [100, 200], [(0, 0, 1, 1), (0, 1, 1, 1)])
        t.words[(0, 0)] = [pdfforms.Word("회사명", 120, 130, 200, 160)]
        return pdfforms._table_xml(st, t, 1.0, uid), ["[서식 2] 일반현황"]
    monkeypatch.setattr(pdfforms, "page_xml", fake_page)
    monkeypatch.setattr(pdfforms, "form_pages", lambda data, texts: [0])  # 시험 PDF엔 한글 글꼴이 없어 '서식' 글자가 안 읽힘
    monkeypatch.setattr("quantdocs.importer._ocr_setup", lambda: object())
    out, notes, titles = pdfforms.rebuild(p, tmp_path / "f.hwpx", names={"2": "일반현황"})
    assert out and titles == {"2": "일반현황"} and "다시 만듦" in notes[-1]
    parts = cut(out, tmp_path / "cut", {"2": "일반현황"})
    assert [(x.no, x.title) for x in parts] == [("2", "일반현황")]
    fill_hwpx(tmp_path / "cut" / parts[0].file, tmp_path / "o.hwpx", vault.Vault({}, info={"회사명": "(가상)회사"}))
    assert "(가상)회사" in zipfile.ZipFile(tmp_path / "o.hwpx").read("Contents/section0.xml").decode()


def test_hidden_form_head_read_from_page_top(monkeypatch):
    """'[서식 7]' 머리가 PDF 글 정보에 없는 쪽 — 쪽 위만 OCR해 머리 줄을 붙임(11/05 창업진흥원 입찰보증금지급각서, OCR을 켰을 때)."""
    from quantdocs import importer, notice
    monkeypatch.setattr(notice, "NOTICE_OCR", True)

    pages = ["- 34 -\n서식 서식 [ 1]  제안서 표지제안서 표지\n제 안 서", "- 45 -\n제 14 조 운영위원회", "- 46 -\n입 찰 보 증 금 지 급 각 서\n1. 입찰건명 :"]
    seen = []

    def fake(data, idx, frac=0.15, dpi=300):
        seen.extend(idx)
        return {1: "한다.\n", 2: "[서직 7]\n\n입찰보증금지급각서\n"}
    monkeypatch.setattr(importer, "ocr_page_tops", fake)
    text = notice._add_hidden_heads(b"", pages, "\n".join(pages), "r.pdf")
    assert seen == [1, 2]
    assert "[서식 7] 입찰보증금지급각서\n- 46 -" in text
    assert "[서식 1]" not in text.split("- 45 -")[1]
    names = [f.no for f in notice.read_forms(text, "r.pdf", notice.load_catalog())]
    assert 7 in names or "7" in names


def test_hidden_form_head_guessed_without_ocr(monkeypatch):
    """OCR이 없어도 쪽 첫 줄이 '…각서' 제목이면 앞 서식 다음 번호로(11/05 — 서버 PC에서 서식 7이 여전히 안 보임)."""
    from quantdocs import importer, notice

    pages = ["- 28 -\n목차 서식 서식 [ 1]", "- 42 -\n서식 공동수급표준협정서서식 공동수급표준협정서[ 6] ( )\nㆍ",
             "- 43 -\nㆍ ㆍ", "- 46 -\n                 입 찰 보 증 금 지 급 각 서입 찰 보 증 금 지 급 각 서  \n  입찰건명 입찰건명 1. :"]
    monkeypatch.setattr(importer, "ocr_page_tops", lambda *a, **k: None)
    text = notice._add_hidden_heads(b"", pages, "\n".join(pages), "r.pdf")
    assert "[서식 7] 입찰보증금지급각서\n- 46 -" in text
    assert text.count("[서식 7]") == 1
    assert notice.guess_form_title("- 43 -\nㆍ ㆍ") is None


def test_bond_amount_filled_without_won_brackets(tmp_path):
    """'입찰금액 :      원 * 부가세 포함'·'입찰보증금액 :      원'·'입찰건명 :' — 금액 칸만 빈 모양도(11/05 창업진흥원 서식 7)."""
    from quantdocs import fill

    rep = fill.FillReport("x")
    xml = ("<hp:p><hp:run><hp:t>입찰보증금지급각서</hp:t></hp:run></hp:p>"
           "<hp:p><hp:run><hp:t>1. 입 찰 건명</hp:t></hp:run></hp:p>"
           "<hp:p><hp:run><hp:t>2. 입찰금액 :          원 * 부가세 포함</hp:t></hp:run></hp:p>"
           "<hp:p><hp:run><hp:t>3. 입찰보증금율 : 5%</hp:t></hp:run></hp:p>"
           "<hp:p><hp:run><hp:t>4. 입 찰 보 증 금 액 원</hp:t></hp:run></hp:p>")
    out = fill._fill_bond(xml, 90_000_000, rep, "스타트업 타운 설계 용역")
    plain = out.replace(fill._VS, "").replace(fill._VE, "")
    assert "입찰금액 : 90,000,000 원 * 부가세 포함" in plain
    assert f"입 찰 보 증 금 액 {int(90_000_000 * fill.BOND_RATE):,} 원" in plain
    assert "입 찰 건명 : 스타트업 타운 설계 용역" in plain
    assert "5%" in plain


def test_bid_name_label_cell_not_filled_twice():
    """11/06 필암서원 서식 5: '입 찰 건 명 :'만 든 표 칸엔 공고 이름을 끼워 넣지 않음 — 오른쪽 값 칸에 따로 들어가 두 번 보였음.
    표 밖 문단·여러 줄 칸의 '입찰건명 :'은 그대로 채움."""
    from quantdocs import fill

    title = "필암서원 집성관 디지털 콘텐츠 보강 기획 및 제작설치"
    head = "<hp:p><hp:run><hp:t>입찰보증금 지급 확약서</hp:t></hp:run></hp:p>"
    cell = lambda t: f"<hp:tc><hp:subList><hp:p><hp:run><hp:t>{t}</hp:t></hp:run></hp:p></hp:subList></hp:tc>"
    table = f"<hp:tbl><hp:tr>{cell('입 찰 건 명 :')}{cell(' ')}</hp:tr></hp:tbl>"
    out = fill._fill_bond(head + table, 400_000_000, fill.FillReport("x"), title)
    assert title not in out.replace(fill._VS, "").replace(fill._VE, "")
    # 표 밖 문단은 그대로 채움(창업진흥원 서식 7 모양)
    para = "<hp:p><hp:run><hp:t>1. 입찰건명 :</hp:t></hp:run></hp:p>"
    out = fill._fill_bond(head + para, 400_000_000, fill.FillReport("x"), title)
    assert f"입찰건명 : {title}" in out.replace(fill._VS, "").replace(fill._VE, "")


def test_ocr_sign_lines_restored_for_fill():
    """OCR이 쌍점을 빠뜨린 서명 줄 '대표자 (인)'·'주 소'·'Al ok 업체명'을 한글 서식 모양으로(11/05 — 한글 서식에서 채우는 값은 OCR 서식에서도)."""
    from quantdocs.pdfforms import _sign_line

    assert _sign_line("대표자 (인)").startswith("대표자 :") and _sign_line("대표자 (인)").endswith("(인)")
    assert _sign_line("주 소") == "주 소 : "
    assert _sign_line("Al ok 업체명") == "업체명 : "
    assert _sign_line("1. 입 찰 건명") == "1. 입 찰 건명 : "
    for keep in ("2026 년 월 일", "작성자 (연락처 휴대폰, e-mail)", "2. 제안서 내용 구성", "창업진흥원장 귀하"):
        assert _sign_line(keep) == keep


def test_institution_name_line_in_pledge_is_bidder(tmp_path):
    """각서 끝 '기관명 : / 대표자 : (인)' = 입찰자 회사(11/05 창업진흥원 서식 7)."""
    from quantdocs import fill, vault

    v = vault.Vault({}, info={"회사명": "(주)가상", "대표자": "홍길동"})
    rep = fill.FillReport("x")
    xml = ("<hp:p><hp:run><hp:t>입찰보증금지급각서</hp:t></hp:run></hp:p>"
           "<hp:p><hp:run><hp:t>기관명 : </hp:t></hp:run></hp:p>"
           "<hp:p><hp:run><hp:t>대표자 :                (인)</hp:t></hp:run></hp:p>")
    out = fill._fill_label_paragraphs(xml, v, rep).replace(fill._VS, "").replace(fill._VE, "")
    assert "기관명 : (주)가상" in out and "홍길동" in out


def test_hidden_form_head_title_when_ocr_spaces_letters(monkeypatch):
    """서버 OCR이 '[서식 7]' 뒤 제목을 '입 찰 보 증 …'처럼 띄어 읽거나 못 읽어도 목록에 이름이 들어감(11/05 서버 화면)."""
    from quantdocs import importer, notice
    monkeypatch.setattr(notice, "NOTICE_OCR", True)

    pages = ["- 28 -\n목차 서식 서식 [ 1]", "- 42 -\n서식 공동수급표준협정서서식 공동수급표준협정서[ 6] ( )\nㆍ",
             "- 46 -\n                 입 찰 보 증 금 지 급 각 서입 찰 보 증 금 지 급 각 서  \n  입찰건명 입찰건명 1. :"]
    for top in ("[서식 7]\n\n입 찰 보 증 금 지 급 각 서\n", "[서식 7]\n\nxr =\n"):
        monkeypatch.setattr(importer, "ocr_page_tops", lambda *a, top=top, **k: {2: top})
        text = notice._add_hidden_heads(b"", pages, "\n".join(pages), "r.pdf")
        assert "[서식 7] 입찰보증금지급각서\n" in text
        forms = {str(f.no): f.name for f in notice.read_forms(text, "r.pdf", notice.load_catalog())}
        assert "입찰보증금" in forms.get("7", "")


def test_license_groups_from_g2b_override_misread_attachment(tmp_path):
    """나라장터 면허제한 '[건축사사무소(4817)] 업종 또는 [실내건축공사업(4990)] 업종'(그룹 둘) — 첨부 글(OCR)에서 '또는'을 못 읽어
    둘 다 '필수'로 나와도 웹앱 수집처럼 그룹 구조로 '이 중 하나'(11/05 창업진흥원 제보)."""
    from quantdocs import g2b

    raw = [{"bidNtceNo": "R26BK0", "bidNtceOrd": "000", "lmtGrpNo": "1", "lmtSno": "1", "lcnsLmtNm": "건축사사무소/4817"},
           {"bidNtceNo": "R26BK0", "bidNtceOrd": "000", "lmtGrpNo": "2", "lmtSno": "1", "lcnsLmtNm": "실내건축공사업/4990"}]
    groups = g2b.license_groups(raw, "R26BK0")
    assert groups == [[["건축사사무소(4817)"]], [["실내건축공사업(4990)"]]]
    doc = tmp_path / "제안요청서.hwpx"
    import zipfile
    sec = ('<hs:sec xmlns:hs="s" xmlns:hp="p"><hp:p><hp:run><hp:t>2. 입찰참가자격</hp:t></hp:run></hp:p>'
           '<hp:p><hp:run><hp:t>가. 「국가를 당사자로 하는 계약에 관한 법률 시행령」 제12조에 따른 자격을 갖춘 자</hp:t></hp:run></hp:p>'
           '<hp:p><hp:run><hp:t>나. 건축사사무소(업종코드 4817)를 등록한 자</hp:t></hp:run></hp:p>'
           '<hp:p><hp:run><hp:t>다. 실내건축공사업(업종코드 4990)을 등록한 자</hp:t></hp:run></hp:p>'
           '<hp:p><hp:run><hp:t>3. 입찰 방법</hp:t></hp:run></hp:p></hs:sec>')
    with zipfile.ZipFile(doc, "w") as z:
        z.writestr("mimetype", "application/hwp+zip")
        z.writestr("Contents/section0.xml", sec)
    plain = g2b.qualification_rows([doc], {"4990"}, None, ["건축사사무소", "실내건축공사업"])
    assert [r["kind"] for r in plain if r["items"][0]["code"]] == ["필수", "필수"]  # 글만 보면 둘 다 필수(문서가 그렇게 적음)
    rows = g2b.qualification_rows([doc], {"4990"}, None, ["건축사사무소", "실내건축공사업"], groups)
    lic = [r for r in rows if any(i["code"] for i in r["items"])]
    assert len(lic) == 1 and lic[0]["kind"] == "이 중 하나"
    assert {i["code"] for i in lic[0]["items"]} == {"4817", "4990"}


def test_pdf_form_table_cells_cover_grid_once():
    """칸들이 격자를 빈틈·겹침 없이 덮어야 한글이 열림 — 'ㄱ'자 합친 칸은 한 칸씩, 칸이 시작하지 않는 줄은 합침(11/05 서식 7 오류)."""
    import numpy as np
    from quantdocs import pdfforms

    img = np.full((400, 600), 255, dtype=np.uint8)
    for y in (20, 120, 220, 320):
        img[y - 1:y + 2, 20:580] = 0
    for x in (20, 200, 400, 580):
        img[20:320, x - 1:x + 2] = 0
    img[121:219, 199:202] = 255  # 가운데 세로선 일부를 지워 (1,0)-(1,1) 합침
    img[219:222, 21:199] = 255  # (1,0)-(2,0) 합침 → (1,0),(1,1),(2,0) 'ㄱ'자
    tables, _ = pdfforms.find_tables(img)
    t = tables[0]
    R, C = len(t.ys) - 1, len(t.xs) - 1
    grid = [[0] * C for _ in range(R)]
    for r, c, rs, cs in t.cells:
        for rr in range(r, r + rs):
            for cc in range(c, c + cs):
                grid[rr][cc] += 1
    assert all(v == 1 for row in grid for v in row)
    assert {r for r, _, _, _ in t.cells} == set(range(R))
    assert pdfforms._compact([0, 10, 20], [0, 5, 10, 15], [(0, 0, 3, 1), (0, 1, 1, 1), (1, 1, 2, 1)]) == \
        ([0, 10, 20], [0, 5, 15], [(0, 0, 2, 1), (0, 1, 1, 1), (1, 1, 1, 1)])


def test_pdf_only_forms_need_hangul_file(tmp_path):
    """서식이 PDF에만 있는 공고는 '한글 파일 필요'만 표시 — OCR로 한글 서식을 다시 만들지 않음(11/05 사용자 결정)."""
    from types import SimpleNamespace
    from quantdocs import build

    f = lambda src: SimpleNamespace(source=src)
    assert build._hwp_needed([f("제안요청서.pdf")], [Path("공고문.pdf"), Path("제안요청서.pdf")]) is True
    assert build._hwp_needed([f("제안요청서.hwp")], [Path("제안요청서.hwp")]) is False  # 한글 파일은 있음(변환 실패는 따로 알림)
    assert build._hwp_needed([f("공고문.pdf"), f("서식.hwpx")], [Path("공고문.pdf"), Path("서식.hwpx")]) is False
    assert build._hwp_needed([], [Path("공고문.pdf")]) is False
    # 11/06 필암서원: 서식 목록은 나라장터 공고문 변환본 PDF에서 읽혔지만 제안요청서는 한글 — '한글 파일 필요' 아님
    assert build._hwp_needed([f("공고문.pdf")] * 3, [Path("공고문.hwpx"), Path("공고문.pdf"), Path("제안요청서.hwpx")]) is False
    assert "from .pdfforms import" not in open(build.__file__, encoding="utf-8").read()



def test_pagefit_only_fixes_forms_wasting_a_page():
    """11/06 사용자 요청: 서식마다 새 쪽은 그대로, 1~2줄·제목만 남아 쪽 하나가 낭비될 때만 손봄 — 빈 줄 하나로 → 표 줄 여유 줄이기 → 빈 줄 빼기
    → (제목만) 표 나누기. 종이 기준 그림 서식은 그림 아래 첫 내용 뒤의 빈 줄만."""
    from types import SimpleNamespace as L

    from quantdocs import pagefit as pf
    from quantdocs.fill import _Chars

    chars = _Chars('<hh:head><hh:charPr id="0" height="1000"></hh:charPr><hh:paraPr id="0"><hh:align horizontal="CENTER"/></hh:paraPr></hh:head>')
    body = round(257 * pf.MM)

    def p(t="", inner=""):
        run = f'<hp:run charPrIDRef="0"><hp:t>{t}</hp:t>{inner}</hp:run>' if t or inner else '<hp:run charPrIDRef="0"/>'
        return f'<hp:p paraPrIDRef="0" pageBreak="0">{run}</hp:p>'

    def tbl(rows):  # rows: 칸 높이(mm) — 글은 짧아 여유가 큼
        trs = "".join(f'<hp:tr><hp:tc><hp:subList><hp:p paraPrIDRef="0"><hp:run charPrIDRef="0"><hp:t>칸</hp:t></hp:run></hp:p></hp:subList>'
                      f'<hp:cellSz width="10000" height="{round(h * pf.MM)}"/></hp:tc></hp:tr>' for h in rows)
        return (f'<hp:tbl rowCnt="{len(rows)}" colCnt="2" textWrap="TOP_AND_BOTTOM" pageBreak="CELL"><hp:sz width="40000" height="{round(sum(rows) * pf.MM)}"/>'
                f'<hp:pos treatAsChar="1" affectLSpacing="0" vertRelTo="PARA" horzRelTo="PARA" horzAlign="LEFT" horzOffset="0"/>{trs}</hp:tbl>')

    mm = lambda x: round(x * pf.MM)
    # 넘침: 끝 한 줄만 다음 쪽
    f = pf._Form([p("〈서식 1〉"), p(), p(), p(), p("본문"), p("귀하")], False)
    lines = [[L(page=0, vertpos=0)], [L(page=0, vertpos=mm(10))], [L(page=0, vertpos=mm(20))], [L(page=0, vertpos=mm(30))],
             [L(page=0, vertpos=mm(40))], [L(page=1, vertpos=0)]]
    prob = pf._problem(f, lines, chars, body)
    assert prob[0] == "넘침"
    assert pf._step(f, prob, lines, chars, "", body) and len(f.paras) == 4  # 잇단 빈 줄 → 하나
    assert pf._step(f, prob, lines, chars, "", body) and len(f.paras) == 3 and "더 뺌" in f.done[-1]  # 표가 없어 줄이기는 건너뜀
    assert not pf._step(f, prob, lines, chars, "", body)  # 넘침엔 표 나누기 없음
    # 두 쪽이 다 차 있으면 손대지 않음
    full = [[L(page=0, vertpos=0)], [L(page=0, vertpos=mm(200))], [L(page=1, vertpos=mm(100))]]
    assert pf._problem(pf._Form([p("a"), p("b"), p("c")], False), full, chars, body) is None
    # 제목만: 표는 그대로 두고 쪽에서 나뉘게(제목 아래에서 시작, 넘치는 부분만 다음 쪽 — 11/06 사용자 결정), 가운데 문단이면 가운데
    g = pf._Form([p("〈서식 16〉"), p("", tbl([60, 60, 60, 70]))], False)
    gl = [[L(page=0, vertpos=0)], [L(page=1, vertpos=0)]]
    prob = pf._problem(g, gl, chars, body)
    assert prob[0] == "제목만"
    center = '<hh:paraPr id="0"><hh:align horizontal="CENTER"/></hh:paraPr>'
    assert pf._step(g, prob, gl, chars, center, body) and "나뉘게" in g.done[-1]
    assert 'pageBreak="TABLE"' in g.paras[1] and 'treatAsChar="0"' in g.paras[1] and 'horzAlign="CENTER"' in g.paras[1]
    assert 'height="' + str(mm(60)) + '"' in g.paras[1]  # 칸 높이는 그대로
    # 나눌 수 없는 표(표지 — 열 하나)는 칸 여유를 줄여 첫 쪽에 들어가게
    c = pf._Form([p("〈서식 11〉"), p("", tbl([60, 60, 60, 70]).replace('colCnt="2"', 'colCnt="1"'))], False)
    assert pf._step(c, ("제목만", mm(6), 1), gl, chars, center, body) and "표 칸 여유" in c.done[-1]
    assert 'pageBreak="TABLE"' not in c.paras[1]
    # 종이 기준 그림 서식: 그림 문단 다음 첫 내용 뒤의 빈 줄만
    fig = '<hp:line><hp:pos treatAsChar="0" vertRelTo="PAPER"/></hp:line>'
    k = pf._Form([p("〈서식 13〉"), p(), p(), p("", fig), p(), p("조직표"), p(), p(), p("주) 3.")], True)
    kl = [[L(page=0, vertpos=mm(i * 20))] for i in range(8)] + [[L(page=1, vertpos=0)]]
    assert pf._step(k, pf._problem(k, kl, chars, body), kl, chars, "", body)
    assert [pf._text(x) for x in k.paras] == ["〈서식 13〉", "", "", "", "", "조직표", "", "주) 3."]


def test_patents_only_in_scope_count_and_go_in_bundle():
    """11/06 사용자 요청: 보유기술 배점이 '※ 영상 제작 및 실감 콘텐츠 관련 보유 특허'만 세는데 보유 특허를 전부 셌음(필암 8건 → 2점).
    공고 인정 분야와 이름이 맞는 특허만 처음 체크·점수·통합본, 화면에서 고르면 그 특허만, 분야 문장이 없으면 전부."""
    from types import SimpleNamespace as NS

    from quantdocs.selfscore import Band, Criterion, _patent, patent_status

    c = Criterion("보유 전문기술 현황", 2.0, "patent")
    c.note = "※ 영상 제작 및 실감 콘텐츠 관련 보유 특허\n"
    c.bands = [Band(4, None, 2.0, "4건 이상"), Band(3, 4, 1.5, "3건 이상 ~ 4건 미만"), Band(2, 3, 1.0, "2건 이상 ~ 3건 미만"),
               Band(1, 2, 0.5, "1건 이상 ~ 2건 미만"), Band(0, 1, 0.0, "0건")]
    names = ["물의순환과정작동모형", "쇼케이스조명장", "정폭도형 체험기구", "치매환자의 기억", "실감형 VR 영상 체험장치"]
    v = NS(patents=[{"명칭": n, "등록번호": n, "파일": f"files/특허증/{n}.pdf"} for n in names], patent_src="파일")
    st = patent_status(c, v)
    assert [s["counted"] for s in st] == [False, False, False, False, True]
    v.patent_pick = {s["key"] for s in st if s["counted"]}
    assert _patent(c, v).score == 0.5
    v.patent_pick = set()  # 화면에서 모두 해제 → 0건
    assert _patent(c, v).score == 0.0
    v.patent_pick = None  # 고르기 없음(예전 동작) → 전부
    assert _patent(c, v).score == 2.0
    c2 = Criterion("보유기술", 2.0, "patent")  # 분야 문장이 없으면 모두 인정
    assert all(s["counted"] for s in patent_status(c2, v))


def test_build_survives_hwpx_that_is_not_zip(tmp_path: Path):
    """11/06 제보: 만들기 'File is not a zip file' — 이름만 .hwpx이고 속은 옛 한글(OLE)인 첨부를 그대로 자르다 멈춤.
    변환기가 없으면 알림만 남기고 나머지 첨부로 만들기는 끝까지."""
    from quantdocs.build import build

    v = vault.load(PW, vault.seal_folder(_company(tmp_path), PW, tmp_path / "c.enc"))
    fake = tmp_path / "붙임_서식.hwpx"
    fake.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 600)  # OLE(옛 한글) 머리
    out = build([FIXTURES / "jeongseon_culture_center_rfp.hwpx", fake], v, tmp_path / "o.zip", "정선", dt.date(2026, 8, 20))
    z = zipfile.ZipFile(out)
    assert "통합서식.hwpx" in z.namelist()
    assert "붙임_서식.hwpx" in z.read("읽어보기.txt").decode()


def test_guide_text_cell_gets_company_in_black(tmp_path: Path):
    """11/06 제보(입찰참가신청서): '상호 또는 법인명' 값 칸에 회색 안내 '공동도급참여시 대표사'가 있어 (주)지일을 안 넣었음 —
    안내 글뿐인 칸은 빈 칸으로 보고 회사명을 검은 글씨로."""
    from quantdocs.fill import fill_hwpx

    header = ('<hh:head><hh:charProperties itemCnt="2"><hh:charPr id="0" height="1000" textColor="#000000"></hh:charPr>'
              '<hh:charPr id="7" height="1200" textColor="#808080"><hh:italic/></hh:charPr></hh:charProperties></hh:head>')
    guide = ('<hp:tc><hp:subList><hp:p paraPrIDRef="0"><hp:run charPrIDRef="7"><hp:t>공동도급참여시 대표사</hp:t></hp:run></hp:p></hp:subList>'
             '<hp:cellAddr colAddr="1" rowAddr="0"/><hp:cellSpan colSpan="1" rowSpan="1"/></hp:tc>')
    src = _mini_hwpx(tmp_path / "g.hwpx", _tbl([[_c(0, 0, "상호 또는 법인명칭"), guide]]))
    with zipfile.ZipFile(src, "a") as z:
        z.writestr("Contents/header.xml", header)
    fill_hwpx(src, tmp_path / "go.hwpx", vault.Vault({}, info={"회사명": "(주)가상", "대표자": "홍길동"}))
    zo = zipfile.ZipFile(tmp_path / "go.hwpx")
    out, head = zo.read("Contents/section0.xml").decode(), zo.read("Contents/header.xml").decode()
    cid = re.search(r'<hp:run charPrIDRef="(\d+)"><hp:t>\(주\)가상', out).group(1)
    assert "공동도급참여시" not in out and cid != "7"
    block = re.search(rf'<hh:charPr id="{cid}".*?</hh:charPr>', head, re.S).group(0)
    assert 'textColor="#000000"' in block and "italic" not in block  # 이름표 칸(기본) 글자 모양으로 — 기울임·회색 안 물려받음


def test_proxy_groups_bond_won_sign_and_notice_no_paragraph(tmp_path: Path):
    """11/06 제보(청주 공항사거리 서식): ① 위임장 '대 표 자'(세로 묶음) 아래 '성 명'·'연 락 처'가 빔 — 대표자 묶음이면 대표자·전화,
    대리인 묶음은 업체명만 ② 각서 '○ 입찰보증금 : 금      원 (￦      )'(이름표가 앞 run에 따로)가 빔 ③ '○ 공 고 번 호 :' 빔
    ④ '○ 입 찰 건 명 : '(뒤 run에 이름이 이미 있음)에 이름이 한 번 더 들어감."""
    from quantdocs.fill import fill_hwpx

    def c(col, row, text, rs=1):
        return (f'<hp:tc><hp:subList><hp:p paraPrIDRef="0"><hp:run charPrIDRef="0"><hp:t>{text}</hp:t></hp:run></hp:p></hp:subList>'
                f'<hp:cellAddr colAddr="{col}" rowAddr="{row}"/><hp:cellSpan colSpan="1" rowSpan="{rs}"/></hp:tc>')
    proxy = ("<hp:p><hp:run><hp:tbl>"
             f"<hp:tr>{c(0, 0, '대 표 자', 2)}{c(1, 0, '성  명')}{c(2, 0, '')}</hp:tr>"
             f"<hp:tr>{c(1, 1, '연  락  처')}{c(2, 1, '')}</hp:tr>"
             f"<hp:tr>{c(0, 2, '대 리 인', 2)}{c(1, 2, '성  명')}{c(2, 2, '')}</hp:tr>"
             f"<hp:tr>{c(1, 3, '연  락  처')}{c(2, 3, '')}</hp:tr></hp:tbl></hp:run></hp:p>")
    pledge = ('<hp:p><hp:run charPrIDRef="0"><hp:t>입찰보증금 지급각서</hp:t></hp:run></hp:p>'
              '<hp:p><hp:run charPrIDRef="0"><hp:t>○ 공 고 번 호 :   </hp:t></hp:run></hp:p>'
              '<hp:p><hp:run charPrIDRef="0"><hp:t>○ 입 찰 건 명 : </hp:t></hp:run><hp:run charPrIDRef="1"><hp:t>가상 전시관 조성</hp:t></hp:run></hp:p>'
              '<hp:p><hp:run charPrIDRef="0"><hp:t>○ </hp:t></hp:run><hp:run charPrIDRef="1"><hp:t>입찰보증금</hp:t></hp:run>'
              '<hp:run charPrIDRef="0"><hp:t> : 금            원 (￦            )   </hp:t></hp:run></hp:p>')
    src = _mini_hwpx(tmp_path / "p.hwpx", proxy + pledge)
    fill_hwpx(src, tmp_path / "po.hwpx", vault.Vault({}, info={"회사명": "(주)가상", "대표자": "홍길동", "전화번호": "033-111-2222"}),
              title="가상 전시관 조성", g2b_no="R26BK00000001-000", basis=400000000)
    out = zipfile.ZipFile(tmp_path / "po.hwpx").read("Contents/section0.xml").decode().replace("", "").replace("", "")
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", out))
    assert "성 명 홍길동 연 락 처 033-111-2222 대 리 인 성 명 연 락 처" in text
    assert "공 고 번 호 : R26BK00000001-000" in text
    assert text.count("가상 전시관 조성") == 1
    assert "금 이천만 원 (￦ 20,000,000 )" in text


def test_plain_font_title_and_seal_glyph_signature(tmp_path: Path):
    """11/06 사용자 요청(청주 서식): 새로 기입하는 글은 늘 기본 글꼴 모양 — 서식에 굵게 적힌 입찰건명도 굵게 빼고,
    표 칸 안 '신청인 업체명' / '대표자      ㉿ (법인인감)'(도장 글자, 쌍점 없음) 서명 묶음에 회사명·대표자."""
    from quantdocs.fill import fill_hwpx

    header = ('<hh:head><hh:charProperties itemCnt="2"><hh:charPr id="0" height="1300" textColor="#000000"></hh:charPr>'
              '<hh:charPr id="9" height="1300" textColor="#000000"><hh:bold/></hh:charPr></hh:charProperties></hh:head>')
    body = ('<hp:p><hp:run charPrIDRef="0"><hp:t>입찰보증금 지급각서</hp:t></hp:run></hp:p>'
            '<hp:p><hp:run charPrIDRef="0"><hp:t>○ 입 찰 건 명 : </hp:t></hp:run><hp:run charPrIDRef="9"><hp:t>가상 전시관 조성</hp:t></hp:run></hp:p>'
            '<hp:p><hp:run><hp:tbl><hp:tr><hp:tc><hp:subList>'
            '<hp:p paraPrIDRef="0"><hp:run charPrIDRef="0"><hp:t>위와 같이 신청합니다.</hp:t></hp:run></hp:p>'
            '<hp:p paraPrIDRef="0"><hp:run charPrIDRef="0"><hp:t>          신청인 업체명</hp:t></hp:run></hp:p>'
            '<hp:p paraPrIDRef="0"><hp:run charPrIDRef="0"><hp:t>             대표자                  \U000F0099 (법인인감)</hp:t></hp:run></hp:p>'
            '</hp:subList><hp:cellAddr colAddr="0" rowAddr="0"/><hp:cellSpan colSpan="1" rowSpan="1"/></hp:tc></hp:tr></hp:tbl></hp:run></hp:p>')
    src = _mini_hwpx(tmp_path / "t.hwpx", body)
    with zipfile.ZipFile(src, "a") as z:
        z.writestr("Contents/header.xml", header)
    fill_hwpx(src, tmp_path / "to.hwpx", vault.Vault({}, info={"회사명": "(주)가상", "대표자": "홍길동"}), title="가상 전시관 조성")
    zo = zipfile.ZipFile(tmp_path / "to.hwpx")
    out, head = zo.read("Contents/section0.xml").decode(), zo.read("Contents/header.xml").decode()
    cid = re.search(r'<hp:run charPrIDRef="(\d+)"><hp:t>가상 전시관 조성</hp:t>', out).group(1)
    assert cid != "9" and "bold" not in re.search(rf'<hh:charPr id="{cid}".*?</hh:charPr>', head, re.S).group(0)
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", out))
    assert "신청인 업체명 (주)가상" in text and "대표자 홍길동" in text


def test_lead_and_joint_member_blocks(tmp_path: Path):
    """11/06 제보(청주 서식): '○ 대 표 사      업 체 명 :'(묶음 머리가 앞에 붙은 줄) 업체명이 빔, '○ 공동참여자' 묶음엔 지일 주소·대표자가
    들어감 — 머리는 떼고 이름표로 채우고, 공동참여자 묶음은 비움."""
    from quantdocs.fill import fill_hwpx

    def p(t):
        return f'<hp:p paraPrIDRef="0"><hp:run charPrIDRef="0"><hp:t>{t}</hp:t></hp:run></hp:p>'
    body = (p("2026년    월    일") + p("○ 대  표  사                       업 체 명 :") + p("                                    주    소 : ")
            + p("                                    대 표 자 :                      (인)") + p("")
            + p("○ 공동참여자                       업 체 명 :") + p("                                    주    소 : ")
            + p("                                    대 표 자 :                      (인)") + p("") + p("청주시장  귀하")
            + p("업 체 명 :") + p("대 표 자 :          (인)"))
    src = _mini_hwpx(tmp_path / "j.hwpx", body)
    fill_hwpx(src, tmp_path / "jo.hwpx", vault.Vault({}, info={"회사명": "(주)가상", "대표자": "홍길동", "주소": "강원 정선"}))
    out = zipfile.ZipFile(tmp_path / "jo.hwpx").read("Contents/section0.xml").decode().replace("", "").replace("", "")
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", out))
    lead, rest = text.split("○ 공동참여자")
    joint, tail = rest.split("청주시장")
    assert "업 체 명 : (주)가상" in lead and "주 소 : 강원 정선" in lead and "대 표 자 : 홍길동" in lead
    assert "(주)가상" not in joint and "홍길동" not in joint and "강원" not in joint
    assert "업 체 명 : (주)가상" in tail and "홍길동" in tail  # 묶음이 끝난 뒤 서명 줄은 그대로 채움


def test_unfilled_slots_finds_blank_company_labels():
    """11/06 사용자 결정: 채운 뒤에도 회사 정보 이름표가 빈 자리를 찾아 '확인 필요' 알림·검사 묶음에 씀 —
    다른 쪽이 적는 칸(대리인 묶음·공동참여자 묶음·발행자 줄)은 세지 않음."""
    from quantdocs.fill import unfilled_slots

    def c(col, row, text, rs=1):
        return (f'<hp:tc><hp:subList><hp:p><hp:run><hp:t>{text}</hp:t></hp:run></hp:p></hp:subList>'
                f'<hp:cellAddr colAddr="{col}" rowAddr="{row}"/><hp:cellSpan colSpan="1" rowSpan="{rs}"/></hp:tc>')

    def p(t):
        return f"<hp:p><hp:run><hp:t>{t}</hp:t></hp:run></hp:p>"
    xml = ("<hs:sec><hp:p><hp:run><hp:tbl>"
           f"<hp:tr>{c(0, 0, '상호 또는 법인명칭')}{c(1, 0, '')}</hp:tr>"
           f"<hp:tr>{c(0, 1, '대 리 인', 2)}{c(1, 1, '성 명')}{c(2, 1, '')}</hp:tr>"
           f"<hp:tr>{c(1, 2, '연 락 처')}{c(2, 2, '')}</hp:tr>"
           f"<hp:tr>{c(0, 3, '주 소')}{c(1, 3, '강원 정선')}</hp:tr></hp:tbl></hp:run></hp:p>"
           + p("○ 공 고 번 호 :   ") + p("○ 공동참여자          업 체 명 :") + p("      대 표 자 :        (인)") + p("청주시장 귀하")
           + p("대 표 자 :          (인)") + "</hs:sec>")
    got = unfilled_slots(xml)
    assert got == ["표: 상호또는법인명칭", "문단: 공고번호", "문단: 대표자"]
    assert unfilled_slots(xml, sign_only=True) == ["문단: 공고번호", "문단: 대표자"]


def test_signature_shapes_from_past_rfps(tmp_path: Path):
    """11/06 과거 제안요청서 검사에서 나온 빈칸 모양: ① '업체명 :      대표자 :      (인)' 한 줄 두 이름표 ② '서 약 자   업 체 명 :'·
    '제출자  상 호 :'(역할 이름이 앞에 붙은 줄) ③ '공동수급체 대표  상 호 :'는 채우고 '공동수급체 구성원  상 호 :' 아래는 비움
    ④ 직접 기입 서식(재직증명서)도 표 칸 안 회사 서명 묶음은 채움."""
    from quantdocs.fill import fill_hwpx

    def p(t):
        return f'<hp:p paraPrIDRef="0"><hp:run charPrIDRef="0"><hp:t>{t}</hp:t></hp:run></hp:p>'
    body = (p("2026년  월  일") + p("업체명 :            대표자 :            (인)") + p("") + p("귀하")
            + p("서 약 자        업   체   명 :") + p("                 대  표  자 :          (인)") + p("") + p("귀하")
            + p("공동수급체 대  표   상  호 :") + p("대표자:          (서명 또는 인)")
            + p("공동수급체 구성원   상  호 :") + p("대표자:          (서명 또는 인)"))
    src = _mini_hwpx(tmp_path / "s.hwpx", body)
    v = vault.Vault({}, info={"회사명": "(주)가상", "대표자": "홍길동", "주소": "강원 정선"})
    fill_hwpx(src, tmp_path / "so.hwpx", v)
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", zipfile.ZipFile(tmp_path / "so.hwpx").read("Contents/section0.xml").decode()
                                     .replace("", "").replace("", "")))
    assert "업체명 : (주)가상 대표자 : 홍길동" in text
    assert "서 약 자 업 체 명 : (주)가상" in text
    lead, member = text.split("공동수급체 구성원")
    assert "상 호 : (주)가상" in lead and "홍길동" in lead.split("공동수급체 대 표")[1]
    assert "(주)가상" not in member and "홍길동" not in member
    # ④ 재직증명서: 사람 칸(표)은 그대로, 같은 표 안 회사 서명 묶음 칸은 채움
    cert = ("<hp:p><hp:run><hp:tbl><hp:tr>" + _c(0, 0, "성 명") + _c(1, 0, "") + "</hp:tr><hp:tr>"
            '<hp:tc><hp:subList>' + p("위와 같이 재직 중임을 증명합니다.") + p("업 체 명 :") + p("주    소 :") + p("대 표 자 :            (인)")
            + '</hp:subList><hp:cellAddr colAddr="0" rowAddr="1"/><hp:cellSpan colSpan="2" rowSpan="1"/></hp:tc></hp:tr></hp:tbl></hp:run></hp:p>')
    src2 = _mini_hwpx(tmp_path / "c.hwpx", cert)
    fill_hwpx(src2, tmp_path / "co.hwpx", v, sign_only=True)
    t2 = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", zipfile.ZipFile(tmp_path / "co.hwpx").read("Contents/section0.xml").decode()
                                   .replace("", "").replace("", "")))
    assert "업 체 명 : (주)가상" in t2 and "주 소 : 강원 정선" in t2 and "대 표 자 : 홍길동" in t2
    assert t2.startswith(" 성 명 위와") or "성 명 위와" in t2  # 사람 칸은 비운 채


def test_fill_face_unified_and_big_label_size(tmp_path: Path):
    """11/10 사용자 요청(필암 5쪽): ① 채운 글 글꼴을 통합서식 첫 서식의 채운 글 글꼴로 통일(face) ② 이름표가 11pt보다 크면
    값도 이름표 크기('나'안 — 굵게는 뺌), 11pt보다 작으면 11pt."""
    from quantdocs.fill import fill_hwpx

    fonts = ('<hh:fontfaces><hh:fontface lang="HANGUL" fontCnt="2"><hh:font id="0" face="휴먼명조" type="TTF"/>'
             '<hh:font id="1" face="HY견명조" type="TTF"/></hh:fontface>'
             '<hh:fontface lang="LATIN" fontCnt="2"><hh:font id="0" face="휴먼명조" type="TTF"/><hh:font id="1" face="HY견명조" type="TTF"/>'
             '</hh:fontface></hh:fontfaces>')
    header = ('<hh:head>' + fonts + '<hh:charProperties itemCnt="2">'
              '<hh:charPr id="0" height="1000" textColor="#000000"><hh:fontRef hangul="0" latin="0"/></hh:charPr>'
              '<hh:charPr id="5" height="1500" textColor="#000000"><hh:fontRef hangul="1" latin="1"/><hh:bold/></hh:charPr>'
              '</hh:charProperties></hh:head>')
    body = ('<hp:p><hp:run charPrIDRef="0"><hp:t>보안서약서</hp:t></hp:run></hp:p>'
            '<hp:p><hp:run charPrIDRef="0"><hp:t>업 체 명 :</hp:t></hp:run></hp:p>'
            '<hp:p><hp:run charPrIDRef="5"><hp:t>서 약 자 :              </hp:t></hp:run></hp:p>')
    src = _mini_hwpx(tmp_path / "t.hwpx", body)
    with zipfile.ZipFile(src, "a") as z:
        z.writestr("Contents/header.xml", header)
    v = vault.Vault({}, info={"회사명": "(주)가상", "대표자": "홍길동"})

    def char_of(path, value):
        zo = zipfile.ZipFile(path)
        out, head = zo.read("Contents/section0.xml").decode(), zo.read("Contents/header.xml").decode()
        cid = re.search(rf'<hp:run charPrIDRef="(\d+)"><hp:t>{re.escape(value)}</hp:t>', out).group(1)
        return re.search(rf'<hh:charPr id="{cid}".*?</hh:charPr>', head, re.S).group(0)

    rep = fill_hwpx(src, tmp_path / "a.hwpx", v)               # 글꼴을 정하지 않으면 그 줄 글꼴 그대로
    big = char_of(tmp_path / "a.hwpx", "홍길동")
    assert 'height="1500"' in big and "bold" not in big and 'hangul="0"' in big   # 이름표 15pt → 값 15pt, 굵게 뺌, 글꼴은 늘 휴먼명조(11/11)
    assert 'height="1100"' in char_of(tmp_path / "a.hwpx", "(주)가상")          # 10pt 이름표 → 11pt
    assert rep.face == "휴먼명조"                                                  # 첫 값의 글꼴
    fill_hwpx(src, tmp_path / "b.hwpx", v, face="휴먼명조")    # 통합서식 첫 서식 글꼴로
    big = char_of(tmp_path / "b.hwpx", "홍길동")
    assert 'hangul="0"' in big and 'latin="0"' in big and 'height="1500"' in big


def test_multi_label_lines_and_sign_only_company(tmp_path: Path):
    """11/10 과거 제안요청서 검사 남은 3건: ① 한 줄 여러 이름표 '주    소 :      전화번호 :'(첫 묶음만 — 아래 같은 줄은 구성원 칸)
    ② '대표업체명:      대표자:      (인)' ③ 직접 기입 서식(재직증명서) 서명 묶음의 '회사명 :'."""
    from quantdocs.fill import fill_hwpx

    p = lambda t: f'<hp:p><hp:run charPrIDRef="0"><hp:t>{t}</hp:t></hp:run></hp:p>'
    body = (p("청 렴 서 약 서") + p("   회 사 명 :") + p("   주    소 :                                  전화번호 :")
            + p("   대 표 자 :               (인)") + p("   주    소 :                                  전화번호 :")
            + p("대표업체명:                 대표자:              (인)"))
    src = _mini_hwpx(tmp_path / "t.hwpx", body)
    v = vault.Vault({}, info={"회사명": "(주)가상", "대표자": "홍길동", "주소": "정선군 가상로 1", "전화번호": "033-000-0000"})
    fill_hwpx(src, tmp_path / "o.hwpx", v)
    text = zipfile.ZipFile(tmp_path / "o.hwpx").read("Contents/section0.xml").decode()
    lines = [re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", m)).strip() for m in re.findall(r"<hp:p\b.*?</hp:p>", text, re.S)]
    assert lines[2] == "주 소 : 정선군 가상로 1 전화번호 : 033-000-0000"
    assert lines[4] == "주 소 : 전화번호 :"                         # 같은 줄이 또 나오면(아래 묶음) 비움
    assert lines[5] == "대표업체명: (주)가상 대표자: 홍길동 (인)"

    body2 = (p("재 직 증 명 서") + '<hp:p><hp:run><hp:tbl><hp:tr><hp:tc><hp:subList>'
             + p("위 사실을 증명합니다.") + p("     회사명 :") + p("     주  소 :") + p("     대표자 :          (인)")
             + '</hp:subList><hp:cellAddr colAddr="0" rowAddr="0"/><hp:cellSpan colSpan="1" rowSpan="1"/></hp:tc></hp:tr></hp:tbl></hp:run></hp:p>')
    src2 = _mini_hwpx(tmp_path / "t2.hwpx", body2)
    fill_hwpx(src2, tmp_path / "o2.hwpx", v, sign_only=True)
    text2 = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", zipfile.ZipFile(tmp_path / "o2.hwpx").read("Contents/section0.xml").decode()))
    assert "회사명 : (주)가상" in text2 and "대표자 : 홍길동" in text2


def test_birth_and_proposal_notice_label():
    """11/10 경남교육청 입찰참가신청서: '입찰(제안)공고번호' 칸, 대표자 줄 '생년월일' 칸(대표자 주민번호 앞 6자리)."""
    from quantdocs.fill import _birth, _label_key
    assert _label_key("입찰(제안)공고번호") == "_notice_no" and _label_key("입찰공고번호") == "_notice_no"
    assert _birth("800101-1******") == "1980. 01. 01." and _birth("050302-3******") == "2005. 03. 02."
    assert _birth("") == "" and _birth("801301-1******") == ""


def test_applicant_signature_line(tmp_path: Path):
    """11/10 경남교육청 입찰참가신청서 끝 '신 청 인 :          (인)' — 대표자 이름. (인) 없는 '신청인 :'은 그대로."""
    from quantdocs.fill import fill_hwpx

    p = lambda t: f'<hp:p><hp:run charPrIDRef="0"><hp:t>{t}</hp:t></hp:run></hp:p>'
    src = _mini_hwpx(tmp_path / "t.hwpx", p("입찰참가신청서") + p("2026 .   .   .") + p("   신 청 인 :                      (인)") + p("   신청인 :"))
    fill_hwpx(src, tmp_path / "o.hwpx", vault.Vault({}, info={"회사명": "(주)가상", "대표자": "홍길동"}))
    text = zipfile.ZipFile(tmp_path / "o.hwpx").read("Contents/section0.xml").decode()
    lines = [re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", m)).strip() for m in re.findall(r"<hp:p\b.*?</hp:p>", text, re.S)]
    assert lines[2] == "신 청 인 : 홍길동 (인)" and lines[3] == "신청인 :"


def test_applicant_signature_line_keeps_width(tmp_path: Path):
    """11/11 '신 청 인 : 이윤봉' 뒤 (인)이 다음 줄로 — 이름을 넣어도 줄 너비(빈칸 단위)가 원래보다 늘지 않게.
    빈칸이 모자라면 앞 들여쓰기를 줄임."""
    from html import unescape

    from quantdocs.fill import _span, fill_hwpx

    p = lambda t: f'<hp:p><hp:run charPrIDRef="0"><hp:t>{t}</hp:t></hp:run></hp:p>'
    roomy = "        신 청 인 :              (인)"
    tight = "                    신 청 인 :     (인)"
    src = _mini_hwpx(tmp_path / "t.hwpx", p("입찰참가신청서") + p(roomy) + p(tight))
    fill_hwpx(src, tmp_path / "o.hwpx", vault.Vault({}, info={"회사명": "(주)가상", "대표자": "이윤봉"}))
    text = zipfile.ZipFile(tmp_path / "o.hwpx").read("Contents/section0.xml").decode()
    lines = [unescape(re.sub(r"<[^>]+>", "", m)) for m in re.findall(r"<hp:p\b.*?</hp:p>", text, re.S)]
    for before, after in ((roomy, lines[1]), (tight, lines[2])):
        assert re.sub(r"\s+", " ", after).strip() == "신 청 인 : 이윤봉 (인)"
        assert _span(after) <= _span(before), (before, after)


def test_pledge_line_with_preset_ceo_word(tmp_path: Path):
    """11/11 '서 약 자 : 업체 대표          (인)' — 대표 성명이 비었음. '업체 대표' 뒤에 이름, 줄 너비는 그대로."""
    from html import unescape

    from quantdocs.fill import _span, fill_hwpx

    p = lambda t: f'<hp:p><hp:run charPrIDRef="0"><hp:t>{t}</hp:t></hp:run></hp:p>'
    line = "     서 약 자 : 업체 대표                    (인)"
    src = _mini_hwpx(tmp_path / "t.hwpx", p("청렴계약 이행서약서") + p("2026년      월      일") + p(line))
    fill_hwpx(src, tmp_path / "o.hwpx", vault.Vault({}, info={"회사명": "(주)가상", "대표자": "이윤봉"}))
    text = zipfile.ZipFile(tmp_path / "o.hwpx").read("Contents/section0.xml").decode()
    lines = [unescape(re.sub(r"<[^>]+>", "", m)) for m in re.findall(r"<hp:p\b.*?</hp:p>", text, re.S)]
    assert re.sub(r"\s+", " ", lines[2]).strip() == "서 약 자 : 업체 대표 이윤봉 (인)"
    assert _span(lines[2]) <= _span(line)


def test_slash_signature_and_bid_rep_group():
    """11/10 경남교육청: 서약서 '업체명      / 대표자 :      (인)', 위임장 묶음 머리 '입찰 대표자', 'E-Mail' 이름표."""
    from quantdocs.fill import _label_key, _row_group, Cell
    assert _label_key("E-Mail") == "이메일"
    head = Cell(start=0, end=0, row=0, col=0, colspan=1, text="입찰 대표자", rowspan=3)
    name = Cell(start=0, end=0, row=1, col=1, colspan=1, text="성  명")
    assert _row_group([head, name], name) == "대표자"


def test_credit_labels_and_applicant_group():
    """11/10 경남교육청: 신용평가등급 확인서 칸 이름표, 실적증명서 '신청인' 묶음(직접 기입 서식이어도 채움)."""
    from quantdocs.fill import _label_key, _row_group, _value, Cell
    assert _label_key("등급평가일") == "신용평가 평가일" and _label_key("등급 유효기간") == "신용평가 유효기간"
    assert _label_key("기업신용 평가등급") == "신용평가등급"
    v = vault.Vault({}, info={"신용평가 평가일": "2026-06-16"})
    assert _value("신용평가 평가일", v) == "2026. 06. 16."
    head = Cell(start=0, end=0, row=0, col=0, colspan=1, text="신 청 인", rowspan=4)
    lab = Cell(start=0, end=0, row=1, col=1, colspan=1, text="대 표 자")
    assert _row_group([head, lab], lab) == "신청인"


def test_table_value_follows_label_font(tmp_path: Path):
    """11/10 사용자 요청: 표 칸 값은 옆 이름표 칸과 같은 글꼴·크기(이름표가 11pt보다 크면 그 크기) — 빈 칸의 기본 글꼴(굴림)을 따르지 않음."""
    from quantdocs.fill import fill_hwpx

    fonts = ('<hh:fontfaces><hh:fontface lang="HANGUL" fontCnt="2"><hh:font id="0" face="굴림" type="TTF"/><hh:font id="1" face="휴먼명조" type="TTF"/></hh:fontface>'
             '<hh:fontface lang="LATIN" fontCnt="2"><hh:font id="0" face="굴림" type="TTF"/><hh:font id="1" face="휴먼명조" type="TTF"/></hh:fontface></hh:fontfaces>')
    header = ('<hh:head>' + fonts + '<hh:charProperties itemCnt="2">'
              '<hh:charPr id="0" height="1000" textColor="#000000"><hh:fontRef hangul="0" latin="0"/></hh:charPr>'
              '<hh:charPr id="3" height="1200" textColor="#000000"><hh:fontRef hangul="1" latin="1"/></hh:charPr></hh:charProperties></hh:head>')
    cell = lambda c, t, cid: (f'<hp:tc><hp:subList><hp:p paraPrIDRef="0"><hp:run charPrIDRef="{cid}"><hp:t>{t}</hp:t></hp:run></hp:p></hp:subList>'
                              f'<hp:cellAddr colAddr="{c}" rowAddr="0"/><hp:cellSpan colSpan="1" rowSpan="1"/></hp:tc>')
    body = '<hp:p><hp:run><hp:tbl><hp:tr>' + cell(0, "상호", "3") + cell(1, "", "0") + '</hp:tr></hp:tbl></hp:run></hp:p>'
    src = _mini_hwpx(tmp_path / "t.hwpx", body)
    with zipfile.ZipFile(src, "a") as z:
        z.writestr("Contents/header.xml", header)
    fill_hwpx(src, tmp_path / "o.hwpx", vault.Vault({}, info={"회사명": "(주)가상"}))
    zo = zipfile.ZipFile(tmp_path / "o.hwpx")
    out, head = zo.read("Contents/section0.xml").decode(), zo.read("Contents/header.xml").decode()
    cid = re.search(r'<hp:run charPrIDRef="(\d+)"><hp:t>\(주\)가상</hp:t>', out).group(1)
    block = re.search(rf'<hh:charPr id="{cid}".*?</hh:charPr>', head, re.S).group(0)
    assert 'hangul="1"' in block and 'height="1200"' in block


def test_track_rows_share_header_font(tmp_path: Path):
    """11/11 사용자 제보: 실적 표 사업명 글꼴이 줄마다 다름 — 연번 '1'이 미리 적힌 줄은 연번 칸 글꼴, 빈 줄은 다른 글꼴.
    모든 줄이 열 머리('사업명') 글꼴을 따라야 함."""
    from quantdocs.fill import fill_hwpx

    fonts = ('<hh:fontfaces><hh:fontface lang="HANGUL" fontCnt="3"><hh:font id="0" face="굴림" type="TTF"/><hh:font id="1" face="휴먼명조" type="TTF"/>'
             '<hh:font id="2" face="돋움" type="TTF"/></hh:fontface>'
             '<hh:fontface lang="LATIN" fontCnt="3"><hh:font id="0" face="굴림" type="TTF"/><hh:font id="1" face="휴먼명조" type="TTF"/>'
             '<hh:font id="2" face="돋움" type="TTF"/></hh:fontface></hh:fontfaces>')
    header = ('<hh:head>' + fonts + '<hh:charProperties itemCnt="3">'
              '<hh:charPr id="0" height="1000" textColor="#000000"><hh:fontRef hangul="0" latin="0"/></hh:charPr>'
              '<hh:charPr id="3" height="1000" textColor="#000000"><hh:fontRef hangul="1" latin="1"/></hh:charPr>'
              '<hh:charPr id="4" height="1000" textColor="#000000"><hh:fontRef hangul="2" latin="2"/></hh:charPr></hh:charProperties></hh:head>')
    cell = lambda c, r, t, cid: (f'<hp:tc><hp:subList><hp:p paraPrIDRef="0"><hp:run charPrIDRef="{cid}"><hp:t>{t}</hp:t></hp:run></hp:p></hp:subList>'
                                 f'<hp:cellAddr colAddr="{c}" rowAddr="{r}"/><hp:cellSpan colSpan="1" rowSpan="1"/></hp:tc>')
    rows = [[cell(0, 0, "연번", "3"), cell(1, 0, "1. 사업명", "3"), cell(2, 0, "계약금액", "3")]]
    for r in range(1, 4):
        rows.append([cell(0, r, "1" if r == 1 else "", "4"), cell(1, r, "", "0"), cell(2, r, "", "0")])
    body = "<hp:p><hp:run><hp:tbl>" + "".join("<hp:tr>" + "".join(x) + "</hp:tr>" for x in rows) + "</hp:tbl></hp:run></hp:p>"
    src = _mini_hwpx(tmp_path / "t.hwpx", body)
    with zipfile.ZipFile(src, "a") as z:
        z.writestr("Contents/header.xml", header)
    import datetime as _dt
    tracks = [{"사업명": f"가상 전시관 {i}", "준공일": _dt.date(2025, 1, i), "계약금액(원)": 100_000_000} for i in range(1, 4)]
    fill_hwpx(src, tmp_path / "o.hwpx", vault.Vault({}, info={"회사명": "(주)가상"}), tracks=tracks)
    zo = zipfile.ZipFile(tmp_path / "o.hwpx")
    out, head = zo.read("Contents/section0.xml").decode(), zo.read("Contents/header.xml").decode()
    faces = set()
    for i in range(1, 4):
        cid = re.search(rf'<hp:run charPrIDRef="(\d+)"><hp:t>가상 전시관 {i}</hp:t>', out).group(1)
        block = re.search(rf'<hh:charPr id="{cid}".*?</hh:charPr>', head, re.S).group(0)
        faces.add(re.search(r'hangul="(\d+)"', block).group(1))
    assert faces == {"1"}, faces


def test_filled_value_always_plain_humanmyeongjo(tmp_path: Path):
    """11/11 사용자 결정: 값을 기입할 때는 일반 명조체(휴먼명조, 보통 굵기)로 고정 — 이름표가 굵은 굴림이어도.
    문서 글꼴 목록에 휴먼명조가 없으면 언어마다 끝 번호로 더함(두 번 더하지 않음)."""
    from quantdocs.fill import ensure_font, fill_hwpx

    fonts = ('<hh:fontfaces><hh:fontface lang="HANGUL" fontCnt="1"><hh:font id="0" face="굴림" type="TTF"/></hh:fontface>'
             '<hh:fontface lang="LATIN" fontCnt="1"><hh:font id="0" face="굴림" type="TTF"/></hh:fontface></hh:fontfaces>')
    header = ('<hh:head>' + fonts + '<hh:charProperties itemCnt="2">'
              '<hh:charPr id="0" height="1000" textColor="#000000"><hh:fontRef hangul="0" latin="0"/></hh:charPr>'
              '<hh:charPr id="3" height="1000" textColor="#000000"><hh:fontRef hangul="0" latin="0"/><hh:bold/></hh:charPr></hh:charProperties></hh:head>')
    assert ensure_font(ensure_font(header)) == ensure_font(header) and ensure_font(header).count('face="휴먼명조"') == 2
    cell = lambda c, t, cid: (f'<hp:tc><hp:subList><hp:p paraPrIDRef="0"><hp:run charPrIDRef="{cid}"><hp:t>{t}</hp:t></hp:run></hp:p></hp:subList>'
                              f'<hp:cellAddr colAddr="{c}" rowAddr="0"/><hp:cellSpan colSpan="1" rowSpan="1"/></hp:tc>')
    body = '<hp:p><hp:run><hp:tbl><hp:tr>' + cell(0, "상호", "3") + cell(1, "", "3") + '</hp:tr></hp:tbl></hp:run></hp:p>'
    src = _mini_hwpx(tmp_path / "t.hwpx", body)
    with zipfile.ZipFile(src, "a") as z:
        z.writestr("Contents/header.xml", header)
    fill_hwpx(src, tmp_path / "o.hwpx", vault.Vault({}, info={"회사명": "(주)가상"}))
    zo = zipfile.ZipFile(tmp_path / "o.hwpx")
    out, head = zo.read("Contents/section0.xml").decode(), zo.read("Contents/header.xml").decode()
    assert '<hh:font id="1" face="휴먼명조"' in head and 'fontCnt="2"' in head
    cid = re.search(r'<hp:run charPrIDRef="(\d+)"><hp:t>\(주\)가상</hp:t>', out).group(1)
    block = re.search(rf'<hh:charPr id="{cid}".*?</hh:charPr>', head, re.S).group(0)
    assert 'hangul="1"' in block and 'latin="1"' in block and "bold" not in block


def test_history_cell_left_aligned_with_hanging_indent(tmp_path: Path):
    """11/11 사용자 요청: 연혁 칸이 양식 문단(가운데 정렬)을 따라 날짜가 들쭉날쭉 — 왼쪽 정렬, 둘째 줄은 날짜 뒤 내용 시작에 맞춰 내어쓰기.
    이름표 문단('2. 주요 연혁')의 모양은 그대로."""
    from quantdocs.fill import fill_hwpx

    header = ('<hh:head><hh:fontfaces><hh:fontface lang="HANGUL" fontCnt="1"><hh:font id="0" face="휴먼명조" type="TTF"/></hh:fontface></hh:fontfaces>'
              '<hh:charProperties itemCnt="1"><hh:charPr id="0" height="1100" textColor="#000000"><hh:fontRef hangul="0"/></hh:charPr></hh:charProperties>'
              '<hh:paraProperties itemCnt="1"><hh:paraPr id="0"><hh:align horizontal="CENTER" vertical="BASELINE"/>'
              '<hh:margin><hh:intent value="0" unit="HWPUNIT"/><hh:left value="0" unit="HWPUNIT"/><hh:right value="0" unit="HWPUNIT"/>'
              '<hh:prev value="0" unit="HWPUNIT"/><hh:next value="0" unit="HWPUNIT"/></hh:margin></hh:paraPr></hh:paraProperties></hh:head>')
    cell = lambda c, r, t: (f'<hp:tc><hp:subList><hp:p paraPrIDRef="0"><hp:run charPrIDRef="0"><hp:t>{t}</hp:t></hp:run></hp:p></hp:subList>'
                            f'<hp:cellAddr colAddr="{c}" rowAddr="{r}"/><hp:cellSpan colSpan="1" rowSpan="1"/></hp:tc>')
    body = "<hp:p><hp:run><hp:tbl><hp:tr>" + cell(0, 0, "2. 주요 연혁(요약)") + "</hp:tr><hp:tr>" + cell(0, 1, "") + "</hp:tr></hp:tbl></hp:run></hp:p>"
    src = _mini_hwpx(tmp_path / "t.hwpx", body)
    with zipfile.ZipFile(src, "a") as z:
        z.writestr("Contents/header.xml", header)
    v = vault.Vault({}, info={"회사명": "(주)가상"}, history=[{"연월일": "2009.09", "내용": "(주)가상 법인설립"},
                                                            {"연월일": "2020.06", "내용": "아주 긴 정부 과제 선정"}])
    fill_hwpx(src, tmp_path / "o.hwpx", v)
    zo = zipfile.ZipFile(tmp_path / "o.hwpx")
    out, head = zo.read("Contents/section0.xml").decode(), zo.read("Contents/header.xml").decode()
    pid = re.search(r'<hp:p paraPrIDRef="(\d+)"><hp:run charPrIDRef="\d+"><hp:t>2009\.09  ', out).group(1)
    assert re.search(r'<hp:p paraPrIDRef="0"><hp:run charPrIDRef="0"><hp:t>2\. 주요 연혁', out)  # 이름표 문단은 그대로
    block = re.search(rf'<hh:paraPr id="{pid}".*?</hh:paraPr>', head, re.S).group(0)
    assert pid != "0" and 'horizontal="LEFT"' in block
    intent = int(re.search(r'<hh:intent value="(-?\d+)"', block).group(1))
    left = int(re.search(r'<hh:left value="(-?\d+)"', block).group(1))
    assert intent < 0 and left == 0  # 한글 내어쓰기(음수 intent) — 첫 줄은 그대로, 둘째 줄부터 날짜 너비만큼 들어감(11/05 '주 소 :'와 같은 방식)


def test_staff_each_points_layouts(tmp_path: Path):
    """11/11 사용자 제보: '특급기술자 | 각 0.5점'(칸 아래), 서식 '특급기술자 | 각0.5점 … 상한 점수 | 3점'을 못 읽어 기술인력 칸이 안 보였음.
    ① 본문 요약표 안 기준 ② '가. 사업수행능력 평가' 다음 같은 차례 '나. 기술능력 평가' ③ 자기평가표 서식 줄."""
    from quantdocs.fill import _cells
    from quantdocs.selfscore import read_criteria, read_form

    def c(col, row, t, cs=1, rs=1):
        return (f'<hp:tc><hp:subList><hp:p paraPrIDRef="0"><hp:run charPrIDRef="0"><hp:t>{t}</hp:t></hp:run></hp:p></hp:subList>'
                f'<hp:cellAddr colAddr="{col}" rowAddr="{row}"/><hp:cellSpan colSpan="{cs}" rowSpan="{rs}"/></hp:tc>')

    def para(t):
        return f'<hp:p paraPrIDRef="0"><hp:run charPrIDRef="0"><hp:t>{t}</hp:t></hp:run></hp:p>'
    staff = _tbl([[c(0, 0, "항목평가"), c(1, 0, "배점"), c(2, 0, "평가방법", 4)],
                  [c(0, 1, "전문인력", 1, 3), c(1, 1, "3.0", 1, 3), c(2, 1, "특급기술자"), c(3, 1, "고급기술자"), c(4, 1, "중급기술자"), c(5, 1, "초급기술자")],
                  [c(2, 2, "각 0.5점"), c(3, 2, "각 0.4점"), c(4, 2, "각 0.3점"), c(5, 2, "각 0.2점")],
                  [c(2, 3, "* 평점 = 인원×기술자별 점수 * 최고 배점 3점을 초과할 수 없음", 4)]])
    want = {"특급": 0.5, "고급": 0.4, "중급": 0.3, "초급": 0.2}
    for i, lead in enumerate((para("1. 정량적 평가 : 20점") + para("나. 기술능력 평가"),
                              para("가. 사업수행능력 평가") + para("나. 기술능력 평가"))):
        doc = _mini_hwpx(tmp_path / f"r{i}.hwpx", lead + staff)
        crit = [x for x in read_criteria([doc]) if x.kind == "staff"]
        assert crit and crit[0].per_grade == want and crit[0].points == 3.0, (i, crit)
    form = _tbl([[c(0, 0, "평가항목"), c(1, 0, "평가요소"), c(2, 0, "배점"), c(3, 0, "자기평점"), c(4, 0, "비고")],
                 [c(0, 1, "기술능력", 1, 5), c(1, 1, "특급기술자"), c(2, 1, "각0.5점"), c(3, 1, ""), c(4, 1, "")],
                 [c(1, 2, "고급기술자"), c(2, 2, "각0.4점"), c(3, 2, ""), c(4, 2, "")],
                 [c(1, 3, "중급기술자"), c(2, 3, "각0.3점"), c(3, 3, ""), c(4, 3, "")],
                 [c(1, 4, "초급기술자"), c(2, 4, "각0.2점"), c(3, 4, ""), c(4, 4, "")],
                 [c(1, 5, "상 한 점 수"), c(2, 5, "3점"), c(3, 5, ""), c(4, 5, "")]])
    xml = f"<hs:sec>{form}</hs:sec>"
    rows = read_form(_cells(xml, (0, len(xml))))
    row = next(r for r in rows if r.label == "기술능력")
    assert row.points == 3.0 and row.crit is not None and row.crit.kind == "staff" and row.crit.per_grade == want


def test_selfscore_nopoints_layout_pieces():
    """11/10 경남교육청 자체평가표: '3건 ~ 2건' 구간, '배점(점)' 머리."""
    from quantdocs.selfscore import parse_range
    assert parse_range("3건 ~ 2건") == (2.0, 4.0, "건") and parse_range("0건") == (0.0, 1.0, "건")


def test_spaced_rep_label_not_party_head(tmp_path: Path):
    """11/11 사용인감계 '대    표    자 :' — 글자를 띄운 '대 표'를 묶음 머리로 떼어 '자 :'만 남아 비었음(표 칸 안·표 밖 서명 줄 둘 다)."""
    from quantdocs.fill import fill_hwpx

    p = lambda t: f'<hp:p><hp:run charPrIDRef="0"><hp:t>{t}</hp:t></hp:run></hp:p>'
    cell = ('<hp:tc><hp:subList>' + p("주          소 :") + p("상호또는법인명 :") + p("대    표    자 :")
            + '</hp:subList><hp:cellAddr colAddr="1" rowAddr="1"/><hp:cellSpan colSpan="1" rowSpan="1"/></hp:tc>')
    tbl = "<hp:p><hp:run><hp:tbl><hp:tr>" + _c(0, 0, "사 용 인 감") + cell + "</hp:tr></hp:tbl></hp:run></hp:p>"
    body = (p("사 용 인 감 계") + tbl + p("2026년      월      일") + p("            주          소 : ")
            + p("            상호또는법인명 : ") + p("            대    표    자 :                     (인)"))
    fill_hwpx(_mini_hwpx(tmp_path / "t.hwpx", body), tmp_path / "o.hwpx",
              vault.Vault({}, info={"회사명": "(주)가상", "대표자": "이윤봉", "주소": "강원 정선"}))
    text = re.sub(r"<[^>]+>", "", zipfile.ZipFile(tmp_path / "o.hwpx").read("Contents/section0.xml").decode())
    assert text.count("이윤봉") == 2 and re.search(r"이윤봉 +\(인\)", text)


def test_seal_on_next_line_moves_up(tmp_path: Path):
    """11/11 청렴계약 이행서약서 — '서  약  자 :' 아래 문단이 '(인)'뿐(원본 서식). 이름 줄로 올리고 아래 줄은 빈 줄로(사용자 결정)."""
    from quantdocs.fill import fill_hwpx

    p = lambda t: f'<hp:p><hp:run charPrIDRef="0"><hp:t>{t}</hp:t></hp:run></hp:p>'
    body = p("청렴계약 이행서약서") + p("2026.    .    .") + p("                서  약  자 : ") + p(" " * 54 + "(인)") + p("본문 끝")
    fill_hwpx(_mini_hwpx(tmp_path / "t.hwpx", body), tmp_path / "o.hwpx", vault.Vault({}, info={"회사명": "(주)가상", "대표자": "이윤봉"}))
    xml = zipfile.ZipFile(tmp_path / "o.hwpx").read("Contents/section0.xml").decode()
    paras = [re.sub(r"<[^>]+>", "", m) for m in re.findall(r"<hp:p\b[^>]*>(.*?)</hp:p>", xml, re.S)]
    i = next(k for k, t in enumerate(paras) if "서  약  자" in t)
    assert re.search(r"서  약  자 : 이윤봉 +\(인\)", paras[i]) and paras[i + 1].strip() == "" and paras[i + 2] == "본문 끝"


def test_office_address_with_paren_label(tmp_path: Path):
    """11/11 필암 서식 17 실적증명서 — 두 줄 이름표 '영 업 소 재 지 / ( 주    소 )' 옆 칸이 비었음(괄호까지 붙여 9자라 이름표로 못 봄)."""
    from quantdocs.fill import _cells, _spans, fill_hwpx

    two = ('<hp:tc><hp:subList><hp:p paraPrIDRef="0"><hp:run charPrIDRef="0"><hp:t>영 업 소 재 지</hp:t></hp:run></hp:p>'
           '<hp:p paraPrIDRef="0"><hp:run charPrIDRef="0"><hp:t>(  주          소  )</hp:t></hp:run></hp:p></hp:subList>'
           '<hp:cellAddr colAddr="0" rowAddr="1"/><hp:cellSpan colSpan="1" rowSpan="1"/></hp:tc>')
    body = _tbl([[_c(0, 0, "업 체 명 ( 상 호 )"), _c(1, 0, "")], [two, _c(1, 1, "")]])
    fill_hwpx(_mini_hwpx(tmp_path / "t.hwpx", body), tmp_path / "o.hwpx",
              vault.Vault({}, info={"회사명": "(주)가상", "주소": "가상시 가상로 1"}))
    xml = zipfile.ZipFile(tmp_path / "o.hwpx").read("Contents/section0.xml").decode()
    got = {(c.row, c.col): c.text for c in _cells(xml, _spans(xml, "tbl")[0])}
    assert got[(0, 1)] == "(주)가상" and got[(1, 1)] == "가상시 가상로 1"


def test_tel_with_email_label(tmp_path: Path):
    """11/11 '접수자전화(전자우편)' 칸에 전화만 들어갔음 — 전화 + (이메일), 이메일이 없으면 전화만."""
    from quantdocs.fill import _cells, _spans, _label_key, fill_hwpx

    assert _label_key("접수자전화(전자우편)") == "_tel_mail" and _label_key("전화(E-mail)") == "_tel_mail"
    assert _label_key("전자우편") == "이메일" and _label_key("전화번호") == "전화번호"
    body = _tbl([[_c(0, 0, "접수자전화"), _c(1, 0, "")], [_c(0, 1, "(전자우편)"), _c(1, 1, "")],
                 [_c(0, 2, "접수자전화(전자우편)"), _c(1, 2, "")]])
    for info, want in (({"전화번호": "033-111-2222", "이메일": "a@b.kr"}, "033-111-2222 (a@b.kr)"),
                       ({"전화번호": "033-111-2222"}, "033-111-2222")):
        fill_hwpx(_mini_hwpx(tmp_path / "t.hwpx", body), tmp_path / "o.hwpx", vault.Vault({}, info=info))
        xml = zipfile.ZipFile(tmp_path / "o.hwpx").read("Contents/section0.xml").decode()
        got = {(c.row, c.col): c.text for c in _cells(xml, _spans(xml, "tbl")[0])}
        assert got[(2, 1)] == want
