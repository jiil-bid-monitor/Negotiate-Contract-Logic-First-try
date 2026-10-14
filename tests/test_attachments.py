"""첨부파일 텍스트 추출 단위 테스트. 표준 라이브러리 unittest만 사용한다.

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import io
import sys
import tempfile
import unittest
import unittest.mock
import zipfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nego.attachments import (  # noqa: E402
    AttachmentError,
    _decode_para_text,
    _hwp_section_paragraphs,
    _hwpx_section_text,
    _sniff_ext,
    extract_text,
    fetch_attachment_text,
    redact_personal_contacts,
    save_attachment_texts,
)
from nego.models import notice_from_raw  # noqa: E402
from nego.pipeline import Candidate  # noqa: E402
from nego.qualify import JointSupply, LicenseGroup, QualificationResult  # noqa: E402
from nego.screen import ScreenResult, Schedule  # noqa: E402

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "attachments"


def _build_pdf(text: str) -> bytes:
    """텍스트 추출 검증용 최소 PDF. 표준 Helvetica만 쓰므로 폰트 임베딩이 필요 없다."""
    content = f"BT /F1 24 Tf 10 100 Td ({text}) Tj ET".encode("latin-1")
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
    ]

    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = [0]
    for i, body in enumerate(objs, start=1):
        offsets.append(out.tell())
        out.write(f"{i} 0 obj".encode() + b"\n" + body + b"\nendobj\n")
    xref_offset = out.tell()
    n = len(objs) + 1
    out.write(f"xref\n0 {n}\n".encode())
    out.write(b"0000000000 65535 f \n")
    for off in offsets[1:]:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(b"trailer\n<< /Size " + str(n).encode() + b" /Root 1 0 R >>\n")
    out.write(b"startxref\n" + str(xref_offset).encode() + b"\n%%EOF")
    return out.getvalue()


def _build_pdf_pages(texts: list[str]) -> bytes:
    """여러 페이지짜리 최소 PDF. 한 페이지 추출 실패 시 나머지 보존 테스트용."""
    objs: list[bytes] = []

    def add(body: bytes) -> int:
        objs.append(body)
        return len(objs)

    catalog_num = add(b"")  # 자리만 예약, 아래서 채움
    pages_num = add(b"")
    font_num = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    page_nums = []
    for text in texts:
        content = f"BT /F1 24 Tf 10 100 Td ({text}) Tj ET".encode("latin-1")
        content_num = add(b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream")
        page_num = add(
            b"<< /Type /Page /Parent "
            + str(pages_num).encode()
            + b" 0 R /MediaBox [0 0 200 200] /Resources << /Font << /F1 "
            + str(font_num).encode()
            + b" 0 R >> >> /Contents "
            + str(content_num).encode()
            + b" 0 R >>"
        )
        page_nums.append(page_num)

    objs[catalog_num - 1] = b"<< /Type /Catalog /Pages " + str(pages_num).encode() + b" 0 R >>"
    kids = " ".join(f"{n} 0 R" for n in page_nums).encode()
    objs[pages_num - 1] = b"<< /Type /Pages /Kids [" + kids + b"] /Count " + str(len(page_nums)).encode() + b" >>"

    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = [0]
    for i, body in enumerate(objs, start=1):
        offsets.append(out.tell())
        out.write(f"{i} 0 obj".encode() + b"\n" + body + b"\nendobj\n")
    xref_offset = out.tell()
    n = len(objs) + 1
    out.write(f"xref\n0 {n}\n".encode())
    out.write(b"0000000000 65535 f \n")
    for off in offsets[1:]:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(b"trailer\n<< /Size " + str(n).encode() + b" /Root 1 0 R >>\n")
    out.write(b"startxref\n" + str(xref_offset).encode() + b"\n%%EOF")
    return out.getvalue()


def _build_pdf_with_broken_cid_font() -> bytes:
    """실측 pypdf 버그를 그대로 재현한 3페이지 PDF.

    1페이지: 정상 Helvetica. 2페이지: /Subtype이 /Type0인데 /DescendantFonts가
    없는 비정상 폰트(ToUnicode CMap으로 "AB"를 인코딩) — pypdf가 이 폰트를
    만나면 KeyError('/DescendantFonts')를 던진다(_font.py). 3페이지: 다시
    정상 Helvetica. 세 페이지 모두 성공적으로 뽑혀야 실제 버그가 고쳐진 것이다
    (건너뛰는 게 아니라 진짜로 파싱됨).
    """
    cmap_stream = (
        b"/CIDInit /ProcSet findresource begin\n12 dict begin\nbegincmap\n"
        b"1 begincodespacerange\n<0000> <FFFF>\nendcodespacerange\n"
        b"2 beginbfchar\n<0041> <0041>\n<0042> <0042>\nendbfchar\nendcmap\nend\nend\n"
    )

    objs: list[bytes] = []

    def add(body: bytes) -> int:
        objs.append(body)
        return len(objs)

    catalog_num = add(b"")
    pages_num = add(b"")
    helvetica_num = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    cmap_num = add(b"<< /Length " + str(len(cmap_stream)).encode() + b" >>\nstream\n" + cmap_stream + b"\nendstream")
    # 실측 버그의 핵심: Type0인데 /DescendantFonts가 없다.
    broken_font_num = add(
        b"<< /Type /Font /Subtype /Type0 /BaseFont /BrokenCID /Encoding /Identity-H /ToUnicode "
        + str(cmap_num).encode()
        + b" 0 R >>"
    )

    def add_page(font_num: int, content: bytes) -> int:
        content_num = add(b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream")
        return add(
            b"<< /Type /Page /Parent "
            + str(pages_num).encode()
            + b" 0 R /MediaBox [0 0 200 200] /Resources << /Font << /F1 "
            + str(font_num).encode()
            + b" 0 R >> >> /Contents "
            + str(content_num).encode()
            + b" 0 R >>"
        )

    page1 = add_page(helvetica_num, b"BT /F1 24 Tf 10 100 Td (Region limit page) Tj ET")
    page2 = add_page(broken_font_num, b"BT /F1 24 Tf 10 100 Td <00410042> Tj ET")
    page3 = add_page(helvetica_num, b"BT /F1 24 Tf 10 100 Td (Joint supply page) Tj ET")

    objs[catalog_num - 1] = b"<< /Type /Catalog /Pages " + str(pages_num).encode() + b" 0 R >>"
    kids = f"{page1} 0 R {page2} 0 R {page3} 0 R".encode()
    objs[pages_num - 1] = b"<< /Type /Pages /Kids [" + kids + b"] /Count 3 >>"

    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = [0]
    for i, body in enumerate(objs, start=1):
        offsets.append(out.tell())
        out.write(f"{i} 0 obj".encode() + b"\n" + body + b"\nendobj\n")
    xref_offset = out.tell()
    n = len(objs) + 1
    out.write(f"xref\n0 {n}\n".encode())
    out.write(b"0000000000 65535 f \n")
    for off in offsets[1:]:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(b"trailer\n<< /Size " + str(n).encode() + b" /Root 1 0 R >>\n")
    out.write(b"startxref\n" + str(xref_offset).encode() + b"\n%%EOF")
    return out.getvalue()


def _build_hwpx(paragraphs: list[str]) -> bytes:
    """지역/면허/공동수급 원문 대조에 실제로 쓰일 문단 구조를 재현한 최소 HWPX."""
    body = "".join(f"<hp:p><hp:run><hp:t>{p}</hp:t></hp:run></hp:p>" for p in paragraphs)
    section_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<hp:sec xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph">'
        f"{body}</hp:sec>"
    ).encode("utf-8")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("mimetype", "application/hwp+zip")
        zf.writestr("Contents/section0.xml", section_xml)
    return buf.getvalue()


def _build_hwp_record(tag_id: int, payload: bytes, level: int = 0) -> bytes:
    size = len(payload)
    if size < 0xFFF:
        header = (size << 20) | (level << 10) | tag_id
        return header.to_bytes(4, "little") + payload
    header = (0xFFF << 20) | (level << 10) | tag_id
    return header.to_bytes(4, "little") + size.to_bytes(4, "little") + payload


class FakeResponse:
    def __init__(self, content: bytes, status_code: int = 200):
        self.content = content
        self.status_code = status_code
        self.text = ""


class FakeSession:
    """requests.Session 흉내. 고정된 바이트만 돌려준다."""

    def __init__(self, content: bytes, status_code: int = 200):
        self._content = content
        self._status_code = status_code

    def get(self, url, timeout=None):
        return FakeResponse(self._content, self._status_code)


class TestPdfExtraction(unittest.TestCase):
    def test_extracts_plain_text(self):
        data = _build_pdf("Hello Nego")
        text = extract_text(data, "pdf")
        self.assertIn("Hello Nego", text)

    def test_broken_pdf_raises_attachment_error(self):
        with self.assertRaises(AttachmentError):
            extract_text(b"not a pdf", "pdf")

    def test_recovers_real_text_from_font_missing_descendant_fonts(self):
        """실측 버그의 근본 수정 검증 (건너뛰기가 아니라 실제 파싱).

        pypdf(_font.py)는 Type1/TrueType/Type3가 아닌 폰트는 무조건
        /DescendantFonts가 있다고 가정하고 바로 인덱싱한다 — 나라장터 첨부
        PDF 일부가 이 키 없는 Type0 폰트를 써서 KeyError로 문서 전체가
        실패했었다. `_patch_missing_descendant_fonts`가 빈 배열을 채워
        넣으면, 그 정보는 글자 폭(레이아웃) 계산에만 쓰이므로 실제
        텍스트(ToUnicode CMap으로 디코딩된 "AB")는 정상적으로 뽑혀야 한다.
        """
        data = _build_pdf_with_broken_cid_font()
        text = extract_text(data, "pdf")

        self.assertIn("Region limit page", text)
        self.assertIn("AB", text)  # 깨진 폰트 페이지도 실제로 파싱됨 — 건너뛴 게 아님
        self.assertIn("Joint supply page", text)
        self.assertNotIn("텍스트 추출 실패", text)

    def test_unexpected_per_page_failure_is_shown_not_silently_dropped(self):
        """패치로도 못 살리는 다른 원인의 실패는, 조용히 빼지 않고 표시만 남긴다."""
        data = _build_pdf_pages(["Region limit page", "Broken page", "Joint supply page"])
        with unittest.mock.patch(
            "pypdf._page.PageObject.extract_text",
            side_effect=["Region limit page", RuntimeError("무언가 다른 원인"), "Joint supply page"],
        ):
            text = extract_text(data, "pdf")

        self.assertIn("Region limit page", text)
        self.assertIn("Joint supply page", text)
        self.assertIn("2페이지 텍스트 추출 실패", text)
        self.assertIn("원본 파일에서 직접 확인", text)

    def test_all_pages_failing_raises_attachment_error(self):
        data = _build_pdf_pages(["Page one", "Page two"])
        with unittest.mock.patch(
            "pypdf._page.PageObject.extract_text",
            side_effect=RuntimeError("무언가 다른 원인"),
        ):
            with self.assertRaises(AttachmentError):
                extract_text(data, "pdf")


class TestExtensionMismatchSniffing(unittest.TestCase):
    """실측: 나라장터 첨부파일이 파일명 확장자와 실제 내용이 다른 경우가 있다
    (.hwpx로 등록됐지만 실제론 구버전 OLE2 .hwp 바이너리 — "File is not a
    zip file"로 실패). 확장자보다 실제 매직 바이트를 믿어야 한다."""

    def test_sniff_detects_pdf_zip_and_ole_magic_bytes(self):
        self.assertEqual(_sniff_ext(b"%PDF-1.4\n..."), "pdf")
        # zip 형식은 안에 Contents/section*.xml이 있어야 HWPX, 아니면 일반 압축 파일(zip)
        self.assertEqual(_sniff_ext(b"PK\x03\x04" + b"\x00" * 10), "zip")
        self.assertEqual(_sniff_ext(_zip_bytes({"Contents/section0.xml": b"<x/>", "mimetype": b"application/hwp+zip"})), "hwpx")
        self.assertEqual(_sniff_ext(_zip_bytes({"공고문.pdf": b"%PDF-1.4"})), "zip")
        self.assertEqual(_sniff_ext(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 10), "hwp")

    def test_sniff_returns_none_for_unrecognized_bytes(self):
        self.assertIsNone(_sniff_ext(b"just some random text"))

    def test_pdf_content_labeled_hwpx_is_still_extracted(self):
        """확장자는 .hwpx인데 실제 내용이 PDF인 경우 — 내용 기준으로 재분류해
        정상 추출돼야 한다(확장자만 보고 hwpx 파서를 강제하면 zip 파싱 실패).

        표준 Helvetica 폰트는 한글을 못 그리므로 ASCII로 검증한다
        (test_successful_pdf_round_trip과 같은 이유).
        """
        data = _build_pdf("Actually PDF")
        text = extract_text(data, "hwpx")
        self.assertIn("Actually PDF", text)

    def test_hwpx_content_labeled_hwp_is_still_extracted(self):
        data = _build_hwpx(["실제로는 HWPX"])
        text = extract_text(data, "hwp")
        self.assertEqual(text, "실제로는 HWPX")


class TestHwpxExtraction(unittest.TestCase):
    def test_extracts_paragraphs_in_order(self):
        data = _build_hwpx(["참가가능지역: 강원특별자치도", "공동수급 허용"])
        text = extract_text(data, "hwpx")
        self.assertEqual(text, "참가가능지역: 강원특별자치도\n공동수급 허용")

    def test_missing_section_raises_attachment_error(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("mimetype", "application/hwp+zip")
        with self.assertRaises(AttachmentError):
            extract_text(buf.getvalue(), "hwpx")

    def test_not_a_zip_raises_attachment_error(self):
        with self.assertRaises(AttachmentError):
            extract_text(b"not a zip", "hwpx")

    def test_section_text_joins_runs_within_paragraph(self):
        xml = (
            b'<?xml version="1.0"?>'
            b'<hp:sec xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph">'
            b"<hp:p><hp:run><hp:t>\xea\xb3\xb5\xeb\x8f\x99 </hp:t></hp:run>"
            b"<hp:run><hp:t>\xed\x97\x88\xec\x9a\xa9</hp:t></hp:run></hp:p>"
            b"</hp:sec>"
        )
        self.assertEqual(_hwpx_section_text(xml), "공동 허용")

    def test_table_renders_as_rows_and_cells_not_a_single_jammed_line(self):
        """평가기준 배점표 재현: 표는 행=줄바꿈, 셀=' | '로 나와야 한다.

        예전엔 root.iter()로 전부 훑어서 표 안 문단이 상위 문단과 합쳐져
        구분자 없이 한 줄로 뭉쳐지고, 동시에 독립 문단으로도 다시 잡혀
        중복까지 됐다 (실제 정선군 제안요청서 배점표에서 발견됨).
        """
        xml = (
            b'<?xml version="1.0"?>'
            b'<hp:sec xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph">'
            b"<hp:p><hp:run><hp:t>1. \xed\x8f\x89\xea\xb0\x80\xea\xb8\xb0\xec\xa4\x80</hp:t>"
            b'<hp:tbl><hp:tr>'
            b"<hp:tc><hp:subList><hp:p><hp:run><hp:t>\xea\xb5\xac\xeb\xb6\x84</hp:t></hp:run></hp:p></hp:subList></hp:tc>"
            b"<hp:tc><hp:subList><hp:p><hp:run><hp:t>\xeb\xb0\xb0\xec\xa0\x90</hp:t></hp:run></hp:p></hp:subList></hp:tc>"
            b"</hp:tr><hp:tr>"
            b"<hp:tc><hp:subList><hp:p><hp:run><hp:t>\xec\x88\x98\xed\x96\x89\xea\xb2\xbd\xed\x97\x98</hp:t></hp:run></hp:p></hp:subList></hp:tc>"
            b"<hp:tc><hp:subList><hp:p><hp:run><hp:t>5.0</hp:t></hp:run></hp:p></hp:subList></hp:tc>"
            b"</hp:tr></hp:tbl>"
            b"</hp:run></hp:p>"
            b"</hp:sec>"
        )
        text = _hwpx_section_text(xml)
        self.assertEqual(text, "1. 평가기준\n구분 | 배점\n수행경험 | 5.0")
        # 표 안 문단이 별도 문단으로 중복되지 않아야 한다.
        self.assertEqual(text.count("구분"), 1)
        self.assertEqual(text.count("수행경험"), 1)

    def test_extract_text_redacts_phone_and_email(self):
        """extract_text()는 형식과 무관하게 담당자 연락처를 자동으로 지운다."""
        data = _build_hwpx(["국립경주박물관 기획운영과 유아름(Tel: 054-740-7520)", "문의: nego@example.go.kr"])
        text = extract_text(data, "hwpx")
        self.assertNotIn("054-740-7520", text)
        self.assertNotIn("nego@example.go.kr", text)
        self.assertIn("유아름", text)  # 이름 자체는 남긴다 (오탐 위험 때문)


class TestRedactPersonalContacts(unittest.TestCase):
    def test_masks_labeled_phone_number(self):
        text = redact_personal_contacts("담당자(Tel: 054-740-7520)에게 문의")
        self.assertNotIn("054-740-7520", text)
        self.assertIn("담당자", text)

    def test_masks_bare_phone_in_parens_without_label(self):
        text = redact_personal_contacts("우리기관 시설팀(054-740-7520)에 문의하여 주시기 바랍니다")
        self.assertNotIn("054-740-7520", text)

    def test_masks_email(self):
        text = redact_personal_contacts("문의: nego@example.go.kr 로 보내주세요")
        self.assertNotIn("nego@example.go.kr", text)

    def test_does_not_touch_product_classification_codes(self):
        """세부품명번호·업종코드처럼 구분자 없이 붙은 숫자열은 전화번호가 아니다."""
        text = "실물모형 및 전시물(세부품명번호 6010989901)"
        self.assertEqual(redact_personal_contacts(text), text)

    def test_does_not_touch_general_hotline_number(self):
        """1588-0800처럼 지역/휴대폰 국번이 아닌 대표번호는 개인 연락처가 아니라 남긴다."""
        text = "조달청 전자조달 콜센터(☎ 1588-0800)"
        self.assertEqual(redact_personal_contacts(text), text)

    def test_does_not_corrupt_surrounding_sentence(self):
        """이름이 아닌 일반 단어(예: '확인')를 이름으로 오인해 문장을 깨면 안 된다."""
        text = redact_personal_contacts("전화로 접수 여부를 확인(☎033-560-2342)하여야 하며")
        self.assertIn("확인", text)
        self.assertNotIn("033-560-2342", text)


class TestHwpRecordParsing(unittest.TestCase):
    """OLE 컨테이너 없이, 압축 해제된 레코드 스트림 파싱만 검증한다.

    실제 .hwp 파일(OLE+zlib)까지 통째로 만드는 건 라이터 라이브러리 없이는
    비현실적이라, 이 레이어(레코드 헤더 디코딩 + 텍스트 복원)만 단위 테스트한다.
    실제 파일 검증은 회사 공고 샘플로 별도 확인이 필요하다.
    """

    def test_decode_para_text_strips_inline_control_chars(self):
        payload = "지역제한\t텍스트".encode("utf-16le")
        self.assertEqual(_decode_para_text(payload), "지역제한텍스트")

    def test_decode_para_text_keeps_newlines(self):
        payload = "1행\n2행".encode("utf-16le")
        self.assertEqual(_decode_para_text(payload), "1행\n2행")

    def test_section_paragraphs_skips_non_text_records_and_extended_size(self):
        para1 = _build_hwp_record(0x43, "협상에 의한 계약".encode("utf-16le"))
        other = _build_hwp_record(0x10, b"\x01\x02\x03\x04")  # PARA_TEXT가 아닌 레코드
        para2 = _build_hwp_record(0x43, "둘째 문단".encode("utf-16le"))
        section = para1 + other + para2

        self.assertEqual(
            _hwp_section_paragraphs(section),
            ["협상에 의한 계약", "둘째 문단"],
        )

    def test_extended_size_record_is_parsed(self):
        text = "긴 문단 " * 500  # 0xFFF(4095)바이트를 넘겨 확장 크기 분기를 타게 한다
        payload = text.encode("utf-16le")
        self.assertGreater(len(payload), 0xFFF)
        record = _build_hwp_record(0x43, payload)

        self.assertEqual(_hwp_section_paragraphs(record), [text])


class TestFetchAttachmentText(unittest.TestCase):
    def test_missing_url_reports_error_without_raising(self):
        result = fetch_attachment_text(FakeSession(b""), {"seq": "1", "file_name": "a.hwp", "url": "", "ext": "hwp"})
        self.assertFalse(result.ok)
        self.assertIn("URL", result.error)

    def test_unsupported_extension_reports_error(self):
        att = {"seq": "1", "file_name": "a.xlsx", "url": "https://example.com/a.xlsx", "ext": "xlsx"}
        result = fetch_attachment_text(FakeSession(b""), att)
        self.assertFalse(result.ok)
        self.assertIn("지원하지 않는", result.error)
        self.assertTrue(result.skipped, "내역서 같은 형식은 실패가 아니라 건너뜀")

    def test_parse_failure_is_not_skipped(self):
        att = {"seq": "1", "file_name": "a.hwp", "url": "https://example.com/a.hwp", "ext": "hwp"}
        result = fetch_attachment_text(FakeSession(b"broken"), att)
        self.assertFalse(result.ok)
        self.assertFalse(result.skipped, "읽어야 할 문서를 못 읽은 건 실패로 센다")

    def test_skipped_attachments_are_not_counted(self):
        raw = {
            "bidNtceNo": "R26TEST0002", "bidNtceOrd": "000", "bidNtceNm": "테스트 공고",
            "ntceSpecFileNm1": "내역서.xlsx", "ntceSpecDocUrl1": "https://example.com/1.xlsx",
        }
        notice = notice_from_raw(raw, "용역")
        with tempfile.TemporaryDirectory() as tmp:
            stats = save_attachment_texts([_FakeCandidate(notice)], Path(tmp), timeout=5.0, session=FakeSession(b"x"))
        self.assertEqual((stats["attempted"], stats["failed"]), (0, 0))

    def test_successful_pdf_round_trip(self):
        # 표준 Helvetica 폰트는 한글을 못 그리므로(WinAnsiEncoding), 여기서는
        # 다운로드→추출 파이프라인이 예외 없이 끝까지 이어지는지만 확인한다.
        data = _build_pdf("Verify")
        att = {"seq": "2", "file_name": "제안요청서.pdf", "url": "https://example.com/2.pdf", "ext": "pdf"}
        result = fetch_attachment_text(FakeSession(data), att)
        self.assertTrue(result.ok, result.error)
        self.assertEqual(result.error, None)
        self.assertIn("Verify", result.text)

    def test_http_error_reports_error_without_raising(self):
        att = {"seq": "1", "file_name": "a.pdf", "url": "https://example.com/a.pdf", "ext": "pdf"}
        result = fetch_attachment_text(FakeSession(b"", status_code=404), att)
        self.assertFalse(result.ok)
        self.assertIn("404", result.error)


def _api_qualification(checked: bool = True, missing: list[str] | None = None) -> QualificationResult:
    """API 면허제한정보 판정 결과를 흉내낸다. checked=False면 API가 자격정보를
    안 준 공고, missing을 주면 API 기준으로 이미 미충족인 그룹이 있는 공고."""
    missing_groups = [LicenseGroup(group_no="api", allowed_names=missing)] if missing else []
    return QualificationResult(
        total_groups=1 if checked else 0,
        missing_groups=missing_groups,
        passes=True,
        checked=checked,
    )


class _FakeCandidate:
    """save_attachment_texts는 candidate.notice/.qualification/.schedule[/.days_left]만
    본다 — pipeline.Candidate 전체를 안 만들어도 된다. schedule을 안 주면
    (getattr 기본값 None) 일정 보충도 시도하지 않는다. days_left는 인스턴스에
    직접 세팅해야만(hasattr) 일정 보충 후 다시 계산해 넣는다."""

    def __init__(self, notice, checked: bool = True, schedule=None, qualification=None):
        self.notice = notice
        self.qualification = qualification or _api_qualification(checked)
        if schedule is not None:
            self.schedule = schedule


class TestSaveAttachmentTexts(unittest.TestCase):
    def test_writes_text_and_qualification_summary_for_real_fixture(self):
        fixture = FIXTURES_DIR / "jeongseon_culture_center_notice.hwpx"
        raw = {
            "bidNtceNo": "R26TEST0001",
            "bidNtceOrd": "000",
            "bidNtceNm": "테스트 공고",
            "ntceSpecFileNm1": fixture.name,
            "ntceSpecDocUrl1": f"file://{fixture}",
        }
        notice = notice_from_raw(raw, "용역")
        session = FakeSession(fixture.read_bytes())

        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            stats = save_attachment_texts([_FakeCandidate(notice)], output_dir, timeout=5.0, session=session)

            self.assertEqual(
                stats,
                {
                    "attempted": 1,
                    "ok": 1,
                    "failed": 0,
                    "qualification_found": 1,
                    "qualification_determined": 0,
                    "deadline_determined": 0,
                    "unnamed_codes": [],
                },
            )

            text_dir = output_dir / "attachment_text"
            written = {p.name for p in text_dir.iterdir()}
            base = f"R26TEST0001_000_1_{fixture.name}"
            self.assertIn(f"{base}.txt", written)
            self.assertIn(f"{base}_참가자격.txt", written)

            summary = (text_dir / f"{base}_참가자격.txt").read_text(encoding="utf-8")
            self.assertTrue(summary.startswith("5. 입찰 참가자격"))
            self.assertIn("실내건축공사업", summary)

    def test_no_summary_file_when_no_qualification_section(self):
        fixture = FIXTURES_DIR / "jeongseon_culture_center_task_order.hwpx"
        raw = {
            "bidNtceNo": "R26TEST0002",
            "bidNtceOrd": "000",
            "bidNtceNm": "테스트 공고2",
            "ntceSpecFileNm1": fixture.name,
            "ntceSpecDocUrl1": f"file://{fixture}",
        }
        notice = notice_from_raw(raw, "용역")
        session = FakeSession(fixture.read_bytes())

        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            stats = save_attachment_texts([_FakeCandidate(notice)], output_dir, timeout=5.0, session=session)
            self.assertEqual(stats["qualification_found"], 0)
            written = {p.name for p in (output_dir / "attachment_text").iterdir()}
            self.assertEqual(len(written), 1)  # .txt만, _참가자격.txt는 없음

    def test_replaces_qualification_with_pass_when_all_codes_held(self):
        """API 자격정보가 없는(checked=False) 공고는 첨부파일 참가자격 절의
        업종코드/세부품명번호를 보유 명단과 대조해 candidate.qualification 자체를
        갈아끼운다 — 다른 공고와 똑같이 '자격 충족'/'자격 미달(이름)'로 보이도록."""
        fixture = FIXTURES_DIR / "jeongseon_culture_center_notice.hwpx"
        raw = {
            "bidNtceNo": "R26TEST0001",
            "bidNtceOrd": "000",
            "bidNtceNm": "테스트 공고",
            "ntceSpecFileNm1": fixture.name,
            "ntceSpecDocUrl1": f"file://{fixture}",
        }
        notice = notice_from_raw(raw, "용역")
        session = FakeSession(fixture.read_bytes())
        candidate = _FakeCandidate(notice, checked=False)
        # 실측 문서(정선군 복합문화센터)가 요구하는 코드 전부를 보유했다고 가정.
        held_codes = {"6010989901", "5610150701", "5611210501", "4990", "4442", "4444", "6484", "1469"}

        with tempfile.TemporaryDirectory() as tmp:
            stats = save_attachment_texts([candidate], Path(tmp), timeout=5.0, session=session, held_codes=held_codes)

        self.assertEqual(stats["qualification_determined"], 1)
        self.assertTrue(candidate.qualification.checked)
        self.assertEqual(candidate.qualification.summary, "자격 충족")

    def test_satisfied_group_label_uses_held_registry_name_when_provided(self):
        """held_code_names를 넘기면 '충족' 항목의 이름표를 문서 원문 파싱 대신
        등록증 원문 이름으로 채운다 — 문서마다 표기가 달라 파싱이 fragile한
        문제를 코드 lookup으로 우회한다(사용자 제보, 2026-09-17)."""
        fixture = FIXTURES_DIR / "jeongseon_culture_center_notice.hwpx"
        raw = {
            "bidNtceNo": "R26TEST0001",
            "bidNtceOrd": "000",
            "bidNtceNm": "테스트 공고",
            "ntceSpecFileNm1": fixture.name,
            "ntceSpecDocUrl1": f"file://{fixture}",
        }
        notice = notice_from_raw(raw, "용역")
        session = FakeSession(fixture.read_bytes())
        candidate = _FakeCandidate(notice, checked=False)
        held_codes = {"6010989901", "5610150701", "5611210501", "4990", "4442", "4444", "6484", "1469"}
        held_code_names = {
            "6010989901": "실물모형및전시물",
            "5610150701": "책장",
            "5611210501": "라운지용의자",
            "4990": "실내건축공사업",
            "4442": "산업디자인전문회사(환경디자인분야)",
            "4444": "산업디자인전문회사(종합디자인분야)",
            "6484": "공공디자인 전문회사",
            "1469": "소프트웨어사업자(디지털콘텐츠개발서비스사업)",
        }

        with tempfile.TemporaryDirectory() as tmp:
            save_attachment_texts(
                [candidate],
                Path(tmp),
                timeout=5.0,
                session=session,
                held_codes=held_codes,
                held_code_names=held_code_names,
            )

        self.assertEqual(candidate.qualification.summary, "자격 충족")
        all_names = [name for g in candidate.qualification.satisfied_groups for name in g.allowed_names]
        self.assertIn("실내건축공사업(4990)", all_names)
        self.assertIn("산업디자인전문회사(종합디자인분야)(4444)", all_names)
        self.assertIn("소프트웨어사업자(디지털콘텐츠개발서비스사업)(1469)", all_names)

    def test_replaces_qualification_with_fail_when_a_code_missing(self):
        fixture = FIXTURES_DIR / "jeongseon_culture_center_notice.hwpx"
        raw = {
            "bidNtceNo": "R26TEST0001",
            "bidNtceOrd": "000",
            "bidNtceNm": "테스트 공고",
            "ntceSpecFileNm1": fixture.name,
            "ntceSpecDocUrl1": f"file://{fixture}",
        }
        notice = notice_from_raw(raw, "용역")
        session = FakeSession(fixture.read_bytes())
        candidate = _FakeCandidate(notice, checked=False)
        # 실내건축공사업(4990)만 빠뜨린 명단.
        held_codes = {"6010989901", "5610150701", "5611210501", "4442", "4444", "6484", "1469"}

        with tempfile.TemporaryDirectory() as tmp:
            stats = save_attachment_texts([candidate], Path(tmp), timeout=5.0, session=session, held_codes=held_codes)

        self.assertEqual(stats["qualification_determined"], 1)
        self.assertIn("자격 미달", candidate.qualification.summary)
        self.assertIn("실내건축공사업", candidate.qualification.summary)

    def test_attachment_requirements_apply_even_when_api_already_passed(self):
        """실측 버그(단양군 미디어아트 R26BK01731335): API 면허제한정보에는
        세부품명번호 필드가 없어서 품목 요건이 통째로 빠진다. API가 자격정보를
        줬고(checked=True) 전부 충족이라 해도, 첨부파일이 미보유 코드를 요구하면
        미달로 뒤집혀야 한다 — 예전에는 API 판정이 있으면 첨부파일을 아예
        보지 않아 '자격 충족'으로 남았다."""
        fixture = FIXTURES_DIR / "jeongseon_culture_center_notice.hwpx"
        raw = {
            "bidNtceNo": "R26TEST0001",
            "bidNtceOrd": "000",
            "bidNtceNm": "테스트 공고",
            "ntceSpecFileNm1": fixture.name,
            "ntceSpecDocUrl1": f"file://{fixture}",
        }
        notice = notice_from_raw(raw, "용역")
        session = FakeSession(fixture.read_bytes())
        # API는 자격정보를 줬고 미충족 없음 = "자격 충족"인 상태.
        candidate = _FakeCandidate(notice, checked=True)
        self.assertEqual(candidate.qualification.summary, "자격 충족")
        # 첨부파일이 요구하는 코드 중 실내건축공사업(4990)만 미보유.
        held_codes = {"6010989901", "5610150701", "5611210501", "4442", "4444", "6484", "1469"}

        with tempfile.TemporaryDirectory() as tmp:
            stats = save_attachment_texts(
                [candidate], Path(tmp), timeout=5.0, session=session, held_codes=held_codes
            )

        self.assertEqual(stats["qualification_determined"], 1)
        self.assertIn("자격 미달", candidate.qualification.summary)
        self.assertIn("실내건축공사업", candidate.qualification.summary)

    def test_api_missing_groups_are_kept_when_merging_attachment_result(self):
        """API 기준 미충족 그룹은 첨부파일 판정을 겹친 뒤에도 남아 있어야 한다."""
        fixture = FIXTURES_DIR / "jeongseon_culture_center_notice.hwpx"
        raw = {
            "bidNtceNo": "R26TEST0001",
            "bidNtceOrd": "000",
            "bidNtceNm": "테스트 공고",
            "ntceSpecFileNm1": fixture.name,
            "ntceSpecDocUrl1": f"file://{fixture}",
        }
        notice = notice_from_raw(raw, "용역")
        session = FakeSession(fixture.read_bytes())
        candidate = _FakeCandidate(notice, qualification=_api_qualification(missing=["전기공사업"]))
        # 첨부파일 쪽 코드는 전부 보유 — 그래도 API 미충족은 그대로 남아야 한다.
        held_codes = {"6010989901", "5610150701", "5611210501", "4990", "4442", "4444", "6484", "1469"}

        with tempfile.TemporaryDirectory() as tmp:
            save_attachment_texts(
                [candidate], Path(tmp), timeout=5.0, session=session, held_codes=held_codes
            )

        self.assertIn("전기공사업", candidate.qualification.summary)

    def test_qualification_untouched_when_no_qualification_section_found(self):
        """참가자격 절 자체가 없는 문서(과업지시서 등)는 판정 근거가 없으니 손대지 않는다."""
        fixture = FIXTURES_DIR / "jeongseon_culture_center_task_order.hwpx"
        raw = {
            "bidNtceNo": "R26TEST0002",
            "bidNtceOrd": "000",
            "bidNtceNm": "테스트 공고2",
            "ntceSpecFileNm1": fixture.name,
            "ntceSpecDocUrl1": f"file://{fixture}",
        }
        notice = notice_from_raw(raw, "용역")
        session = FakeSession(fixture.read_bytes())
        candidate = _FakeCandidate(notice, checked=False)
        original_qualification = candidate.qualification

        with tempfile.TemporaryDirectory() as tmp:
            stats = save_attachment_texts(
                [candidate], Path(tmp), timeout=5.0, session=session, held_codes={"4990"}
            )

        self.assertEqual(stats["qualification_determined"], 0)
        self.assertIs(candidate.qualification, original_qualification)

    def test_qualification_untouched_when_download_fails(self):
        att = {"seq": "1", "file_name": "a.hwpx", "url": "https://example.com/a.hwpx", "ext": "hwpx"}
        raw = {
            "bidNtceNo": "R26TEST0003",
            "bidNtceOrd": "000",
            "bidNtceNm": "테스트 공고3",
            "ntceSpecFileNm1": att["file_name"],
            "ntceSpecDocUrl1": att["url"],
        }
        notice = notice_from_raw(raw, "용역")
        session = FakeSession(b"", status_code=404)
        candidate = _FakeCandidate(notice, checked=False)
        original_qualification = candidate.qualification

        with tempfile.TemporaryDirectory() as tmp:
            stats = save_attachment_texts(
                [candidate], Path(tmp), timeout=5.0, session=session, held_codes={"4990"}
            )

        self.assertEqual(stats["qualification_determined"], 0)
        self.assertIs(candidate.qualification, original_qualification)

    def test_fills_attachment_deadline_when_api_schedule_fully_empty(self):
        """API의 마감 세 필드가 전부 비어 '일정 미상'인 공고는 첨부파일에서 찾은
        제출기한으로 schedule.attachment_deadline을 채운다(실측: 경상남도관광재단
        「K-거상」공고 R26BK01707504 — 세 필드 모두 없지만 첨부 제안요청서에
        "제출기간 : 2026. 9. 14.(월) 9:00~18:00"가 명시돼 있었다)."""
        data = _build_hwpx(
            [
                "2. 입찰관련 일시 및 장소",
                "다. 기본서류 및 제안서 제출일시(반드시 방문제출)",
                "- 제출기간 : 2026. 9. 14.(월) 9:00~18:00 (점심시간 12:00~13:00 접수 불가)",
            ]
        )
        raw = {
            "bidNtceNo": "R26TEST0004",
            "bidNtceOrd": "000",
            "bidNtceNm": "테스트 공고4",
            "ntceSpecFileNm1": "a.hwpx",
            "ntceSpecDocUrl1": "https://example.com/a.hwpx",
        }
        notice = notice_from_raw(raw, "용역")
        session = FakeSession(data)
        schedule = Schedule(qualification_deadline=None, joint_agreement_deadline=None, bid_deadline=None)
        candidate = _FakeCandidate(notice, schedule=schedule)

        with tempfile.TemporaryDirectory() as tmp:
            stats = save_attachment_texts([candidate], Path(tmp), timeout=5.0, session=session)

        self.assertEqual(stats["deadline_determined"], 1)
        self.assertEqual(schedule.attachment_deadline, datetime(2026, 9, 14, 18, 0))
        self.assertEqual(schedule.earliest, ("첨부파일 제출기한", datetime(2026, 9, 14, 18, 0)))

    def test_days_left_recomputed_after_attachment_deadline_filled(self):
        """days_left는 후보 산출 시점에 미리 계산돼 있어(원래 일정 미상이라 None) —
        attachment_deadline을 채운 뒤 다시 계산하지 않으면 '마감/일정'엔 새 날짜가
        뜨는데 '잔여일수'는 여전히 빈 채로 어긋난다."""
        data = _build_hwpx(["- 제출기한 : 2026. 9. 14.(월) 18:00"])
        raw = {
            "bidNtceNo": "R26TEST0007",
            "bidNtceOrd": "000",
            "bidNtceNm": "테스트 공고7",
            "ntceSpecFileNm1": "a.hwpx",
            "ntceSpecDocUrl1": "https://example.com/a.hwpx",
        }
        notice = notice_from_raw(raw, "용역")
        session = FakeSession(data)
        schedule = Schedule(qualification_deadline=None, joint_agreement_deadline=None, bid_deadline=None)
        candidate = _FakeCandidate(notice, schedule=schedule)
        candidate.days_left = None  # 산출 시점엔 '일정 미상'이라 None이었다고 가정

        with tempfile.TemporaryDirectory() as tmp:
            save_attachment_texts(
                [candidate], Path(tmp), timeout=5.0, session=session, now=datetime(2026, 9, 10)
            )

        self.assertEqual(candidate.days_left, 4)

    def test_reorders_candidates_after_filling_a_past_attachment_deadline(self):
        """실측: 경상남도관광재단 K-거상 공고(R26BK01707504)는 build_candidates 시점엔
        '일정 미상'(sort_key가 9999로 취급)이라 목록 맨 뒤에 놓였는데, 첨부파일로 마감이
        채워진 뒤(이미 지난 마감이라 days_left가 음수) 재정렬을 안 하면 이미 마감된
        공고가 여전히 목록 맨 뒤에(예: 앞으로 38일 남은 공고보다도 뒤에) 남는다."""
        soon_raw = {
            "bidNtceNo": "R26TEST0008",
            "bidNtceOrd": "000",
            "bidNtceNm": "마감이 곧인 공고",
            "bidQlfctRgstDt": "2026-09-15 18:00:00",
        }
        soon_notice = notice_from_raw(soon_raw, "용역")
        soon_schedule = Schedule(
            qualification_deadline=datetime(2026, 9, 15, 18, 0),
            joint_agreement_deadline=None,
            bid_deadline=None,
        )
        soon = Candidate(
            notice=soon_notice,
            screen_result=ScreenResult(matched=True, confidence="참고용"),
            qualification=_api_qualification(True),
            joint=JointSupply(allowed=True, submit_type=None, exec_type=None, raw_value=None),
            schedule=soon_schedule,
            days_left=5,
        )

        stale_raw = {
            "bidNtceNo": "R26TEST0009",
            "bidNtceOrd": "000",
            "bidNtceNm": "일정 미상이었다가 첨부파일로 마감이 밝혀진 공고",
            "ntceSpecFileNm1": "a.hwpx",
            "ntceSpecDocUrl1": "https://example.com/a.hwpx",
        }
        stale_notice = notice_from_raw(stale_raw, "용역")
        stale_schedule = Schedule(qualification_deadline=None, joint_agreement_deadline=None, bid_deadline=None)
        stale = Candidate(
            notice=stale_notice,
            screen_result=ScreenResult(matched=True, confidence="참고용"),
            qualification=_api_qualification(True),
            joint=JointSupply(allowed=True, submit_type=None, exec_type=None, raw_value=None),
            schedule=stale_schedule,
            days_left=None,  # build_candidates 시점엔 '일정 미상'
        )

        data = _build_hwpx(["- 제출기한 : 2026. 9. 3.(목) 18:00"])  # now(9/10) 기준 이미 지남
        candidates = [soon, stale]  # build_candidates가 정렬해뒀다고 가정한 초기 순서

        with tempfile.TemporaryDirectory() as tmp:
            save_attachment_texts(
                candidates, Path(tmp), timeout=5.0, session=FakeSession(data), now=datetime(2026, 9, 10)
            )

        self.assertEqual(stale.days_left, -7)
        self.assertEqual([c.notice.notice_no for c in candidates], ["R26TEST0009", "R26TEST0008"])

    def test_attachment_deadline_untouched_when_api_schedule_already_known(self):
        """API가 이미 마감일자를 하나라도 준 공고는 첨부파일 원문을 뒤지지 않는다."""
        data = _build_hwpx(["- 제출기간 : 2026. 9. 14.(월) 9:00~18:00"])
        raw = {
            "bidNtceNo": "R26TEST0005",
            "bidNtceOrd": "000",
            "bidNtceNm": "테스트 공고5",
            "ntceSpecFileNm1": "a.hwpx",
            "ntceSpecDocUrl1": "https://example.com/a.hwpx",
        }
        notice = notice_from_raw(raw, "용역")
        session = FakeSession(data)
        schedule = Schedule(
            qualification_deadline=None,
            joint_agreement_deadline=None,
            bid_deadline=datetime(2026, 9, 20, 18, 0),
        )
        candidate = _FakeCandidate(notice, schedule=schedule)

        with tempfile.TemporaryDirectory() as tmp:
            stats = save_attachment_texts([candidate], Path(tmp), timeout=5.0, session=session)

        self.assertEqual(stats["deadline_determined"], 0)
        self.assertIsNone(schedule.attachment_deadline)

    def test_attachment_deadline_stays_none_when_no_deadline_text_found(self):
        data = _build_hwpx(["과업 내용은 별첨 과업지시서를 참조합니다."])
        raw = {
            "bidNtceNo": "R26TEST0006",
            "bidNtceOrd": "000",
            "bidNtceNm": "테스트 공고6",
            "ntceSpecFileNm1": "a.hwpx",
            "ntceSpecDocUrl1": "https://example.com/a.hwpx",
        }
        notice = notice_from_raw(raw, "용역")
        session = FakeSession(data)
        schedule = Schedule(qualification_deadline=None, joint_agreement_deadline=None, bid_deadline=None)
        candidate = _FakeCandidate(notice, schedule=schedule)

        with tempfile.TemporaryDirectory() as tmp:
            stats = save_attachment_texts([candidate], Path(tmp), timeout=5.0, session=session)

        self.assertEqual(stats["deadline_determined"], 0)
        self.assertIsNone(schedule.attachment_deadline)


if __name__ == "__main__":
    unittest.main()


def _zip_bytes(files: dict, cp949_names: bool = False) -> bytes:
    import io
    import zipfile

    buf = io.BytesIO()
    swaps = []
    with zipfile.ZipFile(buf, "w") as zf:
        for i, (name, data) in enumerate(files.items()):
            if cp949_names:
                # 관공서 zip처럼 파일명을 CP949 바이트로, UTF-8 플래그 없이 넣는다 —
                # zipfile은 비ASCII 이름에 UTF-8 플래그를 켜므로 같은 길이 ASCII 이름으로 쓰고 바이트를 바꿔 끼운다
                raw = name.encode("cp949")
                placeholder = (f"N{i}" + "_" * len(raw))[: len(raw)]
                swaps.append((placeholder.encode(), raw))
                name = placeholder
            zf.writestr(name, data)
    out = buf.getvalue()
    for old, new in swaps:
        out = out.replace(old, new)
    return out


class TestZipAttachment(unittest.TestCase):
    """입찰서류.zip처럼 공고문·과업지시서를 압축해 올린 첨부 (실측: 가양4단지 R26BK01745222)."""

    def test_reads_documents_inside_zip(self):
        from nego.attachments import extract_text

        data = _zip_bytes({
            "입찰서류/과업지시서.pdf": _build_pdf("Task order parking lot"),
            "입찰서류/내역서.xlsx": b"PK\x03\x04 not read",
            "입찰서류/공고문.pdf": _build_pdf("Notice civil works"),
        })
        text = extract_text(data, "zip")
        self.assertIn("Task order parking lot", text)
        self.assertIn("Notice civil works", text)
        self.assertIn("[압축 안] 입찰서류/과업지시서.pdf", text)
        self.assertNotIn("not read", text)

    def test_cp949_file_names_are_restored(self):
        from nego.attachments import extract_text

        data = _zip_bytes({"과업지시서.pdf": _build_pdf("Hello")}, cp949_names=True)
        self.assertIn("[압축 안] 과업지시서.pdf", extract_text(data, "zip"))

    def test_nested_zip_one_level(self):
        from nego.attachments import extract_text

        inner = _zip_bytes({"제안요청서.pdf": _build_pdf("Inner RFP")})
        self.assertIn("Inner RFP", extract_text(_zip_bytes({"묶음.zip": inner}), "zip"))
        too_deep = _zip_bytes({"a.zip": _zip_bytes({"b.zip": inner})})
        with self.assertRaises(AttachmentError):
            extract_text(too_deep, "zip")

    def test_many_drawings_do_not_hide_documents(self):
        """2026-10-01 R26BD00270194: 파일 511개 든 압축에서 앞쪽 60개(도면)만 보고 '읽을 문서 없음'으로 실패했다.
        파일 수 제한은 문서에만 걸고, 공고문·과업지시서 이름을 먼저 읽는다."""
        from nego import attachments
        from nego.attachments import extract_text

        files = {f"도면/A-{i:03d}.dwg": b"x" for i in range(200)}
        files.update({f"참고/사진{i:03d}.pdf": _build_pdf(f"Photo {i}") for i in range(70)})
        files["입찰서류/과업지시서.pdf"] = _build_pdf("Task order body")
        text = extract_text(_zip_bytes(files), "zip")
        self.assertIn("Task order body", text)
        self.assertLess(text.count("[압축 안]"), attachments.ZIP_MAX_MEMBERS + 1)
        self.assertTrue(text.startswith("=== [압축 안] 입찰서류/과업지시서.pdf"))

    def test_zip_without_documents_is_skipped_not_failed(self):
        """내역서·도면만 든 zip은 읽을 대상이 없는 것 — 실패로 세지 않는다."""
        from nego.attachments import AttachmentUnsupported, extract_text

        with self.assertRaises(AttachmentUnsupported):
            extract_text(_zip_bytes({"내역서.xlsx": b"x", "도면.dwg": b"y"}), "zip")

    def test_drm_locked_files_are_skipped_not_failed(self):
        """2026-10-14 R26BK01757744 "7. 설계도서.zip": 도면 PDF 2개가 문서 보안(DRM)으로 잠겨(b"\\x9b DRM…") 실패 1건으로 셌다."""
        from nego.attachments import AttachmentUnsupported, extract_text

        drm = b"\x9b DRM\x00\x01 locked body"
        with self.assertRaises(AttachmentUnsupported):
            extract_text(drm, "pdf")
        with self.assertRaises(AttachmentUnsupported):
            extract_text(_zip_bytes({"인테리어 도면.pdf": drm, "디자인제안 투시도.pdf": drm}), "zip")
        # 잠긴 도면과 읽히는 공고문이 같이 있으면 공고문만 읽는다
        text = extract_text(_zip_bytes({"도면.pdf": drm, "공고문.pdf": _build_pdf("Notice body")}), "zip")
        self.assertIn("Notice body", text)

    def test_zip_with_broken_document_is_a_failure(self):
        from nego.attachments import AttachmentUnsupported, extract_text

        with self.assertRaises(AttachmentError) as ctx:
            extract_text(_zip_bytes({"내역서.xlsx": b"x", "공고문.pdf": b"%PDF-broken"}), "zip")
        self.assertNotIsInstance(ctx.exception, AttachmentUnsupported)
        self.assertIn("읽을 수 있는 문서", str(ctx.exception))

    def test_zip_attachment_through_fetch(self):
        data = _zip_bytes({"과업지시서.pdf": _build_pdf("Fetched")})
        att = {"seq": "3", "file_name": "입찰서류_최종.zip", "url": "https://example.com/3.zip", "ext": "zip"}
        result = fetch_attachment_text(FakeSession(data), att)
        self.assertTrue(result.ok, result.error)
        self.assertIn("Fetched", result.text)
