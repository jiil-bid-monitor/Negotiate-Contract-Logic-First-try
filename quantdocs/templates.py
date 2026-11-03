"""담당자가 채울 빈 엑셀 양식 만들기 — 실적 목록 · 인력 목록 · 서류 상태.

양식 파일(.xlsx)은 저장소에 두지 않고 이 코드로 만든다(.gitignore가 엑셀을 막음 — 채운 양식이 실수로 올라가지 않게).

    python -m quantdocs templates --out "<폴더>"
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

from .catalog import Document, load_catalog

FILE_NAME = "회사자료_양식.xlsx"
ROWS = 300  # 드롭다운·형식을 미리 걸어 두는 줄 수

WORKS = ["물품", "용역", "공사"]
VAT = ["포함", "별도", "모름"]
EMPLOYED = ["재직", "퇴사"]
STATUS = ["사용", "재발급", "확인"]
VALIDITY_RULES = ["그대로 둠", "유효기간 갱신", "3개월 이내", "공고일 이후"]


@dataclass
class Col:
    name: str
    width: int = 14
    kind: str = "text"  # text / date / money / percent / choice
    choices: list[str] | None = None
    note: str = ""  # 안내 시트에 적는 설명


def sheets(catalog: list[Document]) -> dict[str, list[Col]]:
    doc_names = [d.name.split(" (")[0] for d in catalog]
    return {
        "회사정보": [
            Col("항목", 22, note="회사명·대표자·사업자등록번호 등 서식 칸에 그대로 넣는 값 — 항목 이름은 바꾸지 말 것"),
            Col("값", 50),
        ],
        "실적목록": [
            Col("번호", 6, note="실적마다 하나. 증빙 PDF 파일 이름에도 같은 번호를 붙이면 연결이 쉬움"),
            Col("업무", 8, "choice", WORKS),
            Col("사업명", 40),
            Col("발주기관", 22),
            Col("계약일", 12, "date"),
            Col("준공일", 12, "date", note="실적 기간(최근 N년)은 대개 준공일 기준"),
            Col("계약금액(원)", 16, "money", note="원 단위 — 실적집계표가 천원 단위면 그때 바꿔 씀"),
            Col("부가세", 8, "choice", VAT),
            Col("지분율(%)", 10, "percent", note="공동수급이면 지일 지분, 단독이면 100"),
            Col("분야", 16, note="담당자가 정리한 분야 이름(예: 전시·체험)"),
            Col("증빙 파일 이름", 30, note="실적증명서 등 증빙 PDF 파일 이름"),
            Col("실적증명서 발급일", 14, "date"),
            Col("비고", 30),
        ],
        "인력목록": [
            Col("번호", 6, note="자격증 하나에 한 줄 — 한 사람이 자격증이 여럿이면 여러 줄"),
            Col("이름", 10),
            Col("부서·직위", 14),
            Col("입사일", 12, "date"),
            Col("재직", 8, "choice", EMPLOYED, note="퇴사하면 '퇴사'로 — 지우지 않음"),
            Col("자격증", 24),
            Col("등급", 10),
            Col("취득일", 12, "date"),
            Col("유효기간 만료일", 14, "date", note="만료 없는 자격증은 비움"),
            Col("가능 직무", 16, note="PM·설계·시공 등 — 공고마다 정하는 PM은 여기서 고름"),
            Col("증빙 파일 이름", 30),
            Col("비고", 30),
        ],
        "연혁": [
            Col("연월일", 12, "date", note="연혁 한 줄에 하나(예: 2009-09-23)"),
            Col("내용", 60, note="예: (주)지일 법인설립"),
            Col("증빙 파일 이름", 30, note="있으면 files/ 안 파일 이름"),
        ],
        "서류상태": [
            Col("번호", 6),
            Col("서류 종류", 26, "choice", doc_names, note="config/documents.json의 서류 목록"),
            Col("서류 이름", 30, note="예: 실내건축공사업 등록증, 신용평가등급확인서"),
            Col("업종코드·세부품명번호", 22, note="자격 서류면 코드(예: 4990, 6012100201) — 공고 요구 자격과 맞출 때 씀. 여러 개면 쉼표"),
            Col("발급일", 12, "date"),
            Col("유효기간 만료일", 14, "date"),
            Col("유효기간 기준", 14, "choice", VALIDITY_RULES, note="'회사서류_최신'의 구분과 같음"),
            Col("상태", 8, "choice", STATUS),
            Col("파일 이름", 30),
            Col("갱신 담당", 10),
            Col("비고", 30),
        ],
    }


_HEAD_FILL = PatternFill("solid", fgColor="1B6E43")
_HEAD_FONT = Font(bold=True, color="FFFFFF")
_THIN = Side(style="thin", color="BFBFBF")
_FORMATS = {"date": "yyyy-mm-dd", "money": "#,##0", "percent": "0.##"}


def _sheet(wb: Workbook, title: str, cols: list[Col]) -> None:
    ws = wb.create_sheet(title)
    for i, c in enumerate(cols, 1):
        cell = ws.cell(row=1, column=i, value=c.name)
        cell.fill, cell.font = _HEAD_FILL, _HEAD_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = Border(bottom=_THIN)
        letter = get_column_letter(i)
        ws.column_dimensions[letter].width = c.width
        rng = f"{letter}2:{letter}{ROWS + 1}"
        if c.kind in _FORMATS:
            for (cell,) in ws[rng]:
                cell.number_format = _FORMATS[c.kind]
        if c.kind == "choice" and c.choices:
            dv = DataValidation(type="list", formula1='"' + ",".join(c.choices) + '"', allow_blank=True,
                                showErrorMessage=True, errorTitle="목록에서 고르세요", error=" · ".join(c.choices))
        elif c.kind == "date":
            dv = DataValidation(type="date", operator="greaterThan", formula1="1", allow_blank=True,
                                showErrorMessage=True, errorTitle="날짜", error="2024-03-15처럼 날짜로 적어 주세요")
        elif c.kind in ("money", "percent"):
            dv = DataValidation(type="decimal", operator="greaterThanOrEqual", formula1="0", allow_blank=True,
                                showErrorMessage=True, errorTitle="숫자", error="숫자만 적어 주세요(쉼표·원 없이)")
        else:
            continue
        ws.add_data_validation(dv)
        dv.add(rng)
    ws.freeze_panes = "B2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(cols))}1"


COMPANY_ITEMS = ["회사명", "대표자", "사업자등록번호", "법인등록번호", "주소", "전화번호", "팩스번호",
                 "설립일", "업태", "종목", "이메일", "홈페이지", "자본금", "종업원 수"]


def _guide(wb: Workbook, layout: dict[str, list[Col]]) -> None:
    ws = wb.active
    ws.title = "안내"
    lines = [
        ("회사 자료 양식 — 정량평가서 서류 준비용", True),
        ("이 파일은 사내 PC·NAS에만 두세요. 저장소·외부에 올리지 않습니다.", False),
        ("한 줄에 하나씩 적고, 날짜는 2024-03-15처럼, 금액은 숫자만(쉼표·원 없이) 적습니다.", False),
        ("모르는 칸은 비워 두세요 — 짐작해서 채우지 않습니다.", False),
        ("", False),
    ]
    for title, cols in layout.items():
        lines.append((f"[{title}]", True))
        lines += [(f"  {c.name} — {c.note}", False) for c in cols if c.note]
        lines.append(("", False))
    for r, (text, bold) in enumerate(lines, 1):
        cell = ws.cell(row=r, column=1, value=text)
        cell.font = Font(bold=bold, size=13 if r == 1 else 11)
    ws.column_dimensions["A"].width = 100


def build(out_dir: Path, catalog: list[Document] | None = None) -> Path:
    """빈 양식 파일을 out_dir에 만든다. 같은 이름 파일이 있으면 덮어쓰지 않고 오류."""
    out = Path(out_dir) / FILE_NAME
    if out.exists():
        raise FileExistsError(f"이미 있습니다 — 채운 양식일 수 있어 덮어쓰지 않습니다: {out}")
    layout = sheets(catalog or load_catalog())
    wb = Workbook()
    _guide(wb, layout)
    for title, cols in layout.items():
        _sheet(wb, title, cols)
    for r, item in enumerate(COMPANY_ITEMS, 2):  # 회사정보 항목 이름은 미리 적어 둠(값만 채우게)
        wb["회사정보"].cell(row=r, column=1, value=item)
    out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out)
    return out
