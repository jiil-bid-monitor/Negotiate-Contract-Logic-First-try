"""서류 폴더의 경쟁입찰참가자격등록증 PDF로 보유 자격 더하기(2026-11-24, nego/regcert.py)."""

import unittest
from datetime import date

from nego import qualify, regcert

# 나라장터 출력본 글 모양(pypdf) — 이름·날짜가 줄에서 잘린 것 그대로
TEXT = """입찰참가 등록물품
등록일 세부
품명번호 세부품명 제조
2025/10/15 4924159701 조합놀이대 Y 직접생산증명
서
2025/10/
13
2027/10/
12
2025/10/
13
2027/10/
12
2025/11/06 4620010401 방재훈련용모의시
스템 Y 공장등록증 2025/08/
14
2026/08/
14
2025/11/
06
2026/08/
14
* 등록유효기간은 ...
등록업종 및 시공능력
업종명 등록번호
1469(소프트웨어사업자(디지털콘텐츠개
발서비스사업))
제
B23-1
호
2023/09/11 2099/12/31 정상
4991(금속창호·지붕건축물조립공사
업) 제1호 2024/04/03 정상
주력분야
"""


class RegCertTest(unittest.TestCase):
    def test_parse(self):
        got = regcert.parse(TEXT)
        self.assertEqual(got["products"], [
            {"code": "4924159701", "name": "조합놀이대", "until": "2027-10-12"},
            {"code": "4620010401", "name": "방재훈련용모의시스템", "until": "2026-08-14"}])
        self.assertEqual(got["industries"], [
            {"code": "1469", "name": "소프트웨어사업자(디지털콘텐츠개발서비스사업)"},
            {"code": "4991", "name": "금속창호·지붕건축물조립공사업"}])

    def test_merge_adds_only_new_and_valid(self):
        held = {"heldIndustries": [{"code": "1469", "name": "소프트웨어"}], "heldProducts": []}
        out = regcert.merge(held, regcert.parse(TEXT), today=date(2026, 11, 24))
        self.assertEqual([e["code"] for e in out["heldIndustries"]], ["1469", "4991"])
        self.assertEqual([e["code"] for e in out["heldProducts"]], ["4924159701"])  # 지난 물품은 안 더함
        self.assertEqual(held["heldIndustries"], [{"code": "1469", "name": "소프트웨어"}])  # 원본은 그대로
        # 더한 공사업 면허는 '보유 면허로 수집' 경로에도 들어감
        self.assertEqual(qualify.construction_codes(out), frozenset({"4991"}))

    def test_jiil_unchanged(self):
        held = {"heldIndustries": []}
        self.assertIs(regcert.apply(held, "jiil"), held)
        self.assertEqual(qualify.construction_codes(None), qualify.CONSTRUCTION_LICENSE_CODES)


if __name__ == "__main__":
    unittest.main()
