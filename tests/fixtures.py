"""테스트용 공고 원문 픽스처.

실제 나라장터 응답의 **필드명과 값 형식**을 그대로 따른다 (실측 확인된 표기).
실제 공고 데이터가 아니라 형식 검증용으로 구성한 것이다.
"""

from __future__ import annotations

from typing import Any

NEGO = "협상에의한계약-협상에 의한 낙찰자 결정"
NEGO_SW = "협상에의한계약-협상에 의한 낙찰자 결정(SW사업)"
QUALIFY = "적격심사제-추정가격 10억원 미만 3억원 이상인 공사(실적에 의한 경쟁입찰 이외, 전문공사)"
DESIGN_CONTEST = "설계공모-일반설계공모"


def notice(
    no: str,
    ord_: str = "000",
    title: str = "테스트 공고",
    award: str = NEGO,
    kind: str = "등록공고",
    **overrides: Any,
) -> dict[str, Any]:
    base: dict[str, Any] = {
        "bidNtceNo": no,
        "bidNtceOrd": ord_,
        "bidNtceNm": title,
        "ntceInsttNm": "테스트기관",
        "dminsttNm": "테스트수요기관",
        "sucsfbidMthdNm": award,
        "cntrctCnclsMthdNm": "제한경쟁",
        "bidMethdNm": "전자입찰",
        "ntceKindNm": kind,
        "reNtceYn": "N",
        "presmptPrce": "500000000",
        "asignBdgtAmt": "550000000",
        "bidNtceDt": "2026-09-01 10:00:00",
        "bidQlfctRgstDt": "2026-09-20 18:00:00",
        "bidClseDt": "2026-09-25 10:00:00",
        "cmmnSpldmdMethdCd": "01",
        "cmmnSpldmdMethdNm": "(전자)공동이행 또는 분담이행",
        "techAbltEvlRt": "90",
        "bidPrceEvlRt": "10",
        "indstrytyLmtYn": "Y",
        "bidNtceDtlUrl": f"https://www.g2b.go.kr/detail/{no}",
        "ntceSpecFileNm1": "입찰공고문.hwp",
        "ntceSpecDocUrl1": f"https://www.g2b.go.kr/.../downloadFile.do?bidPbancNo={no}&fileSeq=1",
        "ntceSpecFileNm2": "제안요청서.pdf",
        "ntceSpecDocUrl2": f"https://www.g2b.go.kr/.../downloadFile.do?bidPbancNo={no}&fileSeq=2",
        # 개인정보 — 저장 전에 제거되어야 한다
        "ntceInsttOfclNm": "홍길동",
        "ntceInsttOfclTelNo": "02-000-0000",
        "ntceInsttOfclEmailAdrs": "someone@example.go.kr",
    }
    base.update(overrides)
    return base


