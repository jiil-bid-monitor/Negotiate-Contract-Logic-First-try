"""사전규격 수집 — 응답 매핑, 수집 범위, 일정, 실패 시 본공고 보호.

필드명은 조달청 사전규격정보서비스 활용가이드 기준 (실제 응답으로는 아직 미확인).
"""

from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nego import attachments, scope  # noqa: E402
from nego.config import load_config  # noqa: E402
from nego.http_client import ApiError  # noqa: E402
from nego.models import notice_from_raw, prespec_from_raw  # noqa: E402
from nego.pipeline import RunStats, build_candidates, collect_prespecs  # noqa: E402
from tests import fixtures  # noqa: E402

NOW = datetime(2026, 9, 14, 9, 0)


def prespec_raw(no="R26BD00012345", title="○○박물관 전시물 제작 설치", **over):
    raw = {
        "bfSpecRgstNo": no,
        "prdctClsfcNoNm": title,
        "orderInsttNm": "○○시",
        "rlDminsttNm": "○○시립박물관",
        "asignBdgtAmt": "350000000",
        "rcptDt": "2026-09-10 10:00:00",
        "opninRgstClseDt": "2026-09-17 18:00:00",
        "bidNtceNoList": "",
        "specDocFileUrl1": "https://www.g2b.go.kr/.../file1",
        "specDocFileUrl2": "",
        "ofclNm": "홍길동",
        "ofclTelNo": "02-000-0000",
    }
    raw.update(over)
    return raw


class _Client:
    def __init__(self, fail=(), items=None):
        self.fail = set(fail)
        self.items = items or {}
        self.calls = []

    def fetch_all_pages_chunked(self, base_url, operation, params, begin, end, label):
        self.calls.append((base_url, label))
        if label in self.fail:
            raise ApiError(label, "SERVICE KEY IS NOT REGISTERED ERROR")
        return self.items.get(label, [])


class TestMapping(unittest.TestCase):
    def test_fields_and_personal_info(self):
        n = prespec_from_raw(prespec_raw(), "물품")
        self.assertEqual((n.kind, n.notice_no, n.notice_ord), ("사전규격", "R26BD00012345", "000"))
        self.assertEqual(n.title, "○○박물관 전시물 제작 설치")
        self.assertEqual(n.demand_institution, "○○시립박물관")
        self.assertEqual(n.budget, 350_000_000)
        self.assertEqual(n.opinion_deadline, "2026-09-17 18:00:00")
        self.assertNotIn("ofclNm", n.raw)
        self.assertNotIn("ofclTelNo", n.raw)
        # 파일명 필드가 없어 확장자 없이 URL만 남는다 → 내려받아 내용으로 형식 판별
        self.assertEqual([(a["seq"], a["ext"]) for a in n.attachments], [("1", "")])

    def test_linked_bid_notices(self):
        n = prespec_from_raw(prespec_raw(bidNtceNoList="R26BK01111111, R26BK02222222"), "용역")
        self.assertEqual(n.linked_bid_notices, ["R26BK01111111", "R26BK02222222"])


class TestScopeAndSchedule(unittest.TestCase):
    def test_prespec_kept_regardless_of_categories(self):
        pre = prespec_from_raw(prespec_raw(), "물품")
        self.assertEqual(scope.bid_category(pre), "사전규격")
        result = scope.apply_scope([pre], {"협상"})
        self.assertEqual(result.kept, [pre])

    def test_candidate_uses_opinion_deadline_and_filters(self):
        config = load_config()
        config.screen.keywords = ["박물관"]
        notices = [notice_from_raw(r, "용역") for r in fixtures.service_notices()]
        notices += [prespec_from_raw(prespec_raw(), "물품"),
                    prespec_from_raw(prespec_raw("R26BD0009", "청사 냉난방기 교체"), "물품")]
        stats = RunStats()
        cands = build_candidates(notices, config, {}, {}, NOW, stats)
        pre = next(c for c in cands if c.notice.kind == "사전규격")
        self.assertEqual(pre.category, "사전규격")
        self.assertEqual(pre.schedule.earliest[0], "의견등록 마감")
        self.assertEqual(pre.days_left, 3)
        self.assertFalse(pre.qualification.checked, "면허제한정보가 없어 API 자격판정은 미확인")
        self.assertNotIn("R26BD0009", [c.notice.notice_no for c in cands], "키워드 미매칭 사전규격은 빠진다")
        # 본공고 일정 계산은 그대로
        bid = next(c for c in cands if c.notice.kind == "본공고")
        self.assertNotEqual(bid.schedule.earliest[0], "의견등록 마감")


class TestLinkedPrespecRemoved(unittest.TestCase):
    def test_prespec_already_posted_goes_to_rejected(self):
        config = load_config()
        config.screen.keywords = ["박물관"]
        notices = [prespec_from_raw(prespec_raw("R26BD01", bidNtceNoList="R26BK01111111"), "물품"),
                   prespec_from_raw(prespec_raw("R26BD02"), "물품")]
        stats = RunStats()
        cands = build_candidates(notices, config, {}, {}, NOW, stats)
        self.assertEqual([c.notice.notice_no for c in cands], ["R26BD02"])
        gone = next(c for c in stats.rejected if c.notice.notice_no == "R26BD01")
        self.assertEqual(gone.excluded_reason, "본공고 게시됨 (R26BK01111111)")
        self.assertEqual(stats.screened_out["본공고 게시됨"], 1)


