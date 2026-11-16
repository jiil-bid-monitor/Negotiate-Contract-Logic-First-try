"""물품 적격심사 서류 표준틀 — 행정안전부 예규 「지방자치단체 입찰시 낙찰자 결정기준」 제2장의3 물품 적격심사 세부기준.
기본사항만 입력 → 별지 제1호 신청서 · 제2호 자기평가와 심사표 · 평점 세부내역 · 별지 제4호 제출서류 목록표가 수식으로 채워짐.
파일은 금액으로 둘(JG_GROUP): 10억 이상 [별표 1] / 10억 미만 [별표 2]·[별표 3](고시금액으로 자동).
회사 값·신용평가·기술인력·증빙은 정량평가서(quantdocs)와 같은 읽기로 회사 자료 폴더(JIIL)에서, 증빙 서류는 회사서류 정리 폴더(DOCS)의 최신본.
여성기업 관련 서류·가점은 넣지 않음(2026-11-16 사용자: 이제 해당 없음).
    python -m jgdocs --work 물품 --group 10억미만 --out 파일.xlsx [--values 값.json]"""
import datetime as dt
import json
import os
import re
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils.indexed_list import IndexedList
from openpyxl.worksheet.datavalidation import DataValidation

from .form import FONT, NC, Form

B = "기본사항"
# 회사 서류 폴더 — 서버 PC .env로 바꿀 수 있음. DOCS = NAS 회사서류 정리 폴더([최신 서류]·서류목록.xlsx), JIIL = 회사 자료(정량평가서와 같은 구성)
DOCS = Path(os.environ.get("JG_DOCS_DIR", r"\\jiilserver\jiil-webapp\회사서류_최신"))
JIIL = Path(os.environ.get("JG_COMPANY_DIR", r"\\jiilserver\002 진행프로젝트\실습폴더\지일 자료"))
DATE_FMT = 'yyyy". "m". "d"."'
# (행, 이름, 설명, 기본값) — 수식이 이 행 번호를 가리킴
# 칸 출처 — 공고: 나라장터 공고·개찰 결과에서(지금은 손으로, 공고번호 불러오기 붙이면 자동), 회사: 지일 자료 폴더에서 자동, 나머지: 담당자
FROM_NOTICE = {"입찰공고일", "입찰등록번호", "구매관리번호", "입찰 대상 물품명", "정부물품 분류번호", "수요기관", "입찰일시", "수량", "입찰금액", "추정가격", "예정가격"}
FROM_COMPANY = {"회사명", "대표자", "주소", "전화", "팩스", "신용평가등급", "신용평가 유효기간", "기술사·기능장 수", "기사·산업기사 수",
                "기능사 수", "일반경력자(3년↑) 수", "중소기업 구분", "소기업·소상공인"}
FIELDS = [
    (4, "입찰등록번호", "나라장터 공고번호-차수", None),
    (5, "구매관리번호", "", None),
    (6, "입찰 대상 물품명", "", None),
    (7, "정부물품 분류번호", "세부품명번호 10자리", None),
    (8, "수요기관", "", None),
    (9, "입찰일시", "예: 2026-11-20 11:00", None),
    (10, "수신", "신청서 '○○ 귀하'·접수증 '○○ 재무관' 자리 — 비우면 수요기관", None),
    (11, "수량", "", None),
    (12, "입찰금액", "숫자만", None),
    (13, "추정가격", "숫자만 — 평가 기준(별표)을 고르는 값", None),
    (14, "예정가격", "숫자만 — 개찰 결과에서", None),
    (15, "고시금액", "기획재정부 국제입찰 대상금액(물품·용역) — 2025~2026년 2억 3천만 원, 2년마다 새로 고시", 230000000),
    (16, "업종구분", "제조 / 공급 / 기타 — 제조가 아니면 기술능력 만점", "제조"),
    (17, "제출일", "날짜 — 증빙 유효기간을 이 날짜로 확인", None),
    (18, "입찰공고일", "날짜 — 기술인력·실적 기간의 기준일(원문: 입찰공고일 기준). 비우면 오늘", None),
    (19, "회사명", "지일 자료에서 자동", None),
    (20, "대표자", "지일 자료에서 자동", None),
    (21, "주소", "지일 자료에서 자동", None),
    (22, "전화", "지일 자료(경쟁입찰참가등록증)에서 — 바뀌었으면 고쳐 적음", None),
    (23, "팩스", "지일 자료(경쟁입찰참가등록증)에서 — 바뀌었으면 고쳐 적음", None),
    (25, "연락책임자 소속", "", None),
    (26, "연락책임자 직위", "", None),
    (27, "연락책임자 성명", "", None),
    (28, "연락책임자 전화", "비우면 회사 전화", None),
    (30, "신용평가등급", "지일 자료 신용평가 확인서에서 자동(정량평가서와 같은 읽기)", None),
    (31, "신용평가 유효기간", "날짜", None),
    (32, "동등이상물품 실적", "[원] 나라장터 이행실적증명서 금액 중 공고가 '동등이상 물품'으로 정한 물품(예: 이번 입찰 물품과 같은 물품)의 납품 금액 합(최근 5년·부가세 제외). "
     "10억 미만은 공고에 실적 평가가 있을 때만, 없으면 비움", None),
    (33, "유사물품 실적", "[원] 같은 증명서에서 공고가 '유사물품'으로 정한 물품(같은 종류지만 성능·품질이 낮은 물품)의 납품 금액 합. 공고에 유사물품 기준이 없으면 비움", None),
    (34, "소기업·소상공인", "중소기업 확인서의 [소기업]·[소상공인]에서 자동 — 예면 실적 10% 가산", "예"),
    (35, "기술사·기능장 수", "제조만 — 1인당 2.0 · '기술인력' 탭의 자격 칸으로 자동", '=COUNTIF(기술인력!$E:$E,"기술사·기능장")'),
    (36, "기사·산업기사 수", "1인당 1.5 · '기술인력' 탭에서 자동", '=COUNTIF(기술인력!$E:$E,"기사·산업기사")'),
    (37, "기능사 수", "1인당 1.0 · '기술인력' 탭에서 자동", '=COUNTIF(기술인력!$E:$E,"기능사")'),
    (38, "일반경력자(3년↑) 수", "1인당 0.5 · 자격 없이 입찰일 기준 3년 이상 재직(경력증명서·4대보험 6개월↑ 제출)", '=COUNTIF(기술인력!$G:$G,"일반경력자")'),
    (39, "시설·장비", "A 보유(공고 기준 충족) / B 보유(미달) / C 미보유 — 원문 주6): 공고에 기준이 없으면 보유자 A. 지일 사업자등록증 제조업·직접생산 공장 보유", "A"),
    (40, "중소기업 구분", "증빙확인 탭의 이노비즈·중소기업 확인서 유효기간(제출일 기준) + 업종구분으로 자동 — 제조는 계약 물품을 직접 제조할 때만",
     '=IF(AND(ISNUMBER(증빙확인!$E$10),증빙확인!$E$10>=IF($B$17="",TODAY(),$B$17)),IF($B$16="제조","혁신형+제조","혁신형"),'
     'IF(AND(ISNUMBER(증빙확인!$E$9),증빙확인!$E$9>=IF($B$17="",TODAY(),$B$17)),IF($B$16="제조","중소+제조","중소"),"없음"))'),
    (41, "신인도 그 밖 가점", "인증·특허(계약 물품에 직접 쓴 것만 — 특허 1.0, 디자인등록 0.5) 등 담당자가 확인한 점수 합", 0),
    (42, "신인도 감점", "이행지연·품질하자 등(양수로 적음)", 0),
]
F = {name: f"'{B}'!$B${r}" for r, name, _, _ in FIELDS}
# 경영상태 — 별표 1·2는 30점(B열), 별표 3은 20점(C열). 회사채·기업어음·기업신용평가 등급을 같은 칸으로
GRADES = [("AAA", 30, 20), ("AA+", 30, 20), ("AA0", 30, 20), ("AA-", 30, 20), ("A1", 30, 20), ("A+", 30, 20), ("A2+", 30, 20),
          ("A0", 30, 20), ("A2", 30, 20), ("A2-", 30, 20), ("A-", 30, 20), ("BBB+", 29.7, 20), ("A3+", 29.7, 20), ("BBB0", 29.5, 20),
          ("BBB", 29.5, 20), ("A3", 29.5, 20), ("BBB-", 29.3, 20), ("A3-", 29.3, 20), ("BB+", 29.0, 19.7), ("BB0", 29.0, 19.7),
          ("BB", 29.0, 19.7), ("B+", 29.0, 19.7), ("BB-", 28.5, 19.3), ("B0", 28.5, 19.3), ("B", 28.5, 19.3), ("B-", 28.0, 19.0),
          ("CCC+", 25, 15), ("CCC", 25, 15), ("CCC-", 25, 15), ("CC", 25, 15), ("C", 25, 15), ("D", 25, 15), ("없음", 20, 10)]
