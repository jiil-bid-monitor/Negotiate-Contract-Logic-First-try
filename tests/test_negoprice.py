"""협상에 의한 계약 가격 점수(11/12 — 당진교육종합센터 R26BK01802876 제안요청서의 입찰가격 평정산식 모양)."""

from __future__ import annotations

import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from nego import bidcalc, negoprice  # noqa: E402
from nego import fields as F  # noqa: E402
from tests.test_bidcalc import FakeClient  # noqa: E402

RFP = (" | 입찰가격평가(10) | 입찰가격 | ○ 제안가격 평가 | 10 | 합   계 | 100 |"
       " 나. 입찰가격 평가(10점) |    「지방자치단체 입찰시 낙찰자 결정기준(행정안전부 예규)」제7장 협상에 의한 계약 낙찰자 결정기준에 의함 |"
       " 《 입찰가격 평정산식 》 |   ① 입찰가격이 예정가격(예정가격을 작성하지 않은 경우에는 추정가격에 부가가치세를 포함한 금액으로 한다."
       " “이하같다”)의 100분의 80 이상인 경우 평점 =  |      입찰가격평가 배점한도 × ( 최저입찰가격 / 해당입찰가격 ) |"
       "   ② 입찰가격이 예정가격의 100분의 80 미만인 경우 평점 =  |      [입찰가격평가 배점한도 × (최저입찰가격 / 예정가격의 80% 상당가격 )]"
       "＋[2 × (예정가격의 80% 상당가격-해당입찰가격)/(예정가격의80%상당가격-예정가격의70%상당가격) |"
       "   ※ 1. 최저입찰가격은 유효한 입찰자중 최저입찰가격으로 하되, 입찰가격이 예정가격의 100분의 70미만일 경우에는 100분의 70으로 계산한다."
       " |      2. 해당입찰가격이 예정가격의 100분의 70미만일 경우에는 배점한도의 30%에 해당하는 평점을 부여한다.")


class NegoPriceTest(unittest.TestCase):
    def test_parse_rule_reads_all_numbers(self):
        r = negoprice.parse_rule(RFP)
        self.assertEqual({k: r[k] for k in ("weight", "upper", "lower", "bonus", "floor", "under")},
                         {"weight": 10, "upper": 80, "lower": 70, "bonus": 2, "floor": 70, "under": 30})
        self.assertEqual(set(r["found"]), {"weight", "upper", "lower", "bonus", "floor", "under"})
        self.assertEqual((r["kind"], r["base"]), ("지방", "예정가격"))
        self.assertIsNone(negoprice.parse_rule("적격심사 낙찰하한율 87.745%"))

    def test_score_bands(self):
        r = negoprice.parse_rule(RFP)
        s = lambda mine, rival: negoprice.score(mine, rival, 100.0, r)
        self.assertEqual(s(100, 90), 9.0)       # 최저가 ÷ 내 가격
        self.assertEqual(s(90, 95), 10.0)       # 내가 최저면 만점
        self.assertEqual(s(75, 90), 10.375)     # 80% 미만: 10 × 75/80 + 2 × 5/10
        self.assertEqual(s(69, 90), 3.0)        # 70% 미만: 배점의 30%
        self.assertEqual(s(90, 60), 7.7778)     # 다른 업체 최저가가 70% 미만이면 70%로

    def test_js_matches_python(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node 없음")
        js = ("const C=require(process.argv[1]);const r={weight:10,upper:80,lower:70,bonus:2,floor:70,under:30};"
              "console.log(JSON.stringify([[100,90],[90,95],[75,90],[69,90],[90,60]].map(([m,x])=>+C.negoScore(m,x,100,r).toFixed(4))))")
        out = subprocess.run([node, "-e", js, str(ROOT / "webapp/static/bidcalc.js")], capture_output=True, text=True, check=True).stdout
        self.assertEqual(out.strip(), "[9,10,10.375,3,7.7778]")

    def test_lookup_attaches_nego_table_data(self):
        notice = {"bidNtceNo": "R26BK01700009", "bidNtceOrd": "000", "bidNtceNm": "가상 전시체험 콘텐츠 제작·설치",
                  "sucsfbidMthdNm": "협상에의한계약", "presmptPrce": "315836364",
                  "ntceSpecFileNm1": "입찰공고문.hwp", "ntceSpecDocUrl1": "https://x/1",
                  "ntceSpecFileNm2": "제안요청서.hwp", "ntceSpecDocUrl2": "https://x/2"}

        def fetch(session, att, timeout=30.0):
            return bidcalc.attachments.AttachmentText(seq=att["seq"], file_name=att["file_name"], url=att["url"], ext=att["ext"],
                                                      text=RFP if "제안" in att["file_name"] else "공고")
        out = bidcalc.lookup(FakeClient({F.BID_NOTICE_OPERATIONS["용역"]: [notice]}), "R26BK01700009", fetch=fetch)
        self.assertTrue(out["nego"]["rule_found"])
        self.assertEqual(out["nego"]["rule"]["file"], "제안요청서.hwp")
        self.assertEqual(out["nego"]["base"], round(315836364 * 1.1))  # 기초금액이 없으면 추정가격 × 1.1
        self.assertFalse(any("낙찰하한율을" in n or "변동폭을" in n for n in out["notes"]))


if __name__ == "__main__":
    unittest.main()
