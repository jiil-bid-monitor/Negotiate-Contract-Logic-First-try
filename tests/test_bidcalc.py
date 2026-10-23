"""투찰 계산기 테스트 — 공고 값 불러오기(`nego/bidcalc.py`)와 화면 계산(`webapp/static/bidcalc.js`, node로 실행)."""

from __future__ import annotations

import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from nego import bidcalc  # noqa: E402
from nego import fields as F  # noqa: E402
from nego.http_client import ApiError  # noqa: E402

# 2026-10-12 Run #94 실응답(기초금액/공사) 칸 그대로
BASIS_ROW = {
    "bidNtceNo": "R26BK01752065", "bidNtceOrd": "000", "bidPrceCalclAYn": "Y", "bssAmtPurcnstcst": "145398641",
    "bssamt": "184521490", "bssamtOpenDt": "2026-10-01 07:33:09", "envCnsrvcst": "0", "mrfnHealthInsrprm": "2711820",
    "npnInsrprm": "3583072", "odsnLngtrmrcprInsrprm": "356333", "qltyMngcst": "0", "qltyMngcstAObjYn": "Y",
    "rsrvtnPrceRngBgnRate": "-3", "rsrvtnPrceRngEndRate": "+3", "rtrfundNon": "1734961",
    "scontrctPayprcePayGrntyFee": "0", "sftyChckMngcst": "0", "sftyMngcst": "3298695",
}


class FakeClient:
    def __init__(self, responses, fail=()):
        self.responses = responses
        self.fail = set(fail)
        self.calls = []

    def fetch_all_pages(self, base_url, operation, params, label):
        self.calls.append((operation, params))
        if operation in self.fail:
            raise ApiError(label, "없는 오퍼레이션")
        return self.responses.get(operation, [])


