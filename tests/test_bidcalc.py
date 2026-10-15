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
                  "totPrdprcNum": "15"}
        older = {**notice, "bidNtceOrd": "000", "sucsfbidLwltRate": "87.745"}
        other = {**notice, "bidNtceNo": "R26BK09999999"}  # 공고번호가 다른 행은 안 씀
        client = FakeClient({
            F.BID_NOTICE_OPERATIONS["공사"]: [older, other, notice],
            F.BASIS_AMOUNT_OPERATIONS["공사"]: [BASIS_ROW],
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
        self.assertEqual(out["notes"], [])
        self.assertEqual(client.calls[0][1], {"inqryDiv": "2", "bidNtceNo": "R26BK01752065"})

    def test_lookup_not_found_keeps_going(self):
        client = FakeClient({}, fail={F.BID_NOTICE_OPERATIONS["물품"]})
        out = bidcalc.lookup(client, "R26BK00000001")
        self.assertNotIn("basis", out)
        self.assertEqual(len(out["notes"]), 2)
        self.assertEqual(out["method"], "pps")

    def test_guess_method(self):
        self.assertEqual(bidcalc.guess_method("", -2, 2), "pps")
        self.assertEqual(bidcalc.guess_method("", -3, 3), "mois")
        self.assertEqual(bidcalc.guess_method("지방자치단체 입찰시 낙찰자 결정기준", -2, 2), "mois")
        self.assertEqual(bidcalc.guess_method("", None, None), "pps")


@unittest.skipUnless(shutil.which("node"), "node 없음 — 화면 계산 테스트 건너뜀")
class BidCalcJsTest(unittest.TestCase):
    def test_js_calculations(self):
        res = subprocess.run(["node", str(ROOT / "tests" / "js" / "bidcalc_test.js")], capture_output=True, text=True, timeout=120)
        self.assertEqual(res.returncode, 0, res.stderr or res.stdout)


if __name__ == "__main__":
    unittest.main()
