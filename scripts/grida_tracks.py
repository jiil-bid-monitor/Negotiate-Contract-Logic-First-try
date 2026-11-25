"""그리다 실적 모으기 — 그리다 사업 폴더의 계약서·실적증명서·준공서류에서 사업명·사업기간·계약금액·발주기관을 읽어
정량평가서 실적 표(서식 '최근 3년간 동종/유사사업 수행실적' 등)에 들어갈 엑셀을 만든다(11/25 사용자 요청).

서버 PC에서(그리다 공유 폴더가 보이는 곳):
    py scripts/grida_tracks.py
    py scripts/grida_tracks.py --root "\\\\jiilserver\\007 그리다\\04_그리다" --dry-run

결과 `공사수주현황_그리다.xlsx`(그리다 서류 폴더 `grida-quant-docs` 안 — 이름에 '수주'가 있어 정량평가서가 실적목록으로 읽음).
못 읽은 칸은 비우고 '확인할 것' 칸에 적음 — 엑셀을 열어 채우거나 고친 뒤 저장하면 다음 만들기부터 그 값.
이미 있는 파일은 덮어쓰지 않고 날짜 붙인 새 이름으로 씀(담당자가 고친 값 보호).
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DEFAULT_ROOT = r"\\jiilserver\007 그리다\04_그리다"
PROJECT = re.compile(r"^(20\d{2})[_\s]")  # '2025_9보병'
SKIP_PROJECT = re.compile(r"사무실|이전|기업자료")
SUFFIXES = (".pdf", ".hwp", ".hwpx", ".docx")
# 먼저 읽을 문서(앞이 먼저) — 실적은 계약서·실적증명서가 가장 정확
PRIORITY = ("실적증명", "계약서", "준공", "완료", "납품", "착수", "착공", "공고", "제안요청", "과업지시")
MAX_FILES = 400
MAX_READ = 12


def _sp(word: str) -> str:
    return r"\s*".join(map(re.escape, word))


_AMOUNT = re.compile(rf"(?:총\s*)?(?:{_sp('계약금액')}|{_sp('계약금')}|{_sp('도급금액')}|{_sp('납품금액')}|{_sp('공사금액')})"
                     r"[^\d\n]{0,40}?([\d]{1,3}(?:,\d{3}){2,})\s*원?")
_DATE = r"(20\d{2})\s*[.\-년/]\s*(\d{1,2})\s*[.\-월/]\s*(\d{1,2})"
_PERIOD = re.compile(rf"(?:계약|사업|용역|공사|납품|수행|과업)\s*기\s*간[^\d\n]{{0,15}}{_DATE}[^\d\n]{{0,15}}?~\s*{_DATE}")
_START = re.compile(rf"(?:착\s*수|착\s*공|계\s*약)\s*(?:일|년\s*월\s*일)[^\d\n]{{0,10}}{_DATE}")
_END = re.compile(rf"(?:준\s*공|완\s*료|납\s*품)\s*(?:일|년\s*월\s*일|예정일)[^\d\n]{{0,10}}{_DATE}")
_ORG = re.compile(r"(?:발\s*주\s*기\s*관|수\s*요\s*기\s*관|발\s*주\s*처|발\s*주\s*자)\s*(?:명)?\s*[:：]?\s*([가-힣A-Za-z0-9()·\s]{2,40}?)(?:\s{2,}|\n|$|[|])")


def _d(y, m, d) -> date | None:
    try:
        return date(int(y), int(m), int(d))
    except ValueError:
        return None


def projects(root: Path) -> list[Path]:
    """'2025_…' 이름의 사업 폴더(3단계 아래까지, 사업 폴더 안의 폴더는 안 봄)."""
    out: list[Path] = []

    def walk(p: Path, depth: int):
        try:
            kids = sorted(k for k in p.iterdir() if k.is_dir())
        except OSError:
            return
        for k in kids:
            if PROJECT.match(k.name):
                if not SKIP_PROJECT.search(k.name):
                    out.append(k)
            elif depth < 3:
                walk(k, depth + 1)
    walk(root, 0)
    return out


def _docs(folder: Path) -> list[Path]:
    files = []
    for i, p in enumerate(folder.rglob("*")):
        if i > MAX_FILES * 5 or len(files) >= MAX_FILES:
            break
        if p.is_file() and p.suffix.lower() in SUFFIXES and not p.name.startswith("~$"):
            files.append(p)

    def rank(p: Path):
        name = p.name
        return next((i for i, w in enumerate(PRIORITY) if w in name), len(PRIORITY))
    return sorted((p for p in files if rank(p) < len(PRIORITY)), key=rank)[:MAX_READ]


def _text(p: Path) -> str:
    from nego.attachments import extract_text

    try:
        return extract_text(p.read_bytes(), p.suffix) or ""
    except Exception:  # noqa: BLE001 — 못 읽는 문서(스캔본·보안 문서)는 건너뜀
        return ""


def read_project(folder: Path) -> dict:
    """사업 폴더 하나 → {사업명, 시작, 끝, 금액, 발주기관, 근거, 확인}."""
    name = re.sub(r"\s+", " ", PROJECT.sub("", folder.name).replace("_", " ")).strip()
    name = re.sub(r"\(?\d{6}\)?$", "", name).strip()  # 끝에 붙은 '_241213' 같은 날짜
    row: dict = {"사업명": name, "연도": int(folder.name[:4]), "시작": None, "끝": None, "금액": None, "발주기관": "", "근거": []}
    for p in _docs(folder):
        t = _text(p)
        if len(re.sub(r"\s", "", t)) < 30:
            continue
        got = False
        if row["금액"] is None and (m := _AMOUNT.search(t)):
            row["금액"], got = int(m.group(1).replace(",", "")), True
        if row["시작"] is None and (m := _PERIOD.search(t)):
            row["시작"], row["끝"], got = _d(*m.group(1, 2, 3)), _d(*m.group(4, 5, 6)), True
        if row["시작"] is None and (m := _START.search(t)):
            row["시작"], got = _d(*m.group(1, 2, 3)), True
        if row["끝"] is None and (m := _END.search(t)):
            row["끝"], got = _d(*m.group(1, 2, 3)), True
        if not row["발주기관"] and (m := _ORG.search(t)):
            org = re.sub(r"\s+", " ", m.group(1)).strip(" :")
            if len(org) >= 2:
                row["발주기관"], got = org, True
        if got:
            row["근거"].append(p.relative_to(folder).as_posix())
        if row["금액"] and row["시작"] and row["끝"] and row["발주기관"]:
            break
    need = [k for k, label in (("금액", "계약금액"), ("시작", "착수일"), ("끝", "준공일"), ("발주기관", "발주기관")) if not row[k]]
    row["확인"] = ", ".join({"금액": "계약금액", "시작": "착수일", "끝": "준공일", "발주기관": "발주기관"}[k] for k in need)
    return row


def write(rows: list[dict], out: Path) -> Path:
    from openpyxl import Workbook

    if out.exists():
        out = out.with_name(f"{out.stem}_{date.today():%Y%m%d}{out.suffix}")
    wb = Workbook()
    ws = wb.active
    ws.title = "공사수주현황"
    ws.append(["No.", "사업명", "계약종류", "사업기간", "계약금액", "발주기관", "근거 파일", "확인할 것"])
    f = lambda d: f"{d:%Y.%m.%d}" if d else ""  # noqa: E731
    for i, r in enumerate(sorted(rows, key=lambda r: (r["시작"] or date(r["연도"], 1, 1))), 1):
        period = f"{f(r['시작'])}~{f(r['끝'])}" if r["시작"] or r["끝"] else ""
        ws.append([i, r["사업명"], "", period, r["금액"], r["발주기관"], " · ".join(r["근거"][:3]), r["확인"]])
    for col, width in zip("ABCDEFGH", (5, 46, 10, 24, 16, 26, 50, 30)):
        ws.column_dimensions[col].width = width
    out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="그리다 사업 폴더 → 실적 엑셀(공사수주현황_그리다.xlsx)")
    ap.add_argument("--root", default=DEFAULT_ROOT, help="그리다 사업 폴더 위 폴더")
    ap.add_argument("--out", default="", help="결과 엑셀(기본: 그리다 서류 폴더 안 공사수주현황_그리다.xlsx)")
    ap.add_argument("--dry-run", action="store_true", help="엑셀은 안 쓰고 화면에만")
    a = ap.parse_args(argv)
    root = Path(a.root)
    if not root.is_dir():
        print(f"폴더가 안 보입니다: {root}")
        return 1
    folders = projects(root)
    print(f"사업 폴더 {len(folders)}개")
    rows = []
    for p in folders:
        r = read_project(p)
        rows.append(r)
        print(f"- {r['연도']} {r['사업명'][:40]} | 금액 {r['금액'] or '-'} | 기간 {r['시작'] or '-'}~{r['끝'] or '-'} | "
              f"발주 {r['발주기관'] or '-'}" + (f" | 확인: {r['확인']}" if r["확인"] else ""))
    if a.dry_run:
        return 0
    if a.out:
        out = Path(a.out)
    else:
        from quantdocs.vault import company_dir

        out = company_dir("grida") / "공사수주현황_그리다.xlsx"
    print(f"저장: {write(rows, out)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
