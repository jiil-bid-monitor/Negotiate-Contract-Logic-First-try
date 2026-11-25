"""한셀로 저장한 엑셀도 읽기 — openpyxl.load_workbook 대신 씀(담당자가 한셀로 고쳐 저장하는 파일, 11/25).

한셀은 글꼴·칸 모양을 <mc:AlternateContent>(한셀용 Choice + 엑셀용 Fallback)로 적는데 openpyxl은 그 안을 안 읽어
모양 번호가 밀리고 'list index out of range'로 멈춤 → Fallback만 남김. 색도 소문자(ffffff00)라 대문자로 맞춤.
"""
from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path
from typing import Any

STYLES = "xl/styles.xml"
_ALT = re.compile(r"<mc:AlternateContent\b.*?</mc:AlternateContent>", re.S)
_FALLBACK = re.compile(r"<mc:Fallback>(.*?)</mc:Fallback>", re.S)


def load_workbook(path: Path | str, **kw: Any):
    import openpyxl

    with zipfile.ZipFile(path) as z:
        styles = z.read(STYLES).decode("utf-8") if STYLES in z.namelist() else ""
        if "<mc:AlternateContent" not in styles:
            return openpyxl.load_workbook(path, **kw)
        styles = _ALT.sub(lambda m: (_FALLBACK.search(m.group(0)) or [None, ""])[1], styles)
        styles = re.sub(r'\brgb="([0-9a-fA-F]{8})"', lambda m: f'rgb="{m.group(1).upper()}"', styles)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as w:
            for n in z.namelist():
                w.writestr(n, styles.encode("utf-8") if n == STYLES else z.read(n))
    buf.seek(0)
    return openpyxl.load_workbook(buf, **kw)