SME = [("혁신형+제조", 2.0), ("혁신형", 1.5), ("중소+제조", 1.5), ("중소", 1.0), ("없음", 0)]
# 구간: 1 = [별표 1] 10억 이상, 2 = [별표 2], 3 = [별표 3]
EST, GOSI = F["추정가격"], F["고시금액"]
# 파일을 금액으로 둘로 나눔(11/16 사용자 결정) — 10억 이상 = [별표 1], 10억 미만 = [별표 2]·[별표 3](고시금액으로 자동)
GROUP = os.environ.get("JG_GROUP", "10억미만")
PLACE_PRINT = GROUP == "10억이상"  # 실적증명서 붙일 탭을 PDF에 넣을지(10억 이상은 이행실적 필수)
T = "1" if GROUP == "10억이상" else f'IF({EST}>=N({GOSI}),2,3)'
WRONG = (f'AND(ISNUMBER({EST}),{EST}<1000000000)' if GROUP == "10억이상" else f'AND(ISNUMBER({EST}),{EST}>=1000000000)')
OTHER = "10억 미만" if GROUP == "10억이상" else "10억 이상"
TIER_NAME = f'CHOOSE({T},"추정가격 10억원 이상","추정가격 10억원 미만 고시금액 이상","추정가격 고시금액 미만")'
TIER_TAB = f'CHOOSE({T},"[별표 1]","[별표 2]","[별표 3]")'
JE = f'{F["업종구분"]}="제조"'
S_STAFF = f'IF({JE},MIN(5,2*{F["기술사·기능장 수"]}+1.5*{F["기사·산업기사 수"]}+{F["기능사 수"]}+0.5*{F["일반경력자(3년↑) 수"]}),5)'
S_FAC = f'IF({JE},IF({F["시설·장비"]}="A",5,IF({F["시설·장비"]}="B",2,1)),5)'
S_TECH = f"({S_STAFF}+{S_FAC})"
P_CREDIT = f"CHOOSE({T},30,30,20)"
S_CREDIT = (f'IFERROR(VLOOKUP(UPPER(SUBSTITUTE(SUBSTITUTE({F["신용평가등급"]}," ",""),"°","0")),점수표!$A$2:$C${len(GRADES) + 1},'
            f'IF({T}=3,3,2),FALSE),{P_CREDIT}-10)')
# 이행실적([별표 1]만): 실적 ÷ 추정가격(소기업이면 ×1.1) → 동등 30·26·22·18·12, 유사 10·8·6·4·2, 합 30 한도. 실적 칸이 비면 0
UP = f'IF({F["소기업·소상공인"]}="예",1.1,1)'
R_EQ = f'IF(N({EST})=0,0,N({F["동등이상물품 실적"]})*{UP}/{EST}*100)'
R_SIM = f'IF(N({EST})=0,0,N({F["유사물품 실적"]})*{UP}/{EST}*100)'
S_EQ = f'IF({F["동등이상물품 실적"]}="",0,IF({R_EQ}>=100,30,IF({R_EQ}>=70,26,IF({R_EQ}>=40,22,IF({R_EQ}>=10,18,12)))))'
S_SIM = f'IF({F["유사물품 실적"]}="",0,IF({R_SIM}>=100,10,IF({R_SIM}>=80,8,IF({R_SIM}>=60,6,IF({R_SIM}>=40,4,2)))))'
S_RECORD = f'IF({T}=1,MIN(30,{S_EQ}+{S_SIM}),"")'
# 입찰가격: 배점 − k × |88 − a/b×100|, 예정가격 이하 · 기준% 이상이면 고정점, 최저 2점
RATIO = f'ROUND({F["입찰금액"]}/{F["예정가격"]},4)*100'  # 나눈 값 소수 다섯째 자리에서 반올림
P_PRICE, K, TH, FIX = (f"CHOOSE({T},{a},{b},{c})" for a, b, c in ((30, 60, 70), (2, 2, 4), (95.5, 95.5, 91.75), (15, 45, 55)))
S_PRICE = (f'IF(OR({F["입찰금액"]}="",{F["예정가격"]}=""),"",IF(AND({RATIO}<=100,{RATIO}>={TH}),{FIX},'
           f'MAX(2,ROUND({P_PRICE}-{K}*ABS(88-{RATIO}),2))))')
