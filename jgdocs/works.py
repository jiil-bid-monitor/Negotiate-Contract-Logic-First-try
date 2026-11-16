"""시설공사 적격심사 표준틀(지방자치단체 입찰시 낙찰자 결정기준 제2장의1, 제344호 2025.12.1. 시행 확인).

    JG_GROUP=10억미만 py build_gs.py <출력 폴더>   # [별표 5·6·7] 추정가격으로 자동(2억·4억 기준)
    JG_GROUP=10억이상 py build_gs.py <출력 폴더>   # [별표 4] 10억 이상 30억 미만(지일 투찰 이력 2023~2026에 30억 이상 없음)

물품 틀(build_jg)의 서식 도구·회사 읽기·증빙 탭을 그대로 씀. 시공경험은 '실적 제한 없는' 공고 방식(업종평가비율 100% 가정),
실적 제한 공고는 담당자가 점수를 직접 적음. 경영상태는 신용평가 방법.
"""

from __future__ import annotations

import datetime as dt
import os
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Border, Font, PatternFill, Side
from openpyxl.utils.indexed_list import IndexedList
from openpyxl.worksheet.datavalidation import DataValidation

from . import goods as jg
from .form import FONT, NC, Form

B = jg.B
GROUP = os.environ.get("JG_GROUP", "10억미만")
FROM_NOTICE = {"수신(발주기관)", "입찰공고일", "업종평가비율(%)", "입찰공고번호", "공사명", "수요기관", "입찰일시", "공사현장 시·군", "입찰금액", "추정가격", "예정가격", "A값"}
FROM_COMPANY = {"회사명", "대표자", "주소", "전화", "팩스", "신용평가등급", "신용평가 유효기간", "실적인정기간 4년 이상", "특별신인도"}
FIELDS = [
    (4, "입찰공고번호", "나라장터 공고번호-차수(신청서 '관리번호')", None),
    (5, "공사명", "", None),
    (6, "수요기관", "", None),
    (7, "입찰일시", "예: 2026-11-20 11:00 — 실적 기간·접근성 기준일", None),
    (8, "공사현장 시·군", "예: 정선군 — 접근성(본점이 현장 시·군 또는 인접 시·군이면 가점)", None),
    (9, "업종", "공고의 업종(예: 실내건축공사업)", None),
    (10, "업종 구분", "종합공사 / 전문·그밖(실내건축·정보통신 등은 전문·그밖) — 시공경험 등급표가 다름", "전문·그밖"),
    (11, "입찰금액", "숫자만", None),
    (12, "추정가격", "숫자만 — 평가 기준(별표)을 고르는 값", None),
    (13, "예정가격", "숫자만 — 개찰 결과에서", None),
    (14, "A값", "공고·기초금액 발표의 A값(국민연금·건강·퇴직공제·노인장기요양·산업안전보건·안전관리·품질관리비 합) — 없으면 0", 0),
    (15, "실적 제한 공고", "예 / 아니오 — '예'면 시공경험은 공고 방식대로 담당자가 아래 '시공경험 직접 입력'에", "아니오"),
    (16, "시공경험 직접 입력", "실적 제한 공고일 때만 — 비우면 자동 계산", None),
    (17, "제출일", "날짜 — 증빙 유효기간을 이 날짜로 확인", None),
    (18, "입찰공고일", "날짜 — 실적 기간·접근성 기준일(원문: 입찰공고일). 비우면 오늘", None),
    (19, "회사명", "지일 자료에서 자동", None),
    (20, "대표자", "지일 자료에서 자동", None),
    (21, "주소", "지일 자료에서 자동", None),
    (22, "전화", "지일 자료(경쟁입찰참가등록증)에서", None),
    (23, "팩스", "지일 자료(경쟁입찰참가등록증)에서", None),
    (24, "수신(발주기관)", "신청서 '○○ 재무관 귀하'·접수증 자리 — 비우면 수요기관", None),
    (25, "작성자 소속", "", None),
    (26, "작성자 성명", "", None),
    (27, "작성자 전화", "비우면 회사 전화", None),
    (28, "업종평가비율(%)", "공고에 명시된 이 업종의 평가비율(원문: 계약담당자가 입찰공고에 명시) — 단일 업종 공고면 100", None),
    (30, "신용평가등급", "지일 자료 신용평가 확인서에서 자동", None),
    (31, "신용평가 유효기간", "날짜", None),
    (32, "업종 실적 합계", "협회 시공실적증명서의 해당 업종 실적(관급·지급자재 제외) — 직접 적거나 숨긴 '시공실적' 탭에서 고르면 합계", None),
    (33, "실적인정기간 4년 이상", "설립 4년 이상이면 예(지일 2009년 설립)", "예"),
    (34, "특별신인도", "여성기업·장애인기업·인구감소지역 소재 기업 — 지일 본점 정선군은 인구감소지역(행안부 고시 제2021-66호 89곳에 포함) → 예", "예"),
    (35, "접근성(주된 사무소)", "예 / 아니오 — 원문: 입찰공고일 전일 현재 공사현장 관할 시·군과 인접 시·군(행정구역 경계선이 접한 시·군)에 주된 사무소가 있으면 예", None),
    (36, "결격 감점", "입찰공고일 현재 기술자 보유가 등록기준 미달이면 15 — 보통 0", 0),
]
F = {name: f"'{B}'!$B${r}" for r, name, _, _ in FIELDS}
EST = F["추정가격"]
BASE = jg.BASE.replace("$B$9", "$B$7")  # 공사: 입찰공고일(18행) → 입찰일시(7행) → 오늘
WIDE = F["업종 구분"] + '="종합공사"'