def service_notices() -> list[dict[str, Any]]:
    return [
        # 1) 정상 협상 용역 — 통과해야 함
        notice("R26TEST00001", title="○○과학관 전시물 제작 및 설치"),
        # 2) 같은 공고의 변경공고 1차 — 최신 차수만 남아야 함
        notice("R26TEST00002", "000", title="△△박물관 전시디자인"),
        notice(
            "R26TEST00002",
            "001",
            title="△△박물관 전시디자인",
            kind="변경공고",
            chgNtceRsn="제안서 제출 마감일 연장",
        ),
        # 3) 취소공고 — 제외되어야 함
        notice("R26TEST00003", title="□□체험관 전시콘텐츠 제작", kind="취소공고"),
        # 4) 협상이 아님 (적격심사) — 제외되어야 함
        notice("R26TEST00004", title="◇◇박물관 전시시설 공사", award=QUALIFY),
        # 5) 설계공모 — 인접 스코프로 분리되어야 함
        notice("R26TEST00005", title="◎◎전시관 설계", award=DESIGN_CONTEST),
        # 6) 제외키워드('유지보수') — 업역 스크리닝에서 제외
        notice("R26TEST00006", title="○○박물관 전시물 유지보수 용역"),
        # 7) 예산 미달 (5천만원) — 최소예산 필터에서 제외
        notice("R26TEST00007", title="○○과학관 체험콘텐츠 제작", presmptPrce="50000000", asignBdgtAmt="50000000"),
        # 8) 공동수급 불허 + 직찰 + 재공고
        notice(
            "R26TEST00008",
            title="◆◆전시홍보관 제작설치",
            kind="재공고",
            reNtceYn="Y",
            bidMethdNm="직찰",
            cmmnSpldmdMethdNm="(없음)공동수급불허",
            bidQlfctRgstDt="",
            bidClseDt="",
            techAbltEvlRt="80",
        ),
        # 9) SW사업 변형 + 공동수급협정 마감이 입찰마감보다 이른 케이스
        notice(
            "R26TEST00009",
            title="○○과학관 미디어아트 체험콘텐츠 개발",
            award=NEGO_SW,
            cmmnSpldmdAgrmntClseDt="2026-09-19 18:00:00",
        ),
        # 10) 키워드 미매칭 — 제외되어야 함
        notice("R26TEST00010", title="상하수도 관로 정밀안전진단"),
    ]


def license_rows() -> list[dict[str, Any]]:
    """면허제한정보 응답. '[업종명/코드]' 형식과 그룹 구조를 재현한다."""
    return [
        # 공고1: 실내건축공사업 단일 그룹 → 지일 보유 → 충족
        {
            "bidNtceNo": "R26TEST00001",
            "lmtGrpNo": "1",
            "lmtSno": "1",
            "lcnsLmtNm": "실내건축공사업/4990",
            "permsnIndstrytyList": "[실내건축공사업/4990]",
        },
        # 공고2: 한 그룹 안 두 행(모두 필요) — 둘 다 지일 보유 업종 → 충족
        {
            "bidNtceNo": "R26TEST00002",
            "lmtGrpNo": "1",
            "lmtSno": "1",
            "lcnsLmtNm": "실내건축공사업/4990",
            "permsnIndstrytyList": "[실내건축공사업/4990]",
        },
        {
            "bidNtceNo": "R26TEST00002",
            "lmtGrpNo": "1",
            "lmtSno": "2",
            "lcnsLmtNm": "산업디자인전문회사(환경디자인분야)/4442",
            "permsnIndstrytyList": "[산업디자인전문회사(환경디자인분야)/4442]",
        },
        # 공고9: 한 그룹 안 두 행(모두 필요) — 둘 다 미보유 → 미달 요건 2건.
        # 두 번째 행은 허용업종이 있어 "기계설비공사업 또는 전기공사업"이다.
        {
            "bidNtceNo": "R26TEST00009",
            "lmtGrpNo": "1",
            "lmtSno": "1",
            "lcnsLmtNm": "건축공사업/0002",
            "permsnIndstrytyList": "[건축공사업/0002]",
        },
        {
            "bidNtceNo": "R26TEST00009",
            "lmtGrpNo": "1",
            "lmtSno": "2",
            "lcnsLmtNm": "기계설비공사업/0009",
            "permsnIndstrytyList": "[기계설비공사업/0009][전기공사업/0007]",
        },
    ]


def substring_overmatch_rows() -> list[dict[str, Any]]:
    """'건축공사업(0002)' 요건 — 보유 '실내건축공사업'의 부분 문자열이지만 다른 면허다.

    예전 양방향 부분일치에선 충족으로 잘못 나왔다. 지금은 미달이어야 한다.
    """
    return [
        {
            "bidNtceNo": "R26SUBSTR",
            "lmtGrpNo": "1",
            "lmtSno": "1",
            "lcnsLmtNm": "건축공사업/0002",
            "permsnIndstrytyList": "[건축공사업/0002]",
        }
    ]