S_SME = f'IFERROR(VLOOKUP({F["중소기업 구분"]},점수표!$E$2:$F${len(SME) + 1},2,FALSE),0)'
S_TRUST = f'MAX(-2,MIN(2,{S_SME}+{F["신인도 그 밖 가점"]}-{F["신인도 감점"]}))'
S_SUM = f'IF({S_PRICE}="","",N({S_RECORD})+{S_TECH}+{S_CREDIT}+{S_PRICE}+{S_TRUST})'

# 증빙: (심사분야, 별지 제4호 내용, 발행기관, 서류목록.xlsx '종류'에서 찾을 낱말들). 여성기업은 뺌
EVIDENCE = [
    ("이행실적", "물품 이행실적증명서(나라장터 발급)", "발주기관 등", ("실적증명",)),
    ("기술능력", "기술·기능분야 자격증", "관계기관 등", ("자격증",)),
    ("기술능력", "4대보험 가입증명서", "국민연금공단 등", ("4대보험 가입",)),
    ("기술능력", "공장등록증명원", "산업단지공단 등", ("공장등록",)),
    ("경영상태", "신용평가등급확인서", "신용평가기관", ("신용평가",)),
    ("신인도", "중소기업·소상공인 확인서", "중소벤처기업부", ("중소기업·소상공인", "중소기업확인")),
    ("신인도", "기술혁신형 중소기업(이노비즈) 확인서", "중소벤처기업부", ("이노비즈확인", "이노비즈")),
    ("신인도", "환경 및 기타 인증(특허 등)", "관계기관 등", ("특허증/",)),
]


BASE = 'IF(기본사항!$B$18<>"",기본사항!$B$18,IF(기본사항!$B$9<>"",DATEVALUE(LEFT(기본사항!$B$9,10)),TODAY()))'  # 입찰공고일(원문 기준일) → 입찰일시 → 오늘


def jg_blank(name):
    return blank(F[name])


def blank(ref):
    return f'IF({ref}="","",{ref})'


def doc_rows(folder=DOCS):
    """회사서류_최신/서류목록.xlsx + _목록.json → [{상태, 종류, 사유, 발급일, 유효기간(기준), 파일}] (회사서류 정리 기능이 쓰는 표). 없으면 빈 목록."""
    p = Path(folder) / "서류목록.xlsx"
    if not p.is_file():
        return []
    rows = list(load_workbook(p, read_only=True, data_only=True).worksheets[0].iter_rows(values_only=True))
    head = [str(h or "").strip() for h in rows[0]]
    try:
        files = {r["종류"]: r["파일"] for r in json.loads((Path(folder) / "_목록.json").read_text(encoding="utf-8"))}
    except (OSError, ValueError, KeyError):
        files = {}
    out = [dict(zip(head, r)) for r in rows[1:] if any(r)]
    for r in out:
        f = files.get(r.get("종류"))
        r["파일"] = Path(folder) / "[최신 서류]" / f if f else None
    return out


def find_doc(rows, words):
    hit = [r for r in rows if any(w in str(r.get("종류") or "") for w in words)]
    return max(hit, key=lambda r: str(r.get("발급일") or ""), default=None)


def company():
    """지일 자료 폴더 → quantdocs Vault(정량평가서와 같은 읽기: 회사정보·신용평가·기술인력·특허, 스캔본은 OCR)."""
    from quantdocs import importer, vault

    v = vault.Vault({})
    vault.add_loose(v, JIIL)
    importer.enrich_vault(v)
    return v


def evidence(v, rows_doc):
    """증빙마다 {보유, 출처, 만료, 메모, 파일들} — 회사서류_최신(지일 자료에서 정리해 넣은 최신본, 11/16) 먼저,
    거기 없는 종류(특허증 등)는 지일 자료 폴더. 이행실적은 공고마다 그 물품 실적으로 새로 발급이라 파일을 넣지 않음."""
    from quantdocs import importer

    out = []
    for area, _, _, words in EVIDENCE:
        if area == "이행실적":
            out.append({"보유": None, "출처": "", "만료": None, "메모": "공고마다 — 이 공고 물품의 실적으로 나라장터에서 발급", "파일들": []})
            continue
        d = find_doc(rows_doc, words)
        if d:
            exp = str(d.get("유효기간(기준)") or "")
            out.append({"보유": d.get("종류"), "출처": "회사서류_최신", "발급": d.get("발급일"),
                        "만료": dt.date.fromisoformat(exp) if exp[:4].isdigit() else None, "3개월": "3개월" in exp,
                        "메모": d.get("사유"), "파일들": [d["파일"]] if d.get("파일") else []})
            continue
        hit = sorted(p for p in v.loose if any(w in p for w in words))
        if hit and "특허증/" in words:
            out.append({"보유": f"특허증 {len(hit)}건", "출처": "지일 자료/특허증", "만료": None,
                        "메모": "계약목적물에 직접 적용된 특허만 가점 — 해당 건만 제출", "파일들": [JIIL / p for p in hit]})
        elif hit:
            p = hit[-1]
            end, memo = importer.validity_from_name(p)
            if end is None and p.lower().endswith(".pdf"):
                data = v.files[f"files/{p}"]
                txt = importer._bytes_pdf_text(data) or importer._ocr_pdf(data) or ""
                end = importer.validity_from_text(txt)[1]
            out.append({"보유": p.rsplit("/", 1)[-1], "출처": "지일 자료", "만료": end, "메모": memo or ("" if end else "유효기간 확인"),
                        "파일들": [JIIL / p]})
        else:
            out.append({"보유": None, "출처": "", "만료": None, "메모": "", "파일들": []})
    return out