if GROUP == "10억이상":
    T = "4"
    WRONG, OTHER = f"AND(ISNUMBER({EST}),OR({EST}<1000000000,{EST}>=3000000000))", "10억 미만(또는 30억 이상)"
else:
    T = f"IF({EST}>=400000000,5,IF({EST}>=200000000,6,7))"
    WRONG, OTHER = f"AND(ISNUMBER({EST}),{EST}>=1000000000)", "10억 이상"
TIER_NAME = (f'CHOOSE({T}-3,"[별표 4] 추정가격 30억 미만 10억 이상","[별표 5] 10억 미만 4억 이상",'
             f'"[별표 6] 4억 미만 2억 이상","[별표 7] 2억 미만")')

# 시공경험(실적 제한 없음): 비율 = 업종 실적 ÷ 추정가격
RATIO = f'IF(N({EST})=0,0,N({F["업종 실적 합계"]})/{EST})'
YEARS4 = F["실적인정기간 4년 이상"] + '="예"'
R = RATIO


def steps(thr, pts, low):
    s = str(low)
    for t, p in reversed(list(zip(thr, pts))):
        s = f"IF({R}>={t},{p},{s})"
    return s


EXP4 = (f'IF(N({F["업종 실적 합계"]})=0,0,IF({WIDE},' + steps((1, .9, .8, .7, .6, .5, .4), (15, 13, 11, 9, 7, 5, 3), 1) + ","
        + steps((.8, .7, .6, .5, .4, .3, .2), (15, 13, 11, 9, 7, 5, 3), 1) + "))")
EXP5 = (f'IF(N({F["업종 실적 합계"]})=0,0,IF({YEARS4},' + steps((.7, .6, .5, .4, .3, .2), (10, 9, 8, 7, 6, 5), 4) + ","
        + steps((.6, .5, .4, .3, .2, .1), (10, 9, 8, 7, 6, 5), 4) + "))")
# 별표 6: 원문에 '실적 없음' 규정이 없어 최저 등급으로 둠(원문 확인 필요)
EXP6 = (f'IF({WIDE},IF({YEARS4},' + steps((.6, .5, .4, .3), (4.8, 3.8, 2.8, 1.8), 0.8) + "," + steps((.5, .4, .3, .2), (4.8, 3.8, 2.8, 1.8), 0.8)
        + "),IF(" + YEARS4 + "," + steps((.6, .5, .4, .3), (5, 4, 3, 2), 1) + "," + steps((.5, .4, .3, .2), (5, 4, 3, 2), 1) + "))")
