import unittest

from under100m.sections import find_sections

NOTICE = """입찰공고문
1. 입찰에 부치는 사항
 가. 입찰명 : 영인본 제작
2. 입찰참가자격 :
 가. 사업장 소재지가 서울특별시 또는 수도권에 위치한 업체
 나. 최근 5년 이내 유물 복제 20건 이상의 실적이 있는 업체
3-1. 제출서류
□ 아래의 서류를 모두 제출
 1) 사업자등록증 사본 1부
 2) 영인 및 족자 장황 제작사양서 1부
4. 기 타 :
 가. 낙찰된 업체는 낙찰통보일로부터 10일 이내에 계약을 체결하여야 함.
2026. 11. 09.
숭실대학교 총무처장
"""


class SectionsTest(unittest.TestCase):
    def test_notice_sections(self):
        found = find_sections([("공고문.hwpx", NOTICE), ("공고문.pdf", NOTICE.replace("가.", "가 ."))])
        submit = found["제출 서류"]
        self.assertEqual(len(submit), 1)  # PDF 사본은 같은 내용이라 한 번만
        self.assertIn("2) 영인 및 족자 장황 제작사양서 1부", submit[0][2])
        qual = found["참가 자격"][0][2]
        self.assertIn("가. 사업장 소재지가 서울특별시 또는 수도권에 위치한 업체", qual)
        self.assertFalse(any("사업자등록증" in line for line in qual))  # 제출서류 절은 자격 쪽에서 뺌
        etc = found["유의사항·기타"][0][2]
        self.assertEqual(etc, ["가. 낙찰된 업체는 낙찰통보일로부터 10일 이내에 계약을 체결하여야 함."])  # 날짜·기관장 줄 빠짐


if __name__ == "__main__":
    unittest.main()


class MethodTailHeadingTest(unittest.TestCase):
    def test_heading_with_submit_method_after_colon(self):  # 마곡 어린이집 R26BK01812866 (워드 공고문)
        text = ("5. 입찰서 제출 및 개찰\n다. 개찰일시 및 장소: 2026년 11월 12일(목) 16:00\n라. 입찰 서류제출 : 나라장터 전자입찰서로 제출\n"
                "1) 공기 준수 서약서 [서식1]\n2) 입찰 내역서\n마. 입찰서의 제출확인은 조달청 전자입찰시스템 웹 송신함에서 반드시 확인하시기 바랍니다.\n")
        lines = find_sections([("공고서.docx", text)])["제출 서류"][0][2]
        self.assertEqual(lines, ["1) 공기 준수 서약서 [서식1]", "2) 입찰 내역서"])