def add_doc_tabs(xlsx, ev):
    """증빙 PDF를 서류마다 탭 하나로(탭 이름 = 별지 제4호 연번_서류 이름, 쪽마다 그림 한 장·쪽 나눔). 탭 이름이 숫자로 시작해
    통합 PDF에 서식 뒤로 함께 나감(한글판은 서식만). 특허증은 해당 건만 내야 해서 '참고_' 탭(PDF에 안 나감). 넣은 탭 수."""
    import io
    import re as _re

    import pypdfium2 as pdfium
    from openpyxl.drawing.image import Image as XImg
    from openpyxl.worksheet.pagebreak import Break

    wb = load_workbook(xlsx)
    n = 0
    for i, ((area, text, _, _), e) in enumerate(zip(EVIDENCE, ev), 3):
        files = e.get("파일들") or []
        title = "특허증" if "특허" in text else " ".join(_re.sub(r"\(.*?\)|[][:*?/]", "", text).split())
        if not files:
            if "실적증명" in text:  # 공고마다 발급하는 실적증명서 — 붙여 넣을 탭(11/16 사용자)
                name = (f"증빙{i}_" if PLACE_PRINT else "참고_") + title[:28]
                ph = wb.create_sheet(name)
                ph.column_dimensions["A"].width = 100  # 그림 붙일 자리(A4 폭) — 인쇄는 A열만
                ph.column_dimensions["B"].width = 3
                ph.column_dimensions["C"].width = 70  # 옆 설명 상자(인쇄 안 됨)
                ph.print_area = "A1:A120"
                ph.page_setup.paperSize, ph.page_setup.fitToWidth, ph.page_setup.fitToHeight = 9, 1, 0
                ph.sheet_properties.pageSetUpPr.fitToPage = True
                pdf_step = "PDF 보기 프로그램(엣지·한PDF 등)에서 증명서를 열고, 쪽마다 'PNG로 저장(이미지로 내보내기)' 또는 캡처(Win+Shift+S)"
                guide = [
                    (f"{text} — 왼쪽 A1 칸 자리에 그림으로 붙여 넣기 (이 상자는 인쇄 안 됨)", True),
                    ("① 증명서를 그림으로 바꾸기", True),
                    ("  · PDF로 받았으면: " + pdf_step, False),
                    ("  · 한글(HWP)로 받았으면: 한글에서 열고 [파일]→[다른 이름으로 저장]→파일 형식 'PNG 그림' (쪽마다 그림 파일이 생김), "
                     "또는 [파일]→[PDF로 저장] 뒤 위 PDF 방법", False),
                    ("② 엑셀에 넣기", True),
                    ("  · 이 탭의 A1 칸을 누르고 [삽입]→[그림]→[이 디바이스](캡처했으면 Ctrl+V)", False),
                    ("  · 그림 오른쪽 아래 모서리를 끌어 왼쪽 빨간 글 칸(A열) 너비에 맞춤(가로세로 비율 유지)", False),
                    ("  · 여러 쪽이면 다음 그림은 앞 그림 바로 아래에 붙이고, [페이지 레이아웃]→[나누기]로 쪽을 나눔", False),
                    ("③ 금액은 기본사항에", True),
                    ("  · 증명서의 '이행실적 금액'(부가세 제외)을 기본사항 32행(동등이상)·33행(유사)에 적으면 평점이 자동 계산됨"
                     if "물품" in text else "  · 증명서의 해당 업종 실적(관급·지급자재 제외)을 기본사항 32행 '업종 실적 합계'에 적으면 평점이 자동 계산됨", False),
                    ("④ 확인", True),
                    ("  · 파일 → 인쇄 미리보기로 서식 뒤에 증명서가 나오는지 확인(이 설명 상자는 인쇄되지 않음)", False),
                ]
                if not PLACE_PRINT:
                    guide.append(("  · 이 파일(10억 미만)은 보통 실적 평가가 없어 탭 이름이 '참고_'(인쇄 안 됨) — 공고에 실적 평가가 있으면 탭 이름 앞 '참고_'를 '증빙3_'으로 바꿈", False))
                box = PatternFill("solid", fgColor="FFFFF8E1")
                edge = Side(style="thin", color="FFB0A070")
                for k, (line, head) in enumerate(guide, 1):
                    x = ph.cell(k, 3, line)
                    x.font = Font(name=FONT, b=head, sz=11 if k == 1 else 10, color="FF333333")
                    x.fill = box
                    x.alignment = Alignment(wrap_text=True, vertical="center")
                    x.border = Border(left=edge, right=edge, top=edge if k == 1 else Side(), bottom=edge if k == len(guide) else Side())
                    ph.row_dimensions[k].height = 34 if (k == 1 or not head) else 22
                n += 1
            continue
        ws = wb.create_sheet((f"증빙{i}_" if "특허" not in text else "참고_") + title[:26])
        ws.column_dimensions["A"].width = 2
        ws.page_setup.paperSize, ws.page_setup.orientation = 9, "portrait"
        ws.page_margins.left = ws.page_margins.right = ws.page_margins.top = ws.page_margins.bottom = 0.4
        ws.print_options.horizontalCentered = True
        row = 1
        for f in files:
            pdf = pdfium.PdfDocument(Path(f).read_bytes())
            for k in range(len(pdf)):
                img = pdf[k].render(scale=150 / 72).to_pil().convert("RGB")
                buf = io.BytesIO()
                img.save(buf, "PNG", optimize=True)
                x = XImg(buf)
                x.width, x.height = 700, int(700 * img.height / img.width)  # A4 세로 폭(여백 뺀)에 맞춤
                ws.add_image(x, f"A{row}")
                rows = int(x.height / 20) + 1
                for r in range(row, row + rows):
                    ws.row_dimensions[r].height = 15  # 15pt = 20px
                row += rows
                ws.row_breaks.append(Break(id=row - 1))
        ws.print_area = f"A1:L{row - 1}"
        ws.page_setup.fitToWidth, ws.page_setup.fitToHeight = 1, 0
        ws.sheet_properties.pageSetUpPr.fitToPage = True
        n += 1
    hide_helpers(wb)
    wb.save(xlsx)
    return n


def printed(title):
    """PDF·한글판에 나가는 탭: 서식(숫자로 시작)과 증빙('증빙'으로 시작)."""
    return re.match(r"(증빙)?\d", title) is not None  # '증빙확인' 탭은 아님


def hide_helpers(wb):
    """제출하지 않는 계산용 탭은 숨김(11/16 사용자 — 파일엔 제출할 것과 입력 칸만 보이게). 지우면 수식이 끊겨 숨기기만."""
    for ws in wb.worksheets:
        if ws.title != B and not printed(ws.title) and not ws.title.startswith("참고_") or ws.title == "참고_특허증":
            ws.sheet_state = "hidden"