EXP7 = f"ROUND(({EXP6})*IF({WIDE},4/4.8,4/5),2)"
# 업종별 점수 = 등급점수 × 업종평가비율(공고 값) — 비율이 비면 점수도 비움
S_EXP = (f'IF({F["시공경험 직접 입력"]}<>"",N({F["시공경험 직접 입력"]}),IF({F["업종평가비율(%)"]}="","",'
         f'ROUND(CHOOSE({T}-3,{EXP4},{EXP5},{EXP6},{EXP7})*{F["업종평가비율(%)"]}/100,2)))')

# 경영상태(신용평가): 등급 묶음 1 AAA~BB0 / 2 BB- / 3 B+~B- / 4 CCC+ 이하
GRADES = ["AAA", "AA+", "AA0", "AA-", "A+", "A0", "A-", "BBB+", "BBB0", "BBB-", "BB+", "BB0", "BB-", "B+", "B0", "B-",
          "CCC+", "CCC0", "CCC-", "CC", "C", "D", "AA", "A", "BBB", "BB", "B", "CCC"]
GROUP_OF = {g: (1 if g in ("AAA", "AA+", "AA0", "AA-", "A+", "A0", "A-", "BBB+", "BBB0", "BBB-", "BB+", "BB0", "AA", "A", "BBB", "BB")
                else 2 if g == "BB-" else 3 if g in ("B+", "B0", "B-", "B") else 4) for g in GRADES}
CG = f'IFERROR(VLOOKUP(TRIM({F["신용평가등급"]}),점수표!$A$2:$B${len(GRADES) + 1},2,FALSE),0)'
S_CREDIT = (f'IF({CG}=0,"",CHOOSE({T}-3,CHOOSE({CG},15,14,13,10),CHOOSE({CG},10,9.8,9.6,6),'
            f'CHOOSE({CG},10,9.8,9.6,6)*5/10,CHOOSE({CG},10,9.8,9.6,6)*5/10))')

# 가점: 특별신인도 +1(별표 5·6·7), 접근성(별표 5: 5억 미만·전기·정보통신·소방 제외 +1 / 별표 6: 종합 0.2, 전문·그밖 1 / 별표 7: 1)
SPECIAL = f'IF(AND({T}>=5,{F["특별신인도"]}="예"),1,0)'
NOEL = f'NOT(OR(ISNUMBER(SEARCH("전기",{F["업종"]})),ISNUMBER(SEARCH("정보통신",{F["업종"]})),ISNUMBER(SEARCH("소방",{F["업종"]}))))'
ACCESS = (f'IF({F["접근성(주된 사무소)"]}<>"예",0,CHOOSE({T}-3,0,IF(AND({EST}<500000000,{NOEL}),1,0),IF({WIDE},0.2,1),1))')
CAP = f"CHOOSE({T}-3,30,20,10,10)"
S_ABILITY = f'MIN({CAP},N({S_EXP})+N({S_CREDIT})+{SPECIAL}+{ACCESS})'

# 입찰가격: 배점 − k × |90 − (입찰−A)/(예정−A)×100|, 비율은 소수점 다섯째자리 반올림, 상한 구간·최저 2점
A = f'N({F["A값"]})'
RATE = f'IF(OR({F["입찰금액"]}="",{F["예정가격"]}=""),"",ROUND(({F["입찰금액"]}-{A})/({F["예정가격"]}-{A}),4)*100)'
P_PRICE, K, CUT, CUTP = (f"CHOOSE({T}-3,70,80,90,90)", f"CHOOSE({T}-3,4,20,20,20)", f"CHOOSE({T}-3,91.25,90.25,90.25,90.25)",
                         f"CHOOSE({T}-3,65,75,85,85)")
S_PRICE = f'IF({RATE}="","",IF(AND({RATE}<=100,{RATE}>={CUT}),{CUTP},MAX(2,ROUND({P_PRICE}-{K}*ABS(90-{RATE}),4))))'
S_SUM = f'IF({S_PRICE}="","",ROUND({S_ABILITY}+{S_PRICE}-N({F["결격 감점"]}),4))'

