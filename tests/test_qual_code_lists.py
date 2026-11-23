"""2026-11-23 사용자 제보 — 주간 메일 자격 팝업 이름표·묶음 오류 4건."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nego import qualify  # noqa: E402


def names(result):
    return [g.allowed_names for g in result.missing_groups]


class TestCodeNameLists(unittest.TestCase):
    def test_code_then_name_comma_list(self):
        """영축산 작은도서관 R26BD00302194 — 첫 번호만 읽고 이름표가 '반드시 G2B'였음."""
        item = ("라. 아래 직접생산확인증명서를 모두 소지한 업체\n〇 반드시 G2B 세부품명번호 5610150201(소파), "
                "5610150701(책장), 5610170601(회의용탁자)을 제조물품으로 입찰 참가 등록한 업체")
        r = qualify.evaluate_attachment_text([item], {"5610150701"}, {"5610150701": "책장"}, {})
        self.assertEqual(names(r), [["소파(5610150201)"], ["회의용탁자(5610170601)"]])

    def test_colon_label_and_item_wide_one_or_more(self):
        """상환능력 예측 모형 R26BD00303824 — '이름 미확인(7528)', '중 하나 이상'인데 모두 필요로 봤음."""
        item = ("ㅇ 입찰일 전일까지 다음의 업종* 중 하나 이상의 입찰자격을 등록한 업체\n"
                "    * 등록업종코드: 개인신용평가업(7528), 개인사업자신용평가업(7529), 소프트웨어사업자(컴퓨터관련서비스사업)(1468)")
        r = qualify.evaluate_attachment_text([item], {"1468"}, {"1468": "소프트웨어사업자"}, {})
        self.assertTrue(r.passes)
        self.assertEqual(names(r), [])
        miss = qualify.evaluate_attachment_text([item], set(), {}, {})
        self.assertIn("개인신용평가업(7528)", miss.missing_groups[0].allowed_names)

    def test_all_required_head_keeps_and(self):
        """'다음 각 호의 자격을 모두 갖춘' 항목은 안쪽 '중 하나 이상'이 항목 전체를 OR로 만들지 않는다."""
        item = ("가. 다음 각 호의 자격을 모두 갖춘 업체 가-① 비디오물제작업(업종코드 3244), 이러닝콘텐츠업(업종코드: 6527) "
                "중 하나 이상으로 등록된 업체 가-② 직접생산확인증명서 [세부품명: 동영상제작서비스(세부제품번호 : 8213160301)]")
        r = qualify.evaluate_attachment_text([item], {"3244"}, {}, {})
        self.assertIn("동영상제작서비스(8213160301)", [n for g in names(r) for n in g])

    def test_underscore_before_code_paren(self):
        """대농근린공원 R26BK01836754 — '설계업(종합설계)_(업종코드 1105)' 이름표가 '_'."""
        item = "4) 전력시설물의 설계업(종합설계)_(업종코드 1105) 또는 설계업(전문설계 1종)_(업종코드 1106) 등록을 필한 업체"
        r = qualify.evaluate_attachment_text([item], set(), {}, {})
        self.assertEqual(names(r), [["전력시설물의 설계업(종합설계)(1105)", "설계업(전문설계1종)(1106)"]])

    def test_alternating_combo_shares_common_code(self):
        """'디자인서비스, 조형물 또는 디자인서비스, 조립식구조물' = 디자인서비스 필수 + 조형물·조립식구조물 중 하나."""
        item = ("2) 반드시 나라장터(G2B)에 디자인서비스(8214150201), 조형물(6012100201) 또는 디자인서비스(8214150201), "
                "조립식구조물(3020179601)을 제조물품으로 입찰참가 등록한 업체")
        r = qualify.evaluate_attachment_text([item], {"8214150201", "6012100201"}, {}, {})
        self.assertTrue(r.passes)
        self.assertEqual(names(r), [])
        r2 = qualify.evaluate_attachment_text([item], {"6012100201"}, {}, {})
        self.assertEqual(names(r2), [["디자인서비스(8214150201)"]])


if __name__ == "__main__":
    unittest.main()