def staff_sheet(wb, staff):
    """'기술인력' 탭(제출 안 함) — 지일 자료 기술인력표의 재직자. 자격 칸(국가기술자격)은 자격증 보고 고르고, 자격이 없으면
    입찰일 기준 3년 이상 재직자를 일반경력자로 셈(다른 회사 같은 분야 경력은 합산 가능 — 늘리려면 '반영' 칸을 직접)."""
    from openpyxl.worksheet.datavalidation import DataValidation as DV

    ws = wb.create_sheet("기술인력")
    for col, w in zip("ABCDEFG", (6, 12, 10, 12, 16, 10, 14)):
        ws.column_dimensions[col].width = w
    ws["A1"] = "기술인력 — 기본사항 35~38행이 이 탭으로 셈. 자격 칸은 국가기술자격(기술·기능분야)만, 기술자 등급(특급·고급)은 자격이 아님"
    ws["A1"].font = Font(name=FONT, b=True)
    for c, h in enumerate(("번호", "이름", "기술자 등급", "입사일", "국가기술자격", "재직 연수", "반영"), 1):
        x = ws.cell(3, c, h)
        x.font, x.fill = Font(name=FONT, b=True), PatternFill("solid", fgColor="FFE8EEF4")
    dv = DV(type="list", formula1='"기술사·기능장,기사·산업기사,기능사,없음"', allow_blank=True)
    ws.add_data_validation(dv)
    rows = [s for s in staff if str(s.get("재직") or "재직") == "재직"]
    for i, s in enumerate(rows, 4):
        join = s.get("입사일")
        lic = str(s.get("자격증") or "")
        kind = next((k for k, ws_ in (("기술사·기능장", ("기술사", "기능장")), ("기사·산업기사", ("기사",)), ("기능사", ("기능사",)))
                     if any(w in lic for w in ws_)), None) if lic else None  # 자격은 추측하지 않음 — 기술인력표 자격증 칸에 적힌 것만
        for c, val in enumerate((i - 3, s.get("이름"), s.get("등급"), join, kind), 1):
            ws.cell(i, c, val).font = Font(name=FONT)
        ws.cell(i, 4).number_format = "yyyy-mm-dd"
        dv.add(f"E{i}")
        ws.cell(i, 6, f'=IF(D{i}="","",ROUND(({BASE}-D{i})/365.25,1))').font = Font(name=FONT)
        ws.cell(i, 7, f'=IF(AND(E{i}<>"",E{i}<>"없음"),E{i},IF(AND(D{i}<>"",F{i}>=3),"일반경력자",""))').font = Font(name=FONT, b=True)


def small_biz(ev):
    """중소기업 확인서 글에 [소기업]·[소상공인]이 있으면 True(못 읽으면 기본 True — 담당자 확인)."""
    import pypdf

    e = ev[5]  # EVIDENCE 순서: 중소기업·소상공인 확인서
    for f in e.get("파일들") or []:
        try:
            t = "".join(pg.extract_text() or "" for pg in pypdf.PdfReader(f).pages)
        except Exception:  # noqa: BLE001
            continue
        if t.strip():
            return bool(re.search(r"소\s*기\s*업|소\s*상\s*공\s*인", t))
    return True


def record_sheet(wb, tracks):
    """'이행실적' 탭(제출 안 함) — 지일 자료 공사수주현황의 실적. 기본사항 32·33행이 이 탭을 합계.
    자동 구분: 사업명에 입찰 물품명이 있으면 '동등이상', 분야가 위 '입찰 물품 분야'와 같으면 '유사', 아니면 '제외'.
    담당자가 '고친 구분'을 고르면 그것이 우선. 최근 5년 = 입찰일시(없으면 오늘) 기준 준공일. 금액은 수주현황 계약금액 그대로(부가세 포함 여부 확인)."""
    from openpyxl.worksheet.datavalidation import DataValidation as DV
    from quantdocs import notice

    ws = wb.create_sheet("이행실적")
    for col, w in zip("ABCDEFGHIJ", (5, 46, 8, 12, 12, 16, 8, 12, 12, 12)):
        ws.column_dimensions[col].width = w
    ws["A1"] = "이행실적 — 자동으로 고른 구분을 담당자가 확인('고친 구분'에서 바꿈). 제출은 나라장터 실적증명서 금액으로"
    ws["A1"].font = Font(name=FONT, b=True)
    ws["A2"] = "입찰 물품 분야"
    fields = sorted({notice.track_field(t.get("사업명") or "") for t in tracks} - {""})
    dv = DV(type="list", formula1='"' + ",".join(fields) + '"', allow_blank=True)
    ws.add_data_validation(dv)
    dv.add("C2")
    ws["C2"].fill = PatternFill("solid", fgColor="FFFFF4C2")
    ws["E2"] = ("원문 주5): 동등이상물품 = 성능·품질 등이 입찰대상물품 이상인 물품, 유사물품 = 동일한 종류로 성능·품질이 동등 미만인 물품 — "
                "범위는 공고(물품명·물품목록번호)가 정함. 공고 범위에 맞는 실적만 '구분' 칸에서 고름")
    for c, h in enumerate(("번호", "사업명", "업무", "분야(참고)", "준공일", "계약금액(원)", "5년 안", "", "구분(담당자)", "구분"), 1):
        x = ws.cell(3, c, h)
        x.font, x.fill = Font(name=FONT, b=True), PatternFill("solid", fgColor="FFE8EEF4")
    dv2 = DV(type="list", formula1='"동등이상,유사,제외"', allow_blank=True)
    ws.add_data_validation(dv2)
    for i, t in enumerate(tracks, 4):
        end = t.get("준공일")
        work = t.get("업무") or ""  # 수주현황에 업무가 없으면 빈칸 — 물품 적격심사는 물품 납품 실적만(공사·용역 계약은 자동 제외)
        for c, val in enumerate((i - 3, t.get("사업명"), work, notice.track_field(t.get("사업명") or ""), end, t.get("계약금액(원)")), 1):
            ws.cell(i, c, val).font = Font(name=FONT, sz=10)
        ws.cell(i, 5).number_format = "yyyy-mm-dd"
        ws.cell(i, 6).number_format = "#,##0"
        ws.cell(i, 7, f'=IF(E{i}="","확인",IF(AND(E{i}<={BASE},E{i}>=EDATE({BASE},-60)),"예","아니오"))')
        ws.cell(i, 8, "")  # 자동 구분 안 함 — 동등이상·유사 범위는 공고가 정함(원문 주5)
        dv2.add(f"I{i}")
        ws.cell(i, 9).fill = PatternFill("solid", fgColor="FFFFF4C2")
        ws.cell(i, 10, f'=IF(I{i}<>"",I{i},H{i})').font = Font(name=FONT, b=True)