EVIDENCE = [  # (심사분야, 별지 제4호 내용, 발행기관, 회사서류에서 찾을 낱말 — 비면 공고마다 새로 준비)
    ("시공경험", "공종별 동일공사 실적명세서", "신청자", ()),
    ("시공경험", "시설공사 시공실적증명서", "협회·발주처", ()),
    ("경영상태", "신용평가등급확인서", "신용평가기관", ("신용평가",)),
    ("신인도", "특별신인도 증빙(본점 소재 — 사업자등록증)", "관계기관", ("사업자등록증",)),
    ("기술인력", "건설기술자 보유증명서", "관련협회", ()),
    ("기타", "법인등기사항 증명서", "관계기관", ("법인등기",)),
]


def basic(wb):
    ws = wb.active
    ws.title = B
    thin = Side(style="thin", color="FF808080")
    bd = Border(left=thin, right=thin, top=thin, bottom=thin)
    yel, hd = PatternFill("solid", fgColor="FFFFF4C2"), PatternFill("solid", fgColor="FFE8EEF4")
    blue, green = PatternFill("solid", fgColor="FFDDEBF7"), PatternFill("solid", fgColor="FFE2EFDA")
    for col, w in zip("ABC", (22, 46, 70)):
        ws.column_dimensions[col].width = w
    ws["A1"] = "공사 적격심사 기본사항"
    ws["A1"].font = Font(name=FONT, sz=16, b=True)
    ws["A2"] = (" * 칸 색 = 값의 출처. 노란 칸만 담당자가 적음 — 신청서·심사표·평점 세부내역·제출서류 목록표는 자동. "
                "기준: 시설공사 적격심사 세부기준(행안부 예규 제344호), 통과 95점")
    ws["A2"].font = Font(name=FONT, sz=9, color="FFC00000")
    for col, (fill, text) in zip("ABC", ((blue, "파랑 = 공고·개찰 결과 값"), (green, "초록 = 회사 자료에서 자동"), (yel, "노랑 = 담당자가 적음"))):
        ws[f"{col}3"], ws[f"{col}3"].fill, ws[f"{col}3"].font = text, fill, Font(name=FONT, sz=9, b=True)
    for r, name, note, default in FIELDS:
        a, b, c = ws.cell(r, 1, name), ws.cell(r, 2, default), ws.cell(r, 3, note)
        a.font, a.fill, a.border = Font(name=FONT, b=True), hd, bd
        b.font, b.fill, b.border = Font(name=FONT), blue if name in FROM_NOTICE else green if name in FROM_COMPANY else yel, bd
        c.font = Font(name=FONT, sz=9, color="FF666666")
        if name in ("입찰금액", "추정가격", "예정가격", "A값", "업종 실적 합계"):
            b.number_format = "#,##0"
        if name in ("제출일", "신용평가 유효기간"):
            b.number_format = "yyyy-mm-dd"
    ws["B32"] = '=IF(SUMIFS(시공실적!$E:$E,시공실적!$I:$I,"넣음")=0,"",SUMIFS(시공실적!$E:$E,시공실적!$I:$I,"넣음"))'
    for name, choices in (("업종 구분", "종합공사,전문·그밖"), ("실적 제한 공고", "예,아니오"), ("실적인정기간 4년 이상", "예,아니오"),
                          ("특별신인도", "예,아니오"), ("접근성(주된 사무소)", "예,아니오")):
        dv = DataValidation(type="list", formula1=f'"{choices}"')
        ws.add_data_validation(dv)
        dv.add(F[name].split("!")[1].replace("$", ""))
    ws["C12"] = f'=IF({WRONG},"⚠ 추정가격이 {OTHER}입니다 — 「{OTHER}」 파일을 쓰세요","숫자만 — 평가 기준(별표)을 고르는 값")'
    ws["C12"].font = Font(name=FONT, sz=9, color="FF666666")
    ws.cell(44, 1, "평가 기준 (자동)").font = Font(name=FONT, b=True)
    ws.cell(44, 2, f'=IF({EST}="","추정가격을 넣으면 정해짐",{TIER_NAME})').font = Font(name=FONT)
    global S_EXP, S_CREDIT, SPECIAL, ACCESS, S_ABILITY, S_PRICE, S_SUM
    ref = lambda r: f"'{B}'!$B${r}"
    # 중간 점수를 이 칸들에 한 번만 계산하고 서식은 칸을 가리킴 — 수식을 이어 붙이면 한셀 한도(8,192자)를 넘음
    rows = [("시공경험", S_EXP), ("경영상태", S_CREDIT), ("특별신인도", SPECIAL), ("접근성", ACCESS),
            ("수행능력 계", f"MIN({CAP},N({ref(45)})+N({ref(46)})+{ref(47)}+{ref(48)})"), ("입찰가격", S_PRICE),
            ("결격 감점", f'-N({F["결격 감점"]})'), ("종합평점", f'IF({ref(50)}="","",ROUND({ref(49)}+{ref(50)}+{ref(51)},4))'),
            ("통과(95점)", f'IF({ref(52)}="","",IF({ref(52)}>=95,"통과","미달"))')]
    for i, (name, f) in enumerate(rows, 45):
        assert len(f) < 8000, (name, len(f))
        ws.cell(i, 1, name).font = Font(name=FONT)
        ws.cell(i, 2, "=" + f).font = Font(name=FONT, b=name in ("종합평점", "통과(95점)"))
    S_EXP, S_CREDIT, SPECIAL, ACCESS, S_ABILITY, S_PRICE, S_SUM = (ref(r) for r in (45, 46, 47, 48, 49, 50, 52))
    ws.freeze_panes = "A4"
    t = wb.create_sheet("점수표")
    t.cell(1, 1, "신용평가등급"), t.cell(1, 2, "묶음(1 AAA~BB0 · 2 BB- · 3 B · 4 CCC 이하)")
    for i, g in enumerate(GRADES, 2):
        t.cell(i, 1, g), t.cell(i, 2, GROUP_OF[g])