class TestPrespecDetailUrl(unittest.TestCase):
    def test_built_from_registration_number(self):
        """사전규격 응답엔 상세 주소 칸이 없다(10/12 실응답) — 등록번호로 만든 주소(사용자 확인: 업무구분 코드 없이 열림)."""
        n = prespec_from_raw(prespec_raw(no="R26BD00281727"), "용역")
        self.assertEqual(n.detail_url, "https://www.g2b.go.kr/link/PRVA004_02/single/?bfSpecRegNo=R26BD00281727")

    def test_api_url_wins_when_present(self):
        n = prespec_from_raw(prespec_raw(bfSpecDtlUrl="https://example/x"), "용역")
        self.assertEqual(n.detail_url, "https://example/x")


class TestLinkedSystem(unittest.TestCase):
    """국방전자조달(d2b) 연계 사전규격 — 나라장터엔 첨부 0건, 규격서는 d2b에만 (2026-10-12 사용자 제보)."""

    def test_d2b_url_anywhere_in_response(self):
        n = prespec_from_raw(prespec_raw(specDocFileUrl1="", orderInsttNm="국방부 국군재정관리단",
                                         bfSpecDtlUrl="http://www.d2b.go.kr/psb/plan/servicePreStdList.do"), "용역")
        self.assertEqual(n.linked_system, {"name": "국방전자조달",
                                           "url": "http://www.d2b.go.kr/psb/plan/servicePreStdList.do"})

    def test_defense_institution_without_attachments(self):
        n = prespec_from_raw(prespec_raw(specDocFileUrl1="", orderInsttNm="국방부 국군재정관리단",
                                         rlDminsttNm="육군 제0000부대"), "용역")
        self.assertEqual(n.linked_system["name"], "국방전자조달")
        self.assertIn("d2b.go.kr", n.linked_system["url"])

    def test_defense_institution_with_attachments_is_normal(self):
        n = prespec_from_raw(prespec_raw(orderInsttNm="국방부 국군재정관리단"), "용역")
        self.assertIsNone(n.linked_system, "나라장터에 첨부가 있으면 그대로 읽으면 된다")

    def test_ordinary_notice_without_attachments_is_not_linked(self):
        n = prespec_from_raw(prespec_raw(specDocFileUrl1=""), "용역")
        self.assertIsNone(n.linked_system)

    def test_ps_reference_number_without_attachments(self):
        """실응답(10/12): 국방 기관 사전규격은 refNo가 PS2026… — 기관명이 국방 표현이 아니어도 잡는다."""
        n = prespec_from_raw(prespec_raw(specDocFileUrl1="", orderInsttNm="○○부대", refNo="PS2026UMM39791"), "용역")
        self.assertEqual(n.linked_system["name"], "국방전자조달")
        self.assertIsNone(prespec_from_raw(prespec_raw(specDocFileUrl1="", refNo="건설안전과-35965"), "용역").linked_system)

    def test_shown_in_report_and_telegram(self):
        from nego import report, telegram

        config = load_config()
        config.screen.keywords = ["박물관"]
        n = prespec_from_raw(prespec_raw(specDocFileUrl1="", orderInsttNm="국방부 국군재정관리단",
                                         prdctClsfcNoNm="육군박물관 전시물 제작 설치"), "용역")
        stats = RunStats()
        cands = build_candidates([n], config, {}, {}, datetime(2026, 9, 12, 9), stats)
        self.assertEqual(len(cands), 1)
        self.assertIn("국방전자조달 공고", report.render_console(cands, stats))
        self.assertIn("국방전자조달에서 확인", report.render_html(cands, stats, datetime(2026, 9, 12, 9)))
        row = telegram.Row.from_candidate(cands[0])
        self.assertIn("· 국방전자조달에서 확인", telegram._line(1, row, False))


class TestCollect(unittest.TestCase):
    def test_failure_does_not_raise(self):
        client = _Client(fail={"사전규격/용역"}, items={"사전규격/물품": [prespec_raw()]})
        stats = RunStats()
        got = collect_prespecs(client, "202609070000", "202609140900", stats)
        self.assertEqual([n.notice_no for n in got], ["R26BD00012345"])
        self.assertEqual(stats.prespec_fetched, 1)
        self.assertIn("사전규격/용역", stats.prespec_error)
        self.assertTrue(stats.prespec_requested)
        self.assertEqual({label for _, label in client.calls}, {"사전규격/용역", "사전규격/물품", "사전규격/공사"})

    def test_base_url_override(self):
        import os
        os.environ["PRESPEC_BASE_URL"] = "https://example.invalid/pre"
        try:
            client = _Client()
            collect_prespecs(client, "202609070000", "202609140900", RunStats())
            self.assertTrue(all(url == "https://example.invalid/pre" for url, _ in client.calls))
        finally:
            del os.environ["PRESPEC_BASE_URL"]


class TestExtensionlessAttachment(unittest.TestCase):
    def test_downloads_and_sniffs_when_no_extension(self):
        class Res:
            status_code = 200
            content = b"not a document"

        class Session:
            called = False

            def get(self, url, timeout):
                Session.called = True
                return Res()

        result = attachments.fetch_attachment_text(Session(), {"seq": "1", "file_name": "", "url": "http://x", "ext": ""})
        self.assertTrue(Session.called, "확장자가 없어도 내려받아 내용으로 판별해야 한다")
        self.assertIn("지원하지 않는 형식", result.error)

    def test_known_unsupported_extension_not_downloaded(self):
        class Session:
            def get(self, url, timeout):
                raise AssertionError("받으면 안 된다")

        result = attachments.fetch_attachment_text(Session(), {"seq": "1", "file_name": "a.xlsx", "url": "http://x", "ext": "xlsx"})
        self.assertIn("지원하지 않는 형식", result.error)


if __name__ == "__main__":
    unittest.main()