def basic(wb, scored):
    ws = wb.active
    ws.title = B
    thin = Side(style="thin", color="FF808080")
    bd = Border(left=thin, right=thin, top=thin, bottom=thin)
    yel, hd = PatternFill("solid", fgColor="FFFFF4C2"), PatternFill("solid", fgColor="FFE8EEF4")
    blue, green = PatternFill("solid", fgColor="FFDDEBF7"), PatternFill("solid", fgColor="FFE2EFDA")
    for col, w in zip("ABC", (22, 46, 62)):
        ws.column_dimensions[col].width = w
    ws["A1"] = "적격심사 기본사항"
    ws["A1"].font = Font(name=FONT, sz=16, b=True)
    ws["A2"] = (" * 칸 색 = 값의 출처(아래). 노란 칸은 담당자가 적음 — 신청서·심사표·평점 세부내역·제출서류 목록표는 자동. 평가 기준은 추정가격으로 자동 선택(물품 적격심사 세부기준 2025.7.1)"
                if scored else " * 노란 칸만 입력 — 이 업무의 적격심사 세부기준을 받기 전이라 자기평점 칸은 비어 있음(서식·서명·증빙 목록은 자동)")
    ws["A2"].font = Font(name=FONT, sz=9, color="FFC00000")
    for col, (fill, text) in zip("ABC", ((blue, "파랑 = 공고·개찰 결과 값"), (green, "초록 = 회사 자료에서 자동"), (yel, "노랑 = 담당자가 적음"))):
        ws[f"{col}3"], ws[f"{col}3"].fill, ws[f"{col}3"].font = text, fill, Font(name=FONT, sz=9, b=True)
    for r, name, note, default in FIELDS:
        a, b, c = ws.cell(r, 1, name), ws.cell(r, 2, default), ws.cell(r, 3, note)
        a.font, a.fill, a.border = Font(name=FONT, b=True), hd, bd
        b.font, b.fill, b.border = Font(name=FONT), blue if name in FROM_NOTICE else green if name in FROM_COMPANY else yel, bd
        c.font = Font(name=FONT, sz=9, color="FF666666")
        if name in ("입찰금액", "추정가격", "예정가격", "고시금액", "동등이상물품 실적", "유사물품 실적"):
            b.number_format = "#,##0"
        if name in ("제출일", "신용평가 유효기간", "입찰공고일"):
            b.number_format = "yyyy-mm-dd"
    for name, choices in (("업종구분", "제조,공급,기타"), ("시설·장비", "A,B,C"), ("소기업·소상공인", "예,아니오"),
                          ("중소기업 구분", ",".join(n for n, _ in SME))):
        dv = DataValidation(type="list", formula1=f'"{choices}"')
        ws.add_data_validation(dv)
        dv.add(F[name].split("!")[1].replace("$", ""))
    ws.cell(44, 1, "평가 기준 (자동)").font = Font(name=FONT, b=True)
    ws.cell(44, 2, f'=IF({EST}="","추정가격을 넣으면 정해짐",{TIER_TAB}&" "&{TIER_NAME})' if scored else "세부기준 받기 전").font = Font(name=FONT)
    if scored:
        for i, (name, f) in enumerate((("이행실적", S_RECORD), ("기술능력", S_TECH), ("경영상태", S_CREDIT), ("입찰가격", S_PRICE),
                                       ("신인도", S_TRUST), ("계", S_SUM)), 45):
            ws.cell(i, 1, name).font = Font(name=FONT)
            ws.cell(i, 2, "=" + f).font = Font(name=FONT, b=name == "계")
    ws.freeze_panes = "A4"
    t = wb.create_sheet("점수표")  # 숫자로 시작하지 않는 탭은 PDF·한글로 안 나감
    for c, h in enumerate(("신용평가등급", "평점(30점)", "평점(20점)", "", "중소기업 구분", "가점"), 1):
        t.cell(1, c, h)
    for i, (g, a, b) in enumerate(GRADES, 2):
        t.cell(i, 1, g), t.cell(i, 2, a), t.cell(i, 3, b)
    for i, (g, s) in enumerate(SME, 2):
        t.cell(i, 5, g), t.cell(i, 6, s)


def header_rows(f, rows):
    """'◦ 이름 : 값' 줄들."""
    for label, value in rows:
        f.ws.row_dimensions[f.r].height = 26
        f.cell(f.r, 2, 8, "◦ " + label, h="distributed")
        f.cell(f.r, 9, 9, ":", h="center")
        f.cell(f.r, 10, NC, value)
        f.r += 1


def sign(f):
    for i, (label, ref) in enumerate((("주    소", F["주소"]), ("회 사 명", F["회사명"]), ("대 표 자", F["대표자"]))):
        f.ws.row_dimensions[f.r].height = 26
        if i == 0:
            f.cell(f.r, 2, 5, "입찰자", h="left")
        f.cell(f.r, 6, 9, label, h="distributed")
        f.cell(f.r, 10, 10, ":", h="center")
        x = f.cell(f.r, 11, NC if i == 0 else 18, "=" + ref, sz=10 if i == 0 else 11)
        if i == 0:
            x.alignment = Alignment(horizontal="left", vertical="center", shrink_to_fit=True)
        if i == 2:
            f.cell(f.r, 19, NC, "(인) 또는 (서명)", h="right", sz=10)
        f.r += 1


def date_line(f, h="center"):
    f.ws.row_dimensions[f.r].height = 28
    f.cell(f.r, 1, NC, "=" + blank(F["제출일"]), h=h, fmt=DATE_FMT)
    f.r += 1


def form1(wb, what):
    f = Form(wb, "1_적격심사신청서")
    f.logo("[별지 제1호 서식]")
    f.title("적 격 심 사 신 청 서")
    header_rows(f, [(k, "=" + blank(F[k])) for k in ("입찰등록번호", "구매관리번호", "입찰 대상 물품명", "정부물품 분류번호", "수요기관", "입찰일시")])
    f.gap(16)
    f.para(f"  위 건 {what}입찰의 적격심사와 관련하여 적격심사 자기평가표와\n"
           "제증빙자료를 신의성실의 원칙에 입각하여 붙임과 같이 작성 제출하며,\n"
           "만일 제출한 서류가 허위나 부정한 방법으로 작성된 사실이 확인될\n"
           "경우에는 적격심사기준, 지방계약법령 등에 따라 처리하여도 아무런\n"
           "이의를 제기하지 않겠음을 확약합니다.", lines=5)
    f.gap(8)
    f.para("붙임 : 제출서류 목록과 관련서류 1부")
    f.gap(18)
    date_line(f)
    f.gap(14)
    sign(f)
    f.gap(14)
    f.ws.row_dimensions[f.r].height = 30
    f.cell(f.r, 1, NC, f'=TRIM(IF({F["수신"]}<>"",{F["수신"]},{jg_blank("수요기관")})&" 귀하")', sz=14, b=True)
    f.r += 1
    f.gap(12)
    for c in range(1, NC + 1):  # 절취선
        f.ws.cell(f.r, c).border = Border(top=Side(style="dashed"))
    f.gap(14)
    f.ws.row_dimensions[f.r].height = 32
    f.cell(f.r, 1, NC, "접    수    증", sz=15, b=True, h="center")
    f.r += 1
    for i, (label, ref) in enumerate((("구매 관리 번호", F["구매관리번호"]), ("입찰대상물품명", F["입찰 대상 물품명"]), ("제    출    자", F["회사명"])), 1):
        f.ws.row_dimensions[f.r].height = 24
        f.cell(f.r, 2, 9, f"{i}. {label}")
        f.cell(f.r, 10, 10, ":", h="center")
        f.cell(f.r, 11, NC, "=" + blank(ref))
        f.r += 1
    f.gap(6)
    f.para(f"위 {what}입찰의 적격심사신청서와 관련서류를 접수했음을 확인합니다.", h="center")
    f.para("20      .      .      .", h="center")
    f.para(f'=TRIM(IF({F["수신"]}<>"",{F["수신"]},{jg_blank("수요기관")})&" 재무관")&"            (인)      "', h="right")
    f.done()