def record_sheet(wb, tracks):
    """'시공실적' 탭(제출 안 함): 지일 공사수주현황 → 기간 안·공사로 확인된 실적만 자동 '넣음'(업무를 모르면 '확인').
    기간: 별표 4 최근 5년, 별표 5~7 최근 3년 이상(3년 + 그 다음 해 1.1.~공고일 → 공고 연도−3년 1월 1일부터)."""
    ws = wb.create_sheet("시공실적")
    for col, w in zip("ABCDEFGHI", (5, 46, 8, 12, 16, 8, 10, 12, 10)):
        ws.column_dimensions[col].width = w
    ws["A1"] = "시공실적(참고: 지일 공사수주현황) — 협회 시공실적증명서의 해당 업종 실적과 맞는 줄만 '넣음'. 기간: 별표 4 최근 5년, 별표 5~7 최근 3년간 + 그 다음 해 1.1.~입찰공고일"
    ws["A1"].font = Font(name=FONT, b=True)
    start = f'IF({T}=4,EDATE({BASE},-60),DATE(YEAR({BASE})-3,1,1))'
    for c, h in enumerate(("번호", "사업명", "업무", "준공일", "계약금액(원)", "기간 안(참고)", "", "넣음/뺌(담당자)", "구분"), 1):
        x = ws.cell(3, c, h)
        x.font, x.fill = Font(name=FONT, b=True), PatternFill("solid", fgColor="FFE8EEF4")
    dv = DataValidation(type="list", formula1='"넣음,뺌"', allow_blank=True)
    ws.add_data_validation(dv)
    for i, t in enumerate(tracks, 4):
        for c, val in enumerate((i - 3, t.get("사업명"), t.get("업무") or "", t.get("준공일"), t.get("계약금액(원)")), 1):
            ws.cell(i, c, val).font = Font(name=FONT, sz=10)
        ws.cell(i, 4).number_format = "yyyy-mm-dd"
        ws.cell(i, 5).number_format = "#,##0"
        ws.cell(i, 6, f'=IF(D{i}="","확인",IF(AND(D{i}<={BASE},D{i}>={start}),"예","아니오"))')
        ws.cell(i, 7, "")  # 자동 구분 안 함 — 업종별 실적은 협회 실적증명 기준(원문)
        dv.add(f"H{i}")
        ws.cell(i, 8).fill = PatternFill("solid", fgColor="FFFFF4C2")
        ws.cell(i, 9, f'=IF(H{i}<>"",H{i},G{i})').font = Font(name=FONT, b=True)


