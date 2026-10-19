"""첨부 고르기·워드/엑셀·연결 사전규격·국방전자조달·첨부 개최지 (2026-10-19 통합 — 팀원 수집기에서 옮김)."""

import io
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nego import attachment_sources as S  # noqa: E402
from nego.attachments import AttachmentUnsupported, extract_text  # noqa: E402
from nego.models import notice_from_raw  # noqa: E402
from nego.venue_text import overseas_venue  # noqa: E402


def _zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


class TestOffice(unittest.TestCase):
    def test_docx_text(self):
        xml = (
            '<w:document xmlns:w="w"><w:body><w:p><w:r><w:t>전시 가벽 설치</w:t></w:r></w:p>'
            "<w:p><w:r><w:t>그래픽&amp;패널</w:t></w:r></w:p></w:body></w:document>"
        ).encode()
        text = extract_text(_zip({"word/document.xml": xml, "[Content_Types].xml": b"x"}), "docx")
        self.assertIn("전시 가벽 설치", text)
        self.assertIn("그래픽&패널", text)

    def test_docx_sniffed_from_wrong_extension(self):
        xml = '<w:document xmlns:w="w"><w:p><w:t>시방서 본문</w:t></w:p></w:document>'.encode()
        self.assertIn("시방서 본문", extract_text(_zip({"word/document.xml": xml}), "zip"))

    def test_xlsx_keeps_text_cells_only(self):
        xml = "<sst><si><t>진열장</t></si><si><t>EA</t></si><si><t>1200</t></si><si><t>그래픽 패널</t></si></sst>".encode()
        text = extract_text(_zip({"xl/sharedStrings.xml": xml, "xl/workbook.xml": b"x"}), "xlsx")
        self.assertEqual(text.splitlines(), ["진열장", "그래픽 패널"])

    def test_broken_xlsx_is_skipped(self):
        with self.assertRaises(AttachmentUnsupported):
            extract_text(b"not a zip", "xlsx")


class TestSpecOrder(unittest.TestCase):
    def test_order(self):
        names = ["입찰공고문.hwp", "제안요청서.hwpx", "제안요청서 등.zip", "과업지시서.pdf", "공내역서.xlsx", "청렴서약서 서식.hwp"]
        ordered = [a["file_name"] for a in S.order_attachments([{"file_name": n} for n in names])]
        self.assertEqual(ordered[:3], ["과업지시서.pdf", "제안요청서.hwpx", "제안요청서 등.zip"])
        self.assertFalse(S.is_spec_name("청렴서약서 서식.hwp"))
        self.assertTrue(S.is_quantity_sheet("공내역서.xlsx"))
        self.assertFalse(S.is_quantity_sheet("내역서 산출근거.xlsx"))


class _FakeClient:
    def __init__(self, items):
        self.items, self.calls = items, []

    def _call_with_retry(self, base, op, params, label):
        self.calls.append((op, params))
        return SimpleNamespace(items=self.items)


class TestLinkedPrespec(unittest.TestCase):
    def _notice(self, reg_no="R26BD00264040"):
        raw = {"bidNtceNo": "R26BK01689798", "bidNtceOrd": "000", "bidNtceNm": "역사관 상설전시", "bfSpecRgstNo": reg_no}
        return notice_from_raw(raw, "용역")

    def test_lookup_and_cache(self):
        client = _FakeClient([{"bfSpecRgstNo": "R26BD00264040", "specDocFileUrl1": "https://x/1", "specDocFileUrl2": ""}])
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "c.json"
            found = S.linked_prespec_attachments(client, self._notice(), cache)
            self.assertEqual([a["url"] for a in found], ["https://x/1"])
            self.assertEqual(client.calls[0][1]["inqryDiv"], "2")
            again = S.linked_prespec_attachments(_FakeClient([]), self._notice(), cache)
            self.assertEqual([a["url"] for a in again], ["https://x/1"], "캐시에서")

    def test_no_link(self):
        self.assertIsNone(S.linked_prespec_no(self._notice("")))
        self.assertEqual(S.linked_prespec_attachments(None, self._notice()), [])


class TestD2b(unittest.TestCase):
    def test_ref_and_links(self):
        n = notice_from_raw({"bfSpecRgstNo": "R26BD00281727", "refNo": "PS2026UMM40015", "prdctClsfcNoNm": "상설육군홍보관"}, "용역")
        self.assertEqual(S.d2b_ref_of(n), ("2026", "UMM", "40015"))
        html = '<a href="/common/downloadSes.do?fileIdxx=AB12" class="x"> 사전규격공개(26F175-B).zip </a>'
        self.assertEqual(S.parse_d2b_links(html), [("사전규격공개(26F175-B).zip", "/common/downloadSes.do?fileIdxx=AB12")])


class TestVenue(unittest.TestCase):
    def test_overseas(self):
        self.assertEqual(overseas_venue("1. 개최 장소: 독일 뒤셀도르프 메쎄\n2. 기간"), "독일 뒤셀도르프 메쎄")
        self.assertEqual(overseas_venue("행사장소 : Las Vegas Convention Center"), "Las Vegas Convention Center")

    def test_domestic_or_missing(self):
        self.assertIsNone(overseas_venue("사업 장소: 강원특별자치도 정선군 일원"))
        self.assertIsNone(overseas_venue("과업 개요만 있음"))


if __name__ == "__main__":
    unittest.main()