def form2(wb, scored):
    f = Form(wb, "2_자기평가와심사표")
    f.logo("[별지 제2호 서식]")
    f.title("적격심사 자기평가와 심사표", sz=18)
    if scored:
        f.ws.row_dimensions[f.r - 1].height = 22
        f.cell(f.r - 1, 1, NC, f'=IF({EST}="","","("&{TIER_NAME}&")")', sz=10, h="center")
    f.para("1. 심사대상물품", b=True)
    f.table([("구매관리번호", "=" + blank(F["구매관리번호"])), ("입 찰 일 시", "=" + blank(F["입찰일시"])),
             ("품        명", "=" + blank(F["입찰 대상 물품명"])), ("정부물품분류번호", "=" + blank(F["정부물품 분류번호"])),
             ("수        량", "=" + blank(F["수량"])), ("입 찰 금 액", "=" + blank(F["입찰금액"]), "#,##0"),
             ("추 정 가 격", "=" + blank(F["추정가격"]), "#,##0")], h=25)
    f.gap(8)
    f.para("2. 심사대상입찰자", b=True)
    up = F["업종구분"]
    f.table([("업  체  명", f'={F["회사명"]}&"     (전화) "&{F["전화"]}'), ("대  표  자", "=" + F["대표자"]),
             ("업 종 구 분", f'="제조( "&IF({up}="제조","○"," ")&" )   공급( "&IF({up}="공급","○"," ")&" )   기타( "&IF({up}="기타","○"," ")&" )"')], h=25)
    f.gap(8)
    f.para("3. 회사를 대표하는 연락책임자 인적사항", b=True)
    f.table([("소        속", "=" + blank(F["연락책임자 소속"])), ("직        위", "=" + blank(F["연락책임자 직위"])),
             ("성        명", "=" + blank(F["연락책임자 성명"])),
             ("전 화 번 호", f'=IF({F["연락책임자 전화"]}="",{F["전화"]},{F["연락책임자 전화"]})&"     (Fax) "&{F["팩스"]}')], h=25)
    f.gap(8)
    f.para("4. 종합평가                                                                        (단위 : 점)", b=True)
    if scored:
        rows = [["계", "100", "=" + S_SUM, ""],
                ["이 행 실 적", f'=IF({T}=1,"30","공고 명시 때")', "=" + S_RECORD, ""],
                ["기 술 능 력", "10", "=" + S_TECH, ""], ["경 영 상 태", f"={P_CREDIT}", "=" + S_CREDIT, ""],
                ["입 찰 가 격", f"={P_PRICE}", "=" + S_PRICE, ""], ["신   인   도", "+2 ~ −2", "=" + S_TRUST, ""], ["결 격 사 유", "", "", ""]]
    else:
        rows = [[n, "", "", ""] for n in ("계", "이 행 실 적", "기 술 능 력", "경 영 상 태", "입 찰 가 격", "신   인   도", "결 격 사 유")]
    f.grid(["구      분", "배    점", "자기평점", "심사평점"], [9, 5, 5, 5], rows, h=25)
    for r in range(f.r - 7, f.r):
        f.ws.cell(r, 15).number_format = "0.00"
    f.done()


def form3(wb):
    f = Form(wb, "3_평점세부내역")
    f.logo("[자기평점 계산 근거]")
    f.title("항목별 평점 세부내역", sz=18)
    f.ws.row_dimensions[f.r - 1].height = 22
    f.cell(f.r - 1, 1, NC, f'=IF({EST}="","",{TIER_TAB}&" "&{TIER_NAME}&" 물품 평가기준")', sz=10, h="center")
    f.ws.row_dimensions[f.r].height = 30
    f.cell(f.r, 1, NC, f'="종합평점 : "&IF({S_SUM}="","(예정가격 입력 후)",TEXT({S_SUM},"0.00")&"점")', sz=13, b=True)
    f.r += 1
    f.gap(6)
    f.para(f'="1. 이행실적 : "&IF({T}=1,TEXT({S_RECORD},"0.0")&"점 (30점)","입찰공고에 평가방법을 명시한 경우에만")', b=True)
    f.grid(["구분", "실적(a)", "추정가격(b)", "a ÷ b", "평점"], [5, 5, 5, 5, 4],
           [["동등이상물품", "=" + blank(F["동등이상물품 실적"]), "=" + blank(EST),
             f'=IF(OR({F["동등이상물품 실적"]}="",N({EST})=0),"",TEXT({R_EQ},"0.0")&"%")', f"=IF({T}=1,{S_EQ},\"\")"],
            ["유사물품", "=" + blank(F["유사물품 실적"]), "=" + blank(EST),
             f'=IF(OR({F["유사물품 실적"]}="",N({EST})=0),"",TEXT({R_SIM},"0.0")&"%")', f"=IF({T}=1,{S_SIM},\"\")"]], h=25)
    for r in (f.r - 2, f.r - 1):
        for c in (6, 11):
            f.ws.cell(r, c).number_format = "#,##0"
    f.para("※ 소기업·소상공인은 실적 10% 가산, 동등이상 + 유사 합 30점 한도(실적증명서는 나라장터 발급본)", sz=9)
    f.gap(6)
    f.para(f'="2. 기술능력 : "&TEXT({S_TECH},"0.0")&"점 (10점)"', b=True)
    f.grid(["세부심사항목", "등급별 인원 · 보유", "기준", "평점"], [5, 10, 5, 4],
           [["기술인력 보유",
             f'="기술사·기능장 "&{F["기술사·기능장 수"]}&" · 기사·산업기사 "&{F["기사·산업기사 수"]}&CHAR(10)&"기능사 "&{F["기능사 수"]}&" · 일반경력자 "&{F["일반경력자(3년↑) 수"]}&"명"',
             f'=IF({JE},"1인당 2·1.5·1·0.5"&CHAR(10)&"최대 5점","물품구매 — 만점")', "=" + S_STAFF],
            ["시설·장비 보유", f'={F["시설·장비"]}&" "&IF({F["시설·장비"]}="A","보유(공고 기준 충족)",IF({F["시설·장비"]}="B","보유(기준 미달)","미보유"))', f'=IF({JE},"A 5 · B 2 · C 1","물품구매 — 만점")', "=" + S_FAC]], h=36)
    f.gap(6)
    f.para(f'="3. 경영상태 : "&TEXT({S_CREDIT},"0.0")&"점 ("&{P_CREDIT}&"점)"', b=True)
    f.grid(["신용평가등급", "유효기간", "평점"], [9, 9, 6],
           [["=" + blank(F["신용평가등급"]), "=" + blank(F["신용평가 유효기간"]), "=" + S_CREDIT]], h=25)
    f.ws.cell(f.r - 1, 10).number_format = "yyyy-mm-dd"
    f.para(f'=IF(AND({F["신용평가 유효기간"]}<>"",{F["제출일"]}<>"",N({F["신용평가 유효기간"]})<N({F["제출일"]})),'
           '"⚠ 신용평가등급 유효기간이 제출일 전에 끝남 — 새 확인서로","")', sz=9)
    f.gap(4)
    f.para(f'="4. 입찰가격 : "&IF({S_PRICE}="","",TEXT({S_PRICE},"0.00"))&"점 ("&{P_PRICE}&"점)"', b=True)
    f.grid(["입찰가격(a)", "예정가격(b)", "a ÷ b × 100", "평점"], [6, 6, 7, 5],
           [["=" + blank(F["입찰금액"]), "=" + blank(F["예정가격"]), f'=IF({F["예정가격"]}="","",TEXT({RATIO},"0.00")&"%")', "=" + S_PRICE]], h=25)
    for c in (1, 7):
        f.ws.cell(f.r - 1, c).number_format = "#,##0"
    f.para(f'="※ 평점 = "&{P_PRICE}&" − "&{K}&" × | 88 − a÷b×100 |,  예정가격 이하이면서 "&{TH}&"% 이상이면 "&{FIX}&"점,  최저 2점"', sz=9)
    f.gap(6)
    f.para(f'="5. 신인도 : "&TEXT({S_TRUST},"0.0")&"점 (+2 ~ −2)"', b=True)
    f.grid(["심사항목", "내용", "평점"], [6, 14, 4],
           [["중소(제조)기업", "=" + blank(F["중소기업 구분"]), "=" + S_SME],
            ["그 밖 가점", "인증·특허 등(담당자 확인)", "=" + F["신인도 그 밖 가점"]],
            ["감점", "이행지연·품질하자·계약질서", f'=-{F["신인도 감점"]}']], h=25)
    f.done()