def form1(wb):
    f = Form(wb, "1_적격심사신청서")
    f.logo("[별지 제2호 서식]")
    f.title("적 격 심 사 신 청 서")
    jg.header_rows(f, [(k, "=" + jg.blank(F[v])) for k, v in (("관리번호", "입찰공고번호"), ("공 사 명", "공사명"),
                                                             ("수요기관", "수요기관"), ("입찰일시", "입찰일시"))])
    f.gap(16)
    f.para("  위 공사의 적격심사와 관련하여 적격심사 자기평가표와 제증빙 자료를\n"
           "신의성실의 원칙에 따라 붙임과 같이 제출하며, 만일 제출한 서류가 허위나\n"
           "부정한 방법으로 작성된 사실이 확인될 경우에는 지방자치단체를 당사자로\n"
           "하는 계약에 관한 법률 제31조와 시설공사 적격심사 세부기준 등에 따라\n"
           "처리해도 아무런 이의를 제기하지 않을 것을 확약합니다.", lines=5)
    f.gap(8)
    f.para("붙임 : 제출서류 목록과 관련서류 1부")
    f.gap(18)
    jg.date_line(f)
    f.gap(14)
    jg.sign(f)
    f.gap(14)
    f.ws.row_dimensions[f.r].height = 30
    f.cell(f.r, 1, NC, f'=IF({F["수신(발주기관)"]}="",{jg.blank(F["수요기관"])},{F["수신(발주기관)"]})&" 재무관 귀하"', sz=14, b=True)
    f.r += 1
    f.gap(12)
    for c in range(1, NC + 1):
        f.ws.cell(f.r, c).border = Border(top=Side(style="dashed"))
    f.gap(14)
    f.ws.row_dimensions[f.r].height = 32
    f.cell(f.r, 1, NC, "접    수    증", sz=15, b=True, h="center")
    f.r += 1
    for i, (label, ref) in enumerate((("관 리 번 호", F["입찰공고번호"]), ("공  사  명", F["공사명"]), ("제  출  자", F["회사명"])), 1):
        f.ws.row_dimensions[f.r].height = 24
        f.cell(f.r, 2, 9, f"{i}. {label}")
        f.cell(f.r, 10, 10, ":", h="center")
        f.cell(f.r, 11, NC, "=" + jg.blank(ref))
        f.r += 1
    f.gap(6)
    f.para("위 공사입찰의 적격심사신청서와 관련서류를 접수했음을 확인합니다.", h="center")
    f.para("20      .      .      .", h="center")
    f.para(f'=IF({F["수신(발주기관)"]}="",{jg.blank(F["수요기관"])},{F["수신(발주기관)"]})&" 재무관            (인)      "', h="right")
    f.done()