class LookupTest(unittest.TestCase):
    def test_a_value_sum_and_items(self):
        total, items = bidcalc.a_value(BASIS_ROW)
        self.assertEqual(total, 3583072 + 2711820 + 356333 + 1734961 + 3298695)
        self.assertEqual([i["name"] for i in items], ["국민연금보험료", "국민건강보험료", "노인장기요양보험료", "퇴직급여충당금", "산업안전보건관리비"])

    def test_quality_cost_only_when_flagged(self):
        row = {**BASIS_ROW, "qltyMngcst": "500000", "qltyMngcstAObjYn": "N"}
        self.assertNotIn("품질관리비", [i["name"] for i in bidcalc.a_value(row)[1]])
        row["qltyMngcstAObjYn"] = "Y"
        self.assertIn("품질관리비", [i["name"] for i in bidcalc.a_value(row)[1]])

    def test_lookup_fills_from_notice_and_basis(self):
        notice = {"bidNtceNo": "R26BK01752065", "bidNtceOrd": "001", "bidNtceNm": "마곡나루역 펀스테이션 전기공사",
                  "dminsttNm": "서울교통공사", "sucsfbidLwltRate": "89.745", "sucsfbidMthdAppStd": "지방자치단체 입찰시 낙찰자 결정기준",
                  "totPrdprcNum": "15", "presmptPrce": "167746809", "cntrctCnclsMthdNm": "제한경쟁",
                  "rgstDt": "2026-10-01 07:33:09"}
        older = {**notice, "bidNtceOrd": "000", "sucsfbidLwltRate": "87.745"}
        other = {**notice, "bidNtceNo": "R26BK09999999"}  # 공고번호가 다른 행은 안 씀
        client = FakeClient({
            F.BID_NOTICE_OPERATIONS["공사"]: [older, other, notice],
            F.BASIS_AMOUNT_OPERATIONS["공사"]: [BASIS_ROW],
            F.LICENSE_LIMIT_OPERATION: [{"bidNtceNo": "R26BK01752065", "bidNtceOrd": "001", "lcnsLmtNm": "전기공사업/0040"},
                                        {"bidNtceNo": "R26BK01752065", "bidNtceOrd": "000", "lcnsLmtNm": "옛차수업/9999"}],
        })
        out = bidcalc.lookup(client, " r26bk01752065-001 ")
        self.assertEqual(out["notice_no"], "R26BK01752065")
        self.assertEqual(out["notice_ord"], "001")
        self.assertEqual(out["lower_rate"], 89.745)
        self.assertEqual(out["work_type"], "공사")
        self.assertEqual(out["basis"], 184521490)
        self.assertEqual((out["range_lo"], out["range_hi"]), (-3, 3))
        self.assertTrue(out["a_applies"])
        self.assertEqual(out["net_cost"], 145398641)
        self.assertEqual(out["method"], "mois")
        self.assertEqual(out["agency"], "local")
        self.assertEqual(out["est_price"], 167746809)
        self.assertEqual(out["contract_method"], "제한경쟁")
        self.assertEqual(out["licenses"], ["전기공사업"])
        self.assertEqual(out["notes"], [])
        self.assertEqual(client.calls[0][1], {"inqryDiv": "2", "bidNtceNo": "R26BK01752065"})
        # 면허제한은 공고번호 조회가 안 돼 등록일 하루치로 받음(실측 resultCode=08)
        lic = [c for c in client.calls if c[0] == F.LICENSE_LIMIT_OPERATION]
        self.assertEqual(lic[0][1], {"inqryDiv": "1", "inqryBgnDt": "202610010000", "inqryEndDt": "202610012359"})

    def test_lookup_not_found_keeps_going(self):
        client = FakeClient({}, fail={F.BID_NOTICE_OPERATIONS["물품"]})
        out = bidcalc.lookup(client, "R26BK00000001")
        self.assertNotIn("basis", out)
        self.assertEqual(len(out["notes"]), 2)
        self.assertEqual(out["method"], "pps")
        self.assertFalse(out["found"])
        self.assertFalse(out["lookup_error"])  # 물품만 오류 — 공고번호 오입력으로 봄

    def test_lookup_all_errors_is_lookup_error(self):
        """공고 조회가 전부 오류면 오입력이 아니라 나라장터 조회 실패(화면이 다른 경고를 띄움)."""
        client = FakeClient({}, fail=set(F.BID_NOTICE_OPERATIONS.values()))
        out = bidcalc.lookup(client, "R26BK00000001")
        self.assertFalse(out["found"])
        self.assertTrue(out["lookup_error"])

    def test_attachment_fallback_for_goods(self):
        """물품 공고: 기초금액 API가 비면 첨부 공고문에서 기초금액·범위를 읽음(2026-10-15 서당골천 R26BK01764801)."""
        notice = {"bidNtceNo": "R26BK01764801", "bidNtceOrd": "000", "bidNtceNm": "서당골천 경관개선을 위한 조형물 제작·설치",
                  "sucsfbidLwltRate": "84.245", "sucsfbidMthdAppStd": "지방자치단체 입찰시 낙찰자 결정기준",
                  "ntceSpecFileNm1": "규격서.hwp", "ntceSpecDocUrl1": "https://x/1",
                  "ntceSpecFileNm2": "입찰공고문(아주동).hwpx", "ntceSpecDocUrl2": "https://x/2"}
        text = (" | 마. 예 산 액: | 금139,000,000원 | 추정가격: | 금126,363,637원\n |   ※ 기초금액 : 금 | 139,000,000원 |\n"
                "가. 예정가격은 「지방자치단체 입찰 및 계약 집행기준」제2장에 의거 기초금액의 ±3%의 범위 내에서 정한 15개\n"
                "예정가격 이하 낙찰하한율(84.245%) 이상 최저가 입찰자 순으로 심사")
        read = []

        def fetch(session, att, timeout=30.0):
            read.append(att["file_name"])
            return bidcalc.attachments.AttachmentText(seq=att["seq"], file_name=att["file_name"], url=att["url"], ext=att["ext"],
                                                      text=text if "공고" in att["file_name"] else "규격")
        client = FakeClient({F.BID_NOTICE_OPERATIONS["물품"]: [notice]})
        out = bidcalc.lookup(client, "R26BK01764801", fetch=fetch)
        self.assertEqual(read, ["입찰공고문(아주동).hwpx"])  # 공고문부터, 다 찾으면 멈춤
        self.assertEqual(out["basis"], 139000000)
        self.assertEqual(out["lower_rate"], 84.245)  # API 값 그대로
        self.assertEqual((out["range_lo"], out["range_hi"]), (-3, 3))
        self.assertEqual(out["basis_source"], "첨부 입찰공고문(아주동).hwpx")
        self.assertEqual(out["method"], "mois")
        self.assertTrue(any("첨부" in n for n in out["notes"]))

    def test_parse_notice_text(self):
        self.assertEqual(bidcalc.parse_notice_text("기초금액(부가세 포함): 85,300,000원 / 기초금액의 ±2% / 낙찰하한율 : 87.745%"),
                         {"basis": 85300000, "lower_rate": 87.745, "range_lo": -2, "range_hi": 2})
        self.assertEqual(bidcalc.parse_notice_text("기초금액은 개찰 전 나라장터에 공개합니다."), {})

    def test_guess_method(self):
        self.assertEqual(bidcalc.guess_method("", -2, 2), "pps")
        self.assertEqual(bidcalc.guess_method("", -3, 3), "mois")
        self.assertEqual(bidcalc.guess_method("지방자치단체 입찰시 낙찰자 결정기준", -2, 2), "mois")
        self.assertEqual(bidcalc.guess_method("", None, None), "pps")

    def test_method_by_agency(self):
        """발주기관이 지방자치단체·교육청이면 행자부(2026-10-22 제보: 울산광역시 울산안전체험관 협상 공고가 조달청으로 나옴)."""
        협상 = "협상에의한계약-협상에 의한 낙찰자 결정"
        for name in ["울산광역시 울산안전체험관", "경상남도 거제시", "강원특별자치도 정선군", "서울특별시 종로구", "경기도교육청",
                     "세종특별자치시", "제주특별자치도", "거제시", "종로구청", "충청북도 교육청"]:
            self.assertEqual(bidcalc.method_reason(협상, None, None, [name]), ("mois", "적용 기준 문구 없음 — 수요기관이 지방자치단체·교육청"), name)
        for name in ["국방부 국군재정관리단", "부산지방국토관리청", "한국도로공사", "조달청", "국립중앙박물관", "한국관광공사"]:
            self.assertEqual(bidcalc.guess_method(협상, None, None, [name]), "pps", name)
        # 수요기관이 비고 공고기관만 지자체여도
        self.assertEqual(bidcalc.guess_method("", None, None, ["", "울산광역시"]), "mois")

    def test_judge_order(self):
        """발주기관 최종 판단(2026-10-23 사용자 결정) — 낙찰자 결정기준 문구 → 공고문 인용 법령 → 기관 이름 → ±3% → 국가."""
        부천, 경기조달 = "경기도 부천시 소사구", "경기지방조달청"
        j = bidcalc.judge("", (None, ""), 부천, 경기조달)
        self.assertEqual((j["kind"], j["method"]), ("local", "mois"))
        self.assertIn("조달청 대행", j["note"])
        self.assertTrue(j["note"].startswith("공고기관 경기지방조달청 · 수요기관 경기도 부천시 소사구 → 지방계약"))
        # 공고문이 국가계약법을 인용하면 기관 이름보다 먼저 — 조달청 대행 공고도 국가계약
        j = bidcalc.judge("", ("national", "국가를 당사자로 하는 계약에 관한 법률"), 부천, 경기조달)
        self.assertEqual((j["kind"], j["method"]), ("national", "pps"))
        self.assertIn("공고문 인용 법령", j["reason"])
        # 나라장터 결정기준 문구가 가장 먼저
        self.assertEqual(bidcalc.judge("지방자치단체 입찰시 낙찰자 결정기준", ("national", "국가계약법"), "국립중앙박물관", "조달청")["kind"], "local")
        self.assertEqual(bidcalc.judge("조달청 적격심사세부기준", (None, ""), 부천, 부천)["kind"], "national")
        # 문구·법령이 없으면 예전처럼 기관 이름 → ±3%
        self.assertEqual(bidcalc.judge("", (None, ""), "", "울산광역시")["kind"], "local")
        self.assertEqual(bidcalc.judge("", (None, ""), "국립중앙박물관", "조달청")["kind"], "national")
        self.assertEqual(bidcalc.judge("", (None, ""), "○○도시공사", "○○도시공사", -3, 3)["kind"], "local")
        # 지방조달청 이름이 지자체로 잘못 걸리지 않음
        for name in ["조달청", "서울지방조달청", "부산지방조달청", "경기지방조달청"]:
            self.assertEqual(bidcalc.guess_method("", None, None, [name]), "pps", name)

    def test_law_kind(self):
        local = "가. 예정가격은 「지방자치단체 입찰 및 계약 집행기준」제2장에 의거 기초금액의 ±3%의 범위 내에서 정한 15개"
        self.assertEqual(bidcalc.law_kind(local), ("local", "지방자치단체 입찰 및 계약 집행기준"))
        national = "예정가격은 「국가를 당사자로 하는 계약에 관한 법률 시행령」 제7조의2에 따라 복수예비가격 15개 중"
        self.assertEqual(bidcalc.law_kind(national)[0], "national")
        # 예정가격 문장 근처가 우선 — 멀리 떨어진 제재 조항의 국가계약법은 안 봄
        far = local + " " + "가" * 400 + " 부정당업자는 국가를 당사자로 하는 계약에 관한 법률에 따라 제재"
        self.assertEqual(bidcalc.law_kind(far)[0], "local")
        # 예정가격 근처에 둘 다 있고 문서 전체에도 둘 다면 못 정함
        both = "예정가격은 지방계약법 및 국가계약법에 따라"
        self.assertEqual(bidcalc.law_kind(both), (None, ""))
        self.assertEqual(bidcalc.law_kind("예정가격은 기초금액의 ±2% 범위"), (None, ""))

    def test_reads_notice_even_when_api_has_values(self):
        """나라장터 값이 다 있어도 공고문을 늘 읽음(2026-10-23) — 적용 법령으로 판단, 값이 다르면 안내(나라장터 값 유지)."""
        notice = {"bidNtceNo": "R26BK01700001", "bidNtceOrd": "000", "bidNtceNm": "○○ 전시물 제작설치", "sucsfbidLwltRate": "87.745",
                  "dminsttNm": "경기도 부천시", "ntceInsttNm": "경기지방조달청",
                  "ntceSpecFileNm1": "입찰공고문.hwp", "ntceSpecDocUrl1": "https://x/1"}
        basis = {**BASIS_ROW, "bidNtceNo": "R26BK01700001", "bssamt": "100000000", "rsrvtnPrceRngBgnRate": "-2", "rsrvtnPrceRngEndRate": "+2"}
        text = "기초금액 : 금 100,000,000원\n예정가격은 「국가를 당사자로 하는 계약에 관한 법률 시행령」에 따라 기초금액의 ±2% … 낙찰하한율 88.745%"
        read = []

        def fetch(session, att, timeout=30.0):
            read.append(att["file_name"])
            return bidcalc.attachments.AttachmentText(seq=att["seq"], file_name=att["file_name"], url=att["url"], ext=att["ext"], text=text)
        client = FakeClient({F.BID_NOTICE_OPERATIONS["공사"]: [notice], F.BASIS_AMOUNT_OPERATIONS["공사"]: [basis]})
        out = bidcalc.lookup(client, "R26BK01700001", fetch=fetch)
        self.assertEqual(read, ["입찰공고문.hwp"])
        self.assertEqual(out["law_kind"], "national")
        self.assertEqual((out["agency"], out["method"]), ("national", "pps"))
        self.assertEqual(out["lower_rate"], 87.745)  # 나라장터 값 유지
        self.assertTrue(any("낙찰하한율: 나라장터 87.745 · 첨부 '입찰공고문.hwp' 88.745" in n for n in out["notes"]), out["notes"])

    def test_missing_values_noted(self):
        notice = {"bidNtceNo": "R26BK01700002", "bidNtceOrd": "000", "bidNtceNm": "협상 공고", "dminsttNm": "울산광역시"}
        out = bidcalc.lookup(FakeClient({F.BID_NOTICE_OPERATIONS["용역"]: [notice]}), "R26BK01700002", fetch=None)
        self.assertIsNone(out.get("range_lo"))
        self.assertTrue(any(n.startswith("변동폭을 나라장터·첨부 공고문에서 찾지 못함") for n in out["notes"]))
        self.assertEqual(out["method"], "mois")


@unittest.skipUnless(shutil.which("node"), "node 없음 — 화면 계산 테스트 건너뜀")
class BidCalcJsTest(unittest.TestCase):
    def test_js_calculations(self):
        res = subprocess.run(["node", str(ROOT / "tests" / "js" / "bidcalc_test.js")], capture_output=True, text=True, timeout=120)
        self.assertEqual(res.returncode, 0, res.stderr or res.stdout)


if __name__ == "__main__":
    unittest.main()