def form4(wb, ev):
    f = Form(wb, "4_제출서류목록표")
    f.logo("[별지 제4호 서식]")
    f.title("제 출 서 류 목 록 표", sz=18)
    rows = [["총괄", "1", "적격심사신청서", "신청자", "별지 제1호"], ["총괄", "2", "적격심사 자기평가와\n심사표", "신청자", "별지 제2호"]]
    for i, (area, text, issuer, _) in enumerate(EVIDENCE, 3):
        rows.append([area, str(i), text.replace("(", "\n(", 1) if len(text) > 12 else text, issuer, ""])
    f.grid(["심사분야", "연번", "내      용", "발행기관", "비  고"], [4, 2, 9, 5, 4], rows, h=34)
    f.gap(8)
    f.para("주) 신인도 증명서류는 원본을 제시하고 사본으로 제출하는 경우 사본에 '원본과 상위 없음'을\n"
           "     대표자가 확인하고 날인해야 한다.", lines=2, sz=9)
    f.done()
    # 담당자용 증빙 확인(제출 안 함 — 숫자로 시작하지 않는 탭은 PDF·한글로 안 나감)
    ws = wb.create_sheet("증빙확인")
    for col, w in zip("ABCDEFG", (10, 30, 34, 12, 16, 22, 46)):
        ws.column_dimensions[col].width = w
    ws["A1"] = "증빙 확인 — 회사서류_최신(최신본) 먼저, 없는 종류는 '지일 자료'. 파일은 공고 폴더 '증빙서류'에 연번대로 들어감. 상태는 '제출일'로 다시 계산"
    ws["A1"].font = Font(name=FONT, b=True, sz=12)
    for c, h in enumerate(("심사분야", "필요 서류", "보유 서류", "출처", "유효기간", "제출일 기준 상태", "메모"), 1):
        x = ws.cell(3, c, h)
        x.font, x.fill = Font(name=FONT, b=True), PatternFill("solid", fgColor="FFE8EEF4")
    for i, ((area, text, _, _), e) in enumerate(zip(EVIDENCE, ev), 4):
        end = e.get("만료")
        for c, val in enumerate([area, text, e["보유"] or "폴더에 없음", e["출처"], end, None, e["메모"]], 1):
            ws.cell(i, c, val).font = Font(name=FONT, sz=10)
        ws.cell(i, 5).number_format = "yyyy-mm-dd"
        if not e["보유"]:
            st = '"없음 — 준비 필요"'
        elif end:
            st = f'IF({F["제출일"]}="","제출일 입력",IF(E{i}<{F["제출일"]},"만료 — 새로 발급","유효"))'
        elif e.get("3개월"):
            st = f'IF({F["제출일"]}="","제출일 입력",IF(EDATE(DATEVALUE("{e["발급"]}"),3)<{F["제출일"]},"3개월 넘음 — 새로 발급","유효"))'
        else:
            st = '"기간 없음 — 내용 확인"'
        ws.cell(i, 6, "=" + st).font = Font(name=FONT, sz=10, b=True)


def build(path, work):
    scored = work == "물품"
    what = {"물품": "물품 제조·구매", "용역": "용역", "공사": "공사"}[work]
    wb = Workbook()
    wb._fonts = IndexedList([Font(name=FONT, sz=11)])  # 엑셀 칸 너비 단위 = 기본 글꼴 글자 폭
    wb._named_styles["Normal"].font = Font(name=FONT, sz=11)
    basic(wb, scored)
    wb[B]["C13"] = (f'=IF({WRONG},"⚠ 추정가격이 {OTHER}입니다 — 「{OTHER}」 파일을 쓰세요",'
                     '"숫자만 — 평가 기준(별표)을 고르는 값")')
    wb[B]["C13"].font = Font(name=FONT, sz=9, color="FF666666")
    form1(wb, what)
    form2(wb, scored)
    if scored:
        form3(wb)
    v = company()
    ev = evidence(v, doc_rows())
    form4(wb, ev)
    staff_sheet(wb, v.staff or [])
    record_sheet(wb, v.tracks or [])
    if not small_biz(ev):
        wb[B]['B34'] = '아니오'
    ws = wb[B]

    def put(name, value):
        ws[F[name].split("!")[1].replace("$", "")] = value
    for name, key in (("회사명", "회사명"), ("대표자", "대표자"), ("주소", "주소"), ("전화", "전화번호"), ("팩스", "팩스번호")):
        put(name, v.info.get(key))
    if v.info.get("신용평가등급"):
        put("신용평가등급", v.info["신용평가등급"])
        if v.info.get("신용평가 유효기간"):
            put("신용평가 유효기간", dt.datetime.strptime(v.info["신용평가 유효기간"], "%Y-%m-%d"))
    wb.calculation.fullCalcOnLoad = True
    wb.save(path)
    return ev