def form2(wb):
    f = Form(wb, "2_자기평가와심사표")
    f.logo("[별지 제3호 서식]")
    f.title("적격심사 자기평가와 심사표", sz=18)
    f.para("1. 심사대상 공사", b=True)
    f.table([("공고번호", "=" + jg.blank(F["입찰공고번호"])), ("공 사 명", "=" + jg.blank(F["공사명"])),
             ("입찰금액", f'=IF({F["입찰금액"]}="","",TEXT({F["입찰금액"]},"#,##0")&" 원")'),
             ("업종구분", f'=IF({F["업종"]}="",{F["업종 구분"]},{F["업종"]}&" ("&{F["업종 구분"]}&")")')])
    f.gap(8)
    f.para("2. 심사대상자", b=True)
    f.table([("회 사 명", "=" + jg.blank(F["회사명"])), ("대 표 자", "=" + jg.blank(F["대표자"])), ("전화번호", "=" + jg.blank(F["전화"]))])
    f.gap(8)
    f.para("3. 작성자 연락처", b=True)
    f.table([("소    속", "=" + jg.blank(F["작성자 소속"])), ("성    명", "=" + jg.blank(F["작성자 성명"])),
             ("전화번호", f'=IF({F["작성자 전화"]}="",{jg.blank(F["전화"])},{F["작성자 전화"]})')])
    f.gap(8)
    f.para("4. 종합평가표                                                              (단위 : 점)", b=True)
    pts = lambda f_: f'=IF({f_}="","",{f_})'
    f.grid(["심사분야", "심사항목", "배  점", "자기평점", "심사평점"], [5, 7, 4, 4, 4], [
        ["계", "", "100", pts(S_SUM), ""],
        ["수행능력평가", "시공경험", f"=CHOOSE({T}-3,15,10,IF({WIDE},4.8,5),4)", pts(S_EXP), ""],
        ["", "경영상태", f"=CHOOSE({T}-3,15,10,5,5)", pts(S_CREDIT), ""],
        ["", "신인도(특별신인도)", f'=IF({T}>=5,"+1","-")', f"={SPECIAL}", ""],
        ["", "접근성", f'=CHOOSE({T}-3,"-","+1",IF({WIDE},"0.2","+1"),"1")', f"={ACCESS}", ""],
        ["", "수행능력 계(한도)", f"={CAP}", pts(S_ABILITY), ""],
        ["입찰가격", "", f"={P_PRICE}", pts(S_PRICE), ""],
        ["결격여부", "", "△15", f'=-N({F["결격 감점"]})', ""],
    ], h=30)
    f.para("※ 종합평가표는 해당 항목만 기재. 시공여유율·직접시공·자재인력조달 등은 이 구간에 해당 없음", sz=9)
    f.gap(10)
    jg.date_line(f)
    f.gap(8)
    jg.sign(f)
    f.done()


def form3(wb):
    f = Form(wb, "3_평점세부내역")
    f.logo("[자기평점 계산 근거]")
    f.title("항목별 평점 세부내역", sz=18)
    f.para(f'=IF({EST}="","",{TIER_NAME}&" 시설공사 평가기준 (통과 95점)")', h="center", sz=10)
    f.para(f'="종합평점 : "&IF({S_SUM}="","",TEXT({S_SUM},"0.00")&"점  ("&IF({S_SUM}>=95,"통과","미달")&")")', b=True, sz=13)
    f.gap(6)
    f.para(f'="1. 시공경험 : "&TEXT({S_EXP},"0.0#")&"점"', b=True)
    f.grid(["업종 실적(a)", "추정가격(b)", "a ÷ b", "구분", "평점"], [6, 6, 4, 5, 3], [[
        f'=TEXT(N({F["업종 실적 합계"]}),"#,##0")', f'=IF({EST}="","",TEXT({EST},"#,##0"))', f'=TEXT({RATIO},"0.0%")',
        f'=IF({F["시공경험 직접 입력"]}<>"","직접 입력",{F["업종 구분"]}&IF({T}>=5,IF({YEARS4}," · 4년 이상"," · 4년 미만"),""))', "=" + S_EXP]], h=30)
    f.para(f'=IF({T}=4,"※ 최근 5년 업종 실적(관급·지급자재 제외), 실적 없으면 0점","※ 최근 3년간 + 그 다음 해 1.1.~입찰공고일 업종 실적(관급·지급자재 제외) × 업종평가비율(공고)")', sz=9)
    f.gap(6)
    f.para(f'="2. 경영상태 : "&IF({S_CREDIT}="","등급 없음",TEXT({S_CREDIT},"0.0#")&"점")', b=True)
    f.grid(["신용평가등급", "유효기간", "평점"], [9, 9, 6], [[
        "=" + jg.blank(F["신용평가등급"]), f'=IF({F["신용평가 유효기간"]}="","",TEXT({F["신용평가 유효기간"]},"yyyy-mm-dd"))', "=" + S_CREDIT]], h=28)
    f.para("※ 신용평가 방법(BB0 이상 만점). 재무비율·종합 방법을 고르면 담당자가 다시 계산", sz=9)
    f.gap(6)
    f.para(f'="3. 가점 : 특별신인도 "&{SPECIAL}&" · 접근성 "&{ACCESS}&"  (수행능력 계 "&TEXT({S_ABILITY},"0.0#")&" / 한도 "&{CAP}&")"', b=True)
    f.grid(["항목", "근거", "평점"], [5, 15, 4], [
        ["특별신인도", f'=IF({F["특별신인도"]}="예","인구감소지역 소재(정선군) 등","해당 없음")', f"={SPECIAL}"],
        ["접근성", f'=IF({F["공사현장 시·군"]}="","공사현장 시·군을 적으면 판단",{F["공사현장 시·군"]}&" — 본점(정선) "&IF({F["접근성(주된 사무소)"]}="예","같은·인접 시·군","해당 아님"))', f"={ACCESS}"],
    ], h=28)
    f.gap(6)
    f.para(f'="4. 입찰가격 : "&IF({S_PRICE}="","",TEXT({S_PRICE},"0.00")&"점")', b=True)
    f.grid(["입찰가격(a)", "예정가격(b)", "A값", "(a−A)÷(b−A)", "평점"], [5, 5, 4, 6, 4], [[
        f'=IF({F["입찰금액"]}="","",TEXT({F["입찰금액"]},"#,##0"))', f'=IF({F["예정가격"]}="","",TEXT({F["예정가격"]},"#,##0"))',
        f'=TEXT({A},"#,##0")', f'=IF({RATE}="","",TEXT({RATE},"0.00")&"%")', "=" + S_PRICE]], h=30)
    f.para(f'="※ 평점 = "&{P_PRICE}&" − "&{K}&" × |90 − (a−A)÷(b−A)×100|, "&{CUT}&"% 이상(100% 이하)이면 "&{CUTP}&"점, 최저 2점"', sz=9)
    f.done()


def form4(wb):
    f = Form(wb, "4_제출서류목록표")
    f.logo("[별지 제4호 서식]")
    f.title("제 출 서 류 목 록 표", sz=18)
    rows = [["총괄", "1", "적격심사신청서", "신청자", "별지 제2호"], ["총괄", "2", "적격심사 자기평가와\n심사표", "신청자", "별지 제3호"]]
    for i, (area, text, issuer, _) in enumerate(EVIDENCE, 3):
        rows.append([area, str(i), text.replace("(", "\n(", 1) if len(text) > 14 else text, issuer, ""])
    f.grid(["심사분야", "연번", "내      용", "발행기관", "비  고"], [4, 2, 9, 5, 4], rows, h=34)
    f.gap(8)
    f.para("주) 시공실적증명서·기술자 보유증명서 등은 제1장 입찰참가자격 사전심사 기준 서식(협회 발급)", sz=9)
    f.done()


def build(path):
    wb = Workbook()
    wb._fonts = IndexedList([Font(name=FONT, sz=11)])
    wb._named_styles["Normal"].font = Font(name=FONT, sz=11)
    jg.F, jg.EVIDENCE, jg.PLACE_PRINT = F, EVIDENCE, True  # 서명란·날짜·증빙 탭 도구가 이 틀의 칸을 보게(공사 시공실적은 늘 평가)
    basic(wb)
    form1(wb)
    form2(wb)
    form3(wb)
    form4(wb)
    v = jg.company()
    ev = jg.evidence(v, jg.doc_rows())
    record_sheet(wb, v.tracks or [])
    ws = wb[B]
    for name, key in (("회사명", "회사명"), ("대표자", "대표자"), ("주소", "주소"), ("전화", "전화번호"), ("팩스", "팩스번호")):
        ws[F[name].split("!")[1].replace("$", "")] = v.info.get(key)
    if v.info.get("신용평가등급"):
        ws["B30"] = v.info["신용평가등급"]
        if v.info.get("신용평가 유효기간"):
            ws["B31"] = dt.datetime.strptime(v.info["신용평가 유효기간"], "%Y-%m-%d")
    wb.calculation.fullCalcOnLoad = True
    wb.save(path)
    return ev


